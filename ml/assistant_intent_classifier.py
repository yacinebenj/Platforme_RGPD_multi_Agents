"""NLP intent classifier for the platform-aware DPO assistant.

The model mirrors Agent A/C style: TF-IDF vectors feed a Logistic Regression
classifier. It is used to retrieve the right platform context before the LLM
writes an answer. It does not make RGPD decisions.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from ml.assistant_intent_cases import ASSISTANT_INTENT_LABELS, SEED_ASSISTANT_INTENT_CASES

MODEL_PATH = Path("data") / "ml" / "dpo_assistant_intent.joblib"
CASES_PATH = Path(__file__).with_name("assistant_intent_cases.py")

CLARIFICATION_HINTS = (
    "that was in the platform",
    "this was in the platform",
    "in the platform",
    "i mean",
    "this one",
    "that one",
    "this module",
    "that module",
    "sur la plateforme",
    "dans la plateforme",
    "je parle de",
    "je veux dire",
)

KEYWORD_RULES = {
    "cnil_notification": ["cnil", "notification", "72h", "72 h", "72 hours", "notifier", "notify", "autorite", "authority"],
    "incident_summary": ["incident", "incidents", "violation", "breach", "breaches", "data breach", "fuite", "perte", "vol", "divulgation"],
    "risk_summary": ["risqu", "risque", "risques", "risk", "risks", "critique", "critical", "eleve", "high risk", "aipd", "droits et libertes"],
    "aipd_status": ["aipd", "dpia", "pia", "impact", "impact assessment", "dossier d impact", "article 35"],
    "dsar_status": ["dsar", "droit", "rights", "acces", "access", "effacement", "erase", "erasure", "rectification", "correct", "portabilite", "portability", "opposition", "objection", "demande", "request"],
    "legal_basis_status": ["base legale", "bases legales", "legal basis", "lawful basis", "article 6", "liceite", "contrat", "contract", "interet legitime", "legitimate interest"],
    "consent_status": ["consentement", "consentements", "consent", "retrait", "retire", "withdraw", "withdrawn", "expire", "expired"],
    "proof_validation": ["preuve", "preuves", "proof", "proofs", "validation", "valider", "validate", "dpo", "opposable", "historique", "evidence", "document", "justificatif", "piece justificative", "uploader", "upload", "deposer", "fournir", "corriger"],
    "corrective_actions": ["action", "actions", "corrective", "corrective action", "priorite", "priority", "responsable", "owner", "retard", "overdue"],
    "governance_summary": ["gouvernance", "governance", "cockpit", "maturite", "maturity", "direction", "reunion", "meeting", "synthese", "summary", "resume", "follow up", "supervisor"],
    "report_summary": ["rapport", "report", "snapshot", "dpo", "export", "pdf"],
    "compliance_summary": ["conformite", "compliance", "ecart", "gap", "gaps", "violation", "traitement", "treatment", "treatments", "registre", "rgpd", "processing", "personal data", "donnees personnelles", "software", "softwares"],
    "platform_navigation": ["ou", "comment", "trouver", "acceder", "page", "onglet", "menu", "where", "find", "open", "tab", "how do i"],
}

INTENT_PRIORITY = {
    "cnil_notification": 1,
    "incident_summary": 2,
    "risk_summary": 3,
    "aipd_status": 4,
    "dsar_status": 5,
    "legal_basis_status": 6,
    "consent_status": 7,
    "proof_validation": 8,
    "corrective_actions": 9,
    "compliance_summary": 10,
    "report_summary": 11,
    "platform_navigation": 12,
    "governance_summary": 13,
}

PLATFORM_SCOPE_TERMS = {
    "rgpd", "dpo", "cnil", "qalitas", "gmao", "traitement", "traitements",
    "donnee", "donnees", "conformite", "registre", "risque", "risques",
    "incident", "incidents", "violation", "violations", "aipd", "dpia",
    "dsar", "droit", "droits", "effacement", "rectification", "portabilite",
    "opposition", "consentement", "consentements", "preuve", "preuves",
    "validation", "validations", "rapport", "gouvernance", "cockpit",
    "maturite", "action", "actions", "base legale", "bases legales",
    "audit", "auditeur", "admin", "contributeur", "plateforme", "module",
    "modules", "superviseur", "reunion", "nahla", "direction",
    "platform", "analysis", "analyses", "risk", "risks", "incident", "incidents",
    "compliance", "register", "report", "reports", "governance", "maturity",
    "legal basis", "consent", "consents", "proof", "proofs", "validation",
    "personal data", "privacy", "treatment", "treatments", "software", "softwares",
    "processing", "rights",
}


def _normalise_text(value: Any) -> str:
    text = str(value or "").lower()
    replacements = {
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "à": "a", "â": "a", "ä": "a",
        "î": "i", "ï": "i",
        "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u",
        "ç": "c",
        "’": "'",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"[^\w\s'/-]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _contextualise_text(text: str, history: list[str] | None = None) -> str:
    normalised = _normalise_text(text)
    if not history:
        return normalised
    needs_context = len(normalised.split()) <= 7 or any(hint in normalised for hint in CLARIFICATION_HINTS)
    if not needs_context:
        return normalised
    recent = [_normalise_text(item) for item in (history or []) if str(item or "").strip()][-3:]
    if not recent:
        return normalised
    return " | ".join(recent + [normalised])


def _is_platform_related(text: str) -> bool:
    normalised = _normalise_text(text)
    return any(term in normalised for term in PLATFORM_SCOPE_TERMS)


def _model_is_stale() -> bool:
    if not MODEL_PATH.exists():
        return True
    try:
        model_mtime = MODEL_PATH.stat().st_mtime
        return CASES_PATH.stat().st_mtime > model_mtime or Path(__file__).stat().st_mtime > model_mtime
    except Exception:
        return False


def dataset_summary() -> dict[str, Any]:
    counts = Counter(case["label"] for case in SEED_ASSISTANT_INTENT_CASES)
    return {
        "dataset_size": len(SEED_ASSISTANT_INTENT_CASES),
        "class_counts": dict(sorted(counts.items())),
        "labels": ASSISTANT_INTENT_LABELS,
        "model_exists": MODEL_PATH.exists(),
        "model_path": str(MODEL_PATH),
    }


def _fallback_predict(text: str, source: str = "fallback_rules", history: list[str] | None = None) -> dict[str, Any]:
    normalised = _contextualise_text(text, history)
    if not normalised:
        return _format_prediction("out_of_scope", 0.0, source, "Question vide.", [])
    if not _is_platform_related(normalised):
        return _format_prediction(
            "out_of_scope",
            0.92,
            source,
            "Aucun indice indiquant une question liee a la plateforme RGPD.",
            [],
        )

    scores = {
        label: sum(1 for keyword in keywords if keyword in normalised)
        for label, keywords in KEYWORD_RULES.items()
    }
    best_label, best_score = sorted(
        scores.items(),
        key=lambda item: (-item[1], INTENT_PRIORITY.get(item[0], 99)),
    )[0]
    if best_score <= 0:
        best_label = "governance_summary"
        confidence = 0.42
        reason = "Question liee a la plateforme, intention precise non detectee."
    else:
        total = sum(score for score in scores.values() if score > 0) or best_score
        confidence = min(0.92, max(0.45, best_score / total))
        reason = "Intention detectee par mots-cles comme secours du modele ML."
    alternatives = [
        (label, score)
        for label, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:3]
        if score > 0 and label != best_label
    ]
    return _format_prediction(best_label, confidence, source, reason, alternatives)


def _format_prediction(
    label: str,
    confidence: float,
    source: str,
    reason: str,
    alternatives: list[tuple[str, float | int]] | None = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "label_display": ASSISTANT_INTENT_LABELS.get(label, label),
        "confidence": round(float(confidence or 0), 3),
        "source": source,
        "reason": reason,
        "alternatives": [
            {
                "label": str(alt_label),
                "label_display": ASSISTANT_INTENT_LABELS.get(str(alt_label), str(alt_label)),
                "score": round(float(score), 3),
            }
            for alt_label, score in (alternatives or [])
            if alt_label != label and score
        ][:3],
        "advisory_only": True,
    }


def train_assistant_intent_model() -> dict[str, Any]:
    try:
        import joblib
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline
    except Exception as exc:
        return {"trained": False, "reason": "dependencies_missing", "detail": str(exc), **dataset_summary()}

    texts = [case["text"] for case in SEED_ASSISTANT_INTENT_CASES]
    labels = [case["label"] for case in SEED_ASSISTANT_INTENT_CASES]
    counts = Counter(labels)
    if len(texts) < 20 or len(counts) < 2:
        return {"trained": False, "reason": "not_enough_data", **dataset_summary()}

    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(
            lowercase=True,
            strip_accents="unicode",
            ngram_range=(1, 2),
            min_df=1,
        )),
        ("clf", LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            random_state=42,
        )),
    ])

    accuracy = None
    can_split = min(counts.values()) >= 8 and len(texts) >= 120
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
    joblib.dump({
        "model": pipeline,
        "labels": ASSISTANT_INTENT_LABELS,
        "trained_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "dataset_size": len(texts),
        "class_counts": dict(sorted(counts.items())),
        "accuracy": accuracy,
    }, MODEL_PATH)
    return {"trained": True, "accuracy": accuracy, **dataset_summary()}


def predict_assistant_intent(text: str, auto_train: bool = True, history: list[str] | None = None) -> dict[str, Any]:
    if not str(text or "").strip():
        return _format_prediction("out_of_scope", 0.0, "empty_text", "Aucune question fournie.", [])

    if auto_train and _model_is_stale():
        train_assistant_intent_model()

    fallback = _fallback_predict(text, history=history)
    if fallback["label"] == "out_of_scope":
        return fallback

    if MODEL_PATH.exists():
        try:
            import joblib

            payload = joblib.load(MODEL_PATH)
            model = payload["model"]
            working_text = _contextualise_text(text, history)
            label = str(model.predict([working_text])[0])
            confidence = 0.65
            alternatives = []
            if hasattr(model, "predict_proba"):
                probabilities = model.predict_proba([working_text])[0]
                classes = list(model.classes_)
                ranked = sorted(zip(classes, probabilities), key=lambda item: item[1], reverse=True)
                label = str(ranked[0][0])
                confidence = float(ranked[0][1])
                alternatives = [(str(cls), float(prob)) for cls, prob in ranked[1:4]]

            if confidence < 0.35 and fallback["label"] != "out_of_scope":
                fallback["source"] = "fallback_rules_low_ml_confidence"
                fallback["ml_low_confidence"] = _format_prediction(
                    label,
                    confidence,
                    "ml_tfidf_logreg",
                    "Prediction ML conservee comme signal secondaire.",
                    alternatives,
                )
                return fallback

            # Deterministic guard: vague platform questions are allowed, truly external
            # questions are already refused before the ML result can over-generalise.
            return _format_prediction(
                label,
                confidence,
                "ml_tfidf_logreg",
                "Prediction issue du modele TF-IDF + Regression Logistique.",
                alternatives,
            )
        except Exception as exc:
            fallback["source"] = "fallback_rules_model_unavailable"
            fallback["model_error"] = str(exc)
            return fallback
    return fallback
