"""Bounded guest traffic producer for disconnect-under-load acceptance."""
import re

PROBE = r'''import json, pathlib, select, subprocess, sys, time
prefix = pathlib.Path(sys.argv[1])
interfaces = [p.name for p in pathlib.Path('/sys/class/net').iterdir()
              if (p/'device'/'driver').resolve().name == 'rndis_host']
if len(interfaces) != 1:
    raise RuntimeError('Expected one RNDIS interface in disposable guest')
process = subprocess.Popen(['ping', '-n', '-I', interfaces[0], '-i', '0.05',
                            '-w', '8', '10.0.3.2'], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
lines = []
ready = False
try:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if select.select([process.stdout], [], [], 0.1)[0]:
            line = process.stdout.readline()
            if not line:
                break
            lines.append(line)
            if not ready and 'bytes from 10.0.3.2:' in line:
                if process.poll() is not None:
                    raise RuntimeError('Traffic ended before readiness')
                prefix.with_suffix('.ready').write_text('traffic-active\n')
                ready = True
        if process.poll() is not None:
            lines.append(process.stdout.read())
            break
    status = process.wait(timeout=1)
    if not ready or status not in (0, 1):
        raise RuntimeError('Traffic producer did not establish a working link')
    prefix.with_suffix('.json').write_text(json.dumps(dict(
        status='completed', initial_reply=True, returncode=status,
        stdout=''.join(lines))))
finally:
    if process.poll() is None:
        process.kill()
    process.wait()
'''


def validate_result(result):
    """Loss during unplug is expected; a failed producer is not acceptance."""
    if (result.get('status') != 'completed' or result.get('initial_reply') is not True
            or type(result.get('returncode')) is not int
            or result['returncode'] not in (0, 1)
            or 'packets transmitted' not in result.get('stdout', '')):
        raise RuntimeError('Incomplete hotplug traffic evidence')
    counts = re.search(r'(\d+) packets transmitted, (\d+) received', result['stdout'])
    if not counts or int(counts[1]) < 2 or not 1 <= int(counts[2]) <= int(counts[1]):
        raise RuntimeError('Incomplete hotplug traffic packet counts')
