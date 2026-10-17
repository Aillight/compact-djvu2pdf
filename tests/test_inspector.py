from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from PIL import Image

from core.inspector import (
    PageType,
    background_requires_grayscale,
    classify_thumbnail,
    inspect_document,
    parse_djvudump_output,
)
from core.process import CancellationToken
from core.system_check import ToolPaths


class DjVuInspectionTests(unittest.TestCase):
    def test_black_and_white_page_with_empty_color_chunk_stays_monochrome(self) -> None:
        output = """
        FORM:DJVM [100]
          FORM:DJVU [50] {page.djvu}
            INFO [10] DjVu 2480x3508, v24, 300 dpi, gamma=2.2
            Sjbz [498] JB2 bilevel data
            BG44 [0] empty background layer
        """

        (page,) = parse_djvudump_output(output, 1)

        self.assertEqual(page.page_type, PageType.MONOCHROME)
        self.assertEqual((page.width, page.height, page.dpi), (2480, 3508, 300))
        self.assertEqual(
            [(chunk.name, chunk.size) for chunk in page.chunks],
            [("INFO", 10), ("Sjbz", 498), ("BG44", 0)],
        )

    def test_grayscale_page_with_real_background_chunk_is_not_color(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.djvu"
            source.write_bytes(b"fixture")
            dump = self._write_tool(
                root / "djvudump",
                """
                print('''FORM:DJVM [100]
                  FORM:DJVU [50] {page.djvu}
                    INFO [10] DjVu 2480x3508, v24, 300 dpi, gamma=2.2
                    Sjbz [498] JB2 bilevel data
                    FGbz [650] JB2 colors data
                    BG44 [5000] IW4 data
                ''')
                """,
            )
            renderer = self._write_tool(
                root / "ddjvu",
                """
                import sys
                from PIL import Image
                args = sys.argv
                output = args[-1]
                if "-mode=background" in args:
                    Image.linear_gradient("L").resize((150, 200)).save(
                        output, format="PPM"
                    )
                else:
                    Image.new("RGB", (150, 200), (128, 128, 128)).save(
                        output, format="PPM"
                    )
                """,
            )
            tools = ToolPaths(
                renderer,
                root / "djvused",
                root / "jbig2",
                dump,
            )

            (page,) = inspect_document(source, 1, tools, CancellationToken())

        self.assertEqual(page.page_type, PageType.GRAYSCALE)
        self.assertEqual((page.width, page.height, page.dpi), (2480, 3508, 300))
        self.assertEqual(page.chunks[-1].name, "BG44")
        self.assertGreater(page.chunks[-1].size, 0)
        self.assertLess(page.mean_chroma, 1.0)

    def test_background_classifier_keeps_photographic_tones_in_grayscale(self) -> None:
        simple_paper = Image.new("L", (150, 200), 245)
        detailed_background = Image.linear_gradient("L").resize((150, 200))

        self.assertFalse(background_requires_grayscale(simple_paper))
        self.assertTrue(background_requires_grayscale(detailed_background))

    def test_saturated_thumbnail_is_classified_as_color(self) -> None:
        page_type, mean_chroma, fraction = classify_thumbnail(
            Image.new("RGB", (150, 200), (220, 20, 30))
        )

        self.assertEqual(page_type, PageType.COLOR)
        self.assertGreater(mean_chroma, 1.0)
        self.assertEqual(fraction, 1.0)

    @staticmethod
    def _write_tool(path: Path, body: str) -> Path:
        path.write_text(
            f"#!{sys.executable}\n" + textwrap.dedent(body),
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path


if __name__ == "__main__":
    unittest.main()
