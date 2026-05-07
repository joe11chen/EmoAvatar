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

import argparse
import asyncio
import os
from pathlib import Path

import cv2
import torch.multiprocessing as mp

import aiohttp_cors
from aiohttp import web

from core.config import AppConfig, load_app_config
from core.plugin_system import PluginType, create, validate_startup_plugins
from logger import logger
from server.http_routes import build_on_shutdown_handler, register_http_routes
from server.rtc_runtime import run_push_session
from server.runtime_context import RuntimeContext


DEFAULT_CONFIG_PATH = Path("config/app.yaml")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=str,
        default=str(DEFAULT_CONFIG_PATH),
        help="YAML config path",
    )
    return parser.parse_args()


def build_runtime_context(config: AppConfig) -> RuntimeContext:
    logger.info(config)
    validate_startup_plugins(config)
    renderer_cls = create(PluginType.RENDERER, config.plugins.renderer, instantiate=False)
    prepared = renderer_cls.prepare_shared(config)
    return RuntimeContext(config=config, renderer_cls=renderer_cls, renderer_prepared=prepared)


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
        site = web.TCPSite(runner, "0.0.0.0", context.config.server.listenport)
        loop.run_until_complete(site.start())
        if context.config.transport.mode == "rtcpush":
            for k in range(context.config.server.max_session):
                push_url = context.config.transport.push_url
                if k != 0:
                    push_url = context.config.transport.push_url + str(k)
                loop.run_until_complete(run_push_session(context, push_url, k))
        loop.run_forever()
    except (KeyboardInterrupt, EOFError):
        logger.info("Event loop is stopping...")
    finally:
        os._exit(0)


if __name__ == "__main__":
    mp.set_start_method("spawn")
    args = parse_args()
    config = load_app_config(args.config)
    context = build_runtime_context(config)
    if config.transport.mode == "rtcpush":
        pagename = "rtcpushapi.html"
    elif config.transport.mode == "webrtc":
        pagename = "webrtcapi.html"
    else:
        pagename = "dashboard.html"
    logger.info("start http server; http://<serverip>:%s/%s", config.server.listenport, pagename)
    if config.transport.mode == "webrtc":
        logger.info("如果使用webrtc，推荐访问webrtc集成前端: http://<serverip>:%s/dashboard.html", config.server.listenport)
    run_server(context, web.AppRunner(create_web_app(context)))
