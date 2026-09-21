"""Cross-worker conversation exclusion, independent of ORM transactions."""

import hashlib
import threading
import time
from contextlib import contextmanager

from sqlalchemy import text

_registry_guard = threading.Lock()
_registry = {}


class ChatBusyError(RuntimeError):
    pass


def lock_key(tenant_id, case_id):
    value = f"onboarding-chat:{len(tenant_id)}:{tenant_id}:{case_id}"
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big", signed=True)


@contextmanager
def conversation_lock(engine, tenant_id, case_id, *, development=False, timeout=10.0):
    key = lock_key(tenant_id, case_id)
    if engine.dialect.name == "sqlite":
        if not development:
            raise RuntimeError("SQLite conversation locks are development-only")
        with _registry_guard:
            entry = _registry.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        acquired = False
        try:
            acquired = entry[0].acquire(timeout=timeout)
            if not acquired:
                raise ChatBusyError("Another message is being processed for this case")
            yield
        finally:
            if acquired:
                entry[0].release()
            with _registry_guard:
                entry[1] -= 1
                if not entry[1]:
                    del _registry[key]
        return
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Conversation coordination requires PostgreSQL")
    # Dedicated connection: domain-service commits cannot release this session lock.
    # Autocommit avoids retaining an idle transaction during a model response.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        acquired = False
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    acquired = bool(
                        connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
                    )
                except Exception:
                    # An interrupted response leaves lock acquisition ambiguous.
                    connection.invalidate()
                    raise
                if acquired:
                    break
                if time.monotonic() >= deadline:
                    raise ChatBusyError("Another message is being processed for this case")
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            yield
        finally:
            if acquired:
                try:
                    released = connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                    ).scalar()
                    if not released:
                        connection.invalidate()
                except Exception:
                    # Never return a possibly locked session to the connection pool.
                    connection.invalidate()
                    raise
