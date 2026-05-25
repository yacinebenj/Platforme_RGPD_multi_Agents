import sys
from pathlib import Path

# Add parent directory to path so we can import agents, llm, etc.
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Response, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Union, List, Optional, Dict, Any
from agents.agent_a import run_agent_a
from agents.agent_b import run_agent_b
from agents.agent_c import DROITS_RGPD, run_agent_c
from agents.agent_d import run_agent_d
from agents.unstructured_detector import scan_bytes as _detector_scan_bytes
from orchestrator.workflow import run_workflow  # LangGraph orchestrator
from database.db import init_db
from database import crud
from security.roles import ROLE_LABELS, has_permission, role_permissions
import os
import json
import asyncio
import base64
import hashlib
import logging
import re
import time
import copy
import threading
import unicodedata
from datetime import datetime
from qalitas.connector import QalitasConnector, MODULE_LABELS, MODULE_ENDPOINTS, get_connector as get_qalitas_connector
from gmao.connector import (
    GMAO_FETCH_TIMEOUT,
    GmaoConnector,
    MODULE_LABELS as GMAO_MODULE_LABELS,
    MODULE_ENDPOINTS as GMAO_MODULE_ENDPOINTS,
    get_connector as get_gmao_connector,
)
from integrations.microsoft365_mail import Microsoft365MailConnector
from integrations.imap_mail import ImapMailConnector
from integrations.mail_text import clean_mail_text
from llm.dpo_assistant import generate_assistant_answer
from ml.assistant_intent_classifier import dataset_summary as assistant_dataset_summary, predict_assistant_intent, train_assistant_intent_model
from ml.dsar_classifier import dataset_summary as dsar_dataset_summary, predict_dsar_intent, train_dsar_intent_model
from ml.field_classifier import canonical_field_label, dataset_summary as field_dataset_summary, predict_field_category, train_field_classifier_model

app = FastAPI()


def _model_to_dict(model: Any) -> dict[str, Any]:
    """Compatibility helper for Pydantic v1/v2 models used by API endpoints."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5501",
        "http://localhost:5501",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

analysis_logger = logging.getLogger("rgpd.analysis")

DEFAULT_REALTIME_MODULES = [
    "customers",
    "suppliers",
    "employees",
    "nonconf",
    "companies",
    "sites",
]

DEMO_MODE_ENABLED = True
QALITAS_DEMO_MODULES = ["customers", "suppliers", "employees", "nonconf", "companies"]
GMAO_DEMO_MODULES = ["customers", "suppliers", "equipments", "toolings", "maintenance_ranges", "articles", "resource_needs"]
QALITAS_DEMO_EXCLUDED = {"audits", "actions", "sites"}
GMAO_DEMO_EXCLUDED = {
    "organization_chart",
    "meeting_actions",
    "meetings",
    "maintenance_teams",
    "qualifications",
    "maintenance_operations",
    "purchase_requests",
    "purchase_orders",
    "supplier_contracts",
    "purchase_invoices",
    "calculation_needs",
}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEMO_AUTH_DISABLED = _env_bool("DEMO_AUTH_DISABLED", True)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: list[str] | None = None) -> list[str] | None:
    value = os.getenv(name)
    if not value:
        return default
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or default


def _demo_modules_for_source(source: str) -> list[str]:
    return QALITAS_DEMO_MODULES if source == "qalitas" else GMAO_DEMO_MODULES


def _demo_labels_for_source(source: str) -> dict[str, str]:
    return MODULE_LABELS if source == "qalitas" else GMAO_MODULE_LABELS


def _demo_module_options(source: str) -> list[dict[str, str]]:
    labels = _demo_labels_for_source(source)
    return [{"id": "all", "label": "Toute la source (démo validée)"}] + [
        {"id": module_id, "label": labels.get(module_id, module_id)}
        for module_id in _demo_modules_for_source(source)
    ]


def _ensure_demo_module_allowed(source: str, module: str) -> None:
    if not DEMO_MODE_ENABLED or module == "all":
        return
    allowed = set(_demo_modules_for_source(source))
    if module in allowed:
        return
    labels = _demo_labels_for_source(source)
    raise HTTPException(
        status_code=400,
        detail=(
            f"Le module '{labels.get(module, module)}' est hors périmètre démo validé. "
            "Utilisez un module stable depuis l'interface."
        ),
    )


def _append_demo_note(note: str, source: str) -> str:
    if not DEMO_MODE_ENABLED:
        return note
    labels = _demo_labels_for_source(source)
    modules = ", ".join(labels.get(module_id, module_id) for module_id in _demo_modules_for_source(source))
    demo_note = f"Mode démo validé : lecture limitée aux modules stables suivants — {modules}."
    if note:
        return f"{note} {demo_note}"
    return demo_note


SOURCE_BUNDLE_CACHE_TTL_SECONDS = _env_int("SOURCE_BUNDLE_CACHE_TTL_SECONDS", 180)
SOURCE_BUNDLE_CACHE_SCHEMA_VERSION = 2
QALITAS_ALL_MODULE_TIMEOUT = _env_int("QALITAS_ALL_MODULE_TIMEOUT", 8)
QALITAS_ALL_MODULE_RETRY_TIMEOUT = _env_int("QALITAS_ALL_MODULE_RETRY_TIMEOUT", 0)
QALITAS_ALL_MODULE_LIMIT_CAP = _env_int("QALITAS_ALL_MODULE_LIMIT_CAP", 50)
# Grouped GMAO reads should inherit the connector's normal fetch timeout by
# default. The previous hard-coded 8s was noticeably shorter than single-module
# reads and caused avoidable timeouts during "Toute la source".
GMAO_ALL_MODULE_TIMEOUT = _env_int("GMAO_ALL_MODULE_TIMEOUT", GMAO_FETCH_TIMEOUT)
GMAO_ALL_MODULE_RETRY_TIMEOUT = _env_int("GMAO_ALL_MODULE_RETRY_TIMEOUT", 0)
GMAO_ALL_MODULE_LIMIT_CAP = _env_int("GMAO_ALL_MODULE_LIMIT_CAP", 50)
GLOBAL_SOURCES_LIMIT_CAP = _env_int("GLOBAL_SOURCES_LIMIT_CAP", 100)

_source_bundle_cache: dict[tuple[str, str, int], dict[str, Any]] = {}
_source_bundle_cache_lock = threading.Lock()


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _log_analysis_timings(source: str, module: str, timings: dict[str, int], records_count: int, note: str = ""):
    suffix = f" note={note}" if note else ""
    analysis_logger.info(
        "[ANALYSIS][%s:%s] login=%sms fetch=%sms agent_a=%sms save=%sms total=%sms records=%s%s",
        source,
        module,
        timings.get("login_ms", 0),
        timings.get("fetch_ms", 0),
        timings.get("agent_a_ms", 0),
        timings.get("save_ms", 0),
        timings.get("total_ms", 0),
        records_count,
        suffix,
    )


def _bundle_cache_key(source: str, module: str, limit: int) -> tuple[int, str, str, int]:
    return (SOURCE_BUNDLE_CACHE_SCHEMA_VERSION, source, module, int(limit))


def _get_cached_bundle(source: str, module: str, limit: int) -> dict[str, Any] | None:
    if SOURCE_BUNDLE_CACHE_TTL_SECONDS <= 0:
        return None
    key = _bundle_cache_key(source, module, limit)
    now = time.monotonic()
    with _source_bundle_cache_lock:
        entry = _source_bundle_cache.get(key)
        if not entry:
            return None
        age = now - entry.get("ts", 0.0)
        if age > SOURCE_BUNDLE_CACHE_TTL_SECONDS:
            _source_bundle_cache.pop(key, None)
            return None
        cached = copy.deepcopy(entry["value"])
    cached["cache_hit"] = True
    cached["cache_age_seconds"] = int(age)
    return cached


def _set_cached_bundle(source: str, module: str, limit: int, bundle: dict[str, Any]) -> None:
    if SOURCE_BUNDLE_CACHE_TTL_SECONDS <= 0:
        return
    key = _bundle_cache_key(source, module, limit)
    stored = copy.deepcopy(bundle)
    stored["cache_hit"] = False
    stored["cache_age_seconds"] = 0
    with _source_bundle_cache_lock:
        _source_bundle_cache[key] = {
            "ts": time.monotonic(),
            "value": stored,
        }

# Initialize DB tables on startup
@app.on_event("startup")
def startup_event():
    init_db()
    crud.ensure_default_users()
    # Realtime monitoring is parked for now. The files/routes remain available,
    # but the background scheduler will not start unless explicitly enabled.
    if _env_bool("REALTIME_AUTO_START", False):
        try:
            from realtime import scheduler as realtime_scheduler
            realtime_scheduler.start(
                interval_seconds=_env_int("REALTIME_INTERVAL_SECONDS", 60),
                sources=_env_list("REALTIME_SOURCES"),
                modules=_env_list("REALTIME_MODULES", DEFAULT_REALTIME_MODULES),
                limit=_env_int("REALTIME_LIMIT", 100),
            )
        except Exception as exc:
            print(f"[REALTIME] Auto-start skipped: {exc}")


@app.on_event("shutdown")
def shutdown_event():
    try:
        from realtime import scheduler as realtime_scheduler
        realtime_scheduler.stop()
    except Exception:
        pass


SESSION_COOKIE_NAME = "rgpd_session"
SESSION_TTL_HOURS = 12


class LoginRequest(BaseModel):
    username: str
    password: str


class SignupRequest(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = ""
    email: Optional[str] = ""
    role: Optional[str] = "contributeur"


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str
    full_name: Optional[str] = ""
    email: Optional[str] = ""
    is_active: bool = True


class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


def _session_token_from_request(request: Request) -> Optional[str]:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        return cookie
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


def current_user_optional(request: Request) -> Optional[dict]:
    if DEMO_AUTH_DISABLED:
        users = crud.list_app_users()
        for user in users:
            if str(user.get("username") or "").strip().lower() == "dpo":
                return user
        if users:
            return users[0]
        fallback = {
            "id": "demo-dpo",
            "username": "dpo",
            "full_name": "Y. Benjemaa - DPO",
            "email": "dpo@rgpd.local",
            "role": "dpo",
            "is_active": True,
        }
    token = _session_token_from_request(request)
    return crud.get_user_by_session_token(token) if token else None


def get_current_user(request: Request) -> dict:
    user = current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Connexion requise.")
    return user


def require_permission(permission: str):
    def checker(user: dict = Depends(get_current_user)):
        if not has_permission(user.get("role"), permission):
            raise HTTPException(status_code=403, detail=f"Acces refuse pour ce role: {permission}")
        return user
    return checker


def require_any_permission(*permissions: str):
    def checker(user: dict = Depends(get_current_user)):
        if not any(has_permission(user.get("role"), permission) for permission in permissions):
            allowed = ", ".join(permissions)
            raise HTTPException(status_code=403, detail=f"Acces refuse pour ce role: {allowed}")
        return user
    return checker


def _user_has_any(user: dict, *permissions: str) -> bool:
    return any(has_permission(user.get("role"), permission) for permission in permissions)


def _auth_payload(user: dict) -> dict:
    return {
        "user": user,
        "roles": ROLE_LABELS,
        "permissions": role_permissions(user.get("role")),
    }


def _actor_name(user: Optional[dict]) -> str:
    if not user:
        return "Utilisateur"
    return user.get("full_name") or user.get("username") or "Utilisateur"


def _is_actor_assigned_to_action(user: dict, action: dict | None) -> bool:
    if not user or not action:
        return False
    owner = str(action.get("owner") or "").strip().lower()
    candidates = {
        str(user.get("username") or "").strip().lower(),
        str(user.get("full_name") or "").strip().lower(),
        str(user.get("email") or "").strip().lower(),
    }
    candidates.discard("")
    return owner in candidates


@app.post("/auth/login")
def auth_login(data: LoginRequest, request: Request, response: Response):
    user = crud.authenticate_user(data.username, data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Identifiants invalides.")
    token = crud.create_app_session(
        user["id"],
        user_agent=request.headers.get("user-agent"),
        ttl_hours=SESSION_TTL_HOURS,
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_TTL_HOURS * 3600,
    )
    crud.save_audit_event(user, "auth.login", "user", user["id"], {"role": user.get("role")})
    return _auth_payload(user)


@app.post("/auth/signup")
def auth_signup(data: SignupRequest, request: Request, response: Response):
    username = (data.username or "").strip()
    password = data.password or ""
    role = (data.role or "contributeur").strip().lower()
    if role not in {"contributeur", "auditeur"}:
        raise HTTPException(status_code=400, detail="L'inscription publique est limitee aux roles contributeur et auditeur.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        raise HTTPException(status_code=400, detail="Nom utilisateur invalide: 3-40 caracteres, lettres/chiffres/._- uniquement.")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Mot de passe trop court: 8 caracteres minimum.")
    try:
        user = crud.create_app_user(
            username=username,
            password=password,
            role=role,
            full_name=data.full_name or username,
            email=data.email or "",
            is_active=True,
            metadata={"origin": "self_signup"},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    token = crud.create_app_session(
        user["id"],
        user_agent=request.headers.get("user-agent"),
        ttl_hours=SESSION_TTL_HOURS,
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_TTL_HOURS * 3600,
    )
    crud.save_audit_event(user, "auth.signup", "user", user["id"], {"role": user.get("role")})
    return _auth_payload(user)


@app.post("/auth/logout")
def auth_logout(request: Request, response: Response, user: dict = Depends(get_current_user)):
    token = _session_token_from_request(request)
    crud.revoke_app_session(token)
    response.delete_cookie(SESSION_COOKIE_NAME)
    crud.save_audit_event(user, "auth.logout", "user", user["id"], {})
    return {"status": "logged_out"}


@app.get("/auth/me")
def auth_me(user: dict = Depends(get_current_user)):
    return _auth_payload(user)


@app.get("/auth/users")
def auth_users(user: dict = Depends(require_permission("users:manage"))):
    return {"users": crud.list_app_users(), "roles": ROLE_LABELS}


@app.post("/auth/users")
def auth_create_user(data: UserCreateRequest, user: dict = Depends(require_permission("users:manage"))):
    try:
        created = crud.create_app_user(
            username=data.username,
            password=data.password,
            role=data.role,
            full_name=data.full_name or "",
            email=data.email or "",
            is_active=data.is_active,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    crud.save_audit_event(
        user,
        "users.create",
        "user",
        created["id"],
        {"username": created["username"], "role": created["role"]},
    )
    return {"user": created}


@app.patch("/auth/users/{user_id}")
def auth_update_user(
    user_id: int,
    data: UserUpdateRequest,
    user: dict = Depends(require_permission("users:manage")),
):
    details = data.dict(exclude_none=True)
    updated = crud.update_app_user(user_id=user_id, **details)
    if not updated:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    crud.save_audit_event(user, "users.update", "user", user_id, details)
    return {"user": updated}


@app.get("/auth/audit")
def auth_audit(limit: int = 100, user: dict = Depends(require_permission("proofs:view"))):
    return {"events": crud.get_audit_events(limit=limit)}


def persist_register_and_actions(traitement_input: dict, agent_a_output: dict, source_analysis_id: int = None):
    """Persist operational register snapshot + auto corrective actions from Q2 gaps."""
    register_entry = agent_a_output.get("q1_register", {})
    if not register_entry:
        return {"register_id": None, "actions_created": 0, "inventory_treatment_id": None}

    register_id = crud.save_register_entry(
        traitement_input=traitement_input,
        register_entry=register_entry,
        source_analysis_id=source_analysis_id
    )

    gaps = agent_a_output.get("q2_conformite", {}).get("gaps", [])
    action_ids = crud.create_actions_from_gaps(
        gaps=gaps,
        linked_treatment_id=register_entry.get("id_traitement") or traitement_input.get("id_traitement"),
        linked_register_id=register_id,
        owner="DPO"
    )
    inventory_treatment_id = crud.save_inventory_snapshot(
        traitement_input=traitement_input,
        agent_a_output=agent_a_output,
        register_id=register_id,
        source_analysis_id=source_analysis_id
    )
    return {
        "register_id": register_id,
        "actions_created": len(action_ids),
        "inventory_treatment_id": inventory_treatment_id
    }


def _safe_json(value, default=None):
    """Decode JSON columns defensively without breaking the register view."""
    if default is None:
        default = {}
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _as_list(value):
    parsed = _safe_json(value, value)
    if parsed in (None, ""):
        return []
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    return [str(parsed)]


def _norm_register_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _register_identity(entry: dict) -> dict:
    source = entry.get("source_json") if isinstance(entry.get("source_json"), dict) else {}
    treatment_values = [
        entry.get("id_traitement"),
        entry.get("nom_traitement"),
        source.get("id_traitement"),
        source.get("nom_traitement"),
        source.get("processus"),
        source.get("treatment_name"),
    ]
    system_values = [entry.get("systeme"), source.get("systeme"), source.get("source_system")]
    module_values = [
        entry.get("module"),
        entry.get("source_module"),
        source.get("module"),
        source.get("source_module"),
        source.get("qalitas_module"),
        source.get("gmao_module"),
    ]
    return {
        "register_id": str(entry.get("id") or ""),
        "treatments": {_norm_register_text(v) for v in treatment_values if _norm_register_text(v)},
        "systems": {_norm_register_text(v) for v in system_values if _norm_register_text(v)},
        "modules": {_norm_register_text(v) for v in module_values if _norm_register_text(v)},
    }


def _prepare_register_entry(row: dict) -> dict:
    entry = dict(row)
    entry["categories_donnees"] = _as_list(entry.get("categories_donnees"))
    entry["personnes_concernees"] = _as_list(entry.get("personnes_concernees"))
    entry["destinataires"] = _as_list(entry.get("destinataires"))
    entry["mesures_securite"] = _as_list(entry.get("mesures_securite"))
    entry["source_json"] = _safe_json(entry.get("source_json"), {})
    source = entry["source_json"] if isinstance(entry["source_json"], dict) else {}
    entry["module"] = source.get("module") or source.get("source_module") or source.get("qalitas_module") or source.get("gmao_module")
    entry["hors_champ_rgpd"] = bool(
        source.get("hors_champ_rgpd")
        or source.get("no_personal_data")
        or source.get("is_out_of_scope")
    )
    return entry


def _validation_matches_register(entry: dict, validation: dict) -> bool:
    ident = _register_identity(entry)
    target_id = _norm_register_text(validation.get("target_id"))
    target_label = _norm_register_text(validation.get("target_label"))
    source_system = _norm_register_text(validation.get("source_system"))
    source_module = _norm_register_text(validation.get("source_module"))

    for candidate in ident["treatments"]:
        if candidate and (candidate == target_id or candidate == target_label or candidate in target_label):
            return True

    if source_system and ident["systems"] and source_system not in ident["systems"]:
        return False
    if source_module and ident["modules"] and source_module not in ident["modules"]:
        return False
    return bool((source_system and source_system in ident["systems"]) or (source_module and source_module in ident["modules"]))


def _action_matches_register(entry: dict, action: dict) -> bool:
    ident = _register_identity(entry)
    linked_register_id = _norm_register_text(action.get("linked_register_id"))
    linked_treatment_id = _norm_register_text(action.get("linked_treatment_id"))
    if linked_register_id and linked_register_id == _norm_register_text(entry.get("id")):
        return True
    if linked_treatment_id and linked_treatment_id in ident["treatments"]:
        return True

    blob = _norm_register_text([
        action.get("treatment_name"),
        action.get("source_system"),
        action.get("source_module"),
        action.get("metadata"),
        action.get("metadata_json"),
    ])
    return any(candidate and candidate in blob for candidate in ident["treatments"])


def _row_matches_register(entry: dict, row: dict, fields: list[str]) -> bool:
    ident = _register_identity(entry)
    blob = _norm_register_text([row.get(field) for field in fields])
    return any(candidate and candidate in blob for candidate in ident["treatments"])


def _latest_validations_for_register(entry: dict, validations: list[dict]) -> dict:
    latest = {}
    for validation in validations:
        if not _validation_matches_register(entry, validation):
            continue
        target_type = validation.get("target_type") or "treatment"
        if target_type not in latest:
            latest[target_type] = validation
    return latest


def _is_action_closed(action: dict) -> bool:
    status = _norm_register_text(action.get("statut") or action.get("status"))
    dpo_validation = action.get("dpo_validation") if isinstance(action.get("dpo_validation"), dict) else {}
    decision = _norm_register_text(dpo_validation.get("decision"))
    return status in {"cloturee", "cloture", "closed", "terminee", "validee"} or decision in {"cloture", "valide"}


def _build_register_status(entry: dict, latest: dict, links: dict) -> dict:
    action_count = len(links.get("actions", []))
    open_actions = len([a for a in links.get("actions", []) if not _is_action_closed(a)])
    incident_count = len(links.get("incidents", []))
    dpia_count = len(links.get("dpia", []))
    proof_count = len(links.get("proofs", [])) + len(latest)

    legal_basis = _norm_register_text(entry.get("base_legale"))
    if entry.get("hors_champ_rgpd"):
        legal_status = "Hors champ RGPD"
        overall = "Hors champ"
    elif latest.get("legal_basis"):
        legal_status = latest["legal_basis"].get("decision_label") or latest["legal_basis"].get("decision") or "Validee"
        overall = "Sous controle" if _norm_register_text(latest["legal_basis"].get("decision")) == "valide" else "A revoir"
    elif not legal_basis or legal_basis in {"non definie", "non définie", "-"}:
        legal_status = "A definir"
        overall = "A completer"
    else:
        legal_status = "A confirmer"
        overall = "A valider"

    if incident_count:
        overall = "Incident a suivre"
    elif open_actions:
        overall = "Actions ouvertes"
    elif latest.get("treatment") and _norm_register_text(latest["treatment"].get("decision")) == "valide" and not open_actions:
        overall = "Sous controle"

    dpia_status = "Non requise"
    if dpia_count:
        dpia_status = "A valider"
    if latest.get("dpia"):
        dpia_status = latest["dpia"].get("decision_label") or latest["dpia"].get("decision") or "Validee"

    treatment_status = "A valider"
    if latest.get("treatment"):
        treatment_status = latest["treatment"].get("decision_label") or latest["treatment"].get("decision") or "Validee"

    return {
        "overall": overall,
        "treatment": treatment_status,
        "legal_basis": legal_status,
        "dpia": dpia_status,
        "actions_open": open_actions,
        "actions_total": action_count,
        "incidents_total": incident_count,
        "proofs_total": proof_count,
    }


def _enrich_register_entries(entries: list[dict]) -> tuple[list[dict], dict]:
    prepared = [_prepare_register_entry(entry) for entry in entries]
    validations = crud.get_dpo_validations(limit=1000)
    actions = crud.get_actions(limit=1000)
    dpia_dossiers = crud.get_dpia_dossiers(limit=500)
    incidents = crud.get_violations(limit=500)
    dsars = crud.get_dsars(limit=500)
    consents = crud.get_consents()

    enriched = []
    for entry in prepared:
        latest = _latest_validations_for_register(entry, validations)
        linked_actions = [
            a for a in actions
            if _norm_register_text(a.get("linked_register_id")) == _norm_register_text(entry.get("id"))
        ]
        if not linked_actions:
            linked_actions = [a for a in actions if _action_matches_register(entry, a)]
        linked_dpia = [d for d in dpia_dossiers if _row_matches_register(entry, d, ["nom_traitement", "systeme", "source_json"])]
        linked_incidents = [i for i in incidents if _row_matches_register(entry, i, ["traitement", "description", "qualification", "source_json"])]
        linked_dsars = [d for d in dsars if _row_matches_register(entry, d, ["nom_demandeur", "type_droit", "systeme_concerne", "id_demande"])]
        linked_consents = [c for c in consents if _row_matches_register(entry, c, ["id_traitement", "personne", "finalite"])]

        proofs = []
        for validation in latest.values():
            proofs.append({
                "type": "validation_dpo",
                "reference": validation.get("proof_reference") or validation.get("validation_id"),
                "label": validation.get("target_label") or validation.get("target_type"),
                "decision": validation.get("decision_label") or validation.get("decision"),
                "created_at": validation.get("created_at"),
            })
        for action in linked_actions:
            action_proofs = action.get("proofs") if isinstance(action.get("proofs"), list) else []
            for proof in action_proofs:
                proofs.append({
                    "type": "action_corrective",
                    "reference": proof.get("proof_reference") or proof.get("id"),
                    "label": proof.get("file_name") or proof.get("summary") or action.get("title"),
                    "decision": proof.get("validation_status") or action.get("statut"),
                    "created_at": proof.get("created_at") or action.get("updated_at"),
                })

        links = {
            "actions": linked_actions,
            "dpia": linked_dpia,
            "incidents": linked_incidents,
            "dsars": linked_dsars,
            "consents": linked_consents,
            "proofs": proofs,
        }
        entry["latest_validations"] = latest
        entry["operational_links"] = links
        entry["register_status"] = _build_register_status(entry, latest, links)
        entry["dpo_validation"] = latest.get("treatment") or latest.get("legal_basis") or latest.get("dpia")
        enriched.append(entry)

    summary = {
        "total": len(enriched),
        "validated": len([e for e in enriched if _norm_register_text(e.get("register_status", {}).get("overall")) == "sous controle"]),
        "to_validate": len([e for e in enriched if "valider" in _norm_register_text(e.get("register_status", {}).get("overall"))]),
        "to_complete": len([e for e in enriched if "completer" in _norm_register_text(e.get("register_status", {}).get("overall")) or "definir" in _norm_register_text(e.get("register_status", {}).get("legal_basis"))]),
        "open_actions": sum(e.get("register_status", {}).get("actions_open", 0) for e in enriched),
        "incidents": sum(e.get("register_status", {}).get("incidents_total", 0) for e in enriched),
        "proofs": sum(e.get("register_status", {}).get("proofs_total", 0) for e in enriched),
    }
    return enriched, summary


def _compact_validation_for_ui(validation: dict | None) -> dict | None:
    if not validation:
        return None
    return {
        "target_type": validation.get("target_type"),
        "target_id": validation.get("target_id"),
        "target_label": validation.get("target_label"),
        "decision": validation.get("decision"),
        "decision_label": validation.get("decision_label"),
        "validator": validation.get("validator"),
        "proof_reference": validation.get("proof_reference") or validation.get("validation_id"),
        "created_at": validation.get("created_at"),
    }


def _compact_register_links_for_ui(links: dict | None) -> dict:
    links = links or {}
    dsars = links.get("dsars") or []
    consents = links.get("consents") or []
    dpia = links.get("dpia") or []
    proofs = links.get("proofs") or []
    actions = links.get("actions") or []
    incidents = links.get("incidents") or []
    return {
        "actions": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "status": item.get("status"),
                "severity": item.get("severity"),
            }
            for item in actions[:10]
        ],
        "actions_count": len(actions),
        "incidents": [
            {
                "id": item.get("id"),
                "id_incident": item.get("id_incident"),
                "qualification": item.get("qualification"),
                "statut": item.get("statut"),
            }
            for item in incidents[:10]
        ],
        "incidents_count": len(incidents),
        "dsars": [
            {
                "id_demande": item.get("id_demande"),
                "type_droit": item.get("type_droit"),
                "nom_demandeur": item.get("nom_demandeur"),
                "statut": item.get("statut"),
            }
            for item in dsars[:10]
        ],
        "dsars_count": len(dsars),
        "consents": [
            {
                "id_consent": item.get("id_consent"),
                "nom_personne": item.get("nom_personne") or item.get("personne"),
                "statut": item.get("statut"),
            }
            for item in consents[:10]
        ],
        "consents_count": len(consents),
        "dpia": [
            {
                "id": item.get("id"),
                "nom_traitement": item.get("nom_traitement"),
                "niveau_risque": item.get("niveau_risque"),
                "statut": item.get("statut"),
            }
            for item in dpia[:10]
        ],
        "dpia_count": len(dpia),
        "proofs": proofs[:15],
        "proofs_count": len(proofs),
    }


def _compact_register_entry_for_ui(entry: dict) -> dict:
    compact = {
        key: value
        for key, value in entry.items()
        if key not in {"source_json", "operational_links", "latest_validations", "dpo_validation"}
    }
    latest = entry.get("latest_validations") or {}
    compact["latest_validations"] = {k: _compact_validation_for_ui(v) for k, v in latest.items() if v}
    compact["operational_links"] = _compact_register_links_for_ui(entry.get("operational_links"))
    compact["dpo_validation"] = _compact_validation_for_ui(entry.get("dpo_validation"))
    return compact


def persist_governance_snapshot(agent_d_output: dict):
    """Persist Agent D governance output for trend analysis."""
    if not agent_d_output:
        return None
    try:
        return crud.save_governance_snapshot(agent_d_output)
    except Exception as db_err:
        print(f"[DB] Warning: could not save governance snapshot: {db_err}")
        return None


def _scan_with_detector(filename: str, content: bytes) -> dict:
    result = _detector_scan_bytes(content, filename)
    output = result.to_dict()
    output["filename"] = output.pop("source_file")

    max_crit = output.get("criticite_globale", "faible")
    error = output.get("error")
    findings = output.get("findings", [])
    file_type = output.get("file_type", "document")
    method = output.get("extraction_method", "scan")

    if error and not findings:
        output["rgpd_impact"] = {"action_requise": False, "recommandation": f"Analyse partielle. {error}"}
    elif not findings:
        output["rgpd_impact"] = {"action_requise": False, "recommandation": f"Aucune donnee personnelle evidente detectee via {method}."}
    elif max_crit in {"critique", "elevee"}:
        output["rgpd_impact"] = {"action_requise": True, "recommandation": f"Verifier ce fichier {file_type}, limiter le partage et definir une mesure de protection adaptee."}
    else:
        output["rgpd_impact"] = {"action_requise": False, "recommandation": "Des donnees personnelles ont ete detectees. Verifier la finalite, la conservation et l'acces."}

    return output


@app.post("/scan/unstructured")
async def scan_unstructured(
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("analysis:run")),
):
    """Simple PDF/image/audio scan only.

    This endpoint intentionally does NOT call Agent A and does NOT persist
    the result in the traitement/register tables. It is used by the
    unstructured import card to preview extracted personal data only.
    """
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file.")

        result = _scan_with_detector(file.filename or "upload.bin", content)
        result["saved"] = False
        result["scan_mode"] = "preview_only"
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unstructured scan error: {str(e)}")



def _build_unstructured_traitement_input(
    scan_result: dict,
    systeme: str | None = None,
    module: str | None = None,
    linked_treatment_id: str | None = None,
) -> dict:
    findings = scan_result.get("findings") or []
    detected_fields: list[str] = []
    person_names: list[str] = []
    categories: set[str] = set()
    personal_like = False
    sensitive_like = False

    pattern_to_field = {
        "nom_prenom": "Nom complet",
        "adresse": "Adresse",
        "email": "Email",
        "id_card": "CIN",
        "nss": "NSS",
        "iban": "IBAN",
        "medical_info": "Donnees de sante",
        "date_naissance": "Date de naissance",
        "gps_coords": "Geolocalisation",
        "ip_address": "Adresse IP",
        "phone_intl": "Telephone",
    }

    for finding in findings:
        pattern = finding.get("pattern") or ""
        field_name = pattern_to_field.get(pattern, pattern or "Donnee detectee")
        if field_name not in detected_fields:
            detected_fields.append(field_name)
        ftype = (finding.get("type") or "").lower()
        criticite = (finding.get("criticite") or "").lower()
        if ftype in {"personnelle", "sensible", "critique"} or criticite in {"elevee", "critique"}:
            personal_like = True
        if ftype in {"sensible", "critique"} or criticite in {"elevee", "critique"}:
            sensitive_like = True
        if pattern == "nom_prenom" and finding.get("extrait"):
            person_names.append(str(finding.get("extrait")).strip())
        if pattern in {"nom_prenom", "date_naissance", "id_card"}:
            categories.add("identite")
        elif pattern in {"email", "phone_intl", "adresse"}:
            categories.add("contact")
        elif pattern in {"iban", "nss"}:
            categories.add("financiere")
        elif pattern == "medical_info":
            categories.add("sante")
        elif pattern in {"gps_coords", "ip_address"}:
            categories.add("localisation")

    base_filename = os.path.splitext(scan_result.get("filename") or "document_importe")[0]
    treatment_id = linked_treatment_id or f"UNSCAN-{int(time.time())}"
    system_label = (systeme or "DOCUMENT IMPORTE").strip() or "DOCUMENT IMPORTE"
    module_label = (module or scan_result.get("file_type") or "document").strip() or "document"
    relation = "personnes physiques" if person_names else "personnes a identifier"
    finalite = f"Analyse documentaire importee - {base_filename}"
    description = (
        f"Analyse automatique d'un fichier non structure {scan_result.get('file_type') or 'document'} "
        f"importe sous le nom {scan_result.get('filename') or 'document'}. "
        f"{scan_result.get('nb_findings', 0)} donnee(s) detectee(s) via {scan_result.get('extraction_method') or 'scan'}."
    )

    return {
        "id_traitement": treatment_id,
        "nom_traitement": f"Analyse documentaire - {base_filename}",
        "systeme": system_label,
        "module": module_label,
        "responsable": "DPO TIM Consulting",
        "description": description,
        "donnees_collectees": detected_fields,
        "categories_donnees": sorted(categories),
        "donnees_sensibles": sensitive_like,
        "finalite": finalite,
        "finalite_definie": True,
        "base_legale": False,
        "consentement_valide": False,
        "consentement_retire": False,
        "type_relation": "document_importe",
        "personnes_concernees": person_names if person_names else [relation],
        "destinataires": [],
        "transfert_etranger": False,
        "garanties_specifiques": False,
        "duree_conservation": "A definir",
        "duree_conservation_definie": False,
        "duree_depassee": False,
        "donnees_minimisees": False if len(detected_fields) > 2 else True,
        "respect_vie_privee": True,
        "mesures_securite": ["controle_acces"] if personal_like else [],
        "chiffrement_actif": False,
        "tests_securite_reguliers": False,
        "controle_acces_physique": False,
        "privacy_by_design": False,
        "privacy_by_default": False,
        "processus_droits_personnes": False,
        "information_personnes_concernees": False,
        "modalites_droits_accessibles": False,
        "collecte_indirecte": True,
        "information_collecte_indirecte": False,
        "consentement_collecte_indirecte": False,
        "information_transfert_fournie": False,
        "opposition_ignoree": False,
        "decision_automatisee": False,
        "garanties_decision_auto": False,
        "dsar_hors_delai": False,
        "violation_donnees": False,
        "notification_72h": False,
        "notification_personnes": False,
        "violation_documentee": False,
        "risque_eleve": sensitive_like,
        "aipd_realisee": False,
        "mise_en_production": False,
        "analyse_risque_avant_production": False,
        "consultation_autorite_si_risque_residuel": True,
        "contrat_sous_traitance": False,
        "garanties_sous_traitant": False,
        "registre_traitement": False,
        "traitement_grande_echelle": False,
        "dpo_designe": True,
        "missions_dpo_garanties": True,
        "politique_protection_donnees": False,
        "revue_periodique_mesures": False,
        "declaration_inpdp": True,
        "fast_source_analysis": True,
        "unstructured_scan_results": [scan_result],
        "imported_document_name": scan_result.get("filename"),
        "imported_document_type": scan_result.get("file_type"),
    }


CORRECTION_PROOF_HINTS = {
    "consent": ["consentement", "consent", "autorise", "autorisation", "opt in", "opt-in", "acceptation"],
    "contract": ["contrat", "clause", "bon de commande", "commande", "client signe", "client signé", "agreement", "signature"],
    "legal_basis": ["base legale", "base légale", "legal basis", "article 6", "licite", "licéité", "interet legitime", "intérêt légitime"],
    "retention": ["conservation", "retention", "archivage", "suppression", "purge", "duree", "durée", "policy"],
    "security": ["securite", "sécurité", "chiffrement", "encryption", "aes", "tls", "journalisation", "logs", "audit", "mfa", "acces", "accès"],
    "identity": ["cin", "passeport", "passport", "piece d identite", "pièce d'identité", "identity", "identite", "identité"],
    "dsar_procedure": ["dsar", "demande de droit", "droit d acces", "droit d'accès", "rectification", "effacement", "opposition", "portabilite", "portabilité"],
    "processor_contract": ["sous traitant", "sous-traitant", "processor", "dpa", "clauses sous-traitant"],
    "transfer_safeguard": ["transfert", "transfer", "scc", "clauses contractuelles types", "uk", "royaume uni", "brexit"],
}

CORRECTION_CATEGORY_HINTS = {
    "employees": ["employees", "employee", "employes", "employés", "personnel", "rh", "hr"],
    "clients": ["clients", "customers", "customer", "prospects", "prospect"],
    "suppliers_contacts": ["suppliers", "supplier", "fournisseurs", "fournisseur"],
    "newsletter_subscribers": ["newsletter", "marketing", "campagne", "campaign", "subscribers", "abonnes", "abonnés"],
    "technicians": ["technicians", "technicien", "techniciens", "maintenance teams", "maintenance"],
}

CORRECTION_SCOPE_LABELS = {
    "person": "personne",
    "category": "categorie",
    "treatment": "traitement",
    "unknown": "indeterminee",
}

CORRECTION_PROOF_LABELS = {
    "legal_basis": "base legale",
    "contract": "contrat / clause",
    "consent": "consentement",
    "retention": "conservation",
    "security": "securite",
    "identity": "identite / justificatif",
    "dsar_procedure": "procedure droits",
    "processor_contract": "sous-traitant",
    "transfer_safeguard": "transfert / garanties",
    "other": "autre",
}

CORRECTION_CATEGORY_LABELS = {
    "employees": "employes",
    "clients": "clients",
    "suppliers_contacts": "contacts fournisseurs",
    "newsletter_subscribers": "abonnes newsletter",
    "technicians": "techniciens",
}


def _safe_json_loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not str(value or "").strip():
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _normalise_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("’", "'")
    text = re.sub(r"[^\w\s'/-]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _merge_correction_findings(results: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        for finding in result.get("findings") or []:
            key = (str(finding.get("pattern") or ""), str(finding.get("extrait") or ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append(finding)
    return merged


def _extract_covered_entities(description: str, findings: list[dict], scope_hint: str | None = None) -> list[str]:
    covered: list[str] = []
    if scope_hint == "category":
        normalised = _normalise_text(description)
        for label, keywords in CORRECTION_CATEGORY_HINTS.items():
            if any(keyword in normalised for keyword in keywords):
                covered.append(label)
    for finding in findings:
        if str(finding.get("pattern") or "") == "nom_prenom":
            value = str(finding.get("extrait") or "").strip()
            if value and value not in covered:
                covered.append(value)
    return covered[:12]


def _detect_proof_type(description: str, findings: list[dict], requested: str | None = None) -> str:
    if requested and requested != "auto":
        return requested
    normalised = _normalise_text(description)
    scores: dict[str, int] = {key: 0 for key in CORRECTION_PROOF_HINTS}
    for key, keywords in CORRECTION_PROOF_HINTS.items():
        scores[key] += sum(1 for keyword in keywords if keyword in normalised)
    finding_patterns = {str(f.get("pattern") or "") for f in findings}
    if "iban" in finding_patterns:
        scores["security"] += 2
    if "medical_info" in finding_patterns:
        scores["security"] += 2
    if "id_card" in finding_patterns:
        scores["identity"] += 2
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] <= 0:
        return "other"
    return ranked[0][0]


def _infer_scope(description: str, findings: list[dict], proof_type: str, requested_scope: str = "treatment") -> tuple[str, list[str]]:
    normalised = _normalise_text(description)
    covered = _extract_covered_entities(description, findings)
    category_matches = []
    for label, keywords in CORRECTION_CATEGORY_HINTS.items():
        if any(keyword in normalised for keyword in keywords):
            category_matches.append(label)
    if category_matches:
        return "category", category_matches[:6]
    if proof_type in {"consent", "identity", "contract"} and len([c for c in covered if c]) == 1:
        return "person", covered[:1]
    if requested_scope == "treatment":
        return "treatment", []
    return requested_scope or "unknown", covered[:6]


def _resolve_scope_selection(
    requested_scope: str,
    scope_target: str | None,
    scope_target_label: str | None,
    inferred_scope: str,
    inferred_entities: list[str],
) -> tuple[str, list[str]]:
    if requested_scope == "person" and str(scope_target_label or scope_target or "").strip():
        return "person", [str(scope_target_label or scope_target).strip()]
    if requested_scope == "category" and str(scope_target_label or scope_target or "").strip():
        return "category", [str(scope_target_label or scope_target).strip()]
    return inferred_scope, inferred_entities[:6]


def _estimate_correction_confidence(
    description: str,
    file_scan: dict | None,
    description_scan: dict | None,
    proof_type: str,
    scope_detected: str,
    covered_entities: list[str],
) -> float:
    score = 0.28
    if str(description or "").strip():
        score += 0.15
    if file_scan and (file_scan.get("nb_findings") or file_scan.get("file_type")):
        score += 0.20
    if description_scan and (description_scan.get("nb_findings") or description_scan.get("extraction_method")):
        score += 0.12
    if proof_type != "other":
        score += 0.15
    if scope_detected != "unknown":
        score += 0.10
    if covered_entities:
        score += 0.08
    return round(min(score, 0.96), 3)


def _build_remaining_gaps(
    register_snapshot: dict | None,
    requested_scope: str,
    scope_detected: str,
    proof_type: str,
    covered_entities: list[str],
    scope_target_label: str | None = None,
    inferred_scope: str | None = None,
    inferred_entities: list[str] | None = None,
) -> list[str]:
    snapshot = register_snapshot or {}
    status = snapshot.get("register_status") or {}
    gaps: list[str] = []
    label_map = {**CORRECTION_CATEGORY_LABELS}

    if requested_scope == "person":
        label = scope_target_label or (covered_entities[0] if covered_entities else "la personne selectionnee")
        gaps.append(f"La preuve couvre {label} uniquement, pas les autres personnes ni l'ensemble du traitement.")
        if inferred_scope == "person" and inferred_entities:
            inferred_label = inferred_entities[0]
            if _normalise_text(inferred_label) != _normalise_text(label):
                gaps.append(f"La lecture automatique mentionne plutot {inferred_label} que {label}.")
    elif requested_scope == "category":
        label = scope_target_label or ", ".join(covered_entities[:3]) or "la categorie selectionnee"
        gaps.append(f"La preuve couvre la categorie {label}, pas l'ensemble du traitement.")
        if inferred_scope == "category" and inferred_entities:
            normalized_inferred = {_normalise_text(item) for item in inferred_entities}
            if _normalise_text(label) not in normalized_inferred:
                inferred_label = ", ".join(inferred_entities[:3])
                gaps.append(f"La lecture automatique semble viser plutot {inferred_label}.")
    elif requested_scope == "treatment" and scope_detected == "person":
        label = covered_entities[0] if covered_entities else "une personne"
        gaps.append(f"La preuve semble couvrir {label} uniquement, pas l'ensemble du traitement.")
    elif requested_scope == "treatment" and scope_detected == "category":
        label = ", ".join(label_map.get(item, item) for item in covered_entities[:3]) if covered_entities else "une categorie"
        gaps.append(f"La preuve semble couvrir {label} uniquement, pas l'ensemble du traitement.")

    legal_basis_status = _normalise_text(status.get("legal_basis") or snapshot.get("base_legale"))
    if proof_type not in {"legal_basis", "contract", "consent"} and (
        not legal_basis_status or "defin" in legal_basis_status or "confirm" in legal_basis_status or "valider" in legal_basis_status
    ):
        gaps.append("La base légale du traitement reste à documenter ou confirmer.")

    dpia_status = _normalise_text(status.get("dpia"))
    if proof_type != "security" and ("impact" in dpia_status or "requis" in dpia_status or "prioritaire" in dpia_status):
        gaps.append("L'arbitrage Étude d'impact reste distinct de cette preuve.")

    if proof_type != "security":
        gaps.append("Les mesures de sécurité et leurs tests restent à confirmer séparément si aucune preuve dédiée n'est fournie.")
    if proof_type != "retention":
        gaps.append("La durée de conservation n'est pas couverte par cette preuve sauf mention explicite.")

    # De-duplicate while preserving order.
    deduped: list[str] = []
    seen: set[str] = set()
    for gap in gaps:
        key = gap.strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(gap)
    return deduped[:6]


def _compact_person_scope_item(item: dict, fallback_index: int = 0) -> dict | None:
    if not isinstance(item, dict):
        return None
    if not item.get("is_personne_physique"):
        return None
    label = str(item.get("display_name") or "").strip()
    if not label:
        return None
    personal_fields = [str(x).strip() for x in (item.get("personal_fields") or []) if str(x).strip()]
    sensitive_fields = [str(x).strip() for x in (item.get("sensitive_fields") or []) if str(x).strip()]
    allowed_fields = set((personal_fields + sensitive_fields)[:12])
    preview_values = item.get("preview_values") or {}
    preview_subset: dict[str, str] = {}
    for key, value in preview_values.items():
        if key in allowed_fields and len(preview_subset) < 8:
            preview_subset[str(key)] = str(value)
    return {
        "id": f"person::{item.get('record_index', fallback_index)}",
        "label": label,
        "record_index": item.get("record_index", fallback_index),
        "personal_fields": personal_fields[:12],
        "sensitive_fields": sensitive_fields[:12],
        "preview_values": preview_subset,
    }


def _build_correction_context_payload(treatment_row: dict) -> dict:
    traitement_input = _safe_json_loads(treatment_row.get("input_json"), {})
    traitement_output = _safe_json_loads(treatment_row.get("output_json"), {})
    intelligence = traitement_output.get("intelligence") or {}
    detection = (
        intelligence.get("qalitas_detected_fields")
        or intelligence.get("gmao_detected_fields")
        or {}
    )
    raw_categories = detection.get("person_categories") or []
    display_categories = detection.get("person_categories_display") or raw_categories
    categories: list[dict] = []
    seen_categories: set[str] = set()
    for idx, label in enumerate(display_categories):
        raw_value = raw_categories[idx] if idx < len(raw_categories) else label
        category_id = str(raw_value or label or "").strip()
        category_label = str(label or raw_value or "").strip()
        key = _normalise_text(category_id or category_label)
        if not key or key in seen_categories:
            continue
        seen_categories.add(key)
        categories.append({
            "id": category_id or category_label,
            "label": category_label or category_id,
        })

    persons: list[dict] = []
    seen_people: set[str] = set()
    for idx, item in enumerate(detection.get("records_details") or []):
        compact_item = _compact_person_scope_item(item, fallback_index=idx)
        if not compact_item:
            continue
        dedupe_key = _normalise_text(compact_item["label"])
        if dedupe_key in seen_people:
            continue
        seen_people.add(dedupe_key)
        persons.append(compact_item)

    return {
        "treatment": {
            "source_analysis_id": treatment_row.get("id"),
            "id_traitement": traitement_input.get("id_traitement") or treatment_row.get("id_traitement"),
            "nom_traitement": traitement_input.get("nom_traitement") or treatment_row.get("nom_traitement"),
            "systeme": traitement_input.get("systeme") or treatment_row.get("systeme"),
            "module": detection.get("module") or intelligence.get("module"),
            "finalite": traitement_input.get("finalite") or treatment_row.get("finalite"),
            "base_legale": traitement_input.get("base_legale") or treatment_row.get("base_legale"),
            "responsable": traitement_input.get("responsable") or treatment_row.get("responsable"),
        },
        "scopes": {
            "treatment": {
                "label": "Traitement complet",
                "description": "Preuve globale applicable a tout le traitement (politique, procedure, clause commune, registre, mesure transversale).",
            },
            "categories": categories[:40],
            "persons": persons[:200],
        },
    }


@app.get("/treatments/corrections/context")
def treatment_correction_context(
    treatment_id: str | None = None,
    source_analysis_id: int | None = None,
    user: dict = Depends(require_any_permission("register:view", "register:view_limited")),
):
    row = None
    if source_analysis_id:
        row = crud.get_treatment_by_row_id(source_analysis_id)
    if not row and treatment_id:
        row = crud.get_treatment_by_id(treatment_id)
    if not row:
        raise HTTPException(status_code=404, detail="Traitement introuvable pour la correction.")
    return _build_correction_context_payload(row)


@app.post("/treatments/corrections/analyse")
async def analyse_treatment_correction(
    treatment_id: str = Form(...),
    treatment_label: str = Form(...),
    source_system: str | None = Form(None),
    source_module: str | None = Form(None),
    description: str | None = Form(None),
    proof_type_hint: str | None = Form(None),
    scope_requested: str = Form("treatment"),
    scope_target: str | None = Form(None),
    scope_target_label: str | None = Form(None),
    register_snapshot: str | None = Form(None),
    file: UploadFile | None = File(None),
    user: dict = Depends(require_permission("analysis:run")),
):
    try:
        if not str(description or "").strip() and not file:
            raise HTTPException(status_code=400, detail="Ajoutez une description ou un fichier preuve.")

        parsed_snapshot: dict = {}
        if str(register_snapshot or "").strip():
            try:
                parsed_snapshot = json.loads(register_snapshot)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Snapshot registre invalide.")

        file_scan = None
        if file:
            content = await file.read()
            if not content:
                raise HTTPException(status_code=400, detail="Fichier preuve vide.")
            file_scan = _scan_with_detector(file.filename or "correction.bin", content)

        description_scan = None
        if str(description or "").strip():
            description_scan = _scan_with_detector(
                "correction_note.txt",
                str(description or "").encode("utf-8", errors="ignore"),
            )

        findings = _merge_correction_findings([x for x in [file_scan, description_scan] if x])
        proof_type = _detect_proof_type(description or "", findings, requested=proof_type_hint)
        inferred_scope, inferred_entities = _infer_scope(
            description or "",
            findings,
            proof_type,
            requested_scope=scope_requested,
        )
        scope_detected, covered_entities = _resolve_scope_selection(
            requested_scope,
            scope_target,
            scope_target_label,
            inferred_scope,
            inferred_entities,
        )
        confidence = _estimate_correction_confidence(
            description or "",
            file_scan,
            description_scan,
            proof_type,
            scope_detected,
            covered_entities,
        )
        remaining_gaps = _build_remaining_gaps(
            parsed_snapshot,
            requested_scope=scope_requested,
            scope_detected=scope_detected,
            proof_type=proof_type,
            covered_entities=covered_entities,
            scope_target_label=scope_target_label,
            inferred_scope=inferred_scope,
            inferred_entities=inferred_entities,
        )

        nlp_hint = None
        if str(description or "").strip():
            try:
                nlp_hint = predict_assistant_intent(description)
            except Exception:
                nlp_hint = None

        summary_bits = [
            f"Portee detectee: {CORRECTION_SCOPE_LABELS.get(scope_detected, scope_detected)}.",
            f"Type de preuve: {CORRECTION_PROOF_LABELS.get(proof_type, proof_type)}.",
        ]
        if covered_entities:
            summary_bits.append(
                "Couverture identifiee: "
                + ", ".join(CORRECTION_CATEGORY_LABELS.get(item, item) for item in covered_entities[:4])
                + "."
            )
        if remaining_gaps:
            summary_bits.append(f"Gaps restants: {remaining_gaps[0]}")

        fallback = {
            "scope_detected": scope_detected,
            "proof_type": proof_type,
            "covered_entities": covered_entities,
            "confidence": confidence,
            "remaining_gaps": remaining_gaps,
            "scope_target": scope_target,
            "scope_target_label": scope_target_label,
            "inferred_scope": inferred_scope,
            "inferred_entities": inferred_entities,
            "file_scan": file_scan,
            "description_scan": description_scan,
            "description_intent": nlp_hint,
            "matched_findings": findings[:20],
            "requested_scope": scope_requested,
            "requested_proof_type": proof_type_hint or "auto",
            "evidence_summary": {
                "treatment_id": treatment_id,
                "treatment_label": treatment_label,
                "source_system": source_system or "",
                "source_module": source_module or "",
                "summary_text": " ".join(summary_bits),
            },
        }
        return _finalize_dsar_type(data.texte, fallback)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Treatment correction analysis error: {str(e)}")


@app.post("/scan/unstructured/analyse")
async def scan_unstructured_analyse(
    file: UploadFile = File(...),
    linked_treatment_id: str | None = Form(None),
    systeme: str | None = Form(None),
    module: str | None = Form(None),
    source_analysis_id: int | None = Form(None),
    user: dict = Depends(require_permission("analysis:run")),
):
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file.")

        scan_result = _scan_with_detector(file.filename or "upload.bin", content)
        try:
            scan_id = crud.save_unstructured_scan(
                result=scan_result,
                linked_treatment_id=linked_treatment_id,
                systeme=systeme,
                module=module,
                source_analysis_id=source_analysis_id,
            )
            scan_result["scan_id"] = scan_id
            scan_result["saved"] = True
        except Exception as db_err:
            scan_result["saved"] = False
            scan_result["save_error"] = str(db_err)

        traitement_input = _build_unstructured_traitement_input(
            scan_result,
            systeme=systeme,
            module=module,
            linked_treatment_id=linked_treatment_id,
        )
        agent_a_output = run_agent_a(traitement_input)
        analysis_id = crud.save_treatment(traitement_input, agent_a_output)
        crud.save_audit_event(
            user,
            "analysis.run",
            "unstructured",
            module or (scan_result.get("file_type") or "document"),
            {
                "filename": scan_result.get("filename"),
                "records": 1,
                "linked_treatment_id": traitement_input.get("id_traitement"),
            },
        )
        fallback = {
            "scan_result": scan_result,
            "traitement_input": traitement_input,
            "agent_a": agent_a_output,
            "analysis_id": analysis_id,
            "records_analysed": 1,
            "analysis_mode": "unstructured_agent_a",
            "statut": "completed",
        }
        return fallback
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unstructured analysis error: {str(e)}")


class Traitement(BaseModel):
    # Identification
    id_traitement: Optional[str] = None
    nom_traitement: Optional[str] = None
    systeme: Optional[str] = None
    responsable: Optional[str] = None
    # Intelligence: natural language description for LLM auto-inference
    # If provided, Agent A will automatically infer missing boolean fields
    description: Optional[str] = None
    # Donnees
    donnees_collectees: Optional[List[str]] = []
    categories_donnees: Optional[List[str]] = []
    donnees_sensibles: bool = False
    donnees_penales: bool = False
    donnees_exactes: bool = True
    # Finalite et base legale
    finalite: Optional[str] = None
    finalite_definie: Optional[bool] = None
    base_legale: Union[str, bool] = False
    # Consentement
    consentement_valide: bool = False
    consentement_retire: bool = False
    consentement_conditionne_service: bool = False
    service_conditionne_consentement: bool = False
    autorisation_donnees_penales: bool = False
    # Personnes et destinataires
    type_relation: Optional[str] = None
    personnes_concernees: Optional[List[str]] = []
    destinataires: Optional[List[str]] = []
    # Transferts
    transfert_etranger: bool = False
    garanties_specifiques: bool = False
    adequation_ou_garanties_documentees: bool = False
    autorisation_inpdp_transfert: bool = False
    niveau_protection_adequat: bool = False
    risque_securite_nationale: bool = False
    # Conservation
    duree_conservation: Optional[str] = None
    duree_conservation_definie: bool = False
    duree_depassee: bool = False
    # Minimisation et vie privee
    donnees_minimisees: bool = False
    respect_vie_privee: bool = True
    # Securite
    mesures_securite: Optional[List[str]] = []
    chiffrement_actif: bool = False
    tests_securite_reguliers: bool = False
    controle_acces_physique: bool = False
    confidentialite_post_traitement: bool = True
    # Privacy by design
    privacy_by_design: bool = False
    privacy_by_default: bool = False
    # Droits personnes
    processus_droits_personnes: bool = False
    information_personnes_concernees: bool = False
    modalites_droits_accessibles: bool = False
    collecte_indirecte: bool = False
    information_collecte_indirecte: bool = False
    consentement_collecte_indirecte: bool = False
    information_transfert_fournie: bool = False
    opposition_ignoree: bool = False
    decision_automatisee: bool = False
    garanties_decision_auto: bool = False
    dsar_hors_delai: bool = False
    # Violations
    violation_donnees: bool = False
    notification_72h: bool = False
    notification_personnes: bool = False
    violation_documentee: bool = False
    # AIPD
    risque_eleve: bool = False
    aipd_realisee: bool = False
    mise_en_production: bool = False
    analyse_risque_avant_production: bool = False
    consultation_autorite_si_risque_residuel: bool = True
    # Sous-traitants
    contrat_sous_traitance: bool = False
    garanties_sous_traitant: bool = False
    registre_traitement: bool = False
    # DPO
    traitement_grande_echelle: bool = False
    dpo_designe: bool = False
    missions_dpo_garanties: bool = False
    # Politiques
    politique_protection_donnees: bool = False
    revue_periodique_mesures: bool = False
    declaration_inpdp: bool = True

class Incident(BaseModel):
    id_incident: Optional[str] = "INC-001"
    date_detection: Optional[str] = None
    type_incident: Optional[str] = None
    description: Optional[str] = None
    donnees_affectees: Optional[List[str]] = []
    nombre_personnes_affectees: int = 0
    gravite_incident: int = 1
    donnees_sensibles_impliquees: bool = False
    donnees_chiffrees: bool = False

class TraitementAvecIncident(BaseModel):
    traitement: Traitement
    incident: Optional[Incident] = None
    agent_a: Optional[Dict[str, Any]] = None

class DemandeDSAR(BaseModel):
    id_demande: Optional[str] = "DSAR-001"
    nom_demandeur: Optional[str] = None
    date_reception: Optional[str] = None
    type_droit: str
    systeme_concerne: Optional[str] = None
    donnees_concernees: Optional[List[str]] = []
    identite_verifiee: bool = False
    demandes_precedentes_30j: int = 0
    base_legale_traitement: Optional[str] = None
    obligation_legale_conservation: bool = False


class MLTextRequest(BaseModel):
    texte: str


class AssistantChatRequest(BaseModel):
    question: str
    history: Optional[list[str]] = None


class MLFieldRequest(BaseModel):
    field_name: str
    module: Optional[str] = None
    source_system: Optional[str] = None


class MLFieldFeedbackRequest(BaseModel):
    field_name: str
    corrected_label: str
    predicted_label: Optional[str] = None
    module: Optional[str] = None
    source_system: Optional[str] = None
    validator: Optional[str] = "Y. Benjemaa - DPO"
    justification: Optional[str] = None
    retrain: bool = True


class DSARExecutionRequest(BaseModel):
    id_demande: str
    executor: str = "DPO"
    mode_execution: str = "safe_log"  # safe_log / platform_apply / manual / approved
    rectification_values: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None

class AnalyseComplete(BaseModel):
    traitement: Traitement
    incident: Optional[Incident] = None
    demande_dsar: Optional[DemandeDSAR] = None


class GovernanceFromAgents(BaseModel):
    agent_a: Dict[str, Any]
    agent_b: Optional[Dict[str, Any]] = None
    agent_c: Optional[Dict[str, Any]] = None


class IncidentDeclarationPayload(BaseModel):
    incident: Incident
    traitement: Optional[Traitement] = None
    agent_a: Optional[Dict[str, Any]] = None


class RealtimeRunRequest(BaseModel):
    sources: Optional[List[str]] = None
    modules: Optional[List[str]] = None
    limit: Optional[int] = 100


class RealtimeStartRequest(RealtimeRunRequest):
    interval_seconds: Optional[int] = 300

@app.get("/")
def root():
    return {"message": "RGPD Multi-Agent Platform - Running"}


# ==============================================================================
# QALITAS CONNECTOR ENDPOINTS
# ==============================================================================

@app.get("/qalitas/modules")
def qalitas_modules(user: dict = Depends(require_permission("analysis:view"))):
    if DEMO_MODE_ENABLED:
        return {"modules": _demo_module_options("qalitas")}
    return {"modules": [{"id": k, "label": v} for k, v in MODULE_LABELS.items()]}


def _fetch_qalitas_bundle(connector: QalitasConnector, module: str, limit: int):
    cached = _get_cached_bundle("qalitas", module, limit)
    if cached is not None:
        return cached

    _ensure_demo_module_allowed("qalitas", module)

    if module != "all":
        records = connector.fetch(module)
        if limit > 0:
            records = records[:limit]
        records = [
            {**record, "_source_module": module}
            if isinstance(record, dict) else record
            for record in records
        ]
        bundle = {
            "module": module,
            "module_label": MODULE_LABELS.get(module, module),
            "records": records,
            "modules": [{
                "id": module,
                "label": MODULE_LABELS.get(module, module),
                "records": len(records),
            }],
            "module_errors": [],
            "fetch_note": _append_demo_note("", "qalitas"),
            "read_limit_per_module": limit,
            "cache_hit": False,
            "cache_age_seconds": 0,
        }
        _set_cached_bundle("qalitas", module, limit, bundle)
        return bundle

    combined_records = []
    modules_summary = []
    module_errors = []
    module_records_map = {}
    effective_limit = limit
    fetch_note = ""
    if limit <= 0 or limit > QALITAS_ALL_MODULE_LIMIT_CAP:
        effective_limit = QALITAS_ALL_MODULE_LIMIT_CAP
        fetch_note = (
            f"Lecture groupée accélérée : maximum {QALITAS_ALL_MODULE_LIMIT_CAP} "
            "enregistrements lus par module pour éviter les attentes trop longues."
        )

    for module_id in (_demo_modules_for_source("qalitas") if DEMO_MODE_ENABLED else MODULE_ENDPOINTS.keys()):
        try:
            module_records = connector.fetch(
                module_id,
                timeout=QALITAS_ALL_MODULE_TIMEOUT,
                retry_timeout=QALITAS_ALL_MODULE_RETRY_TIMEOUT,
            )
            if effective_limit > 0:
                module_records = module_records[:effective_limit]
            module_records = [
                {**record, "_source_module": module_id}
                if isinstance(record, dict) else record
                for record in module_records
            ]
            combined_records.extend(module_records)
            module_records_map[module_id] = module_records
            modules_summary.append({
                "id": module_id,
                "label": MODULE_LABELS.get(module_id, module_id),
                "records": len(module_records),
            })
        except Exception as exc:
            module_errors.append({
                "id": module_id,
                "label": MODULE_LABELS.get(module_id, module_id),
                "error": str(exc),
            })

    bundle = {
        "module": "all",
        "module_label": "Tous les modules",
        "records": combined_records,
        "modules": modules_summary,
        "module_errors": module_errors,
        "fetch_note": _append_demo_note(fetch_note, "qalitas"),
        "read_limit_per_module": effective_limit,
        "_module_records": module_records_map,
        "cache_hit": False,
        "cache_age_seconds": 0,
    }
    _set_cached_bundle("qalitas", module, limit, bundle)
    return bundle


def _build_qalitas_traitement_input(bundle: dict) -> dict:
    """Normalize QALITAS fetched data into the treatment payload used by Agent A."""
    return {
        "qalitas_module": bundle["module"],
        "qalitas_records": bundle["records"],
        "qalitas_modules": bundle["modules"],
        "qalitas_module_errors": bundle["module_errors"],
        "fast_source_analysis": True,
    }


def _persist_qalitas_agent_a_result(traitement_input: dict, agent_a_output: dict):
    """Persist the lightweight QALITAS analysis row produced by Agent A."""
    try:
        return crud.save_treatment(traitement_input, agent_a_output)
    except Exception as db_err:
        print(f"[DB] Warning: {db_err}")
        return None


def _run_qalitas_fast_analysis(
    module: str,
    limit: int,
    background_tasks: BackgroundTasks | None,
    user: dict,
):
    request_started = time.perf_counter()
    login_started = time.perf_counter()
    connector = get_qalitas_connector()
    login_ms = _elapsed_ms(login_started)
    fetch_started = time.perf_counter()
    bundle = _fetch_qalitas_bundle(connector, module, limit)
    fetch_ms = _elapsed_ms(fetch_started)
    records = bundle["records"]

    traitement_input = _build_qalitas_traitement_input(bundle)
    agent_started = time.perf_counter()
    agent_a = run_agent_a(traitement_input)
    agent_a_ms = _elapsed_ms(agent_started)
    save_started = time.perf_counter()
    analysis_id = _persist_qalitas_agent_a_result(traitement_input, agent_a)
    save_ms = _elapsed_ms(save_started)
    if background_tasks is not None:
        background_tasks.add_task(
            _finalize_fast_analysis_background,
            user,
            "qalitas",
            bundle["module"],
            limit,
            len(records),
            traitement_input,
            agent_a,
            analysis_id,
        )
    else:
        _finalize_fast_analysis_background(
            user,
            "qalitas",
            bundle["module"],
            limit,
            len(records),
            traitement_input,
            agent_a,
            analysis_id,
        )
    timings = {
        "login_ms": login_ms,
        "fetch_ms": fetch_ms,
        "agent_a_ms": agent_a_ms,
        "save_ms": save_ms,
        "total_ms": _elapsed_ms(request_started),
    }
    _log_analysis_timings(
        "qalitas",
        bundle["module"],
        timings,
        len(records),
        note=bundle.get("fetch_note") or ("cache" if bundle.get("cache_hit") else ""),
    )

    return {
        "module": bundle["module"],
        "module_label": bundle["module_label"],
        "modules": bundle["modules"],
        "source_summary": _build_grouped_source_summary("qalitas", bundle) if bundle["module"] == "all" else None,
        "module_errors": bundle["module_errors"],
        "fetch_note": bundle.get("fetch_note", ""),
        "read_limit_per_module": bundle.get("read_limit_per_module", limit),
        "cache_hit": bool(bundle.get("cache_hit")),
        "cache_age_seconds": bundle.get("cache_age_seconds", 0),
        "records_analysed": len(records),
        "analysis_mode": "fast_agent_a",
        "statut": "completed",
        "erreurs": [],
        "agent_a": agent_a,
        "agent_b": None,
        "agent_c": None,
        "agent_d": None,
        "persistence": {"analysis_id": analysis_id, "status": "queued"},
        "performance": timings,
    }


@app.post("/qalitas/fetch/{module}")
def qalitas_fetch(module: str, limit: int = 100, user: dict = Depends(require_permission("analysis:run"))):
    request_started = time.perf_counter()
    try:
        from agents.agent_a import detect_qalitas_fields
        login_started = time.perf_counter()
        connector = get_qalitas_connector()
        login_ms = _elapsed_ms(login_started)
        fetch_started = time.perf_counter()
        bundle = _fetch_qalitas_bundle(connector, module, limit)
        fetch_ms = _elapsed_ms(fetch_started)
        records = bundle["records"]
        if not records and bundle["module_errors"]:
            raise HTTPException(status_code=502, detail=f"Aucun module exploitable pour '{module}'")
        detect_started = time.perf_counter()
        detection = detect_qalitas_fields(records, module)
        detect_ms = _elapsed_ms(detect_started)
        crud.save_audit_event(
            user,
            "analysis.fetch",
            "qalitas",
            module,
            {"limit": limit, "records": len(records)},
        )
        _log_analysis_timings(
            source="qalitas.fetch",
            module=module,
            timings={
                "login_ms": login_ms,
                "fetch_ms": fetch_ms,
                "agent_a_ms": detect_ms,
                "save_ms": 0,
                "total_ms": _elapsed_ms(request_started),
            },
            records_count=len(records),
            note=bundle.get("fetch_note") or ("cache" if bundle.get("cache_hit") else ""),
        )
        fallback = {
            "module": bundle["module"],
            "module_label": bundle["module_label"],
            "modules": bundle["modules"],
            "source_summary": _build_grouped_source_summary("qalitas", bundle) if bundle["module"] == "all" else None,
            "module_errors": bundle["module_errors"],
            "fetch_note": bundle.get("fetch_note", ""),
            "read_limit_per_module": bundle.get("read_limit_per_module", limit),
            "cache_hit": bool(bundle.get("cache_hit")),
            "cache_age_seconds": bundle.get("cache_age_seconds", 0),
            "record_count": detection["record_count"],
            "personal_fields": detection["personal_fields"],
            "sensitive_fields": detection["sensitive_fields"],
            "all_fields": detection["all_fields"],
            "records_with_personal_data": detection["records_with_personal_data"],
            "affected_clients": detection["affected_clients"],
            "affected_clients_count": detection["affected_clients_count"],
            "named_records": detection["named_records"],
            "named_records_count": detection["named_records_count"],
            "records_details": detection["records_details"],
            "physical_person_records_count": detection.get("physical_person_records_count", 0),
            "person_categories": detection.get("person_categories", []),
            "person_categories_display": detection.get("person_categories_display", []),
            "has_individual_names": detection["has_individual_names"],
            "sample_record": detection["sample_record"],
            "records_fetched": len(records)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"QALITAS fetch error: {str(e)}")


@app.post("/qalitas/analyse/{module}")
def qalitas_analyse(
    module: str,
    background_tasks: BackgroundTasks,
    limit: int = 200,
    user: dict = Depends(require_permission("analysis:run")),
):
    """
    Fast QALITAS analysis path used by the main UI button.
    It focuses on Agent A (Q1/Q2/Q3) so the response stays practical in demos.
    """
    request_started = time.perf_counter()
    try:
        return _run_qalitas_fast_analysis(module, limit, background_tasks, user)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis error: {str(e)}")


@app.get("/qalitas/status")
def qalitas_status(user: dict = Depends(require_any_permission("connectors:configure", "analysis:run"))):
    from qalitas.connector import QALITAS_USER, QALITAS_PASSWORD, QALITAS_BASE_URL
    return {
        "configured": bool(QALITAS_USER and QALITAS_PASSWORD),
        "user": QALITAS_USER if QALITAS_USER else "not set",
        "url": QALITAS_BASE_URL,
        "modules_available": _demo_modules_for_source("qalitas") if DEMO_MODE_ENABLED else list(MODULE_ENDPOINTS.keys()),
        "demo_mode": DEMO_MODE_ENABLED,
    }


# ==============================================================================
# GMAO CONNECTOR ENDPOINTS
# ==============================================================================

@app.get("/gmao/modules")
def gmao_modules(user: dict = Depends(require_permission("analysis:view"))):
    if DEMO_MODE_ENABLED:
        modules = _demo_module_options("gmao")
    else:
        modules = [{"id": "all", "label": "Toute la source (recommandée)"}]
        modules.extend({"id": k, "label": v} for k, v in GMAO_MODULE_LABELS.items())
    return {"modules": modules}


def _fetch_gmao_bundle(connector: GmaoConnector, module: str, limit: int):
    cached = _get_cached_bundle("gmao", module, limit)
    if cached is not None:
        return cached

    _ensure_demo_module_allowed("gmao", module)

    if module != "all":
        records = connector.fetch(module)
        if limit > 0:
            records = records[:limit]
        records = [
            {**record, "_source_module": module}
            if isinstance(record, dict) else record
            for record in records
        ]
        bundle = {
            "module": module,
            "module_label": GMAO_MODULE_LABELS.get(module, module),
            "records": records,
            "modules": [{
                "id": module,
                "label": GMAO_MODULE_LABELS.get(module, module),
                "records": len(records),
            }],
            "module_errors": [],
            "fetch_note": _append_demo_note("", "gmao"),
            "read_limit_per_module": limit,
            "cache_hit": False,
            "cache_age_seconds": 0,
        }
        _set_cached_bundle("gmao", module, limit, bundle)
        return bundle

    combined_records = []
    modules_summary = []
    module_errors = []
    module_records_map = {}
    effective_limit = limit
    fetch_note = ""
    if limit <= 0 or limit > GMAO_ALL_MODULE_LIMIT_CAP:
        effective_limit = GMAO_ALL_MODULE_LIMIT_CAP
        fetch_note = (
            f"Lecture groupée accélérée : maximum {GMAO_ALL_MODULE_LIMIT_CAP} "
            "enregistrements lus par module pour garder une lecture DPO fluide."
        )

    for module_id in (_demo_modules_for_source("gmao") if DEMO_MODE_ENABLED else GMAO_MODULE_ENDPOINTS.keys()):
        try:
            module_records = connector.fetch(
                module_id,
                timeout=GMAO_ALL_MODULE_TIMEOUT,
                retry_timeout=GMAO_ALL_MODULE_RETRY_TIMEOUT,
            )
            if effective_limit > 0:
                module_records = module_records[:effective_limit]
            module_records = [
                {**record, "_source_module": module_id}
                if isinstance(record, dict) else record
                for record in module_records
            ]
            combined_records.extend(module_records)
            module_records_map[module_id] = module_records
            modules_summary.append({
                "id": module_id,
                "label": GMAO_MODULE_LABELS.get(module_id, module_id),
                "records": len(module_records),
            })
        except Exception as exc:
            module_errors.append({
                "id": module_id,
                "label": GMAO_MODULE_LABELS.get(module_id, module_id),
                "error": str(exc),
            })

    bundle = {
        "module": "all",
        "module_label": "Toute la source",
        "records": combined_records,
        "modules": modules_summary,
        "module_errors": module_errors,
        "fetch_note": _append_demo_note(fetch_note, "gmao"),
        "read_limit_per_module": effective_limit,
        "_module_records": module_records_map,
        "cache_hit": False,
        "cache_age_seconds": 0,
    }
    _set_cached_bundle("gmao", module, limit, bundle)
    return bundle


def _build_gmao_traitement_input(bundle: dict) -> dict:
    """Normalize GMAO fetched data into the treatment payload used by Agent A."""
    return {
        "gmao_module": bundle["module"],
        "gmao_records": bundle["records"],
        "gmao_modules": bundle["modules"],
        "gmao_module_errors": bundle["module_errors"],
        "systeme": "GMAO PRO",
        "fast_source_analysis": True,
    }


def _persist_gmao_agent_a_result(traitement_input: dict, agent_a_output: dict):
    try:
        return crud.save_treatment(traitement_input, agent_a_output)
    except Exception as db_err:
        print(f"[DB] Warning: {db_err}")
        return None


def _run_gmao_fast_analysis(
    module: str,
    limit: int,
    background_tasks: BackgroundTasks | None,
    user: dict,
):
    request_started = time.perf_counter()
    login_started = time.perf_counter()
    connector = get_gmao_connector()
    login_ms = _elapsed_ms(login_started)
    fetch_started = time.perf_counter()
    bundle = _fetch_gmao_bundle(connector, module, limit)
    fetch_ms = _elapsed_ms(fetch_started)
    records = bundle["records"]

    traitement_input = _build_gmao_traitement_input(bundle)
    agent_started = time.perf_counter()
    agent_a = run_agent_a(traitement_input)
    agent_a_ms = _elapsed_ms(agent_started)
    save_started = time.perf_counter()
    analysis_id = _persist_gmao_agent_a_result(traitement_input, agent_a)
    save_ms = _elapsed_ms(save_started)
    if background_tasks is not None:
        background_tasks.add_task(
            _finalize_fast_analysis_background,
            user,
            "gmao",
            bundle["module"],
            limit,
            len(records),
            traitement_input,
            agent_a,
            analysis_id,
        )
    else:
        _finalize_fast_analysis_background(
            user,
            "gmao",
            bundle["module"],
            limit,
            len(records),
            traitement_input,
            agent_a,
            analysis_id,
        )
    timings = {
        "login_ms": login_ms,
        "fetch_ms": fetch_ms,
        "agent_a_ms": agent_a_ms,
        "save_ms": save_ms,
        "total_ms": _elapsed_ms(request_started),
    }
    _log_analysis_timings(
        "gmao",
        bundle["module"],
        timings,
        len(records),
        note=bundle.get("fetch_note") or ("cache" if bundle.get("cache_hit") else ""),
    )

    return {
        "module": bundle["module"],
        "module_label": bundle["module_label"],
        "modules": bundle["modules"],
        "source_summary": _build_grouped_source_summary("gmao", bundle) if bundle["module"] == "all" else None,
        "module_errors": bundle["module_errors"],
        "fetch_note": bundle.get("fetch_note", ""),
        "read_limit_per_module": bundle.get("read_limit_per_module", limit),
        "cache_hit": bool(bundle.get("cache_hit")),
        "cache_age_seconds": bundle.get("cache_age_seconds", 0),
        "records_analysed": len(records),
        "analysis_mode": "fast_agent_a",
        "statut": "completed",
        "erreurs": [],
        "agent_a": agent_a,
        "agent_b": None,
        "agent_c": None,
        "agent_d": None,
        "persistence": {"analysis_id": analysis_id, "status": "queued"},
        "performance": timings,
    }


def _finalize_fast_analysis_background(
    user: dict,
    source: str,
    module: str,
    limit: int,
    records_count: int,
    traitement_input: dict,
    agent_a_output: dict,
    analysis_id: int | None,
):
    try:
        if analysis_id:
            persist_register_and_actions(
                traitement_input=traitement_input,
                agent_a_output=agent_a_output,
                source_analysis_id=analysis_id,
            )
        crud.save_audit_event(
            user,
            "analysis.run",
            source,
            module,
            {"limit": limit, "records": records_count, "analysis_id": analysis_id},
        )
    except Exception as exc:
        logging.warning(f"[ANALYSIS][{source}:{module}] background finalize failed: {exc}")


@app.post("/gmao/fetch/{module}")
def gmao_fetch(module: str, limit: int = 100, user: dict = Depends(require_permission("analysis:run"))):
    request_started = time.perf_counter()
    try:
        from agents.agent_a import detect_qalitas_fields
        login_started = time.perf_counter()
        connector = get_gmao_connector()
        login_ms = _elapsed_ms(login_started)
        fetch_started = time.perf_counter()
        bundle = _fetch_gmao_bundle(connector, module, limit)
        fetch_ms = _elapsed_ms(fetch_started)
        records = bundle["records"]
        if not records and bundle["module_errors"]:
            raise HTTPException(status_code=502, detail=f"Aucun module exploitable pour '{module}'")
        detect_started = time.perf_counter()
        detection = detect_qalitas_fields(records, module, source_system="gmao")
        detect_ms = _elapsed_ms(detect_started)
        crud.save_audit_event(
            user,
            "analysis.fetch",
            "gmao",
            module,
            {"limit": limit, "records": len(records)},
        )
        _log_analysis_timings(
            source="gmao.fetch",
            module=module,
            timings={
                "login_ms": login_ms,
                "fetch_ms": fetch_ms,
                "agent_a_ms": detect_ms,
                "save_ms": 0,
                "total_ms": _elapsed_ms(request_started),
            },
            records_count=len(records),
        )
        fallback = {
            "module": bundle["module"],
            "module_label": bundle["module_label"],
            "modules": bundle["modules"],
            "source_summary": _build_grouped_source_summary("gmao", bundle) if bundle["module"] == "all" else None,
            "module_errors": bundle["module_errors"],
            "fetch_note": bundle.get("fetch_note", ""),
            "read_limit_per_module": bundle.get("read_limit_per_module", limit),
            "cache_hit": bool(bundle.get("cache_hit")),
            "cache_age_seconds": bundle.get("cache_age_seconds", 0),
            "record_count": detection["record_count"],
            "personal_fields": detection["personal_fields"],
            "sensitive_fields": detection["sensitive_fields"],
            "all_fields": detection["all_fields"],
            "source_system": detection.get("source_system"),
            "records_with_personal_data": detection["records_with_personal_data"],
            "affected_clients": detection["affected_clients"],
            "affected_clients_count": detection["affected_clients_count"],
            "named_records": detection["named_records"],
            "named_records_count": detection["named_records_count"],
            "records_details": detection["records_details"],
            "physical_person_records_count": detection.get("physical_person_records_count", 0),
            "person_categories": detection.get("person_categories", []),
            "person_categories_display": detection.get("person_categories_display", []),
            "has_individual_names": detection["has_individual_names"],
            "sample_record": detection["sample_record"],
            "records_fetched": len(records)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GMAO fetch error: {str(e)}")


@app.post("/gmao/analyse/{module}")
def gmao_analyse(
    module: str,
    background_tasks: BackgroundTasks,
    limit: int = 200,
    user: dict = Depends(require_permission("analysis:run")),
):
    """
    Fast GMAO analysis path: Agent A only (Q1/Q2/Q3).
    """
    try:
        return _run_gmao_fast_analysis(module, limit, background_tasks, user)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GMAO analysis error: {str(e)}")


def _source_has_direct_personal_data(source: str, analysis_payload: dict | None) -> bool:
    if not analysis_payload:
        return False
    intelligence = ((analysis_payload.get("agent_a") or {}).get("intelligence") or {})
    if source == "gmao":
        detection = intelligence.get("gmao_detected_fields") or intelligence.get("qalitas_detected_fields") or {}
    else:
        detection = intelligence.get("qalitas_detected_fields") or {}
    return bool(
        detection.get("records_with_personal_data")
        or detection.get("personal_fields")
        or detection.get("sensitive_fields")
    )


def _friendly_source_error_message(source: str, message: str) -> str:
    raw = str(message or "")
    lower = raw.lower()
    label = "QALITAS" if source == "qalitas" else "GMAO PRO"
    if "read timed out" in lower or "connecttimeout" in lower or "max retries exceeded" in lower:
        return f"{label} répond trop lentement pour une lecture globale. Ouvrez le détail source ou réduisez le volume."
    if "no data found" in lower:
        return f"{label} n'a renvoyé aucun enregistrement exploitable sur cette lecture."
    if "aucun module exploitable" in lower:
        return f"{label} n'a pas pu fournir de module exploitable pendant cette lecture globale."
    return raw


def _build_grouped_source_summary(source: str, bundle: dict) -> dict:
    module_records_map = bundle.get("_module_records") or {}
    module_rows: list[dict[str, Any]] = []
    modules_with_direct_personal = 0
    modules_hors_champ = 0
    total_alerts = 0
    modules_to_review: list[str] = []

    for module_meta in bundle.get("modules", []) or []:
        module_id = module_meta.get("id")
        if not module_id:
            continue
        module_records = module_records_map.get(module_id) or []
        if not module_records:
            continue

        module_bundle = {
            "module": module_id,
            "module_label": module_meta.get("label", module_id),
            "records": module_records,
            "modules": [{
                "id": module_id,
                "label": module_meta.get("label", module_id),
                "records": len(module_records),
            }],
            "module_errors": [],
        }
        traitement_input = (
            _build_qalitas_traitement_input(module_bundle)
            if source == "qalitas"
            else _build_gmao_traitement_input(module_bundle)
        )
        agent_a = run_agent_a(traitement_input)
        q2 = agent_a.get("q2_conformite") or {}
        direct_personal = _source_has_direct_personal_data(source, {"agent_a": agent_a})
        hors_champ = bool(q2.get("hors_champ_rgpd"))
        violations = int(q2.get("nombre_violations") or 0)
        documentary_points = int(q2.get("nombre_points_documentaires") or 0)
        review_required = direct_personal and not hors_champ and (violations > 0 or documentary_points > 0)

        if direct_personal:
            modules_with_direct_personal += 1
        if hors_champ:
            modules_hors_champ += 1
        if not hors_champ:
            total_alerts += violations + documentary_points
        if review_required:
            modules_to_review.append(module_id)

        module_rows.append({
            "id": module_id,
            "label": module_meta.get("label", module_id),
            "records": len(module_records),
            "direct_personal": direct_personal,
            "hors_champ_rgpd": hors_champ,
            "violations": violations,
            "documentary_points": documentary_points,
            "niveau_risque": q2.get("niveau_risque"),
            "scope_reason": q2.get("scope_reason"),
            "review_required": review_required,
            "affected_clients_count": int((((agent_a.get("intelligence") or {}).get("gmao_detected_fields") or ((agent_a.get("intelligence") or {}).get("qalitas_detected_fields") or {})).get("affected_clients_count") or 0)),
            "affected_clients_preview": ((((agent_a.get("intelligence") or {}).get("gmao_detected_fields") or ((agent_a.get("intelligence") or {}).get("qalitas_detected_fields") or {})).get("affected_clients") or [])[:5]),
            "person_categories": (((agent_a.get("intelligence") or {}).get("gmao_detected_fields") or ((agent_a.get("intelligence") or {}).get("qalitas_detected_fields") or {})).get("person_categories_display") or ((agent_a.get("intelligence") or {}).get("gmao_detected_fields") or ((agent_a.get("intelligence") or {}).get("qalitas_detected_fields") or {})).get("person_categories") or []),
        })

    return {
        "modules": module_rows,
        "modules_read": len(module_rows),
        "modules_with_direct_personal": modules_with_direct_personal,
        "modules_hors_champ": modules_hors_champ,
        "modules_to_review": modules_to_review,
        "total_alerts": total_alerts,
        "review_required": bool(modules_to_review),
    }


@app.post("/sources/analyse")
def sources_analyse(
    background_tasks: BackgroundTasks,
    limit: int = 100,
    user: dict = Depends(require_permission("analysis:run")),
):
    effective_limit = limit
    global_note = ""
    if limit <= 0 or limit > GLOBAL_SOURCES_LIMIT_CAP:
        effective_limit = GLOBAL_SOURCES_LIMIT_CAP
        global_note = (
            f"Lecture globale accélérée : maximum {GLOBAL_SOURCES_LIMIT_CAP} lignes demandées par source. "
            "Utilisez ensuite les pages de détail pour approfondir."
        )

    results: dict[str, Any] = {"qalitas": None, "gmao": None, "errors": []}

    try:
        results["qalitas"] = _run_qalitas_fast_analysis("all", effective_limit, background_tasks, user)
    except HTTPException as exc:
        results["errors"].append({"source": "qalitas", "message": _friendly_source_error_message("qalitas", str(exc.detail))})
    except Exception as exc:
        results["errors"].append({"source": "qalitas", "message": _friendly_source_error_message("qalitas", str(exc))})

    try:
        results["gmao"] = _run_gmao_fast_analysis("all", effective_limit, background_tasks, user)
    except HTTPException as exc:
        results["errors"].append({"source": "gmao", "message": _friendly_source_error_message("gmao", str(exc.detail))})
    except Exception as exc:
        results["errors"].append({"source": "gmao", "message": _friendly_source_error_message("gmao", str(exc))})

    completed_sources = [name for name in ("qalitas", "gmao") if results.get(name)]
    if not completed_sources:
        raise HTTPException(status_code=502, detail="Aucune source exploitable pour la lecture globale.")

    total_records = sum((results[name] or {}).get("records_analysed", 0) for name in completed_sources)
    total_direct_violations = 0
    total_documentary_points = 0
    total_alerts = 0
    total_modules_to_review = 0
    sources_with_direct_personal = 0
    sources_hors_champ = 0
    sources_to_review = []
    sources_without_direct_personal = []

    for name in completed_sources:
        payload = results[name]
        source_summary = payload.get("source_summary") or {}
        q2 = ((payload.get("agent_a") or {}).get("q2_conformite") or {})
        direct_personal = (
            bool(source_summary.get("modules_with_direct_personal"))
            or _source_has_direct_personal_data(name, payload)
        )
        hors_champ = bool(
            source_summary.get("modules_read")
            and source_summary.get("modules_read") == source_summary.get("modules_hors_champ")
            and not source_summary.get("modules_with_direct_personal")
        )
        direct_violations = int(q2.get("nombre_violations") or 0)
        doc_points = int(source_summary.get("total_alerts") or 0) - direct_violations
        if hors_champ:
            sources_hors_champ += 1
            sources_without_direct_personal.append(name)
        else:
            total_direct_violations += direct_violations
            total_documentary_points += max(0, doc_points)
            total_alerts += int(source_summary.get("total_alerts") or direct_violations or 0)
            if direct_personal:
                sources_with_direct_personal += 1
            total_modules_to_review += len(source_summary.get("modules_to_review") or [])
            if source_summary.get("review_required") or direct_violations > 0 or doc_points > 0:
                sources_to_review.append(name)

    return {
        "limit": effective_limit,
        "global_note": global_note,
        "qalitas": results["qalitas"],
        "gmao": results["gmao"],
        "errors": results["errors"],
        "summary": {
            "sources_completed": len(completed_sources),
            "sources_failed": len(results["errors"]),
            "total_records": total_records,
            "total_violations": total_direct_violations,
            "total_direct_violations": total_direct_violations,
            "total_documentary_points": total_documentary_points,
            "total_alerts": total_alerts,
            "total_module_alerts": total_alerts,
            "total_modules_to_review": total_modules_to_review,
            "sources_with_direct_personal": sources_with_direct_personal,
            "sources_hors_champ": sources_hors_champ,
            "sources_to_review": sources_to_review,
            "sources_without_direct_personal": sources_without_direct_personal,
        },
    }


@app.get("/gmao/status")
def gmao_status(user: dict = Depends(require_any_permission("connectors:configure", "analysis:run"))):
    from gmao.connector import GMAO_USER, GMAO_PASSWORD, GMAO_BASE_URL
    return {
        "configured": bool(GMAO_USER and GMAO_PASSWORD),
        "user": GMAO_USER if GMAO_USER else "not set",
        "url": GMAO_BASE_URL,
        "modules_available": _demo_modules_for_source("gmao") if DEMO_MODE_ENABLED else list(GMAO_MODULE_ENDPOINTS.keys()),
        "demo_mode": DEMO_MODE_ENABLED,
    }


# ==============================================================================
# REAL-TIME SOURCE WATCHER
# ==============================================================================

@app.get("/realtime/sources")
def realtime_sources(user: dict = Depends(require_permission("settings:manage"))):
    from realtime.detector import available_sources
    return available_sources()


@app.get("/realtime/status")
def realtime_status(user: dict = Depends(require_permission("settings:manage"))):
    from realtime import scheduler as realtime_scheduler
    return realtime_scheduler.get_status()


@app.post("/realtime/run-now")
def realtime_run_now(payload: RealtimeRunRequest, user: dict = Depends(require_permission("settings:manage"))):
    try:
        from realtime import scheduler as realtime_scheduler
        return realtime_scheduler.run_once(
            sources=payload.sources,
            modules=payload.modules,
            limit=payload.limit or 100,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Real-time run error: {str(e)}")


@app.post("/realtime/start")
def realtime_start(payload: RealtimeStartRequest, user: dict = Depends(require_permission("settings:manage"))):
    try:
        from realtime import scheduler as realtime_scheduler
        return realtime_scheduler.start(
            interval_seconds=payload.interval_seconds or 300,
            sources=payload.sources,
            modules=payload.modules,
            limit=payload.limit or 100,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Real-time start error: {str(e)}")


@app.post("/realtime/stop")
def realtime_stop(user: dict = Depends(require_permission("settings:manage"))):
    from realtime import scheduler as realtime_scheduler
    return realtime_scheduler.stop()


@app.get("/realtime/events")
def realtime_events(
    source_system: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    user: dict = Depends(require_permission("settings:manage")),
):
    try:
        return {
            "events": crud.get_realtime_events(
                source_system=source_system,
                status=status,
                limit=max(1, min(int(limit or 100), 500)),
            )
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Real-time events error: {str(e)}")


@app.get("/realtime/snapshot/{source_system}/{module}")
def realtime_snapshot(source_system: str, module: str, user: dict = Depends(require_permission("settings:manage"))):
    try:
        row = crud.get_latest_realtime_snapshot(source_system=source_system, module=module)
        if not row:
            return {"snapshot": None}
        snapshot = dict(row)
        raw = snapshot.pop("snapshot_json", "{}") or "{}"
        try:
            snapshot["data"] = json.loads(raw)
        except json.JSONDecodeError:
            snapshot["data"] = {}
        return {"snapshot": snapshot}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Real-time snapshot error: {str(e)}")


@app.get("/realtime/stream")
async def realtime_stream(user: dict = Depends(require_permission("settings:manage"))):
    async def event_generator():
        while True:
            try:
                from realtime import scheduler as realtime_scheduler
                payload = {
                    "status": realtime_scheduler.get_status(),
                    "events": crud.get_realtime_events(limit=80),
                    "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                data = json.dumps(payload, ensure_ascii=False, default=str)
                yield f"event: realtime\ndata: {data}\n\n"
            except Exception as exc:
                data = json.dumps({"error": str(exc)}, ensure_ascii=False)
                yield f"event: realtime_error\ndata: {data}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

@app.post("/analyser")
def analyser(traitement: Traitement, user: dict = Depends(require_permission("analysis:run"))):
    try:
        data = _model_to_dict(traitement)
        result = run_agent_a(data)
        # Persist every analysis automatically
        try:
            analysis_id = crud.save_treatment(data, result)
            persist_register_and_actions(
                traitement_input=data,
                agent_a_output=result,
                source_analysis_id=analysis_id
            )
        except Exception as db_err:
            print(f"[DB] Warning: could not save treatment: {db_err}")
        return result
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/risques")
def risques(payload: TraitementAvecIncident, user: dict = Depends(require_permission("analysis:run"))):
    try:
        data = _model_to_dict(payload.traitement)
        incident = _model_to_dict(payload.incident) if payload.incident else None
        agent_a = run_agent_a(data)
        result = run_agent_b(data, agent_a, incident)
        # Persist treatment + incident
        try:
            analysis_id = crud.save_treatment(data, agent_a)
            persist_register_and_actions(
                traitement_input=data,
                agent_a_output=agent_a,
                source_analysis_id=analysis_id
            )
            risk_review_id = crud.save_risk_review(
                traitement_input=data,
                agent_b_output=result,
                source_analysis_id=analysis_id
            )
            incident_review_id = None
            if incident:
                crud.save_violation(incident, result)
                incident_review_id = crud.save_incident_review(
                    incident_input=incident,
                    agent_b_output=result,
                    source_analysis_id=analysis_id
                )
            result["persistence"] = {
                "analysis_id": analysis_id,
                "risk_review_id": risk_review_id,
                "incident_review_id": incident_review_id
            }
        except Exception as db_err:
            print(f"[DB] Warning: could not save risques: {db_err}")
        return result
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/droits")
def droits(demande: DemandeDSAR, user: dict = Depends(require_permission("dsar:manage"))):
    try:
        data = _model_to_dict(demande)
        result = run_agent_c(data)
        # Persist DSAR request
        try:
            crud.save_dsar(data, result)
        except Exception as db_err:
            print(f"[DB] Warning: could not save DSAR: {db_err}")
        q5 = result.get("q5_droits", {})
        trimmed_q5 = dict(q5)
        recherche = trimmed_q5.pop("recherche_transversale", None)
        if recherche:
            trimmed_q5["recherche_resume"] = recherche.get("resume", {})
            trimmed_q5["termes_recherche"] = recherche.get("termes_recherche", [])
        return {
            **result,
            "q5_droits": trimmed_q5,
        }
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/droits/execute")
def execute_droits(payload: DSARExecutionRequest, user: dict = Depends(require_permission("dsar:manage"))):
    """Q5 - Safe execution log, plus optional local platform apply for rectification/effacement."""
    try:
        saved = crud.get_dsar_by_id(payload.id_demande)
        if not saved:
            raise HTTPException(status_code=404, detail="DSAR not found.")

        dsar_input = json.loads(saved.get("input_json") or "{}")
        dsar_output = json.loads(saved.get("output_json") or "{}")
        q5 = dsar_output.get("q5_droits", {})
        paquet = q5.get("paquet_dsar", {})
        operationnel = paquet.get("package_operationnel", {})

        if not operationnel:
            raise HTTPException(status_code=400, detail="No DSAR package available for execution.")

        qualification = q5.get("qualification")
        can_execute = operationnel.get("can_execute", False)
        type_droit = operationnel.get("type_droit") or dsar_input.get("type_droit")

        targets = []
        targets.extend(operationnel.get("rectification_targets", []))
        targets.extend(operationnel.get("erasure_targets", []))
        targets.extend(operationnel.get("restriction_targets", []))
        targets.extend(operationnel.get("opposition_targets", []))
        targets.extend(operationnel.get("automated_decision_targets", []))
        if operationnel.get("access_payload"):
            targets.append({"target_type": "access_payload", "count": operationnel["access_payload"].get("resume", {})})
        if operationnel.get("portability_export"):
            targets.append({"target_type": "portability_export", "format": operationnel["portability_export"].get("format_recommande")})

        apply_result = None
        if can_execute and payload.mode_execution == "platform_apply":
            apply_result = crud.apply_dsar_platform_execution(
                type_droit=type_droit,
                dsar_input=dsar_input,
                dsar_output=dsar_output,
                rectification_values=payload.rectification_values,
            )
            if not apply_result.get("supported", False):
                decision = "review_required"
                statut = "bloque"
            else:
                decision = "execute" if apply_result.get("records_modified", 0) > 0 else "review_required"
                statut = "cloture" if decision == "execute" else "journalise"
        else:
            decision = "execute" if can_execute else "blocked"
            statut = "journalise" if can_execute else "bloque"

        legal_basis_note = f"Qualification={qualification}; mode={payload.mode_execution}; "
        if payload.mode_execution == "platform_apply":
            legal_basis_note += "local platform data only; no external QALITAS/GMAO write performed."
        else:
            legal_basis_note += "no live destructive write performed."
        if payload.notes:
            legal_basis_note += f" Notes: {payload.notes}"

        execution_payload = {
            "id_demande": payload.id_demande,
            "executor": payload.executor,
            "mode_execution": payload.mode_execution,
            "qualification": qualification,
            "type_droit": type_droit,
            "targets": targets,
            "package_summary": paquet.get("resume_officiel", {}),
            "source_input": dsar_input,
            "rectification_values": payload.rectification_values or {},
            "apply_result": apply_result,
        }

        execution_id = crud.save_dsar_execution(
            id_demande=payload.id_demande,
            type_droit=type_droit,
            executor=payload.executor,
            mode_execution=payload.mode_execution,
            decision=decision,
            statut=statut,
            legal_basis_note=legal_basis_note,
            targets_count=len(targets),
            execution_payload=execution_payload
        )

        return {
            "execution_id": execution_id,
            "id_demande": payload.id_demande,
            "decision": decision,
            "statut": statut,
            "targets_count": len(targets),
            "message": (
                (apply_result or {}).get("message")
                if payload.mode_execution == "platform_apply" and can_execute else
                "Execution safely logged. No live deletion or update was sent to business systems."
                if can_execute else
                "Execution blocked because the DSAR package is not executable in its current state."
            ),
            "details": execution_payload,
            "apply_result": apply_result,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/gouvernance")
def gouvernance(payload: AnalyseComplete, user: dict = Depends(require_permission("reports:generate"))):
    """Full DPO governance analysis via LangGraph: A -> B -> C -> D"""
    try:
        data     = _model_to_dict(payload.traitement)
        incident = _model_to_dict(payload.incident) if payload.incident else None
        demande  = _model_to_dict(payload.demande_dsar) if payload.demande_dsar else None

        # Run full LangGraph workflow
        result = run_workflow(
            traitement=data,
            incident=incident,
            demande_dsar=demande
        )

        # Return Agent D output (DPO governance) — same as before
        # but now properly orchestrated by LangGraph
        agent_d = result.get("agent_d", {})
        if agent_d:
            persist_governance_snapshot(agent_d)
            return agent_d
        # Fallback if Agent D failed
        raise HTTPException(
            status_code=500,
            detail="Agent D failed: " + str(result.get("erreurs", []))
        )
    except HTTPException:
        raise
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/gouvernance/from-agents")
def gouvernance_from_agents(payload: GovernanceFromAgents, user: dict = Depends(require_permission("reports:generate"))):
    """Governance based on the latest already-computed agents, to keep UI pages consistent."""
    try:
        agent_d = run_agent_d(payload.agent_a, payload.agent_b, payload.agent_c)
        if agent_d:
            persist_governance_snapshot(agent_d)
            return agent_d
        raise HTTPException(status_code=500, detail="Agent D failed from provided agent context.")
    except HTTPException:
        raise
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/workflow")
def workflow(payload: AnalyseComplete, user: dict = Depends(require_permission("analysis:run"))):
    try:
        data = _model_to_dict(payload.traitement)
        incident = _model_to_dict(payload.incident) if payload.incident else None
        demande = _model_to_dict(payload.demande_dsar) if payload.demande_dsar else None
        result = run_workflow(data, incident, demande)
        if result.get("agent_d"):
            persist_governance_snapshot(result["agent_d"])
        return result
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/", response_class=HTMLResponse)
def root_interface():
    html = open("api/interface.html", encoding="utf-8").read()
    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


@app.get("/interface", response_class=HTMLResponse)
def interface():
    html = open("api/interface.html", encoding="utf-8").read()
    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


class DSARText(BaseModel):
    texte: str


class DSARMailboxCreate(BaseModel):
    email_address: str
    provider: str = "microsoft365"
    label: Optional[str] = None
    sample_message: Optional[str] = None
    imap_host: Optional[str] = None
    imap_port: Optional[int] = 993
    imap_username: Optional[str] = None
    imap_password: Optional[str] = None
    imap_folder: Optional[str] = "INBOX"


class DSARScanGmailRequest(BaseModel):
    email: str
    app_password: str
    max_emails: int = 30


@app.post("/dsar/scan_gmail")
async def dsar_scan_gmail(
    data: DSARScanGmailRequest,
    user: dict = Depends(require_permission("dsar:manage")),
):
    """
    Simplified DSAR email scanner.
    Takes a Gmail address + App Password, connects via IMAP,
    finds DSAR-related emails, and uses Groq LLM to extract structured info.
    """
    from integrations.imap_mail import ImapMailConnector
    from datetime import datetime, timedelta

    email_addr = (data.email or "").strip()
    password = (data.app_password or "").strip()
    if not email_addr or not password:
        raise HTTPException(status_code=400, detail="Email et mot de passe applicatif requis.")

    # Determine IMAP host from email domain
    domain = email_addr.split("@")[-1].lower() if "@" in email_addr else ""
    imap_hosts = {
        "gmail.com": "imap.gmail.com",
        "googlemail.com": "imap.gmail.com",
        "outlook.com": "outlook.office365.com",
        "hotmail.com": "outlook.office365.com",
        "yahoo.com": "imap.mail.yahoo.com",
        "yahoo.fr": "imap.mail.yahoo.com",
    }
    host = imap_hosts.get(domain, f"imap.{domain}")

    # Connect via IMAP
    try:
        connector = ImapMailConnector(
            host=host,
            port=993,
            username=email_addr,
            password=password,
            folder="INBOX",
            use_ssl=True,
        )
        top = min(max(data.max_emails or 30, 5), 50)
        candidates = connector.extract_dsar_candidates(top=max(top, 25))
    except Exception as exc:
        error_msg = str(exc)
        if "AUTHENTICATIONFAILED" in error_msg.upper() or "LOGIN" in error_msg.upper():
            raise HTTPException(
                status_code=401,
                detail="Authentification IMAP echouee. Verifiez l'email et le mot de passe applicatif Gmail."
            )
        raise HTTPException(status_code=500, detail=f"Erreur connexion IMAP: {error_msg}")

    if not candidates:
        return {
            "status": "ok",
            "email": email_addr,
            "host": host,
            "emails_scanned": 0,
            "dsar_found": 0,
            "dsars": [],
            "message": "Aucun email de type DSAR detecte dans la boite.",
        }

    # Use Groq LLM to extract structured DSAR info from each candidate
    extracted_dsars = []
    try:
        from groq import Groq as _GroqClient
        from dotenv import load_dotenv as _ld
        _ld("key.env")
        groq_client = _GroqClient(api_key=os.getenv("GROQ_API_KEY"))
    except Exception:
        groq_client = None

    for candidate in candidates:
        email_text = candidate.get("text", "")
        email_subject = candidate.get("subject", "")
        email_from = candidate.get("from", "")

        if groq_client:
            try:
                prompt = f"""Tu es un expert RGPD. Analyse cet email et determine s il s agit d une demande d exercice de droits (DSAR).

SUJET: {email_subject}
DE: {email_from}
CONTENU:
{email_text[:3000]}

Reponds UNIQUEMENT avec un JSON valide, sans backticks:
{{
  "is_dsar": true,
  "nom_demandeur": "nom complet extrait",
  "email_demandeur": "email de la personne si visible",
  "type_droit": "acces|rectification|effacement|limitation|portabilite|opposition",
  "systeme_concerne": "nom du systeme mentionne ou vide",
  "donnees_concernees": ["liste", "des", "donnees"],
  "urgence": "normale|elevee|critique",
  "resume": "resume en une phrase"
}}

Regles:
- is_dsar: true si c est clairement une demande RGPD, false sinon
- type_droit: choisis le plus proche
- urgence: elevee si delai mentionne ou ton urgent, critique si mise en demeure"""

                resp = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=400,
                    temperature=0.1,
                )
                raw_text = resp.choices[0].message.content.strip()
                raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                if match:
                    raw_text = match.group(0)
                parsed = json.loads(raw_text)
            except Exception:
                parsed = extract_dsar_from_text_local(email_text)
                parsed["is_dsar"] = True
        else:
            parsed = extract_dsar_from_text_local(email_text)
            parsed["is_dsar"] = True

        if not parsed.get("is_dsar", True):
            continue

        # Calculate 30-day deadline from email date
        date_reception = datetime.now().strftime("%Y-%m-%d")
        date_limite = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        jours_restants = 30

        extracted_dsars.append({
            "email_subject": email_subject,
            "email_from": email_from,
            "email_message_id": candidate.get("message_id", ""),
            "nom_demandeur": parsed.get("nom_demandeur", "Non identifie"),
            "email_demandeur": parsed.get("email_demandeur", email_from),
            "type_droit": parsed.get("type_droit", "acces"),
            "systeme_concerne": parsed.get("systeme_concerne", ""),
            "donnees_concernees": parsed.get("donnees_concernees", []),
            "urgence": parsed.get("urgence", "normale"),
            "resume": parsed.get("resume", ""),
            "date_reception": date_reception,
            "date_limite": date_limite,
            "jours_restants": jours_restants,
        })

    return {
        "status": "ok",
        "email": email_addr,
        "host": host,
        "emails_scanned": len(candidates),
        "dsar_found": len(extracted_dsars),
        "dsars": extracted_dsars,
        "message": f"{len(extracted_dsars)} demande(s) DSAR detectee(s)." if extracted_dsars else "Aucune demande DSAR confirmee.",
    }


DSAR_TYPE_HINTS = {
    "rectification": [
        "rectification",
        "rectifier",
        "corriger",
        "correction",
        "modifier",
        "modification",
        "mettre a jour",
        "numero de telephone",
        "telephone",
        "email est faux",
    ],
    "effacement": [
        "effacement",
        "effacer",
        "supprimer",
        "suppression",
        "retirer mes donnees",
        "droit a l oubli",
        "oublier mes donnees",
    ],
    "limitation": [
        "limitation",
        "limiter",
        "suspendre",
        "geler",
        "bloquer",
    ],
    "portabilite": [
        "portabilite",
        "portable",
        "export",
        "csv",
        "json",
        "format structure",
        "lisible par machine",
    ],
    "opposition": [
        "opposition",
        "m oppose",
        "je m oppose",
        "refuse ce traitement",
        "cesser ce traitement",
    ],
    "decision_automatisee": [
        "decision automatisee",
        "profilage",
        "algorithme",
        "intervention humaine",
    ],
    "acces": [
        "acces",
        "copie de mes donnees",
        "obtenir une copie",
        "quelles donnees",
        "mes informations",
        "consulter mes donnees",
        "recevoir mes donnees",
    ],
}

DSAR_TYPE_PRIORITY = [
    "rectification",
    "effacement",
    "limitation",
    "opposition",
    "portabilite",
    "decision_automatisee",
    "acces",
]


def _normalise_dsar_message_text(texte: str) -> str:
    text = clean_mail_text(texte or "").strip()
    if not text:
        text = str(texte or "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("’", "'").replace("`", "'")
    text = re.sub(r"[^\w\s@']", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _infer_dsar_types_from_text(texte: str) -> dict:
    normalized = _normalise_dsar_message_text(texte)
    scores = {label: 0 for label in DSAR_TYPE_HINTS}
    first_positions = {label: 10**9 for label in DSAR_TYPE_HINTS}
    matches = {label: [] for label in DSAR_TYPE_HINTS}

    for label, hints in DSAR_TYPE_HINTS.items():
        for hint in hints:
            pos = normalized.find(hint)
            if pos == -1:
                continue
            scores[label] += 1
            first_positions[label] = min(first_positions[label], pos)
            matches[label].append(hint)

    detected = [label for label, score in scores.items() if score > 0]
    detected.sort(
        key=lambda label: (
            -scores[label],
            first_positions[label],
            DSAR_TYPE_PRIORITY.index(label) if label in DSAR_TYPE_PRIORITY else 999,
        )
    )
    primary = detected[0] if detected else None
    return {
        "primary": primary,
        "detected": detected,
        "scores": scores,
        "matches": {label: values for label, values in matches.items() if values},
    }


def _finalize_dsar_type(texte: str, payload: dict | None = None) -> dict:
    payload = dict(payload or {})
    heuristic = _infer_dsar_types_from_text(texte)
    ml_prediction = predict_dsar_intent(texte)
    extracted_type = str(payload.get("type_droit") or "").strip()
    heuristic_primary = heuristic.get("primary")
    detected_types = heuristic.get("detected") or []

    if heuristic_primary and (
        not extracted_type
        or extracted_type not in DROITS_RGPD
        or (extracted_type == "acces" and heuristic_primary != "acces")
    ):
        payload["type_droit"] = heuristic_primary
        payload["type_droit_source"] = "heuristic_override"
    elif extracted_type in DROITS_RGPD:
        payload["type_droit"] = extracted_type
        payload["type_droit_source"] = payload.get("type_droit_source") or "extractor"
    else:
        payload["type_droit"] = ml_prediction.get("label", "acces")
        payload["type_droit_source"] = "ml_fallback"

    payload["types_droits_detectes"] = detected_types or [payload.get("type_droit", "acces")]
    payload["type_droit_ml"] = ml_prediction.get("label", "acces")
    payload["type_droit_ml_confidence"] = ml_prediction.get("confidence", 0.0)
    payload["type_droit_matches"] = heuristic.get("matches", {})
    return payload


def extract_dsar_from_text_local(texte: str) -> dict:
    """Lightweight local DSAR extractor used for mailbox sync previews."""
    raw = clean_mail_text(texte or "").strip()
    if not raw:
        raw = (texte or "").strip()
    lower = raw.lower()
    type_meta = _finalize_dsar_type(raw, {"type_droit": ""})
    droit = type_meta.get("type_droit", "acces")

    nom = ""
    for line in [l.strip() for l in raw.splitlines() if l.strip()]:
        if line.lower().startswith(("nom", "name", "je suis", "i am")):
            nom = line.split(":", 1)[-1].strip() if ":" in line else line.replace("Je suis", "").replace("I am", "").strip()
            break

    if not nom:
        import re
        m = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b", raw)
        if m:
            nom = m.group(1)

    systeme = "QALITAS" if "qalitas" in lower else "GMAO PRO" if "gmao" in lower else ""
    donnees = []
    for item, label in [
        ("email", "email"),
        ("telephone", "telephone"),
        ("téléphone", "telephone"),
        ("adresse", "adresse"),
        ("nom", "nom"),
        ("banqu", "carte bancaire"),
        ("carte bancaire", "carte bancaire"),
        ("iban", "carte bancaire"),
        ("rib", "carte bancaire"),
        ("localisation", "localisation_GPS"),
        ("gps", "localisation_GPS"),
    ]:
        if item in lower and label not in donnees:
            donnees.append(label)

    resume_lines = []
    total_chars = 0
    for line in [l.strip() for l in raw.splitlines() if l.strip()]:
        resume_lines.append(line)
        total_chars += len(line) + 1
        if total_chars >= 500 or len(resume_lines) >= 8:
            break
    resume_message = "\n".join(resume_lines).strip() or raw[:500]

    return {
        "nom_demandeur": nom or "Non identifié",
        "type_droit": droit,
        "types_droits_detectes": type_meta.get("types_droits_detectes", [droit]),
        "type_droit_ml": type_meta.get("type_droit_ml", droit),
        "type_droit_ml_confidence": type_meta.get("type_droit_ml_confidence", 0.0),
        "type_droit_source": type_meta.get("type_droit_source", "heuristic_override"),
        "systeme_concerne": systeme,
        "donnees_concernees": donnees,
        "resume_message": resume_message[:500]
    }


@app.post("/extract_dsar")
async def extract_dsar(data: DSARText, user: dict = Depends(require_permission("dsar:manage"))):
    try:
        import os
        from groq import Groq
        from dotenv import load_dotenv
        load_dotenv("key.env")
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        prompt = f"""Tu es un expert RGPD. Analyse ce message et extrait les informations pour une demande DSAR.

MESSAGE:
{data.texte}

Reponds UNIQUEMENT avec un JSON valide, sans texte avant ou apres, sans backticks:
{{
  "nom_demandeur": "nom complet extrait ou vide",
  "type_droit": "acces|rectification|effacement|limitation|portabilite|opposition|decision_automatisee",
  "systeme": "GMAO PRO|QALITAS|inconnu",
  "donnees_concernees": "liste des donnees mentionnees separees par virgule",
  "identite_verifiee": true,
  "base_legale": "contrat|consentement|obligation_legale|interet_legitime",
  "resume": "resume en une phrase de la demande",
  "demandes_precedentes": 0
}}

Regles:
- type_droit: choisis le plus proche parmi les valeurs autorisees
- si plusieurs droits sont demandes, choisis le droit principal exprime en premier et mentionne les autres dans le resume
- si le systeme n est pas mentionne, mets "inconnu"
- identite_verifiee: true si le nom est clairement identifiable
- base_legale: deduis du contexte (employe = contrat, client = contrat, consentement si mentionne)
- demandes_precedentes: nombre de demandes precedentes mentionnees dans le message, 0 si non mentionne"""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.1
        )
        
        import json, re
        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        # Extract JSON object if surrounded by other text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            text = match.group(0)
        try:
            extracted = json.loads(text)
            return _finalize_dsar_type(data.texte, extracted)
        except Exception as e:
            fallback = {
                "nom_demandeur": "",
                "type_droit": "acces",
                "systeme": "inconnu",
                "donnees_concernees": "",
                "identite_verifiee": True,
                "base_legale": "contrat",
                "resume": "Extraction réussie (parsing JSON partiel)",
                "error": str(e),
                "raw": text
            }
            return _finalize_dsar_type(data.texte, fallback)
    except Exception as e:
        import traceback
        fallback = {
            "error": f"Extraction Ã©chouÃ©e: {str(e)}",
            "trace": str(traceback.format_exc()),
            "nom_demandeur": "",
            "type_droit": "acces",
            "systeme": "inconnu"
        }
        return _finalize_dsar_type(data.texte, fallback)
        return {
            "error": f"Extraction échouée: {str(e)}",
            "trace": str(traceback.format_exc()),
            "nom_demandeur": "",
            "type_droit": "acces",
            "systeme": "inconnu"
        }
        return _finalize_dsar_type(data.texte, fallback)


@app.get("/dsar/mailboxes")
def list_dsar_mailboxes(
    active_only: bool = False,
    limit: int = 100,
    user: dict = Depends(require_permission("dsar:manage")),
):
    """Return configured DSAR mailbox sources."""
    return {"mailboxes": crud.get_dsar_mailboxes(active_only=active_only, limit=limit)}


@app.get("/dsar/mailboxes/microsoft365/status")
def microsoft365_mail_status(user: dict = Depends(require_permission("connectors:configure"))):
    """Return Microsoft 365 mail connector configuration status."""
    connector = Microsoft365MailConnector()
    return connector.status()


@app.post("/dsar/mailboxes")
def create_dsar_mailbox(payload: DSARMailboxCreate, user: dict = Depends(require_permission("dsar:manage"))):
    """Save one mailbox source that Agent C can use for DSAR extraction."""
    try:
        mailbox_id = crud.save_dsar_mailbox(
            email_address=payload.email_address,
            provider=payload.provider,
            label=payload.label,
            sample_message=payload.sample_message,
            metadata={
                "source": "interface",
                "imap_host": payload.imap_host,
                "imap_port": payload.imap_port,
                "imap_username": payload.imap_username,
                "imap_password": payload.imap_password,
                "imap_folder": payload.imap_folder or "INBOX",
            }
        )
        rows = crud.get_dsar_mailboxes(limit=100)
        saved = next((r for r in rows if r.get("email_address") == payload.email_address), None)
        return {"saved": True, "mailbox_id": mailbox_id, "mailbox": saved}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Mailbox configuration error: {str(e)}")


@app.post("/dsar/mailboxes/{mailbox_id}/sync")
def sync_dsar_mailbox(mailbox_id: int, user: dict = Depends(require_permission("dsar:manage"))):
    """
    Lightweight DSAR mailbox sync.
    For now, reads the configured sample message and extracts a DSAR preview.
    """
    mailbox = crud.get_dsar_mailbox_by_id(mailbox_id)
    if not mailbox:
        raise HTTPException(status_code=404, detail="Mailbox not found.")
    metadata = json.loads(mailbox.get("metadata_json") or "{}")

    recent_mail_limit = 50

    if mailbox.get("provider") == "microsoft365":
        connector = Microsoft365MailConnector()
        if connector.is_configured():
            try:
                candidates = connector.extract_dsar_candidates(mailbox.get("email_address"), top=recent_mail_limit)
                extractions = []
                for candidate in candidates:
                    preview = extract_dsar_from_text_local(candidate.get("text") or "")
                    preview["email_subject"] = candidate.get("subject")
                    preview["email_from"] = candidate.get("from")
                    preview["email_received_at"] = candidate.get("received_at")
                    extractions.append({
                        "source_mailbox": mailbox.get("email_address"),
                        "provider": "microsoft365",
                        "message_id": candidate.get("message_id"),
                        "extracted_dsar": preview
                    })
                crud.update_dsar_mailbox_sync(
                    mailbox_id,
                    access_status="synced" if extractions else "connected_no_dsar"
                )
                updated = crud.get_dsar_mailbox_by_id(mailbox_id)
                return {
                    "mailbox": updated,
                    "synced": True,
                    "messages_read": len(candidates),
                    "extractions": extractions,
                    "message": "Lecture Microsoft 365 effectuée." if extractions else "Aucune demande détectée parmi les 25 derniers emails de la boîte."
                }
            except Exception as exc:
                crud.update_dsar_mailbox_sync(mailbox_id, access_status="graph_fallback_sample")
                mailbox = crud.get_dsar_mailbox_by_id(mailbox_id) or mailbox
                fallback_message = f"Lecture Microsoft 365 indisponible, bascule sur le message exemple. Détail: {str(exc)}"
            else:
                fallback_message = ""
        else:
            fallback_message = "Connecteur Microsoft 365 non configuré, utilisation du message exemple."
    elif mailbox.get("provider") in {"gmail", "imap"}:
        host = metadata.get("imap_host") or ("imap.gmail.com" if mailbox.get("provider") == "gmail" else "")
        port = metadata.get("imap_port") or 993
        username = metadata.get("imap_username") or mailbox.get("email_address")
        password = metadata.get("imap_password") or ""
        folder = metadata.get("imap_folder") or "INBOX"
        if host and username and password:
            try:
                connector = ImapMailConnector(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    folder=folder,
                    use_ssl=True
                )
                candidates = connector.extract_dsar_candidates(top=recent_mail_limit)
                extractions = []
                for candidate in candidates:
                    preview = extract_dsar_from_text_local(candidate.get("text") or "")
                    preview["email_subject"] = candidate.get("subject")
                    preview["email_from"] = candidate.get("from")
                    extractions.append({
                        "source_mailbox": mailbox.get("email_address"),
                        "provider": mailbox.get("provider"),
                        "message_id": candidate.get("message_id"),
                        "extracted_dsar": preview
                    })
                crud.update_dsar_mailbox_sync(
                    mailbox_id,
                    access_status="synced" if extractions else "connected_no_dsar"
                )
                updated = crud.get_dsar_mailbox_by_id(mailbox_id)
                return {
                    "mailbox": updated,
                    "synced": True,
                    "messages_read": len(candidates),
                    "extractions": extractions,
                    "message": "Lecture IMAP effectuée." if extractions else "Aucune demande détectée parmi les 25 derniers emails de la boîte."
                }
            except Exception as exc:
                crud.update_dsar_mailbox_sync(mailbox_id, access_status="imap_fallback_sample")
                mailbox = crud.get_dsar_mailbox_by_id(mailbox_id) or mailbox
                fallback_message = f"Lecture IMAP indisponible, bascule sur le message exemple. Détail: {str(exc)}"
        else:
            fallback_message = "Connecteur IMAP incomplet, utilisation du message exemple."
    else:
        fallback_message = ""

    sample_message = mailbox.get("sample_message") or ""
    if not sample_message.strip():
        crud.update_dsar_mailbox_sync(mailbox_id, access_status="configured")
        return {
            "mailbox": mailbox,
            "synced": False,
            "messages_read": 0,
            "extractions": [],
            "message": fallback_message or "Aucun message exemple configuré pour cette boîte."
        }

    preview = extract_dsar_from_text_local(sample_message)
    crud.update_dsar_mailbox_sync(mailbox_id, access_status="synced")
    updated = crud.get_dsar_mailbox_by_id(mailbox_id)
    return {
        "mailbox": updated,
        "synced": True,
        "messages_read": 1,
        "extractions": [{
            "source_mailbox": updated.get("email_address"),
            "provider": updated.get("provider"),
            "extracted_dsar": preview
        }],
        "message": fallback_message or "Message exemple extrait avec succès."
    }



# ===========================
# AIPD DOSSIER GENERATOR
# ===========================

class AIPDDossierRequest(BaseModel):
    traitement: Traitement
    risques_identifies: Optional[List[Dict[str, Any]]] = []
    aipd_decision: Optional[str] = ""
    niveau_risque_global: Optional[str] = "Moyen"
    risk_review_id: Optional[int] = None


def _format_risk_review_context(saved_review: dict) -> dict:
    evidence = json.loads(saved_review.get("evidence_json") or "{}")
    output = json.loads(saved_review.get("output_json") or "{}")
    q4 = output.get("q4_risques_aipd", {})
    residual = q4.get("residual_risk", {})
    return {
        "saved_review_id": saved_review.get("id"),
        "saved_created_at": saved_review.get("created_at"),
        "saved_aipd_decision": saved_review.get("aipd_decision"),
        "saved_nombre_risques": saved_review.get("nombre_risques"),
        "saved_risques_critiques": saved_review.get("risques_critiques"),
        "saved_risques_eleves": saved_review.get("risques_eleves"),
        "saved_evidence": evidence,
        "saved_residual_risk": residual,
        "saved_mesures_prioritaires": q4.get("mesures_prioritaires", []),
        "saved_aipd": q4.get("aipd", {}),
    }


def _format_incident_review_context(saved_review: dict) -> dict:
    evidence = json.loads(saved_review.get("evidence_json") or "{}")
    output = json.loads(saved_review.get("output_json") or "{}")
    q6 = output.get("q6_incidents", {})
    assessment = q6.get("evaluation_risque_incident", {})
    return {
        "saved_review_id": saved_review.get("id"),
        "saved_created_at": saved_review.get("created_at"),
        "saved_qualification": saved_review.get("qualification"),
        "saved_notifier_cnil": bool(saved_review.get("notifier_cnil")),
        "saved_notifier_personnes": bool(saved_review.get("notifier_personnes")),
        "saved_evidence": evidence,
        "saved_assessment": assessment,
        "saved_notification": q6.get("notification", {}),
    }

@app.post("/generate_aipd_dossier")
async def generate_aipd_dossier(data: AIPDDossierRequest, user: dict = Depends(require_permission("aipd:manage"))):
    try:
        from groq import Groq
        from dotenv import load_dotenv
        load_dotenv("key.env")
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        t = data.traitement.dict()
        risques = data.risques_identifies or []
        review_context = {}
        if data.risk_review_id:
            saved_review = crud.get_risk_review_by_id(data.risk_review_id)
            if saved_review:
                review_context = _format_risk_review_context(saved_review)
        risques_text = "\n".join([
            f"- [{r.get('niveau','?')}] {r.get('scenario','?')} (Score: {r.get('score','?')})"
            for r in risques
        ]) or "Aucun risque critique identifie"

        residual = review_context.get("saved_residual_risk", {})
        residual_text = "Aucun calcul de risque residuel sauvegarde."
        if residual:
            residual_text = (
                f"- Score initial: {residual.get('score_initial', 0)}\n"
                f"- Score residuel: {residual.get('score_residuel', 0)}\n"
                f"- Niveau residuel: {residual.get('niveau_residuel', 'Inconnu')}\n"
                f"- Decision residuelle: {residual.get('decision_residuelle', 'Non definie')}\n"
                f"- Facteurs de mitigation: {', '.join(residual.get('mitigation_factors', [])) or 'Aucun'}"
            )
        measures_text = "\n".join([f"- {m}" for m in review_context.get("saved_mesures_prioritaires", [])]) or "- Aucune mesure prioritaire sauvegardee"
        aipd_context = review_context.get("saved_aipd", {})
        aipd_trigger_text = "\n".join([f"- {r}" for r in aipd_context.get("raisons_declenchement", [])]) or "- Aucun declencheur detaille"

        review_text = "Aucun historique Q4 sauvegarde fourni."
        if review_context:
            review_text = (
                f"- Review ID: {review_context.get('saved_review_id')}\n"
                f"- Date review: {review_context.get('saved_created_at')}\n"
                f"- AIPD decision saved: {review_context.get('saved_aipd_decision')}\n"
                f"- Nombre de risques sauvegardes: {review_context.get('saved_nombre_risques')}\n"
                f"- Risques critiques sauvegardes: {review_context.get('saved_risques_critiques')}\n"
                f"- Risques eleves sauvegardes: {review_context.get('saved_risques_eleves')}\n"
                f"- Evidence saved: {json.dumps(review_context.get('saved_evidence', {}), ensure_ascii=False)}"
            )

        prompt = f"""Tu es un DPO expert RGPD. Génère un dossier AIPD (Analyse d'Impact relative à la Protection des Données) complet, structuré et conforme à l'Article 35 du RGPD et aux lignes directrices CNIL/EDPB.

TRAITEMENT ANALYSÉ:
- Nom: {t.get('nom_traitement', 'Non spécifié')}
- Système: {t.get('systeme', 'Non spécifié')}
- ID: {t.get('id_traitement', 'Non spécifié')}
- Responsable: {t.get('responsable', 'Non spécifié')}
- Finalité: {t.get('finalite', 'Non spécifiée')}
- Base légale: {t.get('base_legale', 'Non définie')}
- Données collectées: {', '.join(t.get('donnees_collectees', []))}
- Données sensibles (Art.9): {'Oui' if t.get('donnees_sensibles') else 'Non'}
- Transfert hors pays: {'Oui' if t.get('transfert_etranger') else 'Non'}
- Durée de conservation: {t.get('duree_conservation', 'Non définie')}
- Privacy by Design: {'Oui' if t.get('privacy_by_design') else 'Non'}
- Mesures de sécurité: {', '.join(t.get('mesures_securite', []) or [])}

RISQUES IDENTIFIÉS:
{risques_text}

HISTORIQUE Q4 SAUVEGARDE:
{review_text}

RISQUE RESIDUEL ET MESURES:
{residual_text}

MESURES PRIORITAIRES SAUVEGARDEES:
{measures_text}

DECLENCHEURS AIPD SAUVEGARDES:
{aipd_trigger_text}

NIVEAU DE RISQUE GLOBAL: {data.niveau_risque_global}
DÉCISION AIPD: {data.aipd_decision or 'À déterminer'}

Génère un dossier AIPD COMPLET en français avec ces 6 sections obligatoires:

## 1. DESCRIPTION DU TRAITEMENT
Décris en détail le traitement, sa finalité, le contexte opérationnel (QALITAS/GMAO PRO), les catégories de données et les personnes concernées.

## 2. NÉCESSITÉ ET PROPORTIONNALITÉ
Évalue si la collecte est nécessaire et proportionnée à la finalité. Analyse la minimisation des données, la durée de conservation et la base légale.

## 3. ANALYSE DES RISQUES
Pour chaque risque identifié, évalue:
- Vraisemblance et gravité
- Impact sur les droits et libertés des personnes
- Nature du risque (confidentialité, intégrité, disponibilité)

## 4. MESURES D'ATTÉNUATION
Liste les mesures techniques et organisationnelles recommandées, avec priorité et référence normative (ISO 27001, Art. 32 RGPD, etc.).

## 5. AVIS DU DPO
Formule un avis professionnel sur l'acceptabilité du traitement, les conditions de mise en œuvre et les actions correctives urgentes.

## 6. DÉCISION FINALE
Conclusion structurée: Acceptable / Acceptable sous conditions / Risque résiduel inacceptable — avec justification juridique.

Ton: professionnel, juridique, auditable. Maximum 700 mots. Uniquement en français."""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.2
        )
        dossier = response.choices[0].message.content
        aipd_structure = {
            "review_context": {
                "risk_review_id": data.risk_review_id,
                "saved_review_id": review_context.get("saved_review_id"),
                "saved_created_at": review_context.get("saved_created_at"),
                "aipd_decision_saved": review_context.get("saved_aipd_decision"),
            },
            "risques": {
                "nombre": len(risques),
                "items": risques,
            },
            "risque_residuel": residual,
            "mesures_prioritaires": review_context.get("saved_mesures_prioritaires", []),
            "declencheurs_aipd": aipd_context.get("raisons_declenchement", []),
        }
        output = {
            "dossier_aipd": dossier,
            "aipd_structure": aipd_structure,
            "traitement": t.get("nom_traitement", ""),
            "niveau_risque": data.niveau_risque_global,
            "risk_review_id": data.risk_review_id,
            "date_generation": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "statut": "genere"
        }
        # Persist DPIA dossier
        try:
            dpia_id = crud.save_dpia(data.dict(), dossier, data.niveau_risque_global)
            output["dpia_id"] = dpia_id
        except Exception as db_err:
            print(f"[DB] Warning: could not save DPIA: {db_err}")
        return output
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Erreur génération AIPD: {str(e)}\n{traceback.format_exc()}")


# ===========================
# CNIL NOTIFICATION DOSSIER
# ===========================

class CNILNotificationRequest(BaseModel):
    incident: Incident
    traitement: Traitement
    qualification: Optional[str] = "confirmed"
    notifier_autorite: Optional[bool] = True
    notifier_personnes: Optional[bool] = False
    delai_notification: Optional[str] = "72 heures"
    incident_review_id: Optional[int] = None

@app.post("/generate_cnil_notification_dossier")
async def generate_cnil_notification_dossier(
    data: CNILNotificationRequest,
    user: dict = Depends(require_permission("incident:manage")),
):
    try:
        from groq import Groq
        from dotenv import load_dotenv
        load_dotenv("key.env")
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        inc = data.incident.dict()
        t = data.traitement.dict()
        review_context = {}
        if data.incident_review_id:
            saved_review = crud.get_incident_review_by_id(data.incident_review_id)
            if saved_review:
                review_context = _format_incident_review_context(saved_review)
        notification_id = f"CNIL-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{inc.get('id_incident','INC')}"
        review_text = "Aucun historique Q6 sauvegarde fourni."
        if review_context:
            review_text = (
                f"- Review ID: {review_context.get('saved_review_id')}\n"
                f"- Date review: {review_context.get('saved_created_at')}\n"
                f"- Qualification saved: {review_context.get('saved_qualification')}\n"
                f"- Notification autorite saved: {'Oui' if review_context.get('saved_notifier_cnil') else 'Non'}\n"
                f"- Notification personnes saved: {'Oui' if review_context.get('saved_notifier_personnes') else 'Non'}\n"
                f"- Evidence saved: {json.dumps(review_context.get('saved_evidence', {}), ensure_ascii=False)}"
            )
        assessment = review_context.get("saved_assessment", {})
        assessment_text = "Aucune evaluation de risque incident sauvegardee."
        if assessment:
            assessment_text = (
                f"- Score incident: {assessment.get('score_incident', 0)}\n"
                f"- Niveau incident: {assessment.get('niveau_incident', 'Inconnu')}\n"
                f"- Dimensions: {', '.join(assessment.get('dimensions', [])) or 'Aucune'}\n"
                f"- Volume sensible detecte: {assessment.get('sensitive_volume_detected', 0)}"
            )
        notification_ctx = review_context.get("saved_notification", {})
        notification_reasons_text = "\n".join([f"- {r}" for r in notification_ctx.get("raisons_cnil", [])]) or "- Aucune justification sauvegardee"

        prompt = f"""Tu es le DPO de TIM Consulting. Génère une notification formelle de violation de données à la CNIL (Autorité de protection des données), conforme aux Articles 33 et 34 du RGPD.

INCIDENT:
- ID: {inc.get('id_incident', 'INC-001')}
- Date de détection: {inc.get('date_detection', 'Non renseignée')}
- Type: {inc.get('type_incident', 'Non renseigné')}
- Description: {inc.get('description', 'Non renseignée')}
- Données affectées: {', '.join(inc.get('donnees_affectees', []))}
- Nombre de personnes affectées: {inc.get('nombre_personnes_affectees', 0)}
- Gravité: {inc.get('gravite_incident', 1)}/3
- Données sensibles impliquées: {'Oui' if inc.get('donnees_sensibles_impliquees') else 'Non'}
- Données chiffrées: {'Oui' if inc.get('donnees_chiffrees') else 'Non'}

TRAITEMENT CONCERNÉ:
- Nom: {t.get('nom_traitement', 'Non spécifié')}
- Système: {t.get('systeme', 'Non spécifié')}
- Responsable: {t.get('responsable', 'Non spécifié')}

HISTORIQUE Q6 SAUVEGARDE:
{review_text}

DÉCISION DE NOTIFICATION:
- Notifier l'autorité de contrôle (CNIL/INPDP): {'Oui' if data.notifier_autorite else 'Non'}
- Notifier les personnes concernées (Art.34): {'Oui' if data.notifier_personnes else 'Non'}
- Référence notification: {notification_id}

Génère la notification CNIL complète en français avec ces sections:

## NOTIFICATION DE VIOLATION DE DONNÉES PERSONNELLES
### Réf: {notification_id}

### 1. IDENTITÉ DU RESPONSABLE DU TRAITEMENT
Nom, coordonnées, DPO.

### 2. NATURE DE LA VIOLATION
Description précise de l'incident, type de violation (confidentialité/intégrité/disponibilité), chronologie.

### 3. CATÉGORIES ET NOMBRE DE PERSONNES CONCERNÉES
Détail des personnes et données impactées.

### 4. CONSÉQUENCES PROBABLES
Analyse des risques pour les droits et libertés des personnes concernées.

### 5. MESURES PRISES OU PROPOSÉES
Actions immédiates de confinement, mesures correctives, calendrier.

### 6. OBLIGATIONS DE COMMUNICATION (Art. 34)
Décision motivée sur la notification aux personnes concernées.

Ton: officiel, juridique. Uniquement en français."""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
            temperature=0.1
        )
        notification_text = response.choices[0].message.content

        notification_structure = {
            "notification_id": notification_id,
            "date_notification": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "responsable_traitement": t.get("responsable", "TIM Consulting"),
            "systeme": t.get("systeme", ""),
            "incident": {
                "id": inc.get("id_incident"),
                "date_detection": inc.get("date_detection"),
                "type": inc.get("type_incident"),
                "description": inc.get("description"),
                "donnees_affectees": inc.get("donnees_affectees", []),
                "nb_personnes": inc.get("nombre_personnes_affectees", 0),
                "gravite": inc.get("gravite_incident", 1),
                "donnees_sensibles": inc.get("donnees_sensibles_impliquees", False),
                "dimensions": assessment.get("dimensions", []),
                "score_incident": assessment.get("score_incident"),
                "niveau_incident": assessment.get("niveau_incident")
            },
            "decision": {
                "notifier_autorite": data.notifier_autorite,
                "notifier_personnes": data.notifier_personnes,
                "delai": data.delai_notification,
                "article_base": "Art. 33 RGPD" if data.notifier_autorite else "Art. 33.1 RGPD - Exception",
                "raisons_notification": notification_ctx.get("raisons_cnil", [])
            },
            "review_context": {
                "incident_review_id": data.incident_review_id,
                "saved_review_id": review_context.get("saved_review_id"),
                "saved_created_at": review_context.get("saved_created_at"),
                "saved_qualification": review_context.get("saved_qualification"),
            },
            "statut": "pret_a_soumettre"
        }

        output = {
            "notification_id": notification_id,
            "notification_text": notification_text,
            "notification_structure": notification_structure,
            "incident_review_id": data.incident_review_id,
            "date_generation": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "statut": "genere"
        }
        # Persist CNIL notification
        try:
            cnil_id = crud.save_cnil_notification(notification_id, data.dict(), output)
            output["cnil_db_id"] = cnil_id
        except Exception as db_err:
            print(f"[DB] Warning: could not save CNIL notification: {db_err}")
        return output
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Erreur génération CNIL: {str(e)}\n{traceback.format_exc()}")


class DSARReponseRequest(BaseModel):
    qualification: str
    nom_demandeur: str
    type_droit: str
    article: str
    systeme: str
    donnees: str
    date_reception: str
    date_limite: str

@app.post("/generate_reponse_dsar")
async def generate_reponse_dsar(data: DSARReponseRequest, user: dict = Depends(require_permission("dsar:respond"))):
    try:
        import os
        from groq import Groq
        from dotenv import load_dotenv
        load_dotenv("key.env")
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        if data.qualification == "abusive":
            instruction = "La demande est jugee abusive (trop de demandes precedentes). Redige un refus motive professionnel et juridiquement justifie."
        elif data.qualification == "invalide":
            instruction = "La demande est invalide (identite non verifiee ou donnees insuffisantes). Redige une demande de complementation d information."
        else:
            instruction = f"La demande est valide. Redige une reponse confirmant la prise en charge et indiquant le delai de traitement (date limite: {data.date_limite})."

        prompt = f"""Tu es le DPO de TIM Consulting. Redige une reponse officielle DSAR en francais.

CONTEXTE:
- Demandeur: {data.nom_demandeur}
- Droit exerce: {data.type_droit} ({data.article})
- Systeme concerne: {data.systeme}
- Donnees concernees: {data.donnees}
- Date de reception: {data.date_reception}
- Date limite de reponse: {data.date_limite}
- Instruction: {instruction}

REDIGE une lettre officielle avec:
- Objet de la lettre
- Accusé de réception de la demande
- Reference juridique ({data.article})
- Decision et justification
- Prochaines etapes
- Formule de politesse professionnelle
- Signature: DPO - TIM Consulting

Langue: francais uniquement. Ton: professionnel et juridique. Maximum 300 mots."""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.2
        )
        return {"reponse": response.choices[0].message.content}
    except Exception as e:
        import traceback
        return {
            "reponse": f"❌ Erreur lors de la génération: {str(e)}\n\nTraceback: {traceback.format_exc()}",
            "error": str(e)
        }


# ==============================================================================
# READ / HISTORY ENDPOINTS
# ==============================================================================

@app.get("/latest/analysis")
def latest_analysis(user: dict = Depends(require_permission("analysis:view"))):
    """Returns the latest full workflow result from DB for Q4 tab auto-load."""
    try:
        treatments = crud.get_treatments(limit=1)
        if not treatments:
            return {"available": False, "message": "Aucune analyse disponible. Lancez un scan QALITAS."}
        latest = treatments[0]
        import json
        output_json = latest.get("output_json")
        agent_a = json.loads(output_json) if output_json else {}
        try:
            from agents.agent_b import run_agent_b
            from agents.agent_d import run_agent_d
            input_json = latest.get("input_json")
            traitement = json.loads(input_json) if input_json else {}
            agent_b = run_agent_b(traitement, agent_a)
            agent_d = run_agent_d(agent_a, agent_b)
            return {
                "available": True,
                "created_at": latest.get("created_at"),
                "nom_traitement": latest.get("nom_traitement"),
                "systeme": latest.get("systeme"),
                "agent_a": agent_a,
                "agent_b": agent_b,
                "agent_d": agent_d,
            }
        except Exception as e:
            return {"available": True, "created_at": latest.get("created_at"),
                    "nom_traitement": latest.get("nom_traitement"), "agent_a": agent_a, "error": str(e)}
    except Exception as e:
        return {"available": False, "message": str(e)}


@app.post("/declare_incident")
def declare_incident(incident: Incident, user: dict = Depends(require_permission("incident:declare"))):
    """Q6 — Declare incident using last saved treatment."""
    try:
        treatments = crud.get_treatments(limit=1)
        if not treatments:
            raise HTTPException(status_code=404, detail="Aucun traitement disponible. Lancez d'abord un scan QALITAS.")
        import json
        latest = treatments[0]
        traitement = json.loads(latest.get("input_json", "{}"))
        agent_a    = json.loads(latest.get("output_json", "{}"))
        inc = incident.dict()
        result = run_agent_b(traitement, agent_a, inc)
        try:
            crud.save_violation(inc, result)
            incident_review_id = crud.save_incident_review(
                incident_input=inc,
                agent_b_output=result,
                source_analysis_id=latest.get("id")
            )
            result["persistence"] = {
                "analysis_id": latest.get("id"),
                "incident_review_id": incident_review_id
            }
        except Exception as db_err:
            print(f"[DB] Warning: {db_err}")
        return {"incident": inc, "traitement": latest.get("nom_traitement"), "agent_b": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/history/treatments")
def history_treatments(
    systeme: str = None,
    limit: int = 50,
    user: dict = Depends(require_any_permission("analysis:view", "register:view", "register:view_limited")),
):
    """Return all stored treatment analyses."""
    treatments = crud.get_treatment_summaries(systeme=systeme, limit=limit)
    return {
        "treatments": crud.attach_latest_dpo_validations(
            treatments,
            target_types=["treatment", "legal_basis", "dpia"]
        )
    }


@app.get("/history/treatments/{row_id}")
def history_treatment_detail(
    row_id: int,
    user: dict = Depends(require_any_permission("analysis:view", "register:view", "register:view_limited")),
):
    """Return one full stored treatment row when the UI explicitly opens it."""
    row = crud.get_treatment_by_row_id(row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Analyse introuvable.")
    detailed = crud.attach_latest_dpo_validations([row], target_types=["treatment", "legal_basis", "dpia"])
    return {"treatment": detailed[0] if detailed else row}


@app.get("/history/dsars")
def history_dsars(
    statut: str = None,
    limit: int = 50,
    user: dict = Depends(require_permission("dsar:view")),
):
    """Return all stored DSAR requests."""
    return {"dsars": crud.get_dsars(statut=statut, limit=limit)}


@app.get("/history/dsar-executions")
def history_dsar_executions(
    id_demande: str = None,
    limit: int = 100,
    user: dict = Depends(require_permission("dsar:view")),
):
    """Return DSAR execution audit trail."""
    return {"dsar_executions": crud.get_dsar_executions(id_demande=id_demande, limit=limit)}


@app.get("/history/violations")
def history_violations(
    statut: str = None,
    limit: int = 50,
    user: dict = Depends(require_permission("incident:view")),
):
    """Return the Art. 33.5 violation register."""
    return {"violations": crud.get_violations(statut=statut, limit=limit)}


@app.get("/history/risk-reviews")
def history_risk_reviews(
    systeme: str = None,
    module: str = None,
    limit: int = 100,
    user: dict = Depends(require_any_permission("analysis:view", "aipd:view", "aipd:view_assigned")),
):
    """Return saved Agent B Q4 risk review snapshots."""
    return {
        "risk_reviews": crud.get_risk_reviews(
            systeme=systeme,
            module=module,
            limit=limit
        )
    }


@app.get("/history/incident-reviews")
def history_incident_reviews(
    id_incident: str = None,
    systeme: str = None,
    limit: int = 100,
    user: dict = Depends(require_permission("incident:view")),
):
    """Return saved Agent B Q6 incident evidence snapshots."""
    return {
        "incident_reviews": crud.get_incident_reviews(
            id_incident=id_incident,
            systeme=systeme,
            limit=limit
        )
    }


@app.get("/history/governance-snapshots")
def history_governance_snapshots(
    id_traitement: str = None,
    systeme: str = None,
    module: str = None,
    limit: int = 50,
    user: dict = Depends(require_any_permission("reports:view", "reports:view_limited")),
):
    """Return governance snapshot metadata for trend tracking."""
    return {
        "governance_snapshots": crud.get_governance_snapshot_summaries(
            id_traitement=id_traitement,
            systeme=systeme,
            module=module,
            limit=limit
        )
    }


@app.get("/history/governance-snapshot/latest")
def history_governance_snapshot_latest(
    id_traitement: str = None,
    systeme: str = None,
    module: str = None,
    max_snapshot_bytes: int = 5 * 1024 * 1024,
    user: dict = Depends(require_any_permission("reports:view", "reports:view_limited")),
):
    """Return one recent compact governance snapshot payload for cockpit reloads."""
    snapshot = crud.get_latest_governance_snapshot(
        id_traitement=id_traitement,
        systeme=systeme,
        module=module,
        max_snapshot_bytes=max_snapshot_bytes,
    )
    return {"snapshot": snapshot}


@app.get("/history/dpia")
def history_dpia(limit: int = 50, user: dict = Depends(require_permission("aipd:view"))):
    """Return all stored DPIA dossiers."""
    return {"dpia_dossiers": crud.get_dpia_dossiers(limit=limit)}


@app.get("/history/cnil")
def history_cnil(limit: int = 50, user: dict = Depends(require_permission("incident:view"))):
    """Return all stored CNIL notifications."""
    return {"cnil_notifications": crud.get_cnil_notifications(limit=limit)}


@app.get("/history/unstructured")
def history_unstructured(
    linked_treatment_id: str = None,
    systeme: str = None,
    module: str = None,
    limit: int = 100,
    user: dict = Depends(require_permission("register:view")),
):
    """Return stored unstructured scan evidence for Q1."""
    return {
        "unstructured_scans": crud.get_unstructured_scans(
            linked_treatment_id=linked_treatment_id,
            systeme=systeme,
            module=module,
            limit=limit
        )
    }


@app.get("/register")
def register_entries(
    systeme: str = None,
    limit: int = 100,
    user: dict = Depends(require_any_permission("register:view", "register:view_limited")),
):
    """Return enriched operational Article 30 register snapshots."""
    entries = crud.get_register_entries(systeme=systeme, limit=limit)
    enriched, summary = _enrich_register_entries(entries)
    compact = [_compact_register_entry_for_ui(entry) for entry in enriched]
    return {"summary": summary, "register": compact}


@app.get("/inventory/treatments")
def inventory_treatments(
    systeme: str = None,
    module: str = None,
    limit: int = 100,
    user: dict = Depends(require_any_permission("register:view", "register:view_limited")),
):
    """Return central RGPD inventory treatment snapshots."""
    treatments = crud.get_inventory_treatments(
        systeme=systeme,
        module=module,
        limit=limit
    )
    return {
        "inventory_treatments": crud.attach_latest_dpo_validations(
            treatments,
            target_types=["treatment", "legal_basis", "dpia"]
        )
    }


@app.get("/inventory/fields")
def inventory_fields(
    inventory_treatment_id: int = None,
    limit: int = 500,
    user: dict = Depends(require_any_permission("register:view", "register:view_limited")),
):
    """Return stored inventory fields for structured and unstructured data."""
    return {
        "inventory_fields": crud.get_inventory_fields(
            inventory_treatment_id=inventory_treatment_id,
            limit=limit
        )
    }


@app.get("/inventory/flows")
def inventory_flows(
    inventory_treatment_id: int = None,
    limit: int = 500,
    user: dict = Depends(require_any_permission("register:view", "register:view_limited")),
):
    """Return stored Q1 data flow mappings."""
    return {
        "inventory_flows": crud.get_inventory_flows(
            inventory_treatment_id=inventory_treatment_id,
            limit=limit
        )
    }


@app.get("/inventory/alerts")
def inventory_alerts(
    inventory_treatment_id: int = None,
    severity: str = None,
    limit: int = 500,
    user: dict = Depends(require_any_permission("register:view", "register:view_limited")),
):
    """Return stored Q1 alerts."""
    return {
        "inventory_alerts": crud.get_inventory_alerts(
            inventory_treatment_id=inventory_treatment_id,
            severity=severity,
            limit=limit
        )
    }


class ActionRequest(BaseModel):
    title: str
    description: Optional[str] = ""
    severity: Optional[str] = "Moyen"
    owner: Optional[str] = "DPO"
    due_date: Optional[str] = None
    linked_treatment_id: Optional[str] = None
    linked_register_id: Optional[int] = None
    source_regle_id: Optional[str] = None
    recommendation: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ActionProofRequest(BaseModel):
    proof_type: Optional[str] = "document"
    title: Optional[str] = None
    description: Optional[str] = None
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_base64: Optional[str] = None
    submitted_by: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ActionProofValidationRequest(BaseModel):
    decision: str
    validator: Optional[str] = None
    notes: Optional[str] = None


class ActionCloseRequest(BaseModel):
    validator: Optional[str] = None
    justification: Optional[str] = None


@app.post("/actions")
def create_action(data: ActionRequest, user: dict = Depends(require_permission("actions:update"))):
    """Create a manual corrective action."""
    if not data.title or not data.title.strip():
        raise HTTPException(status_code=400, detail="Action title is required.")
    action_id = crud.save_action(
        title=data.title.strip(),
        description=data.description or "",
        severity=data.severity or "Moyen",
        owner=data.owner or _actor_name(user),
        due_date=data.due_date,
        linked_treatment_id=data.linked_treatment_id,
        linked_register_id=data.linked_register_id,
        source_regle_id=data.source_regle_id,
        recommendation=data.recommendation,
        metadata=data.metadata or {"origin": "manual"}
    )
    crud.save_audit_event(
        user,
        "actions.create",
        "action",
        str(action_id),
        {"title": data.title.strip(), "severity": data.severity or "Moyen"},
    )
    return {"action": crud.get_action_by_id(action_id)}


@app.get("/actions")
def list_actions(
    statut: str = None,
    severity: str = None,
    limit: int = 100,
    user: dict = Depends(require_any_permission("actions:view", "actions:view_assigned")),
):
    """Return corrective actions generated from Q2 gaps."""
    actions = crud.get_actions(statut=statut, severity=severity, limit=limit)
    if not _user_has_any(user, "actions:view"):
        actions = [a for a in actions if _is_actor_assigned_to_action(user, a)]
    return {"actions": actions}


@app.get("/actions/{action_id}")
def get_action(action_id: int, user: dict = Depends(require_any_permission("actions:view", "actions:view_assigned"))):
    """Return one corrective action with its proofs."""
    action = crud.get_action_by_id(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found.")
    if not _user_has_any(user, "actions:view") and not _is_actor_assigned_to_action(user, action):
        raise HTTPException(status_code=403, detail="Action reservee a son responsable ou au DPO.")
    return {"action": action}


@app.patch("/actions/{action_id}/status")
def patch_action_status(
    action_id: int,
    status: str,
    user: dict = Depends(require_any_permission("actions:update", "actions:update_assigned")),
):
    """Update one corrective action status."""
    action = crud.get_action_by_id(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found.")
    if not _user_has_any(user, "actions:update") and not _is_actor_assigned_to_action(user, action):
        raise HTTPException(status_code=403, detail="Action reservee a son responsable ou au DPO.")
    allowed = ["A faire", "En cours", "Cloturee"]
    if status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid status. Allowed: {allowed}")
    if status == "Cloturee":
        raise HTTPException(
            status_code=400,
            detail="Use /actions/{action_id}/close so closure is backed by an accepted proof."
        )
    success = crud.update_action_status(action_id=action_id, status=status)
    crud.save_audit_event(user, "actions.status_update", "action", str(action_id), {"status": status})
    return {"action": crud.get_action_by_id(action_id)}


@app.post("/actions/{action_id}/proofs")
def add_action_proof(
    action_id: int,
    data: ActionProofRequest,
    user: dict = Depends(require_any_permission("actions:update", "actions:update_assigned")),
):
    """Attach a proof document or note to a corrective action."""
    action = crud.get_action_by_id(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found.")
    if not _user_has_any(user, "actions:update") and not _is_actor_assigned_to_action(user, action):
        raise HTTPException(status_code=403, detail="Action reservee a son responsable ou au DPO.")
    if not data.description and not data.file_base64:
        raise HTTPException(status_code=400, detail="Provide a proof description or a file.")

    file_path = None
    file_size = None
    checksum = None
    safe_name = data.file_name
    if data.file_base64:
        raw_payload = data.file_base64
        if "," in raw_payload and raw_payload.lower().startswith("data:"):
            raw_payload = raw_payload.split(",", 1)[1]
        try:
            file_bytes = base64.b64decode(raw_payload, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 file content.")
        max_size = 8 * 1024 * 1024
        if len(file_bytes) > max_size:
            raise HTTPException(status_code=400, detail="Proof file is too large. Max: 8 MB.")
        proof_dir = Path("data") / "action_proofs"
        proof_dir.mkdir(parents=True, exist_ok=True)
        original_name = data.file_name or f"preuve_action_{action_id}.bin"
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", original_name)[:120] or f"preuve_{action_id}.bin"
        checksum = hashlib.sha256(file_bytes).hexdigest()
        stored_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{checksum[:10]}_{safe_name}"
        stored_path = proof_dir / stored_name
        stored_path.write_bytes(file_bytes)
        file_path = str(stored_path)
        file_size = len(file_bytes)

    try:
        proof = crud.save_action_proof(
            action_id=action_id,
            proof_type=data.proof_type or "document",
            title=data.title,
            description=data.description,
            file_name=safe_name,
            file_path=file_path,
            mime_type=data.mime_type,
            file_size=file_size,
            checksum=checksum,
            submitted_by=data.submitted_by or _actor_name(user),
            metadata=data.metadata or {}
        )
        crud.save_audit_event(
            user,
            "actions.proof_submit",
            "action",
            str(action_id),
            {"proof_id": proof.get("proof_id"), "proof_type": data.proof_type or "document"},
        )
        return {"proof": proof}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/actions/{action_id}/proofs")
def list_action_proofs(
    action_id: int,
    status: str = None,
    limit: int = 100,
    user: dict = Depends(require_any_permission("actions:view", "actions:view_assigned")),
):
    """Return proofs attached to a corrective action."""
    action = crud.get_action_by_id(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found.")
    if not _user_has_any(user, "actions:view") and not _is_actor_assigned_to_action(user, action):
        raise HTTPException(status_code=403, detail="Action reservee a son responsable ou au DPO.")
    return {"proofs": crud.get_action_proofs(action_id=action_id, status=status, limit=limit)}


@app.post("/action-proofs/{proof_id}/validate")
def validate_action_proof(
    proof_id: str,
    data: ActionProofValidationRequest,
    user: dict = Depends(require_permission("ai:validate")),
):
    """Accept or reject one corrective-action proof."""
    try:
        proof = crud.validate_action_proof(
            proof_id=proof_id,
            decision=data.decision,
            validator=data.validator or _actor_name(user),
            notes=data.notes
        )
        crud.save_audit_event(
            user,
            "actions.proof_validate",
            "action_proof",
            proof_id,
            {"decision": data.decision},
        )
        return {"proof": proof}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/actions/{action_id}/close")
def close_action(
    action_id: int,
    data: ActionCloseRequest,
    user: dict = Depends(require_permission("actions:close")),
):
    """Close an action only after a DPO-accepted proof exists."""
    try:
        action = crud.close_action(
            action_id=action_id,
            validator=data.validator or _actor_name(user),
            justification=data.justification
        )
        crud.save_audit_event(user, "actions.close", "action", str(action_id), {"justification": data.justification})
        return {"action": action}
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc))


# ==============================================================================
# DPO VALIDATIONS (human decision layer)
# ==============================================================================

class DPOValidationRequest(BaseModel):
    target_type: str
    decision: str
    target_id: Optional[str] = None
    target_label: Optional[str] = None
    validator: Optional[str] = None
    role: Optional[str] = None
    justification: Optional[str] = None
    source_system: Optional[str] = None
    source_module: Optional[str] = None
    evidence: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


@app.post("/dpo/validations")
def create_dpo_validation(data: DPOValidationRequest, user: dict = Depends(require_permission("ai:validate"))):
    """
    Save the final DPO decision on an agent recommendation.

    Agents can recommend, classify and pre-fill. This endpoint records the
    human validation/rejection needed for traceability and audit evidence.
    """
    allowed_targets = [
        "treatment",
        "legal_basis",
        "dpia",
        "cnil_notification",
        "dsar",
        "incident",
        "action",
        "field_classification",
    ]
    allowed_decisions = ["valide", "rejete", "correction_requise", "cloture", "preuve_fournie"]
    if data.target_type not in allowed_targets:
        raise HTTPException(status_code=400, detail=f"Invalid target_type. Allowed: {allowed_targets}")
    if data.decision not in allowed_decisions:
        raise HTTPException(status_code=400, detail=f"Invalid decision. Allowed: {allowed_decisions}")

    actor = _actor_name(user)
    role_label = ROLE_LABELS.get(user.get("role"), user.get("role") or "DPO")
    applied_updates = []
    try:
        evidence_payload = copy.deepcopy(data.evidence or {})
        if isinstance(evidence_payload, dict) and evidence_payload.get("file_base64"):
            raw_payload = evidence_payload.get("file_base64")
            if isinstance(raw_payload, str) and "," in raw_payload and raw_payload.lower().startswith("data:"):
                raw_payload = raw_payload.split(",", 1)[1]
            try:
                file_bytes = base64.b64decode(raw_payload, validate=True)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid evidence file content.")
            max_size = 8 * 1024 * 1024
            if len(file_bytes) > max_size:
                raise HTTPException(status_code=400, detail="Evidence file is too large. Max: 8 MB.")
            proof_dir = Path("data") / "dpo_evidence"
            proof_dir.mkdir(parents=True, exist_ok=True)
            original_name = evidence_payload.get("file_name") or f"dpo_evidence_{datetime.now().strftime('%Y%m%d%H%M%S')}.bin"
            safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", original_name)[:120] or "dpo_evidence.bin"
            checksum = hashlib.sha256(file_bytes).hexdigest()
            stored_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{checksum[:10]}_{safe_name}"
            stored_path = proof_dir / stored_name
            stored_path.write_bytes(file_bytes)
            evidence_payload["file_path"] = str(stored_path)
            evidence_payload["file_size"] = len(file_bytes)
            evidence_payload["checksum"] = checksum
            evidence_payload.pop("file_base64", None)

        if data.target_type == "dsar" and data.decision == "cloture" and data.target_id:
            if crud.update_dsar_statut(data.target_id, "Cloture"):
                applied_updates.append("dsar_cloturee")
        elif data.target_type == "incident" and data.decision == "cloture" and data.target_id:
            if crud.update_violation_statut(data.target_id, "Cloture", True):
                applied_updates.append("incident_cloture")
        elif data.target_type == "action" and data.decision == "cloture" and data.target_id:
            try:
                closed_action = crud.close_action(
                    int(data.target_id),
                    validator=data.validator or actor,
                    justification=data.justification
                )
                crud.save_audit_event(
                    user,
                    "dpo.validation",
                    data.target_type,
                    data.target_id,
                    {"decision": data.decision, "applied_updates": ["action_cloturee"]},
                )
                return {
                    "validation_id": closed_action.get("dpo_validation_id"),
                    "proof_reference": closed_action.get("dpo_validation_id"),
                    "proof_status": "opposable",
                    "target_type": data.target_type,
                    "target_id": data.target_id,
                    "decision": data.decision,
                    "applied_updates": ["action_cloturee"],
                    "action": closed_action
                }
            except ValueError:
                raise HTTPException(status_code=400, detail="Action target_id must be numeric and must have an accepted proof.")
        elif data.target_type == "dpia" and data.target_id:
            try:
                statut = "valide" if data.decision == "valide" else "rejete" if data.decision == "rejete" else "correction_requise"
                if crud.update_dpia_statut(int(data.target_id), statut):
                    applied_updates.append(f"dpia_{statut}")
            except ValueError:
                # Some contextual AIPD validations target a treatment key instead of a generated dossier id.
                pass
        elif data.target_type == "cnil_notification" and data.target_id:
            statut = "valide" if data.decision == "valide" else "correction_requise" if data.decision == "correction_requise" else "rejete"
            if crud.update_cnil_notification_statut(data.target_id, statut):
                applied_updates.append(f"cnil_{statut}")

        validation_id = crud.save_dpo_validation(
            target_type=data.target_type,
            target_id=data.target_id,
            target_label=data.target_label,
            decision=data.decision,
            validator=data.validator or actor,
            role=data.role or role_label,
            justification=data.justification,
            source_system=data.source_system,
            source_module=data.source_module,
            evidence=evidence_payload,
            metadata=data.metadata or {}
        )
        proof_reference = validation_id
        crud.save_audit_event(
            user,
            "dpo.validation",
            data.target_type,
            data.target_id or str(validation_id),
            {"decision": data.decision, "applied_updates": applied_updates},
        )
        return {
            "validation_id": validation_id,
            "proof_reference": proof_reference,
            "proof_status": "opposable",
            "target_type": data.target_type,
            "target_id": data.target_id,
            "decision": data.decision,
            "applied_updates": applied_updates
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dpo/validations")
def list_dpo_validations(
    target_type: str = None,
    decision: str = None,
    limit: int = 100,
    user: dict = Depends(require_permission("proofs:view")),
):
    """Return the DPO decision audit trail."""
    return {
        "validations": crud.get_dpo_validations(
            target_type=target_type,
            decision=decision,
            limit=limit
        )
    }


@app.get("/dpo/proofs")
def list_dpo_proofs(limit: int = 100, user: dict = Depends(require_permission("proofs:view"))):
    """Return DPO proof history in an audit-friendly shape."""
    return {"proofs": crud.get_dpo_proof_history(limit=limit)}


@app.get("/dpo/memory")
def list_dpo_memory(
    target_type: Optional[str] = None,
    source_system: Optional[str] = None,
    source_module: Optional[str] = None,
    reusable_only: bool = True,
    limit: int = 100,
    user: dict = Depends(require_permission("proofs:view")),
):
    """Return reusable DPO feedback memories created from final validations."""
    return {
        "memory": crud.get_dpo_feedback_memory(
            target_type=target_type,
            source_system=source_system,
            source_module=source_module,
            reusable_only=reusable_only,
            limit=limit,
        )
    }


@app.get("/dpo/memory/similar")
def list_similar_dpo_memory(
    target_type: Optional[str] = None,
    source_system: Optional[str] = None,
    source_module: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 5,
    user: dict = Depends(require_permission("proofs:view")),
):
    """Return similar past DPO decisions for agent/context assistance."""
    return {
        "memory": crud.find_similar_dpo_memory(
            target_type=target_type,
            source_system=source_system,
            source_module=source_module,
            query=query,
            limit=limit,
        )
    }


@app.get("/ml/dsar/dataset")
def ml_dsar_dataset(user: dict = Depends(require_permission("proofs:view"))):
    """Return the current DSAR ML dataset size and label distribution."""
    try:
        return dsar_dataset_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ml/dsar/train")
def ml_dsar_train(user: dict = Depends(require_permission("ai:validate"))):
    """Train or refresh the advisory DSAR intent classifier."""
    try:
        result = train_dsar_intent_model()
        crud.save_audit_event(user, "ml.train", "dsar", "intent_classifier", result)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ml/dsar/predict")
def ml_dsar_predict(data: MLTextRequest, user: dict = Depends(require_permission("analysis:view"))):
    """Predict a DSAR intent without changing any official decision."""
    try:
        return predict_dsar_intent(data.texte)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ml/assistant/dataset")
def ml_assistant_dataset(user: dict = Depends(require_permission("assistant:chat"))):
    """Return DPO assistant intent dataset and model status."""
    try:
        return assistant_dataset_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ml/assistant/train")
def ml_assistant_train(user: dict = Depends(require_permission("ai:validate"))):
    """Train or refresh the DPO assistant TF-IDF + Logistic Regression intent classifier."""
    try:
        result = train_assistant_intent_model()
        crud.save_audit_event(user, "ml.train", "assistant", "intent_classifier", result)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ml/assistant/predict")
def ml_assistant_predict(data: MLTextRequest, user: dict = Depends(require_permission("assistant:chat"))):
    """Predict assistant question intent without generating an answer."""
    try:
        return predict_assistant_intent(data.texte)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/assistant-dpo/chat")
def assistant_dpo_chat(data: AssistantChatRequest, user: dict = Depends(require_permission("assistant:chat"))):
    """Free-text, read-only DPO assistant grounded in platform data + RAG."""
    question = (data.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required.")
    try:
        result = generate_assistant_answer(question, history=(data.history or [])[-6:], user=user)
        crud.save_audit_event(
            user,
            "assistant.chat",
            "assistant_dpo",
            result.get("intent", {}).get("label", "unknown"),
            {
                "question": question[:500],
                "scope": result.get("scope"),
                "intent": result.get("intent"),
                "sources": result.get("sources", []),
            },
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ml/fields/dataset")
def ml_fields_dataset(user: dict = Depends(require_permission("proofs:view"))):
    """Return the current advisory field-classification dataset summary."""
    try:
        return field_dataset_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ml/fields/train")
def ml_fields_train(user: dict = Depends(require_permission("ai:validate"))):
    """Train or refresh the advisory field semantic classifier."""
    try:
        result = train_field_classifier_model()
        crud.save_audit_event(user, "ml.train", "fields", "field_classifier", result)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ml/fields/predict")
def ml_fields_predict(data: MLFieldRequest, user: dict = Depends(require_permission("analysis:view"))):
    """Predict a field category without changing any RGPD decision."""
    try:
        return predict_field_category(
            data.field_name,
            module=data.module,
            source_system=data.source_system,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ml/fields/feedback")
def ml_fields_feedback(data: MLFieldFeedbackRequest, user: dict = Depends(require_permission("ai:validate"))):
    """Store a DPO correction for Agent A field classification and retrain memory."""
    field_name = (data.field_name or "").strip()
    corrected_label = canonical_field_label(data.corrected_label)
    predicted_label = canonical_field_label(data.predicted_label) or (data.predicted_label or None)
    labels = field_dataset_summary().get("labels", {})

    if not field_name:
        raise HTTPException(status_code=400, detail="field_name is required.")
    if not corrected_label:
        raise HTTPException(status_code=400, detail={
            "message": "Invalid corrected_label.",
            "allowed_labels": labels,
        })

    source_system = data.source_system or "manual"
    source_module = data.module or "unknown"
    target_id = f"{source_system}:{source_module}:{field_name}"
    changed = bool(predicted_label and predicted_label != corrected_label)
    justification = (
        data.justification
        or f"Correction DPO de la classification du champ '{field_name}' vers '{labels.get(corrected_label, corrected_label)}'."
    )

    try:
        validation_id = crud.save_dpo_validation(
            target_type="field_classification",
            target_id=target_id,
            target_label=field_name,
            decision="valide",
            validator=data.validator or _actor_name(user),
            role=ROLE_LABELS.get(user.get("role"), user.get("role") or "DPO"),
            justification=justification,
            source_system=source_system,
            source_module=source_module,
            evidence={
                "field": field_name,
                "field_name": field_name,
                "predicted_label": predicted_label,
                "corrected_label": corrected_label,
                "label": corrected_label,
                "label_display": labels.get(corrected_label, corrected_label),
                "source_system": source_system,
                "module": source_module,
                "model_prediction_corrected": changed,
            },
            metadata={
                "origin": "field_classification_feedback",
                "ml_training_feedback": True,
                "reusable": True,
                "final_value": corrected_label,
                "field_label": corrected_label,
                "label": corrected_label,
                "field": field_name,
                "module": source_module,
                "source_system": source_system,
            },
        )
        training_result = train_field_classifier_model() if data.retrain else {"trained": False, "reason": "Retrain skipped by request."}
        prediction_after = predict_field_category(
            field_name,
            module=source_module,
            source_system=source_system,
        )
        crud.save_audit_event(
            user,
            "ml.field_feedback",
            "field_classification",
            target_id,
            {
                "validation_id": validation_id,
                "field_name": field_name,
                "predicted_label": predicted_label,
                "corrected_label": corrected_label,
            },
        )
        return {
            "validation_id": validation_id,
            "proof_reference": validation_id,
            "memory_status": "stored",
            "field_name": field_name,
            "corrected_label": corrected_label,
            "corrected_label_display": labels.get(corrected_label, corrected_label),
            "prediction_after": prediction_after,
            "training": training_result,
            "dataset": field_dataset_summary(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ml/fields/feedback")
def ml_fields_feedback_list(limit: int = 100, user: dict = Depends(require_permission("proofs:view"))):
    """Return reusable DPO corrections used by Agent A field classification."""
    return {
        "memory": crud.get_dpo_feedback_memory(
            target_type="field_classification",
            reusable_only=True,
            limit=limit,
        ),
        "dataset": field_dataset_summary(),
    }


# ==============================================================================
# CONSENT MANAGEMENT (Q3)
# ==============================================================================

class ConsentCreate(BaseModel):
    nom_personne: str
    email_personne: Optional[str] = None
    id_traitement: str
    nom_traitement: str
    finalite: str
    date_expiration: Optional[str] = None
    preuve: Optional[str] = None


class ConsentWithdraw(BaseModel):
    consent_id: str
    retire_par: Optional[str] = None


@app.post("/consents")
def create_consent(data: ConsentCreate, user: dict = Depends(require_permission("consents:manage"))):
    """Register a new explicit consent (Q3)."""
    try:
        consent_id = crud.save_consent(
            nom_personne=data.nom_personne,
            email=data.email_personne,
            id_traitement=data.id_traitement,
            nom_traitement=data.nom_traitement,
            finalite=data.finalite,
            date_expiration=data.date_expiration,
            preuve=data.preuve
        )
        crud.save_audit_event(
            user,
            "consents.create",
            "consent",
            consent_id,
            {"id_traitement": data.id_traitement, "nom_personne": data.nom_personne},
        )
        return {"consent_id": consent_id, "statut": "actif", "message": "Consentement enregistré."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/consents/withdraw")
def withdraw_consent(data: ConsentWithdraw, user: dict = Depends(require_permission("consents:manage"))):
    """Withdraw a consent — immutable audit trail kept."""
    try:
        success = crud.withdraw_consent(data.consent_id, data.retire_par or _actor_name(user))
        if not success:
            raise HTTPException(status_code=404, detail="Consent not found or already withdrawn.")
        crud.save_audit_event(user, "consents.withdraw", "consent", data.consent_id, {})
        return {"consent_id": data.consent_id, "statut": "retire", "message": "Consentement retiré. Traitement associé doit être suspendu."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/consents")
def list_consents(
    id_traitement: str = None,
    statut: str = None,
    user: dict = Depends(require_permission("consents:view")),
):
    """List consents, optionally filtered by treatment or status."""
    return {"consents": crud.get_consents(id_traitement=id_traitement, statut=statut)}


@app.get("/consents/expiring")
def expiring_consents(days: int = 30, user: dict = Depends(require_permission("consents:view"))):
    """Return consents expiring within the next N days — for DPO alerts."""
    return {"expiring_consents": crud.get_expiring_consents(days_ahead=days)}


# ==============================================================================
# STATUS UPDATE ENDPOINTS
# ==============================================================================

@app.patch("/history/dsars/{id_demande}/statut")
def update_dsar_status(
    id_demande: str,
    statut: str,
    user: dict = Depends(require_permission("dsar:manage")),
):
    """Update a DSAR status (e.g. mark as Cloture)."""
    success = crud.update_dsar_statut(id_demande, statut)
    if not success:
        raise HTTPException(status_code=404, detail="DSAR not found.")
    crud.save_audit_event(user, "dsar.status_update", "dsar", id_demande, {"statut": statut})
    return {"id_demande": id_demande, "statut": statut}


@app.patch("/history/violations/{id_incident}/statut")
def update_violation_status(
    id_incident: str,
    statut: str,
    notification_envoyee: bool = False,
    user: dict = Depends(require_permission("incident:manage")),
):
    """Update a violation status (e.g. Cloture) and notification flag."""
    success = crud.update_violation_statut(id_incident, statut, notification_envoyee)
    if not success:
        raise HTTPException(status_code=404, detail="Violation not found.")
    crud.save_audit_event(
        user,
        "incident.status_update",
        "incident",
        id_incident,
        {"statut": statut, "notification_envoyee": notification_envoyee},
    )
    return {"id_incident": id_incident, "statut": statut, "notification_envoyee": notification_envoyee}
