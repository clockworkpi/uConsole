"""Diskless owned-QEMU UART backpressure reproduction and Tk control regression."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import select
import socket
import subprocess
import threading
import time
import tkinter as tk
from types import SimpleNamespace

from record_forge_desktop import RecorderIO
from test_emulator_pmic import connect
from uconsole_emulator import executable, qmp


def run(output):
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    root = tk.Tk()
    root.withdraw()
    io = RecorderIO()
    writer = ThreadPoolExecutor(max_workers=1)
    process = serial = test_socket = stream = None
    result = {'status': 'failed', 'disk_attached': False, 'physical_target_contacted': False}
    log = (output/'qemu.log').open('xb')
    try:
        process = subprocess.Popen([executable('qemu-system-aarch64'), '-M', 'raspi4b',
            '-accel', 'qtest', '-S', '-display', 'none', '-monitor', 'none',
            '-qtest', f'unix:{output}/qtest,server=on,wait=off',
            '-qmp', f'unix:{output}/qmp,server=on,wait=off',
            '-chardev', f'socket,id=uart,path={output}/serial,server=on,wait=off',
            '-serial', 'chardev:uart'], stdout=log, stderr=log)
        test_socket = connect(output/'qtest', process)
        test_socket.settimeout(30)
        serial = connect(output/'serial', process)
        serial.setblocking(False)
        stream = test_socket.makefile('rwb', buffering=0)
        def command(text):
            stream.write((text+'\n').encode())
            while True:
                line = stream.readline()
                if line.startswith(b'IRQ '): continue
                if not line.startswith(b'OK'): raise RuntimeError('Unexpected qtest reply')
                return
        command('writel 0xfe201030 0x301')
        qmp(output/'qmp', 'query-status')
        condition = threading.Condition()
        progress = [0]
        count = 65536
        def emit():
            for _ in range(count):
                command('writel 0xfe201000 0x41')
                with condition:
                    progress[0] += 1
                    condition.notify_all()
        def stalled(future):
            deadline = time.monotonic()+10
            while time.monotonic() < deadline:
                with condition:
                    previous = progress[0]
                    changed = condition.wait_for(lambda: progress[0] != previous or future.done(), timeout=0.25)
                    if not changed and 0 < previous < count:
                        return previous
                if future.done():
                    future.result()
                    raise RuntimeError('UART did not exhibit backpressure')
            raise TimeoutError('UART backpressure was not observed')
        def drain():
            try: return serial.recv(65536)
            except BlockingIOError: return b''
        first = writer.submit(emit)
        result['baseline_stalled_after_bytes'] = stalled(first)
        try:
            qmp(output/'qmp', 'query-status')
        except TimeoutError as exc:
            result['baseline_qmp_timeout'] = str(exc)
        else:
            raise RuntimeError('Synchronous QMP did not reproduce UART deadlock')
        received = bytearray()
        deadline = time.monotonic()+20
        while len(received) < count or not first.done():
            if time.monotonic() >= deadline: raise TimeoutError('Baseline UART did not resume after draining')
            select.select([serial], [], [], 0.05)
            received.extend(drain())
        first.result()
        if received != b'A'*count: raise ValueError('Baseline UART bytes differ')
        result['baseline_recovered_after_drain'] = qmp(output/'qmp', 'query-status')

        progress[0] = 0
        second = writer.submit(emit)
        result['async_stalled_after_bytes'] = stalled(second)
        received = bytearray()
        replies = []
        runtime = SimpleNamespace(process=process, control=lambda operation: qmp(output/'qmp', operation))
        io.submit(runtime, lambda: qmp(output/'qmp', 'query-status'),
                  lambda value, error: replies.append((value, error)))
        failures = []
        deadline = time.monotonic()+20
        def tick():
            try:
                received.extend(drain())
                io.poll()
                if replies and second.done() and len(received) == count:
                    root.quit()
                    return
                if time.monotonic() >= deadline: raise TimeoutError('Async QMP or UART did not finish')
                root.after(10, tick)
            except Exception as exc:
                failures.append(exc)
                root.quit()
        root.after(0, tick)
        root.mainloop()
        if failures: raise failures[0]
        second.result()
        if received != b'A'*count or len(replies) != 1 or replies[0][1] is not None:
            raise ValueError('Async QMP/UART did not complete exactly once')
        result.update(async_qmp_reply=replies[0][0], async_uart_bytes=len(received),
                      input_retried=False, gui_serial_drain_remained_live=True)
        qmp(output/'qmp', 'quit')
        if process.wait(timeout=10) != 0: raise RuntimeError('Owned QEMU did not exit cleanly')
        result.update(status='passed', qemu_exit=0, forced_cleanup=False)
    except BaseException as exc:
        result['error'] = type(exc).__name__+': '+str(exc)
        raise
    finally:
        if process is not None and process.poll() is None:
            result['forced_cleanup'] = True
            process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if stream is not None: stream.close()
        if test_socket is not None: test_socket.close()
        if serial is not None: serial.close()
        writer.shutdown(wait=True)
        io.close()
        root.destroy()
        log.close()
        (output/'acceptance.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), indent=2))
