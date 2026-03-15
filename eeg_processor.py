import numpy as np
from collections import deque

FS         = 250
WIN_SIZE   = FS * 4
ADC_MID    = 511.5

BANDS = {
    "delta": (0.5, 4),
    "theta": (4,   8),
    "alpha": (8,   13),
    "beta":  (13,  30),
    "gamma": (30,  45),
}

SMOOTH_WINDOW    = 2
COMMIT_VOTES     = 2
CALIBRATION_WINS = 8   # 8 x 4s = 32 seconds


class EEGProcessor:
    def __init__(self):
        self.buffer        = deque(maxlen=WIN_SIZE)
        self.state_window  = deque(maxlen=SMOOTH_WINDOW)
        self.committed     = None
        self.baseline_stds = []
        self.threshold     = 40.0
        self.calibrated    = False

    def push(self, raw_adc):
        self.buffer.append(raw_adc - ADC_MID)
        if len(self.buffer) < WIN_SIZE:
            return None
        return self._analyse()

    def _analyse(self):
        sig    = np.array(self.buffer, dtype=np.float32)
        sig    = self._notch(sig, 50)
        sig    = self._notch(sig, 60)
        powers = self._band_powers(sig)
        total  = sum(powers.values()) + 1e-9
        ratios = {k: v / total for k, v in powers.items()}
        std    = float(np.std(sig))

        alpha  = ratios.get("alpha", 0) + 1e-9
        beta   = ratios.get("beta",  0) + 1e-9
        ab     = round(alpha / beta, 3)

        if not self.calibrated:
            self.baseline_stds.append(std)
            if len(self.baseline_stds) >= CALIBRATION_WINS:
                baseline        = np.mean(self.baseline_stds)
                spread          = np.std(self.baseline_stds)
                self.threshold = round(baseline + max(spread * 1.5, 6.0), 2)
                self.calibrated = True
                print(f"[CALIBRATED] baseline={baseline:.1f}  spread={spread:.1f}  threshold={self.threshold}")
            else:
                remaining = CALIBRATION_WINS - len(self.baseline_stds)
                print(f"[CALIBRATING] {remaining} windows left  STD:{std:.1f}")
        else:
            print(f"STD:{std:.1f} threshold:{self.threshold}  A/B:{ab}")

        raw_state   = self._classify(ratios, std)
        state, conf = self._smooth(raw_state)

        return {
            "state":      state,
            "conf":       round(conf, 3),
            "ratios":     {k: round(v, 4) for k, v in ratios.items()},
            "std":        round(std, 2),
            "ab":         ab,
            "threshold":  round(self.threshold, 2),
            "calibrated": self.calibrated,
        }

    def reset(self):
        self.buffer        = deque(maxlen=WIN_SIZE)
        self.state_window  = deque(maxlen=SMOOTH_WINDOW)
        self.committed     = None
        self.baseline_stds = []
        self.threshold     = 40.0
        self.calibrated    = False

    def _notch(self, sig, freq):
        fft   = np.fft.rfft(sig)
        freqs = np.fft.rfftfreq(len(sig), d=1.0 / FS)
        fft[(freqs >= freq - 2) & (freqs <= freq + 2)] = 0
        return np.fft.irfft(fft, n=len(sig))

    def _band_powers(self, sig):
        fft   = np.fft.rfft(sig * np.hanning(len(sig)))
        power = np.abs(fft) ** 2
        freqs = np.fft.rfftfreq(len(sig), d=1.0 / FS)
        out   = {}
        for name, (lo, hi) in BANDS.items():
            mask      = (freqs >= lo) & (freqs <= hi)
            out[name] = float(power[mask].mean()) if mask.any() else 0.0
        return out

    def _classify(self, ratios, std):
        alpha = ratios.get("alpha", 0) + 1e-9
        beta  = ratios.get("beta",  0) + 1e-9
        ab    = alpha / beta

        std_focused   = std > self.threshold
        ratio_focused = ab < 1.2  

        if std_focused or ratio_focused:
            return "focused"
        return "relaxed"

    def _smooth(self, raw):
        self.state_window.append(raw)
        focused_n = self.state_window.count("focused")
        relaxed_n = self.state_window.count("relaxed")
        if focused_n >= COMMIT_VOTES:
            self.committed = "focused"
        elif relaxed_n >= COMMIT_VOTES:
            self.committed = "relaxed"
        state = self.committed or raw
        conf  = max(focused_n, relaxed_n) / SMOOTH_WINDOW
        return state, conf