# Tactiq ring — bench prototype

Steps 1–3 of the build plan for the AUSSEF paper (*Beyond Screen Readers*):
raw signal acquisition on the ring MCU, the prompted capture harness
implementing Table 4's randomised trial structure, and contact-onset
segmentation per §3.2. Everything downstream (classifier, gate, metrics)
consumes what this repo produces.

```
firmware/tactiq_ring/   Arduino sketch for the Seeed XIAO nRF52840 Sense
host/tactiq/
  contacts.py           the 8 contacts of Table 3 + Table 4 conditions
  stream.py             serial source (real ring) + simulator (no hardware)
  capture.py            step 2: prompted 8×20 randomised protocol
  segment.py            step 3: onset detection + ~200 ms windows
  export_api.py         session → website Gesture Testing API JSON
host/data/sessions/     capture output (gitignored — local only)
```

## Try it right now (no hardware)

```bash
cd host
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m tactiq.capture --sim --speed 25 --reps 2 --seed 7
python -m tactiq.segment data/sessions/<the printed session dir>
python -m tactiq.export_api data/sessions/<the printed session dir>
```

The simulator generates synthetic taps with per-contact signal geometry
(§2.6-style clusters), so the whole pipeline runs end-to-end before the
parts arrive. **Simulated sessions are flagged `"source": "simulated"` in
session.json and must never appear in the paper's results.**

## Hardware (bench BOM, ~AU$60–90)

| Part | Qty | ~Price | Notes |
|---|---|---|---|
| Seeed XIAO nRF52840 **Sense** | 1 | $25 | must be the Sense (built-in IMU) |
| Flex sensor, 2.2" (Spectra Symbol) | 2 | $13 ea | |
| FSR 402 force sensor | 2 | $10 ea | |
| Resistors: 2× 47 kΩ, 2× 10 kΩ | — | $1 | divider legs |
| Breadboard + jumper wires | — | $8 | |
| *Later (step 10):* DRV2605L breakout + LRA motor | 1+1 | $12+$3 | haptics |

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
3. Library Manager → install **Seeed Arduino LSM6DS3**.
4. Open `firmware/tactiq_ring/tactiq_ring.ino`, upload.
5. Sanity check: Serial Monitor shows `I,...` (~208 Hz) and `A,...`
   (100 Hz) lines; tapping the board blinks the LED.

The sketch runs with no sensors wired (pins float), so the stream and
harness can be tested with the bare board.

## Run a real session (Table 4, per-contact discrimination)

```bash
python -m tactiq.capture --participant P01 --condition seated
```

- 20 blocks × 8 contacts = 160 prompted trials (~15 min), randomised
  within blocks, voice prompts via macOS `say`, seed recorded for
  reproducibility. `--reps`, `--seed`, `--break-every` to adjust.
- `--condition` one of: `seated`, `walking`, `cane_other_hand`,
  `phone_pocketed`, `incidental_movement` — the Table 4 conditions.
  Run one session per condition per participant.
- Raw streams are stored continuously with per-trial label + timing rows,
  so segmentation can be re-run with different parameters later without
  re-running the participant.

Then segment: `python -m tactiq.segment data/sessions/<dir>` — reports
detected/missed taps per contact and writes `events.csv`, the ~200 ms
`windows_*.csv`, and `features.csv` (peak accel, flex deltas, FSR peaks —
an early look at §2.6 separability; the real classifier is step 5).

## Naming bridge to the website

Paper (Table 3) says **tip/base**; the site's Gesture Testing API says
knuckle **top/bottom**. `contacts.py` encodes the mapping once; capture
CSVs and `export_api.py` use the API's field names (`finger`, `knuckle`,
`tapCount`, `pressure`, `confidence`), so
`POST /diagnostics/gesture-test/{testId}/record` accepts bench data as-is.

## What's next (build plan)

4. wake-squeeze gate state machine (P9) · 5. §2.6 feature extraction +
LDA/QDA classifier · 6. duration grammar (tap vs 5 s emergency hold) ·
7. pre-registered metrics: confusion matrix, capacity via eq. (3),
unrecovered-error via eqs. (4)–(5), τ* via eq. (7).
