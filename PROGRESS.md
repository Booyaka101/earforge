# earforge PROGRESS

## 0.2.0 release preparation (2026-10-24)

- Owner authorized tag, push, release and publication after review. Route: feature branch and PR, passing CI on the exact release commit, then annotated tag, GitHub release and PyPI upload. Do not bypass failed checks.
- Release pass budget: at most 30 top-level tool calls or 30 minutes. Remaining questions: integrate the newer remote CI change, verify clean-environment hosted tests, validate built distributions, publish and verify matching public artifact hashes. Stop with a clear blocker if gates fail within the budget.
- Confirmed the final cache-bypassing Chrome run passed against real job `4b02bb46548f`: 12 notes, acoustic guitar/drums, export revision 10, zero captured workflow errors. Includes Reload saved conflict recovery, pause/resume and loop tests. `.e2e/v020-review-result.json` records the successful result. Both refreshed screenshots visually inspected on release preparation.
- Real MuseScore engraving remains unverified; this limitation must remain in the release notes. No new weight downloads or accounts are needed.

## Additional review and enhancement loop (2026-10-23)

- Completed bounded backend, frontend and playback review on the existing dirty branch. No commit, push, publication, new model run or weight download.
- Reproduced and fixed Unicode attachment-header crashes and empty/failed-upload orphan jobs. Shared attachment headers now use RFC 5987 encoding; incomplete upload cleanup includes task cancellation.
- Reproduced and fixed old-job audio/playhead retention, stale sheet downloads and controls re-enabled during grid saves, failed library opens, and duplicate zero-note workspace construction. Shared deferred-HTTP harness covers these paths.
- Added **Reload saved** to resolve two-tab conflicts without resetting audio, caches, notes or playback position; documented the control.
- Reproduced and fixed pause/resume counting paused time and loop look-ahead stopping live sources early. Queued takes no longer move the playhead early; delayed timers catch up to the current loop phase. Deterministic scheduler regressions pass.
- Final full Python run: **106 passed, 1 skipped in 17.15s**. Explicit Node: **10 passed, 0 failed**. Clone scan: **53 functions / 1378 pairs**, no similarity >=0.80. `git diff --check` passes with line-ending warnings only.
- Rebuilt and installed wheel passes `-I` package smoke with exact source/static bytes and model-free API/MIDI checks. SHA256: `2cfcbd5afd5ad533a474111e9e823e8a86f3c23284a1ea2040f770bd1fef3a70`. Existing dependency environment reused.
- Browser script now supports `EARFORGE_E2E_JOB` to reuse a recorded real transcription and adds two-tab conflict recovery, pause/resume and loop checks. The first rerun lacked the bundled FluidSynth PATH. The corrected run passed grid/exports/conflict recovery/reopen but failed the resume assertion with an old cached player asset. The final cache-bypassing run **passed**, including pause/resume and looping, with zero captured workflow errors. `.e2e/v020-review-result.json` records the successful outcome.
- Screenshots refreshed by browser checks. Real MuseScore engraving, fresh dependency installation and hosted Linux CI were still unverified at the end of this review. The original eight-point checklist and deferred features below apply subject to newer release evidence above.

## 0.2.0 upgrade ledger (2026-10-23, local verification complete)

- Branch: `feat/saved-musical-grid`, initially clean. No publication, push or model-weight download authorized or performed.
- Phase 0 passed: live release/tag, MIDI source, beats source and PyPI 0.4.0 verified. Tag resolves to `8a1ef88672cc23c26619adaff37ec48d82c0b906`. Code MIT; optional weights CC BY-NC 4.0; no paid service needed for corrections.
- Baseline: 67 passed, 1 GPU test skipped, both before and after installing the published MuScriptor 0.4.0 wheel. Dependency pinned to the verified version.
- Inspected: project docs, requested Python/frontend code, tests, installed upstream MIDI (1-492), BeatGrid (1-400), Note (1-180). Verified drum sentinel 128 and non-idempotent grid resolution.
- Implemented: original detected grid plus nullable manual options, additive migration, immutable revision-addressed MIDI bundles selected with SQLite compare-and-swap. Audio, stems and original event logs are not rewritten by corrections. Initial, corrected and per-instrument score MIDI share the model-free adapter; playback stays separate.
- Completed the three bounded passes: implementation, regression/browser verification, final review/packaging. No delegation, push, PR creation, merge, release, account creation or gated-weight download. Changes remain uncommitted on `feat/saved-musical-grid`.

### Final evidence

- Full suite: `python -m pytest -q --tb=short` -> **103 passed, 1 skipped in 16.20s**. Includes every `tests/js/*.test.mjs` suite through pytest.
- Explicit Node run: `node --test tests/js/*.test.mjs` -> **4 passed, 0 failed** (app wiring, grid, mixer, player).
- Cached real GPU run with `EARFORGE_GPU=1 HF_HUB_OFFLINE=1`: `python -m pytest -q -m gpu --tb=short` -> **1 passed, 103 deselected in 4.32s**. No weight download.
- Real Chrome/CDP E2E against the local server with a real 12-second upload: job `4b02bb46548f`, 12 notes, acoustic guitar and drums. Tested playback while saving, 120 BPM / 4 / 2.0 s ruler, unchanged audio/stem buffer and cache identities and note times, solo, byte-identical repeated exports, stale 409, library reopen, Auto/reset, MIDI/JSONL/per-instrument downloads, and sheets' missing-tool response. Zero captured workflow browser errors; listeners are installed after navigation, so this is not a claim to capture every boot-time exception.
- Fresh screenshots visually inspected: `docs/14-saved-grid.png` and `docs/15-reset-grid.png`.
- `difflib.SequenceMatcher` scan: 52 functions of at least 13 lines, 1326 pairs, **no matches at or above 0.80**. Python AST plus bounded JS function extraction; not a proof that every short fragment is unique. Local details: `.e2e/v020-similarity.json`.
- Installed MuScriptor wheel's `midi.py` and `beats.py` match the pinned upstream commit byte-for-byte after CRLF normalization. SHA256: `42b9565d1f965a6102d9f3befdc778afca4554e958c9e047382552842e3aded2` and `463cdfabc65a7fe2b2a0d2437f3bcfee9c0f6412a126375ffac3b3fb7de98c4f`.
- Built `.e2e/v020-dist/earforge-0.2.0-py3-none-any.whl`; SHA256 `b987ca2a3515fc14396988b4d62913d8000bcede1434eeef9f3002dd4f9cdf1a`. Installed with `--no-deps --target .e2e/v020-clean`; isolated-path smoke checked exact source/static bytes, version/config, static routes, model-free correction API and MIDI download. This reused the existing dependency environment, not a fresh full dependency installation. An initial `--no-isolation` build lacked hatchling; the standard isolated build succeeded without changing runtime dependencies.
- Final review caught and fixed a reproduced stale-completion workspace rebuild, late upload/audio/save/stream response hazards, solo/mute button-state reading, and HTTP cache reuse for revision-dependent exports. Regression tests cover these bounded paths. Earlier review added strict/nonfinite validation, SQLite rollback handling, truthful placeholder-tempo messaging, and a legacy-summary UI hint.
- `git diff --check` passed (line-ending warnings only). All source and tests remain local, including the new untracked files. No hosted CI has run.
- Cleanup: stopped only the recorded project server and launcher (PIDs 42356 and 34432); no listener remained on test port 8418. Unrelated processes and browser tabs were left alone.

Actual E2E output:

```text
REAL TRANSCRIPTION: {"notes":12,"instruments":["acoustic_guitar","drums"]}
BROWSER E2E PASS: real upload/transcription/playback, 120 BPM / 4 / 2s grid, unchanged buffers/notes, solo, repeated save, stale 409, reopen, Auto, reset, exports; 0 browser errors.
SHEETS: real engraving unavailable; 503 install hint verified
```

### Eight-point acceptance checklist

1. **PASS**: upstream release/tag/source/PyPI and free/local cost model verified; `muscriptor==0.4.0` pinned.
2. **PASS**: shared model-free `ExportOptions` / `notes_to_midi` adapter, existing GM mapping and drum sentinel 128; obsolete bridge/port removed.
3. **PASS**: additive migration; immutable original grid, nullable manual settings, resolved metadata and export revision persist. Legacy missing beat maps are not fabricated.
4. **PASS**: validated job-scoped save/reset, stale revision rejection, generate-before-commit bundles, failure cleanup and concurrency coverage.
5. **PASS**: current full/per-instrument MIDI and revision-specific sheets selection; audio/stem caches and original notes remain unchanged.
6. **PASS**: BPM/meter/downbeat controls, Auto/reset, progress/errors, reopen persistence, stale replay/late-response protection, tracked tempo and nullable meter.
7. **PASS**: worked timing example, onset-delay/pickup distinction, model-free regressions, full Python/JS suites, cached GPU run, real browser and fresh screenshots.
8. **PARTIAL**: 0.2.0 docs/version and rebuilt-wheel smoke pass. Real MuseScore engraving, fully fresh dependency installation and hosted CI on an exact committed revision remain unverified. Nothing published.

### WHAT DID WE MISS

- Real MusicXML/PDF/tab engraving cannot be validated without MuseScore 4. Fake-writer tests validate revision selection, packing and concurrent saves, not engraving quality.
- Hosted Linux CI and a fresh dependency solve/install have not run. Local checks used Windows and existing dependencies.
- This browser acceptance take is short. Historical long-file runs remain evidence for 0.1.0, not a new 0.2.0 long-file acceptance claim.
- Old successful export bundles intentionally accumulate to protect in-flight readers. Crash-orphaned, unselected bundles also have no garbage collector.

### WHAT FEATURES ARE MISSING

- Export-history/undo UI and safe bundle pruning.
- Advanced upstream export controls (beat scaling, subdivision/strength, gap filling and leading-bar trimming), arbitrary meter denominators and manual tempo-map editing.
- Deferred practice features: metronome, minus-one audio export and roll zoom.

### Local PR handoff and remaining work

- Suggested title: **Add persistent musical-grid corrections and model-free MIDI re-export (0.2.0)**.
- Summary: pin MuScriptor 0.4.0; replace the legacy export bridge; persist original/manual/resolved grids; atomically select revisioned exports; add correction controls without changing audition timing; add migration, failure, concurrency, JS and browser coverage.
- Review files with `git status --short` (ordinary `git diff --stat` omits new untracked files). Commit only this task's files, then push/open a PR when authorized. Do not release before CI passes on the exact commit.
- Optional owner step: install MuseScore 4, engrave a corrected job, change its grid and verify regenerated full score, MusicXML and instrument/tab PDFs.
- Remaining release checks: fresh dependency installation, hosted CI on the final commit, owner review and release approval. No merge/publication was performed.

## Historical 0.1.0 record

The 0.1.0 notes below are historical. Their no-token/no-real-model and pitch-changing limitations were superseded by the recorded real-model run and pitch-preserving atempo implementation. The release is already published; the old owner push instructions are obsolete. MuseScore availability must be rechecked for this upgrade.

Local, GPU-accelerated learn-by-ear workbench on top of muscriptor. **SHIPPED 2026-10-06: github.com/Booyaka101/earforge (tag v0.1.0, release with wheel attached) and pypi.org/project/earforge/0.1.0.** Verified installed from PyPI into a fresh venv (`pip install earforge` -> `earforge --version` -> 0.1.0).

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

Third review loop (loop 3): a startup sweep now re-marks jobs orphaned as "running" by a server restart (verified live: the seeded crashed job flips to a truthful error on boot and its partial notes still recover); uploads stream to disk in 8 MB chunks instead of holding the whole file in RAM; the 215 MB soundfont prefetches in a background thread at startup; a stem render failure from a network problem returns 502 with an actionable message instead of a bare 500; the worker's error path can no longer die on a broken emit (disk-full safe, finish() idempotent, SSE always closes); the armed loop and the ETA baseline no longer leak from one job into the next (drive-verified: loop button resets when reopening another job). A model crash mid-stream is covered by a test that asserts a clean job_error with the CUDA message.

Fourth review loop (loop 4), with the real model live for the first time: a 3:20 / 199 s real file transcribed in 65 s wall (3x realtime on the 4090), 1573 notes, tempo exactly 100 BPM, long-file playback verified to 2:04 mid-run; playback now starts during stem rendering (the original joins instantly, instruments drop in as rendered, no stale-rate stems). A real UI bug found and fixed: the tempo-fallback banner emitted during streaming was wiped by enterWorkspace, so rubato input completed with no visible warning; banners from the event stream now persist into the workspace (verified live on a synthesized rubato take: banner up, bpm null, transcription complete). Real cancel verified: cancelled at chunk 1/41 of a live job, stream closed cleanly, and the GPU served the next job normally. Cold-start page lag fixed (the torch-importing /api/config answer is computed once); Windows reserved filenames (CON.mp3) can no longer become device-name paths. Final state: 64 passed + 1 gpu (also passing with a token).

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

Fifth review loop (loop 5): the player's scheduling math gained headless unit tests (fake AudioContext recording every scheduled source; 10 assertions across play/pause/seek/stretch/loop/solo, run from pytest alongside the mix-gain tests, which now run every tests/js suite). Dead `setRate` removed. A second upload waiting on the GPU now emits a queued banner instead of looking hung (tested). Stem and speed-render caches are pruned per job to full-speed plus the two most recent speeds, capping what a slider sweep can write (tested against mtimes). Instrument-chip masking verified against the real model: the demo file unmasked yields electric_bass/drums/acoustic_guitar; masked to electric_bass+drums it yields exactly those two lanes. The player-test work also corrected the test's own misuse of the internal schedule API (span is derived, not passed), confirming the shipped math. Final state: 67 passed + 1 gpu (passing with a token), plus 17 JS assertions in node.

## Known limitations (documented in README)

- The real-model transcription path itself was not exercisable on this machine today (no HF token); it is the single owner-assisted step. Everything around it is real.
- Sheets engraving needs a real MuseScore 4 install; not present on this PC (verified the hidden-button and 503 paths instead, plus detection with fake binaries).
- One GPU transcription at a time (upstream's own constraint); tempo slider changes the original's pitch (stems keep pitch); no velocity recovery (model limitation, upstream issue #103).
