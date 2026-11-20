"""Pluggable IdP authentication package (feature 012, M5). API-layer only (P6)."""
from __future__ import annotations

from api.auth.base import AuthError, GroupEnumerating, IdPProvider
from api.auth.registry import AuthConfigError, get_active_provider

__all__ = ["AuthError", "IdPProvider", "GroupEnumerating", "AuthConfigError", "get_active_provider"]
