"""Linux local lease endpoint for the authenticated recovery SSH helper.

Not enabled by the initramfs yet. The caller supplies a synchronous deadline
publisher and must stop servicing the watchdog if publication or polling fails.
Only a successful publication may produce a renewal receipt. This endpoint
never grants disk write access or changes boot selection.
"""
import json
import os
from pathlib import Path
import socket
import stat
import struct
import time


LIMIT = 4096


def decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate lease field')
            result[key] = value
        return result
    if not data or len(data) > LIMIT:
        raise ValueError('Invalid lease packet size')
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def peer_uid(connection):
    return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


class LeaseEndpoint:
    def __init__(self, path, lease, publish, *, now=time.monotonic):
        self.path = Path(path).absolute()
        self.lease, self.publish, self.now = lease, publish, now
        self.failed = False
        self.listener = None
        self.inode = None
        parent = self.path.parent
        info = parent.lstat()
        if (parent.resolve() != parent or not stat.S_ISDIR(info.st_mode) or
                info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError('Lease socket needs a private owned nonsymlink directory')
        lease.check(now())
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            # bind is exclusive; never unlink an existing or stale endpoint.
            listener.bind(str(self.path))
            self.inode = self.path.lstat().st_ino
            os.chmod(self.path, 0o600)
            listener.listen(4)
            listener.settimeout(0.2)
            self.listener = listener
        except BaseException:
            listener.close()
            self.close()
            raise

    def poll(self):
        """Serve at most one bounded packet; caller must tick timers each poll.

        Invalid peers/packets cannot extend a lease. Publication failures poison
        the endpoint rather than permitting a retry with uncertain timer state.
        """
        if self.failed or self.listener is None:
            raise RuntimeError('Lease endpoint is unavailable')
        try:
            self.lease.check(self.now())
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                self.lease.check(self.now())
                return
            with connection:
                connection.settimeout(0.2)
                if peer_uid(connection) != os.geteuid():
                    return
                try:
                    data, _, flags, _ = connection.recvmsg(LIMIT)
                    if flags & socket.MSG_TRUNC:
                        raise ValueError('Oversized lease packet')
                    candidate = self.lease.renew(decode(data), self.now())
                except (ValueError, TypeError, RecursionError, UnicodeError, OSError):
                    self.lease.check(self.now())
                    return
                # A publisher exception is fatal: never acknowledge uncertain
                # timer state or continue to feed a watchdog on its behalf.
                self.publish(candidate)
                candidate.check(self.now())
                self.lease = candidate
                try:
                    connection.send(json.dumps(candidate.receipt()).encode())
                except OSError:
                    # Committed request, lost reply: identical retry is safe.
                    pass
        except BaseException:
            self.failed = True
            raise

    def close(self):
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        if self.inode is not None:
            try:
                info = self.path.lstat()
                if stat.S_ISSOCK(info.st_mode) and info.st_ino == self.inode:
                    self.path.unlink()
            except FileNotFoundError:
                pass
            self.inode = None


def exchange(path, request):
    """Called inside pinned SSH; authenticate the local endpoint as this UID."""
    data = json.dumps(request, allow_nan=False).encode()
    if len(data) > LIMIT:
        raise ValueError('Oversized lease request')
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
        connection.settimeout(2)
        connection.connect(str(path))
        if peer_uid(connection) != os.geteuid():
            raise ValueError('Lease server belongs to another user')
        connection.send(data)
        data, _, flags, _ = connection.recvmsg(LIMIT)
        if flags & socket.MSG_TRUNC:
            raise ValueError('Oversized lease receipt')
        return decode(data)
