from __future__ import annotations

import asyncio
import json
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
    def _session_or_raise(sessionid: int):
        session = context.nerfreals.get(sessionid)
        if session is None:
            raise ValueError(f"invalid sessionid: {sessionid}")
        return session

    def _ok_response(payload: dict | None = None) -> web.Response:
        body = {"code": 0, "msg": "ok"}
        if payload:
            body.update(payload)
        return web.Response(content_type="application/json", text=json.dumps(body))

    def _error_response(exc: Exception) -> web.Response:
        logger.exception("exception:")
        return web.Response(content_type="application/json", text=json.dumps({"code": -1, "msg": str(exc)}))

    async def offer(request):
        params = await request.json()
        offer_obj = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        sessionid = generate_session_id(context, 6)
        nerfreal = await asyncio.get_running_loop().run_in_executor(None, build_nerfreal, context, sessionid)
        context.nerfreals[sessionid] = nerfreal
        logger.info("sessionid=%d, session num=%d", sessionid, len(context.nerfreals))

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

    appasync.router.add_post("/offer", offer)
    appasync.router.add_post("/human", human)
    appasync.router.add_post("/humanaudio", humanaudio)
    appasync.router.add_post("/set_audiotype", set_audiotype)
    appasync.router.add_post("/record", record)
    appasync.router.add_post("/interrupt_talk", interrupt_talk)
    appasync.router.add_post("/is_speaking", is_speaking)
    appasync.router.add_static("/", path="web")


def build_on_shutdown_handler(context: RuntimeContext):
    async def on_shutdown(_app):
        await shutdown_peer_connections(context)

    return on_shutdown
