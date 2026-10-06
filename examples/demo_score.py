"""A small synthesized demo score shared by the example scripts.

This is EXAMPLE DATA, not part of the earforge package: the same generator
produces both the audio (wav/mp3) and the note events, so a seeded job plays
back in sync. Nothing in the earforge package generates or uses this.
"""

import wave
from pathlib import Path

import numpy as np

BPM = 100.0
BEATS_PER_BAR = 4
BARS = 10
SAMPLE_RATE = 32000

# I - V - vi - IV in C major, one bar each, twice through
PROGRESSION = [
    # (bass midi, chord midis)
    (36, [60, 64, 67]),  # C
    (43, [59, 62, 67]),  # G
    (45, [60, 64, 69]),  # Am
    (41, [60, 65, 69]),  # F
] * (BARS // 4)

BAR_SECONDS = BEATS_PER_BAR * 60.0 / BPM
DURATION = BARS * BAR_SECONDS


def midi_hz(midi: int) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def notes() -> list[dict]:
    """The score as earforge event-stream note dicts (start/end/pitch/instrument)."""
    out = []
    index = 0
    for bar, (bass_root, chord) in enumerate(PROGRESSION):
        t0 = bar * BAR_SECONDS
        beat = BAR_SECONDS / BEATS_PER_BAR
        for b in range(BEATS_PER_BAR):
            # drums: kick on every beat, snare on 2 and 4
            out.append(dict(index=index, instrument="drums", pitch=36,
                            start_time=t0 + b * beat, end_time=t0 + b * beat + 0.05))
            index += 1
            if b in (1, 3):
                out.append(dict(index=index, instrument="drums", pitch=38,
                                start_time=t0 + b * beat,
                                end_time=t0 + b * beat + 0.05))
                index += 1
            for e in range(2):  # eighths: hi-hat
                out.append(dict(index=index, instrument="drums", pitch=42,
                                start_time=t0 + b * beat + e * beat / 2,
                                end_time=t0 + b * beat + e * beat / 2 + 0.03))
                index += 1
        for e in range(BEATS_PER_BAR * 2):  # bass eighths on the root
            start = t0 + e * beat / 2
            out.append(dict(index=index, instrument="electric_bass",
                            pitch=bass_root, start_time=start,
                            end_time=start + beat / 2 * 0.85))
            index += 1
        # guitar: one sustained chord per bar
        for pitch in chord:
            out.append(dict(index=index, instrument="clean_electric_guitar",
                            pitch=pitch, start_time=t0, end_time=t0 + BAR_SECONDS * 0.95))
            index += 1
        # organ pad every other bar
        if bar % 2 == 0:
            for pitch in (chord[0] + 12, chord[2] + 12):
                out.append(dict(index=index, instrument="organ", pitch=pitch,
                                start_time=t0, end_time=t0 + 2 * BAR_SECONDS * 0.9))
                index += 1
    out.sort(key=lambda n: n["start_time"])
    return out


def audio() -> np.ndarray:
    """The same score rendered as a mono float32 waveform."""
    t_total = int(DURATION * SAMPLE_RATE)
    sig = np.zeros(t_total)
    rng = np.random.default_rng(7)

    def add(freq: float, start: float, dur: float, amp: float, decay: float = 4.0):
        i0 = int(start * SAMPLE_RATE)
        n = int(dur * SAMPLE_RATE)
        if i0 + n > t_total:
            n = max(0, t_total - i0)
        if n <= 0:
            return
        t = np.arange(n) / SAMPLE_RATE
        env = np.exp(-decay * t) * (1 - np.exp(-400 * t))
        sig[i0 : i0 + n] += amp * env * np.sin(2 * np.pi * freq * t)

    for note in notes():
        start = note["start_time"]
        dur = note["end_time"] - start
        if note["instrument"] == "drums":
            i0 = int(start * SAMPLE_RATE)
            n = int(dur * SAMPLE_RATE)
            if note["pitch"] == 36:  # kick: fast-decaying low sine
                add(55, start, dur + 0.1, 0.5, decay=18)
            else:  # snare / hat: filtered noise burst
                n = min(n, t_total - i0)
                if n > 0:
                    burst = rng.standard_normal(n) * np.exp(-60 * np.arange(n) / SAMPLE_RATE)
                    if note["pitch"] == 42:
                        burst = np.convolve(burst, np.ones(2) / 2, mode="same") * 0.5
                    sig[i0 : i0 + n] += 0.25 * burst
        elif note["instrument"] == "electric_bass":
            add(midi_hz(note["pitch"]), start, dur, 0.45, decay=3.0)
        elif note["instrument"] == "clean_electric_guitar":
            add(midi_hz(note["pitch"]), start, dur, 0.18, decay=1.2)
        else:
            add(midi_hz(note["pitch"]), start, dur, 0.10, decay=0.8)

    peak = np.max(np.abs(sig))
    if peak > 0:
        sig = sig / peak * 0.7
    return sig


def write_wav(path: Path) -> Path:
    sig = (audio() * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(sig.tobytes())
    return path
