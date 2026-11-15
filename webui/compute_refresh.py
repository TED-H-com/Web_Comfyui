"""Process-local refresh locks shared by HTTP, pipelines and collection."""
from pathlib import Path
import threading
from weakref import WeakValueDictionary


_guard = threading.Lock()
_locks = WeakValueDictionary()


def request_lock(path, owner, request_id):
    # Keep a strong reference while acquiring/holding the lock. Idle entries
    # disappear automatically; unrelated requests never share a refresh lock.
    key = (str(Path(path).resolve()), owner, request_id)
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _locks[key] = lock
        return lock
