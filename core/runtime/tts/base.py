from __future__ import annotations

import queue
from enum import Enum
from io import BytesIO
from queue import Queue
from threading import Thread
from typing import TYPE_CHECKING

from logger import logger

if TYPE_CHECKING:
    from core.runtime.renderer.base import BaseReal


class State(Enum):
    RUNNING = 0
    PAUSE = 1


class BaseTTS:
    def __init__(self, opt, parent: BaseReal):
        self.opt = opt
        self.parent = parent

        self.fps = opt.fps
        self.sample_rate = 16000
        self.chunk = self.sample_rate // self.fps
        self.input_stream = BytesIO()

        self.msgqueue: Queue[tuple[str, dict]] = Queue()
        self.state = State.RUNNING
        self.llm_state = "end"  # start, streaming, end
        self._process_thread: Thread | None = None

    def flush_talk(self):
        self.state = State.PAUSE
        while True:
            try:
                self.msgqueue.get_nowait()
            except queue.Empty:
                break

    def put_msg_txt(self, msg: str, datainfo: dict | None = None):
        if not msg:
            return
        self.msgqueue.put((msg, datainfo or {}))

    def render(self, quit_event):
        if self._process_thread and self._process_thread.is_alive():
            return
        self._process_thread = Thread(target=self.process_tts, args=(quit_event,))
        self._process_thread.start()

    def process_tts(self, quit_event):
        while not quit_event.is_set():
            try:
                msg: tuple[str, dict] = self.msgqueue.get(block=True, timeout=1)
                self.llm_state = msg[1].get("llm_status")
                self.state = State.RUNNING
            except queue.Empty:
                continue
            self.txt_to_audio(msg)
        logger.info("tts thread stop")

    def txt_to_audio(self, msg: tuple[str, dict]):
        raise NotImplementedError

