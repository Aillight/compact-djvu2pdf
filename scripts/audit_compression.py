#!/usr/bin/env python3
"""Report DjVu page metadata beside the image streams in a converted PDF."""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pikepdf import Array

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from core.inspector import PageInspection, PageType, inspect_document
from core.parser import parse_print_txt
from core.process import CancellationToken
from core.system_check import find_tools


def _filter_name(value: object) -> str:
    if isinstance(value, (list, tuple, Array)):
        return ",".join(_filter_name(item) for item in value)
    return str(value).lstrip("/")


def _page_images(page: object) -> list[tuple[str, str, int, int, int]]:
    resources = page.get("/Resources", {})
    xobjects = resources.get("/XObject", {})
    images: list[tuple[str, str, int, int, int]] = []
    for name, image in xobjects.items():
        if str(image.get("/Subtype", "")) != "/Image":
            continue
        images.append(
            (
                _filter_name(image.get("/Filter", "none")),
                str(image.get("/ColorSpace", "unknown")).lstrip("/"),
                int(image.get("/Width", 0)),
                int(image.get("/Height", 0)),
                len(image.read_raw_bytes()),
            )
        )
    return images


def _chunk_summary(page: PageInspection) -> str:
    sizes: dict[str, list[int]] = defaultdict(list)
    for chunk in page.chunks:
        sizes[chunk.name].append(chunk.size)
    return ", ".join(
        f"{name}({'+'.join(str(size) for size in chunk_sizes)})"
        for name, chunk_sizes in sizes.items()
    ) or "—"


def audit(source: Path, pdf_path: Path) -> int:
    tools = find_tools()
    source = source.expanduser().resolve()
    pdf_path = pdf_path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"DjVu не найден: {source}")
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF не найден: {pdf_path}")

    token = CancellationToken()
    text = token.run(
        [str(tools.djvused), str(source), "-e", "print-txt"],
        "подсчёт страниц DjVu",
        cwd=str(source.parent),
    )
    source_pages = parse_print_txt(text)
    inspections = inspect_document(source, len(source_pages), tools, token)

    import pikepdf

    with pikepdf.Pdf.open(pdf_path) as document:
        pdf_pages = list(document.pages)
        print(f"DjVu: {source} ({len(inspections)} стр.)")
        print(f"PDF:  {pdf_path} ({pdf_path.stat().st_size:,} байт)")
        print()
        print(
            "Стр. | Тип DjVu | Размер/DPI | Чанки DjVu | "
            "PDF XObject (фильтр, цвет, пиксели, байты)"
        )
        print("-" * 132)

        streams: Counter[tuple[str, str]] = Counter()
        stream_bytes: Counter[tuple[str, str]] = Counter()
        filter_pages: Counter[str] = Counter()
        type_counts: Counter[PageType] = Counter()

        for index, inspection in enumerate(inspections):
            type_counts[inspection.page_type] += 1
            if index < len(pdf_pages):
                images = _page_images(pdf_pages[index])
            else:
                images = []
            pdf_description = []
            page_filters: set[str] = set()
            for filter_name, color_space, width, height, stream_length in images:
                pdf_description.append(
                    f"{filter_name}/{color_space} {width}x{height} "
                    f"{stream_length:,}B"
                )
                stream_key = (filter_name, color_space)
                streams[stream_key] += 1
                stream_bytes[stream_key] += stream_length
                page_filters.add(filter_name)
            for filter_name in page_filters:
                filter_pages[filter_name] += 1

            dimensions = (
                f"{inspection.width}x{inspection.height} px @ {inspection.dpi} dpi"
                if inspection.width and inspection.height
                else "не указаны"
            )
            classification = inspection.page_type.value.upper()
            if inspection.page_type is not PageType.MONOCHROME:
                classification += (
                    f" (chroma {inspection.mean_chroma:.2f}, "
                    f"цветных пикселей {inspection.chromatic_fraction:.2%})"
                )
            print(
                f"{index + 1:4} | {classification:20} | {dimensions:25} | "
                f"{_chunk_summary(inspection)} | "
                f"{'; '.join(pdf_description) or 'изображение не найдено'}"
            )

        print("\nИтог")
        print(
            "Классификация DjVu: "
            + ", ".join(
                f"{page_type.value}={type_counts[page_type]}"
                for page_type in PageType
            )
        )
        print(
            "Страниц PDF: "
            + ", ".join(
                f"{filter_name}={filter_pages[filter_name]}"
                for filter_name in sorted(filter_pages)
            )
        )
        print(
            f"JBIG2-страниц: {filter_pages['JBIG2Decode']}, "
            f"JPEG-страниц: {filter_pages['DCTDecode']}"
        )
        print("Потоки изображений:")
        for filter_name, color_space in sorted(stream_bytes):
            print(
                f"  {filter_name}/{color_space}: "
                f"{streams[(filter_name, color_space)]} поток(ов), "
                f"{stream_bytes[(filter_name, color_space)]:,} байт"
            )
        if len(pdf_pages) != len(inspections):
            print(
                f"ВНИМАНИЕ: число страниц не совпадает "
                f"(DjVu {len(inspections)}, PDF {len(pdf_pages)})."
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Сопоставить структуру DjVu и сжатые потоки PDF постранично."
    )
    parser.add_argument("djvu", type=Path, help="Исходный DjVu")
    parser.add_argument("pdf", type=Path, help="Сконвертированный PDF")
    arguments = parser.parse_args()
    return audit(arguments.djvu, arguments.pdf)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Аудит не выполнен: {error}", file=sys.stderr)
        raise SystemExit(1) from error
