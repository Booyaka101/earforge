"""Library CRUD and artifact paths against a throwaway EARFORGE_HOME."""


def test_create_and_get_roundtrip(home):
    job = home.create_job("song.mp3", "medium", ["electric_bass"])
    fetched = home.get_job(job["id"])
    assert fetched["source_name"] == "song.mp3"
    assert fetched["model"] == "medium"
    assert fetched["instruments_requested"] == ["electric_bass"]
    assert fetched["status"] == "running"
    assert home.job_dir(job["id"]).is_dir()


def test_update_and_list_order(home):
    first = home.create_job("a.wav", "medium", [])
    second = home.create_job("b.wav", "large", [])
    home.update_job(
        first["id"], status="complete", bpm=128.5, instruments_found=["drums"],
        note_count=10,
    )
    jobs = home.list_jobs()
    assert [j["source_name"] for j in jobs] == ["b.wav", "a.wav"]
    done = home.get_job(first["id"])
    assert done["status"] == "complete"
    assert done["bpm"] == 128.5
    assert done["instruments_found"] == ["drums"]
    assert done["note_count"] == 10


def test_get_missing_returns_none(home):
    assert home.get_job("nope") is None


def test_delete_removes_row_and_dir(home):
    job = home.create_job("x.wav", "medium", [])
    home.midi_path(job["id"]).write_bytes(b"MThd")
    assert home.delete_job(job["id"]) is True
    assert home.get_job(job["id"]) is None
    assert not (home.library_root / job["id"]).exists()
    assert home.delete_job(job["id"]) is False


def test_artifact_paths_under_job_dir(home):
    job = home.create_job("x.wav", "medium", [])
    jid = job["id"]
    assert home.source_wav(jid).parent == home.library_root / jid
    assert home.stems_dir(jid).name == "stems"
    assert home.sheets_zip(jid).name == "sheets.zip"
