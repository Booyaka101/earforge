"""Actual published MuScriptor writer, without a model or weights."""

import copy
import io

import mido
import numpy as np
import pytest
from muscriptor.utils.beats import BeatGrid, read_bar_offset

from earforge import midibridge


def note(start=3.25, instrument="electric_bass", pitch=40):
    return dict(start_time=start, end_time=start + 0.25, instrument=instrument, pitch=pitch)


def read(data):
    return mido.MidiFile(file=io.BytesIO(data))


def note_on_seconds(midi):
    time = 0
    result = []
    for msg in midi:
        time += msg.time
        if msg.type == "note_on" and msg.velocity:
            result.append(time)
    return result


def test_roundtrip_all_grid_fields():
    grid = BeatGrid(123, None, 2.0, beats=np.arange(20) * .5,
                    onset_delay=.012, beat_subdivision=2, steady=False)
    restored = midibridge.grid_from_dict(midibridge.grid_dict(grid))
    assert midibridge.grid_dict(restored) == midibridge.grid_dict(grid)
    assert isinstance(restored.beats, np.ndarray)


def test_measure_grid_finds_delay_and_subdivision():
    grid = BeatGrid(120, 4, 0, beats=np.arange(26) * .5)
    measured, subdivision = midibridge.measure_grid(grid, np.arange(48) * .25 + .012)
    assert subdivision is not None and subdivision % 2 == 0
    assert measured.onset_delay == pytest.approx(.012, abs=.005)
    assert grid.onset_delay is None


def test_measure_grid_without_beats_is_zero():
    grid, subdivision = midibridge.measure_grid(BeatGrid(120, 4, 0), [.1, .2])
    assert subdivision is None
    assert grid.onset_delay == 0


def test_worked_example_does_not_retime_raw_notes():
    notes = [note()]
    before = copy.deepcopy(notes)
    detected = BeatGrid(100, 3, .3, onset_delay=0)
    manual = dict(bpm=120, beats_per_bar=4, first_downbeat=2.0)
    metadata = midibridge.metadata(detected, manual, notes)
    midi = read(midibridge.midi_bytes(notes, detected, manual))
    assert metadata["first_downbeat"] == 2
    assert note_on_seconds(midi) == pytest.approx([3.25], abs=.001)
    assert read_bar_offset(midi) == 0
    assert notes == before
    assert detected.first_downbeat == .3


def test_onset_delay_and_pickup_headroom_are_export_only():
    notes = [note(.01), note(3.25)]
    grid = BeatGrid(120, 4, .5, onset_delay=.02)
    midi = read(midibridge.midi_bytes(notes, grid))
    offset = read_bar_offset(midi)
    assert offset == pytest.approx(1.5)
    assert note_on_seconds(midi) == pytest.approx([.01 - .02 + offset, 3.25 - .02 + offset], abs=.001)
    assert min(note_on_seconds(midi)) >= 0
    assert notes[0]["start_time"] == .01


def test_drums_have_own_sentinel_channel_and_named_track():
    notes = [note(1), note(1, "drums", 36)]
    converted, names = midibridge.to_notes(notes)
    assert [n.program for n in converted] == [33, 128]
    assert names == {33: "electric_bass", 128: "drums"}
    midi = read(midibridge.midi_bytes(notes, None))
    drum_track = next(t for t in midi.tracks if t.name == "drums")
    assert {msg.channel for msg in drum_track if msg.type == "note_on"} == {9}
    assert all(msg.channel != 9 for t in midi.tracks if t.name == "electric_bass" for msg in t if msg.type == "note_on")


@pytest.mark.parametrize("beats", [None, np.array([]), np.array([0.0])])
def test_missing_short_beat_maps_are_safe(beats):
    grid = BeatGrid(120, None, 0, beats=beats, onset_delay=0, steady=False)
    midi = read(midibridge.midi_bytes([note()], grid))
    assert note_on_seconds(midi) == pytest.approx([3.25], abs=.001)
    assert not any(m.type == "time_signature" for t in midi.tracks for m in t)


def test_auto_tempo_map_and_explicit_fixed_bpm():
    beats = np.arange(24) * .4 + np.arange(24) ** 2 * .005
    grid = BeatGrid(110, 4, 0, beats=beats, steady=False, onset_delay=0)
    notes = [note(float(t)) for t in beats]
    auto = read(midibridge.midi_bytes(notes, grid))
    fixed = read(midibridge.midi_bytes(notes, grid, {"bpm": 120}))
    assert sum(m.type == "set_tempo" for m in auto.tracks[0]) > 1
    assert sum(m.type == "set_tempo" for m in fixed.tracks[0]) == 1
    assert midibridge.metadata(grid, {}, notes)["resolved_grid"]["steady"] is False
    assert midibridge.metadata(grid, {"bpm": 120}, notes)["resolved_grid"]["steady"] is True
    assert np.array_equal(grid.beats, beats)


def test_quantize_short_note_minimum_and_no_subdivision_noop():
    notes = [dict(note(.24), end_time=.26)]
    grid = BeatGrid(120, 4, 0, onset_delay=0, beat_subdivision=2)
    midi = read(midibridge.midi_bytes(notes, grid, quantize=True))
    assert note_on_seconds(midi) == pytest.approx([.25])
    assert midi.length == pytest.approx(.5)
    grid.beat_subdivision = None
    assert midibridge.midi_bytes(notes, grid, quantize=True) == midibridge.midi_bytes(notes, grid)


def test_no_detection_does_not_claim_placeholder_is_detected():
    assert midibridge.metadata(None, {}, [note()])["resolved_grid"] is None
    midi = read(midibridge.midi_bytes([note()], None))
    assert not any(m.type == "time_signature" for t in midi.tracks for m in t)
    assert midibridge.metadata(None, {"bpm": 120, "beats_per_bar": 4}, [note()])["bpm"] == 120
