"""Private local MCP transport; runtime ownership stays with the caller."""
from collections import deque
import os
from pathlib import Path
import socket
import select
import stat
import threading
import time

from uconsole_mcp import Server
from forge_client import ClientSession


def private_parent(path):
    if os.name != 'posix':
        raise ValueError('Local attachment requires POSIX Unix sockets')
    path = Path(path).absolute()
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError('Socket directory must be owned, private (0700), and not a symlink')
    return path


def relay_stdio(path, input_fd, output_fd):
    """Bridge raw stdio FDs to an existing private endpoint, without authority.

    Bounded chunks and socket/pipe backpressure avoid buffering whole requests
    or replies. A wake pipe releases the input worker if the owner disconnects
    while the coding client still has stdin open.
    """
    path = private_parent(path)
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError('Attachment endpoint must be an owned private socket, not a link')
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    wake_read, wake_write = os.pipe()
    stopped = threading.Event()
    errors = []
    worker = None

    def send_input():
        try:
            while not stopped.is_set():
                ready, _, _ = select.select([input_fd, wake_read], [], [])
                if wake_read in ready:
                    return
                chunk = os.read(input_fd, 65536)
                if not chunk:
                    connection.shutdown(socket.SHUT_WR)
                    return
                connection.sendall(chunk)
        except OSError as exc:
            if not stopped.is_set():
                errors.append(exc)
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    try:
        connection.settimeout(5)
        connection.connect(str(path))
        connection.settimeout(None)
        worker = threading.Thread(target=send_input, name='forge-stdio-input', daemon=True)
        worker.start()
        while chunk := connection.recv(65536):
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(output_fd, remaining)
                if not written:
                    raise BrokenPipeError('MCP output closed')
                remaining = remaining[written:]
        if errors:
            raise errors[0]
    finally:
        stopped.set()
        os.write(wake_write, b'x')
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        connection.close()
        if worker and worker.ident is not None:
            worker.join(timeout=5)
        os.close(wake_read)
        os.close(wake_write)
        if worker and worker.is_alive():
            raise TimeoutError('Stdio attachment input worker did not stop')


class LocalListener:
    """Owner-created listener with bounded sessions and inode-safe cleanup.

    session_factory supplies restricted ClientSessions, never a discovered VM.
    Same-user processes can connect: this is a local-user boundary, not a
    sandbox against other applications running under the owner's identity.
    """
    def __init__(self, path, session_factory, *, max_clients=8, idle_timeout=60):
        if type(max_clients) is not int or not 1 <= max_clients <= 32:
            raise ValueError('Client limit must be 1..32')
        if not 0 < idle_timeout <= 3600:
            raise ValueError('Idle timeout must be positive and at most one hour')
        self.path = private_parent(path)
        self.session_factory = session_factory
        self.idle_timeout = idle_timeout
        self.slots = threading.BoundedSemaphore(max_clients)
        self.mutex = threading.Lock()
        self.clients, self.workers = set(), set()
        self.errors = deque(maxlen=16)
        self.stopped = threading.Event()
        self.identity = None
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Never unlink a pre-existing endpoint, including a stale socket.
            self.listener.bind(str(self.path))
            info = self.path.lstat()
            self.identity = (info.st_dev, info.st_ino)
            self.path.chmod(0o600)
            self.listener.listen(max_clients)
            self.listener.settimeout(0.2)
            self.thread = threading.Thread(target=self.accept, name='forge-local-listener', daemon=True)
            self.thread.start()
        except BaseException:
            self.listener.close()
            self.unlink_owned()
            raise

    def unlink_owned(self):
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return
        if self.identity == (info.st_dev, info.st_ino) and stat.S_ISSOCK(info.st_mode):
            self.path.unlink()

    def accept(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self.slots.acquire(blocking=False):
                connection.close()
                continue
            connection.settimeout(self.idle_timeout)
            worker = threading.Thread(target=self.serve, args=(connection,), daemon=True)
            with self.mutex:
                self.clients.add(connection)
                self.workers.add(worker)
            try:
                worker.start()
            except BaseException:
                with self.mutex:
                    self.clients.discard(connection)
                    self.workers.discard(worker)
                connection.close()
                self.slots.release()
                raise

    def serve(self, connection):
        session = None
        try:
            candidate = self.session_factory()
            if not isinstance(candidate, ClientSession):
                raise TypeError('Listener requires a restricted ClientSession, not an owning controller')
            session = candidate
            with connection.makefile('rb') as source, connection.makefile('w', encoding='utf-8') as destination:
                Server(session).serve(source, destination)
        except (OSError, TimeoutError):
            pass  # Disconnect/idle expiry never cancels accepted owner jobs.
        except Exception as exc:
            self.errors.append(str(exc)[:16384])
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception as exc:
                    self.errors.append(str(exc)[:16384])
            connection.close()
            with self.mutex:
                self.clients.discard(connection)
                self.workers.discard(threading.current_thread())
            self.slots.release()

    def close(self):
        self.stopped.set()
        self.listener.close()
        self.thread.join(timeout=2)
        with self.mutex:
            clients, workers = list(self.clients), list(self.workers)
        for connection in clients:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        deadline = time.monotonic() + 5
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        self.unlink_owned()
        if self.thread.is_alive() or any(worker.is_alive() for worker in workers):
            raise TimeoutError('Local sessions are still closing; owner/controller must remain alive')
