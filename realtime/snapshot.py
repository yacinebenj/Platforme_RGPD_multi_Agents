import hashlib
import json
from datetime import datetime


def _json_default(value):
    return str(value)


def stable_json(value) -> str:
    """Serialize payloads in a deterministic way for hashing."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)


def hash_payload(value) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def collect_fields(records: list[dict]) -> list[str]:
    fields = set()
    for record in records or []:
        if isinstance(record, dict):
            fields.update(str(key) for key in record.keys())
    return sorted(fields, key=lambda item: item.lower())


def build_snapshot(
    source_system: str,
    module: str,
    module_label: str,
    records: list[dict],
    detection: dict | None = None
) -> dict:
    detection = detection or {}
    all_fields = detection.get("all_fields") or collect_fields(records)
    personal_fields = detection.get("personal_fields") or []
    sensitive_fields = detection.get("sensitive_fields") or []
    records_with_personal = detection.get("records_with_personal_data", 0)
    physical_persons = detection.get("physical_person_records_count", 0)

    return {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_system": source_system,
        "module": module,
        "module_label": module_label,
        "records_count": len(records or []),
        "records_hash": hash_payload(records or []),
        "fields_hash": hash_payload(all_fields),
        "all_fields": all_fields,
        "personal_fields": personal_fields,
        "sensitive_fields": sensitive_fields,
        "records_with_personal_data": records_with_personal,
        "physical_persons_count": physical_persons,
        "person_categories": detection.get("person_categories_display") or detection.get("person_categories") or [],
        "sample_names": (detection.get("affected_clients") or detection.get("named_records") or [])[:20],
    }


def decode_snapshot(row: dict | None) -> dict | None:
    if not row:
        return None
    payload = row.get("snapshot_json")
    if not payload:
        return dict(row)
    try:
        parsed = json.loads(payload)
        parsed["_db_row"] = dict(row)
        return parsed
    except Exception:
        row = dict(row)
        row["_decode_error"] = True
        return row


def compare_snapshots(previous: dict | None, current: dict) -> list[dict]:
    """Return normalized changes between the stored snapshot and current one."""
    if not previous:
        return [{
            "type": "initial_snapshot",
            "severity": "info",
            "title": "Snapshot initial cree",
            "message": f"Premier snapshot {current['source_system']} / {current['module_label']}: {current['records_count']} enregistrements.",
            "old_count": 0,
            "new_count": current["records_count"],
            "delta_count": current["records_count"],
        }]

    changes = []
    old_count = int(previous.get("records_count") or 0)
    new_count = int(current.get("records_count") or 0)
    if previous.get("records_hash") != current.get("records_hash"):
        delta = new_count - old_count
        changes.append({
            "type": "records_changed",
            "severity": "warning" if delta else "info",
            "title": "Donnees source modifiees",
            "message": f"{current['module_label']} a change: {old_count} -> {new_count} enregistrements.",
            "old_count": old_count,
            "new_count": new_count,
            "delta_count": delta,
        })

    if previous.get("fields_hash") != current.get("fields_hash"):
        old_fields = set(previous.get("all_fields") or [])
        new_fields = set(current.get("all_fields") or [])
        added = sorted(new_fields - old_fields)
        removed = sorted(old_fields - new_fields)
        changes.append({
            "type": "fields_changed",
            "severity": "warning",
            "title": "Schema source modifie",
            "message": f"Champs ajoutes: {len(added)} | champs retires: {len(removed)}.",
            "old_count": len(old_fields),
            "new_count": len(new_fields),
            "delta_count": len(added) - len(removed),
            "metadata": {"added_fields": added, "removed_fields": removed},
        })

    old_personal = set(previous.get("personal_fields") or [])
    new_personal = set(current.get("personal_fields") or [])
    old_sensitive = set(previous.get("sensitive_fields") or [])
    new_sensitive = set(current.get("sensitive_fields") or [])
    added_personal = sorted((new_personal | new_sensitive) - (old_personal | old_sensitive))
    if added_personal:
        changes.append({
            "type": "personal_fields_changed",
            "severity": "critical" if new_sensitive - old_sensitive else "warning",
            "title": "Nouveaux champs RGPD detectes",
            "message": "Nouveaux champs personnels/sensibles: " + ", ".join(added_personal[:8]),
            "old_count": len(old_personal | old_sensitive),
            "new_count": len(new_personal | new_sensitive),
            "delta_count": len(added_personal),
            "metadata": {"added_personal_fields": added_personal},
        })

    return changes
