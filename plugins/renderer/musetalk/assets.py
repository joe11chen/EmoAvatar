from __future__ import annotations

import glob
import os
import pickle
from dataclasses import dataclass

import torch

from core.runtime.renderer import read_imgs
from data import EMOTION, emotion_avatar_id, emotion_transition_profile
from logger import logger


@dataclass
class AvatarMeta:
    frame_list_cycle: list
    mask_list_cycle: list
    coord_list_cycle: list
    mask_coords_list_cycle: list
    input_latent_list_cycle: list
    length: int
    index: int = 0


def mirror_index(size: int, index: int) -> int:
    turn = index // size
    res = index % size
    if turn % 2 == 0:
        return res
    return size - res - 1


def load_avatar(avatar_id):
    avatar_path = f"./data/avatars/{avatar_id}"
    full_imgs_path = f"{avatar_path}/full_imgs"
    coords_path = f"{avatar_path}/coords.pkl"
    latents_out_path = f"{avatar_path}/latents.pt"
    mask_out_path = f"{avatar_path}/mask"
    mask_coords_path = f"{avatar_path}/mask_coords.pkl"

    input_latent_list_cycle = torch.load(latents_out_path)
    with open(coords_path, "rb") as f:
        coord_list_cycle = pickle.load(f)
    input_img_list = glob.glob(os.path.join(full_imgs_path, "*.[jpJP][pnPN]*[gG]"))
    input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    frame_list_cycle = read_imgs(input_img_list)
    with open(mask_coords_path, "rb") as f:
        mask_coords_list_cycle = pickle.load(f)
    input_mask_list = glob.glob(os.path.join(mask_out_path, "*.[jpJP][pnPN]*[gG]"))
    input_mask_list = sorted(input_mask_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    mask_list_cycle = read_imgs(input_mask_list)
    return frame_list_cycle, mask_list_cycle, coord_list_cycle, mask_coords_list_cycle, input_latent_list_cycle


def load_multi_avatar(avatar_ids: list[EMOTION]):
    avatars: dict[EMOTION, AvatarMeta] = {}
    cache_by_avatar_id: dict[str, AvatarMeta] = {}
    for emotion in avatar_ids:
        avatar_id = emotion_avatar_id(emotion)
        cached = cache_by_avatar_id.get(avatar_id)
        if cached is None:
            frame_list_cycle, mask_list_cycle, coord_list_cycle, mask_coords_list_cycle, input_latent_list_cycle = load_avatar(
                avatar_id
            )
            cached = AvatarMeta(
                frame_list_cycle=frame_list_cycle,
                mask_list_cycle=mask_list_cycle,
                coord_list_cycle=coord_list_cycle,
                mask_coords_list_cycle=mask_coords_list_cycle,
                input_latent_list_cycle=input_latent_list_cycle,
                length=len(input_latent_list_cycle),
            )
            cache_by_avatar_id[avatar_id] = cached
        avatars[emotion] = cached
    return avatars


def _read_transition_frames(transition_dir: str) -> list:
    image_list = glob.glob(os.path.join(transition_dir, "*.[jpJP][pnPN]*[gG]"))
    image_list = sorted(image_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0].split("_")[-1]))
    return read_imgs(image_list)


def load_transitions(avatar_ids: list[EMOTION]):
    transition_path = "./data/transitions"
    transitions: dict[EMOTION, dict[EMOTION, list]] = {}
    for emo1 in avatar_ids:
        transitions[emo1] = {}
        for emo2 in avatar_ids:
            if emo1 == emo2:
                continue
            from_key = emotion_transition_profile(emo1)
            to_key = emotion_transition_profile(emo2)
            transition_dir = os.path.join(transition_path, f"{from_key}2{to_key}")
            if os.path.exists(transition_dir):
                logger.info("Loading transition frames for %s to %s from %s", emo1.name, emo2.name, transition_dir)
                transitions[emo1][emo2] = _read_transition_frames(transition_dir)
            else:
                logger.warning(
                    "No transition frames found for %s to %s. Expected folder: %s",
                    emo1.name,
                    emo2.name,
                    transition_dir,
                )
                transitions[emo1][emo2] = []
    return transitions
