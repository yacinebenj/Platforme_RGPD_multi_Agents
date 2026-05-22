from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime

from realtime.detector import available_sources, run_realtime_cycle


_lock = threading.RLock()
_stop_event = threading.Event()
_thread: threading.Thread | None = None

_state = {
    "running": False,
    "interval_seconds": 300,
    "limit": 100,
    "sources": None,
    "modules": None,
    "last_started_at": None,
    "last_stopped_at": None,
    "last_run_at": None,
    "last_finished_at": None,
    "last_error": None,
    "cycles_count": 0,
    "events_count": 0,
    "modules_checked": 0,
    "latest_summary": None,
    "activity_log": [],
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_sources(sources: list[str] | None) -> list[str] | None:
    valid = set(available_sources().keys())
    selected = [source for source in (sources or []) if source in valid]
    return selected or None


def _normalize_modules(modules: list[str] | None) -> list[str] | None:
    selected = [module for module in (modules or []) if module]
    return selected or None


def get_status() -> dict:
    with _lock:
        status = deepcopy(_state)
    status["available_sources"] = available_sources()
    return status


def _activity_from_summary(summary: dict) -> list[dict]:
    checked_at = _now()
    activity = []
    for item in summary.get("results") or []:
        changes_count = int(item.get("changes_count") or 0)
        records_count = int(item.get("records_count") or 0)
        physical_persons = int(item.get("physical_persons_count") or 0)
        personal_fields = int(item.get("personal_fields_count") or 0)
        activity.append({
            "checked_at": checked_at,
            "source_system": item.get("source_system"),
            "module": item.get("module"),
            "module_label": item.get("module_label") or item.get("module"),
            "status": "changed" if changes_count else "ok",
            "title": "Changement detecte" if changes_count else "Module verifie",
            "message": (
                f"{changes_count} changement(s) detecte(s)."
                if changes_count
                else "Aucun changement detecte depuis le dernier snapshot."
            ),
            "records_count": records_count,
            "physical_persons_count": physical_persons,
            "personal_fields_count": personal_fields,
            "changes_count": changes_count,
        })
    for item in summary.get("errors") or []:
        activity.append({
            "checked_at": checked_at,
            "source_system": item.get("source_system"),
            "module": item.get("module"),
            "module_label": item.get("module_label") or item.get("module"),
            "status": "error",
            "title": "Erreur de synchronisation",
            "message": item.get("error") or "Erreur inconnue.",
            "records_count": 0,
            "physical_persons_count": 0,
            "personal_fields_count": 0,
            "changes_count": 0,
        })
    return activity


def run_once(
    sources: list[str] | None = None,
    modules: list[str] | None = None,
    limit: int = 100,
) -> dict:
    selected_sources = _normalize_sources(sources)
    selected_modules = _normalize_modules(modules)
    safe_limit = max(0, int(limit or 0))

    with _lock:
        _state["last_run_at"] = _now()
        _state["last_error"] = None

    try:
        summary = run_realtime_cycle(
            sources=selected_sources,
            modules=selected_modules,
            limit=safe_limit,
        )
        with _lock:
            _state["last_finished_at"] = _now()
            _state["cycles_count"] += 1
            _state["events_count"] += int(summary.get("events_created") or 0)
            _state["modules_checked"] += int(summary.get("modules_checked") or 0)
            _state["latest_summary"] = summary
            _state["activity_log"] = (_activity_from_summary(summary) + _state.get("activity_log", []))[:80]
        return summary
    except Exception as exc:
        with _lock:
            _state["last_finished_at"] = _now()
            _state["last_error"] = str(exc)
        raise


def _loop(interval_seconds: int, sources: list[str] | None, modules: list[str] | None, limit: int) -> None:
    while not _stop_event.is_set():
        try:
            run_once(sources=sources, modules=modules, limit=limit)
        except Exception:
            # The error is captured in state; the scheduler keeps running.
            pass
        _stop_event.wait(interval_seconds)


def start(
    interval_seconds: int = 300,
    sources: list[str] | None = None,
    modules: list[str] | None = None,
    limit: int = 100,
) -> dict:
    global _thread
    safe_interval = max(30, int(interval_seconds or 300))
    safe_limit = max(0, int(limit or 0))
    selected_sources = _normalize_sources(sources)
    selected_modules = _normalize_modules(modules)

    with _lock:
        if _thread and _thread.is_alive():
            _state["running"] = True
            return get_status()

        _stop_event.clear()
        _state.update({
            "running": True,
            "interval_seconds": safe_interval,
            "limit": safe_limit,
            "sources": selected_sources,
            "modules": selected_modules,
            "last_started_at": _now(),
            "last_stopped_at": None,
            "last_error": None,
        })
        _thread = threading.Thread(
            target=_loop,
            args=(safe_interval, selected_sources, selected_modules, safe_limit),
            name="rgpd-realtime-scheduler",
            daemon=True,
        )
        _thread.start()

    return get_status()


def stop() -> dict:
    global _thread
    with _lock:
        _stop_event.set()
        thread = _thread

    if thread and thread.is_alive():
        thread.join(timeout=2)

    with _lock:
        _state["running"] = bool(_thread and _thread.is_alive())
        if not _state["running"]:
            _thread = None
            _state["last_stopped_at"] = _now()

    return get_status()
