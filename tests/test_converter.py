from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from PIL import Image
from pikepdf import Name, Pdf

from core.converter import (
    _binarize_grayscale,
    _normalize_grayscale_background,
    _render_size,
    convert,
)
from core.inspector import PageInspection, PageType
from core.system_check import ToolPaths


class ConverterIntegrationTests(unittest.TestCase):
    def test_adaptive_threshold_preserves_faint_strokes_on_uneven_paper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "uneven-paper.pgm"
            composite = Image.new("L", (100, 100))
            composite.putdata(
                [
                    235 + row * 20 // 99
                    for row in range(100)
                    for _column in range(100)
                ]
            )
            for row in range(48, 52):
                for column in range(20, 80):
                    composite.putpixel((column, row), 215)
            composite.save(source, format="PPM")
            destination = root / "page.tif"

            _binarize_grayscale(source, destination)

            with Image.open(destination) as binary:
                self.assertEqual(binary.mode, "1")
                self.assertEqual(binary.getpixel((50, 50)), 0)
                self.assertEqual(binary.getpixel((10, 10)), 255)

    def test_grayscale_background_normalization_preserves_contrasting_details(self) -> None:
        image = Image.new("L", (9, 9), 230)
        image.putpixel((4, 4), 180)
        image.putpixel((3, 4), 225)

        normalized = _normalize_grayscale_background(image)

        self.assertEqual(normalized.getpixel((0, 0)), 255)
        self.assertEqual(normalized.getpixel((4, 4)), 180)
        self.assertEqual(normalized.getpixel((3, 4)), 225)

    def test_color_raster_size_is_limited_to_150_dpi(self) -> None:
        self.assertEqual(
            _render_size(
                PageInspection(
                    PageType.GRAYSCALE,
                    "page.djvu",
                    width=2480,
                    height=3508,
                    dpi=300,
                )
            ),
            (1240, 1754, 150),
        )

    def test_mixed_document_is_rendered_and_compressed_in_independent_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.djvu"
            source.write_bytes(b"fake DjVu fixture")
            tools_dir = root / "tools"
            tools_dir.mkdir()

            djvused = self._write_tool(
                tools_dir / "djvused",
                """
                import sys
                command = sys.argv[sys.argv.index("-e") + 1]
                if command == "print-txt":
                    print(' '.join(
                        f'(page 0 0 100 200 (line (word 10 20 80 50 "word-{i}")))'
                        for i in range(1, 5)
                    ) + ' (page 0 0 100 200 "")')
                elif command == "print-outline":
                    print('(bookmarks ("Chapter" "#2" ("Last page" "p3.djvu")))')
                """,
            )
            djvudump = self._write_tool(
                tools_dir / "djvudump",
                """
                print("FORM:DJVM [100]")
                print("FORM:DJVU [30] {p1.djvu} Sjbz")
                print("FORM:DJVU [40] {p2.djvu} BG44")
                print("FORM:DJVU [30] {p3.djvu} Sjbz")
                print("FORM:DJVU [40] {p4.djvu} Sjbz BG44")
                print("FORM:DJVU [40] {p5.djvu} Sjbz BG44")
                """,
            )
            ddjvu = self._write_tool(
                tools_dir / "ddjvu",
                """
                import sys
                from PIL import Image
                args = sys.argv
                output = Path(args[-1])
                page = next(arg for arg in args if arg.startswith("-page="))
                if "-mode=background" in args:
                    background = Image.new("L", (100, 200), 255)
                    if page == "-page=5":
                        background = Image.linear_gradient("L").resize((100, 200))
                    background.save(output, format="PPM")
                elif "-mode=mask" in args:
                    assert not any(arg.startswith("-size=") for arg in args)
                    Image.new("1", (100, 200), 1).save(output, format="TIFF")
                elif "-format=ppm" in args:
                    color = page == "-page=2"
                    value = (220, 30, 40) if color else (128, 128, 128)
                    Image.new("RGB", (100, 200), value).save(output, format="PPM")
                elif "-format=pgm" in args:
                    Image.new("L", (100, 200), 128).save(output, format="PPM")
                else:
                    Image.new("1", (100, 200), 1).save(output, format="TIFF")
                """,
                extra_import="from pathlib import Path",
            )
            jbig2 = self._write_tool(
                tools_dir / "jbig2",
                """
                import sys
                args = sys.argv[1:]
                prefix = Path(args[args.index("-b") + 1])
                input_files = [Path(value) for value in args if value.endswith(".tif")]
                for index, _ in enumerate(input_files):
                    prefix.with_name(prefix.name + f".{index:04d}").write_bytes(b"JBIG2" + bytes([index]))
                prefix.with_suffix(".sym").write_bytes(b"SYMBOL" + input_files[0].name.encode())
                """,
                extra_import="from pathlib import Path",
            )
            tools = ToolPaths(ddjvu, djvused, jbig2, djvudump)
            progress: list[int] = []
            progress_messages: list[str] = []

            def record_progress(message: str, percentage: int) -> None:
                progress_messages.append(message)
                progress.append(percentage)

            output = convert(
                source,
                root / "result.pdf",
                tools=tools,
                chunk_size=1,
                profile="auto",
                progress=record_progress,
            )

            self.assertEqual(progress[-1], 100)
            self.assertIn("Сжатие JBIG2: чанк 1 из 3 (1 стр.)...", progress_messages)
            self.assertIn("JBIG2: чанк 1 из 3 готов.", progress_messages)
            self.assertTrue(any(0 <= value <= 10 for value in progress))
            self.assertTrue(any(10 <= value <= 50 for value in progress))
            self.assertTrue(any(50 <= value <= 85 for value in progress))
            self.assertTrue(any(85 <= value <= 100 for value in progress))
            with Pdf.open(output) as pdf:
                self.assertEqual(len(pdf.pages), 5)
                self.assertEqual(pdf.pages[0].Resources.XObject.Im0.Filter, Name.JBIG2Decode)
                color_image = pdf.pages[1].Resources.XObject.Im0
                self.assertEqual(color_image.Filter, Name.DCTDecode)
                self.assertEqual(color_image.ColorSpace, Name.DeviceRGB)
                self.assertEqual(pdf.pages[2].Resources.XObject.Im0.Filter, Name.JBIG2Decode)
                self.assertEqual(pdf.pages[3].Resources.XObject.Im0.Filter, Name.JBIG2Decode)
                grayscale_image = pdf.pages[4].Resources.XObject.Im0
                self.assertEqual(grayscale_image.Filter, Name.DCTDecode)
                self.assertEqual(grayscale_image.ColorSpace, Name.DeviceGray)
                chunk_globals_1 = (
                    pdf.pages[0]
                    .Resources.XObject.Im0.DecodeParms.JBIG2Globals.read_bytes()
                )
                chunk_globals_2 = (
                    pdf.pages[2]
                    .Resources.XObject.Im0.DecodeParms.JBIG2Globals.read_bytes()
                )
                chunk_globals_3 = (
                    pdf.pages[3]
                    .Resources.XObject.Im0.DecodeParms.JBIG2Globals.read_bytes()
                )
                self.assertNotEqual(chunk_globals_1, chunk_globals_2)
                self.assertNotEqual(chunk_globals_2, chunk_globals_3)
                items = pdf.open_outline().root
                self.assertEqual(items[0].title, "Chapter")
                self.assertEqual(items[0].children[0].title, "Last page")
                self.assertEqual(items[0].children[0].destination[0], pdf.pages[2].obj)

    @staticmethod
    def _write_tool(
        path: Path,
        body: str,
        *,
        extra_import: str = "",
    ) -> Path:
        script = (
            f"#!{sys.executable}\n"
            + (extra_import + "\n" if extra_import else "")
            + textwrap.dedent(body)
        )
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
        return path


if __name__ == "__main__":
    unittest.main()
