"""FastAPI server: jobs, SSE event stream, library, stems, exports, static UI."""

import asyncio
import json
import math
import sqlite3
import threading
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from muscriptor.soundfonts import SF2_URL
from muscriptor.tokenizer.mt3 import (
    MT3_FULL_PLUS_GROUP_NAMES,
    resolve_instrument_names,
)
from muscriptor.utils.download import ModelDownloadError, download_if_necessary

from earforge import __version__, corrections, exports, midibridge, synth, transcribe
from earforge.library import Library, StaleRevision

STATIC_DIR = Path(__file__).resolve().parent / "static"
MODELS = ("small", "medium", "large")
RATE_MIN, RATE_MAX = 0.5, 1.0


class GridSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    bpm: float | None = Field(default=None, ge=20, le=400, allow_inf_nan=False, strict=True)
    beats_per_bar: int | None = Field(default=None, ge=1, le=32, strict=True)
    first_downbeat: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False, strict=True)


class GridReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)


def completion(job):
    return {"type": "job_complete", "instruments": job["instruments_found"],
            **{k: job.get(k) for k in (
                "bpm", "beats_per_bar", "first_downbeat", "onset_delay", "note_count",
                "detected_grid", "resolved_grid", "manual_grid", "export_revision", "grid_origin",
            )}}


class ModelManager:
    """Loads TranscriptionModel lazily, caching the two most recent sizes.

    Loading is the slow part (weights download/run) and happens inside the
    job worker so failures surface as job errors in the UI instead of killing
    the server at startup. Keeping two sizes cached means switching medium ->
    large -> medium does not reload weights each time; VRAM stays bounded on
    small GPUs because the third-newest is evicted.
    """

    def __init__(self, default_model: str, factory: Callable[[str], object] | None = None):
        self.default_model = default_model
        self._factory = factory
        self._models: dict[str, object] = {}
        self._lock = threading.Lock()

    def get(self, size: str):
        with self._lock:
            if size not in self._models:
                if len(self._models) >= 2:
                    self._models.pop(next(iter(self._models)))
                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                if self._factory is not None:
                    self._models[size] = self._factory(size)
                else:
                    from muscriptor.transcription_model import TranscriptionModel

                    self._models[size] = TranscriptionModel.load_model(size)
            return self._models[size]


class JobRun:
    """Live event buffer feeding the SSE endpoint, mirrored to events.jsonl."""

    def __init__(self, job: dict, library: Library):
        self.job = job
        self.library = library
        self.cond = threading.Condition()
        self.events: list[dict] = []
        self.finished = False
        self.cancel = threading.Event()
        self._jsonl = open(library.events_path(job["id"]), "w", encoding="utf-8")

    def emit(self, obj: dict) -> None:
        with self.cond:
            self.events.append(obj)
            self._jsonl.write(json.dumps(obj) + "\n")
            self._jsonl.flush()
            self.cond.notify_all()

    def finish(self) -> None:
        with self.cond:
            if self.finished:
                return
            self.finished = True
            try:
                self._jsonl.close()
            except Exception:  # noqa: BLE001 - already closed or unwritable
                pass
            self.cond.notify_all()

    def stream(self):
        """Yield buffered events as SSE lines, live, until the job finishes.

        Snapshots are taken under the condition variable but yielded outside
        it: a slow client must never stall the worker's emit().
        """
        index = 0
        while True:
            with self.cond:
                batch = self.events[index:]
                index += len(batch)
                finished_now = self.finished and index >= len(self.events)
                waited = False
                if not batch and not finished_now:
                    self.cond.wait(timeout=15)
                    waited = True
                    batch = self.events[index:]
                    index += len(batch)
                    finished_now = self.finished and index >= len(self.events)
            for obj in batch:
                yield f"data: {json.dumps(obj)}\n\n"
            if finished_now:
                return
            if waited and not batch:
                yield ": keepalive\n\n"


def _sse_line(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _replay_file(path: Path, current=None):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                event = json.loads(line)
                if event.get("type") == "job_complete" and current is not None:
                    event = completion(current())
                yield _sse_line(event)


# CreateFile still maps these bare stems to devices on Windows, even with
# an extension and a directory prefix in some APIs
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _safe_name(filename: str | None) -> str:
    """A filename safe to store, print, and put in a Content-Disposition.

    Uploads carry whatever the client sent; quotes and newlines would break
    the header, path bits and angle brackets have no business in a stored name,
    and Windows device names must never become a stem.
    """
    name = Path(filename or "audio").name
    for ch in '"\'\r\n<>&':
        name = name.replace(ch, "_")
    stem = name.split(".")[0]
    if stem.upper() in _WINDOWS_RESERVED:
        name = "_" + name
    return name[:120] or "audio"


def _download_headers(filename: str) -> dict[str, str]:
    encoded = quote(filename, safe="")
    disposition = (f"attachment; filename*=utf-8''{encoded}" if encoded != filename
                   else f'attachment; filename="{filename}"')
    return {"Content-Disposition": disposition, "Cache-Control": "no-store"}


def create_app(
    model: str = "medium",
    library: Library | None = None,
    model_factory: Callable[[str], object] | None = None,
) -> FastAPI:
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}; expected one of {MODELS}")
    app = FastAPI(title="earforge")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # NaN/Infinity are invalid inputs, but echoing them in FastAPI's
        # default validation response would itself fail JSON serialization.
        detail = jsonable_encoder(exc.errors(), custom_encoder={
            float: lambda value: value if math.isfinite(value) else str(value),
        })
        return JSONResponse(status_code=422, content={"detail": detail})

    lib = library or Library()
    lib.init()
    models = ModelManager(model, model_factory)
    runs: dict[str, JobRun] = {}
    runs_lock = threading.Lock()
    # one GPU transcription at a time, like muscriptor's own server
    gpu_lock = threading.Lock()

    # a server restart kills workers without a goodbye; their DB rows would
    # claim "running" forever. Nothing is running yet, so anything still
    # marked running is an orphan of a previous process.
    for row in lib.list_jobs():
        if row["status"] == "running":
            lib.update_job(
                row["id"],
                status="error",
                error="interrupted: the earforge server restarted mid-run",
            )

    # the soundfont is a one-time 215 MB download; pull it in the background
    # so the first stem render is not the first time the network is needed
    if synth.available():

        def prefetch_soundfont():
            try:
                download_if_necessary(SF2_URL)
            except Exception:
                pass  # the render path surfaces the real error with context

        threading.Thread(target=prefetch_soundfont, daemon=True).start()

    def get_job_or_404(job_id: str) -> dict:
        job = lib.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"no job {job_id}")
        return job

    def _stem(job: dict) -> str:
        return Path(job["source_name"]).stem

    def _artifact_response(path: Path, missing: str, media_type: str, filename: str):
        if not path.exists():
            raise HTTPException(status_code=404, detail=missing)
        return FileResponse(
            path,
            media_type=media_type,
            headers=_download_headers(filename),
        )

    def fail(run: JobRun, job_id: str, message: str) -> None:
        """End a job as an error without letting a broken emit eat the cleanup.

        If the disk is full, emitting the error can throw just like the event
        that got us here; the stream must still be closed and the row updated.
        """
        try:
            run.emit({"type": "job_error", "message": message})
        except Exception:  # noqa: BLE001 - nothing left to report through
            pass
        try:
            lib.update_job(job_id, status="error", error=message)
        except Exception:  # noqa: BLE001
            pass

    def worker(run: JobRun, upload_path: Path, instruments: list[str] | None) -> None:
        job = run.job
        try:
            duration = transcribe.convert_audio(upload_path, lib.source_wav(job["id"]))
            upload_path.unlink(missing_ok=True)
            lib.update_job(job["id"], duration_s=duration)
            run.emit(
                {
                    "type": "job_start",
                    "id": job["id"],
                    "source_name": job["source_name"],
                    "model": job["model"],
                    "duration_s": duration,
                }
            )
            if run.cancel.is_set():
                run.emit({"type": "job_cancelled"})
                lib.update_job(job["id"], status="cancelled")
                return
            # say so before parking on the GPU lock: a second upload can wait
            # minutes behind a long one, and silence reads as a hang
            if gpu_lock.locked():
                run.emit({
                    "type": "banner", "kind": "queued",
                    "message": "Another transcription is running; this one starts when the GPU is free.",
                })
            try:
                with gpu_lock:
                    loaded = models.get(job["model"])
                    result = transcribe.run_transcription(
                        loaded,
                        lib.source_wav(job["id"]),
                        run.emit,
                        run.cancel,
                        instruments=instruments,
                    )
            except ModelDownloadError as e:
                fail(run, job["id"], str(e))
                return
            if result.get("cancelled"):
                run.emit({"type": "job_cancelled"})
                lib.update_job(job["id"], status="cancelled")
                return

            key = lib.write_export(job["id"], 0, result["midi_bytes"], result["quantized_midi_bytes"])
            lib.update_job(
                job["id"],
                status="complete",
                instruments_found=result["instruments"],
                bpm=result["bpm"],
                beats_per_bar=result["beats_per_bar"],
                onset_delay=result["onset_delay"],
                note_count=result["note_count"],
                first_downbeat=result["first_downbeat"],
                detected_grid=result["detected_grid"],
                resolved_grid=result["resolved_grid"],
                export_key=key,
            )
            run.emit(completion(lib.get_job(job["id"])))
        except transcribe.TranscriptionError as e:
            fail(run, job["id"], str(e))
        except Exception as e:  # noqa: BLE001 - the job boundary is the right place
            fail(run, job["id"], f"transcription failed: {e}")
        finally:
            try:
                run.finish()
            finally:
                with runs_lock:
                    runs.pop(job["id"], None)

    cuda_checked = False
    cuda_result = None

    @app.get("/api/config")
    async def config():
        """The first call a page makes; the torch import costs seconds on a
        cold start, so the answer is computed once per process."""
        nonlocal cuda_checked, cuda_result
        if not cuda_checked:
            try:
                import torch

                cuda_result = torch.cuda.is_available()
            except Exception:
                cuda_result = False
            cuda_checked = True
        return {
            "version": __version__,
            "ffmpeg": transcribe.ffmpeg_path(),
            "fluidsynth": synth.fluidsynth_path(),
            "musescore": exports.sheets_available(),
            "cuda": cuda_result,
            "model_default": models.default_model,
            "models": list(MODELS),
            "data_dir": str(lib.root),
        }

    @app.get("/api/instruments")
    async def instruments():
        return {"instruments": list(MT3_FULL_PLUS_GROUP_NAMES.keys())}

    @app.post("/api/jobs")
    async def create_job(
        file: UploadFile = File(...),
        model: str = Form(""),
        instruments: str = Form(""),
    ):
        if transcribe.ffmpeg_path() is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "ffmpeg was not found on PATH. earforge uses it to decode"
                    " audio. Install it from https://ffmpeg.org/download.html"
                    " and restart earforge."
                ),
            )
        size = model or models.default_model
        if size not in MODELS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown model {model!r}; expected one of {MODELS}",
            )
        tokens = [t for t in instruments.split(",") if t.strip()]
        try:
            resolved = resolve_instrument_names(tokens) if tokens else []
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail=f"{e}. Ask /api/instruments for the names."
            ) from e

        job = lib.create_job(_safe_name(file.filename), size, resolved)
        suffix = Path(_safe_name(file.filename)).suffix or ".bin"
        # stream to disk in chunks: a large upload must not be held in RAM
        data_size = 0
        upload_tmp = lib.job_dir(job["id"]) / f"upload{suffix}.part"
        try:
            with open(upload_tmp, "wb") as sink:
                while chunk := await file.read(8 * 1024 * 1024):
                    data_size += len(chunk)
                    sink.write(chunk)
            if data_size == 0:
                raise HTTPException(status_code=400, detail="uploaded file is empty")
            upload_path = lib.job_dir(job["id"]) / f"upload{suffix}"
            upload_tmp.replace(upload_path)
        except BaseException:
            # Include client/task cancellation before a worker owns this job.
            lib.delete_job(job["id"])
            raise

        run = JobRun(job, lib)
        with runs_lock:
            runs[job["id"]] = run
        thread = threading.Thread(
            target=worker, args=(run, upload_path, resolved or None), daemon=True
        )
        thread.start()
        return {"id": job["id"]}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        get_job_or_404(job_id)
        with runs_lock:
            run = runs.get(job_id)
        if run is None:
            raise HTTPException(status_code=409, detail="job is not running")
        run.cancel.set()
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str):
        get_job_or_404(job_id)
        with runs_lock:
            run = runs.get(job_id)
        if run is not None:
            gen = run.stream()
        else:
            path = lib.events_path(job_id)
            if not path.exists():
                raise HTTPException(status_code=404, detail="no events for this job")
            gen = _replay_file(path, lambda: get_job_or_404(job_id))
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        job = get_job_or_404(job_id)
        with runs_lock:
            job["active"] = job_id in runs
        job["artifacts"] = {
            "audio": lib.source_wav(job_id).exists(),
            "midi": lib.midi_path(job_id, job).exists(),
            "jsonl": lib.events_path(job_id).exists(),
            "sheets": lib.sheets_zip(job_id, job).exists(),
        }
        return job

    async def apply_grid(job_id, expected, manual):
        job = get_job_or_404(job_id)
        downbeat = manual.get("first_downbeat")
        if downbeat is not None and job["duration_s"] is not None and downbeat > job["duration_s"]:
            raise HTTPException(status_code=422, detail="first downbeat must lie within the recording")
        try:
            return await asyncio.to_thread(corrections.save_grid, lib, job, expected, manual)
        except StaleRevision as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as e:
            raise HTTPException(status_code=500, detail=f"Could not regenerate MIDI; previous revision preserved: {e}") from e

    @app.put("/api/jobs/{job_id}/grid")
    async def save_grid(job_id: str, request: GridSave):
        return await apply_grid(job_id, request.expected_revision, {
            "bpm": request.bpm, "beats_per_bar": request.beats_per_bar,
            "first_downbeat": request.first_downbeat,
        })

    @app.post("/api/jobs/{job_id}/grid/reset")
    async def reset_grid(job_id: str, request: GridReset):
        return await apply_grid(job_id, request.expected_revision, {})

    @app.get("/api/library")
    async def list_jobs():
        return {"jobs": lib.list_jobs()}

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str):
        get_job_or_404(job_id)
        with runs_lock:
            if job_id in runs:
                raise HTTPException(
                    status_code=409,
                    detail="job is still running; cancel it first",
                )
        if not lib.delete_job(job_id):
            raise HTTPException(status_code=404, detail=f"no job {job_id}")
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/audio")
    async def job_audio(job_id: str, rate: float = 1.0):
        get_job_or_404(job_id)
        if not (RATE_MIN <= rate <= RATE_MAX):
            raise HTTPException(
                status_code=400,
                detail=f"rate must be between {RATE_MIN} and {RATE_MAX}",
            )
        path = lib.source_wav(job_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="no audio for this job")
        if rate != 1.0:
            stretched = lib.job_dir(job_id) / "audio_rates" / f"{rate:.2f}.wav"
            if not stretched.exists():
                try:
                    await asyncio.to_thread(
                        transcribe.atomic_render,
                        lambda out: transcribe.render_audio_at_rate(path, rate, out),
                        stretched,
                    )
                except transcribe.TranscriptionError as e:
                    raise HTTPException(status_code=503, detail=str(e)) from e
            lib.prune_rate_caches(job_id)
            path = stretched
        return FileResponse(path, media_type="audio/wav")

    def job_notes(job_id: str) -> list[dict]:
        """All complete notes of a job, from its persisted event log."""
        if not lib.events_path(job_id).exists():
            raise HTTPException(status_code=404, detail="no events for this job")
        return corrections.stored_notes(lib, job_id)

    @app.get("/api/jobs/{job_id}/stems/{instrument}")
    async def job_stem(job_id: str, instrument: str, rate: float = 1.0):
        get_job_or_404(job_id)
        if not (RATE_MIN <= rate <= RATE_MAX):
            raise HTTPException(
                status_code=400,
                detail=f"rate must be between {RATE_MIN} and {RATE_MAX}",
            )
        notes = [n for n in job_notes(job_id) if n["instrument"] == instrument]
        if not notes:
            raise HTTPException(
                status_code=404, detail=f"no notes for instrument '{instrument}'"
            )
        stem_path = lib.stems_dir(job_id) / f"{rate:.2f}" / f"{instrument}.wav"
        if not stem_path.exists():

            def render(out_path: Path) -> None:
                synth.render_stem(notes, instrument, rate, out_path)

            try:
                await asyncio.to_thread(transcribe.atomic_render, render, stem_path)
            except synth.SynthUnavailable as e:
                raise HTTPException(status_code=503, detail=str(e)) from e
            except synth.StemError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e
            except transcribe.TranscriptionError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e
            except Exception as e:  # noqa: BLE001 - e.g. a failed soundfont fetch
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"could not render the '{instrument}' stem: {e}."
                        " If the machine is offline, reconnect once so the"
                        " MuseScore_General soundfont can finish downloading;"
                        " it is cached after that."
                    ),
                ) from e
            lib.prune_rate_caches(job_id)
        return FileResponse(stem_path, media_type="audio/wav")

    @app.get("/api/jobs/{job_id}/export/midi")
    async def export_midi(job_id: str):
        job = get_job_or_404(job_id)
        return _artifact_response(
            lib.midi_path(job_id, job), "no MIDI for this job",
            "audio/midi", f"{_stem(job)}.mid",
        )

    @app.get("/api/jobs/{job_id}/export/jsonl")
    async def export_jsonl(job_id: str):
        job = get_job_or_404(job_id)
        return _artifact_response(
            lib.events_path(job_id), "no events for this job",
            "application/x-ndjson", f"{_stem(job)}.events.jsonl",
        )

    @app.post("/api/jobs/{job_id}/export/sheets")
    async def export_sheets(job_id: str):
        job = get_job_or_404(job_id)
        midi = lib.midi_path(job_id, job)
        if not midi.exists():
            raise HTTPException(status_code=404, detail="no MIDI for this job")
        quantized = lib.quantized_midi_path(job_id, job)
        try:
            zip_path = await asyncio.to_thread(
                exports.export_sheets_zip,
                midi.read_bytes(),
                quantized.read_bytes() if quantized.exists() else None,
                lib.export_dir(job),
            )
        except exports.ExportError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        if get_job_or_404(job_id)["export_revision"] != job["export_revision"]:
            raise HTTPException(status_code=409, detail="Grid changed while engraving. Export sheets again.")
        return FileResponse(
            zip_path,
            media_type="application/zip",
            headers=_download_headers("sheets.zip"),
        )

    @app.get("/api/jobs/{job_id}/export/stem/{instrument}.mid")
    async def export_stem_midi(job_id: str, instrument: str):
        """One instrument with the same corrected conductor and pickup as the score."""
        job = get_job_or_404(job_id)
        notes = [n for n in job_notes(job_id) if n["instrument"] == instrument]
        if not notes:
            raise HTTPException(
                status_code=404, detail=f"no notes for instrument '{instrument}'"
            )
        midi = await asyncio.to_thread(midibridge.midi_bytes, notes,
                                      midibridge.grid_from_dict(job["detected_grid"]), job["manual_grid"])
        return Response(
            content=midi,
            media_type="audio/midi",
            headers=_download_headers(f"{_stem(job)} - {instrument}.mid"),
        )

    @app.get("/api/jobs/{job_id}/export/sheets")
    async def download_sheets(job_id: str):
        job = get_job_or_404(job_id)
        zip_path = lib.sheets_zip(job_id, job)
        if not zip_path.exists():
            raise HTTPException(
                status_code=404,
                detail="sheets have not been engraved for this job yet",
            )
        return FileResponse(
            zip_path,
            media_type="application/zip",
            headers=_download_headers("sheets.zip"),
        )

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
