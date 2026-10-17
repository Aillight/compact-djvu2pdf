"""DjVu page metadata and color-page inspection."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PIL import Image, ImageChops

from .process import CancellationToken
from .system_check import ToolPaths


class PageType(Enum):
    MONOCHROME = "monochrome"
    GRAYSCALE = "grayscale"
    COLOR = "color"


@dataclass(frozen=True)
class PageChunk:
    name: str
    size: int


@dataclass(frozen=True)
class PageInspection:
    page_type: PageType
    component_id: str | None
    chunks: tuple[PageChunk, ...] = ()
    width: int = 0
    height: int = 0
    dpi: int = 300
    mean_chroma: float = 0.0
    chromatic_fraction: float = 0.0


_COLOR_DATA_CHUNKS = frozenset({"BG44", "FG44", "FGbz", "PM44"})
_MIN_MEAN_CHROMA = 1.0
_MIN_CHROMATIC_FRACTION = 0.005
_BACKGROUND_TONE_RANGE = range(40, 221)
_MAX_SIMPLE_BACKGROUND_TONE_FRACTION = 0.10


def inspect_document(
    input_path: Path,
    page_count: int,
    tools: ToolPaths,
    cancellation: CancellationToken,
    *,
    detect_color: bool = True,
) -> tuple[PageInspection, ...]:
    """Read page metadata and verify candidate color pages from small thumbnails."""
    if tools.djvudump is None:
        raise RuntimeError("Для определения цветных страниц необходима djvudump.")
    cancellation.check()
    output = cancellation.run(
        [
            str(tools.djvudump),
            str(input_path),
        ],
        "чтение структуры DjVu",
        cwd=str(input_path.parent),
    )
    pages = parse_djvudump_output(output, page_count)
    if not detect_color or not any(
        page.page_type is PageType.COLOR for page in pages
    ):
        return pages

    with tempfile.TemporaryDirectory(prefix="djvu2pdf-inspect-") as temporary:
        thumbnail_path = Path(temporary) / "thumbnail.ppm"
        background_path = Path(temporary) / "background.pgm"
        inspected: list[PageInspection] = []
        for page_number, page in enumerate(pages, start=1):
            cancellation.check()
            if page.page_type is PageType.MONOCHROME:
                inspected.append(page)
                continue
            thumbnail_path.unlink(missing_ok=True)
            cancellation.run(
                [
                    str(tools.ddjvu),
                    "-format=ppm",
                    f"-page={page_number}",
                    "-size=150x200",
                    str(input_path),
                    str(thumbnail_path),
                ],
                f"проверка цвета страницы {page_number}",
                cwd=str(input_path.parent),
            )
            try:
                with Image.open(thumbnail_path) as image:
                    page_type, mean_chroma, chromatic_fraction = (
                        classify_thumbnail(image)
                    )
                if page_type is PageType.GRAYSCALE:
                    background_path.unlink(missing_ok=True)
                    cancellation.run(
                        [
                            str(tools.ddjvu),
                            "-format=pgm",
                            "-mode=background",
                            f"-page={page_number}",
                            "-size=150x200",
                            str(input_path),
                            str(background_path),
                        ],
                        f"анализ фона страницы {page_number}",
                        cwd=str(input_path.parent),
                    )
                    with Image.open(background_path) as background:
                        if not background_requires_grayscale(background):
                            page_type = PageType.MONOCHROME
            except OSError as error:
                raise RuntimeError(
                    f"Не удалось проверить цвет страницы {page_number}: {error}"
                ) from error
            inspected.append(
                PageInspection(
                    page_type,
                    page.component_id,
                    page.chunks,
                    page.width,
                    page.height,
                    page.dpi,
                    mean_chroma,
                    chromatic_fraction,
                )
            )
    return tuple(inspected)


def classify_thumbnail(image: Image.Image) -> tuple[PageType, float, float]:
    """Classify rendered pixels by meaningful chroma, not RGB container mode."""
    red, green, blue = image.convert("RGB").split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
    histogram = ImageChops.difference(maximum, minimum).histogram()
    pixel_count = sum(histogram)
    if pixel_count == 0:
        return PageType.GRAYSCALE, 0.0, 0.0
    mean_chroma = sum(level * count for level, count in enumerate(histogram)) / pixel_count
    chromatic_pixels = sum(histogram[12:])
    chromatic_fraction = chromatic_pixels / pixel_count
    page_type = (
        PageType.COLOR
        if mean_chroma >= _MIN_MEAN_CHROMA
        and chromatic_fraction >= _MIN_CHROMATIC_FRACTION
        else PageType.GRAYSCALE
    )
    return page_type, mean_chroma, chromatic_fraction


def background_requires_grayscale(image: Image.Image) -> bool:
    """Keep tonal backgrounds as grayscale when they contain substantial detail."""
    histogram = image.convert("L").histogram()
    pixel_count = sum(histogram)
    if pixel_count == 0:
        raise ValueError("Нельзя классифицировать пустое изображение фона.")
    tonal_pixels = sum(histogram[level] for level in _BACKGROUND_TONE_RANGE)
    return tonal_pixels / pixel_count > _MAX_SIMPLE_BACKGROUND_TONE_FRACTION


def parse_djvudump_output(
    output: str,
    page_count: int,
) -> tuple[PageInspection, ...]:
    """Parse DjVuLibre's textual document dump into ordered page metadata."""
    component_blocks = [
        block for block in re.split(r"(?=FORM:DJVI\b)", output)
        if re.match(r"FORM:DJVI\b", block)
    ]
    color_component_ids: dict[str, tuple[PageChunk, ...]] = {}
    for block in component_blocks:
        component_match = re.search(r"FORM:DJVI[^\n]*\{([^}]+)\}", block)
        chunks = _parse_chunks(block)
        if component_match and _has_color_data(chunks, block):
            color_component_ids[component_match.group(1).strip()] = chunks

    page_blocks = [
        block for block in re.split(r"(?=FORM:DJVU\b)", output)
        if re.match(r"FORM:DJVU\b", block)
    ]
    if len(page_blocks) != page_count:
        raise RuntimeError(
            "Не удалось однозначно сопоставить страницы DjVu с чанками djvudump: "
            f"ожидалось {page_count}, найдено {len(page_blocks)}."
        )

    inspections: list[PageInspection] = []
    for block in page_blocks:
        component_match = re.search(r"FORM:DJVU[^\n]*\{([^}]+)\}", block)
        component_id = component_match.group(1).strip() if component_match else None
        chunks = _parse_chunks(block)
        color = _has_color_data(chunks, block)
        if not color and color_component_ids:
            included_ids = (
                included_id.strip()
                for included_id in re.findall(r"\bINCL[^\n]*\{([^}]+)\}", block)
            )
            for included_id in included_ids:
                component_chunks = color_component_ids.get(included_id)
                if component_chunks is not None:
                    chunks += component_chunks
                    color = True
                    break
        info_match = re.search(
            r"\bINFO\s+\[\d+\].*?\bDjVu\s+(\d+)x(\d+),\s*v\d+,\s*(\d+)\s*dpi",
            block,
        )
        width = int(info_match.group(1)) if info_match else 0
        height = int(info_match.group(2)) if info_match else 0
        dpi = int(info_match.group(3)) if info_match else 300
        inspections.append(
            PageInspection(
                PageType.COLOR if color else PageType.MONOCHROME,
                component_id,
                chunks,
                width,
                height,
                dpi,
            )
        )
    return tuple(inspections)


def _parse_chunks(block: str) -> tuple[PageChunk, ...]:
    return tuple(
        PageChunk(match.group(1), int(match.group(2)))
        for match in re.finditer(
            r"^\s+([A-Za-z0-9]{4})\s+\[(\d+)\]",
            block,
            re.MULTILINE,
        )
    )


def _has_color_data(chunks: tuple[PageChunk, ...], block: str) -> bool:
    if any(chunk.name in _COLOR_DATA_CHUNKS and chunk.size > 0 for chunk in chunks):
        return True
    # Keep compatibility with DjVuLibre variants that print inline chunk names.
    return any(
        re.search(rf"\b{name}\b", block) is not None
        for name in _COLOR_DATA_CHUNKS
    ) and not chunks
