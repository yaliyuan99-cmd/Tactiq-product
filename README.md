# Tactiq ring — bench prototype

The full 12-step build plan for the AUSSEF paper (*Beyond Screen Readers*),
implemented. Steps 1–7 and 11 are the verified bench pipeline that produces
the paper's pre-registered results (confusion matrix, effective capacity,
unrecovered-error rate, gate sweep and τ*); steps 8–10 and 12 extend toward
the product and are written but await hardware.

```
firmware/tactiq_ring/       step 1  acquisition sketch (USB serial stream)
firmware/tactiq_ring_ble/   steps 8+10  on-device pipeline: gate, classifier,
                            grammar, NUS tokens + optional HID, haptics
host/tactiq/
  contacts.py               Table 3 contacts + Table 4 conditions
  stream.py                 serial source + simulator (taps, holds,
                            squeezes, incidental idle noise)
  capture.py                step 2  prompted 8x20 randomised protocol
  segment.py                step 3  onset detection, windows, §2.6 features
  gate.py                   step 4  P9 wake-squeeze gate + tau sweep
  idlewear.py               step 4  false-activation / p_g session recorder
  classify.py               step 5  LDA/QDA, confusion matrix, pairwise D²
  grammar.py                step 6  duration grammar -> command tokens (P6)
  evaluate.py               step 7  capacity (eq 3), errors (eqs 4-5),
                            gate fit + tau* (eq 7), paper-ready figures
  calibrate.py              step 11 per-user calibration, quality from D²
  export_api.py             session -> website Gesture Testing API JSON
  export_centroids.py       trained model -> firmware centroids.h
android/                    step 9  AccessibilityService bridge (scaffold)
web-demo/index.html         step 12 Web Bluetooth dashboard + booth demo
docs/PROTOCOL.md            BLE UUIDs, token lines, haptic classes
host/data/                  sessions + analysis (gitignored, local only)
```

## Status at a glance

| Layer | Verified? |
|---|---|
| Steps 1–7, 11 (host pipeline) | ✅ end-to-end on simulated sessions |
| Step 12 (web demo, demo mode) | ✅ in browser |
| Step 1 firmware | ⚠️ written, needs the board to compile/flash |
| Steps 8+10 firmware, step 9 Android | ⚠️ scaffolds, need hardware/SDK |

**Simulated data is for pipeline testing only.** Every sim session is
flagged `"source": "simulated"`, every tool prints a warning on such data,
and nothing simulated may appear in the paper's results.

## Quick start (no hardware)

```bash
cd host
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Then the whole pre-registered protocol, simulated:

```bash
# step 2: 20 blocks x 8 contacts, randomised, seed recorded
python -m tactiq.capture --sim --speed 30 --reps 20 --seed 42
# step 3: onsets, windows, features        (use the printed session dir)
python -m tactiq.segment data/sessions/<capture_dir>
# step 5: cross-validated confusion matrix + D2
python -m tactiq.classify data/sessions/<capture_dir> --out data/analysis/run1
# step 6: duration grammar
python -m tactiq.grammar data/sessions/<capture_dir> --model data/analysis/run1/model.joblib
# step 4: two simulated hours of idle wear + gate sweep
python -m tactiq.idlewear --sim --minutes 120 --seed 11
python -m tactiq.gate data/sessions/<idle_dir>
# step 7: the paper's numbers and figures
python -m tactiq.evaluate --analysis data/analysis/run1 --idle data/sessions/<idle_dir>
# step 11: voice-guided per-user calibration
python -m tactiq.calibrate --sim --speed 30
```

`evaluate` writes `report.json`, `fig_confusion.png`, `fig_d2.png`,
`fig_gate_sweep.png` and an API-shaped `gesture_test_result.json` into the
analysis dir.

## Hardware (bench BOM, ~AU$60–90)

| Part | Qty | ~Price | Notes |
|---|---|---|---|
| Seeed XIAO nRF52840 **Sense** | 1 | $25 | must be the Sense (built-in IMU) |
| Flex sensor, 2.2" (Spectra Symbol) | 2 | $13 ea | |
| FSR 402 force sensor | 2 | $10 ea | |
| Resistors: 2× 47 kΩ, 2× 10 kΩ | — | $1 | divider legs |
| Breadboard + jumper wires | — | $8 | |
| *Step 10:* DRV2605L breakout + LRA motor | 1+1 | $12+$3 | haptics |

Wiring — each sensor is one leg of a voltage divider into an ADC pin:

```
3V3 ── flex sensor ──●── 47 kΩ ── GND        ● → A0 (flex 1), A1 (flex 2)
3V3 ── FSR 402 ─────●── 10 kΩ ── GND         ● → A2 (FSR 1),  A3 (FSR 2)
```

## Flash the firmware

1. Arduino IDE → Settings → Additional boards manager URLs, add:
   `https://files.seeedstudio.com/arduino/package_seeeduino_boards_index.json`
2. Boards Manager → install **Seeed nRF52 Boards**; select
   **Seeed XIAO nRF52840 Sense**.
3. Library Manager → install **Seeed Arduino LSM6DS3** (and
   **Adafruit DRV2605** for the BLE sketch with haptics).
4. Start with `firmware/tactiq_ring/` (bench stream). Serial Monitor shows
   `I,...` (~208 Hz) and `A,...` (100 Hz) lines; tapping blinks the LED.
5. `firmware/tactiq_ring_ble/` is the on-device pipeline (untested until
   the bench data exists). Regenerate its model with
   `python -m tactiq.export_centroids <analysis_or_calibration_dir>`.

## Running the real protocol (Table 4)

One session per condition per participant:

```bash
python -m tactiq.capture --participant P01 --condition seated      # etc.
python -m tactiq.idlewear --participant P01 --minutes 60           # gate row
```

Conditions: `seated`, `walking`, `cane_other_hand`, `phone_pocketed`,
`incidental_movement`. Voice prompts via macOS `say`; seeds and timing are
recorded so sessions are reproducible; raw streams are stored continuously
so segmentation can be re-run without re-running people.

## Naming bridge to the website

Paper (Table 3) says **tip/base**; the site's API says knuckle
**top/bottom**. `contacts.py` encodes the mapping once. `export_api.py`
emits `POST /diagnostics/gesture-test/{testId}/record` bodies,
`evaluate.py` emits the `GET /diagnostics/gesture-test/{testId}` response,
and `calibrate.py` emits the `/calibration/*` shapes — all field-for-field
against `tactiq-api-design.md`. Command tokens (`confirm`, `next`,
`emergency`, …) are defined in `grammar.py` and shared verbatim by the BLE
firmware, the Android bridge, and the web demo (docs/PROTOCOL.md).

## What the hardware unlocks

With the parts on the bench: flash step 1, run one real capture + idle
session per condition, re-run classify/evaluate on the real dirs, and the
paper's Results tables fill from `report.json`. Then calibrate, export
centroids, flash the BLE sketch, and the web demo + Android bridge run
against the actual ring.
