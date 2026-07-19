"""Contact-onset segmentation — build-plan step 3.

Implements the paper's section 3.2 note that the inertial sensor "marks
contact onset": peak detection on high-passed accelerometer magnitude
(scipy.signal.find_peaks with a height threshold and a debounce distance),
then a fixed ~200 ms window around each onset.

Reads a capture session directory, associates detected onsets with prompted
trials, and writes:

    events.csv          every detected onset (matched to a trial or not)
    windows_imu.csv     per-trial IMU window, long format (tRelUs from onset)
    windows_analog.csv  same for flex/FSR channels
    features.csv        the section-2.6 feature vector per matched trial
                        (d = 8), consumed by the step-5 classifier
    segment_summary.json  hit/miss counts per contact

Usage:
    python -m tactiq.segment host/data/sessions/<session_dir>
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

PRE_MS = 50    # window extends 50 ms before onset...
POST_MS = 150  # ...and 150 ms after: the ~200 ms of section 2.6
PRESS_DELTA = 150   # counts above rest that count as "pad pressed"
PRESS_CAP_MS = 6000


def detect_onsets(imu: pd.DataFrame, threshold_g: float, min_gap_ms: float):
    """Return (events_df, fs_hz): tap onsets from |accel| transients."""
    t = imu["t_us"].to_numpy()
    fs = 1e6 / np.median(np.diff(t))
    mag = np.sqrt(imu["ax_g"] ** 2 + imu["ay_g"] ** 2 + imu["az_g"] ** 2)
    # remove gravity/posture with a ~0.5 s rolling median
    win = max(3, int(fs * 0.5) | 1)
    baseline = mag.rolling(win, center=True, min_periods=1).median()
    hp = (mag - baseline).to_numpy()

    peaks, props = find_peaks(hp, height=threshold_g,
                              distance=max(1, int(fs * min_gap_ms / 1000)))
    onsets = []
    for pk, height in zip(peaks, props["peak_heights"]):
        i = pk  # walk back to where the transient leaves the noise floor
        floor = max(0.2 * height, threshold_g * 0.3)
        limit = max(0, pk - int(fs * 0.030))
        while i > limit and hp[i - 1] > floor:
            i -= 1
        onsets.append({"onsetUs": int(t[i]), "peakUs": int(t[pk]),
                       "peakG": round(float(height), 3)})
    return pd.DataFrame(onsets), fs


def match_trials(trials: pd.DataFrame, events: pd.DataFrame):
    """First onset inside each trial's response window is its tap."""
    events = events.copy()
    events["trialIndex"] = -1  # -1 = spontaneous / outside any window
    rows = []
    for tr in trials.itertuples():
        if len(events):
            mask = ((events["onsetUs"] >= tr.promptAtUs) &
                    (events["onsetUs"] <= tr.windowEndUs) &
                    (events["trialIndex"] == -1))
            hits = events.index[mask]
        else:
            hits = []
        row = {"trialIndex": tr.trialIndex, "contactKey": tr.contactKey,
               "matched": len(hits) > 0, "extraOnsets": max(0, len(hits) - 1)}
        if len(hits) > 0:
            events.loc[hits, "trialIndex"] = tr.trialIndex
            row["onsetUs"] = int(events.loc[hits[0], "onsetUs"])
            row["latencyMs"] = round(
                (row["onsetUs"] - tr.promptAtUs) / 1000, 1)
        rows.append(row)
    return pd.DataFrame(rows), events


def cut_windows(stream: pd.DataFrame, matches: pd.DataFrame):
    out = []
    for m in matches.itertuples():
        if not m.matched:
            continue
        lo, hi = m.onsetUs - PRE_MS * 1000, m.onsetUs + POST_MS * 1000
        w = stream[(stream["t_us"] >= lo) & (stream["t_us"] <= hi)].copy()
        w.insert(0, "trialIndex", m.trialIndex)
        w.insert(1, "contactKey", m.contactKey)
        w["tRelUs"] = w["t_us"] - m.onsetUs
        out.append(w.drop(columns=["t_us"]))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def press_duration_ms(analog: pd.DataFrame, onset_us: int,
                      rest: tuple) -> float:
    """How long either pad stays pressed from onset — the grammar's clock.

    Measured on the full analog stream (not the 200 ms window) so the 5 s
    emergency hold is measurable.
    """
    seg = analog[(analog["t_us"] >= onset_us - 30_000) &
                 (analog["t_us"] <= onset_us + PRESS_CAP_MS * 1000)]
    pressed = ((seg["fsr1"] - rest[0] > PRESS_DELTA) |
               (seg["fsr2"] - rest[1] > PRESS_DELTA)).to_numpy()
    t = seg["t_us"].to_numpy()
    start = None
    last_pressed = None
    for i in range(len(pressed)):
        if pressed[i]:
            if start is None:
                start = t[i]
            last_pressed = t[i]
        elif start is not None and t[i] - last_pressed > 100_000:
            break  # released for >100 ms: press is over
    if start is None:
        return 0.0
    return round((last_pressed - start) / 1000, 1)


def extract_features(w_imu, w_adc, analog, matches) -> pd.DataFrame:
    """The section-2.6 feature vector, one row per matched trial (d = 8)."""
    rest_a = analog.head(200)
    rest_flex = (rest_a["flex1"].median(), rest_a["flex2"].median())
    rest_fsr = (rest_a["fsr1"].median(), rest_a["fsr2"].median())
    onset_by_trial = {m.trialIndex: m.onsetUs
                      for m in matches.itertuples() if m.matched}
    rows = []
    for (idx, key), gi in w_imu.groupby(["trialIndex", "contactKey"]):
        mag = np.sqrt(gi["ax_g"] ** 2 + gi["ay_g"] ** 2 + gi["az_g"] ** 2)
        ga = w_adc[w_adc["trialIndex"] == idx]
        rows.append({
            "trialIndex": idx, "contactKey": key,
            "peakAccelG": round(float(mag.max()), 3),
            "accelRmsG": round(float(np.sqrt(((mag - 1.0) ** 2).mean())), 4),
            "gyroPeakDps": round(float(gi[["gx_dps", "gy_dps", "gz_dps"]]
                                       .abs().to_numpy().max()), 1),
            "flexDelta1": round(float(ga["flex1"].max() - rest_flex[0]), 1)
            if len(ga) else np.nan,
            "flexDelta2": round(float(ga["flex2"].max() - rest_flex[1]), 1)
            if len(ga) else np.nan,
            "fsrPeak1": int(ga["fsr1"].max()) if len(ga) else -1,
            "fsrPeak2": int(ga["fsr2"].max()) if len(ga) else -1,
            "pressDurMs": press_duration_ms(analog, onset_by_trial[idx],
                                            rest_fsr),
        })
    return pd.DataFrame(rows).sort_values("trialIndex")


def process_session(session_dir: Path, threshold: float = 0.35,
                    min_gap_ms: float = 150, quiet: bool = False) -> dict:
    d = Path(session_dir)
    session = json.loads((d / "session.json").read_text())
    imu = pd.read_csv(d / "imu.csv")
    analog = pd.read_csv(d / "analog.csv")
    trials = pd.read_csv(d / "trials.csv")

    if session.get("source") == "simulated" and not quiet:
        print("NOTE: SIMULATED session — pipeline testing only, not results.")

    events, fs = detect_onsets(imu, threshold, min_gap_ms)
    matches, events = match_trials(trials, events)

    w_imu = cut_windows(imu, matches)
    w_adc = cut_windows(analog, matches)
    events.to_csv(d / "events.csv", index=False)
    w_imu.to_csv(d / "windows_imu.csv", index=False)
    w_adc.to_csv(d / "windows_analog.csv", index=False)
    feats = extract_features(w_imu, w_adc, analog, matches) if len(w_imu) \
        else pd.DataFrame()
    feats.to_csv(d / "features.csv", index=False)

    n = len(matches)
    matched = int(matches["matched"].sum())
    spontaneous = int((events["trialIndex"] == -1).sum())
    per_contact = matches.groupby("contactKey")["matched"] \
        .agg(["sum", "count"])

    summary = {
        "imuRateHz": round(float(fs), 1),
        "trials": n,
        "matched": matched,
        "missed": n - matched,
        "extraOnsetsInWindows": int(matches["extraOnsets"].sum()),
        "onsetsOutsideWindows": spontaneous,
        "medianLatencyMs": round(float(matches.loc[matches["matched"],
                                 "latencyMs"].median()), 1) if matched else None,
        "threshold_g": threshold,
        "perContact": {k: f"{int(r['sum'])}/{int(r['count'])}"
                       for k, r in per_contact.iterrows()},
    }
    (d / "segment_summary.json").write_text(json.dumps(summary, indent=2))

    if not quiet:
        print(f"IMU rate: {fs:.0f} Hz | trials: {n} | "
              f"matched: {matched} | missed: {n - matched} | "
              f"onsets outside windows: {spontaneous}")
        for key, r in per_contact.iterrows():
            print(f"  {key:12s} {int(r['sum'])}/{int(r['count'])}")
        print(f"Wrote events, windows, features -> {d}")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session_dir", type=Path)
    ap.add_argument("--threshold", type=float, default=0.35,
                    help="onset peak height in g above baseline")
    ap.add_argument("--min-gap-ms", type=float, default=150,
                    help="debounce: minimum spacing between onsets")
    args = ap.parse_args(argv)
    summary = process_session(args.session_dir, args.threshold,
                              args.min_gap_ms)
    return 0 if summary["missed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
