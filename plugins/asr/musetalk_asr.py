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

import numpy as np
import time

from core.contracts import make_audio_out_frame
from core.plugin_system import PluginType, register
from core.runtime.asr.base import BaseASR
from data import EMOTION, DEFAULT_EMOTION
from musetalk.whisper.audio2feature import Audio2Feature

@register(PluginType.ASR, "museasr")
class MuseASR(BaseASR):
    def __init__(self, config, parent, audio_processor:Audio2Feature):
        super().__init__(config, parent)
        self.audio_processor = audio_processor
        self._httpfile_finished = False

    def run_step(self):
        profiler = self.parent._get_httpfile_profiler() if self.mode == "httpfile" and self.parent is not None else None
        run_step_t0 = time.perf_counter()
        if self.mode == "httpfile" and self._httpfile_finished:
            time.sleep(0.01)
            if profiler is not None:
                profiler.observe("asr.idle_after_eos", time.perf_counter() - run_step_t0)
            return

        eos_received = False
        step_count = 0
        collect_audio_t0 = time.perf_counter()
        for _ in range(self.batch_size):
            audio_frame,audio_type,eventpoint = self.get_audio_frame()
            if audio_frame is None and audio_type is None and eventpoint is None:
                eos_received = True
                break
            status = (eventpoint or {}).get("status")
            effective_audio_type = 1 if status == "transition" else audio_type
            self.frames.append(audio_frame[0])
            self.frames.append(audio_frame[1])
            out0 = make_audio_out_frame(audio_frame[0], effective_audio_type, eventpoint)
            out1 = make_audio_out_frame(audio_frame[1], effective_audio_type, eventpoint)
            self.output_queue.put(out0)
            self.output_queue.put(out1)
            step_count += 1
        if profiler is not None:
            profiler.observe("asr.collect_audio_batch", time.perf_counter() - collect_audio_t0)
            profiler.incr("asr.batch_count")
            profiler.incr("asr.pair_frames_in", step_count)

        if self.mode == "httpfile" and eos_received and step_count < self.batch_size:
            pad_t0 = time.perf_counter()
            pad_eventpoint = {"status": "end", "llm_status": "end", "emo": DEFAULT_EMOTION}
            while step_count < self.batch_size:
                silence_pair = (
                    np.zeros(self.chunk, dtype=np.float32),
                    np.zeros(self.chunk, dtype=np.float32),
                )
                self.frames.append(silence_pair[0])
                self.frames.append(silence_pair[1])
                out0 = make_audio_out_frame(silence_pair[0], 1, pad_eventpoint)
                out1 = make_audio_out_frame(silence_pair[1], 1, pad_eventpoint)
                self.output_queue.put(out0)
                self.output_queue.put(out1)
                step_count += 1
            if profiler is not None:
                profiler.observe("asr.pad_eos_tail", time.perf_counter() - pad_t0)

        if self.mode == "httpfile" and eos_received and step_count == 0:
            self._httpfile_finished = True
            if profiler is not None:
                profiler.observe("asr.run_step_total", time.perf_counter() - run_step_t0)
            return

        if len(self.frames) <= self.stride_left_size + self.stride_right_size:
            if self.mode == "httpfile" and eos_received:
                self._httpfile_finished = True
            if profiler is not None:
                profiler.observe("asr.buffer_under_stride", time.perf_counter() - run_step_t0)
            return

        feat_t0 = time.perf_counter()
        inputs = np.concatenate(self.frames) # [N * chunk]
        whisper_feature = self.audio_processor.audio2feat(inputs)
        whisper_chunks = self.audio_processor.feature2chunks(feature_array=whisper_feature,fps=self.fps/2,batch_size=self.batch_size,start=self.stride_left_size/2 )
        if profiler is not None:
            profiler.observe("asr.extract_feature", time.perf_counter() - feat_t0)
        feat_q_t0 = time.perf_counter()
        self.feat_queue.put(whisper_chunks)
        if profiler is not None:
            profiler.observe("asr.enqueue_feature", time.perf_counter() - feat_q_t0)
            profiler.observe("asr.run_step_total", time.perf_counter() - run_step_t0)
        self.frames = self.frames[-(self.stride_left_size + self.stride_right_size):]
        if self.mode == "httpfile" and eos_received:
            self._httpfile_finished = True
