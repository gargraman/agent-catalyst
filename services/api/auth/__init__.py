"""Pluggable IdP authentication package (feature 012, M5). API-layer only (P6)."""
from __future__ import annotations

from api.auth.base import AuthError, IdPProvider
from api.auth.registry import AuthConfigError, get_active_provider

__all__ = ["AuthError", "IdPProvider", "AuthConfigError", "get_active_provider"]
