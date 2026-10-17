"""Discovery and validation of the external DjVu/JBIG2 programs."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


BREW_INSTALL_COMMAND = "brew install djvulibre jbig2enc"
_SEARCH_DIRS = (Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/opt/local/bin"))
_TOOL_NAMES = ("ddjvu", "djvused", "jbig2", "djvudump")


@dataclass(frozen=True)
class ToolPaths:
    ddjvu: Path
    djvused: Path
    jbig2: Path
    djvudump: Path | None = None


class MissingToolsError(RuntimeError):
    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        names = ", ".join(missing)
        super().__init__(
            f"Не найдены утилиты: {names}.\n"
            f"Установите их в Терминале: {BREW_INSTALL_COMMAND}"
        )


def _bundled_tool_directories() -> tuple[Path, ...]:
    if not getattr(sys, "frozen", False):
        return ()

    candidates: list[Path] = []
    extraction_dir = getattr(sys, "_MEIPASS", None)
    if extraction_dir:
        base = Path(extraction_dir)
        candidates.extend((base / "Resources" / "bin", base / "bin"))

    executable = Path(sys.executable).resolve()
    candidates.extend(
        (
            executable.parent.parent / "Resources" / "bin",
            executable.parent / "Resources" / "bin",
        )
    )
    return tuple(dict.fromkeys(candidates))


def _find_executable(name: str) -> Path | None:
    for directory in _bundled_tool_directories():
        candidate = directory / name
        if candidate.is_file() and candidate.stat().st_mode & 0o111:
            return candidate.resolve()

    located = shutil.which(name)
    if located:
        return Path(located).resolve()

    for directory in _SEARCH_DIRS:
        candidate = directory / name
        if candidate.is_file() and candidate.stat().st_mode & 0o111:
            return candidate
    return None


def find_tools() -> ToolPaths:
    """Find all required executables on PATH or in common macOS Homebrew paths."""
    found = {
        name: _find_executable(name)
        for name in _TOOL_NAMES
    }
    missing = tuple(name for name, path in found.items() if path is None)
    if missing:
        raise MissingToolsError(missing)

    ddjvu = found["ddjvu"]
    djvused = found["djvused"]
    jbig2 = found["jbig2"]
    djvudump = found["djvudump"]
    assert (
        ddjvu is not None
        and djvused is not None
        and jbig2 is not None
        and djvudump is not None
    )
    return ToolPaths(
        ddjvu=ddjvu,
        djvused=djvused,
        jbig2=jbig2,
        djvudump=djvudump,
    )
