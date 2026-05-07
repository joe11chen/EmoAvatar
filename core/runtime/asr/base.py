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
from __future__ import annotations
import numpy as np

import queue
from queue import Queue
import torch.multiprocessing as mp
from typing import TYPE_CHECKING

from data import EMOTION
from core.contracts import make_audio_out_frame, make_idle_eventpoint, normalize_eventpoint
if TYPE_CHECKING:
    from core.runtime.renderer.base import BaseReal

class BaseASR:

    def __init__(self, config, parent:BaseReal = None):
        self.config = config
        self.parent = parent

        self.fps = config.runtime.fps # 20 ms per frame
        self.sample_rate = 16000
        self.chunk = self.sample_rate // self.fps # 320 samples per chunk (20ms * 16000 / 1000)
        self.queue = Queue()
        self.output_queue = mp.Queue()

        self.batch_size = config.renderer.batch_size

        self.frames = []
        self.stride_left_size = config.runtime.sync_window.l
        self.stride_right_size = config.runtime.sync_window.r
        #self.context_size = 10
        self.feat_queue = mp.Queue(2)

        self.llm_status = "end"  # start, streaming, end
        self.prev_emo = EMOTION.DEFAULT
        #self.warm_up()

    def flush_talk(self):
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break

    def put_audio_frame(self,audio_chunk,datainfo:dict | None = None): #16khz 20ms pcm
        eventpoint = normalize_eventpoint(datainfo or {})
        self.queue.put((audio_chunk, eventpoint))

    #return frame:audio pcm; type: 0-normal speak, 1-silence; eventpoint:custom event sync with audio
    def get_audio_frame(self):        
        try:
            frame,eventpoint = self.queue.get(block=True,timeout=0.01)
            # eventpoint.update({"asr_status":"streaming"})
            if "llm_status" in eventpoint:
                self.llm_status = eventpoint.get("llm_status")
            if "emo" in eventpoint:
                self.prev_emo = eventpoint.get("emo")
            audio_type = 0
        except queue.Empty:
            if self.parent and self.parent.curr_state>1: #播放自定义音频
                frame = self.parent.get_audio_stream(self.parent.curr_state)
                audio_type = self.parent.curr_state
            else:
                frame = (np.zeros(self.chunk, dtype=np.float32), np.zeros(self.chunk, dtype=np.float32))
                audio_type = 1
            
            idle_emo = self.prev_emo if self.llm_status != "end" else EMOTION.DEFAULT
            eventpoint = make_idle_eventpoint(self.llm_status, idle_emo)
            # eventpoint.update({"asr_status":"idle"})

        return frame,audio_type,eventpoint 

    #return frame:audio pcm; tyspe: 0-normal speak, 1-silence; eventpoint:custom event sync with audio
    def get_audio_out(self): 
        return self.output_queue.get()
    
    def warm_up(self):
        cnt = 0
        while cnt < self.stride_left_size + self.stride_right_size:
            audio_frame,audio_type,eventpoint=self.get_audio_frame()
            self.frames.append(audio_frame[0])
            self.frames.append(audio_frame[1])
            out0 = make_audio_out_frame(audio_frame[0], audio_type, eventpoint)
            out1 = make_audio_out_frame(audio_frame[1], audio_type, eventpoint)
            self.output_queue.put(out0)
            self.output_queue.put(out1)
            cnt += 2
        
        for _ in range(self.stride_left_size):
            self.output_queue.get()

    def run_step(self):
        pass

    def get_next_feat(self,block,timeout):        
        return self.feat_queue.get(block,timeout)
