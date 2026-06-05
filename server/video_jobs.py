from __future__ import annotations

import asyncio
import contextlib
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data import EMOTION, DEFAULT_EMOTION, normalize_emotion
from logger import logger
from server.rtc_runtime import build_nerfreal, generate_session_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class VideoJob:
    job_id: str
    status: str
    text: str
    emotion: str
    user_id: str
    created_at: str
    updated_at: str
    file_path: str | None = None
    error: str | None = None
    request_perf_ts: float = field(default_factory=time.perf_counter, repr=False)
    queued_perf_ts: float = field(default_factory=time.perf_counter, repr=False)
    first_file_served_perf_ts: float | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VideoJobManager:
    def __init__(self, context, output_dir: str = "tmp/video_jobs"):
        self.context = context
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.httpfile_batch_cap = max(1, int(self.context.config.transport.httpfile_batch_cap))
        logger.info("httpfile batch cap configured from yaml: %s", self.httpfile_batch_cap)

        self.jobs: dict[str, VideoJob] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self.worker_task and not self.worker_task.done():
            return
        self.worker_task = asyncio.create_task(self._worker_loop(), name="video-jobs-worker")
        logger.info("video jobs worker started")

    async def shutdown(self) -> None:
        if not self.worker_task:
            return
        self.worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.worker_task
        self.worker_task = None
        logger.info("video jobs worker stopped")

    async def submit(
        self,
        text: str,
        emotion_raw: Any,
        user_id_raw: Any | None = None,
        request_perf_ts: float | None = None,
    ) -> dict[str, Any]:
        if not text or not str(text).strip():
            raise ValueError("text is required")

        emotion = self._normalize_emotion(emotion_raw)
        user_id = self._normalize_user_id(user_id_raw)
        now = _now_iso()
        job_id = uuid.uuid4().hex

        job = VideoJob(
            job_id=job_id,
            status="queued",
            text=str(text),
            emotion=emotion.name,
            user_id=user_id,
            created_at=now,
            updated_at=now,
            request_perf_ts=request_perf_ts if request_perf_ts is not None else time.perf_counter(),
            queued_perf_ts=time.perf_counter(),
        )
        self.jobs[job_id] = job
        await self.queue.put(job_id)
        logger.info("video job queued: job_id=%s user_id=%s emotion=%s text_len=%d", job_id, job.user_id, job.emotion, len(job.text))
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
                logger.info("video job running: job_id=%s", job_id)
                logger.info("[httpfile-prof] queue_wait_sec=%.3f job_id=%s", queue_wait_sec, job_id)
                job_run_start = time.perf_counter()
                file_path = await asyncio.to_thread(self._run_job_sync, job.job_id, job.text, job.emotion, job.user_id)
                job.file_path = file_path
                self._set_job_status(job, "succeeded")
                logger.info("video job succeeded: job_id=%s file=%s", job_id, file_path)
                logger.info(
                    "[httpfile-prof] job_total_sec=%.3f job_id=%s",
                    time.perf_counter() - job_run_start,
                    job_id,
                )
            except Exception as exc:
                job.error = str(exc)
                self._set_job_status(job, "failed")
                logger.exception("video job failed: job_id=%s", job_id)
            finally:
                self.queue.task_done()

    @staticmethod
    def _set_job_status(job: VideoJob, status: str) -> None:
        job.status = status
        job.updated_at = _now_iso()

    @staticmethod
    def _normalize_emotion(value: Any) -> EMOTION:
        return normalize_emotion(value, strict=True)

    def _normalize_user_id(self, value: Any | None) -> str:
        text = str(value or "").strip()
        if not text:
            text = getattr(self.context.config.renderer, "user_id", "default")
        safe = "".join(ch for ch in text if ch.isalnum() or ch in {"-", "_", "."})
        return safe or "default"

    def _prepare_record_resolution(self, session) -> None:
        if getattr(session, "width", 0) > 0 and getattr(session, "height", 0) > 0:
            return

        frame = None
        if getattr(session, "multi_avatar", False):
            avatars = getattr(session, "avatars", {})
            avatar = avatars.get(DEFAULT_EMOTION)
            if avatar is not None and getattr(avatar, "frame_list_cycle", None):
                frame = avatar.frame_list_cycle[0]
        elif getattr(session, "frame_list_cycle", None):
            frame = session.frame_list_cycle[0]

        if frame is None:
            raise RuntimeError("cannot resolve avatar frame size for recording")

        session.height, session.width = frame.shape[:2]
        logger.info("video job recorder size set: %dx%d", session.width, session.height)

    async def _drive_session_recording(self, job_id: str, session, text: str, emotion: EMOTION, user_id: str, output_path: Path):
        t_drive_start = time.perf_counter()
        original_batch_size = getattr(session, "batch_size", None)
        if self.context.config.transport.mode == "httpfile" and original_batch_size is not None:
            effective_batch_size = min(int(original_batch_size), self.httpfile_batch_cap)
            if effective_batch_size != original_batch_size:
                session.batch_size = effective_batch_size
                if getattr(session, "asr", None) is not None:
                    session.asr.batch_size = effective_batch_size
                logger.info(
                    "httpfile runtime batch size adjusted: original=%s effective=%s cap=%s job_id=%s",
                    original_batch_size,
                    effective_batch_size,
                    self.httpfile_batch_cap,
                    job_id,
                )
        render_quit_event = threading.Event()
        render_thread = threading.Thread(
            name=f"httpfile-render-{job_id[:8]}",
            target=session.render,
            args=(render_quit_event,),
            daemon=True,
        )
        render_thread.start()
        produced = Path("data/record.mp4")
        if produced.exists():
            produced.unlink()

        record_started = False
        first_speaking_ts = None
        t_record_start = None
        tail_idle_wait_sec = 0.4
        try:
            await asyncio.sleep(0.2)
            self._prepare_record_resolution(session)
            session.start_recording()
            t_record_start = time.perf_counter()
            record_started = True
            logger.info("video job record started: job_id=%s", job_id)
            session.put_msg_txt(text, {"emo": emotion, "user_id": user_id, "llm_status": "end"})

            started_speaking = False
            last_speaking_ts = time.time()
            start_wait_deadline = time.time() + 90
            finish_deadline = time.time() + max(180, min(900, len(text) * 2 + 120))
            speech_wait_start = time.perf_counter()
            tts_empty_since = None
            asr_empty_since = None

            while time.time() < finish_deadline:
                speaking = session.is_speaking()
                if speaking:
                    started_speaking = True
                    last_speaking_ts = time.time()
                    if first_speaking_ts is None:
                        first_speaking_ts = time.perf_counter()
                        logger.info(
                            "[httpfile-prof] time_to_first_speaking_sec=%.3f job_id=%s",
                            first_speaking_ts - speech_wait_start,
                            job_id,
                        )

                if not started_speaking and time.time() > start_wait_deadline:
                    raise TimeoutError("speech did not start in expected time")

                tts_empty = session.tts.msgqueue.empty()
                asr_empty = session.asr.queue.empty()
                if started_speaking:
                    if tts_empty:
                        if tts_empty_since is None:
                            tts_empty_since = time.time()
                    else:
                        tts_empty_since = None
                    if asr_empty:
                        if asr_empty_since is None:
                            asr_empty_since = time.time()
                    else:
                        asr_empty_since = None

                    if (time.time() - last_speaking_ts) >= tail_idle_wait_sec and tts_empty and asr_empty:
                        logger.info("video job reached stable idle tail: job_id=%s", job_id)
                        break

                await asyncio.sleep(0.05)
            else:
                raise TimeoutError("video job timed out before completion")

            if started_speaking:
                logger.info(
                    "[httpfile-prof] completion_summary job_id=%s speaking_to_end_sec=%.3f tts_empty_tail_sec=%s asr_empty_tail_sec=%s",
                    job_id,
                    time.perf_counter() - first_speaking_ts if first_speaking_ts is not None else -1.0,
                    f"{(time.time() - tts_empty_since):.3f}" if tts_empty_since is not None else "n/a",
                    f"{(time.time() - asr_empty_since):.3f}" if asr_empty_since is not None else "n/a",
                )

        finally:
            if record_started and getattr(session, "recording", False):
                t_stop_record_begin = time.perf_counter()
                session.stop_recording()
                logger.info("video job record stopped: job_id=%s", job_id)
                if t_record_start is not None:
                    logger.info(
                        "[httpfile-prof] record_active_sec=%.3f job_id=%s",
                        time.perf_counter() - t_record_start,
                        job_id,
                    )
                stop_record_sec = time.perf_counter() - t_stop_record_begin
                logger.info(
                    "[httpfile-prof] stop_record_sec=%.3f job_id=%s",
                    stop_record_sec,
                    job_id,
                )

            t_stop_render_begin = time.perf_counter()
            render_quit_event.set()
            render_thread.join(timeout=3.0)
            direct_render_stop_sec = time.perf_counter() - t_stop_render_begin
            logger.info(
                "video job render stopped: job_id=%s stop_sec=%.3f alive=%s",
                job_id,
                direct_render_stop_sec,
                render_thread.is_alive(),
            )

        if not produced.exists() or produced.stat().st_size <= 0:
            raise RuntimeError("record output missing: data/record.mp4")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            output_path.unlink()
        t_move_begin = time.perf_counter()
        logger.info("video job moving output: job_id=%s from=%s to=%s", job_id, produced, output_path)
        shutil.move(str(produced), str(output_path))
        move_output_sec = time.perf_counter() - t_move_begin
        drive_total_sec = time.perf_counter() - t_drive_start
        logger.info("video job output ready: job_id=%s path=%s size=%d", job_id, output_path, output_path.stat().st_size)
        logger.info(
            "[httpfile-prof] move_output_sec=%.3f drive_total_sec=%.3f job_id=%s",
            move_output_sec,
            drive_total_sec,
            job_id,
        )

    def _run_job_sync(self, job_id: str, text: str, emotion_name: str, user_id: str) -> str:
        sessionid = generate_session_id(self.context, 6)
        session = build_nerfreal(self.context, sessionid, user_id=user_id)
        self.context.nerfreals[sessionid] = session

        output_path = self.output_dir / f"{job_id}.mp4"
        emotion = self._normalize_emotion(emotion_name)

        logger.info("video job session created: job_id=%s sessionid=%s user_id=%s", job_id, sessionid, user_id)
        try:
            asyncio.run(self._drive_session_recording(job_id, session, text, emotion, user_id, output_path))
            return str(output_path)
        finally:
            self.context.nerfreals.pop(sessionid, None)
            logger.info("video job session released: job_id=%s sessionid=%s", job_id, sessionid)
