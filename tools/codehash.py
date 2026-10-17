"""SHA-256 over the files that decide the benchmark result (line endings normalised).

    python tools/codehash.py
"""
import hashlib
from pathlib import Path

FILES = ["pidcheck/units.py", "pidcheck/tags.py", "pidcheck/extract.py", "pidcheck/associate.py",
         "pidcheck/equiplist.py", "pidcheck/check.py", "pidcheck/render.py", "synth/generate.py", "bench/run.py"]

root = Path(__file__).resolve().parents[1]
h = hashlib.sha256()
for f in FILES:
    b = (root / f).read_bytes().replace(b"\r\n", b"\n")
    h.update(f.encode() + b"\0" + b)
print(h.hexdigest())
