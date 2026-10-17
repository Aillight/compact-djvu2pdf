"""Background conversion worker."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.converter import ConversionProfile, convert
from core.process import CancellationToken, ConversionCancelled
from core.system_check import ToolPaths


class ConversionWorker(QThread):
    progress_changed = Signal(str, int)

    def __init__(
        self,
        input_path: Path,
        output_path: Path,
        preserve_text: bool,
        use_symbol_dictionary: bool,
        tools: ToolPaths,
        profile: ConversionProfile = ConversionProfile.AUTO,
    ) -> None:
        super().__init__()
        self._input_path = input_path
        self._output_path = output_path
        self._preserve_text = preserve_text
        self._use_symbol_dictionary = use_symbol_dictionary
        self._tools = tools
        self._profile = profile
        self._cancellation = CancellationToken()
        self.result_path: Path | None = None
        self.error: str | None = None
        self.cancelled = False

    def cancel(self) -> None:
        self._cancellation.cancel()

    def run(self) -> None:
        try:
            result = convert(
                self._input_path,
                self._output_path,
                preserve_text=self._preserve_text,
                use_symbol_dictionary=self._use_symbol_dictionary,
                progress=self.progress_changed.emit,
                tools=self._tools,
                cancellation=self._cancellation,
                profile=self._profile,
            )
        except ConversionCancelled:
            self.cancelled = True
        except Exception as error:
            self.error = str(error)
        else:
            self.result_path = result
