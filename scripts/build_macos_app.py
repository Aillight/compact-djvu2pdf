#!/usr/bin/env python3
"""Build a macOS app bundle with Homebrew DjVu/JBIG2 tools and dylibs."""

from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOL_NAMES = ("ddjvu", "djvused", "djvudump", "jbig2")
BREW_BIN_DIRS = (Path("/opt/homebrew/bin"), Path("/usr/local/bin"))
SYSTEM_LIBRARY_PREFIXES = ("/usr/lib/", "/System/Library/")


class BuildError(RuntimeError):
    """Raised for a missing tool or an unbundleable native dependency."""


def find_tool(name: str) -> Path | None:
    located = shutil.which(name)
    if located:
        return Path(located).resolve()
    for directory in BREW_BIN_DIRS:
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def _run(command: list[str]) -> str:
    try:
        result = subprocess.run(command, check=True, text=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None)
        raise BuildError(
            f"Не удалось выполнить {' '.join(command)}"
            + (f": {detail.strip()}" if detail else "")
        ) from error
    return result.stdout


def _otool_dependencies(binary: Path) -> tuple[str, ...]:
    output = _run(["otool", "-L", str(binary)])
    dependencies: list[str] = []
    for line in output.splitlines()[1:]:
        entry = line.strip().split(" (", 1)[0]
        if entry:
            dependencies.append(entry)
    return tuple(dependencies)


def _otool_rpaths(binary: Path) -> tuple[str, ...]:
    output = _run(["otool", "-l", str(binary)])
    lines = output.splitlines()
    rpaths: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() == "cmd LC_RPATH":
            for detail in lines[index + 1:index + 5]:
                match = re.match(r"\s*path (.+?) \(offset \d+\)", detail)
                if match:
                    rpaths.append(match.group(1))
                    break
    return tuple(rpaths)


def _expand_loader_path(value: str, loader: Path, executable_dir: Path) -> str:
    value = value.replace("@loader_path", str(loader.parent))
    return value.replace("@executable_path", str(executable_dir))


def _resolve_dependency(
    reference: str,
    loader: Path,
    executable_dir: Path,
    rpaths: tuple[str, ...],
) -> Path | None:
    candidates: list[Path] = []
    if reference.startswith("@rpath/"):
        relative = reference.removeprefix("@rpath/")
        candidates.extend(
            Path(_expand_loader_path(rpath, loader, executable_dir)) / relative
            for rpath in rpaths
        )
    elif reference.startswith("@loader_path/") or reference.startswith("@executable_path/"):
        candidates.append(
            Path(_expand_loader_path(reference, loader, executable_dir))
        )
    elif reference.startswith("/"):
        candidates.append(Path(reference))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _destination_name(library: Path, destination: Path) -> str:
    name = library.name
    candidate = destination / name
    if not candidate.exists() or candidate.resolve() == library:
        return name
    digest = hashlib.sha256(str(library).encode()).hexdigest()[:10]
    return f"{library.stem}-{digest}{library.suffix}"


def bundle_native_libraries(
    tools: dict[str, Path],
    resource_dir: Path,
) -> None:
    binary_dir = resource_dir / "bin"
    library_dir = resource_dir / "lib"
    binary_dir.mkdir(parents=True, exist_ok=True)
    library_dir.mkdir(parents=True, exist_ok=True)

    copied_libraries: dict[Path, Path] = {}
    queue: list[tuple[Path, Path]] = []
    for name, source in tools.items():
        destination = binary_dir / name
        shutil.copy2(source, destination)
        queue.append((source, destination))

    rewritten: list[tuple[Path, str, str]] = []
    visited: set[Path] = set()
    while queue:
        source, bundled = queue.pop(0)
        if source in visited:
            continue
        visited.add(source)
        rpaths = _otool_rpaths(source)
        for reference in _otool_dependencies(source):
            if reference.startswith(SYSTEM_LIBRARY_PREFIXES):
                continue
            dependency = _resolve_dependency(
                reference,
                source,
                Path(next(iter(tools.values()))).parent,
                rpaths,
            )
            if dependency is None:
                raise BuildError(
                    f"Не удалось найти динамическую библиотеку {reference}, "
                    f"нужную для {source}."
                )
            bundled_dependency = copied_libraries.get(dependency)
            if bundled_dependency is None:
                name = _destination_name(dependency, library_dir)
                bundled_dependency = library_dir / name
                shutil.copy2(dependency, bundled_dependency)
                copied_libraries[dependency] = bundled_dependency
                queue.append((dependency, bundled_dependency))
            relative_reference = (
                f"@loader_path/../lib/{bundled_dependency.name}"
                if bundled.parent == binary_dir
                else f"@loader_path/{bundled_dependency.name}"
            )
            rewritten.append((bundled, reference, relative_reference))

    install_name_tool = shutil.which("install_name_tool")
    if install_name_tool is None:
        raise BuildError("Не найдена команда install_name_tool из Xcode Command Line Tools.")
    for library in copied_libraries.values():
        _run([install_name_tool, "-id", f"@loader_path/{library.name}", str(library)])
    for binary, old_reference, new_reference in rewritten:
        _run(
            [
                install_name_tool,
                "-change",
                old_reference,
                new_reference,
                str(binary),
            ]
        )


def main() -> int:
    if sys.platform != "darwin":
        raise BuildError("Скрипт сборки .app предназначен только для macOS.")

    tools: dict[str, Path] = {}
    missing: list[str] = []
    for name in TOOL_NAMES:
        path = find_tool(name)
        if path is None:
            missing.append(name)
        else:
            tools[name] = path
    if missing:
        raise BuildError(
            "Не найдены Homebrew-утилиты: "
            + ", ".join(missing)
            + ". Установите их командой: brew install djvulibre jbig2enc"
        )

    project_root = Path(__file__).resolve().parents[1]
    spec_path = project_root / "djvu2pdf.spec"
    launcher_candidates = (
        project_root / ".venv" / "bin" / "pyinstaller",
        Path(sys.executable).parent / "pyinstaller",
        Path(sys.prefix) / "bin" / "pyinstaller",
    )
    pyinstaller_launcher = next(
        (candidate for candidate in launcher_candidates if candidate.is_file()),
        None,
    )
    if pyinstaller_launcher is not None:
        pyinstaller_command = [str(pyinstaller_launcher)]
    else:
        pyinstaller_command = [sys.executable, "-m", "PyInstaller"]
    pyinstaller_command.extend(
        (
            "--noconfirm",
            "--clean",
            "--distpath",
            str(project_root / "dist"),
            "--workpath",
            str(project_root / "build" / "pyinstaller"),
            str(spec_path),
        )
    )
    _run(pyinstaller_command)

    app_bundle = project_root / "dist" / "djvu2pdf.app"
    if not app_bundle.is_dir():
        raise BuildError(f"PyInstaller не создал ожидаемый бандл: {app_bundle}")
    resources = app_bundle / "Contents" / "Resources"
    bundle_native_libraries(tools, resources)
    for name, source in tools.items():
        print(f"Bundled {name}: {source} -> {resources / 'bin' / name}")

    info_path = app_bundle / "Contents" / "Info.plist"
    with info_path.open("rb") as plist_file:
        info = plistlib.load(plist_file)
    info["NSHighResolutionCapable"] = True
    info["CFBundleDocumentTypes"] = [
        {
            "CFBundleTypeName": "DjVu Document",
            "CFBundleTypeRole": "Viewer",
            "LSItemContentTypes": ["com.djvuconverter.djvu"],
            "CFBundleTypeExtensions": ["djvu", "djv"],
        }
    ]
    info["UTExportedTypeDeclarations"] = [
        {
            "UTTypeIdentifier": "com.djvuconverter.djvu",
            "UTTypeDescription": "DjVu document",
            "UTTypeConformsTo": ["public.data"],
            "UTTypeTagSpecification": {
                "public.filename-extension": ["djvu", "djv"],
                "public.mime-type": ["image/vnd.djvu", "image/x-djvu"],
            },
        }
    ]
    with info_path.open("wb") as plist_file:
        plistlib.dump(info, plist_file)

    codesign = shutil.which("codesign")
    if codesign:
        code_files = [
            *sorted((resources / "bin").iterdir()),
            *sorted((resources / "lib").glob("*.dylib")),
        ]
        for code_file in code_files:
            _run([codesign, "--force", "--sign", "-", str(code_file)])
        _run([codesign, "--force", "--deep", "--sign", "-", str(app_bundle)])
    print(f"App bundle ready: {app_bundle}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as error:
        print(f"Build failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
