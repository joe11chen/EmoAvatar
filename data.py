from __future__ import annotations

from enum import Enum
from typing import Any


class EMOTION(Enum):
    DEFAULT = "DEFAULT"
    # Five-stage cognitive-emotion progression.
    EMOTIONAL_FLOODING = "EMOTIONAL_FLOODING"
    RIGID_DEFENSE = "RIGID_DEFENSE"
    WAVERING_DOUBT = "WAVERING_DOUBT"
    OPEN_ACCEPTANCE = "OPEN_ACCEPTANCE"
    RELIEF_GROWTH = "RELIEF_GROWTH"


EMOTION_SEQUENCE = [
    EMOTION.EMOTIONAL_FLOODING,
    EMOTION.RIGID_DEFENSE,
    EMOTION.WAVERING_DOUBT,
    EMOTION.OPEN_ACCEPTANCE,
    EMOTION.RELIEF_GROWTH,
]

# End-of-utterance and fallback state.
DEFAULT_EMOTION = EMOTION.DEFAULT

EMOTION_VECTOR = {
    # vec1~vec8: happy, sad, angry, afraid, disgusted, melancholy, suprised, calm
    EMOTION.DEFAULT: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2],
    EMOTION.EMOTIONAL_FLOODING: [0.0, 0.34, 0.06, 0.24, 0.08, 0.28, 0.0, 0.0],
    EMOTION.RIGID_DEFENSE: [0.0, 0.10, 0.38, 0.14, 0.12, 0.08, 0.0, 0.0],
    EMOTION.WAVERING_DOUBT: [0.0, 0.16, 0.10, 0.22, 0.06, 0.16, 0.03, 0.0],
    EMOTION.OPEN_ACCEPTANCE: [0.09, 0.06, 0.02, 0.04, 0.01, 0.06, 0.04, 0.22],
    EMOTION.RELIEF_GROWTH: [0.22, 0.02, 0.0, 0.02, 0.0, 0.02, 0.05, 0.34],
}

EMOTION_DESCRIPTION = {
    EMOTION.DEFAULT: "默认中性态：平稳、客观、不过度卷入情绪。",
    EMOTION.EMOTIONAL_FLOODING: "情绪淹没态：混淆事实与想法，沉浸痛苦，认为感觉即事实。",
    EMOTION.RIGID_DEFENSE: "固着抗辩态：坚持旧想法为真，抗拒改变，容易陷入反复辩解。",
    EMOTION.WAVERING_DOUBT: "动摇疑惑态：意识到旧想法有偏差，但尚缺乏稳定反驳能力。",
    EMOTION.OPEN_ACCEPTANCE: "开放接纳态：愿意尝试新解释框架，开始寻求建设性观点。",
    EMOTION.RELIEF_GROWTH: "释然成长态：情绪更稳定，对未来更有信心并展现自我效能。",
}

# Avatar folder id used by renderer in multi-avatar mode.
EMOTION_AVATAR_ID = {
    EMOTION.DEFAULT: EMOTION.DEFAULT.value,
    EMOTION.EMOTIONAL_FLOODING: EMOTION.EMOTIONAL_FLOODING.value,
    EMOTION.RIGID_DEFENSE: EMOTION.RIGID_DEFENSE.value,
    EMOTION.WAVERING_DOUBT: EMOTION.WAVERING_DOUBT.value,
    EMOTION.OPEN_ACCEPTANCE: EMOTION.OPEN_ACCEPTANCE.value,
    EMOTION.RELIEF_GROWTH: EMOTION.RELIEF_GROWTH.value,
}

# Transition folder key prefix under data/transitions: {from_key}2{to_key}
EMOTION_TRANSITION_PROFILE = {
    EMOTION.DEFAULT: EMOTION.DEFAULT.name,
    EMOTION.EMOTIONAL_FLOODING: EMOTION.EMOTIONAL_FLOODING.name,
    EMOTION.RIGID_DEFENSE: EMOTION.RIGID_DEFENSE.name,
    EMOTION.WAVERING_DOUBT: EMOTION.WAVERING_DOUBT.name,
    EMOTION.OPEN_ACCEPTANCE: EMOTION.OPEN_ACCEPTANCE.name,
    EMOTION.RELIEF_GROWTH: EMOTION.RELIEF_GROWTH.name,
}

_EMOTION_ALIASES = {
    "默认态": EMOTION.DEFAULT,
    "中性态": EMOTION.DEFAULT,
    "情绪淹没态": EMOTION.EMOTIONAL_FLOODING,
    "固着抗辩态": EMOTION.RIGID_DEFENSE,
    "动摇疑惑态": EMOTION.WAVERING_DOUBT,
    "开放接纳态": EMOTION.OPEN_ACCEPTANCE,
    "释然成长态": EMOTION.RELIEF_GROWTH,
}


def normalize_emotion(value: Any, *, default: EMOTION = DEFAULT_EMOTION, strict: bool = False) -> EMOTION:
    if isinstance(value, EMOTION):
        return value

    text = str(value or "").strip()
    if not text:
        return default

    upper = text.upper()
    lower = text.lower()
    for emotion in EMOTION:
        if upper == emotion.name or lower == emotion.value:
            return emotion

    alias = _EMOTION_ALIASES.get(text)
    if alias is not None:
        return alias

    if strict:
        raise ValueError(f"unsupported emotion: {value}")
    return default


def next_emotion(current: EMOTION) -> EMOTION:
    try:
        idx = EMOTION_SEQUENCE.index(current)
    except ValueError:
        return DEFAULT_EMOTION
    if idx >= len(EMOTION_SEQUENCE) - 1:
        return EMOTION_SEQUENCE[-1]
    return EMOTION_SEQUENCE[idx + 1]


def emotion_avatar_id(emotion: EMOTION | str | None) -> str:
    normalized = normalize_emotion(emotion, strict=False)
    return EMOTION_AVATAR_ID.get(normalized, EMOTION_AVATAR_ID[DEFAULT_EMOTION])


def emotion_transition_profile(emotion: EMOTION | str | None) -> str:
    normalized = normalize_emotion(emotion, strict=False)
    return EMOTION_TRANSITION_PROFILE.get(normalized, EMOTION_TRANSITION_PROFILE[DEFAULT_EMOTION])
