from __future__ import annotations

import asyncio
import concurrent.futures
import glob
import os
import queue
import subprocess
import threading
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
    def required_plugins(cls, config) -> list[tuple[PluginType, str]]:
        """Declare concrete plugin names required for startup validation."""
        del config
        return []

    @classmethod
    def prepare_shared(cls, config) -> Any:
        """
        Prepare process-level reusable assets (e.g. models, avatar caches).
        Called once during startup.
        """
        del config
        return None

    @classmethod
    def create_session(cls, session_config, prepared: Any) -> "BaseReal":
        """Create a per-session renderer instance from prepared shared assets."""
        del prepared
        return cls(session_config)

    def __init__(self, config):
        self.config = config
        self.sample_rate = 16000
        self.chunk = self.sample_rate // config.runtime.fps
        self.sessionid = self.config.sessionid

        register_builtin_plugins()
        try:
            self.tts = create(PluginType.TTS, config.plugins.tts, config=config, parent=self)
        except KeyError as exc:
            available = ", ".join(available_plugins(PluginType.TTS))
            raise ValueError(f"Unknown tts plugin '{config.plugins.tts}'. Available: {available}") from exc

        self.speaking = False

        self.recording = False
        self._record_video_pipe = None
        self._record_audio_pipe = None
        self._record_video_async_enabled = False
        self._record_video_queue: queue.Queue | None = None
        self._record_video_writer_thread: threading.Thread | None = None
        self._record_video_stop_token = object()
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
        event = normalize_eventpoint(datainfo or {})
        if self.tmp_audio is None:
            self.tmp_audio = audio_chunk
        else:
            self.asr.put_audio_frame((self.tmp_audio, audio_chunk), event)
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
        for item in self.config.custom_actions:
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

    def _get_httpfile_profiler(self):
        if self.config.transport.mode != "httpfile":
            return None
        return getattr(self, "_httpfile_profiler", None)

    def start_recording(self):
        if self.recording:
            return

        os.makedirs("data", exist_ok=True)
        self._record_video_path = f"temp{self.config.sessionid}.mp4"
        self._record_audio_path = f"temp{self.config.sessionid}.aac"
        httpfile_mode = self.config.transport.mode == "httpfile"

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
            "libx264",
        ]
        if httpfile_mode:
            command.extend(
                [
                    "-preset",
                    "ultrafast",
                    "-tune",
                    "zerolatency",
                    "-threads",
                    "4",
                ]
            )
        command.extend(
            [
            self._record_video_path,
            ]
        )
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
            self._record_audio_path,
        ]
        self._record_audio_pipe = subprocess.Popen(acommand, shell=False, stdin=subprocess.PIPE)
        if httpfile_mode:
            self._record_video_async_enabled = True
            self._record_video_queue = queue.Queue(maxsize=8)
            self._record_video_writer_thread = threading.Thread(
                target=self._record_video_writer_loop,
                name=f"record-video-writer-{self.sessionid}",
                daemon=True,
            )
            self._record_video_writer_thread.start()
        else:
            self._record_video_async_enabled = False
            self._record_video_queue = None
            self._record_video_writer_thread = None
        self.recording = True

    def _record_video_writer_loop(self):
        while True:
            if self._record_video_queue is None:
                return
            payload = self._record_video_queue.get()
            try:
                if payload is self._record_video_stop_token:
                    return
                if self._record_video_pipe and self._record_video_pipe.stdin:
                    self._record_video_pipe.stdin.write(payload)
            except Exception as exc:
                logger.warning("record video writer thread error: %s", exc)
                return
            finally:
                self._record_video_queue.task_done()

    def _stop_record_video_writer(self):
        if not self._record_video_async_enabled or self._record_video_queue is None:
            return
        put_deadline = time.perf_counter() + 5.0
        while True:
            try:
                self._record_video_queue.put(self._record_video_stop_token, timeout=0.1)
                break
            except queue.Full:
                if self._record_video_writer_thread is not None and not self._record_video_writer_thread.is_alive():
                    logger.warning("record video writer thread already stopped before stop token enqueue")
                    break
                if time.perf_counter() > put_deadline:
                    logger.warning("timeout when enqueuing stop token to record video writer thread")
                    break
                continue
        if self._record_video_writer_thread is not None:
            self._record_video_writer_thread.join(timeout=5.0)
            if self._record_video_writer_thread.is_alive():
                logger.warning("record video writer thread did not stop cleanly")
        self._record_video_writer_thread = None
        self._record_video_queue = None
        self._record_video_async_enabled = False

    def record_video_data(self, image):
        if self.width == 0:
            self.height, self.width, _ = image.shape
        if not self.recording:
            return
        if self._record_video_async_enabled and self._record_video_queue is not None:
            payload = image.tobytes()
            put_deadline = time.perf_counter() + 5.0
            while self.recording:
                try:
                    self._record_video_queue.put(payload, timeout=0.1)
                    return
                except queue.Full:
                    if self._record_video_writer_thread is not None and not self._record_video_writer_thread.is_alive():
                        logger.warning("record video writer thread is not alive while queue is full; fallback to direct write")
                        break
                    if time.perf_counter() > put_deadline:
                        logger.warning("record video queue put timeout; fallback to direct write")
                        break
                    continue
            if not self.recording:
                return
        if self._record_video_pipe and self._record_video_pipe.stdin:
            self._record_video_pipe.stdin.write(image.tobytes())

    def record_audio_data(self, frame):
        if self.recording and self._record_audio_pipe and self._record_audio_pipe.stdin:
            self._record_audio_pipe.stdin.write(frame.tobytes())

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        self._stop_record_video_writer()
        self._record_video_pipe.stdin.close()
        self._record_video_pipe.wait()
        self._record_audio_pipe.stdin.close()
        self._record_audio_pipe.wait()
        cmd_combine_audio = (
            f"ffmpeg -y -i {self._record_audio_path} -i {self._record_video_path} "
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

    def _enqueue_audio_batch(self, audio_frames, audio_seq, audio_track, quit_event, loop, record_audio: bool = True) -> tuple[int, int, bool]:
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
            if record_audio:
                self.record_audio_data(frame)
        return audio_seq, enqueued_count, True

    def _record_audio_batch_only(self, audio_frames, record_audio: bool = True) -> int:
        if not record_audio:
            return 0
        written_count = 0
        for audio_frame in audio_frames:
            frame, _audio_type, _eventpoint = audio_frame
            frame = (frame * 32767).astype(np.int16)
            self.record_audio_data(frame)
            written_count += 1
        return written_count

    def _build_frame_monitor_path(self) -> str:
        monitor_dir = self.config.renderer.frame_monitor_dir
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
        copy_output=True,
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
        return combine_frame, (combine_frame.copy() if copy_output else combine_frame)

    def _build_speaking_frame(
        self,
        res_frame,
        idx,
        emo,
        transition_start,
        transition_duration,
        last_silent_frame,
        last_speaking_frame,
        copy_output=True,
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
        return combine_frame, (combine_frame.copy() if copy_output else combine_frame)

    def process_frames(self, quit_event, loop=None, audio_track=None, video_track=None):
        monitor_path = self._build_frame_monitor_path()
        with open(monitor_path, "w+") as frame_log:
            _last_speaking = False
            _transition_start = time.time()
            httpfile_mode = self.config.transport.mode == "httpfile"
            direct_record_mode = httpfile_mode and loop is None and audio_track is None and video_track is None
            profiler = self._get_httpfile_profiler()
            _transition_duration = 0.0 if httpfile_mode else 0.1
            _last_silent_frame = None
            _last_speaking_frame = None

            prev_status = None
            video_seq = 0
            audio_seq = 0
            monitor_last_log_time = time.time()
            monitor_video_enqueued = 0
            monitor_audio_enqueued = 0
            process_loop_start = time.perf_counter()

            while not quit_event.is_set():
                loop_t0 = time.perf_counter()
                wait_t0 = loop_t0
                try:
                    res_frame, face_index, audio_frames = self.res_frame_queue.get(block=True, timeout=1)
                except queue.Empty:
                    continue
                if profiler is not None:
                    profiler.observe("process.wait_res_frame", time.perf_counter() - wait_t0)

                build_t0 = time.perf_counter()
                idx, emo = self._resolve_face_index(face_index)

                primary_event = audio_frames[0][2]
                status = primary_event.get("status") or ""
                if status != prev_status:
                    logger.debug("status changed: %s -> %s", prev_status, status)

                prev_status = status
                is_silence_audio = self._is_silence_audio(audio_frames)
                current_speaking = not is_silence_audio
                self.speaking = current_speaking
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
                    if profiler is not None:
                        profiler.incr("process.frames.transition")

                elif is_silence_audio:
                    self.speaking = False
                    combine_frame, _last_silent_frame = self._build_silence_frame(
                        idx,
                        emo,
                        audio_frames,
                        _transition_start,
                        _transition_duration,
                        _last_silent_frame,
                        _last_speaking_frame,
                        copy_output=not httpfile_mode,
                    )
                    if combine_frame is None:
                        continue
                    if profiler is not None:
                        profiler.incr("process.frames.silence")
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
                        copy_output=not httpfile_mode,
                    )
                    if combine_frame is None:
                        continue
                    if profiler is not None:
                        profiler.incr("process.frames.speaking")

                if not httpfile_mode:
                    combine_frame = combine_frame.copy() if hasattr(combine_frame, "copy") else combine_frame
                extra_text = str(primary_event)
                if not httpfile_mode:
                    cv2.putText(combine_frame, "LiveTalking", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (128, 128, 128), 1)
                recordable_for_httpfile = not (
                    self.config.transport.mode == "httpfile" and is_silence_audio and status != "transition"
                )
                if profiler is not None:
                    profiler.incr("process.frames.total")

                if direct_record_mode:
                    if recordable_for_httpfile:
                        record_video_t0 = time.perf_counter()
                        self.record_video_data(combine_frame)
                        if profiler is not None:
                            profiler.observe("process.record_video", time.perf_counter() - record_video_t0)
                        monitor_video_enqueued += 1
                        if profiler is not None:
                            profiler.incr("process.frames.recorded_video")
                    elif profiler is not None:
                        profiler.incr("process.frames.skipped_video")
                    record_audio_t0 = time.perf_counter()
                    enqueued_audio_cnt = self._record_audio_batch_only(
                        audio_frames,
                        record_audio=recordable_for_httpfile,
                    )
                    if profiler is not None:
                        profiler.observe("process.record_audio", time.perf_counter() - record_audio_t0)
                        if enqueued_audio_cnt > 0:
                            profiler.incr("process.frames.recorded_audio", enqueued_audio_cnt)
                        else:
                            profiler.incr("process.frames.skipped_audio", len(audio_frames))
                    monitor_audio_enqueued += enqueued_audio_cnt
                else:
                    video_seq, video_ok = self._enqueue_video(combine_frame, video_seq, video_track, quit_event, loop)
                    if video_ok:
                        monitor_video_enqueued += 1
                    else:
                        logger.warning("rtcpush video frame enqueue aborted")
                        break

                    if recordable_for_httpfile:
                        self.record_video_data(combine_frame)
                    audio_seq, enqueued_audio_cnt, audio_ok = self._enqueue_audio_batch(
                        audio_frames,
                        audio_seq,
                        audio_track,
                        quit_event,
                        loop,
                        record_audio=recordable_for_httpfile,
                    )
                    monitor_audio_enqueued += enqueued_audio_cnt
                    if not audio_ok:
                        logger.warning("rtcpush audio frame enqueue aborted")
                        break

                if profiler is not None:
                    profiler.observe("process.build_frame", time.perf_counter() - build_t0)
                    profiler.observe("process.loop_total", time.perf_counter() - loop_t0)

                self._write_frame_monitor_line(
                    frame_log,
                    f"frame idx={idx} emo={emo} status={status} event={extra_text}",
                )

                if (time.time() - monitor_last_log_time) >= 1.0:
                    stats = self._collect_producer_stats(
                        monitor_video_enqueued,
                        monitor_audio_enqueued,
                        video_track,
                        audio_track,
                    )
                    stats_line = self._build_producer_stats_line(stats)
                    self._write_frame_monitor_line(frame_log, stats_line)
                    # self._log_producer_stats(stats)
                    frame_log.flush()
                    monitor_video_enqueued = 0
                    monitor_audio_enqueued = 0
                    monitor_last_log_time = time.time()

            frame_log.flush()
            if profiler is not None:
                profiler.observe("process.thread_lifetime", time.perf_counter() - process_loop_start)

        logger.info("basereal process_frames thread stop")

    def _enqueue_webrtc_frame(self, track, frame, eventpoint, kind, quit_event, loop) -> bool:
        if track is None or loop is None:
            return False
        if getattr(track, "readyState", "live") != "live":
            logger.info("skip enqueue[%s]: track is not live", kind)
            return False
        future = asyncio.run_coroutine_threadsafe(track._queue.put((frame, eventpoint)), loop)
        while not quit_event.is_set():
            try:
                future.result(timeout=1.0)
                return True
            except concurrent.futures.TimeoutError:
                if getattr(track, "readyState", "live") != "live":
                    logger.info("stop enqueue[%s]: track closed while waiting", kind)
                    return False
                logger.warning("rtcpush enqueue pending[%s]: qsize=%d", kind, track._queue.qsize())
                continue
            except Exception as exc:
                logger.warning("rtcpush enqueue failed[%s]: %s", kind, exc)
                return False
        return False


__all__ = ["BaseReal", "read_imgs", "create_bytes_stream"]
