"""atomic_render: concurrent renders of one artifact stay valid."""

import threading
import wave
from pathlib import Path

import pytest

from earforge.transcribe import TranscriptionError, atomic_render


def test_renders_via_temp_and_replaces(tmp_path):
    dst = tmp_path / "out.wav"
    seen = []

    def render(out: Path) -> None:
        seen.append(out.name)
        out.write_bytes(b"RIFF-fake")

    assert atomic_render(render, dst) == dst
    assert dst.read_bytes() == b"RIFF-fake"
    assert seen[0] != dst.name  # rendered into a temp name, then renamed


def test_existing_output_short_circuits(tmp_path):
    dst = tmp_path / "out.wav"
    dst.write_bytes(b"already here")
    calls = []

    assert atomic_render(lambda out: calls.append(out), dst) == dst
    assert calls == []
    assert dst.read_bytes() == b"already here"


def test_empty_render_raises(tmp_path):
    dst = tmp_path / "out.wav"
    with pytest.raises(TranscriptionError):
        atomic_render(lambda out: None, tmp_path / "out.wav")
    assert not dst.exists()
    assert list(tmp_path.iterdir()) == []  # temp cleaned up


def test_concurrent_renders_all_leave_a_valid_wav(tmp_path, tools_path):
    """Four threads race for one stem path; the file must never be a mix."""
    import numpy as np

    dst = tmp_path / "stem.wav"

    def make_wav_bytes() -> bytes:
        import io

        sig = (np.sin(np.linspace(0, 440 * 2 * np.pi, 4410)) * 12000).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(sig.tobytes())
        return buf.getvalue()

    data = make_wav_bytes()

    def render(out: Path) -> None:
        out.write_bytes(data)

    errors = []

    def worker():
        try:
            atomic_render(render, dst)
        except Exception as e:  # noqa: BLE001 - recorded, asserted below
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    with wave.open(str(dst), "rb") as w:
        assert w.getnframes() > 0
    # no temp leftovers
    assert [p.name for p in tmp_path.iterdir()] == ["stem.wav"]
