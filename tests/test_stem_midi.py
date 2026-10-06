"""Stem MIDI writing, instrument-to-program mapping, and rate scaling."""

import io

import pytest
from mido import MidiFile

from earforge import synth
from earforge.synth import program_for, scaled_notes, stem_midi_bytes


def _read_midi(data: bytes) -> MidiFile:
    return MidiFile(file=io.BytesIO(data))


def test_program_for_matches_mt3_group_representatives():
    # group 8 is [33, 34, 36, ...]; its first program is the representative
    assert program_for("electric_bass") == (33, False)
    assert program_for("acoustic_piano") == (0, False)
    assert program_for("drums") == (0, True)
    assert program_for("program_42") == (42, False)


def test_stem_midi_header_and_program():
    notes = [
        {"start_time": 0.5, "end_time": 0.68, "pitch": 38},
        {"start_time": 1.0, "end_time": 1.18, "pitch": 40},
    ]
    data = stem_midi_bytes(notes, program=33, is_drums=False)
    assert data[:4] == b"MThd"
    midi = _read_midi(data)
    msgs = [m for m in midi.tracks[0] if not m.is_meta]
    assert msgs[0].type == "program_change"
    assert msgs[0].program == 33
    assert msgs[0].channel == 0
    ons = [m for m in msgs if m.type == "note_on"]
    assert len(ons) == 2
    assert [m.note for m in ons] == [38, 40]


def test_stem_midi_drums_go_to_channel_10():
    notes = [{"start_time": 0.0, "end_time": 0.05, "pitch": 36}]
    msgs = [m for m in _read_midi(stem_midi_bytes(notes, 0, True)).tracks[0]
            if not m.is_meta]
    assert all(m.channel == 9 for m in msgs)


def test_stem_midi_times_map_960_ticks_per_second():
    notes = [{"start_time": 1.0, "end_time": 2.0, "pitch": 60}]
    msgs = [m for m in _read_midi(stem_midi_bytes(notes, 0, False)).tracks[0]
            if not m.is_meta]
    note_on = next(m for m in msgs if m.type == "note_on")
    # program_change has time 0, so the note_on delta is the whole offset
    assert note_on.time == 960


def test_scaled_notes_stretches_by_inverse_rate():
    notes = [{"start_time": 1.0, "end_time": 2.0, "pitch": 60}]
    slow = scaled_notes(notes, 0.5)
    assert slow[0]["start_time"] == pytest.approx(2.0)
    assert slow[0]["end_time"] == pytest.approx(4.0)
    assert slow[0]["pitch"] == 60


def test_retriggered_pitch_never_overlaps():
    # same pitch twice with a zero gap: the off must precede the next on
    notes = [
        {"start_time": 0.0, "end_time": 0.5, "pitch": 40},
        {"start_time": 0.5, "end_time": 1.0, "pitch": 40},
    ]
    msgs = [m for m in _read_midi(stem_midi_bytes(notes, 33, False)).tracks[0]
            if not m.is_meta]
    kinds = [(m.type, m.note) for m in msgs if m.type in ("note_on", "note_off")]
    assert kinds == [("note_on", 40), ("note_off", 40), ("note_on", 40), ("note_off", 40)]


def test_render_stem_without_fluidsynth(tmp_path, monkeypatch):
    monkeypatch.setattr(synth.shutil, "which", lambda name: None)
    with pytest.raises(synth.SynthUnavailable) as e:
        synth.render_stem([{"start_time": 0.0, "end_time": 0.1, "pitch": 40}],
                          "electric_bass", 1.0, tmp_path / "x.wav")
    assert "fluidsynth" in str(e.value)
