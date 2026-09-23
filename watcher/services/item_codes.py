"""
W-036: one canonical storage format for 8-K item codes.

Passing a tuple to a TextField makes Django store str(tuple),
i.e. "('1.01', '9.01')". Every writer must use format_item_codes()
so the database always holds "1.01;9.01".

Values are kept as supplied (trimmed, de-duplicated, order kept).
Nothing is dropped or invented.
"""

import re

ITEM_CODE_SEPARATOR = ";"

_SPLIT_RE = re.compile(r"[;,]")
_STRIP_CHARS = " \t\r\n'\"()[]"


def _split_text(text):
    return [part.strip(_STRIP_CHARS) for part in _SPLIT_RE.split(text)]


def parse_item_codes(value):
    """Return a tuple of item codes from any supported input."""
    if value is None:
        return ()

    if isinstance(value, str):
        parts = _split_text(value)
    else:
        parts = []
        for item in value:
            if item is None:
                continue
            parts.extend(_split_text(str(item)))

    result = []
    seen = set()
    for part in parts:
        if not part or part in seen:
            continue
        seen.add(part)
        result.append(part)
    return tuple(result)


def format_item_codes(value):
    """Return the stored form, e.g. "1.01;9.01" ("" if none)."""
    return ITEM_CODE_SEPARATOR.join(parse_item_codes(value))