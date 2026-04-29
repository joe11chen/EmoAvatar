from __future__ import annotations

import asyncio
import concurrent.futures
import glob
import os
import queue
import subprocess
import time
from io import BytesIO
from typing import Any

import cv2
import numpy as np
import resampy
import soundfile as sf
from av import AudioFrame, VideoFrame
from tqdm import tqdm

from core.contracts import normalize_eventpoint
from core.plugin_system import PluginType, available_plugins, create, register_builtin_plugins
from core.runtime.asr.base import BaseASR
from core.runtime.tts.base import BaseTTS
from data import EMOTION
from logger import logger


def read_imgs(img_list):
    frames = []
    logger.info("reading images...")
    for img_path in tqdm(img_list):
        frame = cv2.imread(img_path)
        frames.append(frame)
    return frames


def create_bytes_stream(byte_stream, target_sample_rate: int) -> np.ndarray:
    stream, sample_rate = sf.read(byte_stream)
    logger.info("[INFO]put audio stream %s: %s", sample_rate, stream.shape)
    stream = stream.astype(np.float32)

    if stream.ndim > 1:
        logger.info("[WARN] audio has %s channels, only use the first.", stream.shape[1])
        stream = stream[:, 0]

    if sample_rate != target_sample_rate and stream.shape[0] > 0:
        logger.info("[WARN] audio sample rate is %s, resampling into %s.", sample_rate, target_sample_rate)
        stream = resampy.resample(x=stream, sr_orig=sample_rate, sr_new=target_sample_rate)

    return stream


class BaseReal:
    tts: BaseTTS
    asr: BaseASR

    @classmethod
    def register_dependencies(cls) -> None:
        """Register dependent plugin families required by this renderer."""

    @classmethod
    def required_plugins(cls, opt) -> list[tuple[PluginType, str]]:
        """Declare concrete plugin names required for startup validation."""
        del opt
        return []

    @classmethod
    def prepare_shared(cls, opt) -> Any:
        """
        Prepare process-level reusable assets (e.g. models, avatar caches).
        Called once during startup.
        """
        del opt
        return None

    @classmethod
    def create_session(cls, session_opt, prepared: Any) -> "BaseReal":
        """Create a per-session renderer instance from prepared shared assets."""
        del prepared
        return cls(session_opt)

    def __init__(self, opt):
        self.opt = opt
        self.sample_rate = 16000
        self.chunk = self.sample_rate // opt.fps
        self.sessionid = self.opt.sessionid

        register_builtin_plugins()
        try:
            self.tts = create(PluginType.TTS, opt.tts, opt=opt, parent=self)
        except KeyError as exc:
            available = ", ".join(available_plugins(PluginType.TTS))
            raise ValueError(f"Unknown tts plugin '{opt.tts}'. Available: {available}") from exc

        self.speaking = False

        self.recording = False
        self._record_video_pipe = None
        self._record_audio_pipe = None
        self.width = self.height = 0

        self.curr_state = 0
        self.custom_img_cycle = {}
        self.custom_audio_cycle = {}
        self.custom_audio_index = {}
        self.custom_index = {}
        self.custom_opt = {}
        self.__loadcustom()

        self.avatars: dict[str, any]
        self.transitions: dict[EMOTION, dict[EMOTION, any]] = None
        self.tmp_audio = None

    def put_msg_txt(self, msg, datainfo: dict | None = None):
        self.tts.put_msg_txt(msg, datainfo or {})

    def put_audio_frame(self, audio_chunk, datainfo: dict | None = None):
        if self.tmp_audio is None:
            self.tmp_audio = audio_chunk
        else:
            self.asr.put_audio_frame((self.tmp_audio, audio_chunk), datainfo or {})
            self.tmp_audio = None

    def put_audio_file(self, filebyte, datainfo: dict | None = None):
        input_stream = BytesIO(filebyte)
        stream = create_bytes_stream(input_stream, self.sample_rate)
        streamlen = stream.shape[0]
        idx = 0
        while streamlen >= self.chunk:
            self.put_audio_frame(stream[idx : idx + self.chunk], datainfo or {})
            streamlen -= self.chunk
            idx += self.chunk

    def flush_talk(self):
        self.tts.flush_talk()
        self.asr.flush_talk()

    def is_speaking(self) -> bool:
        return self.speaking

    def _load_custom_media(self):
        for item in self.opt.customopt:
            logger.info(item)
            input_img_list = glob.glob(os.path.join(item["imgpath"], "*.[jpJP][pnPN]*[gG]"))
            input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
            self.custom_img_cycle[item["audiotype"]] = read_imgs(input_img_list)
            self.custom_audio_cycle[item["audiotype"]], _ = sf.read(item["audiopath"], dtype="float32")
            self.custom_audio_index[item["audiotype"]] = 0
            self.custom_index[item["audiotype"]] = 0
            self.custom_opt[item["audiotype"]] = item

    def __loadcustom(self):
        self._load_custom_media()

    def init_customindex(self):
        self.curr_state = 0
        for key in self.custom_audio_index:
            self.custom_audio_index[key] = 0
        for key in self.custom_index:
            self.custom_index[key] = 0

    def notify(self, eventpoint):
        logger.debug("notify:%s", eventpoint)

    def start_recording(self):
        if self.recording:
            return

        command = [
            "ffmpeg",
            "-y",
            "-an",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{self.width}x{self.height}",
            "-r",
            str(25),
            "-i",
            "-",
            "-pix_fmt",
            "yuv420p",
            "-vcodec",
            "h264",
            f"temp{self.opt.sessionid}.mp4",
        ]
        self._record_video_pipe = subprocess.Popen(command, shell=False, stdin=subprocess.PIPE)

        acommand = [
            "ffmpeg",
            "-y",
            "-vn",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-i",
            "-",
            "-acodec",
            "aac",
            f"temp{self.opt.sessionid}.aac",
        ]
        self._record_audio_pipe = subprocess.Popen(acommand, shell=False, stdin=subprocess.PIPE)
        self.recording = True

    def record_video_data(self, image):
        if self.width == 0:
            self.height, self.width, _ = image.shape
        if self.recording and self._record_video_pipe and self._record_video_pipe.stdin:
            self._record_video_pipe.stdin.write(image.tobytes())

    def record_audio_data(self, frame):
        if self.recording and self._record_audio_pipe and self._record_audio_pipe.stdin:
            self._record_audio_pipe.stdin.write(frame.tobytes())

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        self._record_video_pipe.stdin.close()
        self._record_video_pipe.wait()
        self._record_audio_pipe.stdin.close()
        self._record_audio_pipe.wait()
        cmd_combine_audio = (
            f"ffmpeg -y -i temp{self.opt.sessionid}.aac -i temp{self.opt.sessionid}.mp4 "
            "-c:v copy -c:a copy data/record.mp4"
        )
        os.system(cmd_combine_audio)

    def mirror_index(self, size, index):
        turn = index // size
        res = index % size
        if turn % 2 == 0:
            return res
        return size - res - 1

    def get_audio_stream(self, audiotype):
        idx = self.custom_audio_index[audiotype]
        stream = self.custom_audio_cycle[audiotype][idx : idx + self.chunk]
        self.custom_audio_index[audiotype] += self.chunk
        if self.custom_audio_index[audiotype] >= self.custom_audio_cycle[audiotype].shape[0]:
            self.curr_state = 1
        return stream

    def set_custom_state(self, audiotype, reinit=True):
        logger.info("set_custom_state: %s", audiotype)
        if self.custom_audio_index.get(audiotype) is None:
            return
        self.curr_state = audiotype
        if reinit:
            self.custom_audio_index[audiotype] = 0
            self.custom_index[audiotype] = 0

    def _resolve_face_index(self, face_index):
        if self.multi_avatar:
            return face_index[0], face_index[1]
        return face_index, EMOTION.DEFAULT

    def _normalize_primary_event(self, audio_frames):
        return audio_frames[0][2]

    @staticmethod
    def _is_silence_audio(audio_frames) -> bool:
        return audio_frames[0][1] != 0 and audio_frames[1][1] != 0

    def _enqueue_video(self, combine_frame, video_seq, video_track, quit_event, loop) -> tuple[int, bool]:
        new_frame = VideoFrame.from_ndarray(combine_frame, format="bgr24")
        next_video_seq = video_seq + 1
        video_eventpoint = {"_enqueue_ts": time.time(), "_seq": next_video_seq}
        if self._enqueue_webrtc_frame(video_track, new_frame, video_eventpoint, "video", quit_event, loop):
            return next_video_seq, True
        return video_seq, False

    def _enqueue_audio_batch(self, audio_frames, audio_seq, audio_track, quit_event, loop) -> tuple[int, int, bool]:
        enqueued_count = 0
        for audio_frame in audio_frames:
            frame, _audio_type, eventpoint = audio_frame
            frame = (frame * 32767).astype(np.int16)

            new_audio_frame = AudioFrame(format="s16", layout="mono", samples=frame.shape[0])
            new_audio_frame.planes[0].update(frame.tobytes())
            new_audio_frame.sample_rate = 16000
            next_audio_seq = audio_seq + 1
            audio_eventpoint = dict(eventpoint)
            audio_eventpoint["_enqueue_ts"] = time.time()
            audio_eventpoint["_seq"] = next_audio_seq
            if self._enqueue_webrtc_frame(audio_track, new_audio_frame, audio_eventpoint, "audio", quit_event, loop):
                audio_seq = next_audio_seq
                enqueued_count += 1
            else:
                return audio_seq, enqueued_count, False
            self.record_audio_data(frame)
        return audio_seq, enqueued_count, True

    def _build_frame_monitor_path(self) -> str:
        monitor_dir = getattr(self.opt, "frame_monitor_dir", "tmp/frame_monitor")
        os.makedirs(monitor_dir, exist_ok=True)
        return os.path.join(monitor_dir, f"session_{self.sessionid}.log")

    @staticmethod
    def _write_frame_monitor_line(frame_log, line: str):
        frame_log.write(line + "\n")

    def _collect_producer_stats(self, monitor_video_enqueued, monitor_audio_enqueued, video_track, audio_track):
        video_qsize = -1
        audio_qsize = -1
        res_qsize = -1
        if video_track is not None:
            video_qsize = video_track._queue.qsize()
        if audio_track is not None:
            audio_qsize = audio_track._queue.qsize()
        try:
            res_qsize = self.res_frame_queue.qsize()
        except (NotImplementedError, AttributeError):
            res_qsize = -1

        return monitor_video_enqueued, monitor_audio_enqueued, video_qsize, audio_qsize, res_qsize

    @staticmethod
    def _build_producer_stats_line(stats) -> str:
        video_enq, audio_enq, video_qsize, audio_qsize, res_qsize = stats
        return (
            "producer_stats "
            f"video_enq={video_enq}/s "
            f"audio_enq={audio_enq}/s "
            f"q_video={video_qsize} q_audio={audio_qsize} q_res={res_qsize}"
        )

    @staticmethod
    def _log_producer_stats(stats):
        video_enq, audio_enq, video_qsize, audio_qsize, res_qsize = stats
        logger.info(
            "rtcpush producer stats: video_enq=%d/s audio_enq=%d/s q_video=%d q_audio=%d q_res=%d",
            video_enq,
            audio_enq,
            video_qsize,
            audio_qsize,
            res_qsize,
        )

    @staticmethod
    def _blend_transition(source_frame, target_frame, transition_start, transition_duration):
        if time.time() - transition_start < transition_duration and source_frame is not None:
            alpha = min(1.0, (time.time() - transition_start) / transition_duration)
            return cv2.addWeighted(source_frame, 1 - alpha, target_frame, alpha, 0)
        return target_frame

    def _build_transition_frame(self, primary_event):
        prev = primary_event.get("from")
        now = primary_event.get("to")
        transition_frame_idx = primary_event.get("transition_frame_idx")
        if transition_frame_idx is None:
            logger.error("Transition status found but no transition_frame_idx provided. Skip transition frame.")
            return None
        transition_frame_idx = transition_frame_idx // 2
        transition_frames = self.transitions.get(prev, {}).get(now, []) if self.transitions else []
        if not transition_frames:
            logger.warning("No transition frames found for %s -> %s, skip transition frame.", prev, now)
            return None
        if transition_frame_idx < 0 or transition_frame_idx >= len(transition_frames):
            logger.warning(
                "Transition frame idx out of range: %s (len=%s), skip transition frame.",
                transition_frame_idx,
                len(transition_frames),
            )
            return None
        return transition_frames[transition_frame_idx]

    def _pick_silence_target_frame(self, idx, emo, audiotype):
        if self.custom_index.get(audiotype) is not None:
            mirindex = self.mirror_index(len(self.custom_img_cycle[audiotype]), self.custom_index[audiotype])
            target_frame = self.custom_img_cycle[audiotype][mirindex]
            self.custom_index[audiotype] += 1
            return target_frame
        if self.multi_avatar:
            return self.avatars[emo].frame_list_cycle[idx]
        return self.frame_list_cycle[idx]

    def _build_silence_frame(
        self,
        idx,
        emo,
        audio_frames,
        transition_start,
        transition_duration,
        last_silent_frame,
        last_speaking_frame,
    ):
        audiotype = audio_frames[0][1]
        target_frame = self._pick_silence_target_frame(idx, emo, audiotype)

        if target_frame is None:
            logger.warning(
                "target_frame is None in silence branch, fallback to last cached frame. emo=%s idx=%s audiotype=%s",
                emo,
                idx,
                audiotype,
            )
            target_frame = last_silent_frame if last_silent_frame is not None else last_speaking_frame
            if target_frame is None:
                logger.warning("No cached frame available for fallback, skip this render cycle.")
                return None, last_silent_frame

        combine_frame = self._blend_transition(
            last_speaking_frame,
            target_frame,
            transition_start,
            transition_duration,
        )
        return combine_frame, combine_frame.copy()

    def _build_speaking_frame(
        self,
        res_frame,
        idx,
        emo,
        transition_start,
        transition_duration,
        last_silent_frame,
        last_speaking_frame,
    ):
        try:
            current_frame = self.paste_back_frame(res_frame, idx, emo)
        except Exception as exc:
            logger.warning("paste_back_frame error: %s", exc)
            return None, last_speaking_frame
        if current_frame is None:
            logger.warning("current_frame is None in speaking branch, skip this render cycle. emo=%s idx=%s", emo, idx)
            return None, last_speaking_frame

        combine_frame = self._blend_transition(
            last_silent_frame,
            current_frame,
            transition_start,
            transition_duration,
        )
        return combine_frame, combine_frame.copy()

    def process_frames(self, quit_event, loop=None, audio_track=None, video_track=None):
        monitor_path = self._build_frame_monitor_path()
        with open(monitor_path, "w+") as frame_log:
            _last_speaking = False
            _transition_start = time.time()
            _transition_duration = 0.1
            _last_silent_frame = None
            _last_speaking_frame = None

            prev_status = None
            video_seq = 0
            audio_seq = 0
            monitor_last_log_time = time.time()
            monitor_video_enqueued = 0
            monitor_audio_enqueued = 0

            while not quit_event.is_set():
                try:
                    res_frame, face_index, audio_frames = self.res_frame_queue.get(block=True, timeout=1)
                except queue.Empty:
                    continue

                idx, emo = self._resolve_face_index(face_index)

                primary_event = self._normalize_primary_event(audio_frames)
                status = primary_event.get("status") or ""
                if status != prev_status:
                    logger.debug("status changed: %s -> %s", prev_status, status)

                prev_status = status
                current_speaking = not self._is_silence_audio(audio_frames)
                if current_speaking != _last_speaking:
                    logger.info(
                        "状态切换：%s → %s",
                        "说话" if _last_speaking else "静音",
                        "说话" if current_speaking else "静音",
                    )
                    _transition_start = time.time()
                _last_speaking = current_speaking

                if status == "transition":
                    combine_frame = self._build_transition_frame(primary_event)
                    if combine_frame is None:
                        continue

                elif self._is_silence_audio(audio_frames):
                    self.speaking = False
                    combine_frame, _last_silent_frame = self._build_silence_frame(
                        idx,
                        emo,
                        audio_frames,
                        _transition_start,
                        _transition_duration,
                        _last_silent_frame,
                        _last_speaking_frame,
                    )
                    if combine_frame is None:
                        continue
                else:
                    self.speaking = True
                    combine_frame, _last_speaking_frame = self._build_speaking_frame(
                        res_frame,
                        idx,
                        emo,
                        _transition_start,
                        _transition_duration,
                        _last_silent_frame,
                        _last_speaking_frame,
                    )
                    if combine_frame is None:
                        continue

                if combine_frame is None:
                    logger.warning("combine_frame is None, skip this render cycle. status=%s emo=%s idx=%s", status, emo, idx)
                    continue

                combine_frame = combine_frame.copy() if hasattr(combine_frame, "copy") else combine_frame
                extra_text = str(primary_event)
                cv2.putText(combine_frame, "LiveTalking", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (128, 128, 128), 1)

                video_seq, video_ok = self._enqueue_video(combine_frame, video_seq, video_track, quit_event, loop)
                if video_ok:
                    monitor_video_enqueued += 1
                else:
                    logger.warning("rtcpush video frame enqueue aborted")
                    break

                self.record_video_data(combine_frame)
                self._write_frame_monitor_line(
                    frame_log,
                    f"frame idx={idx} emo={emo} status={status} event={extra_text}",
                )

                audio_seq, enqueued_audio_cnt, audio_ok = self._enqueue_audio_batch(
                    audio_frames,
                    audio_seq,
                    audio_track,
                    quit_event,
                    loop,
                )
                monitor_audio_enqueued += enqueued_audio_cnt
                if not audio_ok:
                    logger.warning("rtcpush audio frame enqueue aborted")
                    break

                if (time.time() - monitor_last_log_time) >= 1.0:
                    stats = self._collect_producer_stats(
                        monitor_video_enqueued,
                        monitor_audio_enqueued,
                        video_track,
                        audio_track,
                    )
                    stats_line = self._build_producer_stats_line(stats)
                    self._write_frame_monitor_line(frame_log, stats_line)
                    self._log_producer_stats(stats)
                    frame_log.flush()
                    monitor_video_enqueued = 0
                    monitor_audio_enqueued = 0
                    monitor_last_log_time = time.time()

            frame_log.flush()

        logger.info("basereal process_frames thread stop")

    def _enqueue_webrtc_frame(self, track, frame, eventpoint, kind, quit_event, loop) -> bool:
        if track is None or loop is None:
            return False
        future = asyncio.run_coroutine_threadsafe(track._queue.put((frame, eventpoint)), loop)
        while not quit_event.is_set():
            try:
                future.result(timeout=1.0)
                return True
            except concurrent.futures.TimeoutError:
                logger.warning("rtcpush enqueue pending[%s]: qsize=%d", kind, track._queue.qsize())
                continue
            except Exception as exc:
                logger.warning("rtcpush enqueue failed[%s]: %s", kind, exc)
                return False
        return False


__all__ = ["BaseReal", "read_imgs", "create_bytes_stream"]
