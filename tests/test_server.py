"""Server integration: jobs, SSE, library, exports, stems, cancel, errors."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from earforge.exports import MuseScoreNotFoundError, find_musescore
from earforge.server import create_app
from tests.conftest import StubModel, make_wav, sse_events


@pytest.fixture()
def stub_home(home, tools_path):
    if not tools_path["ffmpeg"]:
        pytest.skip("ffmpeg not available")
    return home


def _client(home, stub):
    app = create_app(model="medium", library=home, model_factory=lambda size: stub)
    return TestClient(app)


def _client_factory(home, factory):
    app = create_app(model="medium", library=home, model_factory=factory)
    return TestClient(app)


def _post_audio(client, tmp_path, name="song.wav", **fields):
    wav = make_wav(tmp_path / name, seconds=1.0)
    r = client.post(
        "/api/jobs",
        files={"file": (name, wav.read_bytes(), "audio/wav")},
        data=fields,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _wait_complete(client, job_id, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} still running after {timeout}s")


def test_job_sse_and_library(stub_home, tmp_path):
    stub = StubModel()
    client = _client(stub_home, stub)
    job_id = _post_audio(client, tmp_path)

    events = sse_events(client.get(f"/api/jobs/{job_id}/events").text)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "job_start"
    assert "progress" in kinds
    assert kinds[-1] == "job_complete"

    complete = events[-1]
    assert complete["instruments"] == ["electric_bass", "drums"]
    assert complete["note_count"] == 32 + 12
    assert complete["bpm"] == 120.0
    assert complete["beats_per_bar"] == 4

    # every start paired with exactly one end, per the stream contract
    starts = [e for e in events if e["type"] == "start"]
    ends = [e for e in events if e["type"] == "end"]
    assert len(starts) == len(ends) == 44
    assert {e["index"] for e in starts} == {e["start_event_index"] for e in ends}
    # note times per instrument are monotonic in stream order
    for inst in ("electric_bass", "drums"):
        times = [e["start_time"] for e in starts if e["instrument"] == inst]
        assert times == sorted(times)

    # the stub saw the converted 32k wav, and no instrument mask
    assert stub.calls[0]["instruments"] is None
    assert stub.calls[0]["audio"].endswith("source.wav")

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "complete"
    assert job["duration_s"] == pytest.approx(1.0, abs=0.05)
    assert job["instruments_found"] == ["electric_bass", "drums"]

    jobs = client.get("/api/library").json()["jobs"]
    assert [j["id"] for j in jobs] == [job_id]


def test_midi_and_jsonl_exports(stub_home, tmp_path):
    stub = StubModel()
    client = _client(stub_home, stub)
    job_id = _post_audio(client, tmp_path)
    _wait_complete(client, job_id)

    midi = client.get(f"/api/jobs/{job_id}/export/midi")
    assert midi.status_code == 200
    assert midi.content[:4] == b"MThd"
    assert midi.headers["content-disposition"].endswith('song.mid"')

    jsonl = client.get(f"/api/jobs/{job_id}/export/jsonl")
    assert jsonl.status_code == 200
    lines = [json.loads(l) for l in jsonl.text.splitlines() if l.strip()]
    assert any(l["type"] == "job_complete" for l in lines)
    assert any(l["type"] == "start" for l in lines)


def test_instruments_masking_and_resolution(stub_home, tmp_path):
    stub = StubModel()
    client = _client(stub_home, stub)
    job_id = _post_audio(client, tmp_path, instruments="electric_bass, drums")
    _wait_complete(client, job_id)
    assert stub.calls[0]["instruments"] == ["electric_bass", "drums"]

    # unambiguous abbreviations resolve like the muscriptor CLI's
    job_id = _post_audio(client, tmp_path, name="abbrev.wav", instruments="timp")
    _wait_complete(client, job_id)
    assert stub.calls[-1]["instruments"] == ["timpani"]

    # ambiguous token rejected up front with a helpful message
    wav = make_wav(tmp_path / "x.wav", seconds=0.5)
    r = client.post(
        "/api/jobs",
        files={"file": ("x.wav", wav.read_bytes(), "audio/wav")},
        data={"instruments": "bass"},
    )
    assert r.status_code == 400
    assert "ambiguous" in r.json()["detail"]

    # garbage token suggests close spellings
    r = client.post(
        "/api/jobs",
        files={"file": ("x.wav", wav.read_bytes(), "audio/wav")},
        data={"instruments": "timps"},
    )
    assert r.status_code == 400
    assert "timpani" in r.json()["detail"]


def test_model_choice_reaches_factory(stub_home, tmp_path):
    sizes = []

    def factory(size):
        sizes.append(size)
        return StubModel()

    client = _client_factory(stub_home, factory)
    job_id = _post_audio(client, tmp_path, model="large")
    _wait_complete(client, job_id)
    assert sizes == ["large"]


def test_unknown_model_rejected(stub_home, tmp_path):
    client = _client(stub_home, StubModel())
    wav = make_wav(tmp_path / "x.wav", seconds=0.5)
    r = client.post(
        "/api/jobs",
        files={"file": ("x.wav", wav.read_bytes(), "audio/wav")},
        data={"model": "huge"},
    )
    assert r.status_code == 400


def test_tempo_fallback_banner(stub_home, tmp_path):
    stub = StubModel(no_tempo=True)
    client = _client(stub_home, stub)
    job_id = _post_audio(client, tmp_path)
    events = sse_events(client.get(f"/api/jobs/{job_id}/events").text)
    banners = [e for e in events if e["type"] == "banner"]
    assert len(banners) == 1
    assert banners[0]["kind"] == "tempo_fallback"
    assert "no steady tempo" in banners[0]["message"].lower()
    complete = events[-1]
    assert complete["bpm"] is None


def test_cancel_mid_run(stub_home, tmp_path):
    stub = StubModel(chunk_delay=0.5)
    client = _client(stub_home, stub)
    job_id = _post_audio(client, tmp_path)

    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running" or stub.calls:
            break
        time.sleep(0.1)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200

    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.1)
    assert job["status"] == "cancelled"

    events = sse_events(client.get(f"/api/jobs/{job_id}/events").text)
    assert events[-1]["type"] == "job_cancelled"
    # a second cancel now finds nothing running
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409


def test_delete_job(stub_home, tmp_path):
    client = _client(stub_home, StubModel())
    job_id = _post_audio(client, tmp_path)
    _wait_complete(client, job_id)
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_delete_running_job_refused(stub_home, tmp_path):
    stub = StubModel(chunk_delay=0.4)
    client = _client(stub_home, stub)
    job_id = _post_audio(client, tmp_path)
    assert client.delete(f"/api/jobs/{job_id}").status_code == 409
    client.post(f"/api/jobs/{job_id}/cancel")
    _wait_complete(client, job_id)


def test_missing_endpoints_404_cleanly(stub_home):
    client = _client(stub_home, StubModel())
    assert client.get("/api/jobs/zzz").status_code == 404
    assert client.get("/api/jobs/zzz/events").status_code == 404
    assert client.get("/api/jobs/zzz/export/midi").status_code == 404
    assert client.get("/api/jobs/zzz/stems/drums").status_code == 404


def test_empty_upload_rejected(stub_home):
    client = _client(stub_home, StubModel())
    r = client.post(
        "/api/jobs", files={"file": ("empty.wav", b"", "audio/wav")}
    )
    assert r.status_code == 400
    assert "empty" in r.json()["detail"]


def test_sheets_export_503_without_musescore(stub_home, tmp_path, monkeypatch):
    monkeypatch.delenv("MUSCRIPTOR_MUSESCORE", raising=False)
    try:
        find_musescore()
        pytest.skip("MuseScore really is installed")
    except MuseScoreNotFoundError:
        pass
    client = _client(stub_home, StubModel())
    job_id = _post_audio(client, tmp_path)
    _wait_complete(client, job_id)
    r = client.post(f"/api/jobs/{job_id}/export/sheets")
    assert r.status_code == 503
    assert "MuseScore" in r.json()["detail"]


def test_stem_rendering_real_fluidsynth(stub_home, tmp_path, tools_path):
    if not tools_path["fluidsynth"]:
        pytest.skip("fluidsynth not available")
    stub = StubModel()
    client = _client(stub_home, stub)
    job_id = _post_audio(client, tmp_path)
    _wait_complete(client, job_id)

    r = client.get(f"/api/jobs/{job_id}/stems/electric_bass?rate=1.0")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/wav"
    assert len(r.content) > 1000
    import io
    import wave

    with wave.open(io.BytesIO(r.content), "rb") as w:
        assert w.getframerate() == 44100
        # stub score is 8.28s of notes; stem has a little tail
        assert w.getnframes() / 44100 > 7.0

    # half-speed stem is twice as long: pitch held, time stretched
    slow = client.get(f"/api/jobs/{job_id}/stems/electric_bass?rate=0.5")
    assert slow.status_code == 200
    with wave.open(io.BytesIO(slow.content), "rb") as w:
        assert w.getnframes() / 44100 > 14.0

    # invalid rate and unknown instrument are clean errors
    assert client.get(f"/api/jobs/{job_id}/stems/electric_bass?rate=0.3").status_code == 400
    assert client.get(f"/api/jobs/{job_id}/stems/oboe").status_code == 404


def test_ffmpeg_missing_gives_503(stub_home, tmp_path, monkeypatch):
    from earforge import server as server_mod

    monkeypatch.setattr(server_mod.transcribe, "ffmpeg_path", lambda: None)
    client = _client(stub_home, StubModel())
    wav = make_wav(tmp_path / "x.wav", seconds=0.5)
    r = client.post(
        "/api/jobs", files={"file": ("x.wav", wav.read_bytes(), "audio/wav")}
    )
    assert r.status_code == 503
    assert "ffmpeg" in r.json()["detail"]
