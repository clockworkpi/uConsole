"""Owned, headless VM controls shared by Workbench and automation adapters."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid

from forge_boot import refresh_boot
from forge_workspace import WorkspaceLock
from forge_scenario import Scenario
from forge_audio import RECORDING_MODES


class Runtime:
    """One workspace, process and private control namespace.

    No automatic hard poweroff: callers must explicitly acknowledge forced
    stopping. A dead child never authorizes connecting to a recycled endpoint.
    """
    def __init__(self, args):
        self.args = args
        self.workspace = args.workspace.resolve()
        self.identity = 'uconsole-forge-' + uuid.uuid4().hex
        self.process = None
        self.lock = self.directory = self.output = None
        self.serial_mutex = threading.Lock()
        self.control_mutex = threading.Lock()
        self.guest_channel_error = None
        self.active_guest_job = None
        self.audio_recording = None
        self.modem = None

    @property
    def qmp_endpoint(self):
        if not self.directory:
            raise ValueError('Runtime has no control endpoints')
        return Path(self.directory.name) / 'qmp'

    @property
    def serial_endpoint(self):
        if not self.directory:
            raise ValueError('Runtime has no control endpoints')
        return Path(self.directory.name) / 'serial'

    def start(self, *, check_cancel=None, initial_scenario=None):
        from uconsole_emulator import command
        if self.process is not None:
            raise ValueError('Runtime already started')
        scenario_path = getattr(self.args, 'scenario', None)
        if initial_scenario is not None and not isinstance(initial_scenario, Scenario):
            raise ValueError('Initial scenario must be a validated snapshot')
        if initial_scenario is not None and scenario_path:
            raise ValueError('Specify only one initial scenario')
        scenario = initial_scenario or (Scenario.load(scenario_path) if scenario_path else None)
        with_modem = getattr(self.args, 'modem', 'none') == 'composite'
        self.lock = WorkspaceLock(self.workspace)
        try:
            if check_cancel:
                check_cancel()
            refresh_boot(self.workspace, self.args.qemu_img)
            # Boot-artifact refresh has its own publication sequence. Finish
            # it before honouring cancellation; do not launch or erase logs.
            if check_cancel:
                check_cancel()
            self.directory = tempfile.TemporaryDirectory(
                prefix='uc-vm-', dir='/tmp' if os.name != 'nt' else None)
            if getattr(self.args, 'audio', 'none') in RECORDING_MODES:
                # Never reuse a user-selected path or overwrite an earlier
                # recording. Retain the private artifact after runtime release.
                recording_dir = Path(tempfile.mkdtemp(prefix='audio-', dir=self.workspace))
                self.audio_recording = recording_dir / 'playback.wav'
                fd = os.open(self.audio_recording, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                cmd = command(self.args, Path(self.directory.name), audio_output=self.audio_recording)
            else:
                cmd = command(self.args, Path(self.directory.name))
            if (scenario or with_modem) and '-S' not in cmd:
                cmd += ['-S']
            cmd += ['-name', self.identity]
            # The workspace lock excludes forge writers before touching logs.
            (self.workspace / 'serial.log').write_bytes(b'')
            (self.workspace / 'last-command.json').write_text(json.dumps(cmd, indent=2) + '\n')
            self.output = (self.workspace / 'workbench-qemu.log').open('wb')
            self.process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                            stdout=self.output, stderr=self.output,
                                            pass_fds=(self.lock.fileno(),))
            if scenario or with_modem:
                deadline = time.monotonic() + 15
                while not self.qmp_endpoint.exists():
                    if check_cancel:
                        check_cancel()
                    self.require_alive()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Scenario QMP endpoint did not become ready')
                    try:
                        self.process.wait(timeout=0.05)
                    except subprocess.TimeoutExpired:
                        pass
                if check_cancel:
                    check_cancel()
                if with_modem:
                    from forge_modem_runtime import OwnedModem
                    self.modem = OwnedModem(self)
                    self.modem.start()
                if scenario:
                    evidence = scenario.apply(self.control)
                    evidence['runtime_identity'] = self.identity
                    with (self.workspace / f'scenario-{self.identity}.json').open('x') as record:
                        json.dump(evidence, record, indent=2)
                        record.write('\n')
                if check_cancel:
                    check_cancel()
                if not self.args.pause:
                    self.control('cont')
        except BaseException:
            # A failed initial scenario must not leave a paused QEMU holding
            # the workspace. This is the exact child we launched, not a PID
            # discovered in a state file or another client's endpoint.
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
            self.release()
            raise
        return self

    def require_alive(self):
        if self.process is None or self.process.poll() is not None:
            raise ValueError('Owned emulator is not running')

    def control(self, operation, arguments=None):
        with self.control_mutex:
            return self._control_locked(operation, arguments)

    def observe(self, operation, arguments=None):
        """Read-only, low-priority sampling; never queue behind a control job."""
        if operation not in {'query-status', 'query-blockstats', 'qom-list', 'qom-get'}:
            raise ValueError('Operation is not a read-only observation')
        if not self.control_mutex.acquire(blocking=False):
            raise BlockingIOError('Owned runtime control is busy')
        try:
            return self._control_locked(operation, arguments)
        finally:
            self.control_mutex.release()

    def _control_locked(self, operation, arguments=None):
        from uconsole_emulator import qmp
        self.require_alive()
        identity = qmp(self.qmp_endpoint, 'query-name')
        if identity.get('name') != self.identity:
            raise ValueError('QMP runtime identity mismatch')
        self.require_alive()
        return qmp(self.qmp_endpoint, operation, arguments)

    def connect_serial(self):
        from uconsole_emulator import control_connection
        self.require_alive()
        return control_connection(self.serial_endpoint)

    def guest_operation(self, operation, *args, **kwargs):
        """Serialize complete guest transactions, including multi-chunk copies."""
        from uconsole_agent import GuestChannelUncertain
        if self.args.mode != 'maintenance':
            raise ValueError('Guest commands require maintenance mode')
        if not self.serial_mutex.acquire(blocking=False):
            raise ValueError('Another guest operation is in progress')
        try:
            self.require_alive()
            if self.guest_channel_error:
                raise ValueError('Guest serial channel is uncertain after an unacknowledged command; '
                                 'further commands, transfers and clean stop are disabled. '
                                 'An explicit forced stop/restart may lose guest data.')
            try:
                return operation(self.serial_endpoint, *args, **kwargs)
            except GuestChannelUncertain as exc:
                self.guest_channel_error = str(exc)
                raise
        finally:
            self.serial_mutex.release()

    def execute(self, script, timeout=60, *, cancel=None):
        from uconsole_agent import serial_exec
        if cancel is not None:
            from forge_guest_jobs import execute
            def started(state):
                self.active_guest_job = state
            try:
                return self.guest_operation(execute, script, timeout=timeout,
                                            cancel=cancel, on_started=started)
            finally:
                if not self.guest_channel_error:
                    self.active_guest_job = None
        return self.guest_operation(serial_exec, script, timeout=timeout)

    def upload(self, source, destination, *, cancel=None):
        from uconsole_agent import guest_put
        return self.guest_operation(guest_put, Path(source), destination, cancel=cancel)

    def download(self, source, destination, *, cancel=None):
        from uconsole_agent import guest_get
        return self.guest_operation(guest_get, source, Path(destination), cancel=cancel)

    def stop(self, *, force=False):
        """Clean maintenance shutdown, or explicit forced power removal."""
        if force:
            self.require_alive()
            if self.modem:
                try:
                    self.modem.stop()
                except RuntimeError:
                    # Explicit forced stopping still terminates our exact VM
                    # after an uncertain modem link update. Report the error,
                    # but never strand the user behind that failed control path.
                    self.process.terminate()
                    self.process.wait(timeout=10)
                    self.release()
                    raise
            self.process.terminate()  # Only the actual child, never another VM.
        else:
            if self.args.mode != 'maintenance':
                raise ValueError('Shut down inside the guest; forced stopping requires force=True')
            result = self.execute(
                'mountpoint -q /proc || mount -t proc proc /proc; '
                'mountpoint -q /sys || mount -t sysfs sys /sys; '
                "rootmm=$(awk '$5 == \"/\" {print $3; exit}' /proc/self/mountinfo); "
                'rootdev=/dev/$(sed -n \'s/^DEVNAME=//p\' /sys/dev/block/$rootmm/uevent); '
                'test -b "$rootdev" && sync && mount -o remount,ro "$rootdev" /', timeout=60)
            if result['exit_code']:
                raise ValueError('Guest refused read-only remount; emulator left running')
            if self.modem:
                self.modem.stop()
            self.control('quit')
        self.process.wait(timeout=10)
        self.release()

    def release(self):
        if self.process is not None and self.process.poll() is None:
            raise ValueError('Cannot release ownership of a running emulator')
        if self.modem:
            self.modem.stop(vm_dead=True)
            self.modem = None
        if self.output:
            self.output.close()
            self.output = None
        if self.directory:
            self.directory.cleanup()
            self.directory = None
        if self.lock:
            self.lock.close()
            self.lock = None
