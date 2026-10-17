import pytest

from pidcheck.associate import build
from pidcheck.extract import Fragment
from pidcheck.tags import likely_same, parse_tag, tag_at_start


@pytest.mark.parametrize("text,canonical", [
    ("P-101", "P-101"), ("P101", "P-101"), ("P 101", "P-101"), ("P–101", "P-101"), ("HV·111", "HV-111"),
    ("FT-101A", "FT-101A"), ("TICSA 4750.03", "TICSA-4750.03"), ("P4711", "P-4711"), ("H 1008", "H-1008"),
])
def test_tag_spellings(text, canonical):
    assert parse_tag(text).canonical == canonical


@pytest.mark.parametrize("text", ["CLASS 150", "DN 80", "NOTE 1", "Q80", "DEXPI2013", "MNb 47122", "REV 2"])
def test_not_tags(text):
    assert parse_tag(text) is None


def test_confusable_letter_is_flagged():
    t = parse_tag("P-1O1")
    assert t.canonical == "P-101" and t.typo_fixed


def test_tag_at_start_of_label():
    tag, end = tag_at_start('CV-102 3" 150#')
    assert tag.canonical == "CV-102" and 'CV-102 3" 150#'[end:].strip() == '3" 150#'


def test_typo_pairing():
    assert likely_same("HV-121", "HV-112")      # transposed digits
    assert likely_same("P-102", "P-101")        # one digit off
    assert not likely_same("P-101", "E-101")    # different item type


def F(text, x, y, h=2.5, **kw):
    return Fragment(text, x, y, h, kw.pop("source", "TEXT"), **kw)


def test_proximity_link():
    d = build([F("P-101", 0, 0), F("150 GPM", 0, -4), F("P-102", 60, 0), F("80 GPM", 60, -4)])
    assert [s.quantity.text for s in d.specs["P-101"]] == ["150 GPM"]
    assert [s.quantity.text for s in d.specs["P-102"]] == ["80 GPM"]


def test_label_between_two_tags_is_not_guessed():
    d = build([F("P-101", 0, 0), F("P-102", 20, 0), F("150 GPM", 10, -4)])
    assert not d.specs and len(d.ambiguous) == 1
    assert {t for t, _ in d.ambiguous[0].candidates} == {"P-101", "P-102"}


def test_block_attributes_link_exactly():
    d = build([F("P-101", 0, 0, source="ATTRIB", group="A1", field_name="TAG"),
               F("150 GPM", 500, 500, source="ATTRIB", group="A1", field_name="FLOW")])
    assert d.specs["P-101"][0].link == "attribute"


def test_instrument_bubble_is_merged():
    d = build([F("FT", 0, 1.3, h=1.8), F("101", 0, -1.4, h=1.8)])
    assert [o.tag.canonical for o in d.occurrences] == ["FT-101"]


def test_pipe_line_label_is_not_a_tag():
    d = build([F("M 48122 75HB 80", 0, 0)])
    assert not d.occurrences
