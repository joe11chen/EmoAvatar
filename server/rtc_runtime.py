from __future__ import annotations

import asyncio
import copy
import random
from typing import TYPE_CHECKING, Any

import aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription

from core.runtime.renderer.base import BaseReal
from logger import logger
from webrtc import HumanPlayer

if TYPE_CHECKING:
    from server.runtime_context import RuntimeContext


def randN(length: int) -> int:
    min_value = pow(10, length - 1)
    max_value = pow(10, length)
    return random.randint(min_value, max_value - 1)


def generate_session_id(context: RuntimeContext, length: int = 6) -> int:
    for _ in range(64):
        sessionid = randN(length)
        if sessionid not in context.nerfreals:
            return sessionid
    raise RuntimeError("Unable to allocate unique session id")


def _build_session_config(config: Any, sessionid: int) -> Any:
    session_config = copy.copy(config)
    session_config.sessionid = sessionid
    return session_config


def build_nerfreal(context: RuntimeContext, sessionid: int) -> BaseReal:
    session_config = _build_session_config(context.config, sessionid)
    if context.renderer_cls is None:
        raise RuntimeError("renderer plugin is not initialized")
    return context.renderer_cls.create_session(session_config, context.renderer_prepared)


def build_transport_player(container: BaseReal) -> HumanPlayer:
    return HumanPlayer(container)


async def post(url, data):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data) as response:
                response.raise_for_status()
                return await response.text()
    except aiohttp.ClientError as exc:
        raise RuntimeError(f"failed to post sdp to {url}: {exc}") from exc


async def run_push_session(context: RuntimeContext, push_url, sessionid):
    nerfreal = await asyncio.get_event_loop().run_in_executor(None, build_nerfreal, context, sessionid)
    context.nerfreals[sessionid] = nerfreal

    pc = RTCPeerConnection()
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

    await pc.setLocalDescription(await pc.createOffer())
    answer = await post(push_url, pc.localDescription.sdp)
    if not answer:
        raise RuntimeError(f"empty answer from {push_url}")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))


async def shutdown_peer_connections(context: RuntimeContext):
    coros = [pc.close() for pc in context.pcs]
    await asyncio.gather(*coros)
    context.pcs.clear()
