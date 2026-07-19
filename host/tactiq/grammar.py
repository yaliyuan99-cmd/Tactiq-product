"""Duration grammar decoder — build-plan step 6 (P6).

Turns (contact, press duration) pairs into command tokens. Only the pinky
tip is shared between two commands, separated by duration, not tap count:
a brief tap is Quick Action 1, a sustained >=5 s hold is Emergency.
Everything else is a brief tap for its Table 3 command; presses that are
neither brief nor a full emergency hold are indeterminate and dropped
(logged) rather than guessed — a wrong command executes (P7).

Tokens are snake_case strings; they are the wire vocabulary shared by the
BLE firmware, the Android bridge, and the web demo (see docs/PROTOCOL.md).

Usage:
    python -m tactiq.grammar --demo
    python -m tactiq.grammar <session_dir> [--model <analysis>/model.joblib]
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

TAP_MAX_MS = 500
EMERGENCY_MIN_MS = 5000

# contact -> token for brief taps (Table 3)
TAP_TOKENS = {
    "index_tip": "confirm",
    "index_base": "back",
    "middle_tip": "undo",
    "middle_base": "next",
    "ring_tip": "read",
    "ring_base": "previous",
    "pinky_tip": "quick_action_1",
    "pinky_base": "quick_action_2",
}
EMERGENCY_CONTACT = "pinky_tip"


@dataclass(frozen=True)
class Token:
    name: str
    contact_key: str
    t_us: int
    dur_ms: float


def decode_event(contact_key: str, t_us: int, dur_ms: float) -> Token | None:
    if contact_key == EMERGENCY_CONTACT and dur_ms >= EMERGENCY_MIN_MS:
        return Token("emergency", contact_key, t_us, dur_ms)
    if dur_ms <= TAP_MAX_MS:
        return Token(TAP_TOKENS[contact_key], contact_key, t_us, dur_ms)
    return None  # indeterminate: neither tap nor emergency hold


def decode(events) -> tuple[list[Token], int]:
    """events: iterable of (contact_key, t_us, dur_ms)."""
    tokens, dropped = [], 0
    for key, t_us, dur_ms in events:
        tok = decode_event(key, t_us, dur_ms)
        if tok is None:
            dropped += 1
        else:
            tokens.append(tok)
    return tokens, dropped


def _demo() -> int:
    cases = [
        ("index_tip", 1_000_000, 160.0),
        ("middle_base", 2_500_000, 180.0),
        ("pinky_tip", 4_000_000, 150.0),     # brief -> quick_action_1
        ("pinky_tip", 6_000_000, 5200.0),    # sustained -> emergency
        ("ring_base", 13_000_000, 900.0),    # indeterminate -> dropped
    ]
    expected = ["confirm", "next", "quick_action_1", "emergency"]
    tokens, dropped = decode(cases)
    for tok in tokens:
        print(f"  t={tok.t_us / 1e6:6.1f}s  {tok.contact_key:12s} "
              f"{tok.dur_ms:7.1f} ms -> {tok.name}")
    print(f"  dropped as indeterminate: {dropped}")
    ok = [t.name for t in tokens] == expected and dropped == 1
    print("demo PASS" if ok else f"demo FAIL: expected {expected}")
    return 0 if ok else 1


def _session(session_dir: Path, model_path: Path | None) -> int:
    import pandas as pd

    feats = pd.read_csv(session_dir / "features.csv")
    if model_path:
        import joblib
        bundle = joblib.load(model_path)
        X = feats[bundle["features"]].to_numpy()
        contact = bundle["model"].predict(X)
        source_col = "recognised (model)"
    else:
        contact = feats["contactKey"]
        source_col = "prompted label (no model given)"

    events = [(c, 0, d) for c, d in zip(contact, feats["pressDurMs"])]
    tokens, dropped = decode(events)
    out = pd.DataFrame([{"trialIndex": i, "contactKey": e[0],
                         "pressDurMs": e[2],
                         "token": t.name if t else None}
                        for i, (e, t) in enumerate(
                            zip(events, [decode_event(*e) for e in events]))])
    out.to_csv(session_dir / "tokens.csv", index=False)
    counts = out["token"].value_counts().to_dict()
    print(f"Contacts from: {source_col}")
    print(f"{len(tokens)} tokens, {dropped} indeterminate -> tokens.csv")
    for name, n in sorted(counts.items()):
        print(f"  {name:16s} {n}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session_dir", nargs="?", type=Path)
    ap.add_argument("--model", type=Path, default=None,
                    help="model.joblib from tactiq.classify")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args(argv)

    if args.demo or not args.session_dir:
        return _demo()
    return _session(args.session_dir, args.model)


if __name__ == "__main__":
    sys.exit(main())
