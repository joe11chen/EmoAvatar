from __future__ import annotations

import queue
import time

import numpy as np
import torch

from data import EMOTION
from logger import logger
from plugins.renderer.musetalk.assets import AvatarMeta, mirror_index


def _pull_audio_frames(audio_out_queue, batch_size: int):
    is_all_silence = True
    audio_frames = []
    for _ in range(batch_size * 2):
        frame, audio_type, eventpoint = audio_out_queue.get()
        audio_frames.append((frame, audio_type, eventpoint))
        if audio_type == 0:
            is_all_silence = False
    return is_all_silence, audio_frames


@torch.no_grad()
def inference(
    quit_event,
    batch_size,
    input_latent_list_cycle,
    audio_feat_queue,
    audio_out_queue,
    res_frame_queue,
    vae,
    unet,
    pe,
    timesteps,
    profiler=None,
):
    length = len(input_latent_list_cycle)
    index = 0
    count = 0
    counttime = 0
    logger.info("start inference")
    while not quit_event.is_set():
        batch_t0 = time.perf_counter()
        try:
            feat_wait_t0 = time.perf_counter()
            whisper_chunks = audio_feat_queue.get(block=True, timeout=1)
            if profiler is not None:
                profiler.observe("infer.wait_feat_queue", time.perf_counter() - feat_wait_t0)
        except queue.Empty:
            continue
        audio_wait_t0 = time.perf_counter()
        is_all_silence, audio_frames = _pull_audio_frames(audio_out_queue, batch_size)
        if profiler is not None:
            profiler.observe("infer.wait_audio_out_queue", time.perf_counter() - audio_wait_t0)
            profiler.incr("infer.batch_count")
            profiler.incr("infer.audio_frames_in", batch_size * 2)
        if is_all_silence:
            silence_put_t0 = time.perf_counter()
            for i in range(batch_size):
                res_frame_queue.put((None, mirror_index(length, index), audio_frames[i * 2 : i * 2 + 2]))
                index += 1
            if profiler is not None:
                profiler.observe("infer.enqueue_silence_res_frame", time.perf_counter() - silence_put_t0)
                profiler.incr("infer.video_frames_out", batch_size)
                profiler.observe("infer.batch_total", time.perf_counter() - batch_t0)
            continue

        t = time.perf_counter()
        prep_t0 = t
        whisper_batch = np.stack(whisper_chunks)
        latent_batch = []
        for i in range(batch_size):
            idx = mirror_index(length, index + i)
            latent_batch.append(input_latent_list_cycle[idx])
        latent_batch = torch.cat(latent_batch, dim=0)

        audio_feature_batch = torch.from_numpy(whisper_batch)
        audio_feature_batch = audio_feature_batch.to(device=unet.device, dtype=unet.model.dtype)
        audio_feature_batch = pe(audio_feature_batch)
        latent_batch = latent_batch.to(dtype=unet.model.dtype)
        if profiler is not None:
            profiler.observe("infer.prepare_batch", time.perf_counter() - prep_t0)

        unet_t0 = time.perf_counter()
        pred_latents = unet.model(latent_batch, timesteps, encoder_hidden_states=audio_feature_batch).sample
        if profiler is not None:
            profiler.observe("infer.unet_forward", time.perf_counter() - unet_t0)
        vae_t0 = time.perf_counter()
        recon = vae.decode_latents(pred_latents)
        if profiler is not None:
            profiler.observe("infer.vae_decode", time.perf_counter() - vae_t0)

        counttime += time.perf_counter() - t
        count += batch_size
        if count >= 100:
            logger.info("------actual avg infer fps:%.4f", count / counttime)
            count = 0
            counttime = 0
        put_t0 = time.perf_counter()
        for i, res_frame in enumerate(recon):
            res_frame_queue.put((res_frame, mirror_index(length, index), audio_frames[i * 2 : i * 2 + 2]))
            index += 1
        if profiler is not None:
            profiler.observe("infer.enqueue_res_frame", time.perf_counter() - put_t0)
            profiler.incr("infer.video_frames_out", len(recon))
            profiler.observe("infer.batch_total", time.perf_counter() - batch_t0)
    logger.info("musereal inference processor stop")


@torch.no_grad()
def multi_avatar_inference(
    quit_event,
    batch_size,
    avatars: dict[str, AvatarMeta],
    audio_feat_queue,
    audio_out_queue,
    res_frame_queue,
    vae,
    unet,
    pe,
    timesteps,
    profiler=None,
):
    count = 0
    counttime = 0
    last_emo = EMOTION.DEFAULT
    logger.info("start multi inference")
    while not quit_event.is_set():
        batch_t0 = time.perf_counter()
        try:
            feat_wait_t0 = time.perf_counter()
            whisper_chunks = audio_feat_queue.get(block=True, timeout=1)
            if profiler is not None:
                profiler.observe("infer.wait_feat_queue", time.perf_counter() - feat_wait_t0)
        except queue.Empty:
            continue
        audio_wait_t0 = time.perf_counter()
        is_all_silence, audio_frames = _pull_audio_frames(audio_out_queue, batch_size)
        if profiler is not None:
            profiler.observe("infer.wait_audio_out_queue", time.perf_counter() - audio_wait_t0)
            profiler.incr("infer.batch_count")
            profiler.incr("infer.audio_frames_in", batch_size * 2)

        if is_all_silence:
            silence_put_t0 = time.perf_counter()
            for i in range(batch_size):
                pair_frames = audio_frames[i * 2 : i * 2 + 2]
                event0 = pair_frames[0][2]
                event1 = pair_frames[1][2]

                emo0 = event0.get("emo")
                emo1 = event1.get("emo")
                if emo0 is not None and emo1 is not None and emo0 != emo1:
                    logger.error("-multi_avatar inference- Emotion conflict in silence frames, audio frame info : %s;%s", event0, event1)
                emo = emo0 or emo1 or last_emo
                if emo not in avatars:
                    logger.warning("-multi_avatar inference- Unknown emotion %s in silence branch, fallback to DEFAULT", emo)
                    emo = EMOTION.DEFAULT
                last_emo = emo

                status1 = event0.get("status")
                status2 = event1.get("status")
                if status1 != status2:
                    logger.error("-multi_avatar inference- Status conflict in silence frames, audio frame info : %s;%s", event0, event1)
                status = status1 if status1 == status2 else (status1 or status2 or "")

                if status == "transition":
                    idx = avatars[emo].index % avatars[emo].length
                else:
                    idx = mirror_index(avatars[emo].length, avatars[emo].index)
                    avatars[emo].index += 1
                res_frame_queue.put((None, (idx, emo), pair_frames))
            if profiler is not None:
                profiler.observe("infer.enqueue_silence_res_frame", time.perf_counter() - silence_put_t0)
                profiler.incr("infer.video_frames_out", batch_size)
                profiler.observe("infer.batch_total", time.perf_counter() - batch_t0)
            continue

        t = time.perf_counter()
        prep_t0 = t
        whisper_batch = np.stack(whisper_chunks)
        latent_batch = []
        face_indexes = []
        for i in range(batch_size):
            event0 = audio_frames[i * 2][2]
            event1 = audio_frames[i * 2 + 1][2]
            emo0 = event0.get("emo")
            emo1 = event1.get("emo")

            if emo0 is not None and emo1 is not None and emo0 != emo1:
                logger.warning("-avatar inference- Emotion conflict, audio frame info : %s;%s", event0, event1)

            emo = emo0 or emo1 or last_emo
            if emo not in avatars:
                logger.warning("-avatar inference- Unknown emotion %s, fallback to DEFAULT", emo)
                emo = EMOTION.DEFAULT
            last_emo = emo

            status1 = event0.get("status")
            status2 = event1.get("status")
            if status1 != status2:
                logger.error("-multi_avatar inference- Status conflict in silence frames, audio frame info : %s;%s", event0, event1)
            status = status1 if status1 == status2 else (status1 or status2 or "")

            if status == "transition":
                idx = avatars[emo].index % avatars[emo].length
            else:
                idx = mirror_index(avatars[emo].length, avatars[emo].index)
                avatars[emo].index += 1
            latent_batch.append(avatars[emo].input_latent_list_cycle[idx])
            face_indexes.append((idx, emo))
        latent_batch = torch.cat(latent_batch, dim=0)

        audio_feature_batch = torch.from_numpy(whisper_batch)
        audio_feature_batch = audio_feature_batch.to(device=unet.device, dtype=unet.model.dtype)
        audio_feature_batch = pe(audio_feature_batch)
        latent_batch = latent_batch.to(dtype=unet.model.dtype)
        if profiler is not None:
            profiler.observe("infer.prepare_batch", time.perf_counter() - prep_t0)

        unet_t0 = time.perf_counter()
        pred_latents = unet.model(latent_batch, timesteps, encoder_hidden_states=audio_feature_batch).sample
        if profiler is not None:
            profiler.observe("infer.unet_forward", time.perf_counter() - unet_t0)
        vae_t0 = time.perf_counter()
        recon = vae.decode_latents(pred_latents)
        if profiler is not None:
            profiler.observe("infer.vae_decode", time.perf_counter() - vae_t0)

        counttime += time.perf_counter() - t
        count += batch_size
        if count >= 100:
            logger.info("------actual avg infer fps:%.4f", count / counttime)
            count = 0
            counttime = 0
        put_t0 = time.perf_counter()
        for i, res_frame in enumerate(recon):
            res_frame_queue.put((res_frame, face_indexes[i], audio_frames[i * 2 : i * 2 + 2]))
        if profiler is not None:
            profiler.observe("infer.enqueue_res_frame", time.perf_counter() - put_t0)
            profiler.incr("infer.video_frames_out", len(recon))
            profiler.observe("infer.batch_total", time.perf_counter() - batch_t0)
    logger.info("musereal inference processor stop")
