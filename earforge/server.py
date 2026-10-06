"""FastAPI server: jobs, SSE event stream, library, stems, exports, static UI."""

import asyncio
import json
import threading
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from muscriptor.tokenizer.mt3 import (
    MT3_FULL_PLUS_GROUP_NAMES,
    resolve_instrument_names,
)
from muscriptor.utils.download import ModelDownloadError

from earforge import __version__, exports, synth, transcribe
from earforge.library import Library

STATIC_DIR = Path(__file__).resolve().parent / "static"
MODELS = ("small", "medium", "large")
RATE_MIN, RATE_MAX = 0.5, 1.0


class ModelManager:
    """Loads TranscriptionModel lazily, one size at a time.

    Loading is the slow part (weights download/run) and happens inside the
    job worker so failures surface as job errors in the UI instead of killing
    the server at startup.
    """

    def __init__(self, default_model: str, factory: Callable[[str], object] | None = None):
        self.default_model = default_model
        self._factory = factory
        self._model = None
        self._size: str | None = None
        self._lock = threading.Lock()

    def get(self, size: str):
        with self._lock:
            if self._size != size:
                self._model = None
                self._size = None
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            if self._model is None:
                if self._factory is not None:
                    self._model = self._factory(size)
                else:
                    from muscriptor.transcription_model import TranscriptionModel

                    self._model = TranscriptionModel.load_model(size)
                self._size = size
            return self._model


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
            self.finished = True
            self._jsonl.close()
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


def _replay_file(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield _sse_line(json.loads(line))


def create_app(
    model: str = "medium",
    library: Library | None = None,
    model_factory: Callable[[str], object] | None = None,
) -> FastAPI:
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}; expected one of {MODELS}")
    app = FastAPI(title="earforge")
    lib = library or Library()
    lib.init()
    models = ModelManager(model, model_factory)
    runs: dict[str, JobRun] = {}
    runs_lock = threading.Lock()
    # one GPU transcription at a time, like muscriptor's own server
    gpu_lock = threading.Lock()

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
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

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
                run.emit({"type": "job_error", "message": str(e)})
                lib.update_job(job["id"], status="error", error=str(e))
                return
            if result.get("cancelled"):
                run.emit({"type": "job_cancelled"})
                lib.update_job(job["id"], status="cancelled")
                return

            lib.midi_path(job["id"]).write_bytes(result["midi_bytes"])
            if result["quantized_midi_bytes"] is not None:
                lib.quantized_midi_path(job["id"]).write_bytes(
                    result["quantized_midi_bytes"]
                )
            meta = {
                "type": "job_complete",
                "instruments": result["instruments"],
                "bpm": result["bpm"],
                "beats_per_bar": result["beats_per_bar"],
                "first_downbeat": result["first_downbeat"],
                "onset_delay": result["onset_delay"],
                "note_count": result["note_count"],
            }
            run.emit(meta)
            lib.update_job(
                job["id"],
                status="complete",
                instruments_found=result["instruments"],
                bpm=result["bpm"],
                beats_per_bar=result["beats_per_bar"],
                onset_delay=result["onset_delay"],
                note_count=result["note_count"],
            )
        except transcribe.TranscriptionError as e:
            run.emit({"type": "job_error", "message": str(e)})
            lib.update_job(job["id"], status="error", error=str(e))
        except Exception as e:  # noqa: BLE001 - the job boundary is the right place
            run.emit(
                {"type": "job_error", "message": f"transcription failed: {e}"}
            )
            lib.update_job(job["id"], status="error", error=str(e))
        finally:
            run.finish()
            with runs_lock:
                runs.pop(job["id"], None)

    @app.get("/api/config")
    async def config():
        cuda = None
        try:
            import torch

            cuda = torch.cuda.is_available()
        except Exception:
            cuda = False
        return {
            "version": __version__,
            "ffmpeg": transcribe.ffmpeg_path(),
            "fluidsynth": synth.fluidsynth_path(),
            "musescore": exports.sheets_available(),
            "cuda": cuda,
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

        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="uploaded file is empty")

        job = lib.create_job(file.filename or "audio", size, resolved)
        suffix = Path(file.filename or "").suffix or ".bin"
        upload_path = lib.job_dir(job["id"]) / f"upload{suffix}"
        upload_path.write_bytes(data)

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
            gen = _replay_file(path)
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        job = get_job_or_404(job_id)
        job["artifacts"] = {
            "audio": lib.source_wav(job_id).exists(),
            "midi": lib.midi_path(job_id).exists(),
            "jsonl": lib.events_path(job_id).exists(),
            "sheets": lib.sheets_zip(job_id).exists(),
        }
        return job

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
    async def job_audio(job_id: str):
        get_job_or_404(job_id)
        path = lib.source_wav(job_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="no audio for this job")
        return FileResponse(path, media_type="audio/wav")

    def job_notes(job_id: str) -> list[dict]:
        """All complete notes of a job, from its persisted event log."""
        path = lib.events_path(job_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="no events for this job")
        with open(path, encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        return transcribe.pair_events(events)

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
            try:
                await asyncio.to_thread(
                    synth.render_stem, notes, instrument, rate, stem_path
                )
            except synth.SynthUnavailable as e:
                raise HTTPException(status_code=503, detail=str(e)) from e
            except synth.StemError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e
        return FileResponse(stem_path, media_type="audio/wav")

    @app.get("/api/jobs/{job_id}/export/midi")
    async def export_midi(job_id: str):
        job = get_job_or_404(job_id)
        return _artifact_response(
            lib.midi_path(job_id), "no MIDI for this job",
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
        get_job_or_404(job_id)
        midi = lib.midi_path(job_id)
        if not midi.exists():
            raise HTTPException(status_code=404, detail="no MIDI for this job")
        quantized = lib.quantized_midi_path(job_id)
        try:
            zip_path = await asyncio.to_thread(
                exports.export_sheets_zip,
                midi.read_bytes(),
                quantized.read_bytes() if quantized.exists() else None,
                lib.job_dir(job_id),
            )
        except exports.ExportError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        return FileResponse(
            zip_path,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="sheets.zip"'},
        )

    @app.get("/api/jobs/{job_id}/export/sheets")
    async def download_sheets(job_id: str):
        get_job_or_404(job_id)
        zip_path = lib.sheets_zip(job_id)
        if not zip_path.exists():
            raise HTTPException(
                status_code=404,
                detail="sheets have not been engraved for this job yet",
            )
        return FileResponse(
            zip_path,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="sheets.zip"'},
        )

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
