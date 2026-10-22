"""Керування дочірнім процесом ядра (python -m friday) без блокування GUI."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from enum import StrEnum
from typing import Callable

from friday.config import PROJECT_ROOT
from friday.launcher.services.env_store import mask_secret


class CoreProcessStatus(StrEnum):
    """Статуси дочірнього процесу FRIDAY."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


def sanitize_log_line(line: str, secrets_to_redact: list[str] | None = None) -> str:
    """Гарантує, що секретні ключі ніколи не з'являться у панелі логів лаунчера."""
    out = line
    for secret in secrets_to_redact or []:
        s = (secret or "").strip()
        if len(s) > 4 and s in out:
            out = out.replace(s, mask_secret(s))
    return out


class CoreProcessManager:
    """Запускає, зупиняє та моніторить ядро FRIDAY як окремий дочірній процес без консольного вікна."""

    def __init__(
        self,
        command: list[str] | None = None,
        cwd: str | None = None,
        on_status_change: Callable[[CoreProcessStatus, str], None] | None = None,
        on_log_line: Callable[[str], None] | None = None,
    ) -> None:
        self.command = command or [sys.executable, "-m", "friday"]
        self.cwd = cwd or str(PROJECT_ROOT)
        self._on_status_change = on_status_change
        self._on_log_line = on_log_line

        self._proc: subprocess.Popen[str] | None = None
        self._status: CoreProcessStatus = CoreProcessStatus.STOPPED
        self._last_error: str = ""
        self._stopping_intentionally: bool = False
        self._lock = threading.Lock()
        self._output_buffer: deque[str] = deque(maxlen=500)
        self._secrets_to_redact: list[str] = []

    @property
    def status(self) -> CoreProcessStatus:
        return self._status

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def output_lines(self) -> list[str]:
        return list(self._output_buffer)

    def set_redacted_secrets(self, secrets: list[str]) -> None:
        self._secrets_to_redact = [s for s in secrets if s]

    def _set_status(self, new_status: CoreProcessStatus, detail: str = "") -> None:
        self._status = new_status
        if new_status == CoreProcessStatus.ERROR:
            self._last_error = detail
        elif new_status in (CoreProcessStatus.RUNNING, CoreProcessStatus.STOPPED):
            self._last_error = ""
        if self._on_status_change is not None:
            self._on_status_change(new_status, detail)

    def start(self) -> bool:
        """Запускає дочірній процес ядра (якщо він ще не запущений — захист від подвійного запуску)."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True

            self._stopping_intentionally = False
            self._set_status(CoreProcessStatus.STARTING)

            creationflags = 0
            if os.name == "nt":
                creationflags = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                )

            try:
                proc = subprocess.Popen(
                    self.command,
                    cwd=self.cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )
            except Exception as exc:
                self._set_status(CoreProcessStatus.ERROR, f"Не вдалося запустити процес: {exc}")
                return False

            self._proc = proc
            threading.Thread(
                target=self._reader_and_watcher_thread,
                args=(proc,),
                name="friday-core-watcher",
                daemon=True,
            ).start()
            return True

    def _reader_and_watcher_thread(self, proc: subprocess.Popen[str]) -> None:
        # Якщо процес живий після короткої паузи ініціалізації — переводимо у RUNNING
        time.sleep(0.12)
        if proc.poll() is None and not self._stopping_intentionally:
            self._set_status(CoreProcessStatus.RUNNING)

        if proc.stdout is not None:
            for raw_line in proc.stdout:
                clean = sanitize_log_line(raw_line.rstrip("\r\n"), self._secrets_to_redact)
                if clean:
                    self._output_buffer.append(clean)
                    if self._on_log_line is not None:
                        self._on_log_line(clean)
                    if self._status == CoreProcessStatus.STARTING and not self._stopping_intentionally:
                        self._set_status(CoreProcessStatus.RUNNING)

        rc = proc.wait()
        with self._lock:
            if self._proc is proc:
                self._proc = None

        if self._stopping_intentionally:
            self._set_status(CoreProcessStatus.STOPPED)
        elif rc != 0:
            self._set_status(
                CoreProcessStatus.ERROR,
                f"Процес ядра аварійно завершився (код виходу: {rc})",
            )
        else:
            self._set_status(CoreProcessStatus.STOPPED)

    def stop(self, timeout_s: float = 2.0) -> bool:
        """Коректно зупиняє ядро (спершу м'який terminate, потім примусовий kill за таймаутом)."""
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                self._proc = None
                self._set_status(CoreProcessStatus.STOPPED)
                return True
            self._stopping_intentionally = True

        try:
            proc.terminate()
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
        except Exception:
            pass

        with self._lock:
            self._proc = None
        self._set_status(CoreProcessStatus.STOPPED)
        return True

    def restart(self, timeout_s: float = 2.0) -> bool:
        """Перезапускає процес ядра."""
        self.stop(timeout_s=timeout_s)
        return self.start()
