"""MIDI writing across muscriptor versions.

muscriptor 0.3.x on PyPI (current release) has events_to_midi_bytes without a
`quantize` parameter and its BeatGrid carries no `beat_subdivision`. GitHub
main (and presumably the next release) quantizes inside notes_to_midi. We try
the native path first; on 0.3.x we snap the event times ourselves, with math
identical to main's quantized_notes, and delegate the actual MIDI writing to
the installed events_to_midi_bytes untouched. When muscriptor gains `quantize`
this module's port stops being used on its own.
"""

import dataclasses
import inspect

from muscriptor.events import NoteEndEvent, NoteStartEvent
from muscriptor.utils.beats import estimate_onset_delay


def supports_quantize(model) -> bool:
    sig = inspect.signature(model.events_to_midi_bytes)
    return "quantize" in sig.parameters


def measure_grid(grid, onsets):
    """(grid with onset_delay filled in, subdivision or None).

    Port of main's BeatGrid.with_onset_delay, which additionally records the
    subdivision the delay was measured against (0.3.x grids lack the field).
    """
    if grid is None:
        return None, None
    measured = estimate_onset_delay(onsets, grid) if grid.beats is not None else None
    if measured is None:
        return dataclasses.replace(grid, onset_delay=0.0), None
    updates = {"onset_delay": measured.seconds}
    if "beat_subdivision" in grid.__dataclass_fields__:
        # carry it for the native path on muscriptor versions that have the
        # field: their events_to_midi_bytes reads it off the grid
        updates["beat_subdivision"] = measured.subdivision
    return dataclasses.replace(grid, **updates), measured.subdivision


def _snap(t: float, delay: float, offset: float, step: float) -> float:
    """main's quantized_notes snap, expressed in the raw event timeline.

    main snaps after subtracting the onset delay and before the bar offset is
    added at write time; this is the same function with the delay added back,
    so the upstream writer's own shift produces identical ticks.
    """
    return round((t - delay + offset) / step) * step - offset + delay


def snap_events(events, delay: float, offset: float, step: float):
    """Every note's onset and offset moved onto a `step`-second grid.

    A note shorter than half a step would vanish from the score; it gets the
    shortest length the grid has (main's rule). The upstream writer's own
    trim pass then removes any overlap that creates.
    """
    snapped = []
    open_notes: dict[int, NoteStartEvent] = {}
    for ev in events:
        if isinstance(ev, NoteStartEvent):
            t = _snap(ev.start_time, delay, offset, step)
            new_start = dataclasses.replace(ev, start_time=t)
            open_notes[ev.index] = new_start
            snapped.append(new_start)
        elif isinstance(ev, NoteEndEvent):
            start = open_notes.pop(ev.start_event_index, None)
            end = _snap(ev.end_time, delay, offset, step)
            if start is not None and end <= start.start_time:
                end = start.start_time + step
            snapped.append(
                NoteEndEvent(end_time=end, start_event=ev.start_event)
            )
        else:
            snapped.append(ev)
    return snapped


def midi_bytes(model, events, grid, quantize: bool = False, subdivision=None) -> bytes:
    """events_to_midi_bytes with a working `quantize` on any muscriptor.

    `subdivision` is what measure_grid read off the onset measurement; the
    0.3.x path needs it because its BeatGrid cannot carry the value. Quantize
    without a subdivision is a no-op, matching main's behaviour.
    """
    events = list(events)
    if supports_quantize(model):
        return model.events_to_midi_bytes(
            iter(events), beat_grid=grid, quantize=quantize
        )
    if not quantize or grid is None or grid.onset_delay is None:
        return model.events_to_midi_bytes(iter(events), beat_grid=grid)
    if subdivision is None:
        return model.events_to_midi_bytes(iter(events), beat_grid=grid)
    delay = grid.onset_delay
    offset = grid.bar_offset(min_shift=delay)
    step = 60.0 / grid.bpm / subdivision
    return model.events_to_midi_bytes(
        iter(snap_events(events, delay, offset, step)), beat_grid=grid
    )
