"""Recognise and normalise equipment and instrument tags (ISA-5.1 style: P-101, FT-101, PSV-401A)."""
from __future__ import annotations

import re
from dataclasses import dataclass

# letters, an optional separator, a loop number, an optional suffix. Separators: hyphen, the Unicode
# dashes, middle dot and bullet (PDF fonts without an en dash often substitute one), underscore, space.
SEP = "\\-\u2010-\u2015\u00b7\u2022_"
TAG = re.compile(r"^(?P<letters>[A-Z]{1,4})\s*[" + SEP + r" ]?\s*(?P<number>[0-9OIl]{2,5})(?P<suffix>[A-Z]{0,2})$")
# a tag at the start of a longer label ("CV-102 3\" 150#", "CV102 ...", "PSV-I41 ..."): the separator is
# optional, and the loop number may contain a confusable letter as long as it has a digit
TAG_IN_TEXT = re.compile(r"\b(?P<letters>[A-Z]{1,4})\s?[" + SEP + r"]?\s?(?=[0-9OI]*[0-9])(?P<number>[0-9OI]{2,5})"
                         r"(?P<suffix>[A-Z]?)(?=\s|$)")
LETTERS_ONLY = re.compile(r"^[A-Z]{1,4}$")
DIGITS_ONLY = re.compile(r"^[0-9]{2,5}[A-Z]?$")
# confusable characters that OCR, fonts and tired drafters swap
CONFUSABLE = str.maketrans({"O": "0", "I": "1", "l": "1"})
NOT_TAGS = {"NOTE", "REV", "DWG", "SHT", "NO", "PN", "DN", "CL", "NPS"}


@dataclass(frozen=True)
class Tag:
    canonical: str   # P-101
    written: str     # as on the drawing
    typo_fixed: bool = False  # a letter O / I was read as a digit

    @property
    def letters(self) -> str:
        return self.canonical.split("-")[0]


def parse_tag(text: str) -> Tag | None:
    s = text.strip().upper() if not re.search(r"[0-9]l|l[0-9]", text) else text.strip()
    m = TAG.match(s)
    if not m or m.group("letters") in NOT_TAGS:
        return None
    number = m.group("number")
    fixed = number.translate(CONFUSABLE)
    if not fixed.isdigit():
        return None
    return Tag(f"{m.group('letters')}-{fixed}{m.group('suffix')}", text.strip(), fixed != number)


def written_differently(tag: Tag) -> bool:
    """True when the tag is written with a non-standard separator or none (P101, P–101, P 101)."""
    return tag.written.upper() != tag.canonical and not tag.typo_fixed


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def is_transposition(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


def likely_same(drawing: str, listed: str) -> bool:
    """A drawing tag missing from the list that is probably a mistyped list tag."""
    da, la = drawing.split("-", 1), listed.split("-", 1)
    if da[0] != la[0]:
        return False
    return edit_distance(da[1], la[1]) == 1 or is_transposition(da[1], la[1])
