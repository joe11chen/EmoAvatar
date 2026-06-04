from __future__ import annotations

import time
from threading import RLock
from threading import Event, Thread

import cv2
import numpy as np
import torch
import torch.multiprocessing as mp

from core.plugin_system import PluginType, create, register, register_builtin_asr_plugins
from core.runtime.renderer.base import BaseReal
from data import EMOTION, DEFAULT_EMOTION
from logger import logger
from musetalk.myutil import get_image_blending
from musetalk.utils.utils import load_all_model
from musetalk.whisper.audio2feature import Audio2Feature
from plugins.renderer.musetalk.assets import AvatarMeta, MuseTalkUserResource, load_avatar, load_user_resource
from plugins.renderer.musetalk.inference_workers import inference, multi_avatar_inference


def load_model():
    vae, unet, pe = load_all_model()
    device = torch.device(
        "cuda"
        if torch.cuda.is_available()
        else ("mps" if (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()) else "cpu")
    )
    timesteps = torch.tensor([0], device=device)
    pe = pe.half().to(device)
    vae.vae = vae.vae.half().to(device)
    unet.model = unet.model.half().to(device)
    audio_processor = Audio2Feature(model_path="./models/whisper")
    return vae, unet, pe, timesteps, audio_processor


@torch.no_grad()
def warm_up(batch_size, model):
    logger.info("warmup model...")
    vae, unet, pe, timesteps, _ = model
    whisper_batch = np.ones((batch_size, 50, 384), dtype=np.uint8)
    latent_batch = torch.ones(batch_size, 8, 32, 32).to(unet.device)

    audio_feature_batch = torch.from_numpy(whisper_batch)
    audio_feature_batch = audio_feature_batch.to(device=unet.device, dtype=unet.model.dtype)
    audio_feature_batch = pe(audio_feature_batch)
    latent_batch = latent_batch.to(dtype=unet.model.dtype)
    pred_latents = unet.model(latent_batch, timesteps, encoder_hidden_states=audio_feature_batch).sample
    vae.decode_latents(pred_latents)


class MuseTalkModelResource:
    """
    Process-level shared resource holder for MuseTalk.

    Owns heavy model/material loading and warmup. Session renderers reuse
    this object instead of loading model assets repeatedly.
    """

    def __init__(self, config):
        avatar_ids = list(EMOTION)
        self.avatar_ids = avatar_ids
        self.model = load_model()
        self._user_resources: dict[tuple[str, bool], MuseTalkUserResource] = {}
        self._resource_lock = RLock()
        self.enable_transition = bool(getattr(config.renderer, "enable_transition", True))

        self.transitions = None
        if config.renderer.multi_avatar:
            user_resource = self.get_user_resource(config.renderer.user_id, enable_transition=self.enable_transition)
            self.transitions = user_resource.transitions
            self.avatar = user_resource.avatars
        else:
            self.avatar = load_avatar(config.renderer.avatar_id, user_id=config.renderer.user_id)

        warm_up(config.renderer.batch_size, self.model)

    def get_user_resource(
        self,
        user_id: str,
        *,
        reload: bool = False,
        enable_transition: bool | None = None,
    ) -> MuseTalkUserResource:
        transition_enabled = self.enable_transition if enable_transition is None else bool(enable_transition)
        cache_key = (user_id, transition_enabled)
        with self._resource_lock:
            if reload or cache_key not in self._user_resources:
                logger.info(
                    "Loading MuseTalk user resource: user_id=%s reload=%s enable_transition=%s",
                    user_id,
                    reload,
                    transition_enabled,
                )
                self._user_resources[cache_key] = load_user_resource(
                    user_id,
                    self.avatar_ids,
                    enable_transition=transition_enabled,
                )
            return self._user_resources[cache_key]

    def reload_user_resource(self, user_id: str, *, enable_transition: bool | None = None) -> MuseTalkUserResource:
        return self.get_user_resource(user_id, reload=True, enable_transition=enable_transition)


@register(PluginType.RENDERER, "musetalk")
class MuseReal(BaseReal):
    @classmethod
    def register_dependencies(cls) -> None:
        register_builtin_asr_plugins()

    @classmethod
    def required_plugins(cls, config) -> list[tuple[PluginType, str]]:
        asr_plugin = config.plugins.asr
        return [
            (PluginType.ASR, asr_plugin),
        ]

    @classmethod
    def prepare_shared(cls, config):
        return MuseTalkModelResource(config)

    @classmethod
    def create_session(cls, session_config, prepared):
        if not isinstance(prepared, MuseTalkModelResource):
            raise ValueError("musetalk renderer expects MuseTalkModelResource payload")

        asr_plugin = session_config.plugins.asr
        return cls(
            session_config,
            resource=prepared,
            asr_plugin_name=asr_plugin,
        )

    @torch.no_grad()
    def __init__(
        self,
        config,
        resource: MuseTalkModelResource,
        asr_plugin_name: str = "museasr",
    ):
        super().__init__(config)
        self.fps = config.runtime.fps
        self.batch_size = config.renderer.batch_size
        self.res_frame_queue = mp.Queue(self.batch_size * 2)
        self.asr_plugin_name = asr_plugin_name

        self.resource = resource
        self.vae, self.unet, self.pe, self.timesteps, self.audio_processor = self.resource.model

        self.asr = create(
            PluginType.ASR,
            self.asr_plugin_name,
            config=config,
            parent=self,
            audio_processor=self.audio_processor,
        )
        self.asr.warm_up()

        self.multi_avatar = config.renderer.multi_avatar
        self.user_id = config.renderer.user_id
        self.enable_transition = bool(getattr(config.renderer, "enable_transition", True))

        if self.multi_avatar:
            user_resource = self.resource.get_user_resource(
                self.user_id,
                enable_transition=self.enable_transition,
            )
            self.transitions: dict[str, dict[str, any]] = user_resource.transitions
            self.avatars: dict[str, AvatarMeta] = user_resource.avatars
        else:
            self.transitions: dict[str, dict[str, any]] = None
            (
                self.frame_list_cycle,
                self.mask_list_cycle,
                self.coord_list_cycle,
                self.mask_coords_list_cycle,
                self.input_latent_list_cycle,
            ) = load_avatar(config.renderer.avatar_id, user_id=self.user_id)

    def reload_user_resource(self, user_id: str | None = None):
        target_user = user_id or self.user_id
        user_resource = self.resource.reload_user_resource(
            target_user,
            enable_transition=self.enable_transition,
        )
        if self.multi_avatar and target_user == self.user_id:
            self.transitions = user_resource.transitions
            self.avatars = user_resource.avatars
        return user_resource

    def paste_back_frame(self, pred_frame, idx: int, emo: EMOTION = DEFAULT_EMOTION):
        if self.multi_avatar:
            avatar = self.avatars[emo]
            bbox = avatar.coord_list_cycle[idx]
            ori_frame = avatar.frame_list_cycle[idx]
            x1, y1, x2, y2 = bbox

            res_frame = cv2.resize(pred_frame.astype("uint8"), (x2 - x1, y2 - y1))
            mask = avatar.mask_list_cycle[idx]
            mask_crop_box = avatar.mask_coords_list_cycle[idx]
            return get_image_blending(ori_frame, res_frame, bbox, mask, mask_crop_box)

        bbox = self.coord_list_cycle[idx]
        ori_frame = self.frame_list_cycle[idx]
        x1, y1, x2, y2 = bbox

        res_frame = cv2.resize(pred_frame.astype("uint8"), (x2 - x1, y2 - y1))
        mask = self.mask_list_cycle[idx]
        mask_crop_box = self.mask_coords_list_cycle[idx]
        return get_image_blending(ori_frame, res_frame, bbox, mask, mask_crop_box)

    def render(self, quit_event, loop=None, audio_track=None, video_track=None):
        self.init_customindex()
        self.tts.render(quit_event)

        infer_quit_event = Event()
        profiler = self._get_httpfile_profiler()
        infer_target = multi_avatar_inference if self.multi_avatar else inference
        if self.multi_avatar:
            infer_thread = Thread(
                target=infer_target,
                args=(
                    infer_quit_event,
                    self.batch_size,
                    self.avatars,
                    self.asr.feat_queue,
                    self.asr.output_queue,
                    self.res_frame_queue,
                    self.vae,
                    self.unet,
                    self.pe,
                    self.timesteps,
                    profiler,
                ),
            )
        else:
            infer_thread = Thread(
                target=infer_target,
                args=(
                    infer_quit_event,
                    self.batch_size,
                    self.input_latent_list_cycle,
                    self.asr.feat_queue,
                    self.asr.output_queue,
                    self.res_frame_queue,
                    self.vae,
                    self.unet,
                    self.pe,
                    self.timesteps,
                    profiler,
                ),
            )
        infer_thread.start()

        process_quit_event = Event()
        process_thread = Thread(target=self.process_frames, args=(process_quit_event, loop, audio_track, video_track))
        process_thread.start()

        while not quit_event.is_set():
            self.asr.run_step()
            if video_track and video_track._queue.qsize() >= 1.5 * self.config.renderer.batch_size:
                logger.debug("sleep qsize=%d", video_track._queue.qsize())
                time.sleep(0.04 * video_track._queue.qsize() * 0.8)
        logger.info("musereal thread stop")

        infer_quit_event.set()
        infer_thread.join()

        process_quit_event.set()
        process_thread.join()
