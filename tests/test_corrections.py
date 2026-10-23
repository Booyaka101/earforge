"""Saved-grid API, migration, transactional failures and revision caches."""

import hashlib
import io
import json
import sqlite3
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

import mido
import numpy as np
import pytest
from fastapi.testclient import TestClient
from muscriptor.utils.beats import BeatGrid

from earforge import corrections, exports, midibridge
from earforge.library import Library
from earforge.server import create_app
from tests.conftest import sse_events


@pytest.fixture
def completed(home, monkeypatch):
    # No model, network, renderer, or credentials are needed for these tests.
    monkeypatch.setattr("earforge.server.synth.available", lambda: False)
    client = TestClient(create_app(library=home, model_factory=lambda size: pytest.fail("model loaded")))
    job = home.create_job("saved.wav", "medium", [])
    grid = BeatGrid(100, 4, .4, beats=np.arange(20) * .6 + .4,
                    onset_delay=.012, beat_subdivision=2)
    events = [
        dict(type="start", index=0, start_time=3.25, pitch=40, instrument="electric_bass"),
        dict(type="end", start_event_index=0, end_time=3.5),
        dict(type="start", index=1, start_time=.01, pitch=36, instrument="drums"),
        dict(type="end", start_event_index=1, end_time=.06),
        dict(type="job_complete", bpm=100, beats_per_bar=4, first_downbeat=.4, note_count=2),
    ]
    home.events_path(job["id"]).write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    home.source_wav(job["id"]).write_bytes(b"untouched audio")
    notes = corrections.stored_notes(home, job["id"])
    key = home.write_export(job["id"], 0, midibridge.midi_bytes(notes, grid), midibridge.midi_bytes(notes, grid, quantize=True))
    home.update_job(job["id"], status="complete", duration_s=12, note_count=2,
                    instruments_found=["electric_bass", "drums"], export_key=key,
                    detected_grid=midibridge.grid_dict(grid), **midibridge.metadata(grid, {}, notes))
    return client, home, job["id"]


def save(client, jid, revision, **manual):
    return client.put(f"/api/jobs/{jid}/grid", json={"expected_revision": revision, **manual})


def test_repeated_save_reset_reopen_and_unchanged_playback(completed):
    client, lib, jid = completed
    original = client.get(f"/api/jobs/{jid}").json()
    stem = lib.stems_dir(jid) / "1.00" / "electric_bass.wav"
    stem.parent.mkdir(parents=True)
    stem.write_bytes(b"untouched stem")
    paths = [lib.events_path(jid), lib.source_wav(jid), stem]
    hashes = [hashlib.sha256(p.read_bytes()).digest() for p in paths]
    options = dict(bpm=120, beats_per_bar=4, first_downbeat=2.0)
    exports_seen = []
    for revision in range(3):
        response = save(client, jid, revision, **options)
        assert response.status_code == 200, response.text
        assert response.json()["export_revision"] == revision + 1
        exports_seen.append(client.get(f"/api/jobs/{jid}/export/midi").content)
        assert response.json()["detected_grid"] == original["detected_grid"]
    assert exports_seen[0] == exports_seen[1] == exports_seen[2]
    assert [hashlib.sha256(p.read_bytes()).digest() for p in paths] == hashes
    replay = sse_events(client.get(f"/api/jobs/{jid}/events").text)[-1]
    assert replay["export_revision"] == 3
    assert replay["bpm"] == 120
    assert replay["first_downbeat"] == 2
    assert json.loads(paths[0].read_text().splitlines()[-1])["bpm"] == 100
    reopened = Library(lib.root)
    reopened.init()
    assert reopened.get_job(jid)["manual_grid"] == options
    assert client.post(f"/api/jobs/{jid}/grid/reset", json={"expected_revision": 3}).status_code == 200
    reset = reopened.get_job(jid)
    assert reset["manual_grid"] == {}
    assert reset["detected_grid"] == original["detected_grid"]
    assert client.get(f"/api/jobs/{jid}/export/midi").content == lib.midi_path(jid, original).read_bytes()


@pytest.mark.parametrize("fields", [
    {"bpm": 0}, {"bpm": 401}, {"bpm": "120"}, {"bpm": True},
    {"beats_per_bar": 0}, {"beats_per_bar": 4.5}, {"beats_per_bar": True},
    {"first_downbeat": -1}, {"first_downbeat": 13}, {"first_downbeat": "2"},
    {"expected_revision": -1}, {"expected_revision": True}, {"extra": 1},
])
def test_invalid_options_preserve_revision(completed, fields):
    client, lib, jid = completed
    payload = {"expected_revision": 0, **fields}
    assert client.put(f"/api/jobs/{jid}/grid", json=payload).status_code == 422
    assert lib.get_job(jid)["export_revision"] == 0


def test_nonfinite_numbers_rejected(completed):
    client, lib, jid = completed
    for value in ("NaN", "Infinity", "-Infinity"):
        response = client.put(f"/api/jobs/{jid}/grid", content='{"expected_revision":0,"bpm":' + value + '}', headers={"Content-Type": "application/json"})
        assert response.status_code == 422


def test_stale_save_and_reset_rejected(completed):
    client, lib, jid = completed
    assert save(client, jid, 0, bpm=120).status_code == 200
    assert save(client, jid, 0, bpm=140).status_code == 409
    assert client.post(f"/api/jobs/{jid}/grid/reset", json={"expected_revision": 0}).status_code == 409
    assert lib.get_job(jid)["bpm"] == 120


@pytest.mark.parametrize("status", ["running", "cancelled", "error"])
def test_incomplete_jobs_rejected(completed, status):
    client, lib, jid = completed
    lib.update_job(jid, status=status)
    assert save(client, jid, 0, bpm=120).status_code == 409


def test_model_free_no_detected_grid(completed):
    client, lib, jid = completed
    lib.update_job(jid, detected_grid=None, resolved_grid=None, bpm=None)
    assert save(client, jid, 0, bpm=120, beats_per_bar=4, first_downbeat=2).status_code == 200
    assert client.post(f"/api/jobs/{jid}/grid/reset", json={"expected_revision": 1}).json()["resolved_grid"] is None


def test_generation_failure_preserves_artifacts_and_metadata(completed, monkeypatch):
    client, lib, jid = completed
    before = lib.get_job(jid)
    data = lib.midi_path(jid).read_bytes()
    real = midibridge.midi_bytes

    def broken(*args, **kwargs):
        if kwargs.get("quantize"):
            raise RuntimeError("simulated quantizer failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(midibridge, "midi_bytes", broken)
    response = save(client, jid, 0, bpm=120)
    assert response.status_code == 500
    assert "preserved" in response.json()["detail"]
    assert lib.get_job(jid) == before
    assert lib.midi_path(jid).read_bytes() == data


def test_commit_failure_removes_only_uncommitted_bundle(completed, monkeypatch):
    client, lib, jid = completed
    original = lib.get_job(jid)
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("simulated database failure")
    monkeypatch.setattr(lib, "commit_grid", broken)
    # Test the service directly: sqlite failures remain distinct from MIDI failures.
    with pytest.raises(sqlite3.OperationalError):
        corrections.save_grid(lib, original, 0, {"bpm":120})
    assert save(client, jid, 0, bpm=120).status_code == 500
    assert lib.get_job(jid) == original
    assert [p.name for p in (lib.job_dir(jid) / "exports").iterdir()] == [original["export_key"]]


def test_repeated_unsteady_saves_resolve_original_only(completed):
    client, lib, jid = completed
    beats = np.arange(24) * .4 + np.arange(24) ** 2 * .005
    detected = midibridge.grid_dict(BeatGrid(110, None, .83, beats=beats, steady=False, onset_delay=.012))
    lib.update_job(jid, detected_grid=detected)
    results = []
    for revision in range(3):
        response = save(client, jid, revision, first_downbeat=2)
        assert response.status_code == 200
        results.append(response.json()["resolved_grid"])
    assert results[0] == results[1] == results[2]
    assert lib.get_job(jid)["detected_grid"] == detected


def test_two_tabs_downloads_observe_one_coherent_revision(completed, monkeypatch):
    client, lib, jid = completed
    original = lib.get_job(jid)
    previous = lib.midi_path(jid).read_bytes()
    ready, release = threading.Event(), threading.Event()
    real = lib.write_export
    def paused(*args):
        key = real(*args)
        ready.set()
        assert release.wait(timeout=10)
        return key
    monkeypatch.setattr(lib, "write_export", paused)
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(save, client, jid, 0, bpm=140)
        assert ready.wait(timeout=10)
        try:
            assert lib.get_job(jid) == original
            assert client.get(f"/api/jobs/{jid}/export/midi").content == previous
        finally:
            release.set()
        assert result.result().status_code == 200
    assert lib.get_job(jid)["bpm"] == 140
    assert client.get(f"/api/jobs/{jid}/export/midi").content != previous


def test_unknown_job_is_404(completed):
    client, lib, jid = completed
    assert save(client, "absent", 0, bpm=120).status_code == 404


def test_concurrent_writers_only_one_commit(completed, monkeypatch):
    client, lib, jid = completed
    barrier = threading.Barrier(2)
    real = lib.write_export

    def synchronized(*args):
        key = real(*args)
        barrier.wait(timeout=10)
        return key

    monkeypatch.setattr(lib, "write_export", synchronized)
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda bpm: save(client, jid, 0, bpm=bpm), [120, 140]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert lib.get_job(jid)["export_revision"] == 1
    assert len(list((lib.job_dir(jid) / "exports").iterdir())) == 2  # initial plus winner
    midi = mido.MidiFile(file=io.BytesIO(client.get(f"/api/jobs/{jid}/export/midi").content))
    tempo = next(msg.tempo for t in midi.tracks for msg in t if msg.type == "set_tempo")
    assert mido.tempo2bpm(tempo) == pytest.approx(lib.get_job(jid)["bpm"], abs=.001)


def test_latest_revision_sheets_and_stem_conductor(completed, monkeypatch):
    client, lib, jid = completed
    monkeypatch.setattr(exports, "find_musescore", lambda: "fake")
    seen = []

    def engrave(source, directory, quantized):
        seen.append(source)
        directory.mkdir()
        (directory / "score.mid").write_bytes(source)
        (directory / "full_score.pdf").write_bytes(b"fake test PDF")

    monkeypatch.setattr(exports, "write_sheets", engrave)
    assert client.post(f"/api/jobs/{jid}/export/sheets").status_code == 200
    assert save(client, jid, 0, bpm=120, beats_per_bar=3, first_downbeat=2).status_code == 200
    assert client.get(f"/api/jobs/{jid}/export/sheets").status_code == 404
    sheets = client.post(f"/api/jobs/{jid}/export/sheets")
    assert sheets.status_code == 200
    assert sheets.headers["cache-control"] == "no-store"
    assert client.get(f"/api/jobs/{jid}/export/sheets").headers["cache-control"] == "no-store"
    assert len(seen) == 2 and seen[0] != seen[1]
    with zipfile.ZipFile(io.BytesIO(sheets.content)) as archive:
        assert archive.read("score.mid") == lib.quantized_midi_path(jid).read_bytes()
    full = mido.MidiFile(file=io.BytesIO(client.get(f"/api/jobs/{jid}/export/midi").content))
    stem = mido.MidiFile(file=io.BytesIO(client.get(f"/api/jobs/{jid}/export/stem/electric_bass.mid").content))
    assert list(full.tracks[0]) == list(stem.tracks[0])


def test_save_during_engraving_cannot_return_stale_sheets(completed, monkeypatch):
    client, lib, jid = completed
    monkeypatch.setattr(exports, "find_musescore", lambda: "fake")

    def engrave(source, directory, quantized):
        assert save(client, jid, 0, bpm=140).status_code == 200
        directory.mkdir()
        (directory / "score.mid").write_bytes(source)

    monkeypatch.setattr(exports, "write_sheets", engrave)
    assert client.post(f"/api/jobs/{jid}/export/sheets").status_code == 409
    assert client.get(f"/api/jobs/{jid}/export/sheets").status_code == 404


def test_legacy_database_additive_migration(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    # Exact pre-upgrade schema, with existing MIDI and JSONL preserved.
    from earforge.library import _SCHEMA
    with sqlite3.connect(root / "earforge.db") as conn:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT INTO jobs (id,created_at,source_name,model,status,bpm,beats_per_bar,onset_delay) VALUES ('old','today','old.wav','medium','complete',100,4,.01)")
    directory = root / "library" / "old"
    directory.mkdir(parents=True)
    (directory / "score.mid").write_bytes(b"legacy MIDI")
    (directory / "events.jsonl").write_text('{"type":"job_complete","first_downbeat":2.5}\n')
    library = Library(root)
    library.init()
    library.init()
    job = library.get_job("old")
    assert job["export_revision"] == 0
    assert job["manual_grid"] == {}
    assert job["detected_grid"]["beats"] is None
    assert job["detected_grid"]["first_downbeat"] == 2.5
    assert job["grid_origin"] == "legacy"
    assert library.midi_path("old").read_bytes() == b"legacy MIDI"
