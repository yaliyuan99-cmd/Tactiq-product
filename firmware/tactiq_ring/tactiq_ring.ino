/*
  Tactiq ring — bench acquisition firmware (build-plan step 1).

  Board:   Seeed XIAO nRF52840 Sense (built-in LSM6DS3TR-C IMU)
  Sensors: 2x flex sensor + 2x FSR pressure pad on the ADC pins
  Output:  timestamped multichannel sample stream over USB serial

  Line format (CSV, one sample per line):
    I,<t_us>,<ax_g>,<ay_g>,<az_g>,<gx_dps>,<gy_dps>,<gz_dps>   ~208 Hz
    A,<t_us>,<flex1>,<flex2>,<fsr1>,<fsr2>                      100 Hz
  Lines starting with '#' are comments/metadata for the host.

  Wiring (see README for the divider diagrams):
    A0  flex 1 divider tap     A2  FSR 1 divider tap
    A1  flex 2 divider tap     A3  FSR 2 divider tap

  The sketch runs fine with nothing wired to A0-A3 (the pins float),
  so the IMU + host pipeline can be tested with just the bare board.
*/

#include <LSM6DS3.h>
#include <Wire.h>

const int PIN_FLEX1 = A0;
const int PIN_FLEX2 = A1;
const int PIN_FSR1  = A2;
const int PIN_FSR2  = A3;

// 208 Hz is the LSM6DS3's native ODR closest to the 200 Hz target.
const uint32_t IMU_PERIOD_US = 4808;   // ~208 Hz
const uint32_t ADC_PERIOD_US = 10000;  // 100 Hz

// Blink the user LED when |accel| spikes — bench feedback that a tap
// registered, before any classifier exists. LED is active-low on the XIAO.
const float TAP_BLINK_G = 1.35f;
const uint32_t BLINK_US = 60000;

LSM6DS3 imu(I2C_MODE, 0x6A);  // IMU on the internal I2C bus

uint32_t nextImuUs, nextAdcUs;
uint32_t ledOffUs = 0;
bool ledOn = false;

void setup() {
  Serial.begin(115200);  // USB CDC ignores baud; value is nominal
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 4000) {}

  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);  // off

  analogReadResolution(12);  // 0..4095 counts

  imu.settings.accelSampleRate = 208;
  imu.settings.accelRange      = 8;     // g — tap transients clip at 2 g
  imu.settings.gyroSampleRate  = 208;
  imu.settings.gyroRange       = 1000;  // dps

  if (imu.begin() != 0) {
    Serial.println("# ERROR: IMU init failed");
  }

  Serial.println("# tactiq-ring fw 0.1");
  Serial.println("# fmt I,t_us,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps");
  Serial.println("# fmt A,t_us,flex1,flex2,fsr1,fsr2");

  nextImuUs = nextAdcUs = micros();
}

void printImuLine(uint32_t t) {
  float ax = imu.readFloatAccelX();
  float ay = imu.readFloatAccelY();
  float az = imu.readFloatAccelZ();

  Serial.print('I'); Serial.print(',');
  Serial.print(t);   Serial.print(',');
  Serial.print(ax, 3); Serial.print(',');
  Serial.print(ay, 3); Serial.print(',');
  Serial.print(az, 3); Serial.print(',');
  Serial.print(imu.readFloatGyroX(), 1); Serial.print(',');
  Serial.print(imu.readFloatGyroY(), 1); Serial.print(',');
  Serial.println(imu.readFloatGyroZ(), 1);

  float mag = sqrtf(ax * ax + ay * ay + az * az);
  if (mag > TAP_BLINK_G) {
    digitalWrite(LED_BUILTIN, LOW);  // on
    ledOn = true;
    ledOffUs = t + BLINK_US;
  }
}

void printAdcLine(uint32_t t) {
  Serial.print('A'); Serial.print(',');
  Serial.print(t);   Serial.print(',');
  Serial.print(analogRead(PIN_FLEX1)); Serial.print(',');
  Serial.print(analogRead(PIN_FLEX2)); Serial.print(',');
  Serial.print(analogRead(PIN_FSR1));  Serial.print(',');
  Serial.println(analogRead(PIN_FSR2));
}

void loop() {
  uint32_t now = micros();

  // Signed subtraction keeps the schedule correct across micros() rollover
  // (~71 min); the host unwraps the timestamps on its side.
  if ((int32_t)(now - nextImuUs) >= 0) {
    printImuLine(now);
    nextImuUs += IMU_PERIOD_US;
    if ((int32_t)(now - nextImuUs) > (int32_t)(4 * IMU_PERIOD_US)) {
      nextImuUs = now;  // fell behind (host stalled) — resync, don't burst
    }
  }

  if ((int32_t)(now - nextAdcUs) >= 0) {
    printAdcLine(now);
    nextAdcUs += ADC_PERIOD_US;
    if ((int32_t)(now - nextAdcUs) > (int32_t)(4 * ADC_PERIOD_US)) {
      nextAdcUs = now;
    }
  }

  if (ledOn && (int32_t)(now - ledOffUs) >= 0) {
    digitalWrite(LED_BUILTIN, HIGH);
    ledOn = false;
  }
}
