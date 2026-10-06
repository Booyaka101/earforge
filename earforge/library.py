"""SQLite job library and artifact layout under the earforge data directory.

Everything a job produces lives under ``<data>/library/<job_id>/`` so a
transcription survives server restarts and can be reopened from the library
page without re-running the model.
"""

import json
import os
import shutil
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    duration_s REAL,
    model TEXT NOT NULL,
    instruments_requested TEXT NOT NULL DEFAULT '[]',
    instruments_found TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    error TEXT,
    bpm REAL,
    beats_per_bar INTEGER,
    onset_delay REAL,
    note_count INTEGER
);
"""


def app_dir() -> Path:
    """The earforge data directory (overridable with EARFORGE_HOME for tests)."""
    override = os.environ.get("EARFORGE_HOME")
    if override:
        return Path(override)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "earforge"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "earforge"
    return Path.home() / ".local" / "state" / "earforge"


class Library:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else app_dir()
        self.library_root = self.root / "library"
        self.library_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "earforge.db"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with _LOCK, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def job_dir(self, job_id: str) -> Path:
        d = self.library_root / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # paths inside a job directory
    def source_wav(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "source.wav"

    def events_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "events.jsonl"

    def midi_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "score.mid"

    def quantized_midi_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "score.quantized.mid"

    def stems_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "stems"

    def prune_rate_caches(self, job_id: str, keep_full: str = "1.00", max_extra: int = 2) -> None:
        """Speed renders are tens of MB each (a stem set per instrument per
        speed); keep the full-speed renders plus the most recently used few
        so a slider sweep cannot grow a job directory without bound."""
        stems = self.stems_dir(job_id)
        if stems.is_dir():
            extras = sorted(
                (d for d in stems.iterdir() if d.is_dir() and d.name != keep_full),
                key=lambda d: d.stat().st_mtime,
            )
            for d in extras[: max(0, len(extras) - max_extra)]:
                shutil.rmtree(d, ignore_errors=True)
        rates_dir = self.job_dir(job_id) / "audio_rates"
        if rates_dir.is_dir():
            extras = sorted(
                (f for f in rates_dir.glob("*.wav") if f.stem != keep_full),
                key=lambda f: f.stat().st_mtime,
            )
            for f in extras[: max(0, len(extras) - max_extra)]:
                f.unlink(missing_ok=True)

    def sheets_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "sheets"

    def sheets_zip(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "sheets.zip"

    def create_job(self, source_name: str, model: str, instruments: list[str]) -> dict:
        job = {
            "id": uuid.uuid4().hex[:12],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_name": source_name,
            "duration_s": None,
            "model": model,
            "instruments_requested": instruments,
            "instruments_found": [],
            "status": "running",
            "error": None,
            "bpm": None,
            "beats_per_bar": None,
            "onset_delay": None,
            "note_count": None,
        }
        with _LOCK, self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, created_at, source_name, model,"
                " instruments_requested, status)"
                " VALUES (:id, :created_at, :source_name, :model,"
                " :instruments_requested, :status)",
                {**job, "instruments_requested": json.dumps(instruments)},
            )
        self.job_dir(job["id"])
        return job

    def update_job(self, job_id: str, **fields) -> None:
        if not fields:
            return
        for name in ("instruments_requested", "instruments_found"):
            if name in fields and not isinstance(fields[name], str):
                fields[name] = json.dumps(fields[name])
        columns = ", ".join(f"{k} = :{k}" for k in fields)
        with _LOCK, self._connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {columns} WHERE id = :id", {**fields, "id": job_id}
            )

    def get_job(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return _row_to_job(row) if row else None

    def list_jobs(self) -> list[dict]:
        # rowid, not created_at: jobs created in the same second must still
        # list newest-first
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY rowid DESC").fetchall()
        return [_row_to_job(r) for r in rows]

    def delete_job(self, job_id: str) -> bool:
        with _LOCK, self._connect() as conn:
            cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        if cur.rowcount:
            shutil.rmtree(self.library_root / job_id, ignore_errors=True)
        return bool(cur.rowcount)


def _row_to_job(row: sqlite3.Row) -> dict:
    job = dict(row)
    job["instruments_requested"] = json.loads(job["instruments_requested"] or "[]")
    job["instruments_found"] = json.loads(job["instruments_found"] or "[]")
    return job
