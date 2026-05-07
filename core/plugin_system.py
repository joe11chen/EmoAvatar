from __future__ import annotations

from enum import Enum
import importlib
import importlib.util
import inspect
import pkgutil
from typing import Any


class PluginType(str, Enum):
    TTS = "tts"
    ASR = "asr"
    RENDERER = "renderer"


_REGISTRY: dict[PluginType, dict[str, Any]] = {plugin_type: {} for plugin_type in PluginType}
_BOOTSTRAPPED_FAMILIES: set[str] = set()


def _normalize_plugin_type(plugin_type: PluginType | str) -> PluginType:
    if isinstance(plugin_type, PluginType):
        return plugin_type
    return PluginType(str(plugin_type).strip().lower())


def _normalize_name(name: str) -> str:
    key = str(name).strip().lower()
    if not key:
        raise ValueError("Plugin name cannot be empty")
    return key


def _available_plugins(plugin_type: PluginType) -> str:
    return ", ".join(sorted(_REGISTRY[plugin_type].keys())) or "<none>"


def _get_target(plugin_type: PluginType, name: str) -> Any:
    key = _normalize_name(name)
    plugin_map = _REGISTRY[plugin_type]
    if key not in plugin_map:
        raise KeyError(
            f"Unknown plugin '{key}' for type '{plugin_type.value}'. Available: {_available_plugins(plugin_type)}"
        )
    return plugin_map[key]


def _construct_class(cls: type, kwargs: dict[str, Any]) -> Any:
    sig = inspect.signature(cls.__init__)
    params = {k: v for k, v in sig.parameters.items() if k != "self"}
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())

    ctor_kwargs: dict[str, Any] = {}
    for key, value in kwargs.items():
        if has_var_kw or key in params:
            ctor_kwargs[key] = value

    return cls(**ctor_kwargs)


def register(plugin_type: PluginType | str, name: str):
    normalized_type = _normalize_plugin_type(plugin_type)
    key = _normalize_name(name)

    def decorator(target):
        _REGISTRY[normalized_type][key] = target
        return target

    return decorator


def create(plugin_type: PluginType | str, name: str, *, instantiate: bool = True, **kwargs) -> Any:
    normalized_type = _normalize_plugin_type(plugin_type)
    target = _get_target(normalized_type, name)

    if inspect.isclass(target) and instantiate:
        return _construct_class(target, kwargs)

    return target


def available_plugins(plugin_type: PluginType | str) -> list[str]:
    normalized_type = _normalize_plugin_type(plugin_type)
    return sorted(_REGISTRY[normalized_type].keys())


def _iter_plugin_modules(package_name: str) -> list[tuple[str, bool]]:
    spec = importlib.util.find_spec(package_name)
    if spec is None or not spec.submodule_search_locations:
        return []

    discovered: dict[str, bool] = {}
    for search_path in spec.submodule_search_locations:
        for module in pkgutil.iter_modules([search_path]):
            if module.name.startswith("_"):
                continue
            discovered[module.name] = module.ispkg

    return sorted(discovered.items(), key=lambda item: item[0])


def _import_plugin_family(package_name: str, package_runtime_entry: str | None = None) -> None:
    for module_name, is_package in _iter_plugin_modules(package_name):
        if is_package and package_runtime_entry:
            entry_target = f"{package_name}.{module_name}.{package_runtime_entry}"
            if importlib.util.find_spec(entry_target) is not None:
                importlib.import_module(entry_target)
                continue
        importlib.import_module(f"{package_name}.{module_name}")


def _register_builtin_family(package_name: str, *, family_key: str, package_runtime_entry: str | None = None) -> None:
    if family_key in _BOOTSTRAPPED_FAMILIES:
        return
    _import_plugin_family(package_name, package_runtime_entry=package_runtime_entry)
    _BOOTSTRAPPED_FAMILIES.add(family_key)


def register_builtin_asr_plugins() -> None:
    _register_builtin_family("plugins.asr", family_key="asr")


def register_builtin_tts_plugins() -> None:
    _register_builtin_family("plugins.tts", family_key="tts")


def register_builtin_renderer_plugins() -> None:
    _register_builtin_family("plugins.renderer", family_key="renderer", package_runtime_entry="runtime")


def register_builtin_plugins() -> None:
    register_builtin_tts_plugins()
    register_builtin_renderer_plugins()


def validate_startup_plugins(config) -> None:
    register_builtin_plugins()

    renderer_name = getattr(config.plugins, "renderer", None)
    if not renderer_name:
        raise ValueError("renderer plugin name is required (set plugins.renderer in config)")
    renderer_cls = create(PluginType.RENDERER, renderer_name, instantiate=False)
    renderer_cls.register_dependencies()

    required = [
        (PluginType.RENDERER, renderer_name),
        (PluginType.TTS, config.plugins.tts),
    ]
    required.extend(renderer_cls.required_plugins(config))

    errors: list[str] = []
    for plugin_type, name in required:
        if _normalize_name(name) not in _REGISTRY[plugin_type]:
            available = available_plugins(plugin_type)
            errors.append(
                f"Unknown plugin '{name}' for type '{plugin_type.value}'. Available: {', '.join(available) or '<none>'}"
            )

    if errors:
        lines = "\n".join(f"- {item}" for item in errors)
        raise ValueError(f"Startup plugin validation failed:\n{lines}")
