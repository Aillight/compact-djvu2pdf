from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from core.converter import ConversionProfile
from core.system_check import ToolPaths
from gui.main_window import MainWindow


class ConversionProfileUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.djvu"
        self.source.write_bytes(b"source")
        tools = ToolPaths(
            Path("/bin/true"),
            Path("/bin/true"),
            Path("/bin/true"),
            Path("/bin/true"),
        )
        self.window = MainWindow(tools)

    def tearDown(self) -> None:
        if self.window._preview_process is not None:
            self.window._show_empty_preview()
            self.application.processEvents()
        self.window.close()
        self.window.deleteLater()
        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        self.temporary.cleanup()

    def test_auto_profile_is_selected_by_default(self) -> None:
        self.assertEqual(
            self.window.profile_combo.currentData(),
            ConversionProfile.AUTO,
        )

    def test_profile_selector_string_data_is_converted_to_enum(self) -> None:
        self.window.add_paths([str(self.source)])
        self.window.profile_combo.setItemData(0, "auto")

        worker = Mock()
        worker.progress_changed.connect = Mock()
        worker.finished.connect = Mock()
        with patch("gui.main_window.ConversionWorker", return_value=worker) as factory:
            self.window._start_next_job()

        self.assertIs(factory.call_args.args[-1], ConversionProfile.AUTO)
        worker.start.assert_called_once_with()
        self.window._worker = None

    def test_selected_black_and_white_profile_is_passed_to_worker(self) -> None:
        self.window.add_paths([str(self.source)])
        index = self.window.profile_combo.findData(
            ConversionProfile.CLEAN_BLACK_AND_WHITE
        )
        self.assertGreaterEqual(index, 0)
        self.window.profile_combo.setCurrentIndex(index)

        worker = Mock()
        worker.progress_changed.connect = Mock()
        worker.finished.connect = Mock()
        with patch("gui.main_window.ConversionWorker", return_value=worker) as factory:
            self.window._start_next_job()

        self.assertEqual(factory.call_args.args[-1], ConversionProfile.CLEAN_BLACK_AND_WHITE)
        worker.start.assert_called_once_with()
        self.window._worker = None

    def test_completion_notification_uses_escaped_native_script(self) -> None:
        with (
            patch("gui.main_window.sys.platform", "darwin"),
            patch("gui.main_window.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            run.return_value.stderr = ""
            self.window._send_completion_notification(3)

        command = run.call_args.args[0]
        self.assertEqual(command[0:2], ["/usr/bin/osascript", "-e"])
        self.assertIn('display notification "Готово файлов: 3."', command[2])
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
