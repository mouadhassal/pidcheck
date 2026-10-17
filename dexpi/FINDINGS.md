# pidcheck on the public DEXPI test cases

[DEXPI](https://dexpi.org) is the industry standard for exchanging P&IDs between CAD tools (ISO 15926). Its
public test cases ([github.com/DEXPI/TestCases](https://github.com/DEXPI/TestCases)) contain:
- the same small plants exported to XML by several vendors' tools;
- PDFs drawn by those tools, either from their own model or after importing another tool's XML.

These are real CAD outputs, not my synthetic drawings. `python dexpi/study.py` downloads them and runs
pidcheck with the XML as the reference list. Results are in `results/dexpi.json`, checked 17 Oct 2026.
Nothing from the test cases is redistributed in this repository.

## Same tool: 26 pairs (the tool's own XML vs the tool's own drawing)

Here the drawing and the data come from one model, so every finding is either a defect in the test case
or a false alarm by pidcheck. **22 of the 26 pairs come back clean.** I looked at each finding on the other
4 by hand:

| Pair | pidcheck says | What the drawing and the XML show | Verdict |
|---|---|---|---|
| C03 (SAG) | V-001 listed twice, and drawn twice | The XML gives the tag **V001 to two different valves** (a ball valve and a butterfly valve), and the drawing labels both "V001" | **Real defect** in the test case |
| C05 (SAG) | V-001, V-002, V-003 in the list but not on the drawing | The XML tags three valves; the drawing shows the three valve symbols **with no tag text** (checked visually) | **Real difference** (the valves are unlabelled) |
| E07 (SAG) | S-3211, S-3212 on the drawing but not in the list | The drawing labels the packed bed and tray sections "S3211" and "S3212"; the XML names the column sections 01–05 under PV321 | **Real naming mismatch** between the sheet and the data |
| I06 (SAG) | M-001 (actuator) in the list but not on the drawing | The XML has an actuator tagged M001 at plant level (in I05 the same actuator is nested inside its valve); the drawing does not label it | **Real difference** (minor) |

**On these same-tool pairs I found no false alarm from pidcheck.**

All 62 values that appear as text on these sheets agree with the XML after unit handling. Examples:
- "420.0 m^3/hr" against `MetreCubedPerHour`;
- "100.00 C";
- design pressures such as "-1.0 barg";
- "P = 6.0 barg / DN = 25/50" on a safety valve.

Most of these tools do not print equipment data on the sheet, so there are few values to compare.

## Round trip: 10 pairs (tool A's XML, imported into tool B, drawn by tool B)

**No round-trip pair comes back clean.** Every finding points at the same importer behaviour, which is
what these round-trip test cases exist to reveal. I checked C01 (IM01) visually; the other pairs are listed
as pidcheck reports them.

- **The importer renames equipment** with its own numbering: P-001, W-001, W-002, R-001, B-003, P-004 and so
  on, instead of the source tags P4711, H1008 and so on.
- **Tag labels go missing:** P4712 in C01 (IM01) and SV 104.01 in C01, C02 and C03 are drawn without their
  tag, and TA-113 and V001–V003 in C05.
- **Equipment data boxes are empty:** they read "/ / bar" and "/ / m³/h", and some labels read "???" (seen
  on the C01 import). As a result, no values could be compared on any imported sheet.

## What the real files taught the checker (v3)

The first run on these files produced false alarms that the synthetic benchmark never could. These fixes
were made after seeing the real files, and every synthetic set was re-run afterwards with no change in
results (`results/*_v3.json`):

- **European tag styles:** "P4711", "H 1008", "TICSA 4750.03", "HV 4750.01" (no separator or a space,
  dotted loop numbers, five-letter instrument functions). Lowercase fluid codes ("MNb 47122") and pipe line
  labels ("M 48122 75HB 80") are not tags.
- **Data-table copies:** a tag repeated on the same row as an "Ident" or "Name" header is the item's data
  table, not a duplicate. A label drawn twice on the same spot counts once.
- **IEC 81346 designations:** some exporters keep the loop number in `FullLabel`, so "=A10.A20.C05.A80.100.LT"
  becomes LT-100. Tags of sub-components (an actuator inside a valve) belong to their owner.
- **Units:** "m^3/hr", "m^2", "100.00 C", negative pressures, and "DN = 25".
- **Several design values for one field** (max and min, chamber 1 and 2) are not a conflict when the list
  holds several too.

## Limits

- **Small sample.** Each test case is one small sheet, and these are demonstration plants, not a client's
  production P&IDs.
- **"Real" is my judgement.** Each finding was classified by me looking at the drawing and the XML.
- **pidcheck reads text only.** It cannot see symbols, so an unlabelled valve is "missing" to it, exactly
  as it would be to anyone reading the sheet's text.
