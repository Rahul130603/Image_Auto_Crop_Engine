"""Local web UI server for PDF/image crop tool."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote, unquote

import fitz
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from crop_engine import (
    COLOR_MODES,
    DPI_OPTIONS,
    OUTPUT_FORMATS,
    CropSettings,
    run_crop_job,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
DEFAULT_OUTPUT = BASE_DIR / "output"

UPLOAD_DIR.mkdir(exist_ok=True)
DEFAULT_OUTPUT.mkdir(exist_ok=True)

app = FastAPI(title="Publishing Image Crop Tool")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class JobState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.cancel_requested = False
        self.messages: List[str] = []
        self.saved_count = 0
        self.uploaded_files: List[dict] = []
        self.primary_filename = ""
        self.total_pages = 0
        self.current_page = 0
        self.progress_percent = 0
        self.queue_status = "idle"
        self.error_message = ""
        self.current_file = ""
        self.output_dir: Optional[Path] = None
        self.output_images: List[str] = []
        self.job_started_at: Optional[float] = None

    def reset_cancel(self) -> None:
        with self.lock:
            self.cancel_requested = False

    def request_cancel(self) -> None:
        with self.lock:
            self.cancel_requested = True
            self.queue_status = "stopping"

    def is_cancelled(self) -> bool:
        with self.lock:
            return self.cancel_requested

    def append_log(self, msg: str) -> None:
        with self.lock:
            self.messages.append(msg)
            if len(self.messages) > 500:
                self.messages = self.messages[-500:]

    def update_status(self, data: dict) -> None:
        with self.lock:
            if "total_pages" in data:
                self.total_pages = int(data["total_pages"])
            if "current_page" in data:
                self.current_page = int(data["current_page"])
            if "progress_percent" in data:
                self.progress_percent = int(data["progress_percent"])
            if "current_file" in data:
                self.current_file = str(data["current_file"])
            if "queue_status" in data:
                self.queue_status = str(data["queue_status"])
            if "images_saved" in data:
                self.saved_count = int(data["images_saved"])
            if "last_output" in data:
                path = str(data["last_output"])
                if path not in self.output_images:
                    self.output_images.append(path)

    def clear_job(self) -> None:
        with self.lock:
            self.messages.clear()
            self.saved_count = 0
            self.progress_percent = 0
            self.current_page = 0
            self.total_pages = 0
            self.queue_status = "idle"
            self.error_message = ""
            self.current_file = ""
            self.output_images.clear()
            self.job_started_at = None

    def set_running(self, value: bool) -> None:
        with self.lock:
            self.running = value
            if value:
                self.job_started_at = time.time()
                self.queue_status = "running"
            else:
                self.cancel_requested = False
                if self.queue_status == "running":
                    self.queue_status = "done"
                if self.progress_percent < 100 and not self.error_message:
                    self.progress_percent = 100

    def snapshot(self) -> dict:
        with self.lock:
            elapsed = 0
            if self.job_started_at and self.running:
                elapsed = int(time.time() - self.job_started_at)
            thumbs = [
                {
                    "name": Path(p).name,
                    "url": f"/api/output-file?path={quote(str(Path(p).resolve()))}",
                }
                for p in self.output_images[-60:]
            ]
            return {
                "running": self.running,
                "cancel_requested": self.cancel_requested,
                "messages": list(self.messages),
                "saved_count": self.saved_count,
                "file_count": len(self.uploaded_files),
                "queued_files": [f.get("display_name", "") for f in self.uploaded_files],
                "primary_filename": self.primary_filename,
                "total_pages": self.total_pages,
                "current_page": self.current_page,
                "progress_percent": self.progress_percent,
                "queue_status": self.queue_status,
                "error_message": self.error_message,
                "current_file": self.current_file,
                "elapsed_seconds": elapsed,
                "output_images": thumbs,
            }


job = JobState()


class StartJobRequest(BaseModel):
    output_dir: str
    dpi: int = Field(default=300, ge=72, le=2400)
    color_mode: str = "rgb"
    output_format: str = "png"


def _pdf_page_count(path: Path) -> int:
    if path.suffix.lower() != ".pdf":
        return 0
    try:
        doc = fitz.open(path)
        n = len(doc)
        doc.close()
        return n
    except Exception:
        return 0


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "dpi_options": list(DPI_OPTIONS),
        "color_modes": list(COLOR_MODES),
        "output_formats": list(OUTPUT_FORMATS),
    }


@app.get("/api/select-folder")
def select_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise HTTPException(500, "Folder dialog not available") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    path = filedialog.askdirectory(title="Select output folder")
    root.destroy()
    return {"path": path or ""}


@app.get("/api/output-file")
def output_file(path: str):
    file_path = Path(unquote(path)).resolve()
    if job.output_dir:
        base = job.output_dir.resolve()
        try:
            file_path.relative_to(base)
        except ValueError as exc:
            raise HTTPException(403, "Invalid path") from exc
    elif str(file_path) not in job.output_images:
        raise HTTPException(403, "Invalid path")
    if not file_path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(file_path)


def _remove_uploaded_files() -> None:
    for entry in job.uploaded_files:
        path = entry.get("path") if isinstance(entry, dict) else entry
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
    job.uploaded_files.clear()


def _cleanup_upload_dir(keep: Optional[set] = None) -> None:
    keep = keep or set()
    for item in UPLOAD_DIR.glob("*"):
        if item.is_file() and item.resolve() not in keep:
            try:
                item.unlink(missing_ok=True)
            except OSError:
                pass


@app.post("/api/reset")
def reset_session():
    """Clear server queue on page load so refresh does not reuse old PDFs."""
    if job.running:
        return {"ok": True, "skipped": True, "reason": "job running"}
    _remove_uploaded_files()
    _cleanup_upload_dir()
    job.clear_job()
    with job.lock:
        job.primary_filename = ""
        job.total_pages = 0
    return {"ok": True}


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "No files uploaded")

    if job.running:
        raise HTTPException(409, "Wait until the current job finishes")

    # New upload replaces previous PDF(s) — do not append old files
    _remove_uploaded_files()
    job.clear_job()

    saved: List[dict] = []
    total_pages = 0
    primary_name = ""

    for upload in files:
        if not upload.filename:
            continue
        safe_name = Path(upload.filename).name
        dest = UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe_name}"
        data = await upload.read()
        dest.write_bytes(data)
        job.uploaded_files.append({"path": dest, "display_name": safe_name})
        pages = _pdf_page_count(dest)
        if pages:
            total_pages += pages
            if not primary_name:
                primary_name = safe_name
        saved.append({"name": safe_name, "size": len(data), "pages": pages})

    if not primary_name and saved:
        primary_name = saved[0]["name"]

    with job.lock:
        job.primary_filename = primary_name
        job.total_pages = total_pages or (1 if saved else 0)

    keep = {entry["path"].resolve() for entry in job.uploaded_files}
    _cleanup_upload_dir(keep=keep)

    job.append_log(f"Ready to process: {primary_name}")
    return {
        "files": saved,
        "count": len(saved),
        "primary_filename": primary_name,
        "total_pages": job.total_pages,
    }


@app.post("/api/clear-files")
def clear_files():
    if job.running:
        raise HTTPException(409, "Cannot clear while a job is running")
    _remove_uploaded_files()
    _cleanup_upload_dir()
    with job.lock:
        job.primary_filename = ""
        job.total_pages = 0
    job.clear_job()
    return {"ok": True}


@app.get("/api/status")
def status():
    return job.snapshot()


@app.post("/api/stop")
def stop_job():
    if job.running:
        job.request_cancel()
        job.append_log("Stop requested — finishing current step…")
    return {"ok": True}


@app.post("/api/start")
def start_job(body: StartJobRequest):
    if job.running:
        raise HTTPException(409, "A job is already running")

    if not job.uploaded_files:
        raise HTTPException(400, "Add files first (drag & drop or browse)")

    output_dir = Path(body.output_dir.strip())
    if not body.output_dir.strip():
        raise HTTPException(400, "Choose an output folder")

    dpi = body.dpi
    if dpi not in DPI_OPTIONS:
        dpi = min(DPI_OPTIONS, key=lambda d: abs(d - dpi))

    color_mode = body.color_mode.lower()
    if color_mode not in COLOR_MODES:
        raise HTTPException(400, f"Invalid mode. Use: {', '.join(COLOR_MODES)}")

    output_format = body.output_format.lower()
    if output_format not in OUTPUT_FORMATS:
        raise HTTPException(
            400, f"Invalid output format. Use: {', '.join(OUTPUT_FORMATS)}"
        )

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(400, f"Cannot create output folder: {exc}") from exc

    job.reset_cancel()
    job.clear_job()
    job.output_dir = output_dir
    job.set_running(True)
    job.append_log("Detecting images…")
    job.append_log(
        f"Starting — DPI: {dpi}, colour: {color_mode.upper()}, "
        f"save as: {output_format.upper()}, folder: {output_dir}"
    )

    def on_status(data: dict) -> None:
        job.update_status(data)

    settings = CropSettings(
        output_dir=output_dir,
        dpi=dpi,
        color_mode=color_mode,
        output_format=output_format,
        on_progress=job.append_log,
        on_status=on_status,
        should_cancel=job.is_cancelled,
    )

    paths = [entry["path"] for entry in job.uploaded_files]
    display_names = [entry["display_name"] for entry in job.uploaded_files]
    job.append_log("Queue: " + ", ".join(display_names))

    def worker() -> None:
        try:
            count = run_crop_job(paths, settings, display_names=display_names)
            job.saved_count = count
            job.append_log(f"Complete — {count} image(s) saved.")
        except Exception as exc:
            with job.lock:
                job.error_message = str(exc)
                job.queue_status = "error"
            job.append_log(f"Error: {exc}")
        finally:
            job.set_running(False)

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "started": True}


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(404, "UI not found")
    return FileResponse(index_path)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main():
    print("Open in browser: http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
