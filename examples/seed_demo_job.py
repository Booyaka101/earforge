"""Seed the earforge library with the demo score as a finished job.

Example data (see demo_score.py): this exists so you can see the workspace
(piano roll, mixer, stems, exports) without waiting for the MuScriptor
weight download. The job is labelled "[example]" in the library and is a
normal job in every other respect: it reopens from the Library tab, renders
stems through fluidsynth, and exports MIDI and JSONL exactly like a real
transcription. Delete it from the Library tab when done.

    python examples/seed_demo_job.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_score import BPM, BEATS_PER_BAR, DURATION, notes, write_wav  # noqa: E402

from earforge.library import Library  # noqa: E402
from earforge import midibridge  # noqa: E402
from muscriptor.utils.beats import BeatGrid  # noqa: E402


def _demo_midi(score: list[dict]) -> bytes:
    return midibridge.midi_bytes(score, BeatGrid(BPM, BEATS_PER_BAR, 0, onset_delay=0))


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
        detected_grid=midibridge.grid_dict(BeatGrid(BPM, BEATS_PER_BAR, 0, onset_delay=0)),
        first_downbeat=0.0,
        resolved_grid=midibridge.grid_dict(BeatGrid(BPM, BEATS_PER_BAR, 0, onset_delay=0)),
    )
    print(f"done: {len(score)} notes, {len(instruments)} instruments, {DURATION:.0f}s")
    print("start earforge and open the Library tab to reopen it")


if __name__ == "__main__":
    main()
