"""Prompted capture harness — build-plan step 2, implementing Table 4 row 1.

Runs the pre-registered per-contact discrimination protocol: each of the 8
contacts prompted `--reps` times (default 20) in randomised order, as
shuffled blocks so every block contains each contact exactly once. Audio
prompts announce the contact; the full raw stream plus per-trial labels and
timing land in one session directory:

    imu.csv      continuous IMU stream        (t_us, ax_g..gz_dps)
    analog.csv   continuous flex/FSR stream   (t_us, flex1..fsr2)
    trials.csv   one row per prompted trial   (label + response window)
    session.json protocol config, seed, source, device info

Raw data is stored continuously (not pre-cut windows) so segmentation
choices can be revisited later without re-running participants.

Usage:
    python -m tactiq.capture --participant P01 --condition seated
    python -m tactiq.capture --sim --speed 25 --reps 2   # no hardware needed
"""

import argparse
import csv
import datetime as dt
import json
import random
import subprocess
import sys
import time
from pathlib import Path

from .contacts import CONTACTS, CONDITIONS
from .stream import ImuSample, AnalogSample, SerialSource, SimSource

SCHEMA_VERSION = 1
RESPONSE_WINDOW_MS = 2500  # time allowed for the tap after the prompt
INTER_TRIAL_MS = 400


class SessionWriter:
    def __init__(self, out_dir: Path):
        self.dir = out_dir
        self.dir.mkdir(parents=True, exist_ok=False)
        self._imu_f = open(self.dir / "imu.csv", "w", newline="")
        self._imu = csv.writer(self._imu_f)
        self._imu.writerow(["t_us", "ax_g", "ay_g", "az_g",
                            "gx_dps", "gy_dps", "gz_dps"])
        self._adc_f = open(self.dir / "analog.csv", "w", newline="")
        self._adc = csv.writer(self._adc_f)
        self._adc.writerow(["t_us", "flex1", "flex2", "fsr1", "fsr2"])
        self._trials_f = open(self.dir / "trials.csv", "w", newline="")
        self._trials = csv.writer(self._trials_f)
        self._trials.writerow(["trialIndex", "block", "contactKey", "finger",
                               "knuckle", "promptAtUs", "windowEndUs"])
        self.imu_count = 0
        self.adc_count = 0

    def write_samples(self, samples):
        for s in samples:
            if isinstance(s, ImuSample):
                self._imu.writerow(s)
                self.imu_count += 1
            elif isinstance(s, AnalogSample):
                self._adc.writerow(s)
                self.adc_count += 1

    def write_trial(self, index, block, contact, prompt_us, window_end_us):
        self._trials.writerow([index, block, contact.key, contact.finger,
                               contact.knuckle, prompt_us, window_end_us])
        self._trials_f.flush()

    def close(self):
        for f in (self._imu_f, self._adc_f, self._trials_f):
            f.close()


def build_schedule(reps: int, rng: random.Random):
    """`reps` shuffled blocks of all 8 contacts: balanced and randomised."""
    schedule = []
    prev_last = None
    for block in range(reps):
        order = CONTACTS[:]
        rng.shuffle(order)
        while order[0] is prev_last:  # avoid back-to-back repeats at seams
            rng.shuffle(order)
        prev_last = order[-1]
        schedule.append(order)
    return schedule


def speak(text: str, enabled: bool):
    print(f"  >> Touch: {text}", flush=True)
    if enabled:
        subprocess.run(["say", text], check=False)


def pump_until(source, writer, target_us: int):
    while source.now_us() < target_us:
        samples = source.read()
        if samples:
            writer.write_samples(samples)
        else:
            time.sleep(0.004)


def preflight(source, writer, warmup_ms: int = 2000):
    """Verify both streams are flowing and report their rates."""
    print("Waiting for sample stream...", flush=True)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if source.read():
            break
        time.sleep(0.02)
    else:
        raise RuntimeError("No samples within 10 s — check the USB cable "
                           "and that the firmware is flashed.")
    t0 = source.now_us()
    i0, a0 = writer.imu_count, writer.adc_count
    pump_until(source, writer, t0 + warmup_ms * 1000)
    span_s = (source.now_us() - t0) / 1e6
    imu_hz = (writer.imu_count - i0) / span_s
    adc_hz = (writer.adc_count - a0) / span_s
    print(f"Stream OK: IMU {imu_hz:.0f} Hz, analog {adc_hz:.0f} Hz")
    if imu_hz < 150 or adc_hz < 70:
        print("WARNING: sample rates below expected (208/100 Hz)")
    return {"imuHz": round(imu_hz, 1), "analogHz": round(adc_hz, 1)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--participant", default="P00")
    ap.add_argument("--hand", default="right", choices=["left", "right"])
    ap.add_argument("--condition", default="seated", choices=CONDITIONS)
    ap.add_argument("--reps", type=int, default=20,
                    help="blocks of 8 contacts (Table 4: 20)")
    ap.add_argument("--seed", type=int, default=None,
                    help="schedule RNG seed (default: random, recorded)")
    ap.add_argument("--port", default="auto")
    ap.add_argument("--sim", action="store_true",
                    help="synthetic source — run the pipeline with no hardware")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="sim clock multiplier (sim only)")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--break-every", type=int, default=4,
                    help="pause for a rest every N blocks (0 = never)")
    ap.add_argument("--out", default=None,
                    help="sessions root (default: <repo>/host/data/sessions)")
    args = ap.parse_args(argv)

    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    rng = random.Random(seed)
    schedule = build_schedule(args.reps, rng)

    audio = (not args.no_audio) and (not args.sim) and sys.platform == "darwin"

    out_root = Path(args.out) if args.out else \
        Path(__file__).resolve().parent.parent / "data" / "sessions"
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = "_sim" if args.sim else ""
    session_dir = out_root / f"{stamp}_{args.participant}_{args.condition}{tag}"

    source = SimSource(speed=args.speed, seed=seed) if args.sim \
        else SerialSource(port=args.port)
    writer = SessionWriter(session_dir)

    session = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "gesture_capture",
        "createdAt": dt.datetime.now().astimezone().isoformat(),
        "participant": args.participant,
        "hand": args.hand,
        "condition": args.condition,
        "source": "simulated" if args.sim else "device",
        "seed": seed,
        "reps": args.reps,
        "responseWindowMs": RESPONSE_WINDOW_MS,
        "contacts": [c.key for c in CONTACTS],
        "port": None if args.sim else source.port,
    }
    (session_dir / "session.json").write_text(json.dumps(session, indent=2))

    print(f"Session: {session_dir}")
    print(f"Protocol: {args.reps} blocks x 8 contacts = "
          f"{args.reps * 8} trials, seed {seed}, condition {args.condition}")
    if args.sim:
        print("NOTE: SIMULATED data — pipeline testing only, not results.")

    trial_index = 0
    aborted = False
    try:
        session["stream"] = preflight(source, writer)
        for block, order in enumerate(schedule):
            if (args.break_every and block and not args.sim
                    and block % args.break_every == 0):
                input(f"--- Break after block {block}. Enter to continue ---")
            for contact in order:
                print(f"[block {block + 1}/{args.reps} "
                      f"trial {trial_index + 1}/{args.reps * 8}]", end="")
                speak(contact.spoken, audio)
                prompt_us = source.now_us()
                if args.sim:
                    delay = min(max(rng.gauss(0.7, 0.15), 0.25), 1.5)
                    source.expect_tap(contact, prompt_us + int(delay * 1e6))
                window_end = prompt_us + RESPONSE_WINDOW_MS * 1000
                pump_until(source, writer, window_end)
                writer.write_trial(trial_index, block, contact,
                                   prompt_us, window_end)
                trial_index += 1
                pump_until(source, writer,
                           source.now_us() + INTER_TRIAL_MS * 1000)
    except KeyboardInterrupt:
        aborted = True
        print("\nInterrupted — finalising partial session.")
    finally:
        writer.close()
        source.close()
        session["completedTrials"] = trial_index
        session["aborted"] = aborted
        session["endedAt"] = dt.datetime.now().astimezone().isoformat()
        session["parseErrors"] = source.parse_errors
        session["deviceHeader"] = source.header_lines[:10]
        (session_dir / "session.json").write_text(
            json.dumps(session, indent=2))

    print(f"Done: {trial_index} trials, {writer.imu_count} IMU + "
          f"{writer.adc_count} analog samples -> {session_dir}")
    return session_dir


if __name__ == "__main__":
    main()
