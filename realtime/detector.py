from __future__ import annotations

from agents.agent_a import detect_qalitas_fields
from database import crud
from gmao.connector import (
    GmaoConnector,
    MODULE_ENDPOINTS as GMAO_MODULE_ENDPOINTS,
    MODULE_LABELS as GMAO_MODULE_LABELS,
)
from qalitas.connector import (
    QalitasConnector,
    MODULE_ENDPOINTS as QALITAS_MODULE_ENDPOINTS,
    MODULE_LABELS as QALITAS_MODULE_LABELS,
)
from realtime.snapshot import build_snapshot, compare_snapshots, decode_snapshot


SOURCE_CONFIG = {
    "qalitas": {
        "label": "QALITAS WEB",
        "connector": QalitasConnector,
        "modules": QALITAS_MODULE_ENDPOINTS,
        "labels": QALITAS_MODULE_LABELS,
    },
    "gmao": {
        "label": "GMAO PRO",
        "connector": GmaoConnector,
        "modules": GMAO_MODULE_ENDPOINTS,
        "labels": GMAO_MODULE_LABELS,
    },
}


def available_sources() -> dict:
    return {
        source: {
            "label": config["label"],
            "modules": [{"id": key, "label": config["labels"].get(key, key)} for key in config["modules"].keys()],
        }
        for source, config in SOURCE_CONFIG.items()
    }


def _selected_modules(source_system: str, modules: list[str] | None) -> list[str]:
    config = SOURCE_CONFIG[source_system]
    valid = list(config["modules"].keys())
    if not modules:
        return valid
    return [module for module in modules if module in valid]


def fetch_source_records(source_system: str, module: str, limit: int = 100) -> list[dict]:
    config = SOURCE_CONFIG[source_system]
    connector = config["connector"]()
    try:
        connector.login()
        records = connector.fetch(module)
        if limit and limit > 0:
            records = records[:limit]
        return records
    finally:
        try:
            connector.logout()
        except Exception:
            pass


def analyse_source_module(source_system: str, module: str, limit: int = 100) -> dict:
    if source_system not in SOURCE_CONFIG:
        raise ValueError(f"Unknown real-time source: {source_system}")

    config = SOURCE_CONFIG[source_system]
    module_label = config["labels"].get(module, module)
    records = fetch_source_records(source_system, module, limit=limit)
    detection = detect_qalitas_fields(records, module, source_system=source_system)
    current = build_snapshot(source_system, module, module_label, records, detection)
    previous = decode_snapshot(crud.get_latest_realtime_snapshot(source_system, module))
    changes = compare_snapshots(previous, current)

    snapshot_id = crud.save_realtime_snapshot(
        source_system=source_system,
        module=module,
        module_label=module_label,
        records_count=current["records_count"],
        records_hash=current["records_hash"],
        fields_hash=current["fields_hash"],
        personal_fields_count=len(current.get("personal_fields") or []),
        sensitive_fields_count=len(current.get("sensitive_fields") or []),
        physical_persons_count=current.get("physical_persons_count", 0),
        snapshot=current,
    )

    event_ids = []
    for change in changes:
        metadata = {
            "snapshot_id": snapshot_id,
            "records_with_personal_data": current.get("records_with_personal_data", 0),
            "physical_persons_count": current.get("physical_persons_count", 0),
            "personal_fields": current.get("personal_fields", []),
            "sensitive_fields": current.get("sensitive_fields", []),
            **(change.get("metadata") or {}),
        }
        event_ids.append(crud.save_realtime_event(
            source_system=source_system,
            module=module,
            module_label=module_label,
            severity=change.get("severity", "info"),
            event_type=change.get("type", "change"),
            title=change.get("title", "Changement detecte"),
            message=change.get("message", ""),
            old_count=change.get("old_count", 0),
            new_count=change.get("new_count", current["records_count"]),
            delta_count=change.get("delta_count", 0),
            metadata=metadata,
        ))

    return {
        "source_system": source_system,
        "module": module,
        "module_label": module_label,
        "records_count": current["records_count"],
        "personal_fields_count": len(current.get("personal_fields") or []),
        "sensitive_fields_count": len(current.get("sensitive_fields") or []),
        "records_with_personal_data": current.get("records_with_personal_data", 0),
        "physical_persons_count": current.get("physical_persons_count", 0),
        "changes_count": len(changes),
        "event_ids": event_ids,
        "snapshot_id": snapshot_id,
        "changes": changes,
    }


def run_realtime_cycle(
    sources: list[str] | None = None,
    modules: list[str] | None = None,
    limit: int = 100
) -> dict:
    selected_sources = [source for source in (sources or list(SOURCE_CONFIG.keys())) if source in SOURCE_CONFIG]
    results = []
    errors = []

    for source_system in selected_sources:
        for module in _selected_modules(source_system, modules):
            try:
                results.append(analyse_source_module(source_system, module, limit=limit))
            except Exception as exc:
                config = SOURCE_CONFIG[source_system]
                module_label = config["labels"].get(module, module)
                errors.append({
                    "source_system": source_system,
                    "module": module,
                    "module_label": module_label,
                    "error": str(exc),
                })
                crud.save_realtime_event(
                    source_system=source_system,
                    module=module,
                    module_label=module_label,
                    severity="critical",
                    event_type="sync_error",
                    title="Erreur de synchronisation",
                    message=str(exc),
                    metadata={"module": module, "source_system": source_system},
                )

    return {
        "sources": selected_sources,
        "modules": modules or "all",
        "limit": limit,
        "results": results,
        "errors": errors,
        "events_created": sum(len(item.get("event_ids", [])) for item in results) + len(errors),
        "modules_checked": len(results) + len(errors),
    }
