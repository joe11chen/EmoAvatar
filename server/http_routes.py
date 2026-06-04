from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, RTCConfiguration
from aiortc.rtcrtpsender import RTCRtpSender

from llm import llm_response
from logger import logger
from server.rtc_runtime import build_nerfreal, build_transport_player, generate_session_id, shutdown_peer_connections

if TYPE_CHECKING:
    from server.runtime_context import RuntimeContext


def register_http_routes(appasync: web.Application, context: RuntimeContext) -> None:
    def _navigation_items(mode: str) -> list[dict[str, str]]:
        common = [
            {"label": "综合控制台", "path": "/dashboard.html", "desc": "统一控制面板"},
            {"label": "素材批量生成", "path": "/avatar_jobs.html", "desc": "上传图片批量生成情绪素材"},
        ]
        if mode == "webrtc":
            return common + [
                {"label": "WebRTC 基础页", "path": "/webrtcapi.html", "desc": "视频对话主入口"},
                {"label": "WebRTC 聊天页", "path": "/webrtcchat.html", "desc": "文本聊天示例"},
            ]
        if mode == "rtcpush":
            return common + [
                {"label": "RTCPush 基础页", "path": "/rtcpushapi.html", "desc": "推流模式主入口"},
                {"label": "RTCPush 聊天页", "path": "/rtcpushchat.html", "desc": "文本聊天示例"},
            ]
        if mode == "httpfile":
            return common + [
                {"label": "HTTP 视频任务", "path": "/httpfile.html", "desc": "video_jobs 任务页"},
                {"label": "HTTP 纯音频任务", "path": "/audiofile.html", "desc": "audio_jobs 任务页"},
            ]
        return common

    def _session_or_raise(sessionid: int):
        session = context.nerfreals.get(sessionid)
        if session is None:
            raise ValueError(f"invalid sessionid: {sessionid}")
        return session

    def _video_jobs_or_raise():
        if context.config.transport.mode != "httpfile":
            raise ValueError("video_jobs API is only available when transport.mode=httpfile")
        if context.video_jobs is None:
            raise RuntimeError("video jobs manager is not initialized")
        return context.video_jobs

    def _audio_jobs_or_raise():
        if context.config.transport.mode != "httpfile":
            raise ValueError("audio_jobs API is only available when transport.mode=httpfile")
        if context.audio_jobs is None:
            raise RuntimeError("audio jobs manager is not initialized")
        return context.audio_jobs

    def _avatar_jobs_or_raise():
        if context.avatar_jobs is None:
            raise RuntimeError("avatar jobs manager is not initialized")
        return context.avatar_jobs

    def _renderer_resource_or_raise():
        resource = context.renderer_prepared
        reload_fn = getattr(resource, "reload_user_resource", None)
        if reload_fn is None:
            raise RuntimeError("renderer does not support user resource reload")
        return resource

    def _ok_response(payload: dict | None = None) -> web.Response:
        body = {"code": 0, "msg": "ok"}
        if payload:
            body.update(payload)
        return web.Response(content_type="application/json", text=json.dumps(body))

    def _error_response(exc: Exception) -> web.Response:
        logger.exception("exception:")
        return web.Response(content_type="application/json", text=json.dumps({"code": -1, "msg": str(exc)}))

    async def nav_home(_request):
        return web.FileResponse(path=Path("web/nav.html"))

    async def runtime_info(_request):
        try:
            mode = str(context.config.transport.mode)
            return _ok_response(
                {
                    "data": {
                        "transport_mode": mode,
                        "entries": _navigation_items(mode),
                    }
                }
            )
        except Exception as exc:
            return _error_response(exc)

    async def offer(request):
        params = await request.json()
        offer_obj = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
        user_id = str(params.get("user_id") or "").strip() or None

        sessionid = generate_session_id(context, 6)
        nerfreal = await asyncio.get_running_loop().run_in_executor(None, build_nerfreal, context, sessionid, user_id)
        context.nerfreals[sessionid] = nerfreal
        logger.info("sessionid=%d user_id=%s, session num=%d", sessionid, getattr(nerfreal, "user_id", ""), len(context.nerfreals))

        ice_server = RTCIceServer(urls="stun:stun.miwifi.com:3478")
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[ice_server]))
        context.pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info("Connection state is %s", pc.connectionState)
            if pc.connectionState == "failed":
                await pc.close()
                context.pcs.discard(pc)
                context.nerfreals.pop(sessionid, None)
            if pc.connectionState == "closed":
                context.pcs.discard(pc)
                context.nerfreals.pop(sessionid, None)

        player = build_transport_player(context.nerfreals[sessionid])
        pc.addTrack(player.audio)
        pc.addTrack(player.video)
        capabilities = RTCRtpSender.getCapabilities("video")
        preferences = list(filter(lambda x: x.name == "H264", capabilities.codecs))
        preferences += list(filter(lambda x: x.name == "VP8", capabilities.codecs))
        preferences += list(filter(lambda x: x.name == "rtx", capabilities.codecs))
        transceiver = pc.getTransceivers()[1]
        transceiver.setCodecPreferences(preferences)

        await pc.setRemoteDescription(offer_obj)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "sessionid": sessionid}
            ),
        )

    async def human(request):
        try:
            params = await request.json()

            sessionid = int(params.get("sessionid", 0))
            session = _session_or_raise(sessionid)
            if params.get("interrupt"):
                session.flush_talk()

            if params["type"] == "echo":
                session.put_msg_txt(params["text"])
            elif params["type"] == "chat":
                asyncio.get_running_loop().run_in_executor(None, llm_response, params["text"], session)
            return _ok_response()
        except Exception as exc:
            return _error_response(exc)

    async def interrupt_talk(request):
        try:
            params = await request.json()
            sessionid = int(params.get("sessionid", 0))
            _session_or_raise(sessionid).flush_talk()
            return _ok_response()
        except Exception as exc:
            return _error_response(exc)

    async def humanaudio(request):
        try:
            form = await request.post()
            sessionid = int(form.get("sessionid", 0))
            fileobj = form["file"]
            filebytes = fileobj.file.read()
            _session_or_raise(sessionid).put_audio_file(filebytes)
            return _ok_response()
        except Exception as exc:
            return _error_response(exc)

    async def set_audiotype(request):
        try:
            params = await request.json()
            sessionid = int(params.get("sessionid", 0))
            _session_or_raise(sessionid).set_custom_state(params["audiotype"], params["reinit"])
            return _ok_response()
        except Exception as exc:
            return _error_response(exc)

    async def record(request):
        try:
            params = await request.json()
            sessionid = int(params.get("sessionid", 0))
            session = _session_or_raise(sessionid)
            if params["type"] == "start_record":
                session.start_recording()
            elif params["type"] == "end_record":
                session.stop_recording()
            return _ok_response()
        except Exception as exc:
            return _error_response(exc)

    async def is_speaking(request):
        try:
            params = await request.json()
            sessionid = int(params.get("sessionid", 0))
            speaking = _session_or_raise(sessionid).is_speaking()
            return _ok_response({"data": speaking})
        except Exception as exc:
            return _error_response(exc)

    async def create_video_job(request):
        try:
            request_perf_ts = time.perf_counter()
            params = await request.json()
            text = params.get("text", "")
            emotion = params.get("emotion", "")
            user_id = params.get("user_id", "")
            video_jobs = _video_jobs_or_raise()
            job = await video_jobs.submit(text, emotion, user_id_raw=user_id, request_perf_ts=request_perf_ts)
            return _ok_response({"data": {"job_id": job["job_id"], "status": job["status"]}})
        except Exception as exc:
            return _error_response(exc)

    async def get_video_job(request):
        try:
            job_id = request.match_info.get("job_id", "")
            job = _video_jobs_or_raise().get(job_id)
            if job is None:
                raise ValueError(f"invalid job_id: {job_id}")
            return _ok_response({"data": job})
        except Exception as exc:
            return _error_response(exc)

    async def get_video_job_file(request):
        try:
            job_id = request.match_info.get("job_id", "")
            video_jobs = _video_jobs_or_raise()
            job = video_jobs.get(job_id)
            if job is None:
                raise ValueError(f"invalid job_id: {job_id}")
            if job["status"] != "succeeded":
                raise ValueError(f"job is not ready: status={job['status']}")
            file_path = Path(str(job["file_path"]))
            if not file_path.exists():
                raise FileNotFoundError(f"job file not found: {file_path}")
            request_to_file_sec = video_jobs.mark_file_served(job_id)
            if request_to_file_sec is not None:
                logger.info("[httpfile-prof] request_to_file_sec=%.3f job_id=%s", request_to_file_sec, job_id)
            if request.query.get("download") == "1":
                return web.FileResponse(
                    path=file_path,
                    headers={"Content-Disposition": f'attachment; filename=\"{job_id}.mp4\"'},
                )
            return web.FileResponse(path=file_path)
        except Exception as exc:
            return _error_response(exc)

    async def create_audio_job(request):
        try:
            request_perf_ts = time.perf_counter()
            params = await request.json()
            text = params.get("text", "")
            emotion = params.get("emotion", "")
            audio_jobs = _audio_jobs_or_raise()
            job = await audio_jobs.submit(text, emotion, request_perf_ts=request_perf_ts)
            return _ok_response({"data": {"job_id": job["job_id"], "status": job["status"]}})
        except Exception as exc:
            return _error_response(exc)

    async def get_audio_job(request):
        try:
            job_id = request.match_info.get("job_id", "")
            job = _audio_jobs_or_raise().get(job_id)
            if job is None:
                raise ValueError(f"invalid job_id: {job_id}")
            return _ok_response({"data": job})
        except Exception as exc:
            return _error_response(exc)

    async def get_audio_job_file(request):
        try:
            job_id = request.match_info.get("job_id", "")
            audio_jobs = _audio_jobs_or_raise()
            job = audio_jobs.get(job_id)
            if job is None:
                raise ValueError(f"invalid job_id: {job_id}")
            if job["status"] != "succeeded":
                raise ValueError(f"job is not ready: status={job['status']}")
            file_path = Path(str(job["file_path"]))
            if not file_path.exists():
                raise FileNotFoundError(f"job file not found: {file_path}")
            request_to_file_sec = audio_jobs.mark_file_served(job_id)
            if request_to_file_sec is not None:
                logger.info("[httpfile-prof] audio_request_to_file_sec=%.3f job_id=%s", request_to_file_sec, job_id)
            if request.query.get("download") == "1":
                return web.FileResponse(
                    path=file_path,
                    headers={"Content-Disposition": f'attachment; filename=\"{job_id}.wav\"'},
                )
            return web.FileResponse(path=file_path)
        except Exception as exc:
            return _error_response(exc)

    async def avatar_job_options(_request):
        try:
            options = _avatar_jobs_or_raise().get_options()
            return _ok_response({"data": options})
        except Exception as exc:
            return _error_response(exc)

    async def create_avatar_job(request):
        try:
            form = await request.post()
            image = form.get("image")
            if image is None:
                raise ValueError("image is required")

            avatar_id = form.get("avatar_id")
            emotions = form.get("emotions")
            positive_prompt = form.get("positive_prompt")
            negative_prompt = form.get("negative_prompt")

            image_bytes = image.file.read()
            job = await _avatar_jobs_or_raise().submit(
                image_bytes=image_bytes,
                image_filename=image.filename,
                avatar_id=avatar_id,
                emotions_raw=emotions,
                positive_prompt=positive_prompt,
                negative_prompt=negative_prompt,
            )
            return _ok_response({"data": {"job_id": job["job_id"], "status": job["status"]}})
        except Exception as exc:
            return _error_response(exc)

    async def get_avatar_job(request):
        try:
            job_id = request.match_info.get("job_id", "")
            job = _avatar_jobs_or_raise().get(job_id)
            if job is None:
                raise ValueError(f"invalid job_id: {job_id}")
            return _ok_response({"data": job})
        except Exception as exc:
            return _error_response(exc)

    async def reload_avatar_resource(request):
        try:
            user_id = request.query.get("user_id", "")
            if request.can_read_body:
                try:
                    payload = await request.json()
                    if isinstance(payload, dict):
                        user_id = str(payload.get("user_id") or user_id)
                except json.JSONDecodeError:
                    form = await request.post()
                    user_id = str(form.get("user_id") or user_id)
            user_id = user_id.strip() or context.config.renderer.user_id
            resource = _renderer_resource_or_raise()
            loaded = resource.reload_user_resource(user_id)
            refreshed_sessions = []
            for sessionid, session in context.nerfreals.items():
                reload_fn = getattr(session, "reload_user_resource", None)
                if reload_fn is None:
                    continue
                session_user_id = getattr(session, "user_id", None)
                if session_user_id == user_id:
                    reload_fn(user_id)
                    refreshed_sessions.append(sessionid)
            return _ok_response(
                {
                    "data": {
                        "user_id": loaded.user_id,
                        "avatars": [emotion.value for emotion in loaded.avatars.keys()],
                        "refreshed_sessions": refreshed_sessions,
                    }
                }
            )
        except Exception as exc:
            return _error_response(exc)

    appasync.router.add_get("/", nav_home)
    appasync.router.add_get("/runtime_info", runtime_info)
    appasync.router.add_post("/offer", offer)
    appasync.router.add_post("/human", human)
    appasync.router.add_post("/humanaudio", humanaudio)
    appasync.router.add_post("/set_audiotype", set_audiotype)
    appasync.router.add_post("/record", record)
    appasync.router.add_post("/interrupt_talk", interrupt_talk)
    appasync.router.add_post("/is_speaking", is_speaking)
    appasync.router.add_post("/video_jobs", create_video_job)
    appasync.router.add_get("/video_jobs/{job_id}", get_video_job)
    appasync.router.add_get("/video_jobs/{job_id}/file", get_video_job_file)
    appasync.router.add_post("/audio_jobs", create_audio_job)
    appasync.router.add_get("/audio_jobs/{job_id}", get_audio_job)
    appasync.router.add_get("/audio_jobs/{job_id}/file", get_audio_job_file)
    appasync.router.add_get("/avatar_jobs/options", avatar_job_options)
    appasync.router.add_post("/avatar_jobs", create_avatar_job)
    appasync.router.add_get("/avatar_jobs/{job_id}", get_avatar_job)
    appasync.router.add_post("/avatar_resources/reload", reload_avatar_resource)
    appasync.router.add_static("/assets", path="assets")
    appasync.router.add_static("/", path="web")


def build_on_shutdown_handler(context: RuntimeContext):
    async def on_shutdown(_app):
        if context.video_jobs is not None:
            await context.video_jobs.shutdown()
        if context.audio_jobs is not None:
            await context.audio_jobs.shutdown()
        if context.avatar_jobs is not None:
            await context.avatar_jobs.shutdown()
        await shutdown_peer_connections(context)

    return on_shutdown
