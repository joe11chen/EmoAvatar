from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.runtime.renderer.base import BaseReal

if TYPE_CHECKING:
    from aiortc import RTCPeerConnection


@dataclass
class RuntimeContext:
    opt: Any
    renderer_cls: type[BaseReal] | None = None
    renderer_prepared: Any = None
    nerfreals: dict[int, BaseReal] = field(default_factory=dict)
    pcs: set["RTCPeerConnection"] = field(default_factory=set)
