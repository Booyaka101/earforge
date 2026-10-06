"""Per-instrument synth stems rendered through fluidsynth.

A stem is a plain WAV of one instrument's notes, synthesized from a small
per-instrument MIDI file. Practice tempo works by scaling the note times
before rendering: a slower stem is the same pitches with more space between
them, so nothing is pitch-shifted. That is the one thing playbackRate on the
original audio cannot do, and why the original audibly drops in pitch at
reduced speed while the stems do not.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from mido import Message, MidiFile, MidiTrack, MetaMessage, bpm2tempo

from muscriptor.soundfonts import SF2_URL
from muscriptor.tokenizer.mt3 import MT3_FULL_PLUS_GROUP_NAMES
from muscriptor.tokenizer.mt3 import get_group_program_map
from muscriptor.utils.download import download_if_necessary

from earforge.transcribe import pair_events

_SAMPLE_RATE = 44100
_TICKS_PER_SECOND = 960  # 480 ticks per beat at the 120 BPM default tempo

_NO_WINDOW = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

_GROUP_PROGRAMS = get_group_program_map(
    "MT3_FULL_PLUS", misc_programs="SINGLETON_GROUPS", is_mt3=True
)


class SynthUnavailable(RuntimeError):
    """fluidsynth is missing; stems are disabled but the app keeps working."""


class StemError(RuntimeError):
    """fluidsynth ran but produced nothing usable."""


def fluidsynth_path() -> str | None:
    return shutil.which("fluidsynth")


def available() -> bool:
    return fluidsynth_path() is not None


def program_for(instrument: str) -> tuple[int, bool]:
    """(GM program, is_drums) for a decoded instrument name.

    Uses the representative program of the MT3_FULL_PLUS group, the same
    convention as TranscriptionModel._program_for_instrument.
    """
    if instrument == "drums":
        return 0, True
    gid = MT3_FULL_PLUS_GROUP_NAMES.get(instrument)
    if gid is not None:
        programs = _GROUP_PROGRAMS.get(gid)
        if programs:
            return programs[0], False
    if instrument.startswith("program_"):
        try:
            return int(instrument.removeprefix("program_")), False
        except ValueError:
            pass
    return 0, False


def stem_midi_bytes(notes: list[dict], program: int, is_drums: bool) -> bytes:
    """One-instrument MIDI from paired note dicts (start_time/end_time/pitch).

    Times are absolute seconds; the file carries a 120 BPM tempo so ticks map
    960 per second regardless of what the transcription's own grid was.
    """
    channel = 9 if is_drums else 0
    midi = MidiFile(ticks_per_beat=480)
    track = MidiTrack()
    midi.tracks.append(track)
    track.append(MetaMessage("set_tempo", tempo=bpm2tempo(120), time=0))
    track.append(
        Message("program_change", channel=channel, program=program & 0x7F, time=0)
    )

    pending: list[tuple[int, int, int]] = []  # (tick, pitch, on/off 1/0)
    for note in notes:
        start = max(0.0, float(note["start_time"]))
        end = max(start + 0.01, float(note["end_time"]))
        pending.append((round(start * _TICKS_PER_SECOND), note["pitch"], 1))
        pending.append((round(end * _TICKS_PER_SECOND), note["pitch"], 0))
    # offs first at equal ticks so a retrigger of the same pitch never overlaps
    pending.sort(key=lambda t: (t[0], t[2]))

    now = 0
    for tick, pitch, is_on in pending:
        delta = tick - now
        now = tick
        kind = "note_on" if is_on else "note_off"
        velocity = 100 if is_on else 0
        track.append(
            Message(kind, channel=channel, note=pitch & 0x7F, velocity=velocity, time=delta)
        )
    buf = tempfile.SpooledTemporaryFile(max_size=1 << 20)
    midi.save(file=buf)
    buf.seek(0)
    return buf.read()


def scaled_notes(notes: list[dict], rate: float) -> list[dict]:
    """Notes with times stretched by 1/rate (rate 0.5 plays half speed)."""
    return [
        {**n, "start_time": n["start_time"] / rate, "end_time": n["end_time"] / rate}
        for n in notes
    ]


def render_stem(
    notes: list[dict],
    instrument: str,
    rate: float,
    out_path: Path,
    soundfont: Path | None = None,
) -> Path:
    """Render one instrument's notes to ``out_path`` (44.1 kHz mono WAV)."""
    binary = fluidsynth_path()
    if binary is None:
        raise SynthUnavailable(
            "fluidsynth was not found on PATH, so instrument stems are disabled."
            " The piano roll and the original audio still work. Install"
            " fluidsynth (https://github.com/FluidSynth/fluidsynth/releases)"
            " to hear synthesized instruments."
        )
    if soundfont is None:
        # ungated HF repo, downloaded once and cached by muscriptor's util
        soundfont = download_if_necessary(SF2_URL)
    program, is_drums = program_for(instrument)
    midi_bytes = stem_midi_bytes(scaled_notes(notes, rate), program, is_drums)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
        tmp.write(midi_bytes)
        midi_tmp = tmp.name
    # Options must precede the positional soundfont/MIDI arguments: fluidsynth
    # >= 2.5 silently ignores trailing options (upstream hit the same thing).
    try:
        proc = subprocess.run(
            [
                binary,
                "-ni",
                "-g",
                "1.0",
                "-R",
                "0",
                "-C",
                "0",
                "-F",
                str(out_path),
                "-r",
                str(_SAMPLE_RATE),
                str(soundfont),
                midi_tmp,
            ],
            capture_output=True,
            creationflags=_NO_WINDOW,
            timeout=300,
        )
    except subprocess.TimeoutExpired as e:
        Path(midi_tmp).unlink(missing_ok=True)
        raise StemError(
            f"fluidsynth timed out rendering the '{instrument}' stem"
        ) from e
    Path(midi_tmp).unlink(missing_ok=True)
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        detail = proc.stderr.decode(errors="replace").strip().splitlines()
        raise StemError(
            f"fluidsynth failed to render the '{instrument}' stem"
            + (f": {detail[-1]}" if detail else "")
        )
    return out_path
