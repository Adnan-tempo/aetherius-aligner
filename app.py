"""The aligner: times a transcript against its recording, word by word.

Forced alignment with stable-ts (Whisper), on this server, with no speech API:
it is GIVEN the Society's words and asked only WHERE each one falls in the
audio, so it cannot invent or change text. The same model and method as
`scripts/align-transcript.py`, which produced Quick Start Day 1's times.

Flow:
  1. The `align-transcript` Supabase function checks the caller is an editor
     and POSTs {table, id} here with the shared ALIGNER_TOKEN.
  2. This answers 202 at once and queues the job (one at a time: the model
     is memory-hungry and recordings run to an hour).
  3. The worker reads the row, downloads the audio from the private
     `content-audio` bucket, aligns, and writes `transcript_words` back.

It refuses rather than guesses: if the aligner does not return exactly one
time per written word, nothing is written and the app shows plain text --
a wrong highlight is worse than none.

Environment:
  SUPABASE_URL                project URL
  SUPABASE_SERVICE_ROLE_KEY   reads the row, downloads audio, writes times
  ALIGNER_TOKEN               shared secret the Supabase function sends
  WHISPER_MODEL               default "base" (accurate enough when the text is known)
  PORT                        set by Railway
"""

import base64
import json
import logging
import os
import queue
import tempfile
import threading
import urllib.parse
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

log = logging.getLogger("aligner")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
TOKEN = os.environ.get("ALIGNER_TOKEN", "")
MODEL_NAME = os.environ.get("WHISPER_MODEL", "base")
BUCKET = "content-audio"
TABLES = {"journey_days", "teaching_episodes"}

app = FastAPI(title="Aetherius aligner")
jobs: "queue.Queue[tuple[str, str]]" = queue.Queue()
status: dict[str, str] = {}
_model = None


def model():
    """Loaded once, on first use, and kept: loading costs seconds and memory."""
    global _model
    if _model is None:
        import stable_whisper

        log.info("loading whisper model %s", MODEL_NAME)
        _model = stable_whisper.load_model(MODEL_NAME)
    return _model


def key_role(key: str) -> str:
    """Which role a Supabase key acts as, read from the key itself (no secret is printed)."""
    if key.startswith("sb_secret_"):
        return "service_role"
    if key.startswith("sb_publishable_"):
        return "anon"
    try:
        payload = key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role", "unknown")
    except (IndexError, ValueError):
        return "unknown"


def headers() -> dict[str, str]:
    # A new-format secret key is not a JWT: it goes in `apikey` only, and the
    # gateway mints the service_role token itself.
    if SERVICE_KEY.startswith("sb_"):
        return {"apikey": SERVICE_KEY}
    return {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}


def checked(response: httpx.Response) -> httpx.Response:
    """raise_for_status, but say what Supabase said -- a bare 403 hides the reason."""
    if response.is_error:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {response.text[:500]}"
        )
    return response


def align(audio_file: str, transcript: str) -> list[float] | None:
    # The app splits on whitespace and nothing else; count the same way.
    words = transcript.split()
    result = model().align(audio_file, transcript, language="en")
    times = [round(float(w.start), 2) for s in result.segments for w in s.words]
    if len(times) != len(words):
        log.warning("aligner gave %d times for %d words", len(times), len(words))
        return None
    # The database refuses a list that steps backwards; smooth rounding wobble.
    for i in range(1, len(times)):
        if times[i] < times[i - 1]:
            times[i] = times[i - 1]
    return times


def run(table: str, row_id: str) -> str:
    role = key_role(SERVICE_KEY)
    if role != "service_role":
        raise RuntimeError(
            f"SUPABASE_SERVICE_ROLE_KEY is a '{role}' key, not the service_role key. "
            "Copy service_role (or a secret key, sb_secret_...) from Supabase -> "
            "Project Settings -> API Keys."
        )
    with httpx.Client(timeout=600) as http:
        rows = checked(http.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            params={"id": f"eq.{row_id}", "select": "id,audio_path,transcript"},
            headers=headers(),
        )).json()
        if not rows:
            return "no such row"
        row = rows[0]
        transcript = (row.get("transcript") or "").strip()
        if not row.get("audio_path") or not transcript:
            return "no audio or no transcript"

        path = urllib.parse.quote(row["audio_path"])
        audio = checked(http.get(
            f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}", headers=headers()
        ))

        suffix = Path(row["audio_path"]).suffix or ".mp3"
        with tempfile.NamedTemporaryFile(suffix=suffix) as handle:
            handle.write(audio.content)
            handle.flush()
            times = align(handle.name, transcript)
        if times is None:
            return "transcript and recording do not line up; nothing written"

        checked(http.patch(
            f"{SUPABASE_URL}/rest/v1/{table}",
            params={"id": f"eq.{row_id}"},
            json={"transcript_words": times},
            headers={**headers(), "Prefer": "return=minimal"},
        ))
        return f"timed {len(times)} words"


def worker() -> None:
    while True:
        table, row_id = jobs.get()
        key = f"{table}:{row_id}"
        status[key] = "running"
        try:
            status[key] = run(table, row_id)
        except Exception as error:  # report and keep serving
            log.exception("job %s failed", key)
            status[key] = f"failed: {error}"
        log.info("%s -> %s", key, status[key])
        jobs.task_done()


threading.Thread(target=worker, daemon=True).start()


class Job(BaseModel):
    table: str
    id: str


def check(authorization: str | None) -> None:
    if not TOKEN or authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="Not allowed.")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "configured": bool(SUPABASE_URL and SERVICE_KEY and TOKEN), "queued": jobs.qsize()}


@app.post("/align", status_code=202)
def enqueue(job: Job, authorization: str | None = Header(default=None)) -> dict:
    check(authorization)
    if job.table not in TABLES:
        raise HTTPException(status_code=400, detail="Unknown table.")
    key = f"{job.table}:{job.id}"
    status[key] = "queued"
    jobs.put((job.table, job.id))
    return {"queued": True, "position": jobs.qsize()}


@app.get("/status/{table}/{row_id}")
def job_status(table: str, row_id: str, authorization: str | None = Header(default=None)) -> dict:
    check(authorization)
    return {"status": status.get(f"{table}:{row_id}", "unknown")}
