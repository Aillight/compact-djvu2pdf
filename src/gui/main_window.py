"""Batch-capable PySide6 main window."""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QProcess, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QFileOpenEvent, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from core.converter import ConversionProfile
from core.system_check import ToolPaths
from gui.worker import ConversionWorker

_FILE_ROLE = Qt.ItemDataRole.UserRole
_LOGGER = logging.getLogger(__name__)


@dataclass
class QueueJob:
    input_path: Path
    status: str = "В очереди"
    output_path: Path | None = None
    error: str | None = None


class DropArea(QFrame):
    paths_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropArea")
        self.setMinimumHeight(68)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        label = QLabel("Перетащите сюда файлы DjVu или папку с документами")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.addWidget(label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self, tools: ToolPaths) -> None:
        super().__init__()
        self._tools = tools
        self._worker: ConversionWorker | None = None
        self._preview_process: QProcess | None = None
        self._preview_path: Path | None = None
        self._jobs: list[QueueJob] = []
        self._active_job_index: int | None = None
        self._last_output_path: Path | None = None
        self._cancel_queue = False

        self.setWindowTitle("DjVu в PDF")
        self.setMinimumSize(760, 700)
        self.setAcceptDrops(True)

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        title = QLabel("DjVu → PDF")
        title.setObjectName("title")
        subtitle = QLabel("Пакетная конвертация с компактным JBIG2-сжатием")
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.drop_area = DropArea()
        self.drop_area.paths_dropped.connect(self.add_paths)
        layout.addWidget(self.drop_area)

        queue_buttons = QHBoxLayout()
        self.add_files_button = QPushButton("Добавить файлы…")
        self.add_files_button.clicked.connect(self._choose_files)
        self.add_folder_button = QPushButton("Добавить папку…")
        self.add_folder_button.clicked.connect(self._choose_folder)
        self.clear_button = QPushButton("Очистить завершенные")
        self.clear_button.clicked.connect(self.clear_completed)
        queue_buttons.addWidget(self.add_files_button)
        queue_buttons.addWidget(self.add_folder_button)
        queue_buttons.addStretch()
        queue_buttons.addWidget(self.clear_button)
        layout.addLayout(queue_buttons)

        queue_preview = QHBoxLayout()
        self.queue_list = QListWidget()
        self.queue_list.currentRowChanged.connect(self._selection_changed)
        queue_preview.addWidget(self.queue_list, 3)

        preview_panel = QVBoxLayout()
        preview_heading = QLabel("Первая страница")
        preview_heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label = QLabel("Выберите документ")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(170, 220)
        self.preview_label.setMaximumSize(210, 260)
        self.preview_label.setObjectName("preview")
        preview_panel.addWidget(preview_heading)
        preview_panel.addWidget(self.preview_label, alignment=Qt.AlignmentFlag.AlignCenter)
        preview_panel.addStretch()
        queue_preview.addLayout(preview_panel, 1)
        layout.addLayout(queue_preview, 1)

        self.text_checkbox = QCheckBox("Сохранять скрытый текстовый слой")
        self.text_checkbox.setChecked(True)
        self.dictionary_checkbox = QCheckBox("Пакетное сжатие словаря (JBIG2)")
        self.dictionary_checkbox.setChecked(True)
        profile_layout = QHBoxLayout()
        profile_label = QLabel("Профиль конвертации")
        self.profile_combo = QComboBox()
        self.profile_combo.addItem("Авто", ConversionProfile.AUTO)
        self.profile_combo.addItem(
            "Чистый ч/б (JBIG2)",
            ConversionProfile.CLEAN_BLACK_AND_WHITE,
        )
        profile_layout.addWidget(profile_label)
        profile_layout.addWidget(self.profile_combo)
        profile_layout.addStretch()
        layout.addLayout(profile_layout)
        layout.addWidget(self.text_checkbox)
        layout.addWidget(self.dictionary_checkbox)

        self.near_source_radio = QRadioButton("Сохранять рядом с исходником")
        self.near_source_radio.setChecked(True)
        self.output_folder_radio = QRadioButton("Выбрать папку для всех PDF")
        self.output_folder_radio.toggled.connect(self._output_mode_changed)
        self.output_folder_button = QPushButton("Выбрать папку…")
        self.output_folder_button.setEnabled(False)
        self.output_folder_button.clicked.connect(self._choose_output_folder)
        self.output_folder_label = QLabel("Папка не выбрана")
        output_layout = QHBoxLayout()
        output_layout.addWidget(self.near_source_radio)
        output_layout.addWidget(self.output_folder_radio)
        output_layout.addWidget(self.output_folder_button)
        layout.addLayout(output_layout)
        layout.addWidget(self.output_folder_label)
        self._output_folder: Path | None = None

        self.status_label = QLabel("Добавьте файлы для начала")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.savings_label = QLabel("Суммарная экономия: 0 B")
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.savings_label)

        action_layout = QHBoxLayout()
        self.convert_button = QPushButton("Конвертировать очередь")
        self.convert_button.setEnabled(False)
        self.convert_button.clicked.connect(self.start_queue)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_queue)
        self.open_pdf_button = QPushButton("Открыть PDF")
        self.open_pdf_button.setEnabled(False)
        self.open_pdf_button.clicked.connect(self.open_last_pdf)
        self.open_finder_button = QPushButton("Открыть в Finder")
        self.open_finder_button.setEnabled(False)
        self.open_finder_button.clicked.connect(self.open_last_in_finder)
        action_layout.addWidget(self.convert_button)
        action_layout.addWidget(self.cancel_button)
        action_layout.addStretch()
        action_layout.addWidget(self.open_pdf_button)
        action_layout.addWidget(self.open_finder_button)
        layout.addLayout(action_layout)

        self.setCentralWidget(central)
        self.setStyleSheet(
            """
            QMainWindow { background: #f5f5f7; }
            QLabel#title { font-size: 25px; font-weight: 600; color: #1d1d1f; }
            QLabel#subtitle { color: #6e6e73; }
            QFrame#dropArea {
                background: #ffffff; border: 2px dashed #b8b8bd;
                border-radius: 10px;
            }
            QLabel#preview {
                background: #ffffff; border: 1px solid #c8c8cc;
                border-radius: 4px;
            }
            QPushButton { min-height: 30px; padding: 2px 10px; }
            """
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()

    def add_paths(self, paths: list[str] | tuple[str, ...]) -> None:
        existing = {job.input_path.resolve() for job in self._jobs}
        added = 0
        for raw_path in paths:
            path = Path(raw_path).expanduser()
            if path.is_dir():
                candidates = sorted(
                    candidate
                    for candidate in path.rglob("*")
                    if candidate.is_file() and candidate.suffix.lower() in {".djvu", ".djv"}
                )
            else:
                candidates = [path]
            for candidate in candidates:
                if (
                    not candidate.is_file()
                    or candidate.suffix.lower() not in {".djvu", ".djv"}
                ):
                    continue
                resolved = candidate.resolve()
                if resolved in existing:
                    continue
                existing.add(resolved)
                self._jobs.append(QueueJob(resolved))
                self._append_job_row(len(self._jobs) - 1)
                added += 1
        if added:
            self.status_label.setText(f"Добавлено в очередь: {added}")
            self.convert_button.setEnabled(
                self._worker is None
                and any(job.status == "В очереди" for job in self._jobs)
            )
            self.queue_list.setCurrentRow(self.queue_list.count() - added)
        elif not self._jobs:
            self.status_label.setText("Подходящие файлы .djvu/.djv не найдены")

    def _append_job_row(self, job_index: int) -> None:
        item = QListWidgetItem()
        item.setData(_FILE_ROLE, job_index)
        self.queue_list.addItem(item)
        self._refresh_job_row(job_index)

    def _refresh_job_row(self, job_index: int) -> None:
        job = self._jobs[job_index]
        try:
            size = self._format_size(job.input_path.stat().st_size)
        except OSError:
            size = "недоступен"
        description = f"{job.input_path.name}  ·  {size}  ·  {job.status}"
        if job.output_path is not None and job.output_path.is_file():
            description += f"  ·  PDF: {self._format_size(job.output_path.stat().st_size)}"
        elif job.error:
            description += f"  ·  {job.error}"
        item = self.queue_list.item(job_index)
        if item is not None:
            item.setText(description)
            item.setToolTip(str(job.input_path))

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GB"

    def _choose_files(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(
            self, "Выберите файлы DjVu", "", "DjVu files (*.djvu *.djv)"
        )
        if filenames:
            self.add_paths(filenames)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку с DjVu-файлами")
        if folder:
            self.add_paths([folder])

    def _output_mode_changed(self, enabled: bool) -> None:
        self.output_folder_button.setEnabled(enabled)

    def _choose_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Папка для PDF")
        if folder:
            self._output_folder = Path(folder)
            self.output_folder_label.setText(str(self._output_folder))

    def _selection_changed(self, row: int) -> None:
        item = self.queue_list.item(row)
        if item is None:
            self._show_empty_preview()
            return
        job_index = item.data(_FILE_ROLE)
        if not isinstance(job_index, int) or job_index >= len(self._jobs):
            self._show_empty_preview()
            return
        self._start_preview(self._jobs[job_index].input_path)

    def _show_empty_preview(self, message: str = "Предпросмотр недоступен") -> None:
        process = self._preview_process
        self._preview_process = None
        if (
            process is not None
            and process.state() != QProcess.ProcessState.NotRunning
        ):
            process.kill()
        self._preview_path = None
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText(message)

    def _start_preview(self, path: Path) -> None:
        if self._preview_process is not None:
            self._preview_process.kill()
        self._preview_path = path
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("Загрузка превью…")

        process = QProcess(self)
        self._preview_process = process
        process.setWorkingDirectory(str(path.parent))
        process.finished.connect(
            lambda code, status, current=process: self._preview_finished(
                current, code, status
            )
        )
        process.errorOccurred.connect(
            lambda error, current=process: self._preview_failed(current, error)
        )
        process.start(
            str(self._tools.ddjvu),
            [
                "-format=ppm",
                "-page=1",
                "-size=150x200",
                str(path),
                "-",
            ],
        )

    def _preview_finished(
        self,
        process: QProcess,
        exit_code: int,
        _exit_status: QProcess.ExitStatus,
    ) -> None:
        is_current = process is self._preview_process
        path = self._preview_path
        data = bytes(process.readAllStandardOutput())
        process.deleteLater()
        if not is_current:
            return
        self._preview_process = None
        pixmap = QPixmap()
        if exit_code == 0 and pixmap.loadFromData(data, "PPM"):
            self.preview_label.setPixmap(
                pixmap.scaled(
                    self.preview_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.preview_label.setText("")
        else:
            self._show_empty_preview("Не удалось получить превью")
        if path is not None:
            self.preview_label.setToolTip(path.name)

    def _preview_failed(
        self,
        process: QProcess,
        _error: QProcess.ProcessError,
    ) -> None:
        if process is self._preview_process:
            self._preview_process = None
            self._preview_path = None
            process.deleteLater()
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Не удалось запустить ddjvu")

    def start_queue(self) -> None:
        if self._worker is not None:
            return
        if self.output_folder_radio.isChecked() and self._output_folder is None:
            QMessageBox.warning(self, "Не выбрана папка", "Выберите папку для PDF.")
            return
        if not any(job.status == "В очереди" for job in self._jobs):
            return
        self._cancel_queue = False
        self._set_busy(True)
        self.progress_bar.setValue(0)
        self._start_next_job()

    def _start_next_job(self) -> None:
        if self._cancel_queue:
            self._finish_queue("Очередь отменена")
            return
        next_index = next(
            (index for index, job in enumerate(self._jobs) if job.status == "В очереди"),
            None,
        )
        if next_index is None:
            self._finish_queue("Очередь обработана")
            return

        job = self._jobs[next_index]
        output = self._output_path(job.input_path)
        if any(
            other.output_path == output and index != next_index
            for index, other in enumerate(self._jobs)
        ):
            job.status = "Ошибка"
            job.error = "совпадает путь выходного файла"
            self._refresh_job_row(next_index)
            QTimer.singleShot(0, self._start_next_job)
            return
        if output.exists():
            answer = QMessageBox.question(
                self,
                "Файл уже существует",
                f"Перезаписать файл?\n{output}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                job.status = "Ошибка"
                job.error = "пропущен: PDF уже существует"
                self._refresh_job_row(next_index)
                QTimer.singleShot(0, self._start_next_job)
                return

        self._active_job_index = next_index
        job.status = "Обработка (0%)"
        job.output_path = output
        self._refresh_job_row(next_index)
        self.queue_list.setCurrentRow(next_index)
        self._worker = ConversionWorker(
            job.input_path,
            output,
            self.text_checkbox.isChecked(),
            self.dictionary_checkbox.isChecked(),
            self._tools,
            ConversionProfile(self.profile_combo.currentData()),
        )
        self._worker.progress_changed.connect(self._job_progress_changed)
        self._worker.finished.connect(self._worker_finished)
        self.status_label.setText(f"Обработка {job.input_path.name}")
        self._worker.start()

    def _output_path(self, input_path: Path) -> Path:
        if self.output_folder_radio.isChecked() and self._output_folder is not None:
            return self._output_folder / input_path.with_suffix(".pdf").name
        return input_path.with_suffix(".pdf")

    def _job_progress_changed(self, message: str, percentage: int) -> None:
        if self._active_job_index is None or not self._jobs:
            return
        job_index = self._active_job_index
        job = self._jobs[job_index]
        percentage = max(0, min(100, percentage))
        job.status = f"Обработка ({percentage}%)"
        self._refresh_job_row(job_index)
        self.status_label.setText(message)
        total_progress = int((job_index + percentage / 100) * 100 / len(self._jobs))
        self.progress_bar.setValue(total_progress)

    def _worker_finished(self) -> None:
        worker = self._worker
        job_index = self._active_job_index
        self._worker = None
        self._active_job_index = None
        if worker is None or job_index is None:
            QTimer.singleShot(0, self._start_next_job)
            return

        job = self._jobs[job_index]
        if worker.cancelled:
            job.status = "В очереди" if not self._cancel_queue else "Отменено"
            if self._cancel_queue:
                for pending in self._jobs:
                    if pending.status == "В очереди":
                        pending.status = "Отменено"
                        self._refresh_job_row(self._jobs.index(pending))
        elif worker.error is not None:
            job.status = "Ошибка"
            job.error = worker.error
        else:
            job.status = "Готово"
            job.output_path = worker.result_path
            self._last_output_path = worker.result_path
        self._refresh_job_row(job_index)
        self._update_savings()
        QTimer.singleShot(0, self._start_next_job)

    def cancel_queue(self) -> None:
        if self._worker is None:
            return
        self._cancel_queue = True
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Остановка текущего файла…")
        self._worker.cancel()

    def _finish_queue(self, message: str) -> None:
        self._set_busy(False)
        self.status_label.setText(message)
        self.progress_bar.setValue(
            100 if all(job.status in {"Готово", "Ошибка", "Отменено"} for job in self._jobs) else 0
        )
        self._update_savings()
        if self._last_output_path is not None and self._last_output_path.is_file():
            self.open_pdf_button.setEnabled(True)
            self.open_finder_button.setEnabled(True)
        completed_count = sum(job.status == "Готово" for job in self._jobs)
        if (
            completed_count
            and not self._cancel_queue
            and (self.isMinimized() or not self.isActiveWindow())
        ):
            self._send_completion_notification(completed_count)

    def _send_completion_notification(self, completed_count: int) -> None:
        if sys.platform != "darwin":
            return
        message = self._escape_applescript_string(
            f"Готово файлов: {completed_count}."
        )
        title = self._escape_applescript_string("DjVu → PDF")
        script = f'display notification "{message}" with title "{title}"'
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            _LOGGER.warning("Не удалось отправить уведомление macOS: %s", error)
            return
        if result.returncode:
            _LOGGER.warning(
                "osascript завершился с кодом %s: %s",
                result.returncode,
                result.stderr.strip(),
            )

    @staticmethod
    def _escape_applescript_string(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\r", " ")
            .replace("\n", " ")
        )

    def _update_savings(self) -> None:
        savings = sum(
            job.input_path.stat().st_size - job.output_path.stat().st_size
            for job in self._jobs
            if job.status == "Готово"
            and job.output_path is not None
            and job.output_path.is_file()
        )
        self.savings_label.setText(
            f"Суммарная экономия: {self._format_size(savings)}"
            if savings >= 0
            else f"Суммарный прирост: {self._format_size(-savings)}"
        )

    def clear_completed(self) -> None:
        self._jobs = [
            job for job in self._jobs if job.status not in {"Готово", "Ошибка", "Отменено"}
        ]
        self.queue_list.clear()
        for index in range(len(self._jobs)):
            self._append_job_row(index)
        self._update_savings()
        self.convert_button.setEnabled(
            self._worker is None and any(job.status == "В очереди" for job in self._jobs)
        )
        if not self._jobs:
            self._show_empty_preview("Выберите документ")

    def open_last_pdf(self) -> None:
        self._open_path(self._last_output_path)

    def open_last_in_finder(self) -> None:
        if self._last_output_path is not None:
            self._launch_open(["open", "-R", str(self._last_output_path)])

    def _open_path(self, path: Path | None) -> None:
        if path is not None and path.is_file():
            self._launch_open(["open", str(path)])

    def _launch_open(self, command: list[str]) -> None:
        try:
            subprocess.Popen(command)
        except OSError as error:
            QMessageBox.warning(self, "Не удалось открыть файл", str(error))

    def _set_busy(self, busy: bool) -> None:
        self.convert_button.setEnabled(
            not busy and any(job.status == "В очереди" for job in self._jobs)
        )
        self.cancel_button.setEnabled(busy)
        self.add_files_button.setEnabled(not busy)
        self.add_folder_button.setEnabled(not busy)
        self.clear_button.setEnabled(not busy)
        self.near_source_radio.setEnabled(not busy)
        self.output_folder_radio.setEnabled(not busy)
        self.output_folder_button.setEnabled(not busy and self.output_folder_radio.isChecked())
        self.text_checkbox.setEnabled(not busy)
        self.dictionary_checkbox.setEnabled(not busy)
        self.profile_combo.setEnabled(not busy)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self,
                "Конвертация выполняется",
                "Дождитесь завершения текущего файла перед закрытием приложения.",
            )
            event.ignore()
            return
        process = self._preview_process
        self._preview_process = None
        if process is not None:
            process.blockSignals(True)
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                if not process.waitForFinished(3000):
                    _LOGGER.error("Не удалось остановить процесс предпросмотра ddjvu.")
                    process.waitForFinished()
            process.deleteLater()
        event.accept()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.FileOpen:
            file_event = event
            if isinstance(file_event, QFileOpenEvent):
                self.add_paths([file_event.file()])
                return True
        return super().event(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.FileOpen and isinstance(event, QFileOpenEvent):
            file_path = event.file()
            if file_path:
                self.showNormal()
                self.raise_()
                self.activateWindow()
                self.add_paths([file_path])
                return True
        return super().eventFilter(watched, event)
