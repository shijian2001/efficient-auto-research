from __future__ import annotations

import secrets


def generate_v2() -> str:
    return secrets.token_hex(16)


__all__ = ["generate_v2"]
