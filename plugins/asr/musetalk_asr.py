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

from core.contracts import make_audio_out_frame
from core.plugin_system import PluginType, register
from core.runtime.asr.base import BaseASR
from musetalk.whisper.audio2feature import Audio2Feature

@register(PluginType.ASR, "museasr")
class MuseASR(BaseASR):
    def __init__(self, opt, parent, audio_processor:Audio2Feature):
        super().__init__(opt,parent)
        self.audio_processor = audio_processor

    def run_step(self):
        for _ in range(self.batch_size):
            audio_frame,audio_type,eventpoint = self.get_audio_frame()
            self.frames.append(audio_frame[0])
            self.frames.append(audio_frame[1])
            out0 = make_audio_out_frame(audio_frame[0], audio_type, eventpoint)
            out1 = make_audio_out_frame(audio_frame[1], audio_type, eventpoint)
            self.output_queue.put(out0)
            self.output_queue.put(out1)
        
        if len(self.frames) <= self.stride_left_size + self.stride_right_size:
            return
        
        inputs = np.concatenate(self.frames) # [N * chunk]
        whisper_feature = self.audio_processor.audio2feat(inputs)
        whisper_chunks = self.audio_processor.feature2chunks(feature_array=whisper_feature,fps=self.fps/2,batch_size=self.batch_size,start=self.stride_left_size/2 )
        self.feat_queue.put(whisper_chunks)
        self.frames = self.frames[-(self.stride_left_size + self.stride_right_size):]
