"""Orchestrate DjVu rendering, chunked JBIG2 encoding, and PDF assembly."""

from __future__ import annotations

import re
import struct
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from PIL import Image
from PIL import ImageChops, ImageFilter, ImageMath

from .assembler import PageImage, assemble_pdf
from .inspector import PageInspection, PageType, inspect_document
from .parser import OutlineEntry, PageText, parse_print_outline, parse_print_txt
from .process import (
    CancellationToken,
    ConversionCancelled,
    ConversionProcessError,
)
from .system_check import ToolPaths, find_tools

ProgressCallback = Callable[[str, int], None]
_DPI = 300
_IMAGE_DPI = 150
_JPEG_QUALITY = 75
_GRAYSCALE_JPEG_QUALITY = 35
_ADAPTIVE_WINDOW_RADIUS = 15
_ADAPTIVE_THRESHOLD_OFFSET = 8
_PAPER_FLATNESS_TOLERANCE = 2
_DEFAULT_CHUNK_SIZE = 50


class ConversionProfile(str, Enum):
    AUTO = "auto"
    CLEAN_BLACK_AND_WHITE = "clean-black-and-white"


class ConversionError(RuntimeError):
    """Raised when one of the conversion steps fails."""


@dataclass(frozen=True)
class _RenderedPage:
    page_type: PageType
    path: Path
    width: int
    height: int
    dpi: int


def _tiff_dimensions(path: Path) -> tuple[int, int]:
    """Read TIFF width and height from its first IFD without extra packages."""
    data = path.read_bytes()
    if len(data) < 8:
        raise ConversionError(f"Файл TIFF пуст или повреждён: {path.name}")
    if data[:2] == b"II":
        endian = "<"
    elif data[:2] == b"MM":
        endian = ">"
    else:
        raise ConversionError(f"Неизвестный порядок байтов TIFF: {path.name}")
    if struct.unpack_from(endian + "H", data, 2)[0] != 42:
        raise ConversionError(f"Некорректный TIFF-заголовок: {path.name}")

    offset = struct.unpack_from(endian + "I", data, 4)[0]
    if offset + 2 > len(data):
        raise ConversionError(f"Некорректная таблица TIFF: {path.name}")
    entry_count = struct.unpack_from(endian + "H", data, offset)[0]
    width = height = None
    for entry_index in range(entry_count):
        entry_offset = offset + 2 + entry_index * 12
        if entry_offset + 12 > len(data):
            raise ConversionError(f"Обрезанная таблица TIFF: {path.name}")
        tag, value_type, count = struct.unpack_from(endian + "HHI", data, entry_offset)
        if tag not in (256, 257):
            continue
        if count != 1 or value_type not in (3, 4):
            raise ConversionError(f"Неподдерживаемое поле размеров TIFF: {path.name}")
        value = struct.unpack_from(
            endian + ("H" if value_type == 3 else "I"),
            data,
            entry_offset + 8,
        )[0]
        if tag == 256:
            width = value
        else:
            height = value
    if not width or not height:
        raise ConversionError(f"В TIFF отсутствуют размеры изображения: {path.name}")
    return width, height


def _render_size(page: PageInspection) -> tuple[int, int, int]:
    source_dpi = page.dpi if page.dpi > 0 else _DPI
    target_dpi = min(source_dpi, _IMAGE_DPI)
    if page.width <= 0 or page.height <= 0:
        return 0, 0, target_dpi
    return (
        max(1, round(page.width * target_dpi / source_dpi)),
        max(1, round(page.height * target_dpi / source_dpi)),
        target_dpi,
    )


def _binarize_grayscale(source: Path, destination: Path) -> None:
    with Image.open(source) as image:
        grayscale = image.convert("L")
        local_mean = grayscale.filter(
            ImageFilter.BoxBlur(_ADAPTIVE_WINDOW_RADIUS)
        )
        threshold = local_mean.point(
            lambda value: max(0, value - _ADAPTIVE_THRESHOLD_OFFSET)
        )
        binary = ImageMath.lambda_eval(
            lambda images: (images["grayscale"] >= images["threshold"]) * 255,
            grayscale=grayscale,
            threshold=threshold,
        ).convert("1")
        binary.save(destination, format="TIFF", compression="group4")


def _normalize_grayscale_background(image: Image.Image) -> Image.Image:
    """Whiten near-white flat paper while preserving bright image details."""
    grayscale = image.convert("L")
    local_range = ImageChops.subtract(
        grayscale.filter(ImageFilter.MaxFilter(3)),
        grayscale.filter(ImageFilter.MinFilter(3)),
    )
    near_white = grayscale.point(lambda value: 255 if value > 215 else 0)
    flat_background = local_range.point(
        lambda value: 255 if value <= _PAPER_FLATNESS_TOLERANCE else 0
    )
    paper_mask = ImageChops.multiply(near_white, flat_background)
    return ImageChops.lighter(grayscale, paper_mask)


def _has_foreground_mask(page: PageInspection) -> bool:
    return any(chunk.name == "Sjbz" and chunk.size > 0 for chunk in page.chunks)


def _jbig2_page_outputs(prefix: Path, expected_count: int) -> tuple[Path, ...]:
    numbered: list[tuple[int, Path]] = []
    for path in prefix.parent.glob(prefix.name + ".*"):
        match = re.fullmatch(re.escape(prefix.name) + r"\.(\d+)", path.name)
        if path.is_file() and match:
            numbered.append((int(match.group(1)), path))
    numbered.sort(key=lambda item: item[0])
    if len(numbered) != expected_count:
        raise ConversionError(
            f"jbig2 создал {len(numbered)} постраничных потоков вместо {expected_count}."
        )
    return tuple(path for _, path in numbered)


def _unresolved_component_targets(
    entries: tuple[OutlineEntry, ...],
) -> bool:
    return any(
        (entry.target is not None and not entry.target.startswith("#") and entry.page_index is None)
        or _unresolved_component_targets(entry.children)
        for entry in entries
    )


def _map_outline_pages(
    entries: tuple[OutlineEntry, ...],
    component_pages: dict[str, int],
    page_count: int,
) -> tuple[OutlineEntry, ...]:
    mapped: list[OutlineEntry] = []
    for entry in entries:
        page_index = entry.page_index
        if page_index is None and entry.target is not None:
            target = entry.target
            page_index = component_pages.get(target)
            if page_index is None:
                page_index = component_pages.get(target.rsplit("/", 1)[-1])
            if page_index is None:
                page_index = component_pages.get(Path(target).stem)
            if page_index is None:
                page_index = component_pages.get(
                    Path(target.rsplit("/", 1)[-1]).stem.casefold()
                )
            if page_index is not None and not 0 <= page_index < page_count:
                page_index = None
        mapped.append(
            OutlineEntry(
                entry.title,
                page_index,
                _map_outline_pages(entry.children, component_pages, page_count),
                entry.target,
            )
        )
    return tuple(mapped)


def convert(
    input_path: Path,
    output_path: Path,
    *,
    preserve_text: bool = True,
    use_symbol_dictionary: bool = True,
    progress: ProgressCallback | None = None,
    tools: ToolPaths | None = None,
    cancellation: CancellationToken | None = None,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    profile: ConversionProfile | str = ConversionProfile.AUTO,
) -> Path:
    """Convert a DjVu to a searchable PDF, reporting total progress 0–100%."""
    try:
        profile = ConversionProfile(profile)
    except ValueError as error:
        raise ValueError(f"Неизвестный профиль конвертации: {profile}") from error

    source = input_path.expanduser().resolve()
    destination = output_path.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in {".djvu", ".djv"}:
        raise ConversionError("Выберите существующий файл .djvu или .djv.")
    if source == destination:
        raise ConversionError("Путь PDF не может совпадать с исходным файлом.")
    if chunk_size <= 0:
        raise ValueError("Размер чанка должен быть положительным.")

    executables = tools or find_tools()
    if executables.djvudump is None:
        raise ConversionError(
            "Не найдена утилита djvudump. Установите её командой: "
            "brew install djvulibre"
        )
    token = cancellation or CancellationToken()

    def notify(message: str, percentage: int) -> None:
        if progress is not None:
            progress(message, percentage)

    try:
        token.check()
        notify("Извлечение текста...", 0)
        text_output = token.run(
            [
                str(executables.djvused),
                str(source),
                "-e",
                "print-txt",
            ],
            "извлечение текста",
            cwd=str(source.parent),
        )
        parsed_pages = parse_print_txt(text_output)
        if not parsed_pages:
            raise ConversionError("В документе DjVu не найдены страницы.")
        page_count = len(parsed_pages)

        notify("Извлечение оглавления...", 2)
        outline_output = token.run(
            [
                str(executables.djvused),
                str(source),
                "-e",
                "print-outline",
            ],
            "извлечение оглавления",
            cwd=str(source.parent),
        )
        outlines = parse_print_outline(outline_output, page_count)

        notify("Анализ структуры и цветных страниц...", 4)
        inspections = inspect_document(
            source,
            page_count,
            executables,
            token,
            detect_color=profile is ConversionProfile.AUTO,
        )
        if _unresolved_component_targets(outlines):
            component_pages = {
                component_id: page_index
                for page_index, inspection in enumerate(inspections)
                if (component_id := inspection.component_id)
            }
            for page_index, inspection in enumerate(inspections):
                if inspection.component_id:
                    component_pages[inspection.component_id.casefold()] = page_index
                    component = Path(inspection.component_id)
                    for key in (component.name, component.stem):
                        component_pages[key] = page_index
                        component_pages[key.casefold()] = page_index
            outlines = _map_outline_pages(outlines, component_pages, page_count)
        if profile is ConversionProfile.AUTO:
            page_types = [inspection.page_type for inspection in inspections]
        else:
            page_types = [PageType.MONOCHROME] * page_count
        notify("Метаданные и оглавление извлечены.", 10)

        pages = parsed_pages if preserve_text else tuple(
            PageText(
                page.number,
                page.width,
                page.height,
                (),
                page.origin_x,
                page.origin_y,
            )
            for page in parsed_pages
        )

        with tempfile.TemporaryDirectory(prefix="djvu2pdf-") as temporary:
            work_dir = Path(temporary)
            rendered: list[_RenderedPage] = []
            for page_index, (page_type, inspection) in enumerate(
                zip(page_types, inspections),
                start=1,
            ):
                token.check()
                is_monochrome = page_type is PageType.MONOCHROME
                use_foreground_mask = (
                    profile is ConversionProfile.AUTO
                    and is_monochrome
                    and _has_foreground_mask(inspection)
                )
                binarize_composite = (
                    profile is ConversionProfile.CLEAN_BLACK_AND_WHITE
                    or (
                        profile is ConversionProfile.AUTO
                        and page_type is PageType.MONOCHROME
                        and not use_foreground_mask
                    )
                )
                output_format = (
                    "pgm"
                    if binarize_composite or page_type is PageType.GRAYSCALE
                    else "tiff" if is_monochrome else "ppm"
                )
                raster_path = work_dir / (
                    f"page-{page_index:05d}."
                    f"{'tif' if is_monochrome else output_format}"
                )
                rendered_path = (
                    work_dir / f"page-{page_index:05d}-composite.pgm"
                    if binarize_composite
                    else work_dir / f"page-{page_index:05d}-mask.tif"
                    if use_foreground_mask
                    else raster_path
                )
                command = [
                    str(executables.ddjvu),
                    f"-format={output_format}",
                ]
                if use_foreground_mask:
                    command.append("-mode=mask")
                elif is_monochrome and not binarize_composite:
                    command.append("-mode=black")
                if not is_monochrome and not binarize_composite:
                    width, height, rendered_dpi = _render_size(inspection)
                    if width > 0 and height > 0:
                        command.append(f"-size={width}x{height}")
                command.extend(
                    (
                        f"-page={page_index}",
                        str(source),
                        str(rendered_path),
                    )
                )
                token.run(
                    command,
                    f"рендеринг страницы {page_index}",
                    cwd=str(source.parent),
                )
                if binarize_composite:
                    _binarize_grayscale(rendered_path, raster_path)
                    rendered_path.unlink()
                elif use_foreground_mask:
                    with Image.open(rendered_path) as image:
                        image.convert("1").save(
                            raster_path,
                            format="TIFF",
                            compression="group4",
                        )
                    rendered_path.unlink()
                if is_monochrome:
                    width, height = _tiff_dimensions(raster_path)
                    with Image.open(raster_path) as image:
                        if image.mode != "1":
                            raise ConversionError(
                                f"ddjvu создал не 1-битный TIFF для страницы {page_index} "
                                f"(режим {image.mode})."
                            )
                    rendered_dpi = _DPI
                else:
                    with Image.open(raster_path) as image:
                        width, height = image.size
                    if inspection.width and inspection.height:
                        _, _, rendered_dpi = _render_size(inspection)
                    else:
                        rendered_dpi = _DPI
                rendered.append(
                    _RenderedPage(
                        page_type,
                        raster_path,
                        width,
                        height,
                        rendered_dpi,
                    )
                )
                notify(
                    f"Извлечение растров: {page_index} из {page_count}...",
                    10 + (40 * page_index // page_count),
                )

            page_images: list[PageImage | None] = [None] * page_count
            monochrome = [
                (index, page)
                for index, page in enumerate(rendered)
                if page.page_type is PageType.MONOCHROME
            ]
            chunks = [
                monochrome[index:index + chunk_size]
                for index in range(0, len(monochrome), chunk_size)
            ]
            raster_indices = [
                index
                for index, page in enumerate(rendered)
                if page.page_type is not PageType.MONOCHROME
            ]
            compression_steps = len(chunks) + len(raster_indices)
            completed_steps = 0

            for page_index in raster_indices:
                token.check()
                rendered_page = rendered[page_index]
                encoding = (
                    "jpeg"
                    if rendered_page.page_type is PageType.COLOR
                    else "jpeg-gray"
                )
                jpeg_path = work_dir / f"page-{page_index + 1:05d}.jpg"
                with Image.open(rendered_page.path) as image:
                    encoded_image = (
                        image.convert("RGB")
                        if encoding == "jpeg"
                        else _normalize_grayscale_background(image)
                    )
                    encoded_image.save(
                        jpeg_path,
                        format="JPEG",
                        quality=(
                            _GRAYSCALE_JPEG_QUALITY
                            if encoding == "jpeg-gray"
                            else _JPEG_QUALITY
                        ),
                        optimize=True,
                        **(
                            {"subsampling": "4:2:0"}
                            if encoding == "jpeg"
                            else {}
                        ),
                    )
                page_images[page_index] = PageImage(
                    jpeg_path,
                    rendered_page.width,
                    rendered_page.height,
                    encoding,
                    dpi=rendered_page.dpi,
                )
                completed_steps += 1
                raster_type = (
                    "цветной"
                    if rendered_page.page_type is PageType.COLOR
                    else "серой"
                )
                notify(
                    f"Сжатие {raster_type} страницы {page_index + 1}...",
                    50 + (35 * completed_steps // max(compression_steps, 1)),
                )

            for chunk_index, chunk in enumerate(chunks):
                token.check()
                prefix = work_dir / f"jbig2-chunk-{chunk_index:04d}"
                notify(
                    f"Сжатие JBIG2: чанк {chunk_index + 1} из {len(chunks)} "
                    f"({len(chunk)} стр.)...",
                    50 + (35 * completed_steps // max(compression_steps, 1)),
                )
                command = [str(executables.jbig2)]
                if use_symbol_dictionary:
                    command.append("-s")
                command.extend(("-p", "-b", str(prefix)))
                command.extend(str(page.path) for _, page in chunk)
                token.run(
                    command,
                    f"сжатие JBIG2, чанк {chunk_index + 1}",
                    cwd=str(work_dir),
                )

                image_paths = _jbig2_page_outputs(prefix, len(chunk))
                globals_path = prefix.with_suffix(".sym")
                if use_symbol_dictionary and not globals_path.is_file():
                    raise ConversionError(
                        f"jbig2 не создал словарь символов для чанка {chunk_index + 1}."
                    )
                if not globals_path.is_file():
                    globals_path = None
                for (page_index, page), image_path in zip(chunk, image_paths):
                    page_images[page_index] = PageImage(
                        image_path,
                        page.width,
                        page.height,
                        "jbig2",
                        globals_path,
                        page.dpi,
                    )

                completed_steps += 1
                notify(
                    f"JBIG2: чанк {chunk_index + 1} из {len(chunks)} готов.",
                    50 + (35 * completed_steps // max(compression_steps, 1)),
                )

            if any(image is None for image in page_images):
                raise ConversionError("Не для всех страниц подготовлено изображение.")
            token.check()
            notify("Сборка PDF...", 85)
            assemble_pdf(
                destination,
                tuple(image for image in page_images if image is not None),
                pages,
                outlines,
                preserve_text=preserve_text,
                dpi=_DPI,
                cancellation=token,
                progress=notify,
            )

        notify("Готово.", 100)
        return destination
    except ConversionCancelled:
        raise
    except ConversionProcessError as error:
        raise ConversionError(
            f"{error}\nИсходный файл: {source}\nРабочая папка: {source.parent}"
        ) from error
