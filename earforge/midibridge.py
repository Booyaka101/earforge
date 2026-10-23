"""Model-free MuScriptor 0.4 MIDI exports from original recording-time notes."""

import dataclasses
import io

import numpy as np
from muscriptor.tokenizer.notes import DRUM_PROGRAM, Note
from muscriptor.utils.beats import BeatGrid
from muscriptor.utils.midi import ExportOptions, notes_to_midi, resolve_grid

from earforge.synth import program_for


def grid_dict(grid: BeatGrid | None) -> dict | None:
    if grid is None:
        return None
    result = dataclasses.asdict(grid)
    result["beats"] = grid.beats.tolist() if grid.beats is not None else None
    return result


def grid_from_dict(value: dict | None) -> BeatGrid | None:
    if value is None:
        return None
    fields = dict(value)
    if fields.get("beats") is not None:
        fields["beats"] = np.asarray(fields["beats"], dtype=float)
    return BeatGrid(**fields)


def export_options(manual: dict | None = None, *, quantize=False) -> ExportOptions:
    return ExportOptions(**(manual or {}), quantize=quantize)


def measure_grid(grid, onsets):
    measured = grid.with_onset_delay(onsets) if grid is not None else None
    return measured, measured.beat_subdivision if measured else None


def to_notes(notes: list[dict]) -> tuple[list[Note], dict[int, str]]:
    converted, names = [], {}
    for note in notes:
        program, drums = program_for(note["instrument"])
        program = DRUM_PROGRAM if drums else program
        names.setdefault(program, note["instrument"])
        converted.append(Note(
            is_drum=drums, program=program, pitch=note["pitch"],
            onset=note["start_time"], offset=note["end_time"],
        ))
    return converted, names


def midi_bytes(notes: list[dict], detected: BeatGrid | None, manual=None, *, quantize=False) -> bytes:
    converted, names = to_notes(notes)
    # notes_to_midi resolves internally. Never feed it a resolved/smoothed grid.
    midi = notes_to_midi(converted, program_names=names, beat_grid=detected,
                         options=export_options(manual, quantize=quantize))
    buffer = io.BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue()


def metadata(detected: BeatGrid | None, manual: dict, notes: list[dict]) -> dict:
    grid = resolve_grid(detected, export_options(manual), [n["start_time"] for n in notes])
    known = detected is not None or manual.get("bpm") is not None
    resolved = grid_dict(grid) if known else None
    return {
        "resolved_grid": resolved,
        "bpm": grid.bpm if known else None,
        "beats_per_bar": grid.beats_per_bar if known else None,
        "first_downbeat": grid.first_downbeat if known else None,
        "onset_delay": grid.onset_delay if known else None,
    }
