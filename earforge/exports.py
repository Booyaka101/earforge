"""Exports: MIDI, MusicJSONL, and the engraved sheets tree as a zip."""

import tempfile
import threading
import zipfile
from pathlib import Path

from muscriptor.utils.sheets import (
    MuseScoreError, MuseScoreNotFoundError, find_musescore,
    sheets_are_available, write_sheets,
)

_ENGRAVE_LOCK = threading.Lock()


class ExportError(RuntimeError):
    """Export failed for a reason the user can act on; message is UI-ready."""


def sheets_available() -> bool:
    try:
        return sheets_are_available()
    except Exception:
        return False


def export_sheets_zip(midi_bytes: bytes, quantized_bytes: bytes | None, job_dir: Path) -> Path:
    """Engrave the sheets tree into ``job_dir/sheets`` and zip it.

    Uses the quantized MIDI when the job produced one (the notes are already
    on the beat grid, which is what notation should be engraved from), falling
    back to the performance MIDI. Returns the zip path; re-exports reuse the
    already-engraved tree.
    """
    with _ENGRAVE_LOCK:
        return _engrave(midi_bytes, quantized_bytes, job_dir)


def _engrave(midi_bytes, quantized_bytes, job_dir):
    try:
        find_musescore()
    except MuseScoreNotFoundError as e:
        raise ExportError(str(e)) from e

    zip_path = job_dir / "sheets.zip"
    if zip_path.exists():
        return zip_path

    if quantized_bytes is not None:
        source, quantized = quantized_bytes, True
    else:
        source, quantized = midi_bytes, False

    with tempfile.TemporaryDirectory(prefix="engrave-", dir=job_dir) as tmp:
        sheets_dir = Path(tmp) / "sheets"
        try:
            write_sheets(source, sheets_dir, quantized=quantized)
        except MuseScoreError as e:
            raise ExportError(f"engraving failed: {e}") from e
        packed = Path(tmp) / "sheets.zip"
        with zipfile.ZipFile(packed, "w", compression=zipfile.ZIP_STORED) as archive:
            for path in sorted(sheets_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=path.relative_to(sheets_dir))
        packed.replace(zip_path)
    return zip_path
