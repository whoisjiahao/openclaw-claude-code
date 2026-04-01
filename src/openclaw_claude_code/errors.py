from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BridgeError(Exception):
    error_code: str
    message: str
    exit_status: int = 1

    def to_payload(self) -> dict[str, str]:
        return {
            "error_code": self.error_code,
            "message": self.message,
        }

