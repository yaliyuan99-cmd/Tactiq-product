"""Export a segmented session as website-API-shaped JSON.

Produces the request bodies the Tactiq site's Gesture Testing API expects
(POST /diagnostics/gesture-test/{testId}/record), so bench data can populate
the website's diagnostics pages without translation. Until the step-5
classifier exists, `detectedGesture` carries the *prompted* contact and
`confidence` is null — the shape is real, the recognition is not yet.

Usage:
    python -m tactiq.export_api host/data/sessions/<session_dir>
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from .contacts import BY_KEY


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session_dir", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="output file (default: <session>/gesture_test.json)")
    args = ap.parse_args(argv)

    d = args.session_dir
    session = json.loads((d / "session.json").read_text())
    feats = pd.read_csv(d / "features.csv")

    records = []
    for f in feats.itertuples():
        c = BY_KEY[f.contactKey]
        pressure = max(f.fsrPeak1, f.fsrPeak2) / 4095  # raw counts -> 0..1
        records.append({
            "detectedGesture": {
                "hand": session["hand"],
                "finger": c.finger,
                "knuckle": c.knuckle,
                "tapCount": 1,
                "pressure": round(max(0.0, pressure), 2),
                "confidence": None,  # populated once the classifier exists
            }
        })

    body = {
        "testId": d.name,
        "hand": session["hand"],
        "source": session["source"],
        "records": records,
    }
    out = args.out or d / "gesture_test.json"
    out.write_text(json.dumps(body, indent=2))
    print(f"Wrote {len(records)} records -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
