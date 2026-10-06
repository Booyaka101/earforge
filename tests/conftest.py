"""Shared fixtures: bundled binaries on PATH, temp library, stub model, audio."""

import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def _bundled_bin_dirs() -> list[Path]:
    dirs = []
    if TOOLS.exists():
        for exe in TOOLS.rglob("ffmpeg.exe"):
            dirs.append(exe.parent)
        for exe in TOOLS.rglob("fluidsynth.exe"):
            dirs.append(exe.parent)
    return dirs


@pytest.fixture(scope="session")
def tools_path():
    """Bundled ffmpeg/fluidsynth prepended to PATH, returns what was found."""
    found = {}
    old = os.environ["PATH"]
    dirs = _bundled_bin_dirs()
    if dirs:
        os.environ["PATH"] = os.pathsep.join(str(d) for d in dirs) + os.pathsep + old
    try:
        found["ffmpeg"] = shutil.which("ffmpeg")
        found["fluidsynth"] = shutil.which("fluidsynth")
        yield found
    finally:
        os.environ["PATH"] = old


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("EARFORGE_HOME", str(tmp_path / "home"))
    from earforge.library import Library

    lib = Library()
    lib.init()
    return lib


def make_wav(path: Path, seconds: float = 2.0, sr: int = 44100) -> Path:
    """A click on each beat plus a C4-E4-G4 arpeggio, stereo, as a real file."""
    t = np.arange(int(seconds * sr)) / sr
    sig = np.zeros_like(t)
    for beat in range(int(seconds) + 1):
        i = int(beat * sr)
        if i + sr // 40 < len(sig):
            sig[i : i + sr // 40] = np.linspace(1, 0, sr // 40)
    for k, f in enumerate([261.63, 329.63, 392.0]):
        on = int((0.2 + 0.5 * k) * sr)
        n = int(0.4 * sr)
        if on + n < len(sig):
            sig[on : on + n] += 0.4 * np.sin(2 * np.pi * f * t[:n])
    stereo = np.stack([sig, sig], axis=1)
    import soundfile as sf

    sf.write(str(path), stereo, sr)
    return path


def make_mp3(path: Path, wav: Path) -> Path | None:
    """Encode an mp3 with ffmpeg; None when the build lacks an encoder."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(wav), str(path)],
        capture_output=True, text=True,
    )
    return path if proc.returncode == 0 else None


class StubModel:
    """Stands in for TranscriptionModel: same call shapes, deterministic notes.

    Emits a C major scale on electric_bass plus drum hits, in 4 chunks with
    ProgressEvents, exactly like the real decode stream interleaves them.
    """

    def __init__(self, chunk_delay: float = 0.0, no_tempo: bool = False):
        self.chunk_delay = chunk_delay
        self.no_tempo = no_tempo
        self.calls = []

    def transcribe(self, audio, instruments=None, batch_size=1, no_eos_is_ok=True):
        import time

        from muscriptor.events import NoteEndEvent, NoteStartEvent, ProgressEvent

        self.calls.append({"audio": str(audio), "instruments": instruments})
        scale = [38, 40, 41, 43, 45, 47, 48, 50]  # D2..D3, bass register
        yield ProgressEvent(completed=0, total=4)
        for chunk in range(4):
            if self.chunk_delay:
                time.sleep(self.chunk_delay)
            for k, pitch in enumerate(scale):
                t0 = chunk * 2.0 + k * 0.2
                start = NoteStartEvent(
                    pitch=pitch, start_time=t0, index=chunk * 8 + k,
                    instrument="electric_bass",
                )
                yield start
                yield NoteEndEvent(end_time=t0 + 0.18, start_event=start)
            for k, pitch in enumerate([36, 38, 42]):
                t0 = chunk * 2.0 + k * 0.1
                start = NoteStartEvent(
                    pitch=pitch, start_time=t0,
                    index=32 + chunk * 3 + k, instrument="drums",
                )
                yield start
                yield NoteEndEvent(end_time=t0 + 0.05, start_event=start)
            yield ProgressEvent(completed=chunk + 1, total=4)

    def detect_beat_grid_for(self, audio, mode):
        if self.no_tempo:
            from muscriptor.utils.beats import BeatDetectionError

            raise BeatDetectionError(
                "The recording has no fixed tempo (beats deviate 96 ms RMS"
                " from a constant 120.0 BPM)"
            )
        from muscriptor.utils.beats import BeatGrid

        return BeatGrid(bpm=120.0, beats_per_bar=4, first_downbeat=0.0)

    def events_to_midi_bytes(self, events, beat_grid=None, quantize=False):
        from mido import Message, MidiFile, MidiTrack

        midi = MidiFile(ticks_per_beat=480)
        track = MidiTrack()
        midi.tracks.append(track)
        track.append(Message("program_change", channel=0, program=33, time=0))
        open_notes = {}
        for ev in events:
            if type(ev).__name__ == "NoteStartEvent":
                open_notes[ev.index] = ev
                track.append(
                    Message("note_on", channel=0, note=ev.pitch, velocity=100,
                            time=int(ev.start_time * 960))
                )
            elif type(ev).__name__ == "NoteEndEvent":
                start = open_notes.pop(ev.start_event_index, None)
                if start is not None:
                    track.append(
                        Message("note_off", channel=0, note=start.pitch, velocity=0,
                                time=int((ev.end_time - start.start_time) * 960))
                    )
        import io

        buf = io.BytesIO()
        midi.save(file=buf)
        return buf.getvalue()


def sse_events(text: str) -> list[dict]:
    """Parse an SSE body into its JSON payloads."""
    import json

    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: "):]))
    return out
