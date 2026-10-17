"""Build a PDF from JBIG2/JPEG image streams and DjVu metadata."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pikepdf import Array, Dictionary, Name, OutlineItem as PdfOutlineItem, Pdf, Stream

from .parser import OutlineEntry, PageText
from .process import CancellationToken


@dataclass(frozen=True)
class PageImage:
    path: Path
    width: int
    height: int
    encoding: str
    globals_path: Path | None = None
    dpi: int = 300


def _actual_text_hex(text: str) -> str:
    return text.encode("utf-16-be").hex().upper()


def _text_layer(
    page: PageText,
    page_width: float,
    page_height: float,
    source_width: int,
    source_height: int,
) -> bytes:
    operations: list[str] = ["BT", "/F1 1 Tf", "3 Tr"]
    for word in page.words:
        x = (word.x1 - page.origin_x) * page_width / source_width
        y = page_height - (word.y2 - page.origin_y) * page_height / source_height
        actual_text = _actual_text_hex(word.text + " ")
        operations.extend(
            (
                f"1 0 0 1 {x:.4f} {y:.4f} Tm",
                f"/Span << /ActualText <FEFF{actual_text}> >> BDC",
                "<> Tj",
                "EMC",
            )
        )
    operations.append("ET")
    return ("\n".join(operations) + "\n").encode("ascii")


def _add_outline_items(
    target: list[PdfOutlineItem],
    entries: tuple[OutlineEntry, ...],
) -> None:
    for entry in entries:
        item = PdfOutlineItem(entry.title, entry.page_index)
        target.append(item)
        _add_outline_items(item.children, entry.children)


def assemble_pdf(
    output_path: Path,
    images: tuple[PageImage, ...],
    pages: tuple[PageText, ...],
    outlines: tuple[OutlineEntry, ...],
    *,
    preserve_text: bool,
    dpi: int = 300,
    cancellation: CancellationToken | None = None,
    progress: Callable[[str, int], None] | None = None,
) -> None:
    """Write page images, searchable text, and nested clickable outlines."""
    if not images:
        raise ValueError("Нельзя собрать PDF без изображений страниц.")
    if preserve_text and len(pages) != len(images):
        raise ValueError("Число текстовых страниц не совпадает с числом изображений.")
    if dpi <= 0:
        raise ValueError("Разрешение DPI должно быть положительным.")

    pdf = Pdf.new()
    global_streams: dict[Path, Stream] = {}
    for index, image_info in enumerate(images):
        if cancellation is not None:
            cancellation.check()
        if image_info.width <= 0 or image_info.height <= 0:
            raise ValueError(f"Некорректные размеры страницы {index + 1}.")

        image_dpi = image_info.dpi or dpi
        page_width = image_info.width * 72.0 / image_dpi
        page_height = image_info.height * 72.0 / image_dpi
        page = pdf.add_blank_page(page_size=(page_width, page_height))
        image = Stream(pdf, image_info.path.read_bytes())
        image.Type = Name.XObject
        image.Subtype = Name.Image
        image.Width = image_info.width
        image.Height = image_info.height

        if image_info.encoding == "jbig2":
            image.BitsPerComponent = 1
            image.ColorSpace = Name.DeviceGray
            image.Filter = Name.JBIG2Decode
            image.Decode = Array([0, 1])
            if image_info.globals_path is not None:
                global_stream = global_streams.get(image_info.globals_path)
                if global_stream is None:
                    global_stream = Stream(pdf, image_info.globals_path.read_bytes())
                    global_streams[image_info.globals_path] = global_stream
                image.DecodeParms = Dictionary(JBIG2Globals=global_stream)
        elif image_info.encoding == "jpeg":
            image.BitsPerComponent = 8
            image.ColorSpace = Name.DeviceRGB
            image.Filter = Name.DCTDecode
        elif image_info.encoding == "jpeg-gray":
            image.BitsPerComponent = 8
            image.ColorSpace = Name.DeviceGray
            image.Filter = Name.DCTDecode
        else:
            raise ValueError(f"Неизвестное сжатие изображения: {image_info.encoding}")

        resources = Dictionary(XObject=Dictionary(Im0=image))
        contents = [
            f"q {page_width:.4f} 0 0 {page_height:.4f} 0 0 cm /Im0 Do Q"
        ]
        if preserve_text:
            text_page = pages[index]
            source_width = text_page.width or image_info.width
            source_height = text_page.height or image_info.height
            if source_width <= 0 or source_height <= 0:
                raise ValueError(f"Некорректные размеры текстовой страницы {index + 1}.")
            resources.Font = Dictionary(
                F1=Dictionary(
                    Type=Name.Font,
                    Subtype=Name.Type1,
                    BaseFont=Name.Helvetica,
                )
            )
            contents.append(
                _text_layer(
                    text_page,
                    page_width,
                    page_height,
                    source_width,
                    source_height,
                ).decode("ascii")
            )
        page.Resources = resources
        page.Contents = Stream(pdf, ("\n".join(contents) + "\n").encode("ascii"))
        if progress is not None:
            progress(
                f"Сборка PDF: страница {index + 1} из {len(images)}...",
                85 + (12 * (index + 1) // len(images)),
            )

    if outlines:
        if progress is not None:
            progress("Добавление оглавления...", 98)
        with pdf.open_outline() as outline:
            _add_outline_items(outline.root, outlines)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        if progress is not None:
            progress("Запись PDF на диск...", 99)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.name}.",
            suffix=".partial",
            dir=output_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        pdf.save(temporary_path)
        if cancellation is not None:
            cancellation.check()
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
