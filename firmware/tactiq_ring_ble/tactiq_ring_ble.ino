/*
  Tactiq ring — BLE command firmware (build-plan steps 8 + 10).

  The full on-device pipeline: sampling -> wake-squeeze gate (P9) ->
  onset detection -> nearest-centroid contact classification (the paper's
  "threshold rules suffice for a first pass", section 3.2) -> duration
  grammar (P6) -> command tokens over BLE, with tiered haptic feedback
  (P3/P4) on a DRV2605L.

  Board:     Seeed XIAO nRF52840 Sense, Seeed nRF52 (non-mbed) core,
             which bundles the Adafruit Bluefruit BLE stack.
  Libraries: Seeed Arduino LSM6DS3; Adafruit DRV2605 (if HAS_DRV2605).
  Protocol:  docs/PROTOCOL.md. Token lines go over the Nordic UART
             service; set TACTIQ_HID 1 for the iOS route (HID keyboard
             presenting VoiceOver QuickNav chords, section 3.4).
  Model:     centroids.h — regenerate per user with
             python -m tactiq.export_centroids (step 11 calibration).

  STATUS: written to the bench-verified host pipeline's semantics
  (host/tactiq/{gate,classify,grammar}.py) but NOT yet compiled or run on
  hardware — flash the bench sketch first, collect data, calibrate, then
  bring this up.
*/

#include <bluefruit.h>
#include <LSM6DS3.h>
#include <Wire.h>
#include "centroids.h"

#define TACTIQ_HID 0    // 1 = also present as HID keyboard (iOS/VoiceOver)
#define HAS_DRV2605 0   // 1 once the haptic driver is wired (step 10)

#if HAS_DRV2605
#include "Adafruit_DRV2605.h"
Adafruit_DRV2605 drv;
#endif

// ---- configuration ---------------------------------------------------------

const int PIN_FLEX1 = A0, PIN_FLEX2 = A1, PIN_FSR1 = A2, PIN_FSR2 = A3;

const uint32_t IMU_PERIOD_US = 4808;   // ~208 Hz
const uint32_t ADC_PERIOD_US = 10000;  // 100 Hz

// P9 gate — same semantics as host/tactiq/gate.py
const int      GATE_THRESHOLD   = 800;    // counts on min(fsr1, fsr2)
const uint32_t GATE_TAU_MS      = 150;    // engagement hold; replace with
                                          // tau* once eq (7) is measured
const uint32_t ARM_WINDOW_MS    = 3000;   // armed idles back after this

// onset + grammar — same as host/tactiq/{segment,grammar}.py
const float    ONSET_THRESHOLD_G = 0.35f;
const uint32_t FEATURE_WINDOW_MS = 150;
const int      PRESS_DELTA       = 150;   // counts above rest = pressed
const uint32_t TAP_MAX_MS        = 500;
const uint32_t EMERGENCY_MIN_MS  = 5000;
const int      EMERGENCY_CLASS   = 6;     // pinky_tip in CLASS_KEYS

// haptic classes (P4: deliberately few) — DRV2605 effect library numbers
const uint8_t FX_ARMED = 24, FX_CONFIRM = 1, FX_REJECT = 10, FX_SOS = 15;

// ---- state -----------------------------------------------------------------

LSM6DS3 imu(I2C_MODE, 0x6A);
BLEDis bledis;
BLEBas blebas;
BLEUart bleuart;
#if TACTIQ_HID
BLEHidAdafruit blehid;
#endif

uint32_t nextImuUs, nextAdcUs;

// slow baselines (updated only while idle, so presses don't drag them)
float restFlex1 = 0, restFlex2 = 0, restFsr1 = 0, restFsr2 = 0;
float accelMagEma = 1.0f;
bool baselinesReady = false;
uint32_t baselineSamples = 0;

enum GateState { GATE_IDLE, GATE_PENDING, GATE_ARMED };
GateState gate = GATE_IDLE;
uint32_t gatePressStartMs = 0, gateArmedAtMs = 0;

enum RecogState { REC_WAIT_ONSET, REC_COLLECT, REC_WAIT_RELEASE };
RecogState rec = REC_WAIT_ONSET;
uint32_t onsetMs = 0, lastPressedMs = 0;
bool emergencyFired = false;
float fPeakAccel, fFlex1, fFlex2, fFsr1, fFsr2;   // feature accumulators
int latestFsr1 = 0, latestFsr2 = 0;
int pressClass = -1;

// ---- helpers ---------------------------------------------------------------

void haptic(uint8_t effect) {
#if HAS_DRV2605
  drv.setWaveform(0, effect);
  drv.setWaveform(1, 0);
  drv.go();
#else
  (void)effect;
#endif
}

void txLine(const char* fmt, ...) {
  char buf[96];
  va_list args;
  va_start(args, fmt);
  vsnprintf(buf, sizeof(buf), fmt, args);
  va_end(args);
  if (Bluefruit.connected()) bleuart.println(buf);
  Serial.println(buf);  // mirrored on USB for bench debugging
}

#if TACTIQ_HID
// VoiceOver QuickNav chords (§3.4): the fixed command set only. Keycodes
// are USB HID usage IDs. Unmapped commands are intentionally absent — the
// paper does not promise custom/emergency actions on the HID route.
void hidChord(uint8_t k1, uint8_t k2) {
  uint8_t keys[6] = {k1, k2, 0, 0, 0, 0};
  blehid.keyboardReport(0, keys);
  delay(8);
  blehid.keyRelease();
}
bool hidSend(const char* token) {
  if (strcmp(token, "next") == 0)     { hidChord(0x4F, 0); return true; }  // Right
  if (strcmp(token, "previous") == 0) { hidChord(0x50, 0); return true; }  // Left
  if (strcmp(token, "confirm") == 0)  { hidChord(0x52, 0x51); return true; } // Up+Down
  if (strcmp(token, "back") == 0)     { hidChord(0x29, 0); return true; }  // Esc
  return false;
}
#endif

int classify() {
  // nearest centroid on z-scaled features — Mahalanobis with a diagonal
  // pooled covariance, the on-device first pass of section 2.6
  const float x[N_FEATURES] = {
    fFlex1 - restFlex1, fFlex2 - restFlex2, fFsr1, fFsr2, fPeakAccel};
  int best = -1;
  float bestD = 1e30f;
  for (int c = 0; c < N_CLASSES; c++) {
    float d = 0;
    for (int f = 0; f < N_FEATURES; f++) {
      float z = (x[f] - CENTROID_MEAN[c][f]) / FEATURE_SCALE[f];
      d += z * z;
    }
    if (d < bestD) { bestD = d; best = c; }
  }
  return best;
}

void emitToken(const char* token, int cls, uint32_t nowMs, uint32_t durMs) {
  txLine("TOK,%s,%s,%lu,%lu", token, CLASS_KEYS[cls],
         (unsigned long)nowMs, (unsigned long)durMs);
#if TACTIQ_HID
  hidSend(token);
#endif
}

// ---- pipeline --------------------------------------------------------------

void feedAnalog(uint32_t nowMs, int flex1, int flex2, int fsr1, int fsr2) {
  latestFsr1 = fsr1;
  latestFsr2 = fsr2;

  // learn rest baselines while nothing is pressed
  bool pressed = fsr1 - restFsr1 > PRESS_DELTA ||
                 fsr2 - restFsr2 > PRESS_DELTA;
  if (!baselinesReady || (!pressed && rec == REC_WAIT_ONSET)) {
    const float a = baselinesReady ? 0.002f : 0.05f;
    restFlex1 += a * (flex1 - restFlex1);
    restFlex2 += a * (flex2 - restFlex2);
    restFsr1 += a * (fsr1 - restFsr1);
    restFsr2 += a * (fsr2 - restFsr2);
    if (++baselineSamples > 100) baselinesReady = true;
  }

  // ---- P9 gate: both pads sustained for tau -> armed
  int squeeze = min(fsr1, fsr2);
  switch (gate) {
    case GATE_IDLE:
      if (squeeze >= GATE_THRESHOLD) {
        gate = GATE_PENDING;
        gatePressStartMs = nowMs;
      }
      break;
    case GATE_PENDING:
      if (squeeze < GATE_THRESHOLD) {
        gate = GATE_IDLE;
      } else if (nowMs - gatePressStartMs >= GATE_TAU_MS) {
        gate = GATE_ARMED;
        gateArmedAtMs = nowMs;
        haptic(FX_ARMED);
        txLine("GATE,armed,%lu", (unsigned long)nowMs);
      }
      break;
    case GATE_ARMED:
      if (rec == REC_WAIT_ONSET && nowMs - gateArmedAtMs > ARM_WINDOW_MS) {
        gate = GATE_IDLE;
        txLine("GATE,idle,%lu", (unsigned long)nowMs);
      }
      break;
  }

  // ---- feature window + press tracking
  if (rec == REC_COLLECT || rec == REC_WAIT_RELEASE) {
    fFlex1 = max(fFlex1, (float)flex1);
    fFlex2 = max(fFlex2, (float)flex2);
    fFsr1 = max(fFsr1, (float)fsr1);
    fFsr2 = max(fFsr2, (float)fsr2);
    if (pressed) lastPressedMs = nowMs;
  }
}

void feedImu(uint32_t nowMs, float ax, float ay, float az) {
  float mag = sqrtf(ax * ax + ay * ay + az * az);
  accelMagEma += 0.02f * (mag - accelMagEma);
  float hp = mag - accelMagEma;

  switch (rec) {
    case REC_WAIT_ONSET:
      if (gate == GATE_ARMED && baselinesReady && hp > ONSET_THRESHOLD_G) {
        rec = REC_COLLECT;
        onsetMs = nowMs;
        lastPressedMs = nowMs;
        emergencyFired = false;
        fPeakAccel = mag;
        fFlex1 = restFlex1; fFlex2 = restFlex2;
        fFsr1 = 0; fFsr2 = 0;
      }
      break;

    case REC_COLLECT:
      fPeakAccel = max(fPeakAccel, mag);
      if (nowMs - onsetMs >= FEATURE_WINDOW_MS) {
        pressClass = classify();
        rec = REC_WAIT_RELEASE;
      }
      break;

    case REC_WAIT_RELEASE: {
      uint32_t heldMs = lastPressedMs - onsetMs;
      bool released = nowMs - lastPressedMs > 100;

      // P6: emergency fires AT the 5 s mark while still held — an SOS
      // must not wait for the user to let go
      if (!emergencyFired && pressClass == EMERGENCY_CLASS &&
          nowMs - onsetMs >= EMERGENCY_MIN_MS && !released) {
        emergencyFired = true;
        haptic(FX_SOS);
        emitToken("emergency", pressClass, nowMs, nowMs - onsetMs);
      }
      if (released) {
        if (!emergencyFired) {
          if (heldMs <= TAP_MAX_MS) {
            haptic(FX_CONFIRM);
            emitToken(CLASS_TOKENS[pressClass], pressClass, nowMs, heldMs);
            gateArmedAtMs = nowMs;  // a command keeps the window open
          } else {
            haptic(FX_REJECT);  // indeterminate duration: dropped (P7)
          }
        }
        rec = REC_WAIT_ONSET;
      }
      break;
    }
  }
}

// ---- BLE plumbing ----------------------------------------------------------

void connectCallback(uint16_t handle) {
  (void)handle;
  txLine("STA,hello,fw0.2");
}

void pollRx() {
  static char line[32];
  static uint8_t n = 0;
  while (bleuart.available()) {
    char ch = (char)bleuart.read();
    if (ch == '\n' || n >= sizeof(line) - 1) {
      line[n] = 0;
      n = 0;
      if (strcmp(line, "PING") == 0) txLine("STA,pong");
    } else if (ch != '\r') {
      line[n++] = ch;
    }
  }
}

// ---- setup / loop ----------------------------------------------------------

void setup() {
  Serial.begin(115200);
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 2000) {}
  analogReadResolution(12);

  imu.settings.accelSampleRate = 208;
  imu.settings.accelRange = 8;
  imu.settings.gyroSampleRate = 208;
  imu.settings.gyroRange = 1000;
  if (imu.begin() != 0) Serial.println("# ERROR: IMU init failed");

#if HAS_DRV2605
  if (drv.begin()) {
    drv.selectLibrary(1);
    drv.setMode(DRV2605_MODE_INTTRIG);
  } else {
    Serial.println("# ERROR: DRV2605 init failed");
  }
#endif

  Bluefruit.begin();
  Bluefruit.setName("Tactiq Ring");
  Bluefruit.Periph.setConnectCallback(connectCallback);
  bledis.setManufacturer("Tactiq bench prototype");
  bledis.setModel("XIAO nRF52840 Sense");
  bledis.begin();
  blebas.begin();
  blebas.write(100);  // stub: no fuel gauge on the bench build
  bleuart.begin();
#if TACTIQ_HID
  blehid.begin();
#endif

  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addTxPower();
  Bluefruit.Advertising.addService(bleuart);
#if TACTIQ_HID
  Bluefruit.Advertising.addAppearance(BLE_APPEARANCE_HID_KEYBOARD);
  Bluefruit.Advertising.addService(blehid);
#endif
  Bluefruit.ScanResponse.addName();
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(32, 244);
  Bluefruit.Advertising.setFastTimeout(30);
  Bluefruit.Advertising.start(0);

  nextImuUs = nextAdcUs = micros();
}

void loop() {
  uint32_t now = micros();
  uint32_t nowMs = millis();

  if ((int32_t)(now - nextImuUs) >= 0) {
    feedImu(nowMs, imu.readFloatAccelX(), imu.readFloatAccelY(),
            imu.readFloatAccelZ());
    nextImuUs += IMU_PERIOD_US;
    if ((int32_t)(now - nextImuUs) > (int32_t)(4 * IMU_PERIOD_US))
      nextImuUs = now;
  }

  if ((int32_t)(now - nextAdcUs) >= 0) {
    feedAnalog(nowMs, analogRead(PIN_FLEX1), analogRead(PIN_FLEX2),
               analogRead(PIN_FSR1), analogRead(PIN_FSR2));
    nextAdcUs += ADC_PERIOD_US;
    if ((int32_t)(now - nextAdcUs) > (int32_t)(4 * ADC_PERIOD_US))
      nextAdcUs = now;
  }

  pollRx();

  static uint32_t nextStatusMs = 30000;
  if (nowMs >= nextStatusMs) {
    nextStatusMs += 30000;
    txLine("STA,battery,100");
  }
}
