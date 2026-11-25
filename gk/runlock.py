"""Only ONE real (paid or free-quota) run at a time per project folder.

Two runs at once would share the API key's per-minute limit and write to the same evidence files
(the hash-chained experiment log cannot have two writers). The lock is a small file that the
running process touches every few seconds; if it has not been touched for 90 s the owner is gone
(crashed or closed) and the lock is taken over. No process ids are used, so it behaves the same on
Windows and Linux.
"""
from __future__ import annotations

import atexit
import json
import os
import threading
import time

LOCK = "real_run.lock"
STALE = 90.0
BEAT = 5.0


class RunLocked(RuntimeError):
    pass


class RunLock:
    def __init__(self, workdir: str = ".", what: str = "a real run"):
        self.path, self.what = os.path.join(workdir, LOCK), what
        self.token = f"{os.getpid()}-{time.time()}"
        self._stop = threading.Event()
        self.held = False

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, ValueError):
            return {}

    def acquire(self) -> "RunLock":
        if os.path.exists(self.path):
            age = time.time() - os.path.getmtime(self.path)
            if age < STALE:
                info = self._read()
                raise RunLocked(f"another real run is active in this folder ({info.get('what', 'unknown')}, "
                                f"started {info.get('started', '?')}). Wait for it to finish, or stop it first. "
                                f"Running two at once breaks the per-minute limit and the evidence log.")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"token": self.token, "what": self.what,
                       "started": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
        self.held = True
        threading.Thread(target=self._beat, daemon=True).start()
        atexit.register(self.release)
        return self

    def _beat(self) -> None:
        while not self._stop.wait(BEAT):
            if self._read().get("token") != self.token:
                return
            try:
                os.utime(self.path, None)
            except OSError:
                return

    def release(self) -> None:
        self._stop.set()
        if self.held and self._read().get("token") == self.token:
            try:
                os.remove(self.path)
            except OSError:
                pass
        self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
