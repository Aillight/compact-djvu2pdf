#!/usr/bin/env python3
"""Package the built macOS application into a compressed, verified DMG."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class DmgBuildError(RuntimeError):
    """Raised when the application disk image cannot be built."""


def create_dmg(project_root: Path | None = None) -> Path:
    root = (project_root or Path(__file__).resolve().parents[1]).resolve()
    app_bundle = root / "dist" / "djvu2pdf.app"
    destination = root / "dist" / "djvu2pdf-macOS.dmg"
    hdiutil = shutil.which("hdiutil")
    if sys.platform != "darwin" or hdiutil is None:
        raise DmgBuildError("Создание DMG требует macOS и системной утилиты hdiutil.")
    if not app_bundle.is_dir():
        raise DmgBuildError(f"Сначала соберите приложение: {app_bundle} не найден.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_image: Path | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="djvu2pdf-dmg-") as temporary:
            staging = Path(temporary)
            shutil.copytree(app_bundle, staging / app_bundle.name, symlinks=True)
            (staging / "Applications").symlink_to("/Applications")
            with tempfile.NamedTemporaryFile(
                prefix=".djvu2pdf-",
                suffix=".dmg",
                dir=destination.parent,
                delete=False,
            ) as temporary_file:
                temporary_image = Path(temporary_file.name)

            _run(
                [
                    hdiutil,
                    "create",
                    "-volname",
                    "DjVu to PDF",
                    "-srcfolder",
                    str(staging),
                    "-format",
                    "UDZO",
                    "-ov",
                    str(temporary_image),
                ]
            )
        _run([hdiutil, "verify", str(temporary_image)])
        os.replace(temporary_image, destination)
        temporary_image = None
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None)
        raise DmgBuildError(
            f"Не удалось создать образ DMG"
            + (f": {detail.strip()}" if detail else f": {error}")
        ) from error
    finally:
        if temporary_image is not None:
            temporary_image.unlink(missing_ok=True)

    print(f"DMG создан: {destination} ({destination.stat().st_size:,} байт)")
    return destination


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    try:
        create_dmg()
    except DmgBuildError as error:
        print(f"Сборка DMG не выполнена: {error}", file=sys.stderr)
        raise SystemExit(1) from error
