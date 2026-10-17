"""Cancellable execution of external conversion tools."""

from __future__ import annotations

import os
import subprocess
import threading


_UTF8_LOCALE = "C.UTF-8"


class ConversionCancelled(RuntimeError):
    """Raised when the user cancels a running conversion."""


class ConversionProcessError(RuntimeError):
    """Raised when an external process fails."""


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass

    def check(self) -> None:
        if self.is_cancelled:
            raise ConversionCancelled("Конвертация отменена.")

    def run(
        self,
        command: list[str],
        step_name: str,
        *,
        cwd: str | None = None,
    ) -> str:
        self.check()
        try:
            environment = os.environ.copy()
            environment["LC_CTYPE"] = _UTF8_LOCALE
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                env=environment,
            )
        except FileNotFoundError as error:
            raise ConversionProcessError(f"Не найдена команда {command[0]}.") from error

        with self._lock:
            self._process = process
            if self.is_cancelled and process.poll() is None:
                process.terminate()

        try:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if self.is_cancelled and process.poll() is None:
                        process.terminate()
                        try:
                            stdout, stderr = process.communicate(timeout=2)
                            break
                        except subprocess.TimeoutExpired:
                            process.kill()
                            stdout, stderr = process.communicate()
                            break
            self.check()
            if process.returncode:
                detail = (stderr or stdout).strip()
                message = (
                    f"Ошибка на этапе «{step_name}» "
                    f"(код {process.returncode})."
                )
                raise ConversionProcessError(
                    message + (f"\n{detail}" if detail else "")
                )
            return stdout
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
            if process.poll() is None:
                process.kill()
                process.wait()
