"""Idle-wear session recorder — the data for Table 4's false-activation test.

Records a long stretch of "ordinary activity" (no prompted gestures) plus a
known schedule of deliberate wake squeezes, so the gate sweep can measure
both false activations per hour AND the gate's true-positive rate p_g
(equation 4's first factor) from one session.

Sim mode generates the stream in batch (hours of virtual wear in seconds of
wall time): incidental pressure noise with exponentially distributed
durations — the section 3.8 model — plus injected deliberate squeezes.
Real mode records from the ring and speaks "squeeze now" at the scheduled
times.

Usage:
    python -m tactiq.idlewear --sim --minutes 120 --seed 11
    python -m tactiq.idlewear --minutes 60 --participant P01
"""

import argparse
import csv
import datetime as dt
import json
import random
import sys
import time
from pathlib import Path

from .capture import SessionWriter, pump_until, preflight, speak
from .stream import SerialSource, SimSource

SCHEMA_VERSION = 1


def squeeze_schedule(minutes: float, count: int, rng: random.Random):
    """Deliberate squeezes, evenly spread with jitter, away from the ends."""
    if count <= 0:
        return []
    usable = minutes * 60 - 120
    gap = usable / count
    return sorted(60 + i * gap + rng.uniform(0.1, 0.9) * gap * 0.8
                  for i in range(count))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--deliberate", type=int, default=20,
                    help="scheduled wake squeezes for measuring p_g")
    ap.add_argument("--participant", default="P00")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--port", default="auto")
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--idle-rate", type=float, default=240.0,
                    help="sim: incidental pressure events per hour")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    rng = random.Random(seed)
    squeezes_s = squeeze_schedule(args.minutes, args.deliberate, rng)

    out_root = Path(args.out) if args.out else \
        Path(__file__).resolve().parent.parent / "data" / "sessions"
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = "_sim" if args.sim else ""
    session_dir = out_root / f"{stamp}_{args.participant}_idle{tag}"

    writer = SessionWriter(session_dir)
    end_us = int(args.minutes * 60 * 1e6)

    print(f"Idle-wear session: {args.minutes:g} min, "
          f"{args.deliberate} deliberate squeezes, seed {seed}")
    if args.sim:
        print("NOTE: SIMULATED data — pipeline testing only, not results.")
        source = SimSource(seed=seed)
        source.enable_idle_noise(events_per_hour=args.idle_rate)
        for t_s in squeezes_s:
            source.expect_squeeze(int(t_s * 1e6))
        done_note = time.monotonic()
        for chunk in source.generate_until(end_us):
            writer.write_samples(chunk)
            if time.monotonic() - done_note > 5:
                done_note = time.monotonic()
                pct = 100 * min(source._next_imu, end_us) / end_us
                print(f"  generating... {pct:.0f}%", flush=True)
    else:
        source = SerialSource(port=args.port)
        audio = not args.no_audio and sys.platform == "darwin"
        preflight(source, writer)
        t0 = source.now_us()
        next_squeeze = 0
        print("Recording idle wear — go about ordinary activity.")
        while source.now_us() - t0 < end_us:
            if next_squeeze < len(squeezes_s) and \
                    source.now_us() - t0 >= squeezes_s[next_squeeze] * 1e6:
                speak("squeeze now", audio)
                squeezes_s[next_squeeze] = (source.now_us() - t0) / 1e6
                next_squeeze += 1
            pump_until(source, writer, source.now_us() + 500_000)

    writer.close()
    source.close()

    with open(session_dir / "squeezes.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["atUs"])
        for t_s in squeezes_s:
            w.writerow([int(t_s * 1e6)])

    session = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "idle_wear",
        "createdAt": dt.datetime.now().astimezone().isoformat(),
        "participant": args.participant,
        "source": "simulated" if args.sim else "device",
        "seed": seed,
        "minutes": args.minutes,
        "deliberateSqueezes": args.deliberate,
        "idleRatePerHour": args.idle_rate if args.sim else None,
    }
    (session_dir / "session.json").write_text(json.dumps(session, indent=2))
    print(f"Done: {writer.imu_count} IMU + {writer.adc_count} analog "
          f"samples -> {session_dir}")
    return session_dir


if __name__ == "__main__":
    main()
