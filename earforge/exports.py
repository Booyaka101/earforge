"""Exports: MIDI, MusicJSONL, and the engraved sheets tree as a zip."""

import shutil
import zipfile
from pathlib import Path

try:  # muscriptor release that ships sheets (post-0.3.0)
    from muscriptor.utils.sheets import (
        MuseScoreError,
        MuseScoreNotFoundError,
        find_musescore,
        sheets_are_available,
        write_sheets,
    )
except ModuleNotFoundError:  # muscriptor 0.3.x on PyPI
    from earforge._sheets import (
        MuseScoreError,
        MuseScoreNotFoundError,
        find_musescore,
        sheets_are_available,
        write_sheets,
    )


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
    try:
        find_musescore()
    except MuseScoreNotFoundError as e:
        raise ExportError(str(e)) from e

    sheets_dir = job_dir / "sheets"
    zip_path = job_dir / "sheets.zip"
    if zip_path.exists():
        return zip_path

    if quantized_bytes is not None:
        source, quantized = quantized_bytes, True
    else:
        source, quantized = midi_bytes, False

    if sheets_dir.exists():
        # write_sheets wants a fresh directory; re-export starts over
        shutil.rmtree(sheets_dir)
    try:
        write_sheets(source, sheets_dir, quantized=quantized)
    except MuseScoreError as e:
        shutil.rmtree(sheets_dir, ignore_errors=True)
        raise ExportError(f"engraving failed: {e}") from e

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(sheets_dir.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=path.relative_to(sheets_dir))
    return zip_path
