from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SUBMODULE = Path(__file__).resolve().parents[1] / "Bk-Light-AppBypass"
if str(_SUBMODULE) not in sys.path:
    sys.path.insert(0, str(_SUBMODULE))

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_DEFAULT_STATUS_DIR = "/tmp/hermes_agent_status"
_PLACEHOLDER_ADDRESSES = {None, "", "YOUR-BLE-DEVICE-ADDRESS"}

_DEFAULT_STATUSES: dict[str, str] = {
    "idle": "😴",
    "thinking": "🧠",
    "tool_use": "⚙️",
    "permission": "🔔",
    "success": "✅",
    "failed": "❌",
    "cancelled": "⏹️",
}


def is_image_path(value: str) -> bool:
    return Path(value).suffix.lower() in _IMAGE_EXTENSIONS


@dataclass
class DeviceSettings:
    address: str | None = None
    name_prefix: str | None = "BK"
    auto_reconnect: bool = True
    reconnect_delay: float = 2.0
    mtu: int = 2048
    brightness: float = 0.35
    scan_timeout: float = 6.0


@dataclass
class PanelSettings:
    tile_width: int = 32
    tile_height: int = 32


@dataclass
class StatusSettings:
    stale_threshold: int = 3600
    statuses: dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_STATUSES))
    emoji_font: str | None = None
    status_dir: str = _DEFAULT_STATUS_DIR


@dataclass
class AgentStatusConfig:
    device: DeviceSettings = field(default_factory=DeviceSettings)
    panel: PanelSettings = field(default_factory=PanelSettings)
    status: StatusSettings = field(default_factory=StatusSettings)


ClaudeStatusConfig = AgentStatusConfig


def _pick_fields(data: dict[str, Any], cls: type) -> dict[str, Any]:
    valid = {f for f in cls.__dataclass_fields__}
    return {k: v for k, v in data.items() if k in valid}


def _resolve_config_path(path: Path | None) -> Path:
    if path is not None:
        return path
    local = Path("config.local.yaml")
    if local.exists():
        return local
    return Path("config.yaml")


def _status_block(raw: dict[str, Any]) -> dict[str, Any]:
    return raw.get("agent_status") or raw.get("claude_status") or {}


def _fallback_submodule_address() -> str | None:
    candidate = _SUBMODULE / "config.yaml"
    if not candidate.exists():
        return None
    import yaml
    try:
        raw: dict[str, Any] = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    address = (raw.get("device", {}) or {}).get("address")
    if address in _PLACEHOLDER_ADDRESSES:
        return None
    return str(address)


def load_config(path: Path | None = None) -> AgentStatusConfig:
    path = _resolve_config_path(path)
    if not path.exists():
        return AgentStatusConfig()

    print(f"Loading config from: {path}")

    import yaml

    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    device_data = raw.get("device", {})
    env_address = os.getenv("MOONSIDE_ADDRESS") or os.getenv("AGENT_STATUS_DEVICE_ADDRESS") or os.getenv("BK_LIGHT_ADDRESS")
    if env_address:
        device_data["address"] = env_address
    elif device_data.get("address") in _PLACEHOLDER_ADDRESSES:
        fallback_address = _fallback_submodule_address()
        if fallback_address:
            device_data["address"] = fallback_address
    device = DeviceSettings(**_pick_fields(device_data, DeviceSettings))

    panel = PanelSettings(**_pick_fields(raw.get("panels", {}), PanelSettings))

    status_data = _status_block(raw)
    merged_statuses = {**_DEFAULT_STATUSES, **status_data.get("statuses", {})}
    status = StatusSettings(
        stale_threshold=status_data.get("stale_threshold", 3600),
        statuses=merged_statuses,
        emoji_font=status_data.get("emoji_font"),
        status_dir=str(status_data.get("status_dir") or _DEFAULT_STATUS_DIR),
    )

    return AgentStatusConfig(device=device, panel=panel, status=status)


def to_app_config(config: AgentStatusConfig):
    from bk_light.config import AppConfig, DeviceConfig, PanelsConfig

    device = DeviceConfig(
        address=config.device.address,
        auto_reconnect=config.device.auto_reconnect,
        reconnect_delay=config.device.reconnect_delay,
        mtu=config.device.mtu,
        brightness=config.device.brightness,
        scan_timeout=config.device.scan_timeout,
    )
    if config.device.name_prefix is not None:
        setattr(device, "name_prefix", config.device.name_prefix)
    panels = PanelsConfig(
        tile_width=config.panel.tile_width,
        tile_height=config.panel.tile_height,
    )
    return AppConfig(device=device, panels=panels)

