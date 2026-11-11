"""Cooperative pause with Linux process-group suspension for compiler children."""
import os
import signal
import subprocess
import threading
import time


class PauseControl:
    """Freeze the active build and its descendants without losing process state."""

    def __init__(self):
        self._condition = threading.Condition()
        self._paused_at = None
        self._paused_seconds = 0.0
        self._process = None
        self._groups = set()

    @property
    def paused(self):
        with self._condition:
            return self._paused_at is not None

    def clock(self):
        """Return monotonic time excluding pauses (also frozen during a pause)."""
        with self._condition:
            now = self._paused_at if self._paused_at is not None else time.monotonic()
            return now - self._paused_seconds

    @staticmethod
    def _signal(groups, action):
        for group in groups:
            try:
                os.killpg(group, action)
            except ProcessLookupError:
                pass

    def _freeze(self, process):
        if process is None or process.poll() is not None:
            return
        # Stop the launcher first so it cannot spawn additional compiler children.
        try:
            os.kill(process.pid, signal.SIGSTOP)
        except ProcessLookupError:
            return
        from .core import process_groups
        groups = process_groups(process.pid)
        groups.add(process.pid)  # Every managed subprocess starts a new session.
        groups.discard(os.getpgrp())
        try:
            self._signal(groups, signal.SIGSTOP)
        except OSError:
            self._signal(groups, signal.SIGCONT)
            try:
                os.kill(process.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
            raise
        self._groups.update(groups)

    def attach(self, process):
        """Track exactly one compiler process; freeze it if pause was requested."""
        with self._condition:
            self._process = process
            if self._paused_at is not None:
                self._freeze(process)

    def detach(self, process):
        with self._condition:
            if self._process is process:
                self._process = None
            if self._paused_at is None:
                self._groups.clear()

    def pause(self):
        with self._condition:
            if self._paused_at is not None:
                return False
            self._paused_at = time.monotonic()
            try:
                self._freeze(self._process)
            except OSError:
                self._paused_at = None
                self._condition.notify_all()
                raise
            return True

    def resume(self):
        with self._condition:
            if self._paused_at is None:
                return False
            self._paused_seconds += time.monotonic() - self._paused_at
            self._paused_at = None
            groups = set(self._groups)
            self._groups.clear()
            try:
                self._signal(groups, signal.SIGCONT)
            finally:
                # Wake the worker even if the process has already exited.
                self._condition.notify_all()
            return True

    def wait(self, cancel):
        """Pause between build steps, but always allow cancellation to proceed."""
        with self._condition:
            while self._paused_at is not None:
                if cancel.is_set():
                    from .core import Cancelled
                    raise Cancelled()
                self._condition.wait(timeout=0.1)
            if cancel.is_set():
                from .core import Cancelled
                raise Cancelled()
