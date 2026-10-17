"""Generate one synthetic sheet, check its DXF and PDF, and compare with the planted faults."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bench.run import caught  # noqa: E402
from pidcheck.check import ERROR, WARNING, check  # noqa: E402
from synth.generate import make  # noqa: E402


@pytest.fixture(scope="module")
def sheet(tmp_path_factory):
    out = tmp_path_factory.mktemp("synth")
    truth = make(7, out, test=True)
    return out, truth


@pytest.mark.parametrize("fmt", ["dxf", "pdf"])
def test_every_planted_fault_is_surfaced_and_nothing_else(sheet, fmt):
    out, truth = sheet
    rep = check(out / f"{truth['name']}.{fmt}", out / f"{truth['name']}_equipment.xlsx")
    findings = [f for f in rep.findings if f.severity in (ERROR, WARNING)]
    explained = set()
    for fault in truth["faults"]:
        exact, review, idx = caught(fault, findings)
        assert review, f"missed {fault}"
        explained |= set(idx)
    involved = {f["tag"] for f in truth["faults"]} | {f.get("drawing") for f in truth["faults"]}
    for i, f in enumerate(findings):
        assert i in explained or ({f.tag, *f.related, f.drawing} & involved), f"false alarm: {f.message}"


def test_cli_exit_code_and_report(sheet, tmp_path):
    from pidcheck.__main__ import main
    out, truth = sheet
    code = main([str(out / f"{truth['name']}.dxf"), str(out / f"{truth['name']}_equipment.xlsx"),
                 "--out", str(tmp_path)])
    assert code == 1                                     # planted faults are errors
    data = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    assert data["findings"] and (tmp_path / "report.html").stat().st_size > 10_000
