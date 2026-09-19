"""Soft two-note chime for a successful scan (generated once, played asynchronously)."""
import math
import struct
import wave
import winsound
from pathlib import Path

CHIME = Path(__file__).resolve().parent.parent / "data" / "chime.wav"
RATE = 44100


def _make_chime(path: Path):
    notes = [(660.0, 0.0), (990.0, 0.09)]  # a fifth apart, the second one slightly later
    length = 0.55
    samples = []
    for i in range(int(RATE * length)):
        t = i / RATE
        v = 0.0
        for freq, start in notes:
            if t >= start:
                dt = t - start
                attack = min(1.0, dt / 0.008)  # no click at the start
                decay = math.exp(-dt * 7.0)
                # A touch of the octave makes it sound like a small bell rather than a beep.
                v += attack * decay * (math.sin(2 * math.pi * freq * dt) + 0.25 * math.sin(4 * math.pi * freq * dt))
        samples.append(int(max(-1.0, min(1.0, v * 0.18)) * 32767))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def chime():
    try:
        if not CHIME.exists():
            _make_chime(CHIME)
        winsound.PlaySound(str(CHIME), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass
