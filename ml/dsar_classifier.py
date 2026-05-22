"""DSAR intent classifier.

The ML layer is advisory only. Agent C keeps the rule-based qualification as the
authoritative decision, while this module adds a transparent NLP prediction.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from ml.dsar_test_cases import SEED_DSAR_CASES

MODEL_PATH = Path("data") / "ml" / "dsar_intent.joblib"

LABELS = {
    "acces": "Droit d'acces",
    "rectification": "Droit de rectification",
    "effacement": "Droit a l'effacement",
    "limitation": "Droit a la limitation",
    "portabilite": "Droit a la portabilite",
    "opposition": "Droit d'opposition",
    "decision_automatisee": "Decision automatisee",
}

KEYWORD_RULES = {
    "decision_automatisee": [
        "decision automatisee",
        "automatique",
        "algorithme",
        "profilage",
        "intervention humaine",
    ],
    "portabilite": [
        "portabilite",
        "portable",
        "export",
        "csv",
        "json",
        "format structure",
        "lisible par machine",
        "transfert",
    ],
    "effacement": [
        "effacement",
        "effacer",
        "supprimer",
        "suppression",
        "droit a l oubli",
        "oublie",
        "retirer mes donnees",
    ],
    "rectification": [
        "rectification",
        "rectifier",
        "corriger",
        "modifier",
        "mettre a jour",
        "faux",
        "incorrect",
        "adresse email est fausse",
    ],
    "limitation": [
        "limitation",
        "limiter",
        "suspendre",
        "bloquer",
        "geler",
        "temporairement",
        "provisoirement",
    ],
    "opposition": [
        "opposition",
        "oppose",
        "m oppose",
        "refuse",
        "ne veux plus",
        "arretez",
        "cesser",
    ],
    "acces": [
        "acces",
        "copie",
        "consulter",
        "recevoir",
        "transmettre",
        "quelles donnees",
        "toutes les donnees",
        "mes informations",
    ],
}


def _normalise_text(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("’", "'")
    text = re.sub(r"[^\w\s']", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value or "")


def _canonical_label(value: Any) -> str | None:
    text = _normalise_text(value)
    if not text:
        return None
    aliases = {
        "access": "acces",
        "droit acces": "acces",
        "droit d acces": "acces",
        "rectification": "rectification",
        "correction": "rectification",
        "effacement": "effacement",
        "suppression": "effacement",
        "oubli": "effacement",
        "limitation": "limitation",
        "portabilite": "portabilite",
        "export": "portabilite",
        "opposition": "opposition",
        "decision automatisee": "decision_automatisee",
        "profilage": "decision_automatisee",
    }
    if text in LABELS:
        return text
    for needle, label in aliases.items():
        if needle in text:
            return label
    return None


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
    label_keys = {
        "final_value",
        "type_droit",
        "droit_exerce",
        "qualification",
        "dsar_type",
        "intent",
    }
    text_parts = [
        memory.get("target_label"),
        memory.get("context_signature"),
        memory.get("justification"),
        _safe_json(memory.get("agent_suggestion")),
        _safe_json(memory.get("evidence_snapshot")),
    ]
    suggestion = memory.get("agent_suggestion") or {}
    label_candidates = [
        memory.get("final_value"),
        *_walk_values(suggestion, label_keys),
        *_walk_values(memory.get("evidence_snapshot") or {}, label_keys),
    ]
    label = None
    for candidate in label_candidates:
        label = _canonical_label(candidate)
        if label:
            break
    text = " ".join(part for part in text_parts if part)
    if not label or len(_normalise_text(text)) < 8:
        return None
    return {"text": text, "label": label}


def build_training_dataset() -> list[dict[str, str]]:
    """Return seed examples enriched with reusable DPO validation memory."""
    cases = list(SEED_DSAR_CASES)
    try:
        from database import crud

        memories = crud.get_dpo_feedback_memory(
            target_type="dsar",
            reusable_only=True,
            limit=500,
        )
        for memory in memories:
            case = _memory_to_case(memory)
            if case:
                cases.append(case)
    except Exception:
        # ML must never block the RGPD workflow.
        pass

    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for case in cases:
        text = str(case.get("text", "")).strip()
        label = _canonical_label(case.get("label"))
        if not text or not label:
            continue
        key = (_normalise_text(text), label)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"text": text, "label": label})
    return deduped


def dataset_summary() -> dict[str, Any]:
    dataset = build_training_dataset()
    counts = Counter(case["label"] for case in dataset)
    return {
        "dataset_size": len(dataset),
        "class_counts": dict(sorted(counts.items())),
        "labels": LABELS,
        "model_exists": MODEL_PATH.exists(),
        "model_path": str(MODEL_PATH),
    }


def _fallback_predict(text: str, source: str = "fallback_rules") -> dict[str, Any]:
    normalised = _normalise_text(text)
    scores: dict[str, int] = {}
    for label, keywords in KEYWORD_RULES.items():
        scores[label] = sum(1 for keyword in keywords if keyword in normalised)
    best_label, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score <= 0:
        best_label = "acces"
        confidence = 0.35
        reason = "Aucun mot-cle fort detecte; droit d'acces choisi par prudence."
    else:
        total = sum(scores.values()) or best_score
        confidence = min(0.90, 0.45 + (best_score / max(total, 1)) * 0.45)
        reason = "Prediction par mots-cles, utilisee comme secours sans modele entraine."
    alternatives = [
        {"label": label, "score": score}
        for label, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:3]
    ]
    return {
        "label": best_label,
        "label_display": LABELS.get(best_label, best_label),
        "confidence": round(confidence, 3),
        "source": source,
        "reason": reason,
        "alternatives": alternatives,
    }


def train_dsar_intent_model() -> dict[str, Any]:
    """Train a compact TF-IDF + Logistic Regression classifier if sklearn exists."""
    summary = dataset_summary()
    try:
        import joblib
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline
    except Exception as exc:
        return {
            "trained": False,
            "reason": "dependencies_missing",
            "detail": str(exc),
            **summary,
        }

    dataset = build_training_dataset()
    labels = [case["label"] for case in dataset]
    texts = [case["text"] for case in dataset]
    counts = Counter(labels)
    if len(dataset) < 8 or len(counts) < 2:
        return {
            "trained": False,
            "reason": "not_enough_data",
            **dataset_summary(),
        }

    pipeline = Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    lowercase=True,
                    strip_accents="unicode",
                    ngram_range=(1, 2),
                    min_df=1,
                ),
            ),
            (
                "clf",
                LogisticRegression(
                    max_iter=1000,
                    class_weight="balanced",
                    random_state=42,
                ),
            ),
        ]
    )

    accuracy = None
    can_split = len(dataset) >= 18 and min(counts.values()) >= 2
    if can_split:
        x_train, x_test, y_train, y_test = train_test_split(
            texts,
            labels,
            test_size=0.25,
            random_state=42,
            stratify=labels,
        )
        pipeline.fit(x_train, y_train)
        accuracy = round(float(accuracy_score(y_test, pipeline.predict(x_test))), 3)
        pipeline.fit(texts, labels)
    else:
        pipeline.fit(texts, labels)

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": pipeline,
            "labels": LABELS,
            "trained_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "dataset_size": len(dataset),
            "class_counts": dict(sorted(counts.items())),
            "accuracy": accuracy,
        },
        MODEL_PATH,
    )
    return {
        "trained": True,
        "accuracy": accuracy,
        **dataset_summary(),
    }


def predict_dsar_intent(text: str) -> dict[str, Any]:
    """Predict DSAR intent using the trained model, with rule fallback."""
    if not str(text or "").strip():
        return {
            "label": "acces",
            "label_display": LABELS["acces"],
            "confidence": 0.0,
            "source": "empty_text",
            "reason": "Aucun texte fourni.",
            "alternatives": [],
        }
    if MODEL_PATH.exists():
        try:
            import joblib

            payload = joblib.load(MODEL_PATH)
            model = payload["model"]
            label = str(model.predict([text])[0])
            confidence = 0.65
            alternatives = []
            if hasattr(model, "predict_proba"):
                probabilities = model.predict_proba([text])[0]
                classes = list(model.classes_)
                ranked = sorted(
                    zip(classes, probabilities),
                    key=lambda item: item[1],
                    reverse=True,
                )
                label = str(ranked[0][0])
                confidence = float(ranked[0][1])
                alternatives = [
                    {"label": str(cls), "score": round(float(prob), 3)}
                    for cls, prob in ranked[:3]
                ]
            return {
                "label": label,
                "label_display": LABELS.get(label, label),
                "confidence": round(confidence, 3),
                "source": "ml_tfidf_logreg",
                "reason": "Prediction issue du modele entraine sur exemples et validations DPO.",
                "alternatives": alternatives,
            }
        except Exception as exc:
            fallback = _fallback_predict(text, source="fallback_rules_model_unavailable")
            fallback["model_error"] = str(exc)
            return fallback
    return _fallback_predict(text)
