from __future__ import annotations

from typing import Any, Mapping


def normalize_eventpoint(
    payload: Mapping[str, Any] | None,
    *,
    default_emo: Any | None = None,
    default_llm_status: str | None = None,
) -> dict[str, Any]:
    """
    Normalize event payload to a plain dict.
    Runtime chain is unified on dict-based event payloads.
    """

    event: dict[str, Any] = dict(payload) if isinstance(payload, Mapping) else {}
    if event.get("emo") is None and default_emo is not None:
        event["emo"] = default_emo
    if event.get("llm_status") is None and default_llm_status is not None:
        event["llm_status"] = default_llm_status
    return event


def make_idle_eventpoint(llm_status: str, emo: Any) -> dict[str, Any]:
    return normalize_eventpoint({"llm_status": llm_status, "emo": emo})


def make_audio_out_frame(
    frame: Any,
    frame_type: int,
    eventpoint: Mapping[str, Any] | None,
    *,
    default_emo: Any | None = None,
    default_llm_status: str | None = None,
) -> tuple[Any, int, dict[str, Any]]:
    return (
        frame,
        frame_type,
        normalize_eventpoint(
            eventpoint,
            default_emo=default_emo,
            default_llm_status=default_llm_status,
        ),
    )
