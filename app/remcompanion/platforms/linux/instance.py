"""Linux per-user desktop/installer lease and activation; no filesystem locks.

The installer holds the same abstract socket while changing program files.
Only an activation token is accepted: this is not a remote command interface.
"""
import errno
import hashlib
import os
import socket
import weakref

_instance_ref = None
_DEFAULT_NAME = "Local\\RemCompanionUI"


def address(name=_DEFAULT_NAME):
    tag = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return b"\0RemChat." + str(os.getuid()).encode("ascii") + b"." + tag.encode("ascii")


def acquire_single_instance(name=_DEFAULT_NAME):
    """Return a lifetime lease, None if occupied; unexpected OS errors propagate."""
    global _instance_ref
    handle = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        handle.bind(address(name))
        handle.setblocking(False)
    except OSError as error:
        handle.close()
        if error.errno == errno.EADDRINUSE:
            return None
        raise
    _instance_ref = weakref.ref(handle)
    return handle


def signal_existing_instance():
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
            sender.settimeout(0.2)
            sender.sendto(b"activate", address())
        return True
    except OSError:
        return False


def activation_pending():
    """Drain a bounded batch so local senders cannot starve Tk's event loop."""
    handle = _instance_ref() if _instance_ref is not None else None
    if handle is None or handle.fileno() < 0:
        return False
    activate = False
    for _ in range(16):
        try:
            activate = handle.recv(32) == b"activate" or activate
        except BlockingIOError:
            break
    return activate
