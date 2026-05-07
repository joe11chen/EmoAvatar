from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import resampy
import soundfile as sf

from core.plugin_system import PluginType, register
from core.runtime.tts.base import BaseTTS, State
from data import EMOTION, EMOTION_VECTOR
from logger import logger


def _normalize_emotion(value) -> EMOTION:
    if isinstance(value, EMOTION):
        return value
    if isinstance(value, str):
        for emotion in EMOTION:
            if value in {emotion.name, emotion.value}:
                return emotion
    return EMOTION.DEFAULT


@register(PluginType.TTS, "indextts2")
class IndexTTS2(BaseTTS):
    def __init__(self, config, parent):
        super().__init__(config, parent)
        self.server_url = config.tts.server
        self.max_tokens = config.tts.max_tokens
        self.prev_emo = EMOTION.DEFAULT

        default_ref = Path("data/audios/voice_11.wav")
        ref_file = Path(str(config.tts.ref_file))
        self.ref_audio_path = ref_file if ref_file.exists() else default_ref

        try:
            from gradio_client import Client, handle_file

            self.client = Client(self.server_url)
            self.handle_file = handle_file
            logger.info("IndexTTS2 Gradio client initialized: %s", self.server_url)
        except ImportError:
            logger.error("IndexTTS2 requires gradio_client: pip install gradio_client")
            raise
        except Exception:
            logger.exception("IndexTTS2 Gradio client initialization failed")
            raise

    def txt_to_audio(self, msg: tuple[str, dict]):
        text, textevent = msg
        emotion = _normalize_emotion(textevent.get("emo"))
        segments = self.split_text(text)
        if not segments:
            logger.warning("IndexTTS2 split produced no segments, fallback to raw text")
            segments = [text]

        for seg_idx, segment_text in enumerate(segments):
            if self.state != State.RUNNING:
                break

            emotion_vector = EMOTION_VECTOR.get(emotion, EMOTION_VECTOR[EMOTION.DEFAULT])
            audio_file = self.indextts2_generate(segment_text, emotion_vector)
            if not audio_file:
                logger.error("IndexTTS2 generation failed for segment %d", seg_idx + 1)
                continue

            if emotion != self.prev_emo:
                self._emit_transition_silence(self.prev_emo, emotion, textevent, text)

            self.file_to_stream(
                audio_file=audio_file,
                msg=(segment_text, textevent),
                is_first=(seg_idx == 0),
                is_last=(seg_idx == len(segments) - 1),
            )
            self.prev_emo = emotion

    def _emit_transition_silence(
        self,
        prev_emo: EMOTION,
        next_emo: EMOTION,
        textevent: dict,
        text: str,
    ) -> None:
        transitions = getattr(self.parent, "transitions", None)
        if not transitions:
            return
        frames = transitions.get(prev_emo, {}).get(next_emo, [])
        if not frames:
            return
        for frame_idx in range(len(frames) * 2):
            eventpoint = {
                "status": "transition",
                "text": text,
                "from": prev_emo,
                "to": next_emo,
                "transition_frame_idx": frame_idx,
            }
            eventpoint.update(textevent)
            self.parent.put_audio_frame(np.zeros(self.chunk, np.float32), eventpoint)

    def split_text(self, text: str) -> list[str]:
        try:
            result = self.client.predict(
                text=text,
                max_text_tokens_per_segment=self.max_tokens,
                api_name="/on_input_text_change",
            )
            data = result.get("value", {}).get("data", [])
            segments: list[str] = []
            for item in data:
                if len(item) >= 2 and item[1]:
                    segments.append(item[1])
            return segments or [text]
        except Exception:
            logger.exception("IndexTTS2 split_text failed, fallback to raw text")
            return [text]

    def indextts2_generate(self, text: str, emotion_vector: list[float]):
        start = time.perf_counter()
        vec = list(emotion_vector[:8]) + [0.0] * max(0, 8 - len(emotion_vector))
        try:
            result = self.client.predict(
                emo_control_method="Use emotion vectors",
                prompt=self.handle_file(str(self.ref_audio_path)),
                emo_ref_path=self.handle_file(str(self.ref_audio_path)),
                text=text,
                api_name="/gen_single",
                vec1=vec[0],
                vec2=vec[1],
                vec3=vec[2],
                vec4=vec[3],
                vec5=vec[4],
                vec6=vec[5],
                vec7=vec[6],
                vec8=vec[7],
            )
            logger.info("IndexTTS2 generated segment in %.2fs", time.perf_counter() - start)
            if "value" in result:
                return result["value"]
            logger.error("IndexTTS2 unexpected response: %s", result)
            return None
        except Exception:
            logger.exception("IndexTTS2 API call failed")
            return None

    def file_to_stream(
        self,
        audio_file: str,
        msg: tuple[str, dict],
        is_first: bool = False,
        is_last: bool = False,
    ) -> None:
        text, textevent = msg
        try:
            stream, sample_rate = sf.read(audio_file)
            stream = stream.astype(np.float32)

            if stream.ndim > 1:
                stream = stream[:, 0]
            if sample_rate != self.sample_rate and stream.shape[0] > 0:
                stream = resampy.resample(x=stream, sr_orig=sample_rate, sr_new=self.sample_rate)

            streamlen = stream.shape[0]
            idx = 0
            audio_frame_cnt = 0
            first_chunk = True
            while streamlen >= self.chunk and self.state == State.RUNNING:
                status = "start" if first_chunk and is_first else "streaming"
                eventpoint = {"status": status, "text": text}
                eventpoint.update(textevent)
                self.parent.put_audio_frame(stream[idx:idx + self.chunk], eventpoint)
                streamlen -= self.chunk
                idx += self.chunk
                audio_frame_cnt += 1
                first_chunk = False

            if is_last:
                eventpoint = {"status": "end", "text": text}
                eventpoint.update(textevent)
                self.parent.put_audio_frame(np.zeros(self.chunk, np.float32), eventpoint)
                audio_frame_cnt += 1

            # BaseReal packs two 20ms chunks into one ASR input.
            if audio_frame_cnt % 2 == 1:
                tail_status = "end" if is_last else "streaming"
                tail_event = {"status": tail_status, "text": text}
                tail_event.update(textevent)
                self.parent.put_audio_frame(np.zeros(self.chunk, np.float32), tail_event)
        except Exception:
            logger.exception("IndexTTS2 file_to_stream failed")
