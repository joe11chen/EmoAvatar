###############################################################################
#  Copyright (C) 2024 LiveTalking@lipku https://github.com/lipku/LiveTalking
#  email: lipku@foxmail.com
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

import os
import cv2
import json
import argparse
import asyncio

import torch.multiprocessing as mp

import aiohttp_cors
from aiohttp import web

from core.plugin_system import PluginType, create, validate_startup_plugins
from logger import logger
from server.http_routes import build_on_shutdown_handler, register_http_routes
from server.rtc_runtime import run_push_session
from server.runtime_context import RuntimeContext


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--fps", type=int, default=50, help="audio fps,must be 50")
    parser.add_argument("-l", type=int, default=10)
    parser.add_argument("-m", type=int, default=8)
    parser.add_argument("-r", type=int, default=10)

    parser.add_argument("--W", type=int, default=450, help="GUI width")
    parser.add_argument("--H", type=int, default=450, help="GUI height")

    parser.add_argument("--avatar_id", type=str, default="avator_1", help="define which avatar in data/avatars")
    parser.add_argument("--batch_size", type=int, default=16, help="infer batch")
    parser.add_argument("--customvideo_config", type=str, default="", help="custom action json")

    parser.add_argument("--tts", type=str, default="indextts2", help="tts plugin name")
    parser.add_argument("--asr", type=str, default="museasr", help="asr plugin name")
    parser.add_argument("--renderer", type=str, default="musetalk", help="renderer plugin name")
    parser.add_argument("--REF_FILE", type=str, default="data/audios/voice_11.wav", help="IndexTTS2 reference audio path")
    parser.add_argument("--REF_TEXT", type=str, default=None)
    parser.add_argument("--TTS_SERVER", type=str, default="http://127.0.0.1:7860/", help="IndexTTS2 gradio server url")

    parser.add_argument("--transport", type=str, default="rtcpush", choices=["webrtc", "rtcpush"])
    parser.add_argument(
        "--push_url",
        type=str,
        default="http://localhost:1985/rtc/v1/whip/?app=live&stream=livestream",
        help="WHIP endpoint for rtcpush mode",
    )
    parser.add_argument(
        "--rtc_audio_queue_maxsize",
        type=int,
        default=600,
        help="rtcpush audio queue size; larger means less drop risk but more latency",
    )
    parser.add_argument(
        "--rtc_video_queue_maxsize",
        type=int,
        default=300,
        help="rtcpush video queue size; larger means less drop risk but more latency",
    )

    parser.add_argument("--max_session", type=int, default=1)
    parser.add_argument("--listenport", type=int, default=8010, help="web listen port")
    parser.add_argument("--multi_avatar", type=bool, default=False, help="use multi avatar for musetalk")
    return parser.parse_args()


def build_runtime_context(opt) -> RuntimeContext:
    opt.customopt = []
    if opt.customvideo_config != "":
        with open(opt.customvideo_config, "r") as file:
            opt.customopt = json.load(file)

    logger.info(opt)
    validate_startup_plugins(opt)
    renderer_cls = create(PluginType.RENDERER, opt.renderer, instantiate=False)
    prepared = renderer_cls.prepare_shared(opt)
    return RuntimeContext(opt=opt, renderer_cls=renderer_cls, renderer_prepared=prepared)


def create_web_app(context: RuntimeContext):
    appasync = web.Application(client_max_size=1024**2 * 100)
    appasync.on_shutdown.append(build_on_shutdown_handler(context))
    register_http_routes(appasync, context)

    cors = aiohttp_cors.setup(
        appasync,
        defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
            )
        },
    )
    for route in list(appasync.router.routes()):
        cors.add(route)
    return appasync


def run_server(context: RuntimeContext, runner: web.AppRunner):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "0.0.0.0", context.opt.listenport)
        loop.run_until_complete(site.start())
        if context.opt.transport == "rtcpush":
            for k in range(context.opt.max_session):
                push_url = context.opt.push_url
                if k != 0:
                    push_url = context.opt.push_url + str(k)
                loop.run_until_complete(run_push_session(context, push_url, k))
        loop.run_forever()
    except (KeyboardInterrupt, EOFError):
        logger.info("Event loop is stopping...")
    finally:
        os._exit(0)


if __name__ == "__main__":
    mp.set_start_method("spawn")
    opt = parse_args()
    context = build_runtime_context(opt)
    pagename = "rtcpushapi.html" if opt.transport == "rtcpush" else "webrtcapi.html"
    logger.info("start http server; http://<serverip>:%s/%s", opt.listenport, pagename)
    logger.info("如果使用webrtc，推荐访问webrtc集成前端: http://<serverip>:%s/dashboard.html", opt.listenport)
    run_server(context, web.AppRunner(create_web_app(context)))
