from __future__ import annotations

import glob
import os
import pickle
from dataclasses import dataclass

import torch

from core.runtime.renderer import read_imgs
from data import EMOTION
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
    avatars = {}
    for avatar_id in avatar_ids:
        frame_list_cycle, mask_list_cycle, coord_list_cycle, mask_coords_list_cycle, input_latent_list_cycle = load_avatar(
            avatar_id.value
        )
        avatars[avatar_id] = AvatarMeta(
            frame_list_cycle=frame_list_cycle,
            mask_list_cycle=mask_list_cycle,
            coord_list_cycle=coord_list_cycle,
            mask_coords_list_cycle=mask_coords_list_cycle,
            input_latent_list_cycle=input_latent_list_cycle,
            length=len(input_latent_list_cycle),
        )
    return avatars


def load_transitions(avatar_ids: list[EMOTION]):
    transition_path = "./data/transitions"
    transitions = {}
    for emo1 in avatar_ids:
        transitions[emo1] = {}
        for emo2 in avatar_ids:
            if emo1 == emo2:
                continue
            transition_dir = os.path.join(transition_path, f"{emo1.name}2{emo2.name}")
            if os.path.exists(transition_dir):
                logger.info("Loading transition frames for %s to %s", emo1, emo2)
                image_list = glob.glob(os.path.join(transition_dir, "*.[jpJP][pnPN]*[gG]"))
                image_list = sorted(
                    image_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0].split("_")[-1])
                )
                transitions[emo1][emo2] = read_imgs(image_list)
            else:
                logger.warning("No transition frames found for %s to %s. Expected folder: %s", emo1, emo2, transition_dir)
                transitions[emo1][emo2] = []
    return transitions
