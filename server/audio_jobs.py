from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.plugin_system import PluginType, create
from data import EMOTION, normalize_emotion
from logger import logger


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class AudioJob:
    job_id: str
    status: str
    text: str
    emotion: str
    created_at: str
    updated_at: str
    file_path: str | None = None
    error: str | None = None
    request_perf_ts: float = field(default_factory=time.perf_counter, repr=False)
    queued_perf_ts: float = field(default_factory=time.perf_counter, repr=False)
    first_file_served_perf_ts: float | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AudioJobManager:
    def __init__(self, context, output_dir: str = "tmp/audio_jobs"):
        self.context = context
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.jobs: dict[str, AudioJob] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self.worker_task and not self.worker_task.done():
            return
        self.worker_task = asyncio.create_task(self._worker_loop(), name="audio-jobs-worker")
        logger.info("audio jobs worker started")

    async def shutdown(self) -> None:
        if not self.worker_task:
            return
        self.worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.worker_task
        self.worker_task = None
        logger.info("audio jobs worker stopped")

    async def submit(self, text: str, emotion_raw: Any, request_perf_ts: float | None = None) -> dict[str, Any]:
        if not text or not str(text).strip():
            raise ValueError("text is required")

        emotion = self._normalize_emotion(emotion_raw)
        now = _now_iso()
        job_id = uuid.uuid4().hex

        job = AudioJob(
            job_id=job_id,
            status="queued",
            text=str(text),
            emotion=emotion.name,
            created_at=now,
            updated_at=now,
            request_perf_ts=request_perf_ts if request_perf_ts is not None else time.perf_counter(),
            queued_perf_ts=time.perf_counter(),
        )
        self.jobs[job_id] = job
        await self.queue.put(job_id)
        logger.info("audio job queued: job_id=%s emotion=%s text_len=%d", job_id, job.emotion, len(job.text))
        return job.to_dict()

    def get(self, job_id: str) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        return job.to_dict()

    def mark_file_served(self, job_id: str) -> float | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if job.first_file_served_perf_ts is not None:
            return None
        job.first_file_served_perf_ts = time.perf_counter()
        return max(0.0, job.first_file_served_perf_ts - job.request_perf_ts)

    async def _worker_loop(self):
        while True:
            job_id = await self.queue.get()
            job = self.jobs.get(job_id)
            if job is None:
                self.queue.task_done()
                continue

            try:
                self._set_job_status(job, "running")
                queue_wait_sec = max(0.0, time.perf_counter() - getattr(job, "queued_perf_ts", time.perf_counter()))
                logger.info("audio job running: job_id=%s", job_id)
                logger.info("[httpfile-prof] audio_queue_wait_sec=%.3f job_id=%s", queue_wait_sec, job_id)
                job_run_start = time.perf_counter()
                file_path = await asyncio.to_thread(self._run_job_sync, job.job_id, job.text, job.emotion)
                job.file_path = file_path
                self._set_job_status(job, "succeeded")
                logger.info("audio job succeeded: job_id=%s file=%s", job_id, file_path)
                logger.info("[httpfile-prof] audio_job_total_sec=%.3f job_id=%s", time.perf_counter() - job_run_start, job_id)
            except Exception as exc:
                job.error = str(exc)
                self._set_job_status(job, "failed")
                logger.exception("audio job failed: job_id=%s", job_id)
            finally:
                self.queue.task_done()

    @staticmethod
    def _set_job_status(job: AudioJob, status: str) -> None:
        job.status = status
        job.updated_at = _now_iso()

    @staticmethod
    def _normalize_emotion(value: Any) -> EMOTION:
        return normalize_emotion(value, strict=True)

    def _run_job_sync(self, job_id: str, text: str, emotion_name: str) -> str:
        output_path = self.output_dir / f"{job_id}.wav"
        if output_path.exists():
            output_path.unlink()

        tts = create(
            PluginType.TTS,
            self.context.config.plugins.tts,
            config=self.context.config,
            parent=None,
        )
        if not hasattr(tts, "synthesize_to_wav"):
            raise RuntimeError(f"tts plugin '{self.context.config.plugins.tts}' does not support audio_jobs")

        emotion = self._normalize_emotion(emotion_name)
        logger.info("audio job synthesis started: job_id=%s tts=%s", job_id, self.context.config.plugins.tts)
        metrics = tts.synthesize_to_wav(text=text, emotion=emotion, output_path=str(output_path))
        logger.info(
            "[httpfile-prof] audio_tts_sec=%.3f audio_duration_sec=%.3f segments=%s job_id=%s",
            float(metrics.get("tts_total_sec", 0.0)),
            float(metrics.get("audio_duration_sec", 0.0)),
            metrics.get("segments", "n/a"),
            job_id,
        )

        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError("audio output missing")
        return str(output_path)

