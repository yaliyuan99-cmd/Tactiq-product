"""Feature-space contact classifier — build-plan step 5.

Implements section 2.6 literally: the feature vector x (flex angle, contact
pressure, inertial transient statistics) is classified over per-contact
Gaussian clusters. LDA — Mahalanobis distance to class means under a pooled
covariance — is exactly the paper's stated model and is the default; QDA
(per-class covariances) is available for comparison.

Outputs, per analysis directory:

    confusion.csv    8x8 cross-validated confusion matrix (rows = intended)
    d2_matrix.csv    pairwise Mahalanobis D-squared (equation 1)
    metrics.json     per-contact and overall accuracy, min pairwise D2
    centroids.json   class means + pooled scale, for the firmware's
                     nearest-centroid first pass (export_centroids.py)
    model.joblib     fitted sklearn model for the host-side pipeline

Usage:
    python -m tactiq.classify <session_dir> [<session_dir> ...] --out <dir>
"""

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.discriminant_analysis import (LinearDiscriminantAnalysis,
                                           QuadraticDiscriminantAnalysis)
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from .contacts import KEYS

# the section-2.6 vector (d = 7 here; pressDurMs is deliberately excluded —
# duration belongs to the grammar layer (P6), not the contact classifier)
FEATURES = ["flexDelta1", "flexDelta2", "fsrPeak1", "fsrPeak2",
            "peakAccelG", "accelRmsG", "gyroPeakDps"]


def load_features(session_dirs) -> tuple[pd.DataFrame, bool]:
    """Concatenate features.csv across sessions; returns (df, any_simulated)."""
    frames, simulated = [], False
    for d in map(Path, session_dirs):
        session = json.loads((d / "session.json").read_text())
        f = pd.read_csv(d / "features.csv")
        f["session"] = d.name
        f["condition"] = session.get("condition", "unknown")
        simulated |= session.get("source") == "simulated"
        frames.append(f)
    df = pd.concat(frames, ignore_index=True).dropna(subset=FEATURES)
    return df, simulated


def crossval_confusion(df: pd.DataFrame, model: str = "lda",
                       folds: int = 5, seed: int = 0):
    X = df[FEATURES].to_numpy()
    y = df["contactKey"].to_numpy()
    folds = min(folds, int(pd.Series(y).value_counts().min()))
    clf = LinearDiscriminantAnalysis() if model == "lda" \
        else QuadraticDiscriminantAnalysis(reg_param=0.1)
    cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    pred = cross_val_predict(clf, X, y, cv=cv)
    conf = pd.crosstab(pd.Series(y, name="intended"),
                       pd.Series(pred, name="recognised"))
    conf = conf.reindex(index=KEYS, columns=KEYS, fill_value=0)
    per_contact = {k: round(conf.loc[k, k] / conf.loc[k].sum(), 4)
                   for k in KEYS if conf.loc[k].sum()}
    overall = round(float(np.trace(conf.to_numpy()) /
                          conf.to_numpy().sum()), 4)
    return conf, per_contact, overall, folds


def pairwise_d2(df: pd.DataFrame) -> pd.DataFrame:
    """Equation (1): D2 under the pooled within-class covariance."""
    X = df[FEATURES].to_numpy(dtype=float)
    y = df["contactKey"].to_numpy()
    means, pooled = {}, np.zeros((len(FEATURES), len(FEATURES)))
    n_total = 0
    for k in KEYS:
        Xi = X[y == k]
        means[k] = Xi.mean(axis=0)
        pooled += (len(Xi) - 1) * np.cov(Xi, rowvar=False)
        n_total += len(Xi) - 1
    pooled /= n_total
    pooled += np.eye(len(FEATURES)) * 1e-6 * np.trace(pooled)  # regularise
    inv = np.linalg.inv(pooled)
    d2 = pd.DataFrame(0.0, index=KEYS, columns=KEYS)
    for i, a in enumerate(KEYS):
        for b in KEYS[i + 1:]:
            diff = means[a] - means[b]
            d2.loc[a, b] = d2.loc[b, a] = float(diff @ inv @ diff)
    return d2.round(1)


def min_offdiag_d2(d2: pd.DataFrame) -> float:
    vals = d2.to_numpy()[~np.eye(len(d2), dtype=bool)]
    return float(vals.min())


def fit_and_save(df: pd.DataFrame, out_dir: Path, model: str = "lda",
                 simulated: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    conf, per_contact, overall, folds = crossval_confusion(df, model)
    d2 = pairwise_d2(df)

    X, y = df[FEATURES].to_numpy(), df["contactKey"].to_numpy()
    clf = LinearDiscriminantAnalysis() if model == "lda" \
        else QuadraticDiscriminantAnalysis(reg_param=0.1)
    clf.fit(X, y)
    joblib.dump({"model": clf, "features": FEATURES}, out_dir / "model.joblib")

    centroids = {
        "features": FEATURES,
        "classes": KEYS,
        "means": {k: [round(v, 3) for v in
                      X[y == k].mean(axis=0)] for k in KEYS},
        "scale": [round(v, 3) for v in X.std(axis=0)],
    }
    (out_dir / "centroids.json").write_text(json.dumps(centroids, indent=2))

    conf.to_csv(out_dir / "confusion.csv")
    d2.to_csv(out_dir / "d2_matrix.csv")
    metrics = {
        "source": "simulated" if simulated else "device",
        "model": model,
        "cvFolds": folds,
        "trials": len(df),
        "sessions": sorted(df["session"].unique().tolist()),
        "conditions": sorted(df["condition"].unique().tolist()),
        "overallAccuracy": overall,
        "perContactAccuracy": per_contact,
        "minPairwiseD2": round(min_offdiag_d2(d2), 1),
        "acceptanceTarget": 0.95,  # Table 4: >=95% per contact
        "meetsTarget": all(v >= 0.95 for v in per_contact.values()),
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sessions", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", choices=["lda", "qda"], default="lda")
    args = ap.parse_args(argv)

    df, simulated = load_features(args.sessions)
    if simulated:
        print("NOTE: SIMULATED data — pipeline testing only, not results.")
    metrics = fit_and_save(df, args.out, args.model, simulated)

    print(f"{metrics['trials']} trials, {metrics['cvFolds']}-fold CV "
          f"({metrics['model'].upper()})")
    print(f"Overall accuracy: {metrics['overallAccuracy']:.1%}  "
          f"(Table 4 target: >=95% per contact — "
          f"{'MET' if metrics['meetsTarget'] else 'NOT MET'})")
    for k, v in metrics["perContactAccuracy"].items():
        print(f"  {k:12s} {v:.1%}")
    print(f"Min pairwise D2: {metrics['minPairwiseD2']}")
    print(f"Wrote confusion, D2, centroids, model -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
