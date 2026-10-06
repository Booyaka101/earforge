"""ffmpeg conversion and the per-job transcription pipeline.

The worker mirrors muscriptor's own server: stream NoteStart/NoteEnd/Progress
events out of ``model.transcribe`` while collecting them, then build the MIDI
with ``model.events_to_midi_bytes`` so exported bytes are identical to what
``muscriptor transcribe`` would have written.
"""

import dataclasses
import os
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path

from muscriptor.events import NoteEndEvent, NoteStartEvent, ProgressEvent
from muscriptor.utils.beats import BeatDetectionError

from earforge import midibridge

SAMPLE_RATE = 32000

# subprocess.CREATE_NO_WINDOW, spelled for non-Windows too
_NO_WINDOW = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


class TranscriptionError(RuntimeError):
    """A job failed for a reason the user can act on; message is UI-ready."""


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def convert_audio(src: Path, dst: Path) -> float:
    """Convert any audio file to 32 kHz mono 16-bit WAV, return its duration.

    The conversion exists so the model never sees a container it cannot parse
    (the failure mode behind muscriptor issues #109/#110): ffmpeg handles every
    input format and hands the model a plain PCM wav.
    """
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise TranscriptionError(
            "ffmpeg was not found on PATH. earforge uses it to decode audio."
            " Install it from https://ffmpeg.org/download.html and reopen earforge."
        )
    if not Path(src).exists():
        raise TranscriptionError(f"uploaded file not found: {src}")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(src),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                str(dst),
            ],
            capture_output=True,
            text=True,
            errors="replace",
            creationflags=_NO_WINDOW,
            timeout=300,
        )
    except subprocess.TimeoutExpired as e:
        raise TranscriptionError(
            f"ffmpeg timed out converting '{Path(src).name}'"
        ) from e
    if proc.returncode != 0 or not dst.exists():
        detail = (proc.stderr or "").strip().splitlines()
        hint = detail[-1] if detail else "no ffmpeg diagnostic output"
        raise TranscriptionError(
            f"ffmpeg could not decode '{Path(src).name}': {hint}"
        )
    with wave.open(str(dst), "rb") as w:
        return w.getnframes() / w.getframerate()


def render_audio_at_rate(src: Path, rate: float, dst: Path) -> Path:
    """Time-stretch a wav to 1/rate speed with pitch preserved (ffmpeg atempo).

    This is what lets the original recording slow down with the synth stems
    without dropping in pitch: every playback source is pre-rendered on the
    stretched timeline. atempo's single-filter range covers our 0.5-1.0.
    """
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise TranscriptionError(
            "ffmpeg was not found on PATH, so speed control on the original"
            " audio is unavailable. Install it from https://ffmpeg.org/download.html"
        )
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(src),
                "-filter:a",
                f"atempo={rate:.6f}",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                str(dst),
            ],
            capture_output=True,
            text=True,
            errors="replace",
            creationflags=_NO_WINDOW,
            timeout=300,
        )
    except subprocess.TimeoutExpired as e:
        raise TranscriptionError(
            f"ffmpeg timed out stretching '{Path(src).name}' to {rate:.0%}"
        ) from e
    if proc.returncode != 0 or not dst.exists():
        detail = (proc.stderr or "").strip().splitlines()
        hint = detail[-1] if detail else "no ffmpeg diagnostic output"
        raise TranscriptionError(
            f"ffmpeg could not slow '{Path(src).name}' to {rate:.0%}: {hint}"
        )
    return dst


def atomic_render(render: Callable[[Path], None], dst: Path) -> Path:
    """Run a blocking render into `dst` so concurrent requests can't corrupt it.

    Two tabs asking for the same stem (or the same slowed original) would
    otherwise run two synth processes writing one file. Rendering into a
    per-process temp name and renaming is atomic on the same volume, so a
    loser's output is simply replaced by a winner's.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return dst
    fd, tmp_name = tempfile.mkstemp(
        dir=str(dst.parent), prefix=dst.stem + "-", suffix=dst.suffix
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        render(tmp)
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise TranscriptionError(f"render produced no audio for '{dst.name}'")
        try:
            os.replace(tmp, dst)
        except PermissionError:
            # Windows: a concurrent request is streaming the destination we
            # are replacing. If their file is installed, ours is redundant;
            # only a missing destination is a real failure.
            if not dst.exists():
                raise
    finally:
        tmp.unlink(missing_ok=True)
    return dst


def event_to_dict(ev: NoteStartEvent | NoteEndEvent | ProgressEvent) -> dict:
    """The SSE/JSONL wire shape, matching muscriptor's own server."""
    if isinstance(ev, ProgressEvent):
        return {"type": "progress", "completed": ev.completed, "total": ev.total}
    if isinstance(ev, NoteStartEvent):
        return {
            "type": "start",
            "pitch": ev.pitch,
            "start_time": ev.start_time,
            "index": ev.index,
            "instrument": ev.instrument,
        }
    return {
        "type": "end",
        "end_time": ev.end_time,
        "start_event_index": ev.start_event_index,
    }


def pair_events(event_dicts: list[dict]) -> list[dict]:
    """Fold a start/end event stream into complete notes.

    Tolerates a stream that was cut off (cancel, crash): a start that never
    got its end is closed 0.1 s later, matching how a truncated take is more
    useful than a dropped one. Notes are returned sorted by start time.
    """
    notes: dict[int, dict] = {}
    order: list[int] = []
    for ev in event_dicts:
        if ev.get("type") == "start":
            index = ev["index"]
            if index not in notes:
                order.append(index)
            notes[index] = {**ev, "end_time": ev["start_time"] + 0.1}
        elif ev.get("type") == "end":
            note = notes.get(ev["start_event_index"])
            if note is not None:
                note["end_time"] = ev["end_time"]
    return sorted((notes[i] for i in order), key=lambda n: n["start_time"])


def instruments_in(notes_or_events) -> list[str]:
    """Instrument names in first-seen order (used for lane ordering).

    Accepts event objects or wire dicts, whichever the caller happens to hold.
    """
    seen: dict[str, None] = {}
    for ev in notes_or_events:
        name = getattr(ev, "instrument", None)
        if name is None and isinstance(ev, dict):
            name = ev.get("instrument")
        if name:
            seen.setdefault(name, None)
    return list(seen)


def run_transcription(
    model,
    wav_path: Path,
    emit,
    cancel,
    instruments: list[str] | None = None,
) -> dict:
    """Run the model over a converted wav, emitting events via ``emit``.

    ``emit`` is called with each wire dict (progress/start/end plus earforge's
    own banner/job_* events). Returns the final job meta. Raises
    TranscriptionError on failures that should end the job.
    """
    events: list[NoteStartEvent | NoteEndEvent] = []
    for ev in model.transcribe(
        wav_path,
        instruments=instruments,
        batch_size=1,
        no_eos_is_ok=True,
    ):
        if cancel is not None and cancel.is_set():
            return {"cancelled": True}
        if isinstance(ev, ProgressEvent):
            emit(event_to_dict(ev))
            continue
        events.append(ev)
        emit(event_to_dict(ev))

    # Tempo detection with a visible fallback: muscriptor's "best-effort" mode
    # swallows the failure into a stderr warning, but the point of a practice
    # tool is that you are told when the grid could not be found.
    grid = None
    try:
        grid = model.detect_beat_grid_for(wav_path, True)
    except BeatDetectionError as e:
        emit(
            {
                "type": "banner",
                "kind": "tempo_fallback",
                "message": (
                    f"No steady tempo detected ({e}); transcription continued"
                    " without a beat grid. Exports will have no tempo or time"
                    " signature. Rubato recordings transcribe notably worse."
                ),
            }
        )

    starts = [ev.start_time for ev in events if isinstance(ev, NoteStartEvent)]
    grid, subdivision = midibridge.measure_grid(grid, starts)

    midi_bytes_ = midibridge.midi_bytes(model, events, grid)
    quantized = (
        midibridge.midi_bytes(model, events, grid, quantize=True, subdivision=subdivision)
        if subdivision is not None
        else None
    )

    found = instruments_in(
        [ev for ev in events if isinstance(ev, NoteStartEvent)]
    )

    return {
        "cancelled": False,
        "events": events,
        "midi_bytes": midi_bytes_,
        "quantized_midi_bytes": quantized,
        "instruments": found,
        "bpm": grid.bpm if grid else None,
        "beats_per_bar": grid.beats_per_bar if grid else None,
        "first_downbeat": grid.first_downbeat if grid else None,
        "onset_delay": grid.onset_delay if grid else None,
        "note_count": len(starts),
    }
