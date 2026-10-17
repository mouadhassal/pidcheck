import pytest

from pidcheck.units import compare, parse_number, parse_quantities


def q(text):
    qs = parse_quantities(text)
    assert qs, f"nothing parsed from {text!r}"
    return qs[0]


@pytest.mark.parametrize("a,b", [
    ("150 GPM", "34.1 m³/h"),        # unit conversion within display rounding
    ("150 GPM", "34,1 m3/h"),        # decimal comma
    ("420.0 m^3/hr", "420 m³/h"),    # exporter spelling
    ("DN80", '3"'),                  # nominal size: DN80 is NPS 3, although 3 in = 76.2 mm
    ("DN 80", "NPS 3"),
    ("1-1/2\"", "DN40"),
    ("150#", "CLASS 150"),           # flange rating spellings
    ("CL300", "PN50"),               # EN 1759 correspondence
    ("10.0 barg", "11.0 bara"),      # gauge vs absolute
    ("65 °C", "149 °F"),
    ("100.00 C", "100 °C"),          # bare C as written by some CAD exporters
    ("75.0 kW", "101 hp"),
])
def test_equal_despite_different_writing(a, b):
    assert compare(q(a), q(b)).equal


@pytest.mark.parametrize("a,b", [
    ("150 GPM", "36.3 m³/h"),        # 160 GPM: a real change
    ("150 GPM", "150 m³/h"),         # unit slip: same number, wrong unit
    ("DN80", '4"'),
    ("150#", "CLASS 300"),
    ("10.0 barg", "10.0 bara"),      # 1 atm apart
    ("-1.0 barg", "1.0 barg"),       # a vacuum design pressure is not a positive one
])
def test_real_differences(a, b):
    assert not compare(q(a), q(b)).equal


def test_unmarked_pressure_is_an_assumption():
    c = compare(q("10.0 bar"), q("10.0 barg"))
    assert c.equal and "assumed gauge" in c.assumption


@pytest.mark.parametrize("text,value,half", [
    ("1,100", 1100, 0.5), ("1.100,5", 1100.5, 0.05), ("34,1", 34.1, 0.05), ("0,150", 0.15, 0.0005),
])
def test_number_formats(text, value, half):
    v, h = parse_number(text)
    assert v == pytest.approx(value) and h == pytest.approx(half)


def test_several_quantities_in_one_label():
    kinds = [x.kind for x in parse_quantities('CV-102 3" 150#')]
    assert kinds == ["size", "rating"]


def test_ranges_are_not_negative():
    assert q("0-200 GPM").si > 0
