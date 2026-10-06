"""Seed the earforge library with the demo score as a finished job.

Example data (see demo_score.py): this exists so you can see the workspace
(piano roll, mixer, stems, exports) without waiting for the MuScriptor
weight download. The job is labelled "[example]" in the library and is a
normal job in every other respect: it reopens from the Library tab, renders
stems through fluidsynth, and exports MIDI and JSONL exactly like a real
transcription. Delete it from the Library tab when done.

    python examples/seed_demo_job.py
"""

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_score import BPM, BEATS_PER_BAR, DURATION, notes, write_wav  # noqa: E402

from earforge.library import Library  # noqa: E402


def _demo_midi(score: list[dict]) -> bytes:
    """The score as a multi-instrument MIDI file (mido, one track per voice)."""
    from mido import MidiFile, MidiTrack, Message, MetaMessage, bpm2tempo

    programs = {
        "electric_bass": (33, 0),
        "clean_electric_guitar": (28, 1),
        "organ": (19, 2),
        "drums": (0, 9),
    }
    midi = MidiFile(ticks_per_beat=480)
    by_inst: dict[str, list[dict]] = {}
    for note in score:
        by_inst.setdefault(note["instrument"], []).append(note)
    for inst in ("electric_bass", "clean_electric_guitar", "organ", "drums"):
        track = MidiTrack()
        midi.tracks.append(track)
        program, channel = programs[inst]
        track.append(MetaMessage("set_tempo", tempo=bpm2tempo(BPM), time=0))
        track.append(Message("program_change", channel=channel, program=program, time=0))
        events = []
        for note in by_inst.get(inst, []):
            events.append((round(note["start_time"] * 960), note["pitch"], 1))
            events.append((round(note["end_time"] * 960), note["pitch"], 0))
        events.sort(key=lambda e: (e[0], e[2]))
        now = 0
        for tick, pitch, is_on in events:
            track.append(Message(
                "note_on" if is_on else "note_off", channel=channel,
                note=pitch, velocity=100 if is_on else 0, time=tick - now))
            now = tick
    buf = io.BytesIO()
    midi.save(file=buf)
    return buf.getvalue()


def main() -> None:
    lib = Library()
    lib.init()
    job = lib.create_job("[example] demo score", "medium", [])
    jid = job["id"]
    print(f"seeding job {jid} in {lib.job_dir(jid)}")

    write_wav(lib.source_wav(jid))
    lib.midi_path(jid).write_bytes(_demo_midi(notes()))

    score = notes()
    lines = [
        json.dumps({
            "type": "job_start", "id": jid,
            "source_name": "[example] demo score", "model": "medium",
            "duration_s": DURATION,
        })
    ]
    chunks = 8
    per = max(1, len(score) // chunks)
    for i in range(0, len(score), per):
        batch = score[i : i + per]
        lines.append(json.dumps({
            "type": "progress",
            "completed": min(chunks, i // per + 1), "total": chunks}))
        for note in batch:
            lines.append(json.dumps({
                "type": "start", "pitch": note["pitch"],
                "start_time": round(note["start_time"], 3),
                "index": note["index"], "instrument": note["instrument"]}))
            lines.append(json.dumps({
                "type": "end", "end_time": round(note["end_time"], 3),
                "start_event_index": note["index"]}))
    instruments = []
    for note in score:
        if note["instrument"] not in instruments:
            instruments.append(note["instrument"])
    lines.append(json.dumps({
        "type": "job_complete", "instruments": instruments,
        "bpm": BPM, "beats_per_bar": BEATS_PER_BAR, "first_downbeat": 0.0,
        "onset_delay": 0.0, "note_count": len(score)}))
    lib.events_path(jid).write_text("\n".join(lines) + "\n", encoding="utf-8")

    lib.update_job(
        jid, duration_s=DURATION, status="complete",
        instruments_found=instruments, bpm=BPM,
        beats_per_bar=BEATS_PER_BAR, onset_delay=0.0, note_count=len(score),
    )
    print(f"done: {len(score)} notes, {len(instruments)} instruments, {DURATION:.0f}s")
    print("start earforge and open the Library tab to reopen it")


if __name__ == "__main__":
    main()
