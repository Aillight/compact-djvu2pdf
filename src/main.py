"""Application entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from core.converter import ConversionProfile, convert
from core.system_check import MissingToolsError, find_tools
from gui.main_window import MainWindow


def _convert_from_command_line(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="djvu2pdf --convert",
        description="Convert a DjVu document using the app's bundled tools.",
    )
    parser.add_argument("input", type=Path, help="Input DjVu file")
    parser.add_argument("output", type=Path, help="Output PDF file")
    parser.add_argument(
        "--profile",
        choices=[profile.value for profile in ConversionProfile],
        default=ConversionProfile.AUTO.value,
        help="Conversion profile (default: auto)",
    )
    options = parser.parse_args(arguments)
    try:
        result = convert(
            options.input,
            options.output,
            tools=find_tools(),
            profile=ConversionProfile(options.profile),
            progress=lambda message, percent: print(
                f"{percent:3d}% {message}",
                flush=True,
            ),
        )
    except (MissingToolsError, OSError, RuntimeError, ValueError) as error:
        print(f"Конвертация не выполнена: {error}", file=sys.stderr)
        return 1
    print(f"PDF создан: {result}")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--convert":
        return _convert_from_command_line(sys.argv[2:])

    app = QApplication(sys.argv)
    app.setApplicationName("DjVu в PDF")
    try:
        tools = find_tools()
    except MissingToolsError as error:
        if "--check-tools" in sys.argv:
            print(str(error), file=sys.stderr)
            return 1
        QMessageBox.critical(
            None,
            "Не найдены необходимые утилиты",
            str(error),
        )
        return 1

    if "--check-tools" in sys.argv:
        print(f"ddjvu={tools.ddjvu}")
        print(f"djvused={tools.djvused}")
        print(f"djvudump={tools.djvudump}")
        print(f"jbig2={tools.jbig2}")
        return 0

    window = MainWindow(tools)
    app.installEventFilter(window)
    window.show()
    startup_files = [
        argument
        for argument in sys.argv[1:]
        if argument.lower().endswith((".djvu", ".djv"))
    ]
    if startup_files:
        window.add_paths(startup_files)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
