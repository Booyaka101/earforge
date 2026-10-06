"""Pairing the SSE start/end stream into notes, and wire shapes."""

import pytest

from earforge.transcribe import event_to_dict, instruments_in, pair_events

from muscriptor.events import NoteEndEvent, NoteStartEvent, ProgressEvent


def _stream_dicts():
    start1 = NoteStartEvent(pitch=40, start_time=0.5, index=0, instrument="electric_bass")
    start2 = NoteStartEvent(pitch=43, start_time=1.0, index=1, instrument="electric_bass")
    start3 = NoteStartEvent(pitch=38, start_time=0.75, index=2, instrument="drums")
    return [
        event_to_dict(ProgressEvent(completed=0, total=2)),
        event_to_dict(start1),
        event_to_dict(start3),
        event_to_dict(ProgressEvent(completed=1, total=2)),
        event_to_dict(NoteEndEvent(end_time=0.8, start_event=start3)),
        event_to_dict(NoteEndEvent(end_time=0.9, start_event=start1)),
        event_to_dict(start2),
        event_to_dict(NoteEndEvent(end_time=1.4, start_event=start2)),
        event_to_dict(ProgressEvent(completed=2, total=2)),
    ]


def test_event_to_dict_shapes():
    d = event_to_dict(
        NoteStartEvent(pitch=40, start_time=0.5, index=3, instrument="drums")
    )
    assert d == {
        "type": "start", "pitch": 40, "start_time": 0.5,
        "index": 3, "instrument": "drums",
    }
    start = NoteStartEvent(pitch=40, start_time=0.5, index=3, instrument="drums")
    e = event_to_dict(NoteEndEvent(end_time=0.9, start_event=start))
    assert e == {"type": "end", "end_time": 0.9, "start_event_index": 3}
    p = event_to_dict(ProgressEvent(completed=1, total=4))
    assert p == {"type": "progress", "completed": 1, "total": 4}


def test_pairing_matches_every_start_with_its_end():
    notes = pair_events(_stream_dicts())
    by_index = {n["index"]: n for n in notes}
    assert set(by_index) == {0, 1, 2}
    assert by_index[0]["end_time"] == 0.9
    assert by_index[1]["end_time"] == 1.4
    assert by_index[2]["end_time"] == 0.8
    assert [n["start_time"] for n in notes] == sorted(n["start_time"] for n in notes)


def test_pairing_closes_unpaired_start_after_truncation():
    start = NoteStartEvent(pitch=60, start_time=2.0, index=7, instrument="organ")
    stream = [event_to_dict(start)]  # stream cut off before its end
    notes = pair_events(stream)
    assert notes[0]["end_time"] == pytest.approx(2.1)


def test_pairing_ignores_end_without_start():
    stream = [
        {"type": "end", "end_time": 5.0, "start_event_index": 99},
    ]
    assert pair_events(stream) == []


def test_pairing_duplicate_start_is_last_write_wins():
    """SSE reconnects replay the stream; the UI dedupes by index, and so do we."""
    stream = [
        event_to_dict(NoteStartEvent(pitch=40, start_time=0.5, index=0, instrument="drums")),
        event_to_dict(NoteStartEvent(pitch=40, start_time=0.5, index=0, instrument="drums")),
    ]
    notes = pair_events(stream)
    assert len(notes) == 1


def test_instruments_in_first_seen_order():
    events = [
        {"instrument": "electric_bass"},
        {"instrument": "drums"},
        {"instrument": "electric_bass"},
    ]
    assert instruments_in(events) == ["electric_bass", "drums"]
    assert instruments_in([{"type": "progress"}]) == []
