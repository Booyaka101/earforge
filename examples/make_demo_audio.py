"""Render the demo score to an audio file you can drop into earforge.

Example data (see demo_score.py). Usage:

    python examples/make_demo_audio.py demo.wav
    python examples/make_demo_audio.py demo.mp3   # needs ffmpeg on PATH
"""

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_score import DURATION, write_wav


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    out = Path(sys.argv[1])
    with tempfile.TemporaryDirectory() as tmp:
        wav = write_wav(Path(tmp) / "demo.wav")
        if out.suffix.lower() == ".wav":
            out.write_bytes(wav.read_bytes())
        else:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(wav), "-b:a", "320k", str(out)],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                print(proc.stderr)
                raise SystemExit(1)
    print(f"wrote {out} ({DURATION:.0f}s of the demo score)")


if __name__ == "__main__":
    main()
