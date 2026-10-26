"""Descriptor locks with POSIX-compatible flags and contention exceptions.

POSIX keeps native flock semantics, including interoperability with existing
queue processes. Windows uses Portalocker's Win32 shared/exclusive locks.
This primitive does not replace callers' platform-specific path/ACL checks.
"""
from __future__ import annotations

import errno
import os

LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8

if os.name == 'nt':
    import portalocker

    def flock(descriptor: int, operation: int) -> None:
        if operation not in (LOCK_SH, LOCK_EX, LOCK_SH | LOCK_NB, LOCK_EX | LOCK_NB, LOCK_UN):
            raise ValueError('Unsupported file lock operation')
        try:
            if operation == LOCK_UN:
                portalocker.unlock(descriptor)
                return
            flags = portalocker.LOCK_EX if operation & LOCK_EX else portalocker.LOCK_SH
            if operation & LOCK_NB:
                flags |= portalocker.LOCK_NB
            portalocker.lock(descriptor, flags)
        except portalocker.exceptions.AlreadyLocked as error:
            raise BlockingIOError(errno.EAGAIN, 'File lock is held by another process') from error
        except portalocker.exceptions.LockException as error:
            raise OSError(errno.EIO, 'File lock operation failed') from error
else:
    from fcntl import flock, LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN
