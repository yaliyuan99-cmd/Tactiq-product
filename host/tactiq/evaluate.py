"""Evaluation suite — build-plan step 7: the pre-registered metrics.

Turns classifier + gate outputs into the paper's decisive numbers and
figures:

  * the 8x8 confusion matrix heatmap, with the predicted within-finger
    blocks outlined (section 3.5's falsifiable structure claim);
  * effective capacity in bits per command — equation (3)'s mutual
    information — against the log2(8) = 3-bit ceiling;
  * the error-propagation composition of equations (4)-(5): first-attempt
    success p_g*p_c and unrecovered consequential error p_g*(1-p_c)*(1-p_u);
  * the gate sweep fitted to f(tau) = f0*exp(-tau/tau0) and the optimal
    hold tau* from equation (7), tabulated over alpha/beta weight ratios
    (placeholders until the Phase-1 participatory study measures them);
  * gesture_test_result.json in the website's GET /diagnostics/gesture-test
    response shape.

Usage:
    python -m tactiq.evaluate --analysis data/analysis/sim1 \
        --idle data/sessions/<idle_dir> --hand right
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

from .contacts import CONTACTS, KEYS

INK = "#334155"       # text and axes: neutral ink, never series color
GRID = "#e2e8f0"
BLUE = "#2563eb"      # single measured-data hue
ACCENT = "#b45309"    # acceptance threshold line
ALPHA_BETA_RATIOS = [0.01, 0.03, 0.1, 0.3]  # hours-equivalent per false fire


def mutual_information_bits(conf: pd.DataFrame) -> float:
    """Equation (3): I(X;Y) from the confusion matrix, uniform intents."""
    joint = conf.to_numpy(dtype=float)
    joint = joint / joint.sum()
    px = joint.sum(axis=1, keepdims=True)
    py = joint.sum(axis=0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = joint * np.log2(joint / (px @ py))
    return float(np.nansum(terms))


def fit_gate(sweep: list[dict]):
    """Fit f(tau) = f0 * exp(-tau/tau0); tau in seconds, f per hour."""
    tau = np.array([r["tauMs"] for r in sweep]) / 1000.0
    f = np.array([r["falsePerHour"] for r in sweep], dtype=float)
    (f0, tau0), _ = curve_fit(lambda t, f0, tau0: f0 * np.exp(-t / tau0),
                              tau, f, p0=[max(f.max(), 1.0), 0.15],
                              maxfev=10_000)
    return float(f0), float(tau0)


def tau_star(f0: float, tau0: float, ratio: float) -> float | None:
    """Equation (7): tau* = tau0 * ln(alpha*f0 / (beta*tau0)).

    `ratio` = alpha/beta in hour-equivalents of latency cost per false
    activation — a placeholder weighting until the participatory study.
    """
    arg = ratio * f0 / tau0
    return tau0 * np.log(arg) if arg > 1 else None  # else tau*=0: gate free


def fig_confusion(conf: pd.DataFrame, out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    counts = conf.to_numpy(dtype=float)
    rownorm = counts / counts.sum(axis=1, keepdims=True)
    fig, ax = plt.subplots(figsize=(7.2, 6.2))
    im = ax.imshow(rownorm, cmap="Blues", vmin=0, vmax=1)
    labels = [k.replace("_", "\n") for k in conf.index]
    ax.set_xticks(range(len(labels)), labels, fontsize=8, color=INK)
    ax.set_yticks(range(len(labels)), labels, fontsize=8, color=INK)
    ax.set_xlabel("recognised", color=INK)
    ax.set_ylabel("intended", color=INK)
    for i in range(counts.shape[0]):
        for j in range(counts.shape[1]):
            if counts[i, j]:
                ax.text(j, i, int(counts[i, j]), ha="center", va="center",
                        fontsize=8,
                        color="white" if rownorm[i, j] > 0.5 else INK)
    # outline the within-finger 2x2 blocks — where section 3.5 predicts
    # confusion should cluster
    for b in range(4):
        ax.add_patch(plt.Rectangle((2 * b - 0.5, 2 * b - 0.5), 2, 2,
                                   fill=False, edgecolor=INK,
                                   linewidth=1.2, linestyle=":"))
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.tick_params(color=GRID)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("row fraction", color=INK)
    ax.set_title("Per-contact confusion matrix (cross-validated)",
                 color=INK, fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    plt.close(fig)


def fig_gate(sweep, f0, tau0, tau_star_s, out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tau_ms = np.array([r["tauMs"] for r in sweep])
    f = np.array([r["falsePerHour"] for r in sweep])
    xs = np.linspace(0, max(tau_ms) * 1.15, 200)
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    ax.plot(xs, f0 * np.exp(-xs / 1000 / tau0), color=BLUE, lw=2,
            label=f"fit: f(τ) = {f0:.1f}·exp(−τ/{tau0 * 1000:.0f} ms)")
    ax.plot(tau_ms, f, "o", color=BLUE, ms=7, mec="white", mew=1.5,
            label="measured", zorder=3)
    ax.axhline(1.0, color=ACCENT, lw=1.2, ls="--")
    ax.text(xs[-1], 1.0, "  target ≤1/h", color=ACCENT, fontsize=8,
            va="bottom", ha="right")
    if tau_star_s is not None:
        ax.axvline(tau_star_s * 1000, color=INK, lw=1.2, ls=":")
        ax.text(tau_star_s * 1000, ax.get_ylim()[1] * 0.95,
                f" τ* ≈ {tau_star_s * 1000:.0f} ms", color=INK, fontsize=9)
    ax.set_xlabel("engagement hold τ (ms)", color=INK)
    ax.set_ylabel("false activations per hour", color=INK)
    ax.set_title("Gate sweep: false activation vs engagement hold",
                 color=INK, fontsize=11)
    ax.grid(color=GRID, lw=0.6)
    ax.tick_params(colors=INK)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    plt.close(fig)


def fig_d2(d2: pd.DataFrame, out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    vals = d2.to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=(7.2, 6.2))
    im = ax.imshow(vals, cmap="Blues")
    labels = [k.replace("_", "\n") for k in d2.index]
    ax.set_xticks(range(len(labels)), labels, fontsize=8, color=INK)
    ax.set_yticks(range(len(labels)), labels, fontsize=8, color=INK)
    lim = vals.max()
    for i in range(vals.shape[0]):
        for j in range(vals.shape[1]):
            if i != j:
                ax.text(j, i, f"{vals[i, j]:.0f}", ha="center", va="center",
                        fontsize=7.5,
                        color="white" if vals[i, j] > 0.5 * lim else INK)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Mahalanobis D²", color=INK)
    ax.set_title("Pairwise contact separation (equation 1)",
                 color=INK, fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    plt.close(fig)


def api_result(metrics: dict, hand: str, test_id: str) -> dict:
    """GET /diagnostics/gesture-test/{testId} response shape."""
    results = []
    worst = (None, 1.0)
    for c in CONTACTS:
        acc = metrics["perContactAccuracy"].get(c.key)
        if acc is None:
            continue
        if acc < worst[1]:
            worst = (c, acc)
        results.append({
            "gesture": f"{hand}_{c.finger}_{c.knuckle}_single_tap",
            "confidence": acc,  # per-contact accuracy as proxy confidence
            "status": "accurate" if acc >= 0.95 else "needs_calibration",
        })
    rec = "All contacts meet the 95% per-contact target." \
        if metrics["meetsTarget"] else \
        (f"Recalibrate for {worst[0].finger} {worst[0].knuckle} "
         f"({worst[1]:.0%}).")
    return {"testId": test_id, "source": metrics["source"],
            "overallAccuracy": metrics["overallAccuracy"],
            "results": results, "recommendation": rec}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--analysis", type=Path, required=True,
                    help="output dir of tactiq.classify")
    ap.add_argument("--idle", type=Path, default=None,
                    help="idle-wear session dir (for the gate sweep)")
    ap.add_argument("--hand", default="right")
    ap.add_argument("--pu", type=float, default=0.9,
                    help="P(error caught by feedback+undo) — paper's default")
    ap.add_argument("--pg-tau", type=float, default=150,
                    help="tau (ms) whose measured p_g feeds equation (4)")
    args = ap.parse_args(argv)

    a = args.analysis
    conf = pd.read_csv(a / "confusion.csv", index_col=0)
    d2 = pd.read_csv(a / "d2_matrix.csv", index_col=0)
    metrics = json.loads((a / "metrics.json").read_text())
    if metrics["source"] == "simulated":
        print("NOTE: SIMULATED data — pipeline testing only, not results.")

    # -- capacity (equation 3) -----------------------------------------------
    bits = mutual_information_bits(conf)
    ceiling = np.log2(len(KEYS))
    # -- error propagation (equations 4-5) -----------------------------------
    p_c = metrics["overallAccuracy"]
    p_g, gate_fit, tau_table = None, None, []
    if args.idle:
        gate = json.loads((args.idle / "gate_sweep.json").read_text())
        row = min(gate["sweep"], key=lambda r: abs(r["tauMs"] - args.pg_tau))
        p_g = row["pG"]
        f0, tau0 = fit_gate(gate["sweep"])
        gate_fit = {"f0PerHour": round(f0, 2), "tau0Ms": round(tau0 * 1e3, 1)}
        tau_table = [{"alphaOverBeta": r,
                      "tauStarMs": round(t * 1000, 0) if (t := tau_star(
                          f0, tau0, r)) else 0}
                     for r in ALPHA_BETA_RATIOS]
    p_g_eff = p_g if p_g is not None else 1.0
    first_attempt = p_g_eff * p_c                            # equation (4)
    unrecovered = p_g_eff * (1 - p_c) * (1 - args.pu)        # equation (5)

    report = {
        "source": metrics["source"],
        "trials": metrics["trials"],
        "overallAccuracy": p_c,
        "perContactAccuracy": metrics["perContactAccuracy"],
        "meets95Target": metrics["meetsTarget"],
        "minPairwiseD2": metrics["minPairwiseD2"],
        "capacityBitsPerCommand": round(bits, 3),
        "capacityCeilingBits": round(float(ceiling), 3),
        "capacityFractionOfIdeal": round(bits / ceiling, 4),
        "pG": p_g, "pGTauMs": args.pg_tau if p_g is not None else None,
        "pC": p_c, "pU": args.pu,
        "firstAttemptSuccess": round(first_attempt, 4),
        "unrecoveredErrorRate": round(unrecovered, 5),
        "unrecoveredOneInN": round(1 / unrecovered) if unrecovered else None,
        "gateFit": gate_fit,
        "tauStarTable": tau_table,
        "tauStarNote": "alpha/beta placeholders pending Phase-1 study",
    }
    (a / "report.json").write_text(json.dumps(report, indent=2))

    fig_confusion(conf, a / "fig_confusion.png")
    fig_d2(d2, a / "fig_d2.png")
    if gate_fit:
        default_tau = next((r["tauStarMs"] / 1000 for r in tau_table
                            if r["alphaOverBeta"] == 0.1 and r["tauStarMs"]),
                           None)
        gate = json.loads((args.idle / "gate_sweep.json").read_text())
        fig_gate(gate["sweep"], gate_fit["f0PerHour"],
                 gate_fit["tau0Ms"] / 1000, default_tau,
                 a / "fig_gate_sweep.png")

    api = api_result(metrics, args.hand, a.name)
    (a / "gesture_test_result.json").write_text(json.dumps(api, indent=2))

    print(f"Capacity: {bits:.2f} bits/command of {ceiling:.2f}-bit ceiling "
          f"({bits / ceiling:.1%} of ideal)")
    print(f"Eq (4) first-attempt success: {first_attempt:.1%}   "
          f"(p_g={p_g_eff}, p_c={p_c})")
    print(f"Eq (5) unrecovered error: {unrecovered:.4%} — one in "
          f"~{report['unrecoveredOneInN']} commands (p_u={args.pu})")
    if gate_fit:
        print(f"Gate fit: f0={gate_fit['f0PerHour']}/h, "
              f"tau0={gate_fit['tau0Ms']} ms")
        for r in tau_table:
            print(f"  alpha/beta={r['alphaOverBeta']:<5g} "
                  f"tau* = {r['tauStarMs']:.0f} ms")
    print(f"Wrote report.json, figures, gesture_test_result.json -> {a}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
