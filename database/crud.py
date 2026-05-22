"""
database/crud.py
================
Save and load helpers for all 6 database tables.
"""

import json
import logging
import re
import hashlib
import hmac
import secrets
import uuid
from typing import Optional, Any
from datetime import datetime, timedelta
from database.db import get_connection
from security.roles import normalise_role, public_user

DEFAULT_APP_USERS = [
    {
        "username": "admin",
        "password": "admin123",
        "role": "admin",
        "full_name": "Administrateur plateforme",
        "email": "admin@rgpd.local",
    },
    {
        "username": "dpo",
        "password": "dpo123",
        "role": "dpo",
        "full_name": "Y. Benjemaa - DPO",
        "email": "dpo@rgpd.local",
    },
    {
        "username": "metier",
        "password": "metier123",
        "role": "contributeur",
        "full_name": "Contributeur metier",
        "email": "metier@rgpd.local",
    },
    {
        "username": "auditeur",
        "password": "auditeur123",
        "role": "auditeur",
        "full_name": "Auditeur / Lecteur",
        "email": "auditeur@rgpd.local",
    },
]


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 120_000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def _verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    try:
        algorithm, iterations, salt, digest = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        ).hex()
        return hmac.compare_digest(candidate, digest)
    except Exception:
        return False


def ensure_default_users() -> list:
    """Create local demo users if they do not exist yet."""
    conn = get_connection()
    cursor = conn.cursor()
    now = _now_iso()
    for user in DEFAULT_APP_USERS:
        cursor.execute("SELECT id FROM app_users WHERE username = ?", (user["username"],))
        if cursor.fetchone():
            continue
        cursor.execute(
            """
            INSERT INTO app_users (
                created_at, updated_at, username, password_hash, full_name,
                email, role, is_active, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                now,
                now,
                user["username"],
                _hash_password(user["password"]),
                user["full_name"],
                user["email"],
                normalise_role(user["role"]),
                _json_dumps({"origin": "default_demo_user"}),
            ),
        )
    conn.commit()
    conn.close()
    return list_app_users()


def list_app_users() -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM app_users ORDER BY role, username")
    rows = [public_user(dict(row)) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_app_user(user_id: int) -> Optional[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM app_users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return public_user(dict(row)) if row else None


def authenticate_user(username: str, password: str) -> Optional[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM app_users WHERE lower(username) = lower(?) AND is_active = 1",
        (username or "",),
    )
    row = cursor.fetchone()
    if not row or not _verify_password(password or "", row["password_hash"]):
        conn.close()
        return None
    now = _now_iso()
    cursor.execute(
        "UPDATE app_users SET last_login_at = ?, updated_at = ? WHERE id = ?",
        (now, now, row["id"]),
    )
    conn.commit()
    cursor.execute("SELECT * FROM app_users WHERE id = ?", (row["id"],))
    refreshed = cursor.fetchone()
    conn.close()
    return public_user(dict(refreshed)) if refreshed else None


def create_app_user(
    username: str,
    password: str,
    role: str,
    full_name: str = "",
    email: str = "",
    is_active: bool = True,
    metadata: Optional[dict] = None,
) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    now = _now_iso()
    cursor.execute(
        """
        INSERT INTO app_users (
            created_at, updated_at, username, password_hash, full_name,
            email, role, is_active, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            now,
            username.strip(),
            _hash_password(password),
            full_name,
            email,
            normalise_role(role),
            1 if is_active else 0,
            _json_dumps(metadata or {"origin": "admin_created"}),
        ),
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return get_app_user(user_id)


def update_app_user(
    user_id: int,
    full_name: Optional[str] = None,
    email: Optional[str] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    password: Optional[str] = None,
) -> Optional[dict]:
    updates = []
    values = []
    if full_name is not None:
        updates.append("full_name = ?")
        values.append(full_name)
    if email is not None:
        updates.append("email = ?")
        values.append(email)
    if role is not None:
        updates.append("role = ?")
        values.append(normalise_role(role))
    if is_active is not None:
        updates.append("is_active = ?")
        values.append(1 if is_active else 0)
    if password:
        updates.append("password_hash = ?")
        values.append(_hash_password(password))
    if not updates:
        return get_app_user(user_id)
    updates.append("updated_at = ?")
    values.append(_now_iso())
    values.append(user_id)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE app_users SET {', '.join(updates)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return get_app_user(user_id)


def create_app_session(user_id: int, user_agent: str = None, ttl_hours: int = 12) -> str:
    raw_token = secrets.token_urlsafe(32)
    now = _now_iso()
    expires_at = (datetime.now() + timedelta(hours=ttl_hours)).replace(microsecond=0).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO app_sessions (
            created_at, updated_at, token_hash, user_id, expires_at, user_agent, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            now,
            _hash_secret(raw_token),
            user_id,
            expires_at,
            user_agent,
            _json_dumps({}),
        ),
    )
    conn.commit()
    conn.close()
    return raw_token


def get_user_by_session_token(token: str) -> Optional[dict]:
    if not token:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT u.*
        FROM app_sessions s
        JOIN app_users u ON u.id = s.user_id
        WHERE s.token_hash = ?
          AND s.revoked_at IS NULL
          AND s.expires_at > ?
          AND u.is_active = 1
        ORDER BY s.created_at DESC
        LIMIT 1
        """,
        (_hash_secret(token), _now_iso()),
    )
    row = cursor.fetchone()
    conn.close()
    return public_user(dict(row)) if row else None


def revoke_app_session(token: str) -> bool:
    if not token:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    now = _now_iso()
    cursor.execute(
        "UPDATE app_sessions SET revoked_at = ?, updated_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
        (now, now, _hash_secret(token)),
    )
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def save_audit_event(
    actor: Optional[dict],
    action: str,
    target_type: str = None,
    target_id: str = None,
    details: Optional[dict] = None,
) -> Optional[str]:
    """Append an audit event. Audit failures never block the business action."""
    try:
        event_id = f"AUD-{uuid.uuid4().hex[:12].upper()}"
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audit_events (
                created_at, event_id, actor_user_id, actor_username, actor_role,
                action, target_type, target_id, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now_iso(),
                event_id,
                actor.get("id") if actor else None,
                actor.get("username") if actor else None,
                actor.get("role") if actor else None,
                action,
                target_type,
                str(target_id) if target_id is not None else None,
                _json_dumps(details or {}),
            ),
        )
        conn.commit()
        conn.close()
        return event_id
    except Exception as exc:
        logging.warning(f"[AUDIT] Could not save event {action}: {exc}")
        return None


def get_audit_events(limit: int = 100) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    events = []
    for row in cursor.fetchall():
        item = dict(row)
        try:
            item["details"] = json.loads(item.get("details_json") or "{}")
        except Exception:
            item["details"] = {}
        events.append(item)
    conn.close()
    return events


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# TREATMENTS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_treatment(traitement_input: dict, agent_a_output: dict) -> int:
    """Save a treatment analysis (Agent A output) to the DB."""
    q2 = agent_a_output.get("q2_conformite", {})
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO treatments (
            id_traitement, nom_traitement, systeme, responsable,
            finalite, base_legale, donnees_sensibles,
            score_conformite, niveau_risque, nb_violations,
            input_json, output_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        traitement_input.get("id_traitement"),
        traitement_input.get("nom_traitement"),
        traitement_input.get("systeme"),
        traitement_input.get("responsable"),
        traitement_input.get("finalite"),
        str(traitement_input.get("base_legale", "")),
        int(traitement_input.get("donnees_sensibles", False)),
        q2.get("score_normalise"),
        q2.get("niveau_risque"),
        q2.get("nombre_violations"),
        json.dumps(traitement_input, ensure_ascii=False),
        json.dumps(agent_a_output, ensure_ascii=False)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_treatment_summaries(systeme: str = None, limit: int = 50) -> list:
    """Return lightweight treatment rows without the heavy JSON payloads."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT
            id, created_at, id_traitement, nom_traitement, systeme, responsable,
            finalite, base_legale, donnees_sensibles, score_conformite,
            niveau_risque, nb_violations,
            IFNULL(length(input_json), 0) AS input_json_bytes,
            IFNULL(length(output_json), 0) AS output_json_bytes
        FROM treatments
    """
    params = []
    if systeme:
        query += " WHERE systeme = ?"
        params.append(systeme)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_treatments(systeme: str = None, limit: int = 50) -> list:
    """Return list of stored treatments, optionally filtered by system."""
    conn = get_connection()
    cursor = conn.cursor()
    if systeme:
        cursor.execute(
            "SELECT * FROM treatments WHERE systeme = ? ORDER BY created_at DESC LIMIT ?",
            (systeme, limit)
        )
    else:
        cursor.execute(
            "SELECT * FROM treatments ORDER BY created_at DESC LIMIT ?", (limit,)
        )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_treatment_by_id(treatment_id: str) -> dict | None:
    """Return a single treatment by its id_traitement."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM treatments WHERE id_traitement = ? ORDER BY created_at DESC LIMIT 1",
        (treatment_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_treatment_by_row_id(row_id: int) -> dict | None:
    """Return a single treatment row by DB id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM treatments WHERE id = ? LIMIT 1",
        (row_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# DSARS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_dsar(demande_input: dict, agent_c_output: dict) -> int:
    """Save a DSAR request + qualification to the DB."""
    q5 = agent_c_output.get("q5_droits", {})
    delais = q5.get("delais", {})
    reponse = q5.get("reponse", {})
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO dsars (
            id_demande, nom_demandeur, type_droit, systeme_concerne,
            date_reception, date_limite, qualification, statut,
            jours_restants, input_json, output_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        demande_input.get("id_demande", f"DSAR-{datetime.now().strftime('%Y%m%d%H%M%S')}"),
        demande_input.get("nom_demandeur"),
        demande_input.get("type_droit"),
        demande_input.get("systeme_concerne"),
        delais.get("date_reception"),
        delais.get("date_limite_30j"),
        q5.get("qualification"),
        reponse.get("statut_reponse"),
        delais.get("jours_restants"),
        json.dumps(demande_input, ensure_ascii=False),
        json.dumps(agent_c_output, ensure_ascii=False)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_dsars(statut: str = None, limit: int = 50, include_payload: bool = False) -> list:
    """Return list of DSAR requests, optionally filtered by statut."""
    conn = get_connection()
    cursor = conn.cursor()
    fields = (
        "*"
        if include_payload else
        "id, created_at, id_demande, nom_demandeur, type_droit, systeme_concerne, "
        "date_reception, date_limite, qualification, statut, jours_restants"
    )
    if statut:
        cursor.execute(
            f"SELECT {fields} FROM dsars WHERE statut = ? ORDER BY created_at DESC LIMIT ?",
            (statut, limit)
        )
    else:
        cursor.execute(
            f"SELECT {fields} FROM dsars ORDER BY created_at DESC LIMIT ?", (limit,)
        )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def update_dsar_statut(id_demande: str, statut: str) -> bool:
    """Update the status of a DSAR (e.g. mark as 'Cloture')."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE dsars SET statut = ? WHERE id_demande = ?",
        (statut, id_demande)
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# VIOLATIONS (Art. 33.5 register)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_violation(incident_input: dict, agent_b_output: dict) -> Optional[int]:
    """Save an incident/violation to the Art. 33.5 mandatory register."""
    q6 = agent_b_output.get("q6_incidents", {})
    if not q6.get("incident_declare"):
        return None  # no incident â€” nothing to save
    notification = q6.get("notification", {})
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO violations (
            id_incident, date_detection, type_incident, description,
            nb_personnes_affectees, gravite, donnees_sensibles,
            qualification, notifier_cnil, notifier_personnes,
            statut, input_json, output_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        incident_input.get("id_incident", f"INC-{datetime.now().strftime('%Y%m%d%H%M%S')}"),
        incident_input.get("date_detection"),
        incident_input.get("type_incident"),
        incident_input.get("description"),
        incident_input.get("nombre_personnes_affectees", 0),
        incident_input.get("gravite_incident", 1),
        int(incident_input.get("donnees_sensibles_impliquees", False)),
        q6.get("qualification"),
        int(notification.get("notifier_cnil", False)),
        int(notification.get("notifier_personnes", False)),
        "Ouvert",
        json.dumps(incident_input, ensure_ascii=False),
        json.dumps(agent_b_output, ensure_ascii=False)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_violations(statut: str = None, limit: int = 50) -> list:
    """Return violations register, optionally filtered by statut."""
    conn = get_connection()
    cursor = conn.cursor()
    if statut:
        cursor.execute(
            "SELECT * FROM violations WHERE statut = ? ORDER BY created_at DESC LIMIT ?",
            (statut, limit)
        )
    else:
        cursor.execute(
            "SELECT * FROM violations ORDER BY created_at DESC LIMIT ?", (limit,)
        )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def update_violation_statut(id_incident: str, statut: str, notification_envoyee: bool = False) -> bool:
    """Update incident status and notification flag."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE violations SET statut = ?, notification_envoyee = ? WHERE id_incident = ?",
        (statut, int(notification_envoyee), id_incident)
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# DPIA DOSSIERS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_dpia(request_dict: dict, dossier_text: str, niveau_risque: str) -> int:
    """Save a generated DPIA dossier to the DB."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO dpia_dossiers (
            nom_traitement, niveau_risque, aipd_decision,
            dossier_text, input_json
        ) VALUES (?, ?, ?, ?, ?)
    """, (
        request_dict.get("traitement", {}).get("nom_traitement"),
        niveau_risque,
        request_dict.get("aipd_decision"),
        dossier_text,
        json.dumps(request_dict, ensure_ascii=False)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_dpia_dossiers(limit: int = 50) -> list:
    """Return all stored DPIA dossiers."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM dpia_dossiers ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CONSENTS (Q3)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def update_dpia_statut(dpia_id: int, statut: str) -> bool:
    """Update the validation status of a generated DPIA dossier."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE dpia_dossiers SET statut = ? WHERE id = ?",
        (statut, dpia_id)
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def save_consent(
    nom_personne: str,
    email: str,
    id_traitement: str,
    nom_traitement: str,
    finalite: str,
    date_expiration: str = None,
    preuve: str = None
) -> str:
    """Create a new active consent record. Returns the consent ID."""
    conn = get_connection()
    cursor = conn.cursor()
    safe_id = (id_traitement or "UNKNOWN").replace("/", "-")[:8]
    consent_id = f"CON-{datetime.now().strftime('%Y%m%d%H%M%S')}-{safe_id}"
    cursor.execute("""
        INSERT INTO consents (
            id_consent, nom_personne, email_personne,
            id_traitement, nom_traitement, finalite,
            statut, date_collecte, date_expiration, preuve
        ) VALUES (?, ?, ?, ?, ?, ?, 'actif', ?, ?, ?)
    """, (
        consent_id,
        nom_personne,
        email,
        id_traitement,
        nom_traitement,
        finalite,
        datetime.now().strftime("%Y-%m-%d"),
        date_expiration,
        preuve or f"Consentement collectÃ© le {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    ))
    conn.commit()
    conn.close()
    return consent_id


def withdraw_consent(consent_id: str, retire_par: str) -> bool:
    """Mark a consent as withdrawn. Immutable audit trail â€” original record kept."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE consents
        SET statut = 'retire',
            retire_par = ?,
            date_retrait = ?,
            updated_at = ?
        WHERE id_consent = ? AND statut = 'actif'
    """, (
        retire_par,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        consent_id
    ))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def get_consents(id_traitement: str = None, statut: str = None) -> list:
    """Return consents, optionally filtered by treatment or status."""
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM consents WHERE 1=1"
    params = []
    if id_traitement:
        query += " AND id_traitement = ?"
        params.append(id_traitement)
    if statut:
        query += " AND statut = ?"
        params.append(statut)
    query += " ORDER BY created_at DESC"
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_expiring_consents(days_ahead: int = 30) -> list:
    """Return consents that will expire within the next N days â€” for alerts."""
    conn = get_connection()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    future = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT * FROM consents
        WHERE statut = 'actif'
          AND date_expiration IS NOT NULL
          AND date_expiration BETWEEN ? AND ?
        ORDER BY date_expiration ASC
    """, (today, future))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CNIL NOTIFICATIONS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_cnil_notification(notification_id: str, request_dict: dict, output_dict: dict) -> int:
    """Save a generated CNIL notification dossier."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO cnil_notifications (
            notification_id, id_incident, responsable, systeme,
            notifier_autorite, notifier_personnes,
            notification_text, input_json, output_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        notification_id,
        request_dict.get("incident", {}).get("id_incident"),
        request_dict.get("traitement", {}).get("responsable"),
        request_dict.get("traitement", {}).get("systeme"),
        int(request_dict.get("notifier_autorite", True)),
        int(request_dict.get("notifier_personnes", False)),
        output_dict.get("notification_text"),
        json.dumps(request_dict, ensure_ascii=False, default=str),
        json.dumps(output_dict, ensure_ascii=False, default=str)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_cnil_notifications(limit: int = 50) -> list:
    """Return all stored CNIL notifications."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM cnil_notifications ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def update_cnil_notification_statut(notification_ref: str, statut: str) -> bool:
    """Update a CNIL notification status by DB id or notification_id."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE cnil_notifications SET statut = ? WHERE id = ?",
            (statut, int(notification_ref))
        )
    except (TypeError, ValueError):
        cursor.execute(
            "UPDATE cnil_notifications SET statut = ? WHERE notification_id = ?",
            (statut, notification_ref)
        )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# ============================================================
# RGPD REGISTER (ARTICLE 30)
# ============================================================

def save_register_entry(
    traitement_input: dict,
    register_entry: dict,
    source_analysis_id: int | None = None
) -> int:
    """Save one Article 30 register snapshot from analysis output."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO rgpd_register (
            updated_at,
            id_traitement, nom_traitement, systeme, responsable,
            finalite, base_legale,
            categories_donnees, personnes_concernees, destinataires,
            duree_conservation, mesures_securite,
            risk_level, missing_info, last_checked,
            source_analysis_id, source_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        register_entry.get("id_traitement") or traitement_input.get("id_traitement"),
        register_entry.get("nom_traitement") or traitement_input.get("nom_traitement"),
        register_entry.get("systeme") or traitement_input.get("systeme"),
        register_entry.get("responsable") or traitement_input.get("responsable"),
        register_entry.get("finalite") or traitement_input.get("finalite"),
        register_entry.get("base_legale") or str(traitement_input.get("base_legale", "")),
        json.dumps(register_entry.get("categories_donnees", []), ensure_ascii=False),
        json.dumps(register_entry.get("personnes_concernees", []), ensure_ascii=False),
        json.dumps(register_entry.get("destinataires", []), ensure_ascii=False),
        register_entry.get("duree_conservation") or traitement_input.get("duree_conservation"),
        json.dumps(register_entry.get("mesures_securite", []), ensure_ascii=False),
        register_entry.get("risk_level", "Inconnu"),
        int(register_entry.get("missing_info", False)),
        register_entry.get("last_checked") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        source_analysis_id,
        json.dumps(register_entry, ensure_ascii=False, default=str)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_register_entries(systeme: str = None, limit: int = 100) -> list:
    """List register entries, optionally filtered by system."""
    conn = get_connection()
    cursor = conn.cursor()
    if systeme:
        cursor.execute(
            "SELECT * FROM rgpd_register WHERE systeme = ? ORDER BY created_at DESC LIMIT ?",
            (systeme, limit)
        )
    else:
        cursor.execute(
            "SELECT * FROM rgpd_register ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ============================================================
# CORRECTIVE ACTIONS
# ============================================================

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_action_json(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _normalise_action_severity(severity: str | None) -> str:
    raw = (severity or "Moyen").strip().lower()
    if raw in {"g3", "critique", "critical", "high", "elevee", "eleve", "elevÃ©", "Ã©levÃ©"}:
        return "Critique" if raw in {"g3", "critique", "critical"} else "Eleve"
    if raw in {"g2", "moyen", "medium", "modere", "moderee", "modÃ©rÃ©", "modÃ©rÃ©e"}:
        return "Moyen"
    if raw in {"g1", "faible", "low", "mineur", "mineure"}:
        return "Faible"
    return "Moyen"


def _action_delay_days(severity: str) -> int:
    if severity == "Critique":
        return 1
    if severity == "Eleve":
        return 7
    if severity == "Moyen":
        return 30
    return 60


def _find_open_action(
    linked_treatment_id: str = None,
    source_regle_id: str = None,
    title: str = None
) -> Optional[int]:
    """Avoid creating duplicate open actions for the same gap."""
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT id FROM actions WHERE status != 'Cloturee'"
    params = []
    if linked_treatment_id:
        query += " AND linked_treatment_id = ?"
        params.append(linked_treatment_id)
    if source_regle_id:
        query += " AND source_regle_id = ?"
        params.append(source_regle_id)
    elif title:
        query += " AND title = ?"
        params.append(title)
    query += " ORDER BY created_at DESC LIMIT 1"
    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()
    return int(row["id"]) if row else None


def _score_action_proof(
    proof_type: str = None,
    description: str = "",
    file_name: str = None,
    file_size: int = None
) -> int:
    """Small heuristic score used to help the DPO review proof quality."""
    score = 25
    text = (description or "").lower()
    if len(text) > 40:
        score += 20
    if len(text) > 120:
        score += 15
    if file_name:
        score += 20
    if file_size and file_size > 0:
        score += 10
    if (proof_type or "").lower() in {"audit", "journal", "log", "document", "photo", "contrat"}:
        score += 10
    keywords = [
        "capture", "audit", "journal", "contrat", "procedure", "formation",
        "chiffrement", "mfa", "registre", "suppression", "anonymisation",
        "export", "signature", "rapport", "politique", "validation",
    ]
    score += min(5 * sum(1 for keyword in keywords if keyword in text), 25)
    return max(0, min(score, 100))


def _enrich_action_row(row: dict) -> dict:
    row = dict(row)
    row["metadata"] = _parse_action_json(row.get("metadata_json"))
    proofs = get_action_proofs(action_id=row["id"], limit=50)
    row["proofs"] = proofs
    row["proofs_count"] = len(proofs)
    row["accepted_proofs_count"] = sum(1 for proof in proofs if proof.get("verification_status") == "Acceptee")
    row["pending_proofs_count"] = sum(1 for proof in proofs if proof.get("verification_status") == "A verifier")
    row["rejected_proofs_count"] = sum(1 for proof in proofs if proof.get("verification_status") == "Rejetee")
    row["has_accepted_proof"] = row["accepted_proofs_count"] > 0
    row["can_close"] = row.get("status") != "Cloturee" and row["has_accepted_proof"]
    row["is_overdue"] = False
    due_date = row.get("due_date")
    if due_date and row.get("status") != "Cloturee":
        try:
            row["is_overdue"] = datetime.strptime(due_date, "%Y-%m-%d").date() < datetime.now().date()
        except Exception:
            row["is_overdue"] = False
    return row


def save_action(
    title: str,
    description: str = "",
    severity: str = "Moyen",
    owner: str = "DPO",
    due_date: str = None,
    linked_treatment_id: str = None,
    linked_register_id: int = None,
    source_regle_id: str = None,
    recommendation: str = None,
    metadata: dict = None,
    status: str = "A faire"
) -> int:
    """Create one corrective action."""
    severity = _normalise_action_severity(severity)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO actions (
            updated_at, title, description, severity, owner, due_date, status,
            linked_treatment_id, linked_register_id, source_regle_id,
            recommendation, metadata_json, proof_status, proof_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Aucune preuve', NULL)
    """, (
        _now(),
        title,
        description,
        severity,
        owner,
        due_date,
        status,
        linked_treatment_id,
        linked_register_id,
        source_regle_id,
        recommendation,
        json.dumps(metadata or {}, ensure_ascii=False, default=str)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def create_actions_from_gaps(
    gaps: list,
    linked_treatment_id: str = None,
    linked_register_id: int = None,
    owner: str = "DPO"
) -> list[int]:
    """Create multiple actions from Q2 structured gaps, without duplicating open ones."""
    created_ids = []
    for gap in gaps or []:
        sev = _normalise_action_severity(gap.get("severity") or gap.get("gravite"))
        delay_days = _action_delay_days(sev)
        due = (datetime.now() + timedelta(days=delay_days)).strftime("%Y-%m-%d")
        title = gap.get("title") or gap.get("message") or gap.get("regle") or "Action corrective RGPD"
        description = gap.get("message") or gap.get("description") or ""
        source_regle_id = gap.get("id_regle") or gap.get("rule_id") or gap.get("code")
        existing_id = _find_open_action(
            linked_treatment_id=linked_treatment_id,
            source_regle_id=source_regle_id,
            title=title
        )
        if existing_id:
            created_ids.append(existing_id)
            continue
        action_id = save_action(
            title=title,
            description=description,
            severity=sev,
            owner=owner,
            due_date=due,
            linked_treatment_id=linked_treatment_id,
            linked_register_id=linked_register_id,
            source_regle_id=source_regle_id,
            recommendation=gap.get("recommendation"),
            metadata=gap
        )
        created_ids.append(action_id)
    return created_ids


def get_action_proofs(action_id: int = None, status: str = None, limit: int = 100) -> list:
    """Return proof records attached to corrective actions."""
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM action_proofs WHERE 1=1"
    params = []
    if action_id is not None:
        query += " AND action_id = ?"
        params.append(action_id)
    if status:
        query += " AND verification_status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    for row in rows:
        row["metadata"] = _parse_action_json(row.get("metadata_json"))
    return rows


def get_actions(
    statut: str = None,
    severity: str = None,
    limit: int = 100
) -> list:
    """List corrective actions with optional filters."""
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM actions WHERE 1=1"
    params = []
    if statut:
        query += " AND status = ?"
        params.append(statut)
    if severity:
        query += " AND severity = ?"
        params.append(severity)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [_enrich_action_row(dict(r)) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_action_by_id(action_id: int) -> Optional[dict]:
    """Return one enriched corrective action."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM actions WHERE id = ?", (action_id,))
    row = cursor.fetchone()
    conn.close()
    return _enrich_action_row(dict(row)) if row else None


def save_action_proof(
    action_id: int,
    proof_type: str = "document",
    title: str = None,
    description: str = None,
    file_name: str = None,
    file_path: str = None,
    mime_type: str = None,
    file_size: int = None,
    checksum: str = None,
    submitted_by: str = "Y. Benjemaa - DPO",
    metadata: dict = None
) -> dict:
    """Attach a proof to an action and put the action in review state."""
    action = get_action_by_id(action_id)
    if not action:
        raise ValueError("Action not found.")
    proof_id = f"ACT-PROOF-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    score = _score_action_proof(
        proof_type=proof_type,
        description=description or "",
        file_name=file_name,
        file_size=file_size
    )
    now = _now()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO action_proofs (
            updated_at, proof_id, action_id, proof_type, title, description,
            file_name, file_path, mime_type, file_size, checksum, submitted_by,
            verification_status, verification_score, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'A verifier', ?, ?)
    """, (
        now,
        proof_id,
        action_id,
        proof_type or "document",
        title or f"Preuve action #{action_id}",
        description,
        file_name,
        file_path,
        mime_type,
        file_size,
        checksum,
        submitted_by,
        score,
        json.dumps(metadata or {}, ensure_ascii=False, default=str)
    ))
    cursor.execute("""
        UPDATE actions
        SET status = CASE WHEN status = 'A faire' THEN 'En cours' ELSE status END,
            proof_status = 'Preuve a verifier',
            proof_summary = ?,
            updated_at = ?
        WHERE id = ?
    """, (
        f"Preuve {proof_id} deposee - score {score}/100",
        now,
        action_id
    ))
    conn.commit()
    conn.close()
    proof = get_action_proofs(action_id=action_id, limit=1)[0]
    proof["action"] = get_action_by_id(action_id)
    return proof


def validate_action_proof(
    proof_id: str,
    decision: str,
    validator: str = "Y. Benjemaa - DPO",
    notes: str = None
) -> dict:
    """Accept or reject one action proof and save the DPO evidence decision."""
    normalized = (decision or "").strip().lower()
    accepted = normalized in {"valide", "valid", "accepte", "acceptee", "accepted", "approve", "approved"}
    status = "Acceptee" if accepted else "Rejetee"
    now = _now()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM action_proofs WHERE proof_id = ?",
        (proof_id,)
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError("Proof not found.")
    proof = dict(row)
    cursor.execute("""
        UPDATE action_proofs
        SET verification_status = ?, verification_notes = ?, verified_by = ?,
            verified_at = ?, updated_at = ?
        WHERE proof_id = ?
    """, (status, notes, validator, now, now, proof_id))
    summary = (
        f"Preuve {proof_id} acceptee par {validator}"
        if accepted else
        f"Preuve {proof_id} rejetee par {validator}"
    )
    cursor.execute("""
        UPDATE actions
        SET proof_status = ?, proof_summary = ?, updated_at = ?
        WHERE id = ?
    """, ("Preuve acceptee" if accepted else "Preuve rejetee", summary, now, proof["action_id"]))
    conn.commit()
    conn.close()

    updated_proof = get_action_proofs(action_id=proof["action_id"], limit=50)
    action = get_action_by_id(proof["action_id"])
    validation_id = save_dpo_validation(
        target_type="action",
        decision="valide" if accepted else "correction_requise",
        target_id=str(proof["action_id"]),
        target_label=action.get("title") if action else None,
        validator=validator,
        role="DPO",
        justification=notes or summary,
        source_system="Plateforme RGPD",
        source_module="Actions correctives",
        evidence={"proof_id": proof_id, "proof_status": status, "action": action},
        metadata={"origin": "action_proof_validation", "proofs": updated_proof}
    )
    result = next((p for p in updated_proof if p.get("proof_id") == proof_id), {})
    result["action"] = action
    result["dpo_validation_id"] = validation_id
    return result


def close_action(
    action_id: int,
    validator: str = "Y. Benjemaa - DPO",
    justification: str = None
) -> dict:
    """Close an action only when at least one proof has been accepted by the DPO."""
    action = get_action_by_id(action_id)
    if not action:
        raise ValueError("Action not found.")
    accepted_proofs = [p for p in action.get("proofs", []) if p.get("verification_status") == "Acceptee"]
    if not accepted_proofs:
        raise ValueError("Action cannot be closed without an accepted proof.")
    now = _now()
    summary = justification or f"Action cloturee avec {len(accepted_proofs)} preuve(s) acceptee(s)."
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE actions
        SET status = 'Cloturee', closed_at = ?, proof_status = 'Preuve acceptee',
            proof_summary = ?, updated_at = ?
        WHERE id = ?
    """, (now, summary, now, action_id))
    conn.commit()
    conn.close()
    closed = get_action_by_id(action_id)
    validation_id = save_dpo_validation(
        target_type="action",
        decision="cloture",
        target_id=str(action_id),
        target_label=closed.get("title") if closed else None,
        validator=validator,
        role="DPO",
        justification=summary,
        source_system="Plateforme RGPD",
        source_module="Actions correctives",
        evidence={"action": closed, "accepted_proofs": accepted_proofs},
        metadata={"origin": "action_closure"}
    )
    closed["dpo_validation_id"] = validation_id
    return closed


def update_action_status(action_id: int, status: str) -> bool:
    """Update status for a corrective action."""
    if status == "Cloturee":
        close_action(action_id)
        return True
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE actions SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), action_id)
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# ============================================================
# DPO VALIDATIONS (human decision audit trail)
# ============================================================

def save_dpo_validation(
    target_type: str,
    decision: str,
    target_id: str = None,
    target_label: str = None,
    validator: str = "Y. Benjemaa - DPO",
    role: str = "DPO",
    justification: str = None,
    source_system: str = None,
    source_module: str = None,
    evidence: dict = None,
    metadata: dict = None
) -> str:
    """Save a final DPO validation/rejection decision."""
    validation_id = f"DPO-VAL-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO dpo_validations (
            updated_at, validation_id, target_type, target_id, target_label,
            decision, validator, role, justification, source_system, source_module,
            evidence_json, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now,
        validation_id,
        target_type,
        target_id,
        target_label,
        decision,
        validator,
        role,
        justification,
        source_system,
        source_module,
        json.dumps(evidence or {}, ensure_ascii=False, default=str),
        json.dumps(metadata or {}, ensure_ascii=False, default=str)
    ))
    conn.commit()
    conn.close()
    try:
        create_dpo_feedback_memory_from_validation(
            validation_id=validation_id,
            target_type=target_type,
            target_id=target_id,
            target_label=target_label,
            decision=decision,
            validator=validator,
            role=role,
            justification=justification,
            source_system=source_system,
            source_module=source_module,
            evidence=evidence or {},
            metadata=metadata or {}
        )
    except Exception as exc:
        logging.warning("[DPO MEMORY] Could not create memory for %s: %s", validation_id, exc)
    return validation_id


def get_dpo_validations(
    target_type: str = None,
    decision: str = None,
    limit: int = 100
) -> list:
    """List DPO validation decisions with optional filters."""
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM dpo_validations WHERE 1=1"
    params = []
    if target_type:
        query += " AND target_type = ?"
        params.append(target_type)
    if decision:
        query += " AND decision = ?"
        params.append(decision)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [_enrich_dpo_validation_row(dict(r)) for r in cursor.fetchall()]
    conn.close()
    return rows


def _json_or_default(raw: Any, default: Any) -> Any:
    """Safely decode JSON stored in audit tables."""
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def _enrich_dpo_validation_row(row: dict) -> dict:
    """Add parsed proof fields to one DPO validation row."""
    row["evidence"] = _json_or_default(row.get("evidence_json"), {})
    row["metadata"] = _json_or_default(row.get("metadata_json"), {})
    row["proof_reference"] = row.get("validation_id") or f"DPO-PROOF-{row.get('id')}"
    row["proof_status"] = "opposable"
    return row


def _normalise_memory_text(value: Any) -> str:
    """Create a stable, searchable text fragment for DPO memory matching."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9_ -]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _find_first_memory_value(data: Any, keys: set[str]) -> str | None:
    """Find a meaningful validated value inside nested evidence/metadata."""
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in keys and value not in (None, "", [], {}):
                return str(value)
        for value in data.values():
            found = _find_first_memory_value(value, keys)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_first_memory_value(item, keys)
            if found:
                return found
    return None


def _collect_memory_values(data: Any, keys: set[str], limit: int = 8) -> list[str]:
    """Collect compact contextual values from nested proof payloads."""
    values = []

    def walk(node):
        if len(values) >= limit:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                key_norm = str(key).lower()
                if key_norm in keys and value not in (None, "", [], {}):
                    text = str(value)
                    if text not in values:
                        values.append(text)
                    if len(values) >= limit:
                        return
            for value in node.values():
                walk(value)
                if len(values) >= limit:
                    return
        elif isinstance(node, list):
            for item in node:
                walk(item)
                if len(values) >= limit:
                    return

    walk(data)
    return values


def _memory_tags(*values: Any) -> list:
    """Return compact tags that make DPO memory easy to filter in the UI."""
    tags = []
    for value in values:
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple, set)):
            for item in value:
                if item not in (None, "", [], {}):
                    tags.append(str(item).strip())
        else:
            tags.append(str(value).strip())
    unique = []
    seen = set()
    for tag in tags:
        key = _normalise_memory_text(tag)
        if key and key not in seen:
            seen.add(key)
            unique.append(tag)
    return unique[:12]


def _build_dpo_memory_signature(
    target_type: str = None,
    target_id: str = None,
    target_label: str = None,
    source_system: str = None,
    source_module: str = None,
    final_value: str = None,
    evidence: dict = None,
    metadata: dict = None
) -> str:
    """Build the searchable signature used for similar-decision retrieval."""
    fragments = [
        target_type,
        target_id,
        target_label,
        source_system,
        source_module,
        final_value,
        (metadata or {}).get("origin") if isinstance(metadata, dict) else None,
        (metadata or {}).get("finalite") if isinstance(metadata, dict) else None,
        (evidence or {}).get("source") if isinstance(evidence, dict) else None,
    ]
    context_keys = {
        "type_droit", "droit_exerce", "qualification", "motif", "statut", "status",
        "message_reponse", "type_incident", "qualification_incident", "description",
        "notification_autorite_requise", "notification_personnes_requise",
        "aipd_requise", "aipd_decision", "decision", "finalite", "base_legale",
        "base_legale_confirmee", "niveau", "niveau_risque", "nom_demandeur",
        "donnees_concernees", "systeme_concerne", "prediction_ml", "label",
        "label_display", "field", "field_name", "field_label", "corrected_label",
    }
    if isinstance(evidence, dict):
        fragments.extend(_collect_memory_values(evidence, context_keys))
    if isinstance(metadata, dict):
        fragments.extend(_collect_memory_values(metadata, context_keys))
    return " | ".join([_normalise_memory_text(fragment) for fragment in fragments if fragment])


def _enrich_dpo_memory_row(row: dict) -> dict:
    """Parse JSON payloads stored with one reusable DPO memory."""
    row["agent_suggestion"] = _json_or_default(row.get("agent_suggestion_json"), {})
    row["dpo_decision"] = _json_or_default(row.get("dpo_decision_json"), {})
    row["tags"] = _json_or_default(row.get("tags_json"), [])
    row["metadata"] = _json_or_default(row.get("metadata_json"), {})
    row["reusable"] = bool(row.get("reusable", 1))
    return row


def save_dpo_feedback_memory(
    validation_id: str,
    target_type: str,
    target_id: str = None,
    target_label: str = None,
    decision: str = None,
    final_value: str = None,
    source_system: str = None,
    source_module: str = None,
    context_signature: str = None,
    agent_suggestion: dict = None,
    dpo_decision: dict = None,
    justification: str = None,
    reusable: bool = True,
    confidence: float = 0.75,
    tags: list = None,
    metadata: dict = None
) -> str:
    """Persist one reusable DPO decision memory."""
    memory_id = f"DPO-MEM-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO dpo_feedback_memory (
            updated_at, memory_id, validation_id, target_type, target_id, target_label,
            decision, final_value, source_system, source_module, context_signature,
            agent_suggestion_json, dpo_decision_json, justification, reusable, confidence,
            tags_json, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now,
        memory_id,
        validation_id,
        target_type,
        target_id,
        target_label,
        decision,
        final_value,
        source_system,
        source_module,
        context_signature,
        json.dumps(agent_suggestion or {}, ensure_ascii=False, default=str),
        json.dumps(dpo_decision or {}, ensure_ascii=False, default=str),
        justification,
        1 if reusable else 0,
        confidence,
        json.dumps(tags or [], ensure_ascii=False, default=str),
        json.dumps(metadata or {}, ensure_ascii=False, default=str)
    ))
    conn.commit()
    conn.close()
    return memory_id


def create_dpo_feedback_memory_from_validation(
    validation_id: str,
    target_type: str,
    decision: str,
    target_id: str = None,
    target_label: str = None,
    validator: str = None,
    role: str = None,
    justification: str = None,
    source_system: str = None,
    source_module: str = None,
    evidence: dict = None,
    metadata: dict = None
) -> str:
    """Turn a final DPO decision into safe reusable feedback memory."""
    value_keys = {
        "final_value",
        "base_legale",
        "base_legale_confirmee",
        "legal_basis",
        "base_recommandee",
        "aipd_decision",
        "qualification",
        "statut",
        "status",
        "decision_finale",
        "type_droit",
        "droit_exerce",
        "notification_autorite_requise",
        "notification_personnes_requise",
        "notification_required",
        "notifier_cnil",
        "notifier_autorite",
        "notifier_personnes",
        "type_incident",
        "qualification_incident",
        "niveau_incident",
        "aipd_requise",
        "decision_notification",
        "label",
        "label_display",
        "field_label",
        "corrected_label",
    }
    final_value = (
        _find_first_memory_value(metadata or {}, value_keys)
        or _find_first_memory_value(evidence or {}, value_keys)
        or decision
    )
    type_keys = {
        "type_droit",
        "droit_exerce",
        "type_incident",
        "qualification_incident",
        "aipd_decision",
        "decision_notification",
        "label",
        "label_display",
        "field_label",
        "corrected_label",
    }
    memory_type = (
        _find_first_memory_value(metadata or {}, type_keys)
        or _find_first_memory_value(evidence or {}, type_keys)
    )
    agent_suggestion = {
        "target_type": target_type,
        "target_id": target_id,
        "target_label": target_label,
        "source_system": source_system,
        "source_module": source_module,
        "evidence": evidence or {},
        "metadata": metadata or {},
    }
    dpo_decision = {
        "decision": decision,
        "validator": validator,
        "role": role,
        "justification": justification,
    }
    context_signature = _build_dpo_memory_signature(
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        source_system=source_system,
        source_module=source_module,
        final_value=final_value,
        evidence=evidence or {},
        metadata=metadata or {},
    )
    tags = _memory_tags(target_type, source_system, source_module, decision, final_value, memory_type)
    return save_dpo_feedback_memory(
        validation_id=validation_id,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        decision=decision,
        final_value=final_value,
        source_system=source_system,
        source_module=source_module,
        context_signature=context_signature,
        agent_suggestion=agent_suggestion,
        dpo_decision=dpo_decision,
        justification=justification,
        reusable=True,
        confidence=0.85 if decision in ("valide", "cloture") else 0.7,
        tags=tags,
        metadata={"origin": "dpo_validation", "validation_id": validation_id, **(metadata or {})},
    )


def get_dpo_feedback_memory(
    target_type: str = None,
    source_system: str = None,
    source_module: str = None,
    reusable_only: bool = True,
    limit: int = 100
) -> list:
    """Return reusable DPO feedback examples with optional filters."""
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM dpo_feedback_memory WHERE 1=1"
    params = []
    if reusable_only:
        query += " AND reusable = 1"
    if target_type:
        query += " AND target_type = ?"
        params.append(target_type)
    if source_system:
        query += " AND LOWER(COALESCE(source_system, '')) = LOWER(?)"
        params.append(source_system)
    if source_module:
        query += " AND LOWER(COALESCE(source_module, '')) = LOWER(?)"
        params.append(source_module)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [_enrich_dpo_memory_row(dict(r)) for r in cursor.fetchall()]
    conn.close()
    return rows


def find_similar_dpo_memory(
    target_type: str = None,
    source_system: str = None,
    source_module: str = None,
    query: str = None,
    limit: int = 5
) -> list:
    """Find similar DPO decisions using lightweight semantic-style matching."""
    query_text = _normalise_memory_text(query or "")
    query_terms = set(query_text.split()) if query_text else set()
    candidates = get_dpo_feedback_memory(reusable_only=True, limit=500)
    scored = []
    for row in candidates:
        score = 0
        if target_type and row.get("target_type") == target_type:
            score += 4
        if source_system and str(row.get("source_system") or "").lower() == source_system.lower():
            score += 3
        if source_module and str(row.get("source_module") or "").lower() == source_module.lower():
            score += 2
        haystack = _normalise_memory_text(" ".join([
            row.get("target_label") or "",
            row.get("target_id") or "",
            row.get("final_value") or "",
            row.get("justification") or "",
            row.get("context_signature") or "",
            " ".join(row.get("tags") or []),
        ]))
        if query_terms:
            score += min(8, len(query_terms.intersection(set(haystack.split()))))
        if score > 0:
            item = dict(row)
            item["match_score"] = score
            scored.append(item)
    scored.sort(key=lambda item: (item.get("match_score", 0), item.get("created_at") or ""), reverse=True)
    return scored[:limit]


def _row_validation_candidates(row: dict) -> set[str]:
    """Return stable identifiers that can link a register row to DPO proofs."""
    candidates = set()
    for key in ("id_traitement", "inventory_key", "nom_traitement", "target_id", "target_label"):
        value = row.get(key)
        if value is not None and str(value).strip():
            candidates.add(str(value).strip())
    if row.get("id") is not None:
        candidates.add(str(row.get("id")))
    return candidates


def attach_latest_dpo_validations(
    rows: list,
    target_types: list[str] | None = None,
    limit: int = 1000
) -> list:
    """
    Attach the latest human DPO decision to register/inventory/treatment rows.

    This keeps the Article 30 register dynamic without duplicating proof data:
    the decision stays in dpo_validations, while views expose the current status.
    """
    if not rows:
        return rows

    wanted_types = set(target_types or ["treatment", "legal_basis"])
    validations = [
        v for v in get_dpo_validations(limit=limit)
        if v.get("target_type") in wanted_types
    ]

    for row in rows:
        candidates = _row_validation_candidates(row)
        row_system = str(row.get("systeme") or "").strip().lower()
        latest = None
        for validation in validations:
            target_id = str(validation.get("target_id") or "").strip()
            target_label = str(validation.get("target_label") or "").strip()
            validation_system = str(validation.get("source_system") or "").strip().lower()
            direct_match = target_id in candidates
            label_match = target_label in candidates and (
                not validation_system or not row_system or validation_system == row_system
            )
            if direct_match or label_match:
                latest = validation
                break
        row["dpo_validation"] = latest
        row["dpo_decision"] = latest.get("decision") if latest else None
        row["dpo_proof_reference"] = latest.get("proof_reference") if latest else None
        row["dpo_validated_at"] = latest.get("created_at") if latest else None
        row["dpo_validator"] = latest.get("validator") if latest else None
    return rows


def get_dpo_proof_history(limit: int = 100) -> list:
    """Return a DPO proof ledger ready for audit/review screens."""
    proofs = []
    for row in get_dpo_validations(limit=limit):
        proofs.append({
            "proof_reference": row.get("proof_reference"),
            "created_at": row.get("created_at"),
            "target_type": row.get("target_type"),
            "target_id": row.get("target_id"),
            "target_label": row.get("target_label"),
            "decision": row.get("decision"),
            "validator": row.get("validator"),
            "role": row.get("role"),
            "justification": row.get("justification"),
            "source_system": row.get("source_system"),
            "source_module": row.get("source_module"),
            "evidence": row.get("evidence", {}),
            "metadata": row.get("metadata", {}),
            "proof_status": row.get("proof_status", "opposable"),
        })
    return proofs


# ============================================================
# UNSTRUCTURED SCANS (Q1 evidence)
# ============================================================

def save_unstructured_scan(
    result: dict,
    linked_treatment_id: str = None,
    systeme: str = None,
    module: str = None,
    source_analysis_id: int = None
) -> int:
    """Save one unstructured scan result as Q1 evidence."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO unstructured_scans (
            filename, file_type, extraction_method, nb_findings, criticite_globale,
            linked_treatment_id, systeme, module, source_analysis_id,
            rgpd_impact_json, findings_json, result_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result.get("filename"),
        result.get("file_type"),
        result.get("extraction_method"),
        result.get("nb_findings", 0),
        result.get("criticite_globale"),
        linked_treatment_id,
        systeme,
        module,
        source_analysis_id,
        json.dumps(result.get("rgpd_impact", {}), ensure_ascii=False, default=str),
        json.dumps(result.get("findings", []), ensure_ascii=False, default=str),
        json.dumps(result, ensure_ascii=False, default=str)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_unstructured_scans(
    linked_treatment_id: str = None,
    systeme: str = None,
    module: str = None,
    limit: int = 100,
    lightweight: bool = False,
) -> list:
    """List stored unstructured scans with optional filters."""
    conn = get_connection()
    cursor = conn.cursor()
    fields = (
        "id, created_at, filename, file_type, linked_treatment_id, systeme, module, nb_findings, criticite_globale"
        if lightweight else
        "*"
    )
    query = f"SELECT {fields} FROM unstructured_scans WHERE 1=1"
    params = []
    if linked_treatment_id:
        query += " AND linked_treatment_id = ?"
        params.append(linked_treatment_id)
    if systeme:
        query += " AND systeme = ?"
        params.append(systeme)
    if module:
        query += " AND module = ?"
        params.append(module)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ============================================================
# CENTRAL RGPD INVENTORY
# ============================================================

def save_inventory_snapshot(
    traitement_input: dict,
    agent_a_output: dict,
    register_id: int = None,
    source_analysis_id: int = None
) -> int | None:
    """Upsert one central RGPD inventory snapshot and refresh its fields."""
    cartographie = agent_a_output.get("q1_cartographie", {})
    if not cartographie:
        return None

    intelligence = agent_a_output.get("intelligence", {})
    q1_register = agent_a_output.get("q1_register", {})
    q2 = agent_a_output.get("q2_conformite", {})
    module = intelligence.get("qalitas_module") or traitement_input.get("qalitas_module")
    systeme = cartographie.get("systeme") or traitement_input.get("systeme")
    id_traitement = q1_register.get("id_traitement") or cartographie.get("id_traitement") or traitement_input.get("id_traitement")
    inventory_key = f"{systeme or 'UNKNOWN'}::{module or 'manual'}::{id_traitement or cartographie.get('nom_traitement') or 'unknown'}"

    unstructured = cartographie.get("donnees_non_structurees", {}) or {}
    snapshot_json = {
        "traitement_input": traitement_input,
        "agent_a_output": agent_a_output,
    }

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM inventory_treatments WHERE inventory_key = ?", (inventory_key,))
    existing = cursor.fetchone()

    values = (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        inventory_key,
        id_traitement,
        q1_register.get("nom_traitement") or cartographie.get("nom_traitement") or traitement_input.get("nom_traitement"),
        systeme,
        module,
        q1_register.get("responsable") or cartographie.get("responsable") or traitement_input.get("responsable"),
        q1_register.get("finalite") or traitement_input.get("finalite"),
        q1_register.get("base_legale") or str(traitement_input.get("base_legale", "")),
        json.dumps(q1_register.get("personnes_concernees", cartographie.get("personnes_concernees", [])), ensure_ascii=False),
        json.dumps(q1_register.get("categories_donnees", cartographie.get("categories_donnees", [])), ensure_ascii=False),
        q1_register.get("duree_conservation") or cartographie.get("duree_conservation"),
        q2.get("niveau_risque"),
        int(unstructured.get("fichiers_scannes", 0)),
        sum(item.get("nb_findings", 0) for item in unstructured.get("details", [])),
        source_analysis_id,
        register_id,
        json.dumps(snapshot_json, ensure_ascii=False, default=str),
    )

    if existing:
        inventory_treatment_id = existing["id"]
        cursor.execute("""
            UPDATE inventory_treatments
            SET updated_at = ?, id_traitement = ?, nom_traitement = ?, systeme = ?, module = ?,
                responsable = ?, finalite = ?, base_legale = ?, personnes_concernees = ?,
                categories_donnees = ?, duree_conservation = ?, risk_level = ?,
                unstructured_files_count = ?, unstructured_findings_count = ?,
                source_analysis_id = ?, register_id = ?, snapshot_json = ?
            WHERE inventory_key = ?
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            id_traitement,
            q1_register.get("nom_traitement") or cartographie.get("nom_traitement") or traitement_input.get("nom_traitement"),
            systeme,
            module,
            q1_register.get("responsable") or cartographie.get("responsable") or traitement_input.get("responsable"),
            q1_register.get("finalite") or traitement_input.get("finalite"),
            q1_register.get("base_legale") or str(traitement_input.get("base_legale", "")),
            json.dumps(q1_register.get("personnes_concernees", cartographie.get("personnes_concernees", [])), ensure_ascii=False),
            json.dumps(q1_register.get("categories_donnees", cartographie.get("categories_donnees", [])), ensure_ascii=False),
            q1_register.get("duree_conservation") or cartographie.get("duree_conservation"),
            q2.get("niveau_risque"),
            int(unstructured.get("fichiers_scannes", 0)),
            sum(item.get("nb_findings", 0) for item in unstructured.get("details", [])),
            source_analysis_id,
            register_id,
            json.dumps(snapshot_json, ensure_ascii=False, default=str),
            inventory_key
        ))
    else:
        cursor.execute("""
            INSERT INTO inventory_treatments (
                updated_at, inventory_key, id_traitement, nom_traitement, systeme, module,
                responsable, finalite, base_legale, personnes_concernees, categories_donnees,
                duree_conservation, risk_level, unstructured_files_count, unstructured_findings_count,
                source_analysis_id, register_id, snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, values)
        inventory_treatment_id = cursor.lastrowid

    _refresh_inventory_fields(
        cursor=cursor,
        inventory_treatment_id=inventory_treatment_id,
        module=module,
        cartographie=cartographie,
        intelligence=intelligence,
    )
    _refresh_inventory_flows(
        cursor=cursor,
        inventory_treatment_id=inventory_treatment_id,
        cartographie=cartographie,
    )
    _refresh_inventory_alerts(
        cursor=cursor,
        inventory_treatment_id=inventory_treatment_id,
        cartographie=cartographie,
    )

    conn.commit()
    conn.close()
    return inventory_treatment_id


def _refresh_inventory_fields(
    cursor,
    inventory_treatment_id: int,
    module: str,
    cartographie: dict,
    intelligence: dict
):
    cursor.execute("DELETE FROM inventory_fields WHERE inventory_treatment_id = ?", (inventory_treatment_id,))

    sensitive_fields = set((intelligence.get("qalitas_detected_fields") or {}).get("sensitive_fields", []) or [])
    personal_fields = set((intelligence.get("qalitas_detected_fields") or {}).get("personal_fields", []) or [])

    for field in cartographie.get("classification_donnees", []) or []:
        field_name = field.get("donnee")
        if not field_name:
            continue
        is_sensitive = int(field_name in sensitive_fields or field.get("type") == "sensible")
        metadata = {
            "detected_in_personal_fields": field_name in personal_fields,
            "detected_in_sensitive_fields": field_name in sensitive_fields,
        }
        cursor.execute("""
            INSERT INTO inventory_fields (
                updated_at, inventory_treatment_id, field_name, source_kind, data_type,
                criticite, is_sensitive, origin_module, evidence_count, metadata_json
            ) VALUES (?, ?, ?, 'structured', ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            inventory_treatment_id,
            field_name,
            field.get("type"),
            field.get("criticite"),
            is_sensitive,
            module,
            1,
            json.dumps(metadata, ensure_ascii=False, default=str)
        ))

    unstructured = cartographie.get("donnees_non_structurees", {}) or {}
    evidence_count = int(unstructured.get("fichiers_avec_donnees_personnelles", 0))
    for pattern in unstructured.get("types_detectes", []) or []:
        cursor.execute("""
            INSERT INTO inventory_fields (
                updated_at, inventory_treatment_id, field_name, source_kind, data_type,
                criticite, is_sensitive, origin_module, evidence_count, metadata_json
            ) VALUES (?, ?, ?, 'unstructured', ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            inventory_treatment_id,
            pattern,
            "sensible" if pattern in {"nss", "medical_info", "gps_coords"} else "personnelle",
            unstructured.get("criticite_globale", "faible"),
            int(pattern in {"nss", "medical_info", "gps_coords"}),
            module,
            evidence_count,
            json.dumps({"from_unstructured_scan": True}, ensure_ascii=False)
        ))


def _refresh_inventory_flows(
    cursor,
    inventory_treatment_id: int,
    cartographie: dict
):
    cursor.execute("DELETE FROM inventory_flows WHERE inventory_treatment_id = ?", (inventory_treatment_id,))
    for flow in cartographie.get("flux_donnees", []) or []:
        cursor.execute("""
            INSERT INTO inventory_flows (
                updated_at, inventory_treatment_id, etape, source, cible, flux_type,
                criticite, donnees_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            inventory_treatment_id,
            flow.get("etape"),
            flow.get("source"),
            flow.get("cible"),
            flow.get("flux_type"),
            flow.get("criticite"),
            json.dumps(flow.get("donnees", []), ensure_ascii=False, default=str),
            json.dumps(
                {
                    "description": flow.get("description"),
                    "module": flow.get("module"),
                    "outil": flow.get("outil"),
                },
                ensure_ascii=False,
                default=str
            )
        ))


def _refresh_inventory_alerts(
    cursor,
    inventory_treatment_id: int,
    cartographie: dict
):
    cursor.execute("DELETE FROM inventory_alerts WHERE inventory_treatment_id = ?", (inventory_treatment_id,))
    for alert in cartographie.get("alertes_q1", []) or []:
        cursor.execute("""
            INSERT INTO inventory_alerts (
                updated_at, inventory_treatment_id, alert_code, titre, message,
                severity, statut, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            inventory_treatment_id,
            alert.get("code"),
            alert.get("titre"),
            alert.get("message"),
            alert.get("severity"),
            alert.get("statut", "ouverte"),
            json.dumps(alert.get("metadata", {}), ensure_ascii=False, default=str)
        ))


def get_inventory_treatments(systeme: str = None, module: str = None, limit: int = 100, lightweight: bool = False) -> list:
    """List central RGPD inventory treatment snapshots."""
    conn = get_connection()
    cursor = conn.cursor()
    fields = (
        "id, created_at, updated_at, inventory_key, id_traitement, nom_traitement, systeme, module, "
        "responsable, finalite, base_legale, risk_level, register_id, source_analysis_id"
        if lightweight else
        "*"
    )
    query = f"SELECT {fields} FROM inventory_treatments WHERE 1=1"
    params = []
    if systeme:
        query += " AND systeme = ?"
        params.append(systeme)
    if module:
        query += " AND module = ?"
        params.append(module)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_inventory_fields(inventory_treatment_id: int = None, limit: int = 500, lightweight: bool = False) -> list:
    """List inventory fields, optionally filtered by treatment snapshot id."""
    conn = get_connection()
    cursor = conn.cursor()
    fields = (
        "id, updated_at, inventory_treatment_id, field_name, source_kind, data_type, criticite, is_sensitive, origin_module"
        if lightweight else
        "*"
    )
    query = f"SELECT {fields} FROM inventory_fields WHERE 1=1"
    params = []
    if inventory_treatment_id:
        query += " AND inventory_treatment_id = ?"
        params.append(inventory_treatment_id)
    query += " ORDER BY updated_at DESC, field_name ASC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_inventory_flows(inventory_treatment_id: int = None, limit: int = 500) -> list:
    """List stored Q1 flow mappings."""
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM inventory_flows WHERE 1=1"
    params = []
    if inventory_treatment_id:
        query += " AND inventory_treatment_id = ?"
        params.append(inventory_treatment_id)
    query += " ORDER BY updated_at DESC, id ASC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_inventory_alerts(
    inventory_treatment_id: int = None,
    severity: str = None,
    limit: int = 500
) -> list:
    """List stored Q1 alerts."""
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM inventory_alerts WHERE 1=1"
    params = []
    if inventory_treatment_id:
        query += " AND inventory_treatment_id = ?"
        params.append(inventory_treatment_id)
    if severity:
        query += " AND severity = ?"
        params.append(severity)
    query += " ORDER BY updated_at DESC, id ASC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ============================================================
# DSAR EXECUTIONS
# ============================================================

def _safe_json_load(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _replace_text_values(text: str, replacements: dict[str, str]) -> tuple[str, int]:
    if not isinstance(text, str) or not text:
        return text, 0
    updated = text
    changes = 0
    for old, new in replacements.items():
        if not old:
            continue
        occurrences = updated.count(old)
        if occurrences:
            updated = updated.replace(old, new)
            changes += occurrences
    return updated, changes


def _transform_payload(value, field_updates: dict, term_replacements: dict[str, str]) -> tuple[object, int]:
    changes = 0

    if isinstance(value, dict):
        transformed = {}
        for key, item in value.items():
            if key in field_updates:
                transformed[key] = field_updates[key]
                if item != field_updates[key]:
                    changes += 1
                continue
            new_item, item_changes = _transform_payload(item, field_updates, term_replacements)
            transformed[key] = new_item
            changes += item_changes
        return transformed, changes

    if isinstance(value, list):
        transformed = []
        for item in value:
            new_item, item_changes = _transform_payload(item, field_updates, term_replacements)
            transformed.append(new_item)
            changes += item_changes
        return transformed, changes

    if isinstance(value, str):
        updated, text_changes = _replace_text_values(value, term_replacements)
        return updated, text_changes

    return value, 0


def apply_dsar_platform_execution(
    type_droit: str,
    dsar_input: dict,
    dsar_output: dict,
    rectification_values: dict | None = None
) -> dict:
    """
    Apply a safe local DSAR execution to platform-managed data only.
    Supported rights: rectification, effacement.
    """
    q5 = dsar_output.get("q5_droits", {})
    recherche = q5.get("recherche_transversale", {})
    matches = recherche.get("matches", {})
    terms = [str(t) for t in recherche.get("termes_recherche", []) if t]

    field_names = {
        item.get("field_name")
        for item in matches.get("inventory_fields", [])
        if item.get("field_name")
    }

    if type_droit not in {"rectification", "effacement"}:
        return {
            "supported": False,
            "message": "Platform apply currently supports only rectification and effacement.",
            "records_modified": 0,
            "table_stats": {},
            "updated_ids": {},
        }

    if type_droit == "rectification" and not rectification_values:
        return {
            "supported": False,
            "message": "Rectification needs at least one replacement value.",
            "records_modified": 0,
            "table_stats": {},
            "updated_ids": {},
        }

    if type_droit == "effacement":
        placeholder = "[DONNEE_EFFACEE]"
        term_replacements = {term: placeholder for term in terms}
        field_updates = {field_name: placeholder for field_name in field_names}
    else:
        rectification_values = rectification_values or {}
        field_updates = {
            str(key): value
            for key, value in rectification_values.items()
            if key in field_names
        }
        term_replacements = {
            str(key): str(value)
            for key, value in rectification_values.items()
            if value is not None
        }

    conn = get_connection()
    cursor = conn.cursor()

    table_stats: dict[str, int] = {}
    updated_ids: dict[str, list[int]] = {}

    def register_change(table_name: str, record_id: int):
        table_stats[table_name] = table_stats.get(table_name, 0) + 1
        updated_ids.setdefault(table_name, []).append(record_id)

    def update_json_table(table_name: str, id_col: str, json_columns: list[str]):
        cursor.execute(f"SELECT * FROM {table_name}")
        rows = [dict(r) for r in cursor.fetchall()]
        for row in rows:
            updates = {}
            for column in json_columns:
                payload = _safe_json_load(row.get(column), {})
                if payload in ({}, []) and not row.get(column):
                    continue
                transformed, changes = _transform_payload(payload, field_updates, term_replacements)
                if changes:
                    updates[column] = json.dumps(transformed, ensure_ascii=False, default=str)
            if updates:
                assignments = ", ".join(f"{column} = ?" for column in updates.keys())
                params = list(updates.values()) + [row[id_col]]
                cursor.execute(
                    f"UPDATE {table_name} SET {assignments} WHERE {id_col} = ?",
                    params
                )
                register_change(table_name, row[id_col])

    update_json_table("treatments", "id", ["input_json", "output_json"])
    update_json_table("unstructured_scans", "id", ["findings_json", "result_json", "rgpd_impact_json"])
    update_json_table("inventory_treatments", "id", ["snapshot_json"])
    update_json_table("rgpd_register", "id", ["source_json"])

    cursor.execute("SELECT * FROM consents")
    consent_rows = [dict(r) for r in cursor.fetchall()]
    for row in consent_rows:
        updates = {}
        new_name, name_changes = _replace_text_values(row.get("nom_personne") or "", term_replacements)
        new_email, email_changes = _replace_text_values(row.get("email_personne") or "", term_replacements)

        if type_droit == "rectification":
            if "nom_personne" in field_updates:
                new_name = field_updates["nom_personne"]
                name_changes = max(name_changes, 1)
            if "email_personne" in field_updates:
                new_email = field_updates["email_personne"]
                email_changes = max(email_changes, 1)

        if name_changes:
            updates["nom_personne"] = new_name
        if email_changes:
            updates["email_personne"] = new_email
        if updates:
            updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            assignments = ", ".join(f"{column} = ?" for column in updates.keys())
            params = list(updates.values()) + [row["id"]]
            cursor.execute(f"UPDATE consents SET {assignments} WHERE id = ?", params)
            register_change("consents", row["id"])

    records_modified = sum(table_stats.values())
    conn.commit()
    conn.close()

    if records_modified:
        update_dsar_statut(dsar_input.get("id_demande"), "Cloture")

    return {
        "supported": True,
        "message": "Platform-managed data updated successfully." if records_modified else "No matching platform-managed data required changes.",
        "records_modified": records_modified,
        "table_stats": table_stats,
        "updated_ids": updated_ids,
        "field_updates": field_updates,
        "term_replacements": term_replacements,
    }

def get_dsar_by_id(id_demande: str) -> dict | None:
    """Return one saved DSAR request by business id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM dsars WHERE id_demande = ? ORDER BY created_at DESC LIMIT 1",
        (id_demande,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def save_dsar_execution(
    id_demande: str,
    type_droit: str,
    executor: str,
    mode_execution: str,
    decision: str,
    statut: str,
    legal_basis_note: str,
    targets_count: int,
    execution_payload: dict
) -> int:
    """Save one DSAR execution/audit log."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO dsar_executions (
            updated_at, id_demande, type_droit, executor, mode_execution,
            decision, statut, legal_basis_note, targets_count, execution_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        id_demande,
        type_droit,
        executor,
        mode_execution,
        decision,
        statut,
        legal_basis_note,
        targets_count,
        json.dumps(execution_payload, ensure_ascii=False, default=str)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_dsar_executions(id_demande: str = None, limit: int = 100) -> list:
    """List DSAR execution logs."""
    conn = get_connection()
    cursor = conn.cursor()
    if id_demande:
        cursor.execute(
            "SELECT * FROM dsar_executions WHERE id_demande = ? ORDER BY created_at DESC LIMIT ?",
            (id_demande, limit)
        )
    else:
        cursor.execute(
            "SELECT * FROM dsar_executions ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ============================================================
# RISK REVIEWS (Q4)
# ============================================================

def save_risk_review(
    traitement_input: dict,
    agent_b_output: dict,
    source_analysis_id: int = None
) -> int:
    """Save one Agent B Q4 risk/AIPD review snapshot."""
    q4 = agent_b_output.get("q4_risques_aipd", {})
    evidence = q4.get("evidence", {})
    aipd = q4.get("aipd", {})
    review_id = f"RISK-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO risk_reviews (
            updated_at, review_id, id_traitement, nom_traitement, systeme, module,
            aipd_requise, aipd_decision, nombre_risques, risques_critiques,
            risques_eleves, source_analysis_id, evidence_json, output_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        review_id,
        evidence.get("id_traitement") or traitement_input.get("id_traitement"),
        evidence.get("nom_traitement") or traitement_input.get("nom_traitement"),
        evidence.get("systeme") or traitement_input.get("systeme"),
        evidence.get("module") or traitement_input.get("qalitas_module"),
        int(aipd.get("aipd_requise", False)),
        aipd.get("decision"),
        q4.get("nombre_risques", 0),
        q4.get("risques_critiques", 0),
        q4.get("risques_eleves", 0),
        source_analysis_id,
        json.dumps(evidence, ensure_ascii=False, default=str),
        json.dumps(q4, ensure_ascii=False, default=str)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_risk_reviews(
    systeme: str = None,
    module: str = None,
    limit: int = 100
) -> list:
    """List saved Agent B Q4 risk reviews."""
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM risk_reviews WHERE 1=1"
    params = []
    if systeme:
        query += " AND systeme = ?"
        params.append(systeme)
    if module:
        query += " AND module = ?"
        params.append(module)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_risk_review_by_id(review_id: int) -> dict | None:
    """Return one saved risk review by DB id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM risk_reviews WHERE id = ? LIMIT 1", (review_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ============================================================
# INCIDENT REVIEWS (Q6)
# ============================================================

def save_incident_review(
    incident_input: dict,
    agent_b_output: dict,
    source_analysis_id: int = None
) -> int | None:
    """Save one Agent B Q6 incident evidence snapshot."""
    q6 = agent_b_output.get("q6_incidents", {})
    if not q6.get("incident_declare"):
        return None

    evidence = q6.get("evidence", {})
    notification = q6.get("notification", {})
    review_id = f"Q6-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO incident_reviews (
            updated_at, review_id, id_incident, id_traitement, nom_traitement, systeme,
            module, qualification, notifier_cnil, notifier_personnes,
            source_analysis_id, evidence_json, output_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        review_id,
        incident_input.get("id_incident", f"INC-{datetime.now().strftime('%Y%m%d%H%M%S')}"),
        evidence.get("id_traitement"),
        evidence.get("nom_traitement"),
        evidence.get("systeme"),
        evidence.get("module"),
        q6.get("qualification"),
        int(notification.get("notifier_cnil", False)),
        int(notification.get("notifier_personnes", False)),
        source_analysis_id,
        json.dumps(evidence, ensure_ascii=False, default=str),
        json.dumps(q6, ensure_ascii=False, default=str)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_incident_reviews(
    id_incident: str = None,
    systeme: str = None,
    limit: int = 100
) -> list:
    """List saved Agent B Q6 incident review snapshots."""
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM incident_reviews WHERE 1=1"
    params = []
    if id_incident:
        query += " AND id_incident = ?"
        params.append(id_incident)
    if systeme:
        query += " AND systeme = ?"
        params.append(systeme)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_incident_review_by_id(review_id: int) -> dict | None:
    """Return one saved incident review by DB id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM incident_reviews WHERE id = ? LIMIT 1", (review_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def save_governance_snapshot(agent_d_output: dict) -> int:
    """Persist one Agent D governance snapshot for later trend analysis."""
    def _snapshot_summary(entry: dict) -> dict:
        if not isinstance(entry, dict):
            return {}
        return {
            "id": entry.get("id"),
            "created_at": entry.get("created_at"),
            "updated_at": entry.get("updated_at"),
            "id_traitement": entry.get("id_traitement"),
            "nom_traitement": entry.get("nom_traitement"),
            "systeme": entry.get("systeme"),
            "module": entry.get("module"),
            "score_maturite_rgpd": entry.get("score_maturite_rgpd"),
            "niveau_maturite": entry.get("niveau_maturite"),
            "violations_count": entry.get("violations_count"),
            "risk_reviews_count": entry.get("risk_reviews_count"),
            "incident_reviews_count": entry.get("incident_reviews_count"),
            "dsars_open_count": entry.get("dsars_open_count"),
            "actions_open_count": entry.get("actions_open_count"),
            "critical_alerts_count": entry.get("critical_alerts_count"),
            "snapshot_json_bytes": entry.get("snapshot_json_bytes"),
        }

    compact_payload = json.loads(json.dumps(agent_d_output or {}, ensure_ascii=False, default=str))
    q7 = compact_payload.get("q7_gouvernance") or {}
    historique = q7.get("historique") or {}
    snapshots = historique.get("snapshots") or []
    compact_snapshots = [_snapshot_summary(entry) for entry in snapshots[:20] if isinstance(entry, dict)]
    if historique:
        historique["snapshots"] = compact_snapshots
        recent = historique.get("recent") or {}
        if isinstance(recent, dict) and "governance_snapshots" in recent:
            recent["governance_snapshots"] = compact_snapshots[:5]
        q7["historique"] = historique
    compact_payload["q7_gouvernance"] = q7

    q7 = agent_d_output.get("q7_gouvernance", {})
    historique = q7.get("historique", {})
    identity = historique.get("identity", {})
    summary = historique.get("summary", {})
    consolidation = q7.get("consolidation", {})
    conformite = consolidation.get("conformite", {})

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO governance_snapshots (
            updated_at, id_traitement, nom_traitement, systeme, module,
            score_maturite_rgpd, niveau_maturite, violations_count,
            risk_reviews_count, incident_reviews_count, dsars_open_count,
            actions_open_count, critical_alerts_count, snapshot_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        identity.get("id_traitement"),
        identity.get("nom_traitement"),
        identity.get("systeme"),
        identity.get("module"),
        q7.get("score_maturite_rgpd", 0),
        q7.get("niveau_maturite"),
        conformite.get("nombre_violations", 0),
        summary.get("risk_reviews_count", 0),
        summary.get("incident_reviews_count", 0),
        summary.get("dsars_open_count", 0),
        summary.get("actions_open_count", 0),
        len(q7.get("alertes_critiques", [])),
        json.dumps(compact_payload, ensure_ascii=False, default=str)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_governance_snapshot_summaries(
    id_traitement: str = None,
    systeme: str = None,
    module: str = None,
    limit: int = 50
) -> list:
    """Return governance snapshot metadata without loading the full snapshot JSON."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT
            id, created_at, updated_at, id_traitement, nom_traitement, systeme, module,
            score_maturite_rgpd, niveau_maturite, violations_count,
            risk_reviews_count, incident_reviews_count, dsars_open_count,
            actions_open_count, critical_alerts_count,
            IFNULL(length(snapshot_json), 0) AS snapshot_json_bytes
        FROM governance_snapshots
        WHERE 1=1
    """
    params = []
    if id_traitement:
        query += " AND id_traitement = ?"
        params.append(id_traitement)
    if systeme:
        query += " AND systeme = ?"
        params.append(systeme)
    if module:
        query += " AND module = ?"
        params.append(module)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_latest_governance_snapshot(
    id_traitement: str = None,
    systeme: str = None,
    module: str = None,
    max_snapshot_bytes: int = 5 * 1024 * 1024,
) -> dict | None:
    """Return one recent governance snapshot payload that stays below a safe size."""
    # First fetch lightweight metadata only, then load the JSON for one selected row.
    # This avoids scanning and materialising the heavy snapshot_json column too early.
    summaries = get_governance_snapshot_summaries(
        id_traitement=id_traitement,
        systeme=systeme,
        module=module,
        limit=20,
    )
    candidate = next(
        (
            item for item in summaries
            if int(item.get("snapshot_json_bytes") or 0) <= max_snapshot_bytes
        ),
        None,
    )
    if not candidate:
        return None

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            id, created_at, updated_at, id_traitement, nom_traitement, systeme, module,
            score_maturite_rgpd, niveau_maturite, violations_count,
            risk_reviews_count, incident_reviews_count, dsars_open_count,
            actions_open_count, critical_alerts_count, snapshot_json,
            IFNULL(length(snapshot_json), 0) AS snapshot_json_bytes
        FROM governance_snapshots
        WHERE id = ?
        LIMIT 1
        """,
        (candidate["id"],),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_governance_snapshots(
    id_traitement: str = None,
    systeme: str = None,
    module: str = None,
    limit: int = 50
) -> list:
    """Return saved Agent D governance snapshots."""
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM governance_snapshots WHERE 1=1"
    params = []
    if id_traitement:
        query += " AND id_traitement = ?"
        params.append(id_traitement)
    if systeme:
        query += " AND systeme = ?"
        params.append(systeme)
    if module:
        query += " AND module = ?"
        params.append(module)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def save_dsar_mailbox(
    email_address: str,
    provider: str,
    label: str = None,
    sample_message: str = None,
    metadata: dict | None = None
) -> int:
    """Create or update a DSAR mailbox source."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO dsar_mailboxes (
            updated_at, email_address, provider, label, sample_message, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(email_address) DO UPDATE SET
            updated_at = excluded.updated_at,
            provider = excluded.provider,
            label = excluded.label,
            sample_message = excluded.sample_message,
            metadata_json = excluded.metadata_json
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        email_address,
        provider,
        label,
        sample_message,
        json.dumps(metadata or {}, ensure_ascii=False, default=str)
    ))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_dsar_mailboxes(active_only: bool = False, limit: int = 100) -> list:
    """Return configured DSAR mailbox sources."""
    conn = get_connection()
    cursor = conn.cursor()
    if active_only:
        cursor.execute(
            "SELECT * FROM dsar_mailboxes WHERE is_active = 1 ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        )
    else:
        cursor.execute(
            "SELECT * FROM dsar_mailboxes ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_dsar_mailbox_by_id(mailbox_id: int) -> dict | None:
    """Return one DSAR mailbox source by id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM dsar_mailboxes WHERE id = ? LIMIT 1", (mailbox_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_dsar_mailbox_sync(mailbox_id: int, access_status: str = "synced") -> bool:
    """Update mailbox sync timestamp and access status."""
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        UPDATE dsar_mailboxes
        SET updated_at = ?, last_sync_at = ?, access_status = ?
        WHERE id = ?
    """, (now, now, access_status, mailbox_id))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# ============================================================
# REAL-TIME SYNC (snapshots + events)
# ============================================================

def save_realtime_snapshot(
    source_system: str,
    module: str,
    module_label: str,
    records_count: int,
    records_hash: str,
    fields_hash: str,
    personal_fields_count: int = 0,
    sensitive_fields_count: int = 0,
    physical_persons_count: int = 0,
    snapshot: dict | None = None
) -> int:
    """Create or update the latest fingerprint for one source/module."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO realtime_snapshots (
            updated_at, source_system, module, module_label, records_count,
            records_hash, fields_hash, personal_fields_count,
            sensitive_fields_count, physical_persons_count, snapshot_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_system, module) DO UPDATE SET
            updated_at = excluded.updated_at,
            module_label = excluded.module_label,
            records_count = excluded.records_count,
            records_hash = excluded.records_hash,
            fields_hash = excluded.fields_hash,
            personal_fields_count = excluded.personal_fields_count,
            sensitive_fields_count = excluded.sensitive_fields_count,
            physical_persons_count = excluded.physical_persons_count,
            snapshot_json = excluded.snapshot_json
    """, (
        now,
        source_system,
        module,
        module_label,
        records_count,
        records_hash,
        fields_hash,
        personal_fields_count,
        sensitive_fields_count,
        physical_persons_count,
        json.dumps(snapshot or {}, ensure_ascii=False, default=str),
    ))
    row_id = cursor.lastrowid
    if not row_id:
        cursor.execute(
            "SELECT id FROM realtime_snapshots WHERE source_system = ? AND module = ?",
            (source_system, module)
        )
        row = cursor.fetchone()
        row_id = row["id"] if row else 0
    conn.commit()
    conn.close()
    return row_id


def get_latest_realtime_snapshot(source_system: str, module: str) -> dict | None:
    """Return the latest stored fingerprint for one source/module."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM realtime_snapshots
        WHERE source_system = ? AND module = ?
        LIMIT 1
    """, (source_system, module))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def save_realtime_event(
    source_system: str,
    event_type: str,
    title: str,
    message: str,
    module: str = None,
    module_label: str = None,
    severity: str = "info",
    old_count: int = 0,
    new_count: int = 0,
    delta_count: int = 0,
    status: str = "open",
    metadata: dict | None = None,
    event_id: str = None
) -> str:
    """Persist one real-time sync/detection event."""
    event_id = event_id or f"RT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO realtime_events (
            updated_at, event_id, source_system, module, module_label,
            severity, event_type, title, message, old_count, new_count,
            delta_count, status, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now,
        event_id,
        source_system,
        module,
        module_label,
        severity,
        event_type,
        title,
        message,
        old_count,
        new_count,
        delta_count,
        status,
        json.dumps(metadata or {}, ensure_ascii=False, default=str),
    ))
    conn.commit()
    conn.close()
    return event_id


def get_realtime_events(
    source_system: str = None,
    status: str = None,
    limit: int = 100
) -> list:
    """List recent real-time sync/detection events."""
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM realtime_events WHERE 1=1"
    params = []
    if source_system:
        query += " AND source_system = ?"
        params.append(source_system)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

