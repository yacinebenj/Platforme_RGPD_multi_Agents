"""Role and permission model for the RGPD platform.

The agents can recommend and pre-fill, but final legal decisions stay tied to
human roles. Keeping permissions centralized makes the UI and API consistent.
"""

from __future__ import annotations

from typing import Any


ROLE_LABELS = {
    "admin": "Admin",
    "dpo": "DPO",
    "contributeur": "Contributeur metier",
    "auditeur": "Auditeur / Lecteur",
}

ROLE_ALIASES = {
    "administrator": "admin",
    "administrateur": "admin",
    "dpo": "dpo",
    "delegue": "dpo",
    "delegue_protection_donnees": "dpo",
    "contributeur": "contributeur",
    "contributeur_metier": "contributeur",
    "metier": "contributeur",
    "auditeur": "auditeur",
    "lecteur": "auditeur",
    "reader": "auditeur",
}

ROLE_PERMISSIONS = {
    "admin": {
        "auth:login",
        "dashboard:view",
        "analysis:run",
        "analysis:view",
        "incident:declare",
        "incident:view",
        "actions:view",
        "actions:update",
        "actions:update_assigned",
        "actions:close",
        "reports:generate",
        "reports:view",
        "assistant:chat",
        "proofs:view",
        "register:view",
        "users:manage",
        "roles:manage",
        "connectors:configure",
        "settings:manage",
    },
    "dpo": {
        "auth:login",
        "dashboard:view",
        "analysis:run",
        "analysis:view",
        "ai:validate",
        "legal_basis:manage",
        "consents:view",
        "consents:manage",
        "dsar:view",
        "dsar:manage",
        "dsar:respond",
        "incident:view",
        "incident:declare",
        "incident:manage",
        "aipd:view",
        "aipd:manage",
        "actions:view",
        "actions:update",
        "actions:update_assigned",
        "actions:close",
        "reports:generate",
        "reports:view",
        "assistant:chat",
        "proofs:view",
        "register:view",
    },
    "contributeur": {
        "auth:login",
        "dashboard:view",
        "analysis:view",
        "legal_basis:suggest",
        "consents:view",
        "consents:suggest",
        "dsar:view_assigned",
        "dsar:support",
        "incident:declare",
        "incident:view_assigned",
        "incident:support",
        "aipd:view_assigned",
        "aipd:support",
        "actions:view_assigned",
        "actions:update_assigned",
        "reports:view_limited",
        "proofs:view_limited",
        "register:view_limited",
    },
    "auditeur": {
        "auth:login",
        "dashboard:view",
        "analysis:view",
        "incident:view",
        "dsar:view",
        "aipd:view",
        "reports:view",
        "assistant:chat",
        "proofs:view",
        "register:view",
    },
}


def normalise_role(role: str | None) -> str:
    """Return the canonical internal role key."""
    if not role:
        return "auditeur"
    key = role.strip().lower().replace(" ", "_").replace("-", "_")
    return ROLE_ALIASES.get(key, key if key in ROLE_LABELS else "auditeur")


def role_permissions(role: str | None) -> list[str]:
    """Return sorted permissions for a role."""
    return sorted(ROLE_PERMISSIONS.get(normalise_role(role), set()))


def has_permission(role: str | None, permission: str) -> bool:
    """Check whether a role includes a permission."""
    return permission in ROLE_PERMISSIONS.get(normalise_role(role), set())


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive fields and attach display metadata."""
    role = normalise_role(row.get("role"))
    return {
        "id": row.get("id"),
        "username": row.get("username"),
        "full_name": row.get("full_name") or row.get("username"),
        "email": row.get("email"),
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "permissions": role_permissions(role),
        "is_active": bool(row.get("is_active", 1)),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "last_login_at": row.get("last_login_at"),
    }
