"""Published-invoke plane (feature 018) — caller-token auth for external/cURL/channel callers.

Distinct from the console/session plane (`api.deps.session_principal`): callers here authenticate with a
hashed bearer *caller token* (role `executer`), the tenant is pinned by the token row, and authorization is
the per-resource access policy (not console RBAC). See `token_auth.resolve_caller`.
"""
