"""Parser for the S-expression output produced by ``djvused print-txt``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class WordBox:
    text: str
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class PageText:
    number: int
    width: int | None
    height: int | None
    words: tuple[WordBox, ...]
    origin_x: int = 0
    origin_y: int = 0


@dataclass(frozen=True)
class OutlineEntry:
    title: str
    page_index: int | None
    children: tuple[OutlineEntry, ...] = ()
    target: str | None = None


_TOKEN_RE = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+')


def _decode_string(token: str) -> str:
    body = token[1:-1]
    result = bytearray()
    index = 0
    escapes = {"n": b"\n", "r": b"\r", "t": b"\t", "b": b"\b", "f": b"\f"}

    while index < len(body):
        char = body[index]
        if char != "\\":
            result.extend(char.encode("utf-8"))
            index += 1
            continue

        index += 1
        if index == len(body):
            result.extend(b"\\")
            break
        escaped = body[index]
        if escaped in escapes:
            result.extend(escapes[escaped])
            index += 1
        elif escaped in ('"', "\\"):
            result.extend(escaped.encode("utf-8"))
            index += 1
        elif escaped in "01234567":
            end = index
            while end < min(index + 3, len(body)) and body[end] in "01234567":
                end += 1
            result.append(int(body[index:end], 8))
            index = end
        else:
            result.extend(escaped.encode("utf-8"))
            index += 1

    return result.decode("utf-8", errors="replace")


def _parse_tokens(tokens: list[str]) -> list[object]:
    roots: list[object] = []
    stack: list[list[object]] = []

    for token in tokens:
        if token == "(":
            node: list[object] = []
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
        elif token == ")":
            if not stack:
                raise ValueError("Лишняя закрывающая скобка в выводе djvused.")
            stack.pop()
        else:
            value: object = _decode_string(token) if token.startswith('"') else token
            (stack[-1] if stack else roots).append(value)

    if stack:
        raise ValueError("Незакрытая скобка в выводе djvused.")
    return roots


def _walk(node: object) -> Iterator[list[object]]:
    if not isinstance(node, list):
        return
    yield node
    for child in node:
        yield from _walk(child)


def _as_int(value: object) -> int | None:
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
        return int(value)
    return None


def _parse_page_target(
    target: str,
    page_count: int,
    component_pages: dict[str, int],
) -> int | None:
    if target.startswith("#") and target[1:].isdigit():
        page_index = int(target[1:]) - 1
    elif target.isdigit():
        page_index = int(target) - 1
    else:
        page_index = component_pages.get(target)
        if page_index is None:
            component_name = target.rsplit("/", 1)[-1]
            page_index = component_pages.get(component_name)
            if page_index is None:
                page_index = component_pages.get(component_name.casefold())
            if page_index is None:
                page_index = component_pages.get(component_name.rsplit(".", 1)[0])
            if page_index is None:
                page_index = component_pages.get(
                    component_name.rsplit(".", 1)[0].casefold()
                )
    return page_index if page_index is not None and 0 <= page_index < page_count else None


def parse_print_outline(
    output: str,
    page_count: int,
    component_pages: dict[str, int] | None = None,
) -> tuple[OutlineEntry, ...]:
    """Parse ``djvused print-outline`` data into zero-based nested bookmarks.

    ``component_pages`` maps DjVu component IDs or filenames to zero-based PDF
    page indices. Unresolvable or out-of-range targets remain as non-clickable
    outline entries so their titles and children are not lost.
    """
    if page_count <= 0:
        raise ValueError("Число страниц должно быть положительным.")
    tokens = _TOKEN_RE.findall(output)
    if not tokens:
        return ()
    roots = _parse_tokens(tokens)
    mappings = component_pages or {}

    def parse_entry(node: object) -> OutlineEntry | None:
        if (
            not isinstance(node, list)
            or len(node) < 2
            or not isinstance(node[0], str)
            or not isinstance(node[1], str)
        ):
            return None
        children = tuple(
            entry
            for item in node[2:]
            if (entry := parse_entry(item)) is not None
        )
        return OutlineEntry(
            node[0],
            _parse_page_target(node[1], page_count, mappings),
            children,
            node[1],
        )

    entries: list[OutlineEntry] = []
    for root in roots:
        if isinstance(root, list) and root and root[0] == "bookmarks":
            candidates = root[1:]
        else:
            candidates = [root]
        entries.extend(
            entry
            for item in candidates
            if (entry := parse_entry(item)) is not None
        )
    return tuple(entries)


def parse_print_txt(output: str) -> tuple[PageText, ...]:
    """Parse ``djvused -e 'print-txt'`` output into pages and word rectangles.

    Standard page forms contain a bounding rectangle after ``page``. Forms
    with just a page number or explicit width/height are also accepted. When
    dimensions are absent, ``width`` and ``height`` are ``None`` and renderer
    dimensions are used by the assembler.
    """
    tokens = _TOKEN_RE.findall(output)
    if not tokens:
        raise ValueError("djvused не вернул текстовых данных.")
    roots = _parse_tokens(tokens)
    page_forms = [
        node for root in roots for node in _walk(root)
        if node and node[0] == "page"
    ]
    if not page_forms:
        raise ValueError("В выводе djvused не найдены страницы.")

    pages: list[PageText] = []
    for ordinal, page_form in enumerate(page_forms, start=1):
        numeric_header: list[int] = []
        for item in page_form[1:]:
            if isinstance(item, list):
                break
            number = _as_int(item)
            if number is not None:
                numeric_header.append(number)

        width: int | None = None
        height: int | None = None
        origin_x = origin_y = 0
        if len(numeric_header) == 4:
            origin_x, origin_y, right, bottom = numeric_header
            width, height = right - origin_x, bottom - origin_y
        elif len(numeric_header) >= 5:
            origin_x, origin_y, right, bottom = numeric_header[-4:]
            width, height = right - origin_x, bottom - origin_y
        elif len(numeric_header) == 3:
            width, height = numeric_header[-2:]
        elif len(numeric_header) == 2:
            width, height = numeric_header
        if width is not None and (width <= 0 or height is None or height <= 0):
            width = height = None

        words: list[WordBox] = []
        for node in _walk(page_form):
            if not node or node[0] != "word" or len(node) < 6:
                continue
            coords = [_as_int(value) for value in node[1:5]]
            text = node[5]
            if all(value is not None for value in coords) and isinstance(text, str):
                x1, y1, x2, y2 = (int(value) for value in coords if value is not None)
                if x2 >= x1 and y2 >= y1:
                    words.append(WordBox(text=text, x1=x1, y1=y1, x2=x2, y2=y2))

        page_number = (
            numeric_header[0]
            if len(numeric_header) in (1, 3) or len(numeric_header) >= 5
            else ordinal
        )
        pages.append(
            PageText(page_number, width, height, tuple(words), origin_x, origin_y)
        )
    return tuple(pages)
