"""ffmpeg conversion: real runs against the bundled binary."""

import wave
from pathlib import Path

import pytest

from earforge import transcribe
from tests.conftest import make_mp3, make_wav


def test_convert_stereo_to_mono_32k(tmp_path, tools_path):
    if not tools_path["ffmpeg"]:
        pytest.skip("ffmpeg not available")
    src = make_wav(tmp_path / "in.wav", seconds=2.0, sr=44100)
    dst = tmp_path / "out.wav"
    duration = transcribe.convert_audio(src, dst)
    with wave.open(str(dst), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == transcribe.SAMPLE_RATE
        assert w.getsampwidth() == 2
    assert duration == pytest.approx(2.0, abs=0.05)


def test_convert_mp3(tmp_path, tools_path):
    if not tools_path["ffmpeg"]:
        pytest.skip("ffmpeg not available")
    wav = make_wav(tmp_path / "in.wav", seconds=1.5)
    mp3 = make_mp3(tmp_path / "in.mp3", wav)
    if mp3 is None:
        pytest.skip("this ffmpeg build cannot encode mp3")
    duration = transcribe.convert_audio(mp3, tmp_path / "out.wav")
    assert duration == pytest.approx(1.5, abs=0.1)


def test_convert_corrupt_file_clear_error(tmp_path, tools_path):
    if not tools_path["ffmpeg"]:
        pytest.skip("ffmpeg not available")
    bad = tmp_path / "broken.mp3"
    bad.write_bytes(b"\x00\x01\x02 not audio at all" * 100)
    with pytest.raises(transcribe.TranscriptionError) as e:
        transcribe.convert_audio(bad, tmp_path / "out.wav")
    assert "could not decode" in str(e.value)


def test_convert_missing_file(tmp_path, tools_path):
    if not tools_path["ffmpeg"]:
        pytest.skip("ffmpeg not available")
    with pytest.raises(transcribe.TranscriptionError) as e:
        transcribe.convert_audio(tmp_path / "nope.wav", tmp_path / "out.wav")
    assert "not found" in str(e.value)


def test_missing_ffmpeg_message(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe.shutil, "which", lambda name: None)
    src = tmp_path / "in.wav"
    src.write_bytes(b"RIFF")
    with pytest.raises(transcribe.TranscriptionError) as e:
        transcribe.convert_audio(src, tmp_path / "out.wav")
    assert "ffmpeg was not found" in str(e.value)
    assert "https://ffmpeg.org" in str(e.value)
