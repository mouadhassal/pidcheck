# Pre-registration: pidcheck on a held-out set of synthetic P&IDs

Written on 17 October 2026, **before the test set was generated or the checker was run on it**.
The code that decides the result is frozen by hash, and so is this file (its SHA-256 is recorded in the
commit that follows it). Anything changed after the test run is reported as an amendment, with both
results kept.

## What is frozen

- **Code hash:** `python tools/codehash.py` gives
  `cdc0a4a9119abe071d0d7ebb8f5e14447a155c6e99b95eea39c73183dc7380ce`.
  It covers the reader, the linker, the unit and tag logic, the comparison, the PDF renderer, the
  generator and this scorer.
- **Development set:** seeds 0–19 (`python synth/generate.py --set dev`). The checker was built and debugged
  on it only. Final dev result: 139 of 139 faults caught, 0 false alarms, on both DXF and PDF.
- **Test set:** seeds 1000–1039 (`python synth/generate.py --set test`), 40 drawings, each as DXF and as
  vector PDF.

## What the test set adds that development never saw

The checker has never run on any of these:

1. **Rotated labels:** about 20% of valves have vertical labels, rotated 90°.
2. **Crowded sheets:** about 35% of sheets. Equipment is spaced 30 units apart instead of 58, and on those
   sheets about half of the text-style valve labels drift halfway towards the next equipment tag. They are
   genuinely ambiguous.
3. **Multi-line text anchoring:** MTEXT is anchored middle-left and bottom-centre as well as top-left and
   top-centre.
4. **More decimal commas:** in 40% of metric drawings, against 25% in development.

## Scoring rules (implemented in bench/run.py)

**A planted fault counts as caught when:**
- **A value, size or rating was changed, or a unit slipped:** there is a `SPEC_MISMATCH` on the same tag
  and field.
- **An item is missing:** there is a `MISSING_ON_DRAWING` on that tag.
- **An extra item was added:** there is a `NOT_IN_LIST` on that tag.
- **A tag is duplicated on the drawing:** there is a `DUPLICATE_ON_DRAWING`.
- **A letter replaces a digit:** there is a `TAG_TYPO`.
- **A tag has transposed digits:** there is a `POSSIBLE_TYPO` naming both tags, or both halves are raised
  as errors (`NOT_IN_LIST` for the typo and `MISSING_ON_DRAWING` for the real tag).
- **A tag is duplicated in the list:** there is a `DUPLICATE_IN_LIST`.

**Caught for review:** an `AMBIGUOUS_LABEL` that names the tag counts here, which is weaker than caught.

**False alarm:** any error or warning that no planted fault explains.

## Hypotheses (test set)

| | Prediction | Pass if |
|---|---|---|
| H1 | DXF: caught / planted | ≥ 95% |
| H2 | PDF: caught / planted | ≥ 90% (a PDF has no block attributes, so every link is positional) |
| H3 | False alarms per drawing, DXF and PDF each | ≤ 0.25 (≤ 10 over 40 drawings) |
| H4 | Precision of high-confidence errors, DXF and PDF each | ≥ 98% |
| H5 | High-confidence `SPEC_MISMATCH` false alarms on crowded sheets | 0 (a crowded label must never be confidently pinned on the wrong tag) |
| H6 | `SPEC_MISMATCH` false alarms anywhere (any unit, DN/NPS, PN/Class, gauge/absolute or decimal-comma trap that gets flagged) | 0 |
| H7 | Recall gap between DXF and PDF | ≤ 5 points |

**Where I expect this to fail, written down in advance:**
- **Crowded sheets:** H3 and H5 are the riskiest. The 1.6× ambiguity margin and the 9-text-height radius
  were tuned on uncrowded sheets.
- **Rotated labels:** these could break the bubble merge, and the PDF centre estimate.

## Limits stated up front

- **The drawings are synthetic,** made by the same author as the checker. Passing here shows the logic is
  sound on the cases it was designed for. It does not show what happens on a real client's P&ID, where
  symbols, fonts, layers and drafting habits vary far more.
- **Scanned PDFs (images) need OCR.** They are out of scope.
- **Each field holds one value.** Design and operating values of the same kind on one item are not told
  apart yet.
