"""Sample sources for the capture harness.

Two interchangeable sources produce the same record types:

  SerialSource — the real ring firmware over USB serial (build-plan step 1).
  SimSource    — a synthetic generator so the whole pipeline runs before any
                 hardware exists. Simulated sessions are flagged in
                 session.json and must never feed the paper's results.

Records carry the device clock (t_us, microseconds, unwrapped past the
32-bit micros() rollover). Sources also expose now_us(), an estimate of the
current device-clock reading, which capture uses to timestamp prompts on the
same clock as the samples.

The simulator supports four event kinds, enough to exercise every pipeline
stage: prompted taps (capture), sustained holds (the P6 emergency grammar),
deliberate wake squeezes and incidental pressure noise (the P9 gate and its
false-activation sweep). Incidental press durations are drawn exponentially,
which by construction reproduces the paper's section 3.8 model
f(tau) = f0 * exp(-tau/tau0).
"""

import math
import random
import time
from typing import NamedTuple


class ImuSample(NamedTuple):
    t_us: int
    ax: float  # g
    ay: float
    az: float
    gx: float  # dps
    gy: float
    gz: float


class AnalogSample(NamedTuple):
    t_us: int
    flex1: int  # raw 12-bit counts
    flex2: int
    fsr1: int
    fsr2: int


def _host_us() -> int:
    return int(time.monotonic() * 1_000_000)


class SerialSource:
    """Parses the firmware's I/A line stream from a USB serial port."""

    def __init__(self, port: str = "auto", baud: int = 115200):
        import serial  # deferred so --sim works without pyserial installed

        if port == "auto":
            port = self._autodetect()
        self.port = port
        self._ser = serial.Serial(port, baud, timeout=0)
        self._buf = b""
        self._last_raw_t = None
        self._wrap_offset = 0          # accumulated 2^32 us rollovers
        self._clock_offset = None      # host_us - device_us, min-tracked
        self.header_lines: list[str] = []
        self.parse_errors = 0

    @staticmethod
    def _autodetect() -> str:
        from serial.tools import list_ports

        candidates = []
        for p in list_ports.comports():
            text = " ".join(filter(None, [p.description, p.manufacturer or ""]))
            if p.vid == 0x2886 or "Seeed" in text or "XIAO" in text:
                candidates.append(p.device)
        if not candidates:
            # fall back to anything that looks like a USB CDC device
            candidates = [p.device for p in list_ports.comports()
                          if "usbmodem" in p.device or "ttyACM" in p.device]
        if not candidates:
            ports = ", ".join(p.device for p in list_ports.comports()) or "none"
            raise RuntimeError(
                f"No XIAO nRF52840 found (ports seen: {ports}). "
                "Plug the ring in, or pass --port explicitly.")
        return candidates[0]

    def _unwrap(self, raw_t: int) -> int:
        if self._last_raw_t is not None and raw_t < self._last_raw_t - 2**31:
            self._wrap_offset += 2**32
        self._last_raw_t = raw_t
        return raw_t + self._wrap_offset

    def read(self):
        """Drain available bytes; return parsed samples (non-blocking)."""
        n = self._ser.in_waiting
        if n:
            self._buf += self._ser.read(n)
        samples = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            text = line.decode("ascii", errors="replace").strip()
            if not text:
                continue
            if text.startswith("#"):
                self.header_lines.append(text)
                continue
            try:
                fields = text.split(",")
                kind = fields[0]
                t = self._unwrap(int(fields[1]))
                if kind == "I":
                    samples.append(ImuSample(t, *map(float, fields[2:8])))
                elif kind == "A":
                    samples.append(AnalogSample(t, *map(int, fields[2:6])))
                else:
                    self.parse_errors += 1
                    continue
                # Arrival lags true sample time by USB buffering, so the
                # minimum observed (host - device) best estimates the offset.
                est = _host_us() - t
                if self._clock_offset is None or est < self._clock_offset:
                    self._clock_offset = est
            except (ValueError, IndexError, TypeError):
                self.parse_errors += 1  # torn line at connect time etc.
        return samples

    def now_us(self) -> int:
        if self._clock_offset is None:
            raise RuntimeError("No samples received yet — cannot map clocks.")
        return _host_us() - self._clock_offset

    def close(self):
        self._ser.close()


# --- Simulator ---------------------------------------------------------------

# Illustrative per-contact signal geometry, loosely following section 2.6:
# flex1 rises as the thumb reaches toward the pinky, flex2 falls; tip
# contacts curl slightly more and land on FSR pad 1, base contacts on pad 2;
# the inertial transient weakens toward the shorter fingers. Within-finger
# pairs share flex values, reproducing the paper's predicted confusion
# structure. Synthetic data is for pipeline testing only — not results.
_FINGER_FLEX = {"index": (120, 420), "middle": (220, 320),
                "ring": (320, 220), "pinky": (420, 120)}
_FINGER_AMP = {"index": 1.15, "middle": 1.05, "ring": 0.95, "pinky": 0.85}

_BASE_FLEX = (1500, 1520)
_BASE_FSR = (30, 28)

ACCEL_TAIL_S = 0.15  # the damped oscillation dies out within this


class _Event:
    """One synthetic actuation: envelope on flex/FSR + accel transient."""

    def __init__(self, at_us: int, flex_delta=(0.0, 0.0), fsr_peak=(0.0, 0.0),
                 amp_g=0.0, rise=0.030, hold=0.080, fall=0.060):
        self.at_us = at_us
        self.flex_delta = flex_delta
        self.fsr_peak = fsr_peak
        self.amp_g = amp_g
        self.rise, self.hold, self.fall = rise, hold, fall
        self.duration = rise + hold + fall

    def envelope(self, t_rel: float) -> float:
        if t_rel < 0 or t_rel > self.duration:
            return 0.0
        if t_rel < self.rise:
            return t_rel / self.rise
        if t_rel < self.rise + self.hold:
            return 1.0
        return 1.0 - (t_rel - self.rise - self.hold) / self.fall

    def accel(self, t_rel: float) -> float:
        if t_rel < 0 or t_rel > ACCEL_TAIL_S or self.amp_g == 0.0:
            return 0.0
        return self.amp_g * math.exp(-t_rel / 0.040) * \
            math.sin(2 * math.pi * 55 * t_rel)


def _contact_params(finger: str, knuckle: str, rng: random.Random) -> dict:
    f1, f2 = _FINGER_FLEX[finger]
    tip = knuckle == "top"
    jitter = lambda v: v * rng.gauss(1.0, 0.08)
    return {
        "flex_delta": (jitter(f1 + (60 if tip else 0)), jitter(f2)),
        "fsr_peak": (jitter(1200) if tip else jitter(60),
                     jitter(60) if tip else jitter(1000)),
        "amp_g": jitter(_FINGER_AMP[finger] + (0.10 if tip else 0.0)),
    }


class SimSource:
    """Synthetic sample stream on a virtual clock (speed x real time)."""

    IMU_PERIOD_US = 4808
    ADC_PERIOD_US = 10000

    def __init__(self, speed: float = 1.0, seed: int | None = None):
        self.speed = speed
        self.rng = random.Random(seed)
        self._t0 = time.monotonic()
        self._next_imu = 0
        self._next_adc = 0
        self._events: list[_Event] = []
        # incidental (idle-wear) noise, off unless enabled
        self._idle_rate_hz = 0.0
        self._idle_next_us = None
        self.header_lines = ["# simulated source"]
        self.parse_errors = 0

    def now_us(self) -> int:
        return int((time.monotonic() - self._t0) * self.speed * 1_000_000)

    # -- event scheduling ----------------------------------------------------

    def expect_tap(self, contact, at_us: int, hold_s: float | None = None):
        """A prompted thumb-to-finger contact; hold_s > default for P6 holds."""
        p = _contact_params(contact.finger, contact.knuckle, self.rng)
        self._events.append(_Event(
            at_us, p["flex_delta"], p["fsr_peak"], p["amp_g"],
            hold=hold_s if hold_s is not None else 0.080))

    def expect_squeeze(self, at_us: int, hold_s: float = 0.6):
        """Deliberate wake squeeze: both FSR pads pressed, little motion."""
        j = lambda v: v * self.rng.gauss(1.0, 0.08)
        self._events.append(_Event(
            at_us, (j(30), j(30)), (j(1300), j(1300)), amp_g=0.15,
            rise=0.050, hold=max(0.1, self.rng.gauss(hold_s, 0.1)),
            fall=0.080))

    def enable_idle_noise(self, events_per_hour: float = 240.0,
                          mean_dur_s: float = 0.11,
                          both_pad_fraction: float = 0.30):
        """Incidental pressure noise for the false-activation test.

        Durations are exponential(mean_dur_s), so gate false activations
        follow f(tau) = f0 * exp(-tau / mean_dur_s) — the section 3.8 model.
        """
        self._idle_rate_hz = events_per_hour / 3600.0
        self._idle_mean_dur = mean_dur_s
        self._idle_both = both_pad_fraction
        self._idle_next_us = self._draw_idle_gap(0)

    def _draw_idle_gap(self, from_us: int) -> int:
        return from_us + int(self.rng.expovariate(self._idle_rate_hz) * 1e6)

    def _schedule_idle(self, up_to_us: int):
        while self._idle_next_us is not None and self._idle_next_us <= up_to_us:
            at = self._idle_next_us
            dur = self.rng.expovariate(1.0 / self._idle_mean_dur)
            peak = self.rng.uniform(600, 1500)
            if self.rng.random() < self._idle_both:
                fsr = (peak, peak * self.rng.uniform(0.7, 1.0))
            else:
                fsr = (peak, 0.0) if self.rng.random() < 0.5 else (0.0, peak)
            self._events.append(_Event(
                at, (0.0, 0.0), fsr, amp_g=self.rng.uniform(0.05, 0.25),
                rise=0.020, hold=dur, fall=0.040))
            self._idle_next_us = self._draw_idle_gap(at)

    # -- sample synthesis ----------------------------------------------------

    def _active(self, t_us: int):
        self._events = [e for e in self._events
                        if (t_us - e.at_us) / 1e6 < e.duration + 1.0]
        return [e for e in self._events
                if 0 <= (t_us - e.at_us) / 1e6 <= max(e.duration, ACCEL_TAIL_S)]

    def _imu_at(self, t_us: int) -> ImuSample:
        g = self.rng.gauss
        ax, ay, az = g(0, 0.008), g(0, 0.008), 1.0 + g(0, 0.008)
        gx, gy, gz = g(0, 0.5), g(0, 0.5), g(0, 0.5)
        for e in self._active(t_us):
            a = e.accel((t_us - e.at_us) / 1_000_000)
            az += a
            ax += 0.4 * a
            gy += 60.0 * a
        return ImuSample(t_us, round(ax, 3), round(ay, 3), round(az, 3),
                         round(gx, 1), round(gy, 1), round(gz, 1))

    def _adc_at(self, t_us: int) -> AnalogSample:
        g = self.rng.gauss
        flex = [_BASE_FLEX[0] + g(0, 6), _BASE_FLEX[1] + g(0, 6)]
        fsr = [_BASE_FSR[0] + g(0, 4), _BASE_FSR[1] + g(0, 4)]
        for e in self._active(t_us):
            env = e.envelope((t_us - e.at_us) / 1_000_000)
            flex[0] += env * e.flex_delta[0]
            flex[1] += env * e.flex_delta[1]
            fsr[0] += env * e.fsr_peak[0]
            fsr[1] += env * e.fsr_peak[1]
        clip = lambda v: max(0, min(4095, int(v)))
        return AnalogSample(t_us, clip(flex[0]), clip(flex[1]),
                            clip(fsr[0]), clip(fsr[1]))

    def _emit_due(self, until_us: int):
        self._schedule_idle(until_us)
        samples = []
        while self._next_imu <= until_us or self._next_adc <= until_us:
            if self._next_imu <= self._next_adc:
                samples.append(self._imu_at(self._next_imu))
                self._next_imu += self.IMU_PERIOD_US
            else:
                samples.append(self._adc_at(self._next_adc))
                self._next_adc += self.ADC_PERIOD_US
        return samples

    def read(self):
        """Generate all samples due between the last call and now."""
        return self._emit_due(self.now_us())

    def generate_until(self, t_us: int, chunk_s: float = 10.0):
        """Batch mode: yield chunks up to t_us, ignoring the wall clock.

        Used by idle-wear generation, where hours of virtual stream are
        produced as fast as the CPU allows.
        """
        step = int(chunk_s * 1e6)
        while self._next_imu < t_us or self._next_adc < t_us:
            yield self._emit_due(min(t_us, self._next_imu + step))

    def close(self):
        pass
