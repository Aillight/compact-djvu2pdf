from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFileOpenEvent

from core.converter import ConversionProfile
from core.system_check import ToolPaths
from gui.main_window import MainWindow


class RichUiEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tools = ToolPaths(
            Path("/bin/true"),
            Path("/bin/true"),
            Path("/bin/true"),
            Path("/bin/true"),
        )
        self.window = MainWindow(self.tools)

    def tearDown(self) -> None:
        if self.window._worker is not None:
            self.window._worker.cancel()
            self.window._worker.wait(3000)
            self.window._worker = None
        self.window.close()
        self.window.deleteLater()
        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        self.temporary.cleanup()

    def test_folder_adds_multiple_files_and_queue_runs_sequentially(self) -> None:
        source_folder = self.root / "input"
        nested = source_folder / "nested"
        nested.mkdir(parents=True)
        inputs = (
            source_folder / "one.djvu",
            nested / "two.djv",
            nested / "three.djvu",
        )
        for index, path in enumerate(inputs, start=1):
            path.write_bytes(b"source" * index)
        (nested / "ignore.txt").write_text("not a DjVu", encoding="utf-8")

        self.window.add_paths([str(source_folder)])
        self.assertEqual(self.window.queue_list.count(), 3)
        self.assertIn("В очереди", self.window.queue_list.item(0).text())

        output_folder = self.root / "pdf"
        output_folder.mkdir()
        self.window.output_folder_radio.setChecked(True)
        self.window._output_folder = output_folder

        def fake_convert(
            input_path: Path,
            output_path: Path,
            *,
            progress,
            cancellation,
            **_options,
        ) -> Path:
            cancellation.check()
            progress("test conversion", 50)
            output_path.write_bytes(b"pdf" + input_path.read_bytes())
            progress("done", 100)
            return output_path

        with (
            patch("gui.worker.convert", side_effect=fake_convert),
            patch.object(self.window, "_send_completion_notification"),
        ):
            self.window.start_queue()
            deadline = time.monotonic() + 10
            while (
                self.window.status_label.text() != "Очередь обработана"
                and time.monotonic() < deadline
            ):
                self.application.processEvents()
                time.sleep(0.01)
            self.application.processEvents()

        self.assertEqual(self.window.status_label.text(), "Очередь обработана")
        self.assertIsNone(self.window._worker)
        self.assertTrue(all(job.status == "Готово" for job in self.window._jobs))
        self.assertTrue(
            all((output_folder / path.with_suffix(".pdf").name).is_file() for path in inputs)
        )
        self.assertIn("Суммарный прирост", self.window.savings_label.text())

    @unittest.skipUnless(
        shutil.which("ddjvu")
        and Path(__file__).parent.joinpath(
            "fixtures", "Gusev_Nanokristallicheskie_materialy.djvu"
        ).is_file(),
        "Requires installed ddjvu and the real DjVu fixture",
    )
    def test_selecting_real_document_renders_first_page_preview(self) -> None:
        fixture = (
            Path(__file__).parent
            / "fixtures"
            / "Gusev_Nanokristallicheskie_materialy.djvu"
        )
        self.window._tools = ToolPaths(
            Path(shutil.which("ddjvu") or "/bin/true"),
            self.tools.djvused,
            self.tools.jbig2,
            self.tools.djvudump,
        )
        self.window.add_paths([str(fixture)])

        deadline = time.monotonic() + 5
        while self.window._preview_process is not None and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)

        self.assertIsNone(self.window._preview_process)
        self.assertIsNotNone(self.window.preview_label.pixmap())
        self.assertFalse(self.window.preview_label.pixmap().isNull())

    def test_completed_jobs_can_be_cleared(self) -> None:
        source = self.root / "done.djvu"
        source.write_bytes(b"test")
        self.window.add_paths([str(source)])
        self.window._jobs[0].status = "Готово"
        self.window._refresh_job_row(0)

        self.window.clear_completed()

        self.assertEqual(self.window.queue_list.count(), 0)
        self.assertEqual(self.window._jobs, [])

    def test_macos_file_open_event_adds_document_to_queue(self) -> None:
        source = self.root / "opened-from-finder.djvu"
        source.write_bytes(b"source")
        event = QFileOpenEvent(str(source))

        handled = self.window.eventFilter(self.application, event)

        self.assertTrue(handled)
        self.assertEqual(len(self.window._jobs), 1)
        self.assertEqual(self.window._jobs[0].input_path, source.resolve())

    def test_black_and_white_profile_is_available_in_ui(self) -> None:
        self.assertEqual(
            self.window.profile_combo.findData(ConversionProfile.AUTO),
            0,
        )
        self.assertGreaterEqual(
            self.window.profile_combo.findData(
                ConversionProfile.CLEAN_BLACK_AND_WHITE
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
