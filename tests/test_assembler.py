from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image
from pikepdf import Name, Pdf

from core.assembler import PageImage, assemble_pdf
from core.parser import OutlineEntry, PageText, WordBox


class PdfAssemblyTests(unittest.TestCase):
    def test_outline_and_chunk_local_globals_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page_text = (PageText(1, 100, 200, ()), PageText(2, 100, 200, ()))
            images: list[PageImage] = []
            for index in range(2):
                encoded_page = root / f"page-{index}.jb2"
                encoded_page.write_bytes(bytes((index, 1, 2)))
                dictionary = root / f"chunk-{index}.sym"
                dictionary.write_bytes(bytes((index + 10, 20)))
                images.append(PageImage(encoded_page, 100, 200, "jbig2", dictionary))

            destination = root / "book.pdf"
            assemble_pdf(
                destination,
                tuple(images),
                page_text,
                (
                    OutlineEntry(
                        "Глава",
                        0,
                        (OutlineEntry("Подглава", 1),),
                    ),
                ),
                preserve_text=False,
            )

            with Pdf.open(destination) as pdf:
                self.assertEqual(len(pdf.pages), 2)
                outline_item = pdf.open_outline().root[0]
                self.assertEqual(outline_item.title, "Глава")
                self.assertEqual(outline_item.children[0].title, "Подглава")
                self.assertEqual(outline_item.destination[0], pdf.pages[0].obj)
                self.assertEqual(
                    outline_item.children[0].destination[0],
                    pdf.pages[1].obj,
                )
                for index, page in enumerate(pdf.pages):
                    image = page.Resources.XObject.Im0
                    self.assertEqual(
                        image.DecodeParms.JBIG2Globals.read_bytes(),
                        bytes((index + 10, 20)),
                    )

    def test_color_image_uses_dct_decode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jpeg = root / "cover.jpg"
            Image.new("RGB", (100, 200), color=(220, 20, 30)).save(jpeg, quality=83)
            destination = root / "color.pdf"
            assemble_pdf(
                destination,
                (PageImage(jpeg, 100, 200, "jpeg"),),
                (PageText(1, 100, 200, ()),),
                (),
                preserve_text=False,
            )

            with Pdf.open(destination) as pdf:
                image = pdf.pages[0].Resources.XObject.Im0
                self.assertEqual(image.Filter, Name.DCTDecode)
                self.assertEqual(image.ColorSpace, Name.DeviceRGB)

    def test_grayscale_image_uses_device_gray_and_preserves_page_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jpeg = root / "gray.jpg"
            Image.new("L", (100, 200), 128).save(
                jpeg,
                format="JPEG",
                quality=78,
                optimize=True,
            )
            destination = root / "gray.pdf"
            assemble_pdf(
                destination,
                (PageImage(jpeg, 100, 200, "jpeg-gray", dpi=200),),
                (PageText(1, 150, 300, ()),),
                (),
                preserve_text=False,
            )

            with Pdf.open(destination) as pdf:
                image = pdf.pages[0].Resources.XObject.Im0
                self.assertEqual(image.Filter, Name.DCTDecode)
                self.assertEqual(image.ColorSpace, Name.DeviceGray)
                self.assertAlmostEqual(float(pdf.pages[0].MediaBox[2]), 36.0)
                self.assertAlmostEqual(float(pdf.pages[0].MediaBox[3]), 72.0)

    def test_cyrillic_text_is_encoded_as_unicode_actual_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "page.jb2"
            image.write_bytes(b"JBIG2")
            destination = root / "searchable.pdf"
            assemble_pdf(
                destination,
                (PageImage(image, 100, 200, "jbig2"),),
                (PageText(1, 100, 200, (WordBox("Тест", 10, 20, 50, 40),)),),
                (),
                preserve_text=True,
            )

            with Pdf.open(destination) as pdf:
                content = pdf.pages[0].Contents.read_bytes()
                self.assertIn(b"3 Tr", content)
                self.assertIn(b"/ActualText <FEFF04220435044104420020>", content)


if __name__ == "__main__":
    unittest.main()
