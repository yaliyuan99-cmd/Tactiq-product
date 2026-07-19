"""Wake-squeeze activation gate — build-plan step 4 (P9).

The gate admits recognition only after a deliberate engagement squeeze:
both FSR pads pressed simultaneously (a thumb tap presses one pad; a
squeeze of the ring body presses both), sustained for the engagement-hold
duration tau — the tunable parameter of equation (7).

This module evaluates the gate offline over recorded sessions:

  * finds every "press" (sustained above-threshold run of the squeeze
    signal) and whether it would arm the gate at a given tau;
  * against an idle-wear session's squeezes.csv ground truth, splits
    activations into true positives (p_g) and false activations per hour;
  * sweeps tau over Table 4's values (100/150/250/500 ms) to produce the
    curve that step 7 fits f(tau) = f0 * exp(-tau/tau0) to.

Usage:
    python -m tactiq.gate host/data/sessions/<idle_session_dir>
    python -m tactiq.gate <dir> --taus 100,150,250,500 --threshold 800
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

GATE_THRESHOLD = 800     # counts; squeeze presses both pads well above this
MATCH_WINDOW_S = 2.0     # armed within this of a scheduled squeeze = true +


def squeeze_signal(analog: pd.DataFrame, mode: str = "both") -> np.ndarray:
    """'both' = min(fsr1, fsr2): requires both pads pressed (default)."""
    if mode == "both":
        return np.minimum(analog["fsr1"].to_numpy(),
                          analog["fsr2"].to_numpy())
    return np.maximum(analog["fsr1"].to_numpy(), analog["fsr2"].to_numpy())


def find_presses(analog: pd.DataFrame, threshold: float,
                 mode: str = "both") -> pd.DataFrame:
    """Contiguous above-threshold runs of the squeeze signal."""
    sig = squeeze_signal(analog, mode)
    t = analog["t_us"].to_numpy()
    above = sig >= threshold
    edges = np.diff(above.astype(np.int8))
    starts = np.flatnonzero(edges == 1) + 1
    ends = np.flatnonzero(edges == -1) + 1
    if above[0]:
        starts = np.insert(starts, 0, 0)
    if above[-1]:
        ends = np.append(ends, len(above))
    rows = [{"startUs": int(t[s]), "endUs": int(t[e - 1]),
             "durMs": round((t[e - 1] - t[s]) / 1000, 1),
             "peak": int(sig[s:e].max())}
            for s, e in zip(starts, ends)]
    return pd.DataFrame(rows,
                        columns=["startUs", "endUs", "durMs", "peak"])


def activations(presses: pd.DataFrame, tau_ms: float) -> pd.DataFrame:
    """Presses that sustain past tau arm the gate at startUs + tau."""
    armed = presses[presses["durMs"] >= tau_ms].copy()
    armed["armedAtUs"] = armed["startUs"] + int(tau_ms * 1000)
    return armed


def evaluate_gate(session_dir: Path, taus_ms, threshold=GATE_THRESHOLD,
                  mode: str = "both") -> dict:
    d = Path(session_dir)
    analog = pd.read_csv(d / "analog.csv")
    session = json.loads((d / "session.json").read_text())
    hours = (analog["t_us"].iloc[-1] - analog["t_us"].iloc[0]) / 3.6e9

    truth = []
    if (d / "squeezes.csv").exists():
        truth = pd.read_csv(d / "squeezes.csv")["atUs"].tolist()

    presses = find_presses(analog, threshold, mode)
    presses.to_csv(d / "gate_presses.csv", index=False)

    sweep = []
    for tau in taus_ms:
        armed = activations(presses, tau)
        hits = set()
        false_count = 0
        for a in armed.itertuples():
            match = next((s for s in truth if s not in hits and
                          0 <= a.armedAtUs - s <= MATCH_WINDOW_S * 1e6), None)
            if match is not None:
                hits.add(match)
            else:
                false_count += 1
        sweep.append({
            "tauMs": tau,
            "activations": len(armed),
            "falseActivations": false_count,
            "falsePerHour": round(false_count / hours, 2),
            "pG": round(len(hits) / len(truth), 3) if truth else None,
        })

    result = {
        "sessionDir": d.name,
        "source": session.get("source"),
        "hours": round(hours, 3),
        "threshold": threshold,
        "signalMode": mode,
        "totalPresses": len(presses),
        "deliberateSqueezes": len(truth),
        "sweep": sweep,
    }
    (d / "gate_sweep.json").write_text(json.dumps(result, indent=2))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session_dir", type=Path)
    ap.add_argument("--taus", default="100,150,250,500",
                    help="comma-separated engagement holds in ms (Table 4)")
    ap.add_argument("--threshold", type=float, default=GATE_THRESHOLD)
    ap.add_argument("--signal", choices=["both", "either"], default="both")
    args = ap.parse_args(argv)

    taus = [float(x) for x in args.taus.split(",")]
    r = evaluate_gate(args.session_dir, taus, args.threshold, args.signal)

    if r["source"] == "simulated":
        print("NOTE: SIMULATED session — pipeline testing only, not results.")
    print(f"{r['hours']:.2f} h of wear, {r['totalPresses']} presses, "
          f"{r['deliberateSqueezes']} deliberate squeezes "
          f"(threshold {r['threshold']:g}, signal {r['signalMode']})")
    print(f"{'tau ms':>8} {'false/h':>9} {'p_g':>6}")
    for row in r["sweep"]:
        pg = f"{row['pG']:.3f}" if row["pG"] is not None else "  n/a"
        print(f"{row['tauMs']:>8g} {row['falsePerHour']:>9.2f} {pg:>6}")
    print(f"Wrote gate_presses.csv, gate_sweep.json -> {args.session_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
