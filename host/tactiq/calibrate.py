"""Per-user calibration — build-plan step 11, mirroring the Calibration API.

Runs a short voice-guided capture (default 3 blocks x 8 contacts), fits a
per-user classifier, and scores calibration quality from the minimum
pairwise Mahalanobis D-squared — the section 2.6 criterion that the user's
own clusters are separable. Everything lands in API shape:

    calibration.json      POST /calibration/{id}/sample bodies + the
                          /complete response (calibrationQuality)
    calibration_model/    per-user model.joblib, centroids.json, metrics

Quality bands (min pairwise D2): >=25 excellent (5-sigma separation),
>=9 good (3-sigma), below that needs_more_data.

Usage:
    python -m tactiq.calibrate --participant P01           # real ring
    python -m tactiq.calibrate --sim --speed 25 --seed 5   # pipeline test
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from . import capture, classify, segment
from .contacts import BY_KEY

# raw flex counts -> degrees: placeholder linear map for the API's
# flexAngle field until the flex sensors are bench-calibrated
COUNTS_PER_DEGREE = 11.0

QUALITY_BANDS = [(25.0, "excellent"), (9.0, "good"), (0.0, "needs_more_data")]


def quality_from_d2(min_d2: float) -> str:
    return next(label for floor, label in QUALITY_BANDS if min_d2 >= floor)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--participant", default="P00")
    ap.add_argument("--hand", default="right", choices=["left", "right"])
    ap.add_argument("--reps", type=int, default=3,
                    help="calibration blocks per contact")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--port", default="auto")
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--no-audio", action="store_true")
    args = ap.parse_args(argv)

    cap_args = ["--participant", args.participant, "--hand", args.hand,
                "--reps", str(args.reps), "--condition", "seated",
                "--break-every", "0"]
    if args.seed is not None:
        cap_args += ["--seed", str(args.seed)]
    if args.sim:
        cap_args += ["--sim", "--speed", str(args.speed)]
    else:
        cap_args += ["--port", args.port]
    if args.no_audio:
        cap_args += ["--no-audio"]

    print(f"Calibration: {args.reps} samples per contact, voice-guided.")
    session_dir = capture.main(cap_args)
    segment.process_session(session_dir, quiet=True)

    df, simulated = classify.load_features([session_dir])
    model_dir = session_dir / "calibration_model"
    metrics = classify.fit_and_save(df, model_dir, "lda", simulated)

    quality = quality_from_d2(metrics["minPairwiseD2"])
    feats = pd.read_csv(session_dir / "features.csv")
    samples = []
    for f in feats.itertuples():
        c = BY_KEY[f.contactKey]
        samples.append({
            "gesture": {"hand": args.hand, "finger": c.finger,
                        "knuckle": c.knuckle, "tapCount": 1},
            "sensorData": {
                "pressure": round(max(f.fsrPeak1, f.fsrPeak2) / 4095, 2),
                "flexAngle": round(max(f.flexDelta1, f.flexDelta2)
                                   / COUNTS_PER_DEGREE, 1),
                "durationMs": round(f.pressDurMs),
            },
        })

    calibration = {
        "calibrationId": session_dir.name,
        "deviceId": "bench_prototype",
        "hand": args.hand,
        "mode": "voice_guided",
        "source": "simulated" if simulated else "device",
        "samples": samples,
        "complete": {
            "success": True,
            "message": "Calibration completed successfully.",
            "calibrationQuality": quality,
        },
        "minPairwiseD2": metrics["minPairwiseD2"],
    }
    (session_dir / "calibration.json").write_text(
        json.dumps(calibration, indent=2))

    print(f"Calibration quality: {quality.upper()} "
          f"(min pairwise D2 = {metrics['minPairwiseD2']})")
    print(f"Per-user model -> {model_dir}")
    print(f"API-shaped record -> {session_dir / 'calibration.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
