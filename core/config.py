from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf


@dataclass
class RuntimeSyncWindowConfig:
    l: int
    m: int
    r: int


@dataclass
class RuntimeFrameConfig:
    width: int
    height: int


@dataclass
class RuntimeConfig:
    fps: int
    sync_window: RuntimeSyncWindowConfig
    frame: RuntimeFrameConfig


@dataclass
class PluginsConfig:
    tts: str
    asr: str
    renderer: str


@dataclass
class RendererConfig:
    avatar_id: str
    batch_size: int
    customvideo_config: str
    multi_avatar: bool
    frame_monitor_dir: str = "tmp/frame_monitor"


@dataclass
class TTSConfig:
    ref_file: str
    ref_text: str | None
    server: str
    max_tokens: int = 120


@dataclass
class TransportConfig:
    mode: str
    push_url: str
    rtc_audio_queue_maxsize: int
    rtc_video_queue_maxsize: int


@dataclass
class ServerConfig:
    max_session: int
    listenport: int


@dataclass
class AppConfig:
    runtime: RuntimeConfig
    plugins: PluginsConfig
    renderer: RendererConfig
    tts: TTSConfig
    transport: TransportConfig
    server: ServerConfig
    custom_actions: list[dict[str, Any]] = field(default_factory=list)
    sessionid: int = 0


def _as_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Config field '{path}' must be a mapping")
    return value


def _as_int(value: Any, path: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Config field '{path}' must be an integer") from exc


def _as_str(value: Any, path: str) -> str:
    if value is None:
        raise ValueError(f"Config field '{path}' cannot be null")
    return str(value)


def _as_bool(value: Any, path: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Config field '{path}' must be a boolean")


def _get_required(data: dict[str, Any], key: str, path: str):
    if key not in data:
        raise ValueError(f"Missing required config field: {path}")
    return data[key]


def _load_custom_actions(path_text: str) -> list[dict[str, Any]]:
    if not path_text:
        return []
    path = Path(path_text)
    if not path.exists():
        raise FileNotFoundError(f"Custom video config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, list):
        raise ValueError(f"Custom video config must be a list: {path}")
    return loaded


def load_app_config(config_path: str) -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a mapping")

    runtime_data = _as_dict(_get_required(raw, "runtime", "runtime"), "runtime")
    sync_data = _as_dict(_get_required(runtime_data, "sync_window", "runtime.sync_window"), "runtime.sync_window")
    frame_data = _as_dict(_get_required(runtime_data, "frame", "runtime.frame"), "runtime.frame")

    plugins_data = _as_dict(_get_required(raw, "plugins", "plugins"), "plugins")
    renderer_data = _as_dict(_get_required(raw, "renderer", "renderer"), "renderer")
    tts_data = _as_dict(_get_required(raw, "tts", "tts"), "tts")
    transport_data = _as_dict(_get_required(raw, "transport", "transport"), "transport")
    server_data = _as_dict(_get_required(raw, "server", "server"), "server")

    runtime = RuntimeConfig(
        fps=_as_int(_get_required(runtime_data, "fps", "runtime.fps"), "runtime.fps"),
        sync_window=RuntimeSyncWindowConfig(
            l=_as_int(_get_required(sync_data, "l", "runtime.sync_window.l"), "runtime.sync_window.l"),
            m=_as_int(_get_required(sync_data, "m", "runtime.sync_window.m"), "runtime.sync_window.m"),
            r=_as_int(_get_required(sync_data, "r", "runtime.sync_window.r"), "runtime.sync_window.r"),
        ),
        frame=RuntimeFrameConfig(
            width=_as_int(_get_required(frame_data, "width", "runtime.frame.width"), "runtime.frame.width"),
            height=_as_int(_get_required(frame_data, "height", "runtime.frame.height"), "runtime.frame.height"),
        ),
    )
    plugins = PluginsConfig(
        tts=_as_str(_get_required(plugins_data, "tts", "plugins.tts"), "plugins.tts"),
        asr=_as_str(_get_required(plugins_data, "asr", "plugins.asr"), "plugins.asr"),
        renderer=_as_str(_get_required(plugins_data, "renderer", "plugins.renderer"), "plugins.renderer"),
    )
    renderer = RendererConfig(
        avatar_id=_as_str(_get_required(renderer_data, "avatar_id", "renderer.avatar_id"), "renderer.avatar_id"),
        batch_size=_as_int(_get_required(renderer_data, "batch_size", "renderer.batch_size"), "renderer.batch_size"),
        customvideo_config=str(renderer_data.get("customvideo_config", "")),
        multi_avatar=_as_bool(_get_required(renderer_data, "multi_avatar", "renderer.multi_avatar"), "renderer.multi_avatar"),
        frame_monitor_dir=str(renderer_data.get("frame_monitor_dir", "tmp/frame_monitor")),
    )
    tts = TTSConfig(
        ref_file=_as_str(_get_required(tts_data, "ref_file", "tts.ref_file"), "tts.ref_file"),
        ref_text=None if tts_data.get("ref_text") is None else str(tts_data["ref_text"]),
        server=_as_str(_get_required(tts_data, "server", "tts.server"), "tts.server"),
        max_tokens=_as_int(tts_data.get("max_tokens", 120), "tts.max_tokens"),
    )
    transport = TransportConfig(
        mode=_as_str(_get_required(transport_data, "mode", "transport.mode"), "transport.mode"),
        push_url=_as_str(_get_required(transport_data, "push_url", "transport.push_url"), "transport.push_url"),
        rtc_audio_queue_maxsize=_as_int(
            _get_required(transport_data, "rtc_audio_queue_maxsize", "transport.rtc_audio_queue_maxsize"),
            "transport.rtc_audio_queue_maxsize",
        ),
        rtc_video_queue_maxsize=_as_int(
            _get_required(transport_data, "rtc_video_queue_maxsize", "transport.rtc_video_queue_maxsize"),
            "transport.rtc_video_queue_maxsize",
        ),
    )
    if transport.mode not in {"webrtc", "rtcpush"}:
        raise ValueError("transport.mode must be one of: webrtc, rtcpush")

    server = ServerConfig(
        max_session=_as_int(_get_required(server_data, "max_session", "server.max_session"), "server.max_session"),
        listenport=_as_int(_get_required(server_data, "listenport", "server.listenport"), "server.listenport"),
    )

    custom_actions = _load_custom_actions(renderer.customvideo_config)
    return AppConfig(
        runtime=runtime,
        plugins=plugins,
        renderer=renderer,
        tts=tts,
        transport=transport,
        server=server,
        custom_actions=custom_actions,
    )
