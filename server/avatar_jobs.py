from __future__ import annotations

import asyncio
import contextlib
import json
import mimetypes
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

import aiohttp

from data import DEFAULT_EMOTION, EMOTION, EMOTION_SEQUENCE, normalize_emotion
from logger import logger


_RESULT_NODE_ID = "633"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _is_video_name(text: str) -> bool:
    lowered = text.lower()
    return lowered.endswith(".mp4") or lowered.endswith(".mov") or lowered.endswith(".mkv")


@dataclass
class AvatarJob:
    job_id: str
    status: str
    avatar_id: str
    emotions: list[str]
    created_at: str
    updated_at: str
    image_path: str
    results: dict[str, str] = field(default_factory=dict)
    transition_results: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    emotion_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    transition_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    current_emotion: str | None = None
    error: str | None = None
    request_perf_ts: float = field(default_factory=time.perf_counter, repr=False)
    queued_perf_ts: float = field(default_factory=time.perf_counter, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AvatarJobManager:
    def __init__(self, context, assets_dir: str = "assets"):
        self.context = context
        self.config = context.config.avatar_jobs
        self.assets_dir = Path(assets_dir)

        self.output_dir = Path(self.config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = Path(self.config.tmp_dir)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self.jobs: dict[str, AvatarJob] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker_task: asyncio.Task | None = None

        self.emotion_video_map = self._normalize_emotion_video_map(self.config.emotion_video_map)
        self.emotion_prompt_map = self._normalize_emotion_prompt_map(self.config.emotion_prompts)

    @staticmethod
    def _avatar_emotions() -> list[EMOTION]:
        return list(EMOTION_SEQUENCE)

    async def start(self) -> None:
        if not self.config.enabled:
            logger.info("avatar jobs disabled")
            return
        if self.worker_task and not self.worker_task.done():
            return
        self.worker_task = asyncio.create_task(self._worker_loop(), name="avatar-jobs-worker")
        logger.info("avatar jobs worker started")

    async def shutdown(self) -> None:
        if not self.worker_task:
            return
        self.worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.worker_task
        self.worker_task = None
        logger.info("avatar jobs worker stopped")

    async def submit(
        self,
        image_bytes: bytes,
        image_filename: str,
        avatar_id: str | None = None,
        emotions_raw: Any | None = None,
        positive_prompt: str | None = None,
        negative_prompt: str | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            raise RuntimeError("avatar jobs are disabled")
        if not image_bytes:
            raise ValueError("image file is required")

        avatar_id = _safe_text(avatar_id) or uuid.uuid4().hex[:8]
        emotions = self._normalize_emotions(emotions_raw)
        now = _now_iso()
        job_id = uuid.uuid4().hex

        job_dir = self.tmp_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        image_path = job_dir / self._safe_filename(image_filename or "input.png")
        image_path.write_bytes(image_bytes)

        job = AvatarJob(
            job_id=job_id,
            status="queued",
            avatar_id=avatar_id,
            emotions=[item.value for item in emotions],
            created_at=now,
            updated_at=now,
            image_path=str(image_path),
            request_perf_ts=time.perf_counter(),
            queued_perf_ts=time.perf_counter(),
        )
        for emotion in emotions:
            self._set_emotion_status(job, emotion.value, "queued", message="等待处理")
        for source, target in self._transition_pairs(emotions):
            self._set_transition_status(job, self._transition_name(source, target), "queued", message="等待处理")
        self.jobs[job_id] = job
        await self.queue.put(job_id)
        logger.info("avatar job queued: job_id=%s avatar_id=%s emotions=%s", job_id, avatar_id, job.emotions)
        job.extra["positive_prompt"] = _safe_text(positive_prompt)
        job.extra["negative_prompt"] = _safe_text(negative_prompt)
        return job.to_dict()

    def get(self, job_id: str) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        return job.to_dict()

    def get_options(self) -> dict[str, Any]:
        emotions = []
        for emotion in self._avatar_emotions():
            path = None
            try:
                path = str(self._resolve_emotion_video_path(emotion))
                available = True
            except FileNotFoundError:
                available = False
            emotions.append({"name": emotion.value, "video_path": path, "available": available})
        return {
            "emotions": emotions,
            "enable_transition": bool(self.config.enable_transition),
            "positive_prompt_template": self.config.positive_prompt_template,
            "emotion_prompts": dict(self.emotion_prompt_map),
            "negative_prompt": self.config.negative_prompt,
        }

    async def _worker_loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            job = self.jobs.get(job_id)
            if job is None:
                self.queue.task_done()
                continue

            try:
                self._set_job_status(job, "running")
                queue_wait_sec = max(0.0, time.perf_counter() - getattr(job, "queued_perf_ts", time.perf_counter()))
                logger.info("avatar job running: job_id=%s", job_id)
                logger.info("[avatar-prof] queue_wait_sec=%.3f job_id=%s", queue_wait_sec, job_id)
                job_run_start = time.perf_counter()

                await self._run_job(job)

                status = "succeeded"
                if job.errors:
                    status = "partial" if job.results else "failed"
                self._set_job_status(job, status)
                logger.info(
                    "avatar job completed: job_id=%s status=%s results=%d errors=%d",
                    job_id,
                    status,
                    len(job.results),
                    len(job.errors),
                )
                logger.info(
                    "[avatar-prof] job_total_sec=%.3f job_id=%s",
                    time.perf_counter() - job_run_start,
                    job_id,
                )
            except Exception as exc:
                job.error = str(exc)
                self._set_job_status(job, "failed")
                logger.exception("avatar job failed: job_id=%s", job_id)
            finally:
                job.current_emotion = None
                self.queue.task_done()

    async def _run_job(self, job: AvatarJob) -> None:
        image_path = Path(job.image_path)
        if not image_path.exists():
            raise FileNotFoundError("image file missing")

        if not self.config.api_base_url:
            raise RuntimeError("avatar_jobs.api_base_url is required")

        api_base = self.config.api_base_url.rstrip("/")
        timeout = aiohttp.ClientTimeout(total=self.config.poll_timeout_sec + 120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            image_ref = await self._upload_file(session, image_path)
            for emotion_name in job.emotions:
                emotion = normalize_emotion(emotion_name, strict=True)
                job.current_emotion = emotion.value
                try:
                    video_path = self._resolve_emotion_video_path(emotion)
                    video_ref = await self._upload_file(session, video_path)

                    positive_prompt = self._build_positive_prompt(job, emotion)
                    negative_prompt = self._build_negative_prompt(job)

                    prompt_id = await self._submit_generate(
                        session,
                        api_base=api_base,
                        image_ref=image_ref,
                        video_ref=video_ref,
                        positive_prompt=positive_prompt,
                        negative_prompt=negative_prompt,
                    )
                    self._set_emotion_status(
                        job,
                        emotion.value,
                        "api_submitted",
                        message="已提交api任务",
                        prompt_id=prompt_id,
                        positive_prompt=positive_prompt,
                    )
                    result_payload = await self._poll_result(session, api_base, prompt_id)
                    video_url = self._extract_video_url(api_base, result_payload)
                    if not video_url:
                        raise RuntimeError("result video url not found")

                    output_path = self._output_path(job.avatar_id, emotion.value, job.job_id)
                    await self._download_file(session, video_url, output_path)
                    job.results[emotion.value] = str(output_path)
                    self._set_emotion_status(
                        job,
                        emotion.value,
                        "video_done",
                        message="收到视频结果",
                        video_path=str(output_path),
                    )
                    logger.info("avatar job emotion done: job_id=%s emotion=%s file=%s", job.job_id, emotion.value, output_path)
                except Exception as exc:
                    job.errors[emotion.value] = str(exc)
                    self._set_emotion_status(job, emotion.value, "failed", message=str(exc))
                    logger.exception("avatar job emotion failed: job_id=%s emotion=%s", job.job_id, emotion.value)
                    if not self.config.continue_on_error:
                        raise

            await self._run_transition_video_generation(job, session, api_base, image_ref)

        await self._run_musetalk_generation(job)
        await self._run_transition_frame_generation(job)

    @staticmethod
    def _set_job_status(job: AvatarJob, status: str) -> None:
        job.status = status
        job.updated_at = _now_iso()

    @staticmethod
    def _set_emotion_status(job: AvatarJob, emotion: str, status: str, **fields: Any) -> None:
        current = job.emotion_status.setdefault(emotion, {})
        current["status"] = status
        current["updated_at"] = _now_iso()
        for key, value in fields.items():
            if value is not None:
                current[key] = value
        job.updated_at = _now_iso()

    @staticmethod
    def _set_transition_status(job: AvatarJob, transition: str, status: str, **fields: Any) -> None:
        current = job.transition_status.setdefault(transition, {})
        current["status"] = status
        current["updated_at"] = _now_iso()
        for key, value in fields.items():
            if value is not None:
                current[key] = value
        job.updated_at = _now_iso()

    def _normalize_emotions(self, raw: Any | None) -> list[EMOTION]:
        def without_default(items: Iterable[EMOTION]) -> list[EMOTION]:
            return [item for item in items if item != DEFAULT_EMOTION]

        if raw is None:
            return self._avatar_emotions()
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return self._avatar_emotions()
            if text.startswith("["):
                try:
                    loaded = json.loads(text)
                    return self._normalize_emotions(loaded)
                except json.JSONDecodeError:
                    pass
            items = [item.strip() for item in text.split(",") if item.strip()]
            if not items:
                return self._avatar_emotions()
            normalized = without_default(normalize_emotion(item, strict=True) for item in items)
            return normalized or self._avatar_emotions()
        if isinstance(raw, Iterable):
            normalized = without_default(normalize_emotion(item, strict=True) for item in raw)
            return normalized or self._avatar_emotions()
        return self._avatar_emotions()

    def _normalize_emotion_video_map(self, raw_map: dict[str, str] | None) -> dict[str, str]:
        mapping: dict[str, str] = {}
        if not raw_map:
            return mapping
        for key, value in raw_map.items():
            normalized = _safe_text(key).upper()
            if not normalized or not value:
                continue
            mapping[normalized] = str(value)
        return mapping

    def _normalize_emotion_prompt_map(self, raw_map: dict[str, str] | None) -> dict[str, str]:
        mapping: dict[str, str] = {}
        if not raw_map:
            return mapping
        for key, value in raw_map.items():
            prompt = _safe_text(value)
            if not prompt:
                continue
            try:
                emotion = normalize_emotion(key, strict=True)
            except ValueError:
                logger.warning("ignore unsupported avatar job emotion prompt key: %s", key)
                continue
            mapping[emotion.name] = prompt
        return mapping

    def _resolve_emotion_video_path(self, emotion: EMOTION) -> Path:
        override = self.emotion_video_map.get(emotion.name)
        if override:
            return self._resolve_path(override)

        candidates = [
            f"{emotion.name}.mp4",
            f"{emotion.value}.mp4",
            f"{emotion.name.lower()}.mp4",
        ]
        for name in candidates:
            candidate = self.assets_dir / name
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"emotion video missing in assets: {emotion.value}")

    def _resolve_transition_video_path(self, source: EMOTION, target: EMOTION) -> Path | None:
        name = self._transition_name(source, target)
        candidates = [
            self.assets_dir / "transitions" / f"{name}.mp4",
            self.assets_dir / "transitions" / f"{name.lower()}.mp4",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _resolve_path(self, path_text: str) -> Path:
        path = Path(path_text)
        if path.is_absolute():
            return path
        return Path.cwd() / path

    def _output_path(self, avatar_id: str, emotion: str, job_id: str) -> Path:
        safe_avatar = self._safe_filename(avatar_id)
        safe_emotion = self._safe_filename(emotion)
        out_dir = self.output_dir / safe_avatar
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{safe_emotion}_{job_id[:8]}.mp4"

    def _transition_output_path(self, avatar_id: str, transition_name: str, job_id: str) -> Path:
        safe_avatar = self._safe_filename(avatar_id)
        safe_transition = self._safe_filename(transition_name)
        out_dir = self.output_dir / safe_avatar / "transitions"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{safe_transition}_{job_id[:8]}.mp4"

    def _user_output_dir(self, avatar_id: str) -> Path:
        return self.output_dir / self._safe_filename(avatar_id)

    def _musetalk_user_id(self, avatar_id: str) -> str:
        return self._safe_filename(avatar_id)

    def _musetalk_avatar_id(self, emotion: EMOTION) -> str:
        return self._safe_filename(emotion.value)

    def _musetalk_avatar_dir(self, avatar_id: str, emotion: EMOTION) -> Path:
        return Path("data") / self._musetalk_user_id(avatar_id) / "avatars" / self._musetalk_avatar_id(emotion)

    def _transition_frames_dir(self, avatar_id: str, transition_name: str) -> Path:
        return Path("data") / self._musetalk_user_id(avatar_id) / "transitions" / self._safe_filename(transition_name)

    @staticmethod
    def _transition_name(source: EMOTION, target: EMOTION) -> str:
        return f"{source.name}2{target.name}"

    def _transition_pairs(self, emotions: Iterable[EMOTION]) -> list[tuple[EMOTION, EMOTION]]:
        if not self.config.enable_transition:
            return []

        selected = []
        seen = set()
        for emotion in emotions:
            if emotion == DEFAULT_EMOTION:
                continue
            if emotion in seen:
                continue
            seen.add(emotion)
            selected.append(emotion)

        pairs = []
        for emotion in selected:
            for source, target in ((DEFAULT_EMOTION, emotion), (emotion, DEFAULT_EMOTION)):
                if self._resolve_transition_video_path(source, target) is not None:
                    pairs.append((source, target))
        return pairs

    def _latest_emotion_video_path(self, avatar_id: str, emotion: EMOTION) -> Path | None:
        user_dir = self._user_output_dir(avatar_id)
        if not user_dir.exists():
            return None
        prefixes = {emotion.name, emotion.value, emotion.name.lower(), emotion.value.lower()}
        candidates = []
        for path in user_dir.iterdir():
            if not path.is_file() or not _is_video_name(path.name):
                continue
            stem = path.stem
            stem_lower = stem.lower()
            if any(stem == prefix or stem.startswith(f"{prefix}_") for prefix in prefixes) or any(
                stem_lower == prefix or stem_lower.startswith(f"{prefix}_") for prefix in prefixes
            ):
                candidates.append(path)
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.stat().st_mtime)

    async def _run_transition_video_generation(
        self,
        job: AvatarJob,
        session: aiohttp.ClientSession,
        api_base: str,
        image_ref: str,
    ) -> None:
        emotions = [normalize_emotion(item, strict=True) for item in job.emotions]
        for source, target in self._transition_pairs(emotions):
            transition_name = self._transition_name(source, target)
            job.current_emotion = transition_name
            try:
                template_path = self._resolve_transition_video_path(source, target)
                if template_path is None:
                    self._set_transition_status(
                        job,
                        transition_name,
                        "skipped",
                        message="transition template missing",
                    )
                    continue

                video_ref = await self._upload_file(session, template_path)
                positive_prompt = self._build_transition_positive_prompt(job, source, target)
                negative_prompt = self._build_negative_prompt(job)
                prompt_id = await self._submit_generate(
                    session,
                    api_base=api_base,
                    image_ref=image_ref,
                    video_ref=video_ref,
                    positive_prompt=positive_prompt,
                    negative_prompt=negative_prompt,
                )
                self._set_transition_status(
                    job,
                    transition_name,
                    "transition_api_submitted",
                    message="已提交transition api任务",
                    prompt_id=prompt_id,
                    template_path=str(template_path),
                )

                result_payload = await self._poll_result(session, api_base, prompt_id)
                video_url = self._extract_video_url(api_base, result_payload)
                if not video_url:
                    raise RuntimeError("transition result video url not found")

                output_path = self._transition_output_path(job.avatar_id, transition_name, job.job_id)
                await self._download_file(session, video_url, output_path)
                job.transition_results[transition_name] = str(output_path)
                self._set_transition_status(
                    job,
                    transition_name,
                    "transition_video_done",
                    message="收到transition视频结果",
                    video_path=str(output_path),
                )
                logger.info(
                    "avatar job transition done: job_id=%s transition=%s file=%s",
                    job.job_id,
                    transition_name,
                    output_path,
                )
            except Exception as exc:
                job.errors[transition_name] = str(exc)
                self._set_transition_status(job, transition_name, "failed", message=str(exc))
                logger.exception("avatar job transition failed: job_id=%s transition=%s", job.job_id, transition_name)
                if not self.config.continue_on_error:
                    raise

    async def _run_musetalk_generation(self, job: AvatarJob) -> None:
        manifest_items: list[dict[str, str]] = []
        musetalk_dirs: dict[str, str] = {}

        for emotion_name in job.emotions:
            emotion = normalize_emotion(emotion_name, strict=True)
            latest_video = self._latest_emotion_video_path(job.avatar_id, emotion)
            if latest_video is None:
                if emotion.value not in job.errors:
                    message = f"latest result video not found for emotion: {emotion.value}"
                    job.errors[emotion.value] = message
                    self._set_emotion_status(job, emotion.value, "failed", message=message)
                continue

            user_id = self._musetalk_user_id(job.avatar_id)
            avatar_id = self._musetalk_avatar_id(emotion)
            avatar_dir = self._musetalk_avatar_dir(job.avatar_id, emotion)
            self._set_emotion_status(
                job,
                emotion.value,
                "musetalk_submitted",
                message="已提交MuseTalk任务",
                video_path=str(latest_video),
                avatar_id=avatar_id,
                avatar_dir=str(avatar_dir),
                user_id=user_id,
            )
            manifest_items.append(
                {
                    "file": str(latest_video),
                    "avatar_id": avatar_id,
                    "user_id": user_id,
                    "output_dir": str(avatar_dir),
                }
            )
            musetalk_dirs[emotion.value] = str(avatar_dir)

        if not manifest_items:
            logger.info("avatar job musetalk skipped: job_id=%s no videos", job.job_id)
            return

        manifest_path = Path(job.image_path).parent / "musetalk_manifest.json"
        manifest_path.write_text(json.dumps({"items": manifest_items}, ensure_ascii=False, indent=2), encoding="utf-8")
        job.extra["musetalk_manifest"] = str(manifest_path)
        job.extra["musetalk_avatar_dirs"] = musetalk_dirs

        start = time.perf_counter()
        script_path = Path.cwd() / "genavatar_musetalk.py"
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            "--batch_manifest",
            str(manifest_path),
            "--clean",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.cwd()),
        )
        stdout, stderr = await proc.communicate()
        stdout_text = stdout.decode(errors="replace").strip()
        stderr_text = stderr.decode(errors="replace").strip()

        if proc.returncode != 0:
            message = f"MuseTalk generation failed: exit={proc.returncode}"
            if stderr_text:
                message = f"{message} stderr={stderr_text[-1000:]}"
            for item in manifest_items:
                emotion_value = item["avatar_id"]
                job.errors[emotion_value] = message
                self._set_emotion_status(job, emotion_value, "failed", message=message)
            logger.error("avatar job musetalk failed: job_id=%s %s", job.job_id, message)
            if not self.config.continue_on_error:
                raise RuntimeError(message)
            return

        for item in manifest_items:
            emotion_value = item["avatar_id"]
            avatar_dir = Path(item["output_dir"])
            self._set_emotion_status(
                job,
                emotion_value,
                "musetalk_done",
                message="已完成MuseTalk资源生成",
                avatar_id=item["avatar_id"],
                user_id=item["user_id"],
                avatar_dir=str(avatar_dir),
            )
        self._reload_renderer_user_resource(job, self._musetalk_user_id(job.avatar_id))
        logger.info(
            "avatar job musetalk done: job_id=%s items=%d sec=%.3f stdout=%s",
            job.job_id,
            len(manifest_items),
            time.perf_counter() - start,
            stdout_text[-1000:] if stdout_text else "",
        )

    def _reload_renderer_user_resource(self, job: AvatarJob, user_id: str) -> None:
        prepared = getattr(self.context, "renderer_prepared", None)
        reload_fn = getattr(prepared, "reload_user_resource", None)
        if reload_fn is None:
            return
        try:
            reload_fn(user_id)
            job.extra["renderer_user_reloaded"] = user_id
        except Exception as exc:
            job.extra["renderer_user_reload_error"] = str(exc)
            logger.warning("avatar job renderer user reload failed: job_id=%s user_id=%s error=%s", job.job_id, user_id, exc)

    async def _run_transition_frame_generation(self, job: AvatarJob) -> None:
        manifest_items = []
        for transition_name, video_path in job.transition_results.items():
            output_dir = self._transition_frames_dir(job.avatar_id, transition_name)
            self._set_transition_status(
                job,
                transition_name,
                "transition_frames_submitted",
                message="已提交transition转帧任务",
                video_path=video_path,
                frames_dir=str(output_dir),
            )
            manifest_items.append(
                {
                    "name": transition_name,
                    "file": video_path,
                    "output_dir": str(output_dir),
                }
            )

        if not manifest_items:
            logger.info("avatar job transition frames skipped: job_id=%s no transition videos", job.job_id)
            return

        manifest_path = Path(job.image_path).parent / "transition_manifest.json"
        manifest_path.write_text(json.dumps({"items": manifest_items}, ensure_ascii=False, indent=2), encoding="utf-8")
        job.extra["transition_manifest"] = str(manifest_path)
        job.extra["transition_dirs"] = {item["name"]: item["output_dir"] for item in manifest_items}

        script_path = Path.cwd() / "gen_transition.py"
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            "--manifest",
            str(manifest_path),
            "--clean",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.cwd()),
        )
        stdout, stderr = await proc.communicate()
        stdout_text = stdout.decode(errors="replace").strip()
        stderr_text = stderr.decode(errors="replace").strip()

        if proc.returncode != 0:
            message = f"transition frame generation failed: exit={proc.returncode}"
            if stderr_text:
                message = f"{message} stderr={stderr_text[-1000:]}"
            for item in manifest_items:
                job.errors[item["name"]] = message
                self._set_transition_status(job, item["name"], "failed", message=message)
            logger.error("avatar job transition frames failed: job_id=%s %s", job.job_id, message)
            if not self.config.continue_on_error:
                raise RuntimeError(message)
            return

        for item in manifest_items:
            self._set_transition_status(
                job,
                item["name"],
                "transition_done",
                message="已完成transition资源生成",
                video_path=item["file"],
                frames_dir=item["output_dir"],
            )
        self._reload_renderer_user_resource(job, self._musetalk_user_id(job.avatar_id))
        logger.info(
            "avatar job transition frames done: job_id=%s items=%d stdout=%s",
            job.job_id,
            len(manifest_items),
            stdout_text[-1000:] if stdout_text else "",
        )

    def _build_positive_prompt(self, job: AvatarJob, emotion: EMOTION) -> str:
        template = self.config.positive_prompt_template
        override = getattr(job, "extra", {}).get("positive_prompt")
        if override:
            template = override
        else:
            emotion_prompt = self.emotion_prompt_map.get(emotion.name)
            if emotion_prompt:
                template = emotion_prompt
        if not template:
            return emotion.value
        return template.format(emotion=emotion.value, emotion_name=emotion.name)

    def _build_transition_positive_prompt(self, job: AvatarJob, source: EMOTION, target: EMOTION) -> str:
        template = self.config.positive_prompt_template
        override = getattr(job, "extra", {}).get("positive_prompt")
        if override:
            template = override
        transition_text = f"{source.value} to {target.value}"
        if not template:
            return transition_text
        return template.format(
            emotion=target.value,
            emotion_name=target.name,
            source_emotion=source.value,
            source_emotion_name=source.name,
            target_emotion=target.value,
            target_emotion_name=target.name,
            transition=transition_text,
        )

    def _build_negative_prompt(self, job: AvatarJob) -> str:
        override = getattr(job, "extra", {}).get("negative_prompt")
        if override:
            return override
        return self.config.negative_prompt

    async def _upload_file(self, session: aiohttp.ClientSession, file_path: Path) -> str:
        url = self.config.api_base_url.rstrip("/") + self.config.upload_endpoint
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        form = aiohttp.FormData()
        with file_path.open("rb") as handle:
            form.add_field("file", handle, filename=file_path.name, content_type=content_type)
            async with session.post(url, data=form) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"upload failed: {resp.status} {text}")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {}
        name = self._extract_upload_name(payload)
        return name or file_path.name

    @staticmethod
    def _extract_upload_name(payload: dict[str, Any]) -> str | None:
        if not payload:
            return None
        candidates = [
            payload.get("name"),
            payload.get("filename"),
            payload.get("file"),
            payload.get("file_name"),
        ]
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        candidates.extend(
            [
                data.get("name"),
                data.get("filename"),
                data.get("file"),
                data.get("file_name"),
            ]
        )
        for item in candidates:
            if item:
                return str(item)
        return None

    async def _submit_generate(
        self,
        session: aiohttp.ClientSession,
        *,
        api_base: str,
        image_ref: str,
        video_ref: str,
        positive_prompt: str,
        negative_prompt: str,
    ) -> str:
        url = api_base + self.config.generate_endpoint
        payload = {
            "workflow_id": self.config.workflow_id,
            "input_values": {
                self.config.input_image_key: image_ref,
                self.config.input_video_key: video_ref,
                self.config.positive_prompt_key: positive_prompt,
                self.config.negative_prompt_key: negative_prompt,
            },
        }
        async with session.post(url, json=payload) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(f"generate failed: {resp.status} {data}")
        prompt_id = data.get("prompt_id") or (data.get("data") or {}).get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"prompt_id missing in response: {data}")
        return str(prompt_id)

    async def _poll_result(self, session: aiohttp.ClientSession, api_base: str, prompt_id: str) -> dict[str, Any]:
        url = api_base + self.config.result_endpoint + "?" + urlencode({"prompt_id": prompt_id})
        logger.info("polling avatar job result: prompt_id=%s url=%s", prompt_id, url)
        deadline = time.time() + self.config.poll_timeout_sec
        last_payload: dict[str, Any] | None = None
        while time.time() < deadline:
            async with session.get(url) as resp:
                payload = await resp.json(content_type=None)
                last_payload = payload
            status = self._extract_status(payload)
            if status in {"succeeded", "success", "completed", "finished"}:
                return payload
            if status in {"failed", "error"}:
                raise RuntimeError(f"workflow failed: {payload}")
            if self._extract_video_url(api_base, payload):
                return payload
            await asyncio.sleep(self.config.poll_interval_sec)
        raise TimeoutError(f"workflow result timeout: {prompt_id} last_payload={last_payload}")

    @staticmethod
    def _extract_status(payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        for key in ("status", "state"):
            value = payload.get(key)
            if isinstance(value, str):
                return value.lower()
            if isinstance(value, dict):
                nested_value = value.get("status_str") or value.get("status") or value.get("state")
                if isinstance(nested_value, str):
                    return nested_value.lower()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        value = data.get("status_str") or data.get("status") or data.get("state")
        if isinstance(value, str):
            return value.lower()
        if len(payload) == 1:
            only_value = next(iter(payload.values()))
            if isinstance(only_value, dict):
                return AvatarJobManager._extract_status(only_value)
        for value in payload.values():
            if isinstance(value, dict):
                nested = AvatarJobManager._extract_status(value)
                if nested:
                    return nested
        return ""

    def _extract_video_url(self, api_base: str, payload: dict[str, Any]) -> str | None:
        try:
            keys = list(payload.keys())
        except AttributeError:
            keys = []
        logger.info("[avatar-job] extract_video_url payload_keys=%s", keys)

        result_url = self._extract_output_result_url(payload)
        if result_url:
            normalized = self._normalize_result_url(api_base, result_url)
            logger.info("[avatar-job] output result resolved_url=%s", normalized)
            return normalized

        node_entry = self._extract_node_entry(payload, _RESULT_NODE_ID)
        if node_entry is not None:
            logger.info("[avatar-job] node %s entry keys=%s", _RESULT_NODE_ID, list(node_entry.keys()))
            resolved = self._resolve_node_entry(api_base, node_entry)
            if resolved:
                normalized = self._normalize_result_url(api_base, resolved)
                logger.info("[avatar-job] node %s resolved_url=%s", _RESULT_NODE_ID, normalized)
                return normalized
            logger.info("[avatar-job] node %s resolved_url missing", _RESULT_NODE_ID)
            return None

        found = self._find_video_url(payload)
        if not found:
            logger.info("[avatar-job] fallback url not found")
            return None
        normalized = self._normalize_result_url(api_base, found)
        logger.info("[avatar-job] fallback resolved_url=%s", normalized)
        return normalized

    @staticmethod
    def _normalize_result_url(api_base: str, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("/"):
            return api_base + url
        return api_base + "/" + url

    def _extract_node_entry(self, payload: dict[str, Any], node_id: str) -> dict[str, Any] | None:
        outputs = self._extract_outputs(payload)
        if not outputs:
            logger.info("[avatar-job] outputs missing for node %s", node_id)
            return None
        logger.info("[avatar-job] outputs keys=%s", list(outputs.keys()))
        node = outputs.get(node_id)
        if not isinstance(node, dict):
            logger.info("[avatar-job] node %s missing in outputs", node_id)
            return None
        for key in ("gifs", "videos", "images"):
            items = node.get(key)
            if isinstance(items, list) and items:
                entry = items[0]
                if isinstance(entry, dict):
                    logger.info("[avatar-job] node %s using %s entry", node_id, key)
                    return entry
        return None

    def _extract_output_result_url(self, payload: dict[str, Any]) -> str | None:
        results = self._extract_results(payload)
        if not results:
            logger.info("[avatar-job] results missing")
            return None

        logger.info("[avatar-job] results count=%d", len(results))
        for item in results:
            if not isinstance(item, dict):
                continue
            raw = item.get("raw")
            if not isinstance(raw, dict):
                continue
            if _safe_text(raw.get("type")).lower() != "output":
                continue
            url = item.get("url")
            if isinstance(url, str) and url:
                logger.info("[avatar-job] using output result url=%s", url)
                return url
        logger.info("[avatar-job] output result url not found")
        return None

    @staticmethod
    def _extract_outputs(payload: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        direct = payload.get("outputs")
        if isinstance(direct, dict):
            return direct
        for key in ("data", "result"):
            nested = payload.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("outputs"), dict):
                return nested.get("outputs")
        for value in payload.values():
            if not isinstance(value, dict):
                continue
            direct = value.get("outputs")
            if isinstance(direct, dict):
                return direct
            for key in ("data", "result"):
                nested = value.get(key)
                if isinstance(nested, dict) and isinstance(nested.get("outputs"), dict):
                    return nested.get("outputs")
        return None

    @staticmethod
    def _extract_results(payload: dict[str, Any]) -> list[Any] | None:
        if not isinstance(payload, dict):
            return None
        direct = payload.get("results")
        if isinstance(direct, list):
            return direct
        for key in ("data", "result"):
            nested = payload.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("results"), list):
                return nested.get("results")
        for value in payload.values():
            if not isinstance(value, dict):
                continue
            direct = value.get("results")
            if isinstance(direct, list):
                return direct
            for key in ("data", "result"):
                nested = value.get(key)
                if isinstance(nested, dict) and isinstance(nested.get("results"), list):
                    return nested.get("results")
        return None

    def _resolve_node_entry(self, api_base: str, entry: dict[str, Any]) -> str | None:
        fullpath = entry.get("fullname") or entry.get("fullpath") or entry.get("full_path")
        if isinstance(fullpath, str):
            logger.info("[avatar-job] node entry fullpath=%s", fullpath)
            if fullpath.startswith("http://") or fullpath.startswith("https://"):
                return fullpath

            filename = entry.get("filename") or entry.get("file_name")
            if isinstance(filename, str):
                logger.info("[avatar-job] node entry filename=%s subfolder=%s", filename, entry.get("subfolder"))
                return self._build_view_url(entry, filename)

            parsed = self._parse_fullpath(fullpath)
            if parsed is not None:
                filename, subfolder = parsed
                logger.info("[avatar-job] node entry parsed filename=%s subfolder=%s", filename, subfolder)
                return self._build_view_url(
                    {"filename": filename, "subfolder": subfolder, "type": "output"},
                    filename,
                )

        filename = entry.get("filename") or entry.get("file_name")
        if isinstance(filename, str):
            logger.info("[avatar-job] node entry filename without fullpath=%s", filename)
            return self._build_view_url(entry, filename)
        return None

    @staticmethod
    def _parse_fullpath(fullpath: str) -> tuple[str, str] | None:
        try:
            path = Path(fullpath)
        except (TypeError, ValueError):
            return None
        if not path.name:
            return None
        if "output" in path.parts:
            idx = path.parts.index("output")
            if idx < len(path.parts) - 1:
                subfolder_parts = path.parts[idx + 1 : -1]
                subfolder = "/".join(subfolder_parts)
                return path.name, subfolder
        if path.parent.name:
            return path.name, path.parent.name
        return None

    def _find_video_url(self, payload: Any) -> str | None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in {"url", "video_url", "file_url"} and isinstance(value, str) and _is_video_name(value):
                    return value
                if key in {"filename", "file_name"} and isinstance(value, str) and _is_video_name(value):
                    return self._build_view_url(payload, value)
                nested = self._find_video_url(value)
                if nested:
                    return nested
        elif isinstance(payload, list):
            for item in payload:
                nested = self._find_video_url(item)
                if nested:
                    return nested
        elif isinstance(payload, str) and _is_video_name(payload):
            if payload.startswith("/") and "/ComfyUI/" in payload:
                return None
            return payload
        return None

    def _build_view_url(self, payload: dict[str, Any], filename: str) -> str:
        params = {"filename": filename}
        subfolder = payload.get("subfolder")
        if subfolder:
            params["subfolder"] = subfolder
        file_type = payload.get("type")
        if file_type:
            params["type"] = file_type
        query = urlencode(params)
        return self.config.comfy_view_endpoint + "?" + query

    @staticmethod
    async def _download_file(session: aiohttp.ClientSession, url: str, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with session.get(url) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"download failed: {resp.status} {url}")
            with output_path.open("wb") as handle:
                async for chunk in resp.content.iter_chunked(1024 * 512):
                    handle.write(chunk)

    @staticmethod
    def _safe_filename(name: str) -> str:
        text = _safe_text(name)
        if not text:
            return "unnamed"
        return "".join(ch for ch in text if ch.isalnum() or ch in {"-", "_", "."})
