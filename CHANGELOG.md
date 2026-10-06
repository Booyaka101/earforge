# Changelog

## 0.1.0 (2026-10-06)

First release.

- `earforge` command starts a local server on 127.0.0.1:8417 and opens the browser.
- Drag-drop any audio file; converted to 32 kHz mono WAV with ffmpeg before the
  model ever sees it, so container problems cannot reach the model.
- MuScriptor transcription with model medium (default) / large / small, device
  auto-picked (CUDA on this class of machine), events streamed to the browser
  over SSE exactly as decoded.
- Live canvas piano roll, one colored lane per instrument, bar lines from the
  detected grid, click-to-seek, drag-on-ruler A/B loop.
- Playback: original audio plus per-instrument stems rendered through
  fluidsynth with MuseScore_General.sf2 (downloaded once, cached). Solo, mute,
  and gain per instrument and for the original. Speed 50-100%: stems are
  re-rendered with scaled note times so they keep pitch; the original uses
  playbackRate and drops in pitch like a slowed tape.
- Tempo detection falls back with a visible banner on rubato input instead of
  failing quietly.
- Exports: performance MIDI, MusicJSONL of the event stream, and the full
  engraved sheets tree (score.mid, score.musicxml, full_score.pdf, per
  instrument PDFs, tab PDFs for fretted instruments) as a zip via MuseScore 4+.
- Library of past transcriptions in SQLite with one-click reopen, stored under
  %LOCALAPPDATA%/earforge.
- Expected-instruments chips mask the model vocabulary through muscriptor's
  resolve_instrument_names, so phantom instruments cannot be decoded at all
  (verified against the real model: the same file unmasked yields three
  lanes; masked to bass and drums it yields exactly those two).
- A second upload while the GPU is busy says it is queued instead of looking
  hung, and speed-render caches are pruned per job (full speed plus the two
  most recent speeds) so a slider sweep cannot grow storage without bound.
- Speed control keeps pitch everywhere: stems re-render from scaled note
  times, the original recording through ffmpeg's atempo. Per-speed audio is
  cached server-side, and each mixer row can download its instrument as a
  standalone MIDI file.
- Uploaded filenames are sanitized before they reach storage, the library
  page, or a Content-Disposition header.
- Survives its own failures: a worker that dies mid-run (or a server restart)
  leaves a replayable event log, and the browser finalizes the job from the
  record instead of reconnecting forever. A startup sweep re-marks jobs that
  claimed "running" across a restart, uploads stream to disk instead of RAM,
  and stem/speed renders are atomic, so concurrent requests can never
  interleave writes into one file. The soundfont prefetches in the background
  at startup so the first play is not the first network hop.
- The roll caches its static scene offscreen; the animation frame only blits
  it and draws the playhead, active notes, and loop region, so long songs
  stay smooth. Arrow keys seek (2 s, Shift for 10), Space toggles playback.
- Labelled examples (repo only, not in the wheel): a synthesized demo track
  and a seedable example job so the workspace can be tried before the model
  weights finish downloading.
- Works against muscriptor 0.3.x on PyPI, which does not yet ship the sheets
  module or MIDI quantization that exist upstream: earforge prefers the
  upstream implementations when installed and otherwise uses ports of them
  (earforge/_sheets.py, earforge/midibridge.py).
