from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openclaw_claude_code.errors import BridgeError

DEFAULT_TIMEZONE_NAME = "UTC"


def detect_local_timezone_name() -> str:
    env_tz = os.environ.get("TZ")
    if env_tz and _is_valid_timezone_name(env_tz):
        return env_tz

    tzinfo = datetime.now().astimezone().tzinfo
    tz_key = getattr(tzinfo, "key", None)
    if tz_key and _is_valid_timezone_name(tz_key):
        return tz_key

    localtime = Path("/etc/localtime")
    try:
        resolved = str(localtime.resolve())
    except OSError:
        resolved = str(localtime)

    marker = "/zoneinfo/"
    if marker in resolved:
        candidate = resolved.split(marker, 1)[1]
        if _is_valid_timezone_name(candidate):
            return candidate

    return DEFAULT_TIMEZONE_NAME


def validate_timezone_name(timezone_name: str) -> str:
    candidate = timezone_name.strip()
    if not candidate:
        raise BridgeError("invalid_config", "`timezone` 不能为空。")
    if not _is_valid_timezone_name(candidate):
        raise BridgeError("invalid_config", f"无效的时区：{candidate}。")
    return candidate


def current_time_iso(timezone_name: str) -> str:
    zone = ZoneInfo(validate_timezone_name(timezone_name))
    value = datetime.now(zone).replace(microsecond=0).isoformat()
    if timezone_name == DEFAULT_TIMEZONE_NAME:
        return value.replace("+00:00", "Z")
    return value


def timestamp_to_iso(timestamp: float, timezone_name: str) -> str:
    zone = ZoneInfo(validate_timezone_name(timezone_name))
    value = datetime.fromtimestamp(timestamp, tz=zone).replace(microsecond=0).isoformat()
    if timezone_name == DEFAULT_TIMEZONE_NAME:
        return value.replace("+00:00", "Z")
    return value


def _is_valid_timezone_name(timezone_name: str) -> bool:
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return False
    return True
