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

import asyncio
import json
import logging
import threading
import time
from typing import Tuple, Dict, Optional, Set, Union
from av.frame import Frame
from av.packet import Packet
from av import AudioFrame
import fractions
import numpy as np

AUDIO_PTIME = 0.020  # 20ms audio packetization
VIDEO_CLOCK_RATE = 90000
VIDEO_PTIME = 0.040 #1 / 25  # 30fps
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)
SAMPLE_RATE = 16000
AUDIO_TIME_BASE = fractions.Fraction(1, SAMPLE_RATE)

#from aiortc.contrib.media import MediaPlayer, MediaRelay
#from aiortc.rtcrtpsender import RTCRtpSender
from aiortc import (
    MediaStreamTrack,
)

logging.basicConfig()
logger = logging.getLogger(__name__)
from logger import logger as mylogger


class PlayerStreamTrack(MediaStreamTrack):
    """
    A video track that returns an animated flag.
    """

    def __init__(self, player, kind, queue_maxsize=100, realtime=True):
        super().__init__()  # don't forget this!
        self.kind = kind
        self._player = player
        self._queue = asyncio.Queue(maxsize=queue_maxsize)
        self._realtime = realtime
        self.timelist = [] #记录最近包的时间戳
        self.current_frame_count = 0
        self._stats_window_start = time.time()
        self._stats_frames = 0
        self._stats_queue_delay_sum_ms = 0.0
        self._stats_queue_delay_max_ms = 0.0
        self._stats_missing_enqueue_ts = 0
        self._stats_late_frames = 0
        self._stats_seq_gaps = 0
        self._stats_last_seq = None
        if self.kind == 'video':
            self.framecount = 0
            self.lasttime = time.perf_counter()
            self.totaltime = 0

    def _log_stats_if_needed(self):
        now = time.time()
        elapsed = now - self._stats_window_start
        if elapsed < 1.0:
            return

        avg_delay_ms = 0.0
        if self._stats_frames > 0:
            avg_delay_ms = self._stats_queue_delay_sum_ms / self._stats_frames
        fps = self._stats_frames / elapsed if elapsed > 0 else 0.0

        mylogger.info(
            "rtcpush consumer stats[%s]: fps=%.2f qsize=%d avg_queue_delay_ms=%.2f max_queue_delay_ms=%.2f late_frames=%d missing_enqueue_ts=%d seq_gaps=%d",
            self.kind,
            fps,
            self._queue.qsize(),
            avg_delay_ms,
            self._stats_queue_delay_max_ms,
            self._stats_late_frames,
            self._stats_missing_enqueue_ts,
            self._stats_seq_gaps,
        )

        self._stats_window_start = now
        self._stats_frames = 0
        self._stats_queue_delay_sum_ms = 0.0
        self._stats_queue_delay_max_ms = 0.0
        self._stats_missing_enqueue_ts = 0
        self._stats_late_frames = 0
        self._stats_seq_gaps = 0
    
    _start: float
    _timestamp: int

    async def next_timestamp(self) -> Tuple[int, fractions.Fraction]:
        if self.readyState != "live":
            raise Exception

        if self.kind == 'video':
            if hasattr(self, "_timestamp"):
                #self._timestamp = (time.time()-self._start) * VIDEO_CLOCK_RATE
                self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCK_RATE)
                self.current_frame_count += 1
                wait = self._start + self.current_frame_count * VIDEO_PTIME - time.time()
                # wait = self.timelist[0] + len(self.timelist)*VIDEO_PTIME - time.time()               
                if self._realtime and wait>0:
                    await asyncio.sleep(wait)
                # if len(self.timelist)>=100:
                #     self.timelist.pop(0)
                # self.timelist.append(time.time())
            else:
                self._start = time.time()
                self._timestamp = 0
                self.timelist.append(self._start)
                mylogger.info('video start:%f',self._start)
            return self._timestamp, VIDEO_TIME_BASE
        else: #audio
            if hasattr(self, "_timestamp"):
                #self._timestamp = (time.time()-self._start) * SAMPLE_RATE
                self._timestamp += int(AUDIO_PTIME * SAMPLE_RATE)
                self.current_frame_count += 1
                wait = self._start + self.current_frame_count * AUDIO_PTIME - time.time()
                # wait = self.timelist[0] + len(self.timelist)*AUDIO_PTIME - time.time()
                if self._realtime and wait>0:
                    await asyncio.sleep(wait)
                # if len(self.timelist)>=200:
                #     self.timelist.pop(0)
                #     self.timelist.pop(0)
                # self.timelist.append(time.time())
            else:
                self._start = time.time()
                self._timestamp = 0
                self.timelist.append(self._start)
                mylogger.info('audio start:%f',self._start)
            return self._timestamp, AUDIO_TIME_BASE

    async def recv(self) -> Union[Frame, Packet]:
        # frame = self.frames[self.counter % 30]            
        self._player._start(self)
        # if self.kind == 'video':
        #     frame = await self._queue.get()
        # else: #audio
        #     if hasattr(self, "_timestamp"):
        #         wait = self._start + self._timestamp / SAMPLE_RATE + AUDIO_PTIME - time.time()
        #         if wait>0:
        #             await asyncio.sleep(wait)
        #         if self._queue.qsize()<1:
        #             #frame = AudioFrame(format='s16', layout='mono', samples=320)
        #             audio = np.zeros((1, 320), dtype=np.int16)
        #             frame = AudioFrame.from_ndarray(audio, layout='mono', format='s16')
        #             frame.sample_rate=16000
        #         else:
        #             frame = await self._queue.get()
        #     else:
        #         frame = await self._queue.get()
        frame,eventpoint = await self._queue.get()
        now = time.time()
        enqueue_ts = None
        seq = None
        if isinstance(eventpoint, dict):
            enqueue_ts = eventpoint.get("_enqueue_ts")
            seq = eventpoint.get("_seq")

        if enqueue_ts is not None:
            queue_delay_ms = (now - enqueue_ts) * 1000.0
            self._stats_queue_delay_sum_ms += queue_delay_ms
            self._stats_queue_delay_max_ms = max(self._stats_queue_delay_max_ms, queue_delay_ms)
            late_threshold_ms = 120.0 if self.kind == 'video' else 60.0
            if queue_delay_ms > late_threshold_ms:
                self._stats_late_frames += 1
        else:
            self._stats_missing_enqueue_ts += 1

        if isinstance(seq, int):
            if self._stats_last_seq is not None and seq > self._stats_last_seq + 1:
                self._stats_seq_gaps += (seq - self._stats_last_seq - 1)
            self._stats_last_seq = seq

        self._stats_frames += 1
        self._log_stats_if_needed()

        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        # if eventpoint and self._player is not None:
        #     self._player.notify(eventpoint)
        if frame is None:
            self.stop()
            raise Exception
        if self.kind == 'video':
            self.totaltime += (time.perf_counter() - self.lasttime)
            self.framecount += 1
            self.lasttime = time.perf_counter()
            if self.framecount==100:
                # mylogger.info(f"------actual avg final fps:{self.framecount/self.totaltime:.4f}")
                self.framecount = 0
                self.totaltime=0
        return frame
    
    def stop(self):
        super().stop()
        # Drain & delete remaining frames
        while not self._queue.empty():
            item = self._queue.get_nowait()
            del item
        if self._player is not None:
            self._player._stop(self)
            self._player = None

def player_worker_thread(
    quit_event,
    loop,
    container,
    audio_track,
    video_track
):
    container.render(quit_event,loop,audio_track,video_track)

class HumanPlayer:

    def __init__(
        self, nerfreal, format=None, options=None, timeout=None, loop=False, decode=True
    ):
        self.__thread: Optional[threading.Thread] = None
        self.__thread_quit: Optional[threading.Event] = None

        # examine streams
        self.__started: Set[PlayerStreamTrack] = set()
        self.__audio: Optional[PlayerStreamTrack] = None
        self.__video: Optional[PlayerStreamTrack] = None

        audio_queue_maxsize = 200
        video_queue_maxsize = 100
        realtime_consume = True
        if hasattr(nerfreal, "config"):
            audio_queue_maxsize = nerfreal.config.transport.rtc_audio_queue_maxsize
            video_queue_maxsize = nerfreal.config.transport.rtc_video_queue_maxsize
            realtime_consume = nerfreal.config.transport.mode != "httpfile"
        mylogger.info(
            "HumanPlayer consume mode: realtime=%s audio_q=%d video_q=%d",
            realtime_consume,
            audio_queue_maxsize,
            video_queue_maxsize,
        )

        self.__audio = PlayerStreamTrack(
            self,
            kind="audio",
            queue_maxsize=audio_queue_maxsize,
            realtime=realtime_consume,
        )
        self.__video = PlayerStreamTrack(
            self,
            kind="video",
            queue_maxsize=video_queue_maxsize,
            realtime=realtime_consume,
        )

        self.__container = nerfreal

    def notify(self,eventpoint):
        if self.__container is not None:
            self.__container.notify(eventpoint)

    @property
    def audio(self) -> MediaStreamTrack:
        """
        A :class:`aiortc.MediaStreamTrack` instance if the file contains audio.
        """
        return self.__audio

    @property
    def video(self) -> MediaStreamTrack:
        """
        A :class:`aiortc.MediaStreamTrack` instance if the file contains video.
        """
        return self.__video

    def _start(self, track: PlayerStreamTrack) -> None:
        self.__started.add(track)
        if self.__thread is None:
            self.__log_debug("Starting worker thread")
            self.__thread_quit = threading.Event()
            self.__thread = threading.Thread(
                name="media-player",
                target=player_worker_thread,
                daemon=True,
                args=(
                    self.__thread_quit,
                    asyncio.get_event_loop(),
                    self.__container,
                    self.__audio,
                    self.__video                   
                ),
            )
            self.__thread.start()

    def _stop(self, track: PlayerStreamTrack) -> None:
        self.__started.discard(track)

        if not self.__started and self.__thread is not None:
            self.__log_debug("Stopping worker thread")
            self.__thread_quit.set()
            join_timeout = 0.2 if not getattr(track, "_realtime", True) else 5.0
            self.__thread.join(timeout=join_timeout)
            if self.__thread.is_alive():
                mylogger.warning("HumanPlayer worker thread join timeout, continue cleanup")
            else:
                self.__thread = None

        if not self.__started and self.__container is not None:
            #self.__container.close()
            self.__container = None

    def __log_debug(self, msg: str, *args) -> None:
        mylogger.debug(f"HumanPlayer {msg}", *args)
