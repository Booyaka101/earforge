"""Generate first, then atomically select an immutable export revision."""

import json
import shutil

from earforge import midibridge, transcribe
from earforge.library import StaleRevision


def stored_notes(library, job_id):
    with library.events_path(job_id).open(encoding="utf-8") as stream:
        return transcribe.pair_events([json.loads(line) for line in stream if line.strip()])


def save_grid(library, job, expected, manual):
    if job["status"] != "complete" or job["export_revision"] != expected:
        raise StaleRevision("Only the latest completed job can be corrected. Reload the job.")
    notes = stored_notes(library, job["id"])
    detected = midibridge.grid_from_dict(job["detected_grid"])
    metadata = midibridge.metadata(detected, manual, notes)
    key = library.write_export(
        job["id"], expected + 1,
        midibridge.midi_bytes(notes, detected, manual),
        midibridge.midi_bytes(notes, detected, manual, quantize=True),
    )
    try:
        library.commit_grid(job["id"], expected, manual_grid=manual, export_key=key, **metadata)
    except Exception:
        shutil.rmtree(library.library_root / job["id"] / "exports" / key, ignore_errors=True)
        raise
    # Return this commit, not an unrelated revision saved immediately after it.
    return {**job, **metadata, "manual_grid": manual, "export_key": key,
            "export_revision": expected + 1}
