from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from core.process import CancellationToken, ConversionCancelled


class CancellationTests(unittest.TestCase):
    def test_external_process_gets_utf8_locale_without_inherited_locale(self) -> None:
        with patch.dict("os.environ", {"PATH": ""}, clear=True):
            output = CancellationToken().run(
                [
                    sys.executable,
                    "-c",
                    "import os; print(os.environ.get('LC_CTYPE', ''))",
                ],
                "UTF-8 locale test",
            )

        self.assertEqual(output.strip(), "C.UTF-8")

    def test_process_can_run_with_input_directory_as_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token = CancellationToken()
            output = token.run(
                [sys.executable, "-c", "import os; print(os.getcwd())"],
                "working directory test",
                cwd=directory,
            )

        self.assertEqual(Path(output.strip()).resolve(), Path(directory).resolve())

    def test_cancel_terminates_active_subprocess(self) -> None:
        token = CancellationToken()
        outcome: list[BaseException] = []

        def run() -> None:
            try:
                token.run(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    "test process",
                )
            except BaseException as error:
                outcome.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 3
        while token._process is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(token._process, "subprocess did not start")
        token.cancel()
        thread.join(timeout=3)

        self.assertFalse(thread.is_alive(), "cancel did not stop subprocess promptly")
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], ConversionCancelled)


if __name__ == "__main__":
    unittest.main()
