"""Advisory ML classifier for field semantics.

Agent A remains rule-driven for RGPD decisions. This module only adds an
explainable ML/NLP hint such as identity, contact, address, or organization.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from ml.field_test_cases import FIELD_LABELS, SEED_FIELD_CASES

MODEL_PATH = Path("data") / "ml" / "field_classifier.joblib"

KEYWORD_RULES = {
    "sensitive": [
        "health", "medical", "disability", "accident", "fingerprint", "face",
        "biometric", "religion", "political", "union", "sante", "blood",
    ],
    "identity_document": [
        "cin", "passport", "nationalid", "national id", "registrationnumber",
        "identity", "idcard", "issued", "issuedto",
    ],
    "contact": [
        "email", "mail", "phone", "telephone", "mobile", "fax", "tel",
        "contact",
    ],
    "address": [
        "address", "adresse", "city", "ville", "zip", "postal", "latitude",
        "longitude", "gps", "geo", "location", "localisation",
    ],
    "identity": [
        "firstname", "first name", "lastname", "last name", "fullname",
        "full name", "prenom", "nom", "name", "civility", "civilite",
        "employee name", "technician name",
    ],
    "professional": [
        "department", "job", "function", "employee", "matricule",
        "certificate", "habilitation", "sharedwith", "shared with",
        "manager", "technician", "service",
    ],
    "organization": [
        "designation", "company", "societe", "supplier", "customer",
        "category", "type", "sector", "nature", "fiscal", "matricule fiscale",
        "website", "web site", "siret", "tax", "exp loc",
    ],
    "technical": [
        "id", "guid", "uuid", "created", "updated", "rowversion",
        "crud", "enabled", "active", "deleted", "currentuser", "version",
    ],
    "non_personal": [
        "code", "number", "internal reference", "reference", "status",
        "sort", "order",
    ],
}


def _normalise_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
    text = text.lower().replace("_", " ").replace("-", " ")
    text = text.replace("â€™", "'")
    text = re.sub(r"[^\w\s']", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value or "")


def _canonical_label(value: Any) -> str | None:
    text = _normalise_text(value).replace(" ", "_")
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    aliases = {
        "identite": "identity",
        "identity": "identity",
        "personne": "identity",
        "coordonnees": "contact",
        "contact": "contact",
        "adresse": "address",
        "address": "address",
        "localisation": "address",
        "piece_d_identite": "identity_document",
        "identity_document": "identity_document",
        "document_identite": "identity_document",
        "professionnel": "professional",
        "professional": "professional",
        "sensible": "sensitive",
        "sensitive": "sensitive",
        "organisation": "organization",
        "organization": "organization",
        "societe": "organization",
        "technical": "technical",
        "technique": "technical",
        "non_personal": "non_personal",
        "non_personnel": "non_personal",
        "ambiguous": "ambiguous",
        "ambigu": "ambiguous",
    }
    if text in aliases:
        return aliases[text]
    return None


def canonical_field_label(value: Any) -> str | None:
    """Public wrapper used by APIs to validate DPO-provided field labels."""
    return _canonical_label(value)


def _field_identity(value: Any) -> str:
    """Stable key for exact reusable DPO corrections."""
    return _normalise_text(value).replace(" ", "")


def _case_to_text(case: dict[str, Any]) -> str:
    field = case.get("field") or case.get("field_name") or ""
    module = case.get("module") or ""
    source = case.get("source_system") or ""
    context = case.get("context") or case.get("justification") or ""
    return " ".join(part for part in [field, module, source, context] if part)


def _walk_values(data: Any, wanted_keys: set[str]) -> list[str]:
    values: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            key_norm = _normalise_text(key).replace(" ", "_")
            if key_norm in wanted_keys and value not in (None, ""):
                values.append(str(value))
            values.extend(_walk_values(value, wanted_keys))
    elif isinstance(data, list):
        for item in data:
            values.extend(_walk_values(item, wanted_keys))
    return values


def _memory_to_case(memory: dict[str, Any]) -> dict[str, str] | None:
    label_keys = {"label", "field_label", "category", "categorie", "final_value", "corrected_label"}
    field_keys = {"field", "field_name", "donnee", "champ"}
    suggestion = memory.get("agent_suggestion") or {}
    evidence = memory.get("evidence_snapshot") or {}
    label_candidates = [
        memory.get("final_value"),
        *_walk_values(suggestion, label_keys),
        *_walk_values(evidence, label_keys),
    ]
    field_candidates = [
        memory.get("target_label"),
        *_walk_values(suggestion, field_keys),
        *_walk_values(evidence, field_keys),
    ]
    label = None
    for candidate in label_candidates:
        label = _canonical_label(candidate)
        if label:
            break
    field = next((str(item).strip() for item in field_candidates if str(item or "").strip()), "")
    if not label or not field:
        return None
    return {
        "field": field,
        "module": str(memory.get("source_module") or ""),
        "source_system": str(memory.get("source_system") or ""),
        "label": label,
        "context": str(memory.get("justification") or ""),
    }


def _memory_override(field_name: str, module: str | None = None, source_system: str | None = None) -> dict[str, Any] | None:
    """Return an exact DPO correction before using the advisory model."""
    try:
        from database import crud

        memory_batches = []
        if module or source_system:
            memory_batches.append(crud.get_dpo_feedback_memory(
                target_type="field_classification",
                source_system=source_system,
                source_module=module,
                reusable_only=True,
                limit=200,
            ))
        memory_batches.append(crud.get_dpo_feedback_memory(
            target_type="field_classification",
            reusable_only=True,
            limit=500,
        ))

        wanted = _field_identity(field_name)
        for memories in memory_batches:
            for memory in memories:
                case = _memory_to_case(memory)
                if not case or _field_identity(case.get("field")) != wanted:
                    continue
                confidence = max(float(memory.get("confidence") or 0), 0.96)
                return _format_prediction(
                    field_name,
                    module,
                    source_system,
                    case["label"],
                    confidence,
                    "dpo_memory",
                    "Classification reprise depuis une correction/validation DPO reutilisable.",
                    [],
                )
    except Exception:
        # Agent A must stay available even if the feedback store is temporarily unavailable.
        return None
    return None


def build_training_dataset() -> list[dict[str, str]]:
    """Return seed examples enriched with reusable DPO validation memory."""
    cases = list(SEED_FIELD_CASES)
    try:
        from database import crud

        memories = crud.get_dpo_feedback_memory(
            target_type="field_classification",
            reusable_only=True,
            limit=500,
        )
        for memory in memories:
            case = _memory_to_case(memory)
            if case:
                cases.append(case)
    except Exception:
        # The ML hint must never block Agent A.
        pass

    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for case in cases:
        label = _canonical_label(case.get("label"))
        text = _case_to_text(case).strip()
        if not label or not text:
            continue
        key = (_normalise_text(text), label)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({
            "field": str(case.get("field") or case.get("field_name") or "").strip(),
            "module": str(case.get("module") or "").strip(),
            "source_system": str(case.get("source_system") or "").strip(),
            "text": text,
            "label": label,
        })
    return deduped


def dataset_summary() -> dict[str, Any]:
    dataset = build_training_dataset()
    counts = Counter(case["label"] for case in dataset)
    return {
        "dataset_size": len(dataset),
        "class_counts": dict(sorted(counts.items())),
        "labels": FIELD_LABELS,
        "model_exists": MODEL_PATH.exists(),
        "model_path": str(MODEL_PATH),
    }


def _rule_score(normalised: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for label, keywords in KEYWORD_RULES.items():
        scores[label] = sum(1 for keyword in keywords if keyword in normalised)
    return scores


def _fallback_predict(field_name: str, module: str | None = None, source_system: str | None = None, source: str = "fallback_rules") -> dict[str, Any]:
    text = " ".join(part for part in [field_name, module or "", source_system or ""] if part)
    normalised = _normalise_text(text)
    if not normalised:
        return _format_prediction(field_name, module, source_system, "ambiguous", 0.0, source, "Champ vide ou non lisible.", [])

    scores = _rule_score(normalised)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_label, best_score = ranked[0]
    if best_score <= 0:
        return _format_prediction(
            field_name,
            module,
            source_system,
            "ambiguous",
            0.25,
            source,
            "Aucun motif ML/NLP clair detecte dans le nom du champ.",
            ranked[1:4],
        )
    total = sum(score for _, score in ranked if score > 0) or best_score
    confidence = min(0.92, max(0.45, best_score / total))
    return _format_prediction(
        field_name,
        module,
        source_system,
        best_label,
        confidence,
        source,
        f"Motifs semantiques detectes pour la categorie {FIELD_LABELS.get(best_label, best_label)}.",
        ranked[1:4],
    )


def _format_prediction(
    field_name: str,
    module: str | None,
    source_system: str | None,
    label: str,
    confidence: float,
    source: str,
    reason: str,
    alternatives: list[tuple[str, float | int]] | None = None,
) -> dict[str, Any]:
    label = str(label)
    cleaned_alternatives = []
    for alt_label, alt_score in alternatives or []:
        alt_label = str(alt_label)
        if alt_label == label or not alt_score:
            continue
        cleaned_alternatives.append({
            "label": alt_label,
            "label_display": str(FIELD_LABELS.get(alt_label, alt_label)),
            "score": round(float(alt_score), 3),
        })
    return {
        "field": field_name,
        "module": module,
        "source_system": source_system,
        "label": label,
        "label_display": str(FIELD_LABELS.get(label, label)),
        "confidence": round(float(confidence or 0), 3),
        "source": source,
        "reason": reason,
        "alternatives": cleaned_alternatives[:3],
        "advisory_only": True,
    }


def train_field_classifier_model() -> dict[str, Any]:
    """Train or refresh the advisory field classifier if sklearn is available."""
    dataset = build_training_dataset()
    if len(dataset) < 12:
        return {
            "trained": False,
            "reason": "Dataset insuffisant",
            **dataset_summary(),
        }
    try:
        from joblib import dump
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
    except Exception as exc:
        return {
            "trained": False,
            "reason": f"sklearn/joblib indisponible: {exc}",
            **dataset_summary(),
        }

    texts = [case["text"] for case in dataset]
    labels = [case["label"] for case in dataset]
    model = Pipeline([
        ("tfidf", TfidfVectorizer(
            lowercase=True,
            strip_accents="unicode",
            analyzer="char_wb",
            ngram_range=(2, 5),
            min_df=1,
        )),
        ("clf", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42)),
    ])
    model.fit(texts, labels)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    dump(model, MODEL_PATH)
    return {
        "trained": True,
        "dataset_size": len(dataset),
        "class_counts": dict(sorted(Counter(labels).items())),
        "model_path": str(MODEL_PATH),
        "labels": FIELD_LABELS,
    }


def predict_field_category(field_name: str, module: str | None = None, source_system: str | None = None) -> dict[str, Any]:
    """Predict field category as an advisory ML/NLP hint."""
    field_name = str(field_name or "").strip()
    override = _memory_override(field_name, module=module, source_system=source_system)
    if override:
        return override

    fallback = _fallback_predict(field_name, module=module, source_system=source_system)
    if not MODEL_PATH.exists():
        return fallback

    try:
        from joblib import load

        model = load(MODEL_PATH)
        text = " ".join(part for part in [field_name, module or "", source_system or ""] if part)
        label = model.predict([text])[0]
        confidence = fallback.get("confidence", 0.0)
        alternatives = []
        if hasattr(model, "predict_proba"):
            probabilities = model.predict_proba([text])[0]
            classes = list(model.classes_)
            ranked = sorted(zip(classes, probabilities), key=lambda item: item[1], reverse=True)
            label, confidence = ranked[0]
            alternatives = [(item_label, float(score)) for item_label, score in ranked[1:4]]
        return _format_prediction(
            field_name,
            module,
            source_system,
            str(label),
            float(confidence),
            "ml_model",
            "Prediction ML sur le nom du champ et son contexte module/source.",
            alternatives,
        )
    except Exception as exc:
        fallback["source"] = "fallback_after_model_error"
        fallback["reason"] = f"Modele indisponible, fallback applique: {exc}"
        return fallback
