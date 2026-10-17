from __future__ import annotations

import unittest

from core.inspector import PageType, parse_djvudump_output
from core.parser import parse_print_outline, parse_print_txt


class DjVuStringDecodingTests(unittest.TestCase):
    def test_print_txt_decodes_octal_utf8(self) -> None:
        page, = parse_print_txt(
            r'(page 0 0 1000 1200 (line (word 10 20 100 60 '
            r'"\320\242\320\265\321\201\321\202")))'
        )

        self.assertEqual(page.words[0].text, "Тест")

    def test_invalid_octal_utf8_is_replaced(self) -> None:
        page, = parse_print_txt(
            r'(page 0 0 1000 1200 (line (word 10 20 100 60 "\377")))'
        )

        self.assertEqual(page.words[0].text, "\ufffd")

    def test_print_outline_decodes_octal_utf8_and_preserves_nesting(self) -> None:
        entries = parse_print_outline(
            r'(bookmarks ("\320\223\320\273\320\260\320\262\320\260 1" '
            r'"#1" ("Подглава" "#5")) ("Chapter 2" "#15"))',
            page_count=20,
        )

        self.assertEqual(entries[0].title, "Глава 1")
        self.assertEqual(entries[0].page_index, 0)
        self.assertEqual(entries[0].children[0].page_index, 4)
        self.assertEqual(entries[1].page_index, 14)

    def test_outline_resolves_component_ids(self) -> None:
        entries = parse_print_outline(
            '(bookmarks ("Раздел" "page-component.djvu"))',
            page_count=3,
            component_pages={"page-component.djvu": 2},
        )

        self.assertEqual(entries[0].page_index, 2)

    def test_outline_keeps_unresolvable_entry_without_destination(self) -> None:
        entries = parse_print_outline(
            '(bookmarks ("Unknown" "#100"))',
            page_count=4,
        )

        self.assertIsNone(entries[0].page_index)

    def test_djvudump_identifies_color_chunks_and_component_ids(self) -> None:
        pages = parse_djvudump_output(
            "FORM:DJVM [100]\n"
            "  FORM:DJVU [20] {page-one.djvu}\n"
            "    Sjbz [10]\n"
            "  FORM:DJVU [80] {page-two.djvu}\n"
            "    BG44 [30]\n",
            page_count=2,
        )

        self.assertEqual(pages[0].page_type, PageType.MONOCHROME)
        self.assertEqual(pages[0].component_id, "page-one.djvu")
        self.assertEqual(pages[1].page_type, PageType.COLOR)
        self.assertEqual(pages[1].component_id, "page-two.djvu")

    def test_djvudump_identifies_color_from_included_component(self) -> None:
        pages = parse_djvudump_output(
            "FORM:DJVI [20] {shared-color.djvi}\n"
            "  BG44 [10]\n"
            "FORM:DJVU [30] {page.djvu}\n"
            "  INCL [5] {shared-color.djvi}\n"
            "  Sjbz [10]\n",
            page_count=1,
        )

        self.assertEqual(pages[0].page_type, PageType.COLOR)


if __name__ == "__main__":
    unittest.main()
