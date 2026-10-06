"""The MIDI compat bridge: quantize math and the native-path probe."""

import dataclasses

import numpy as np
import pytest

from earforge import midibridge

from muscriptor.events import NoteEndEvent, NoteStartEvent
from muscriptor.utils.beats import BeatGrid


class _OldModel:
    """Signature of muscriptor 0.3.x: events_to_midi_bytes without quantize."""

    def __init__(self):
        self.seen = []

    def events_to_midi_bytes(self, events, beat_grid=None):
        self.seen = list(events)
        return b"MThd-old"


class _NewModel(_OldModel):
    """Signature of muscriptor main: quantize parameter exists."""

    def __init__(self):
        super().__init__()
        self.quantize = None

    def events_to_midi_bytes(self, events, beat_grid=None, quantize=False):
        self.quantize = quantize
        return super().events_to_midi_bytes(events, beat_grid)


def test_supports_quantize_probe():
    assert midibridge.supports_quantize(_NewModel()) is True
    assert midibridge.supports_quantize(_OldModel()) is False


def _events_on_grid():
    """Notes placed a hair late on an eighth-note grid (the onset-delay case)."""
    events = []
    lag = 0.012
    index = 0
    for k in range(48):
        t = k * 0.25 + lag
        start = NoteStartEvent(pitch=60, start_time=t, index=index, instrument="acoustic_piano")
        events.append(start)
        events.append(NoteEndEvent(end_time=t + 0.1, start_event=start))
        index += 1
    return events, lag


def _steady_grid(bpm=120.0, beats_per_bar=4):
    beats = np.arange(0.0, 48 * 0.25 + 1, 0.5)
    return BeatGrid(bpm=bpm, beats_per_bar=beats_per_bar, first_downbeat=0.0, beats=beats)


def test_measure_grid_finds_delay_and_subdivision():
    events, lag = _events_on_grid()
    grid, subdivision = midibridge.measure_grid(_steady_grid(), [e.start_time for e in events if isinstance(e, NoteStartEvent)])
    assert subdivision is not None
    assert grid.onset_delay == pytest.approx(lag, abs=0.005)


def test_measure_grid_without_beats_is_zero():
    grid, subdivision = midibridge.measure_grid(
        BeatGrid(bpm=120.0, beats_per_bar=4, first_downbeat=0.0), [0.1, 0.2]
    )
    assert subdivision is None
    assert grid.onset_delay == 0.0


def test_quantize_on_old_model_snaps_onto_the_grid():
    events, lag = _events_on_grid()
    grid, subdivision = midibridge.measure_grid(
        _steady_grid(), [e.start_time for e in events if isinstance(e, NoteStartEvent)]
    )
    model = _OldModel()
    midibridge.midi_bytes(model, events, grid, quantize=True, subdivision=subdivision)

    starts = [e for e in model.seen if isinstance(e, NoteStartEvent)]
    step = 0.25  # 120 BPM, subdivision 2 -> eighth notes are 0.25 s
    for s in starts:
        # snapped to the grid in the raw timeline (delay folded back in)
        assert abs((s.start_time - grid.onset_delay) / step
                   - round((s.start_time - grid.onset_delay) / step)) < 1e-6


def test_quantize_no_subdivision_is_noop():
    events, _ = _events_on_grid()
    grid = BeatGrid(bpm=120.0, beats_per_bar=4, first_downbeat=0.0, onset_delay=0.0)
    model = _OldModel()
    out = midibridge.midi_bytes(model, events, grid, quantize=True, subdivision=None)
    assert out == b"MThd-old"
    assert model.seen[0] is events[0]  # untouched


def test_native_path_gets_quantize_flag():
    events, _ = _events_on_grid()
    model = _NewModel()
    midibridge.midi_bytes(model, events, None, quantize=True, subdivision=2)
    assert model.quantize is True


def test_snap_events_shortest_note_gets_a_full_step():
    start = NoteStartEvent(pitch=60, start_time=0.24, index=0, instrument="organ")
    events = [start, NoteEndEvent(end_time=0.26, start_event=start)]
    snapped = midibridge.snap_events(events, delay=0.0, offset=0.0, step=0.25)
    assert snapped[0].start_time == pytest.approx(0.25)
    assert snapped[1].end_time == pytest.approx(0.5)
