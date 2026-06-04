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
    user_id: str = "default"
    enable_transition: bool = True
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
    httpfile_batch_cap: int = 8


@dataclass
class ServerConfig:
    max_session: int
    listenport: int


@dataclass
class AvatarJobsConfig:
    enabled: bool = True
    enable_transition: bool = False
    api_base_url: str = "https://uu888898-7797583f5900.bjb2.seetacloud.com:8443"
    workflow_id: str = "myanimate_v0526"
    assets_dir: str = "assets"
    output_dir: str = "assets/user"
    tmp_dir: str = "tmp/avatar_jobs"
    poll_interval_sec: float = 2.0
    poll_timeout_sec: int = 900
    positive_prompt_template: str = "a person speaking with {emotion} emotion"
    negative_prompt: str = "low quality, blurry, overexposed, subtitles, text, watermark"
    continue_on_error: bool = True
    input_image_key: str = "601:image"
    input_video_key: str = "640:video"
    positive_prompt_key: str = "648:positive_prompt"
    negative_prompt_key: str = "648:negative_prompt"
    upload_endpoint: str = "/api/comfy/upload/file"
    generate_endpoint: str = "/api/workflow/generate"
    result_endpoint: str = "/api/workflow/result"
    comfy_view_endpoint: str = "/api/comfy/view"
    emotion_video_map: dict[str, str] = field(default_factory=dict)
    emotion_prompts: dict[str, str] = field(default_factory=dict)


@dataclass
class AppConfig:
    runtime: RuntimeConfig
    plugins: PluginsConfig
    renderer: RendererConfig
    tts: TTSConfig
    transport: TransportConfig
    server: ServerConfig
    avatar_jobs: AvatarJobsConfig = field(default_factory=AvatarJobsConfig)
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


def _as_float(value: Any, path: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Config field '{path}' must be a float") from exc


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
    avatar_jobs_data = raw.get("avatar_jobs", {})
    if avatar_jobs_data is None:
        avatar_jobs_data = {}
    if not isinstance(avatar_jobs_data, dict):
        raise ValueError("Config field 'avatar_jobs' must be a mapping")

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
        user_id=_as_str(renderer_data.get("user_id", renderer_data.get("avatar_id", "default")), "renderer.user_id"),
        enable_transition=_as_bool(
            renderer_data.get("enabletransition", renderer_data.get("enable_transition", True)),
            "renderer.enabletransition",
        ),
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
        push_url=_as_str(transport_data.get("push_url", ""), "transport.push_url"),
        rtc_audio_queue_maxsize=_as_int(
            _get_required(transport_data, "rtc_audio_queue_maxsize", "transport.rtc_audio_queue_maxsize"),
            "transport.rtc_audio_queue_maxsize",
        ),
        rtc_video_queue_maxsize=_as_int(
            _get_required(transport_data, "rtc_video_queue_maxsize", "transport.rtc_video_queue_maxsize"),
            "transport.rtc_video_queue_maxsize",
        ),
        httpfile_batch_cap=_as_int(transport_data.get("httpfile_batch_cap", 8), "transport.httpfile_batch_cap"),
    )
    if transport.mode not in {"webrtc", "rtcpush", "httpfile"}:
        raise ValueError("transport.mode must be one of: webrtc, rtcpush, httpfile")

    server = ServerConfig(
        max_session=_as_int(_get_required(server_data, "max_session", "server.max_session"), "server.max_session"),
        listenport=_as_int(_get_required(server_data, "listenport", "server.listenport"), "server.listenport"),
    )

    emotion_video_map_raw = avatar_jobs_data.get("emotion_video_map") or {}
    if not isinstance(emotion_video_map_raw, dict):
        raise ValueError("avatar_jobs.emotion_video_map must be a mapping")
    emotion_video_map = {str(k): str(v) for k, v in emotion_video_map_raw.items() if k and v}

    emotion_prompts_raw = avatar_jobs_data.get("emotion_prompts") or {}
    if not isinstance(emotion_prompts_raw, dict):
        raise ValueError("avatar_jobs.emotion_prompts must be a mapping")
    emotion_prompts = {str(k): str(v) for k, v in emotion_prompts_raw.items() if k and v}

    avatar_jobs = AvatarJobsConfig(
        enabled=_as_bool(avatar_jobs_data.get("enabled", True), "avatar_jobs.enabled"),
        enable_transition=_as_bool(
            avatar_jobs_data.get("enable_transition", False),
            "avatar_jobs.enable_transition",
        ),
        api_base_url=_as_str(
            avatar_jobs_data.get("api_base_url", AvatarJobsConfig.api_base_url),
            "avatar_jobs.api_base_url",
        ),
        workflow_id=_as_str(
            avatar_jobs_data.get("workflow_id", AvatarJobsConfig.workflow_id),
            "avatar_jobs.workflow_id",
        ),
        assets_dir=_as_str(avatar_jobs_data.get("assets_dir", AvatarJobsConfig.assets_dir), "avatar_jobs.assets_dir"),
        output_dir=_as_str(avatar_jobs_data.get("output_dir", AvatarJobsConfig.output_dir), "avatar_jobs.output_dir"),
        tmp_dir=_as_str(avatar_jobs_data.get("tmp_dir", AvatarJobsConfig.tmp_dir), "avatar_jobs.tmp_dir"),
        poll_interval_sec=_as_float(
            avatar_jobs_data.get("poll_interval_sec", AvatarJobsConfig.poll_interval_sec),
            "avatar_jobs.poll_interval_sec",
        ),
        poll_timeout_sec=_as_int(
            avatar_jobs_data.get("poll_timeout_sec", AvatarJobsConfig.poll_timeout_sec),
            "avatar_jobs.poll_timeout_sec",
        ),
        positive_prompt_template=_as_str(
            avatar_jobs_data.get("positive_prompt_template", AvatarJobsConfig.positive_prompt_template),
            "avatar_jobs.positive_prompt_template",
        ),
        negative_prompt=_as_str(
            avatar_jobs_data.get("negative_prompt", AvatarJobsConfig.negative_prompt),
            "avatar_jobs.negative_prompt",
        ),
        continue_on_error=_as_bool(
            avatar_jobs_data.get("continue_on_error", AvatarJobsConfig.continue_on_error),
            "avatar_jobs.continue_on_error",
        ),
        input_image_key=_as_str(
            avatar_jobs_data.get("input_image_key", AvatarJobsConfig.input_image_key),
            "avatar_jobs.input_image_key",
        ),
        input_video_key=_as_str(
            avatar_jobs_data.get("input_video_key", AvatarJobsConfig.input_video_key),
            "avatar_jobs.input_video_key",
        ),
        positive_prompt_key=_as_str(
            avatar_jobs_data.get("positive_prompt_key", AvatarJobsConfig.positive_prompt_key),
            "avatar_jobs.positive_prompt_key",
        ),
        negative_prompt_key=_as_str(
            avatar_jobs_data.get("negative_prompt_key", AvatarJobsConfig.negative_prompt_key),
            "avatar_jobs.negative_prompt_key",
        ),
        upload_endpoint=_as_str(
            avatar_jobs_data.get("upload_endpoint", AvatarJobsConfig.upload_endpoint),
            "avatar_jobs.upload_endpoint",
        ),
        generate_endpoint=_as_str(
            avatar_jobs_data.get("generate_endpoint", AvatarJobsConfig.generate_endpoint),
            "avatar_jobs.generate_endpoint",
        ),
        result_endpoint=_as_str(
            avatar_jobs_data.get("result_endpoint", AvatarJobsConfig.result_endpoint),
            "avatar_jobs.result_endpoint",
        ),
        comfy_view_endpoint=_as_str(
            avatar_jobs_data.get("comfy_view_endpoint", AvatarJobsConfig.comfy_view_endpoint),
            "avatar_jobs.comfy_view_endpoint",
        ),
        emotion_video_map=emotion_video_map,
        emotion_prompts=emotion_prompts,
    )

    custom_actions = _load_custom_actions(renderer.customvideo_config)
    return AppConfig(
        runtime=runtime,
        plugins=plugins,
        renderer=renderer,
        tts=tts,
        transport=transport,
        server=server,
        avatar_jobs=avatar_jobs,
        custom_actions=custom_actions,
    )
