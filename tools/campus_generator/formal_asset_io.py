"""Shared locking and atomic-write primitives for formal campus assets."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path


_THREAD_LOCK = threading.Lock()


def sync_directory(path: str | Path) -> None:
    """Persist directory-entry changes where the platform exposes fsync."""
    if os.name == "nt":
        # The temporary file itself is flushed before replacement.  Python's
        # standard library does not expose a portable Windows directory flush.
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(Path(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def formal_asset_lock(lock_path: str | Path, timeout_seconds: float = 30):
    """Serialize formal-asset writers across threads and local processes."""
    started = time.monotonic()
    if not _THREAD_LOCK.acquire(timeout=timeout_seconds):
        raise TimeoutError("timed out waiting for the campus publication lock")
    handle = None
    unlock_file = None
    try:
        path = Path(lock_path)
        if path.is_symlink():
            raise ValueError("formal asset lock path must not be a symlink")
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = started + timeout_seconds
        if os.name == "nt":
            import msvcrt

            def try_lock():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def unlock_file():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def try_lock():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock_file():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        while True:
            try:
                try_lock()
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "timed out waiting for the campus publication lock"
                    ) from exc
                time.sleep(0.1)
        yield
    finally:
        try:
            if unlock_file is not None:
                try:
                    unlock_file()
                except OSError:
                    pass
        finally:
            try:
                if handle is not None:
                    handle.close()
            finally:
                _THREAD_LOCK.release()


def atomic_write_json(path: str | Path, value) -> None:
    """Write JSON to a same-directory temporary file, then atomically replace."""
    target = Path(path)
    if target.is_symlink():
        raise ValueError(f"atomic JSON target must not be a symlink: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target_mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", delete=False,
                dir=target.parent, prefix=f".{target.name}.", suffix=".tmp") as temporary:
            temporary_name = temporary.name
            json.dump(value, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
            temporary.flush()
            if target_mode is not None:
                os.chmod(temporary.name, target_mode)
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
        temporary_name = None
        sync_directory(target.parent)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def atomic_copy(source: str | Path, target: str | Path) -> None:
    """Copy bytes through a same-directory temporary file and atomic replace."""
    source_path, target_path = Path(source), Path(target)
    if target_path.is_symlink():
        raise ValueError(f"atomic copy target must not be a symlink: {target_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_mode = (
        stat.S_IMODE(target_path.stat().st_mode) if target_path.exists() else None
    )
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", delete=False, dir=target_path.parent,
                prefix=f".{target_path.name}.", suffix=".restore.tmp") as temporary:
            temporary_name = temporary.name
            with source_path.open("rb") as backup:
                while True:
                    chunk = backup.read(1024 * 1024)
                    if not chunk:
                        break
                    temporary.write(chunk)
            temporary.flush()
            if target_mode is not None:
                os.chmod(temporary.name, target_mode)
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target_path)
        temporary_name = None
        sync_directory(target_path.parent)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
