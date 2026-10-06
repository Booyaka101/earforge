# earforge PROGRESS

Local, GPU-accelerated learn-by-ear workbench on top of muscriptor. PyPI `earforge` 0.1.0. State: SHIPPED (v1 complete, pending the owner's PyPI/GitHub push from the phone).

## Phase 0 verification (2026-10-06, all live-fetched)

- muscriptor README verified: exact claims match (most accurate open-source transcription model, MIT code / CC BY-NC 4.0 weights, models 103M/307M/1.4B, Windows GPU via cu128 backend, sheets output tree, MuseScore 4+ requirement).
- muscriptor main.py, events.py, transcription_model.py, beats.py, sheets.py, auralization.py, download.py, soundfonts.py, mt3.py, server.py all fetched from raw.githubusercontent.
- Issues page verified: exactly 19 open, #109/#110 (MP3 handling), #102 (4090 speed), #71 (tempo grid, 7 reactions), #85 (engraving) all present with the named users/dates.
- pianoify README verified: "Mirelo bills 2.5 credits per second of input", "The box costs nothing per clip because the instance is rented by the hour", "muscriptor fork tailored for piano".

### The one gap Phase 0 caught late

muscriptor on PyPI (0.3.0, latest) lags GitHub main: the wheel has no `muscriptor/utils/sheets.py`, no `transcribe_and_postprocess`, and `events_to_midi_bytes` has no `quantize` parameter. Resolution (shipped): earforge probes for the upstream implementation first and otherwise uses `earforge/_sheets.py` (verbatim attributed port of upstream's MIT sheets module) plus `earforge/midibridge.py` (event-time quantizer, algebraically identical to upstream's note-level snap). Both go unused when a future muscriptor ships them.

## Cost model

No paid anything. Model weights gated-free (HF license acceptance + free token, owner one-time step the brief itself specifies). Soundfont ungated MIT (verified `gated: false` via the HF API). ffmpeg/fluidsynth/MuseScore are free optional system deps. NOT BLOCKED.

## Environment (this PC)

- RTX 4090 24 GB, driver 610.88. Python 3.11.9 default, 3.12 used for the venv.
- ffmpeg 9.0.2 and fluidsynth 2.6.1 bundled under tools/ (gitignored) for dev/test; absent system-wide.
- MuseScore: intentionally not installed (would need a system install outside the project folder); the sheets path degrades exactly as the brief's edge case requires and detection is tested with fake binaries.
- HF_TOKEN: absent machine-wide (env user+machine, ~/.cache/huggingface, ~/.huggingface). The model repo is `gated: "auto"` and account creation is forbidden to the agent, so the real transcription run is owner-assisted. Everything else was verified live.

## Verified working (real, not stubs)

**The real-model acceptance run happened (2026-10-06, after the owner pointed at the machine's HF token).** The token was already on the PC at `D:\hf-cache\token` (user env `HF_HOME=D:\hf-cache`, set up for earlier CivitAI/video-pipeline work); only the MuScriptor license acceptance was missing, done as a one-click form submit from the owner's logged-in HF session. With it:

- `EARFORGE_GPU=1 pytest -m gpu` -> **1 passed in 17.4 s**: real TranscriptionModel on the 4090, real click+scale wav, >=1 instrument, monotonic times, MThd.
- Real browser transcription of the 24 s demo MP3: ~11 s wall time, lanes `electric_bass / drums / acoustic_guitar`, 161 notes, tempo detected 100.036 BPM against a ground truth of 100. Playback, bass solo, stems, and all four export endpoints 200. Zero console exceptions. docs/demo.gif (the README hero), docs/10-real-transcription.png and docs/11-real-solo.png are from this run.
- The offline claim holds by construction: weights and soundfont are now in `D:\hf-cache` and every later run loads from disk.

`pytest -q`: 63 passed + 1 gpu (the gpu test now also passes when run with a token; it stays auto-skipped without one).
- Real ffmpeg conversion (stereo 44.1k wav and a 320 kbps mp3 to 32 kHz mono), corrupt-input and missing-ffmpeg error paths.
- Real fluidsynth stem rendering through the really-downloaded MuseScore_General.sf2 (215 MB, from the ungated MuScriptor/assets HF repo), including the half-speed re-render being twice as long.
- Server integration (stub model over the real FastAPI app): SSE pairing invariant, progress, tempo-fallback banner, cancel mid-run, instrument masking + abbreviation/ambiguity errors, model switch, exports (MThd MIDI, ndjson), library CRUD, delete-running refused, 503s with install hints, empty upload.
- Full browser drive over CDP against the built wheel served from a clean venv install (screenshots in docs/):
  - 35 instrument chips load; real mp3 dropped via the file input; real conversion; the REAL gated-download error rendered as setup instructions (02-auth-path.png).
  - Library lists jobs; the example job reopens through the real SSE replay path: 4 lanes, 208 notes, duration 24 s (04-workspace.png).
  - Stems rendered at 1.00 and 0.75, playback advances the playhead (0:01.2 -> 0:02.7), solo engages (05-solo-bass.png), loop arms ("Loop: 0.0s-14.8s"), rate 75% re-renders stems AND the original through atempo (06-loop-rate.png; originalBuffer 31.95 s for the 24 s take, audioCache ["1","0.75"]).
  - Per-instrument MIDI export answers 200 audio/midi; canvas painted (627k non-background pixels); MIDI export Content-Disposition correct; DELETE removes jobs.
  - Zero console exceptions across a full session after the review pass.

Bugs found by driving and fixed: mixer id mismatch killed the workspace (`gainOriginal` vs `gainoriginal`), EventSource reconnect loop after terminal events, blocking fluidsynth/MuseScore calls inside async endpoints, no subprocess timeouts, native-quantize grid field not carried forward. Review-pass fixes and enhancements: atempo factor initially inverted (caught by the pitch test), slider fetch storm (debounced), header/markup injection via uploaded filenames (sanitized + escaped), Space double-firing on focused buttons, frozen clock after Library -> Practice, roll not relaying out on window resize, model cache thrashing on size ping-pong (LRU of 2), dead code removed (`onSourcesStopped`, `jobMeta`, inline instrument-order clone).

Second review loop (loop 2): the hard-crash case (worker killed mid-run leaves DB status "running" with no terminal event) still reconnect-looped, because the client trusted the DB status; the job record now carries an `active` flag from the server's live run table and the browser finalizes from the record (verified live with a seeded crashed job: 100 partial notes recover into a working workspace, stream closed, stays settled). Stems and speed renders made atomic (temp + rename), which the new concurrency test used to catch a real Windows WinError 5 when one request streams a file another replaces. The roll's static scene moved to an offscreen layer (blit + playhead per frame instead of redrawing every note 60x/s), stale gain nodes from previous jobs are disconnected, arrow-key seek added, library rows show the model, MuseScore invocations no longer flash a console window on Windows, and a duplicated reset line plus a stale rafId that would have re-frozen the clock were caught and removed.

Third review loop (loop 3): a startup sweep now re-marks jobs orphaned as "running" by a server restart (verified live: the seeded crashed job flips to a truthful error on boot and its partial notes still recover); uploads stream to disk in 8 MB chunks instead of holding the whole file in RAM; the 215 MB soundfont prefetches in a background thread at startup; a stem render failure from a network problem returns 502 with an actionable message instead of a bare 500; the worker's error path can no longer die on a broken emit (disk-full safe, finish() idempotent, SSE always closes); the armed loop and the ETA baseline no longer leak from one job into the next (drive-verified: loop button resets when reopening another job). A model crash mid-stream is covered by a test that asserts a clean job_error with the CUDA message. Final state: 63 passed + 1 gpu-skipped, full CDP drive against the rebuilt wheel with zero console exceptions.

## Next steps for the owner (acceptance run, from the phone)

1. Create the GitHub repo (Booyaka101/earforge), push, then `pip install -e .` anywhere or `twine upload dist/earforge-0.1.0-py3-none-any.whl` (the wheel in dist/ is current).
2. The HF setup is DONE on this machine: token at `D:\hf-cache\token` (HF_HOME points there) and the muscriptor-medium license is accepted. If `large` is ever wanted, one more acceptance click on its model page.
3. Optional polish: a 3-minute real song run for the README's second example, and the sheets export once MuseScore 4 is installed.

## Feature candidates for the next release (not in v1, deliberate)

- "Minus one" export: render the mix minus soloed/muted instruments to WAV for playing along outside the browser.
- Metronome click during looped playback (the grid is known when tempo detection succeeded).
- Roll zoom (currently fixed fit-to-width with follow) and keyboard shortcuts beyond Space.
- Upstream's auralization (stereo original-vs-synth check mix) as an export.
- Swap the vendored `_sheets.py`/`midibridge` ports for upstream imports the day muscriptor ships them on PyPI (the code already prefers upstream when present; then delete the ports and the try/except).

Done in the review pass instead of deferred: pitch-preserving slowdown of the original (ffmpeg atempo), per-instrument MIDI download, upload-filename sanitization, model-size LRU cache, slider debounce, resize-aware roll, delete confirmation, and the Space-key/mixer-button focus conflict.

## Known limitations (documented in README)

- The real-model transcription path itself was not exercisable on this machine today (no HF token); it is the single owner-assisted step. Everything around it is real.
- Sheets engraving needs a real MuseScore 4 install; not present on this PC (verified the hidden-button and 503 paths instead, plus detection with fake binaries).
- One GPU transcription at a time (upstream's own constraint); tempo slider changes the original's pitch (stems keep pitch); no velocity recovery (model limitation, upstream issue #103).
