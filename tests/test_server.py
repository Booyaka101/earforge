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


def test_model_cache_keeps_two_sizes_loaded(stub_home, tmp_path):
    sizes = []

    def factory(size):
        sizes.append(size)
        return StubModel()

    client = _client_factory(stub_home, factory)
    first = _post_audio(client, tmp_path, name="one.wav")
    _wait_complete(client, first)
    second = _post_audio(client, tmp_path, name="two.wav", model="large")
    _wait_complete(client, second)
    third = _post_audio(client, tmp_path, name="three.wav")
    _wait_complete(client, third)
    # medium -> large -> medium must not have reloaded medium
    assert sizes == ["medium", "large"]


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


def test_startup_sweep_orphans_running_jobs(home):
    """A server restart kills workers silently; their rows must not claim
    running forever."""
    job = home.create_job("ghost.wav", "medium", [])
    home.update_job(job["id"], duration_s=10.0)
    # no live run exists: this is exactly the state a restart leaves behind
    create_app(model="medium", library=home, model_factory=lambda s: StubModel())
    swept = home.get_job(job["id"])
    assert swept["status"] == "error"
    assert "restarted" in swept["error"]


class _CrashingModel(StubModel):
    """Yields one event, then dies like a GPU fault would."""

    def transcribe(self, audio, instruments=None, batch_size=1, no_eos_is_ok=True):
        from muscriptor.events import ProgressEvent

        yield ProgressEvent(completed=0, total=4)
        raise RuntimeError("CUDA error: device-side assert")


def test_model_crash_mid_stream_is_a_clean_error(stub_home, tmp_path):
    client = _client_factory(stub_home, lambda size: _CrashingModel())
    job_id = _post_audio(client, tmp_path)
    _wait_complete(client, job_id)

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "error"
    assert "CUDA error" in job["error"]

    events = sse_events(client.get(f"/api/jobs/{job_id}/events").text)
    assert events[-1]["type"] == "job_error"
    assert "CUDA error" in events[-1]["message"]
    # the stream closed: the last line is terminal, not a hang


def test_stem_render_network_failure_clear_message(stub_home, tmp_path, monkeypatch, tools_path):
    """A failed soundfont fetch must explain itself, not bare-500."""
    stub = StubModel()
    client = _client(stub_home, stub)
    job_id = _post_audio(client, tmp_path)
    _wait_complete(client, job_id)

    import earforge.synth as synth_mod

    def broken_render(*args, **kwargs):
        raise OSError("network is unreachable")

    monkeypatch.setattr(synth_mod, "render_stem", broken_render)
    # defeat any cached copy from a previous run
    stems = stub_home.stems_dir(job_id) / "1.00" / "electric_bass.wav"
    stems.unlink(missing_ok=True)
    r = client.get(f"/api/jobs/{job_id}/stems/electric_bass?rate=1.0")
    assert r.status_code == 502
    assert "soundfont" in r.json()["detail"]


def test_second_job_reports_queued_behind_gpu_lock(stub_home, tmp_path):
    """A second upload waiting on the GPU must say so, not look hung."""
    stub = StubModel(chunk_delay=0.5)
    client = _client(stub_home, stub)
    first = _post_audio(client, tmp_path, name="one.wav")
    time.sleep(0.3)  # let the first reach the model
    second = _post_audio(client, tmp_path, name="two.wav")
    events = sse_events(client.get(f"/api/jobs/{second}/events").text)
    kinds = [(e["type"], e.get("kind")) for e in events]
    assert ("banner", "queued") in kinds
    _wait_complete(client, first)
    _wait_complete(client, second)
    assert client.get(f"/api/jobs/{second}").json()["status"] == "complete"


def test_delete_running_job_refused(stub_home, tmp_path):
    stub = StubModel(chunk_delay=0.4)
    client = _client(stub_home, stub)
    job_id = _post_audio(client, tmp_path)
    assert client.delete(f"/api/jobs/{job_id}").status_code == 409
    client.post(f"/api/jobs/{job_id}/cancel")
    _wait_complete(client, job_id)


def test_crashed_job_replay_ends_without_terminal_event(stub_home, tmp_path):
    """A worker that died mid-run leaves no terminal event. The replay must
    end cleanly (the browser then finalizes from the job record instead of
    reconnecting forever)."""
    stub = StubModel()
    client = _client(stub_home, stub)
    job_id = _post_audio(client, tmp_path)
    _wait_complete(client, job_id)

    path = stub_home.events_path(job_id)
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    kept = [l for l in lines if '"job_complete"' not in l]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    stub_home.update_job(job_id, status="running")

    events = sse_events(client.get(f"/api/jobs/{job_id}/events").text)
    kinds = [e["type"] for e in events]
    assert "start" in kinds
    assert "job_complete" not in kinds
    assert "job_error" not in kinds

    # the job record tells the truth the DB cannot: nothing is live, even
    # though the status column still says running
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "running"
    assert job["active"] is False


def test_concurrent_same_stem_requests(stub_home, tmp_path, tools_path):
    """Two tabs, one stem: both get 200 and the cached wav is valid."""
    import io
    import threading
    import wave as wavemod

    if not tools_path["fluidsynth"]:
        pytest.skip("fluidsynth not available")
    stub = StubModel()
    app = create_app(model="medium", library=stub_home, model_factory=lambda s: stub)
    job_id = _post_audio(TestClient(app), tmp_path)
    _wait_complete(TestClient(app), job_id)

    results = []

    def fetch():
        with TestClient(app) as c:
            r = c.get(f"/api/jobs/{job_id}/stems/electric_bass?rate=1.0")
            results.append((r.status_code, len(r.content)))

    threads = [threading.Thread(target=fetch) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(status == 200 and size > 1000 for status, size in results)
    cached = stub_home.stems_dir(job_id) / "1.00" / "electric_bass.wav"
    with wavemod.open(str(cached), "rb") as w:
        assert w.getnframes() > 0
    assert [p.name for p in cached.parent.iterdir()] == ["electric_bass.wav"]


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
    assert stub_home.list_jobs() == []
    assert list(stub_home.library_root.iterdir()) == []


def test_failed_upload_does_not_leave_a_running_job(stub_home, monkeypatch):
    async def failed_read(self, size=-1):
        raise OSError("simulated upload read failure")

    monkeypatch.setattr("starlette.datastructures.UploadFile.read", failed_read)
    client = _client(stub_home, StubModel())
    with pytest.raises(OSError, match="simulated upload"):
        client.post("/api/jobs", files={"file": ("broken.wav", b"audio", "audio/wav")})
    assert stub_home.list_jobs() == []
    assert list(stub_home.library_root.iterdir()) == []


def test_unicode_download_filenames(stub_home, tmp_path):
    from urllib.parse import quote

    client = _client(stub_home, StubModel())
    name = "日本語🎵.wav"
    jid = _post_audio(client, tmp_path, name=name)
    assert _wait_complete(client, jid)["status"] == "complete"
    for endpoint, filename in [
        ("midi", "日本語🎵.mid"),
        ("jsonl", "日本語🎵.events.jsonl"),
        ("stem/electric_bass.mid", "日本語🎵 - electric_bass.mid"),
    ]:
        response = client.get(f"/api/jobs/{jid}/export/{endpoint}")
        assert response.status_code == 200
        assert response.headers["content-disposition"] == "attachment; filename*=utf-8''" + quote(filename)
        assert response.headers["cache-control"] == "no-store"


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


def test_audio_at_rate_pitch_preserved_render(stub_home, tmp_path, tools_path):
    """The original recording is slowed with ffmpeg atempo: longer, same pitch."""
    stub = StubModel()
    client = _client(stub_home, stub)
    wav = make_wav(tmp_path / "song.wav", seconds=3.0)
    r = client.post(
        "/api/jobs", files={"file": ("song.wav", wav.read_bytes(), "audio/wav")}
    )
    job_id = r.json()["id"]
    _wait_complete(client, job_id)

    import io
    import wave

    full = client.get(f"/api/jobs/{job_id}/audio")
    assert full.status_code == 200
    with wave.open(io.BytesIO(full.content), "rb") as w:
        full_s = w.getnframes() / w.getframerate()

    slow = client.get(f"/api/jobs/{job_id}/audio?rate=0.75")
    assert slow.status_code == 200
    import numpy as np

    with wave.open(io.BytesIO(slow.content), "rb") as w:
        slow_s = w.getnframes() / w.getframerate()
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    assert slow_s == pytest.approx(full_s / 0.75, rel=0.02)

    # atempo keeps pitch: the 330 Hz tone stays at 330. A resample would
    # move it to 0.75 x 330 = 247 Hz.
    spectrum = np.abs(np.fft.rfft(frames.astype(float)))
    freqs = np.fft.rfftfreq(len(frames), d=1.0 / 32000)

    def band(hz, width=10):
        mask = (freqs > hz - width) & (freqs < hz + width)
        return spectrum[mask].max() if mask.any() else 0.0

    assert band(330) > 5 * band(247)
    assert band(330) > 5 * band(100)

    # rate bounds enforced, unknown rate rejected
    assert client.get(f"/api/jobs/{job_id}/audio?rate=0.3").status_code == 400
    assert client.get(f"/api/jobs/{job_id}/audio?rate=1.5").status_code == 400


def test_per_instrument_midi_export(stub_home, tmp_path):
    stub = StubModel()
    client = _client(stub_home, stub)
    job_id = _post_audio(client, tmp_path)
    _wait_complete(client, job_id)

    r = client.get(f"/api/jobs/{job_id}/export/stem/electric_bass.mid")
    assert r.status_code == 200
    assert r.content[:4] == b"MThd"
    assert "electric_bass" in r.headers["content-disposition"]
    assert client.get(f"/api/jobs/{job_id}/export/stem/oboe.mid").status_code == 404


def test_upload_filename_sanitized(stub_home, tmp_path):
    """Quotes and path bits must not reach the DB or Content-Disposition."""
    client = _client(stub_home, StubModel())
    wav = make_wav(tmp_path / "x.wav", seconds=0.5)
    r = client.post(
        "/api/jobs",
        files={"file": ('evil\\"<song>.mp3\r\n', wav.read_bytes(), "audio/wav")},
        data={},
    )
    assert r.status_code == 200
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert '"' not in job["source_name"]
    assert "<" not in job["source_name"]
    assert "\r" not in job["source_name"]
    midi = client.get(f"/api/jobs/{job['id']}/export/midi")
    # a malformed header would raise or be stripped by the HTTP layer
    assert midi.status_code in (200, 404)


def test_upload_windows_reserved_name_guarded(stub_home, tmp_path):
    """CON.mp3 must not become a device-name path."""
    client = _client(stub_home, StubModel())
    wav = make_wav(tmp_path / "x.wav", seconds=0.5)
    r = client.post(
        "/api/jobs",
        files={"file": ("CON.mp3", wav.read_bytes(), "audio/wav")},
    )
    assert r.status_code == 200
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["source_name"] == "_CON.mp3"


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
