"""GPU integration: a real transcription through the real model.

Run with: pytest -m gpu
Skipped automatically unless CUDA is present and HuggingFace auth is
configured (HF_TOKEN, or a token cached by `hf auth login`). The MuScriptor
weights are gated: accept the license once per model page at
https://huggingface.co/MuScriptor (access is granted automatically).
"""

import os
from pathlib import Path

import numpy as np
import pytest

pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(
        os.environ.get("EARFORGE_GPU") != "1",
        reason="set EARFORGE_GPU=1 (plus HF_TOKEN) to run the real-model test",
    ),
]


def _hf_auth_available() -> bool:
    if os.environ.get("HF_TOKEN"):
        return True
    return (Path.home() / ".cache" / "huggingface" / "token").exists()


pytestmark.append(
    pytest.mark.skipif(not _hf_auth_available(), reason="no HuggingFace token")
)


@pytest.fixture(scope="module")
def model():
    import torch

    if not torch.cuda.is_available():
        pytest.skip("CUDA not available")
    from muscriptor.transcription_model import TranscriptionModel

    size = os.environ.get("EARFORGE_TEST_MODEL", "medium")
    return TranscriptionModel.load_model(size)


def _click_scale_wav(path: Path, seconds: float = 10.0, sr: int = 32000) -> Path:
    """Metronome clicks on the beat plus a C major scale across it."""
    t = np.arange(int(seconds * sr)) / sr
    sig = np.zeros_like(t)
    for beat in range(int(seconds)):
        i = int(beat * sr)
        sig[i : i + int(0.03 * sr)] = np.linspace(0.9, 0, int(0.03 * sr))
    for k, freq in enumerate([261.63, 293.66, 329.63, 349.23, 392.0, 440.0, 493.88, 523.25]):
        on = int((1.0 + 0.9 * k) * sr)
        n = int(0.5 * sr)
        if on + n < len(sig):
            sig[on : on + n] += 0.5 * np.sin(2 * np.pi * freq * t[:n]) * np.linspace(1, 0, n)
    import soundfile as sf

    sf.write(str(path), sig, sr)
    return path


def test_transcribe_click_scale(tmp_path, model):
    wav = _click_scale_wav(tmp_path / "click_scale.wav")

    from earforge.transcribe import run_transcription

    result = run_transcription(model, wav, emit=lambda ev: None, cancel=None)
    assert result["cancelled"] is False
    assert len(result["instruments"]) >= 1
    assert result["note_count"] >= 8
    assert result["midi_bytes"][:4] == b"MThd"

    starts = [e for e in result["events"] if type(e).__name__ == "NoteStartEvent"]
    for inst in {e.instrument for e in starts}:
        times = [e.start_time for e in starts if e.instrument == inst]
        assert times == sorted(times), f"{inst} note times not monotonic"
    assert starts[0].start_time >= 0.0
