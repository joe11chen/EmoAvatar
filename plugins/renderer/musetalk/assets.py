from __future__ import annotations

import glob
import os
import pickle
from dataclasses import dataclass
from pathlib import Path

import torch

from core.runtime.renderer import read_imgs
from data import DEFAULT_USER_ID, EMOTION, emotion_avatar_id, emotion_transition_profile
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


@dataclass
class MuseTalkUserResource:
    user_id: str
    avatars: dict[EMOTION, AvatarMeta]
    transitions: dict[EMOTION, dict[EMOTION, list]]


def mirror_index(size: int, index: int) -> int:
    turn = index // size
    res = index % size
    if turn % 2 == 0:
        return res
    return size - res - 1


def _safe_resource_id(value: str | None, fallback: str = "default") -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    return "".join(ch for ch in text if ch.isalnum() or ch in {"-", "_", "."}) or fallback


def user_data_root(user_id: str | None) -> Path:
    return Path("data") / _safe_resource_id(user_id)


def user_avatar_path(user_id: str | None, avatar_id: str) -> Path:
    return user_data_root(user_id) / "avatars" / avatar_id


def legacy_avatar_path(avatar_id: str) -> Path:
    return Path("data") / "avatars" / avatar_id


def user_transition_path(user_id: str | None, transition_name: str) -> Path:
    return user_data_root(user_id) / "transitions" / transition_name


def legacy_transition_path(transition_name: str) -> Path:
    return Path("data") / "transitions" / transition_name


def resolve_avatar_path(user_id: str | None, avatar_id: str, *, allow_legacy: bool = True) -> Path:
    path = user_avatar_path(user_id, avatar_id)
    if path.exists():
        return path
    legacy = legacy_avatar_path(avatar_id)
    if allow_legacy and legacy.exists():
        logger.info("Using legacy avatar path for avatar_id=%s: %s", avatar_id, legacy)
        return legacy
    return path


def resolve_transition_path(user_id: str | None, transition_name: str, *, allow_legacy: bool = True) -> Path:
    path = user_transition_path(user_id, transition_name)
    if path.exists():
        return path
    legacy = legacy_transition_path(transition_name)
    if allow_legacy and legacy.exists():
        logger.info("Using legacy transition path for transition=%s: %s", transition_name, legacy)
        return legacy
    return path


def load_avatar(avatar_id, user_id: str | None = None):
    avatar_path = resolve_avatar_path(user_id, str(avatar_id))
    full_imgs_path = avatar_path / "full_imgs"
    coords_path = avatar_path / "coords.pkl"
    latents_out_path = avatar_path / "latents.pt"
    mask_out_path = avatar_path / "mask"
    mask_coords_path = avatar_path / "mask_coords.pkl"

    input_latent_list_cycle = torch.load(latents_out_path)
    with open(coords_path, "rb") as f:
        coord_list_cycle = pickle.load(f)
    input_img_list = glob.glob(os.path.join(str(full_imgs_path), "*.[jpJP][pnPN]*[gG]"))
    input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    frame_list_cycle = read_imgs(input_img_list)
    with open(mask_coords_path, "rb") as f:
        mask_coords_list_cycle = pickle.load(f)
    input_mask_list = glob.glob(os.path.join(str(mask_out_path), "*.[jpJP][pnPN]*[gG]"))
    input_mask_list = sorted(input_mask_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    mask_list_cycle = read_imgs(input_mask_list)
    return frame_list_cycle, mask_list_cycle, coord_list_cycle, mask_coords_list_cycle, input_latent_list_cycle


def load_multi_avatar(avatar_ids: list[EMOTION], user_id: str | None = None):
    avatars: dict[EMOTION, AvatarMeta] = {}
    cache_by_avatar_id: dict[str, AvatarMeta] = {}
    for emotion in avatar_ids:
        avatar_id = emotion_avatar_id(emotion)
        cached = cache_by_avatar_id.get(avatar_id)
        if cached is None:
            frame_list_cycle, mask_list_cycle, coord_list_cycle, mask_coords_list_cycle, input_latent_list_cycle = load_avatar(
                avatar_id,
                user_id=user_id,
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


def _read_transition_frames(transition_dir: Path) -> list:
    image_list = glob.glob(os.path.join(str(transition_dir), "*.[jpJP][pnPN]*[gG]"))
    image_list = sorted(image_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0].split("_")[-1]))
    return read_imgs(image_list)


def load_transitions(avatar_ids: list[EMOTION], user_id: str | None = None):
    transitions: dict[EMOTION, dict[EMOTION, list]] = {}
    safe_user_id = _safe_resource_id(user_id)
    allow_legacy = safe_user_id == DEFAULT_USER_ID
    for emo1 in avatar_ids:
        transitions[emo1] = {}
        for emo2 in avatar_ids:
            if emo1 == emo2:
                continue
            from_key = emotion_transition_profile(emo1)
            to_key = emotion_transition_profile(emo2)
            transition_name = f"{from_key}2{to_key}"
            transition_dir = resolve_transition_path(safe_user_id, transition_name, allow_legacy=allow_legacy)
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


def load_user_resource(
    user_id: str | None,
    avatar_ids: list[EMOTION],
    *,
    enable_transition: bool = True,
) -> MuseTalkUserResource:
    safe_user_id = _safe_resource_id(user_id)
    return MuseTalkUserResource(
        user_id=safe_user_id,
        avatars=load_multi_avatar(avatar_ids, user_id=safe_user_id),
        transitions=load_transitions(avatar_ids, user_id=safe_user_id) if enable_transition else {},
    )
