"""Platform-aware DPO assistant.

The assistant is deliberately read-only:
- NLP classifies the free-text question intent.
- SQLite context is selected from the latest platform analyses/history.
- RAG adds legal context when available.
- Groq writes the final answer in French.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from groq import Groq

from database import crud
from ml.assistant_intent_classifier import ASSISTANT_INTENT_LABELS, predict_assistant_intent

load_dotenv("key.env")

_groq_client = None
_rag_cache = None
FOLLOW_UP_HINTS = (
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

REFUSAL = (
    "Je suis l'assistant DPO de la plateforme RGPD. "
    "Je peux uniquement vous aider sur les traitements, analyses, risques, incidents, "
    "DSAR, AIPD, consentements, actions, preuves, validations, rapports et données "
    "présentes dans la plateforme."
)


def _get_groq():
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY manquante.")
        _groq_client = Groq(api_key=api_key)
    return _groq_client


def _safe_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _norm(value: Any) -> str:
    text = str(value or "").lower()
    replacements = {
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "à": "a", "â": "a", "ä": "a",
        "î": "i", "ï": "i",
        "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u",
        "ç": "c", "’": "'",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return re.sub(r"\s+", " ", text).strip()


def _contextualise_question(question: str, history: list[str] | None = None) -> str:
    normalised = _norm(question)
    if not history:
        return question
    needs_context = len(normalised.split()) <= 7 or any(hint in normalised for hint in FOLLOW_UP_HINTS)
    if not needs_context:
        return question
    recent = [str(item or "").strip() for item in (history or []) if str(item or "").strip()][-3:]
    if not recent:
        return question
    return " / ".join(recent + [question])


def extract_entities(question: str) -> dict[str, Any]:
    norm = _norm(question)
    entities: dict[str, Any] = {
        "source_system": None,
        "incident_id": None,
        "treatment_id": None,
        "module_hint": None,
    }
    if "qalitas" in norm:
        entities["source_system"] = "QALITAS"
    elif "gmao" in norm:
        entities["source_system"] = "GMAO"

    inc = re.search(r"\binc[-_\s]?\d+\b", question or "", re.IGNORECASE)
    if inc:
        entities["incident_id"] = inc.group(0).replace(" ", "-").upper()

    trt = re.search(r"\btrt[-_\s]?[a-z0-9-]+\b", question or "", re.IGNORECASE)
    if trt:
        entities["treatment_id"] = trt.group(0).replace(" ", "-").upper()

    module_terms = {
        "rh": "employees",
        "employe": "employees",
        "employes": "employees",
        "client": "customers",
        "clients": "customers",
        "fournisseur": "suppliers",
        "fournisseurs": "suppliers",
        "audit": "audits",
        "audits": "audits",
        "non conformite": "nonconf",
        "incident": "incidents",
        "intervention": "interventions",
        "technicien": "technicians",
        "equipement": "equipment",
    }
    for term, module in module_terms.items():
        if term in norm:
            entities["module_hint"] = module
            break
    return entities


def _filter_system(rows: list[dict], entities: dict[str, Any]) -> list[dict]:
    wanted = entities.get("source_system")
    if not wanted:
        return rows
    wanted_norm = wanted.lower()
    filtered = [
        row for row in rows
        if wanted_norm in str(row.get("systeme") or row.get("source_system") or "").lower()
    ]
    return filtered or rows


def _row_text(row: dict, keys: list[str]) -> str:
    parts = []
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            parts.append(f"{key}={value}")
    return ", ".join(parts) or "donnees limitees"


def _question_requests_proof_guidance(question: str) -> bool:
    q = _norm(question)
    proof_terms = [
        "document", "preuve", "justificatif", "piece justificative", "pièce justificative",
        "upload", "uploader", "televers", "telecharger", "fournir", "deposer", "déposer",
        "corriger", "correction", "what should i upload", "what document", "quel document",
        "quelle preuve", "quoi fournir", "que faire", "what should i do",
    ]
    scope_terms = ["traitement", "dernier", "latest", "last treatment", "last one", "ce traitement"]
    return any(term in q for term in proof_terms) and any(term in q for term in scope_terms)


def _question_targets_latest_treatment(question: str) -> bool:
    q = _norm(question)
    return any(term in q for term in [
        "dernier traitement", "derniere traitement", "dereniere traitement",
        "latest treatment", "last treatment", "dernier dossier", "derniere dossier",
        "ce traitement", "ce dossier", "last one", "latest one",
    ]) or ("dernier" in q and "traitement" in q) or ("derniere" in q and "traitement" in q) or ("dereniere" in q and "traitement" in q)


def _question_asks_for_summary(question: str, family: str) -> bool:
    q = _norm(question)
    keywords = {
        "risk": ["plus risques", "plus risqués", "risques", "top risk", "most risky"],
        "cnil": ["cnil", "72h", "notification", "notifier"],
        "proofs": ["preuves", "proofs", "valider", "validation", "justificatif"],
        "compliance": ["conformite", "compliance", "violations", "ecarts", "qalityas", "gmao"],
        "governance": [
            "gouvernance", "cockpit", "maturite", "kpi", "indicateur", "indicateurs",
            "priorites", "pilotage", "tableau de bord", "resume reunion", "synthese direction",
        ],
    }
    return any(term in q for term in keywords.get(family, []))


def _dedupe_rows(rows: list[dict], keys: list[str]) -> list[dict]:
    unique: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        marker = tuple(_norm(row.get(key)) for key in keys)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(row)
    return unique


def _valid_treatment_rows(rows: list[dict]) -> list[dict]:
    return [
        row for row in rows
        if str(row.get("nom_traitement") or row.get("id_traitement") or "").strip()
        and str(row.get("systeme") or "").strip()
    ]


def _maybe_override_intent(question: str, intent: dict[str, Any]) -> dict[str, Any]:
    q = _norm(question)
    if _question_requests_proof_guidance(question):
        intent = dict(intent or {})
        intent["label"] = "proof_validation"
        intent["label_display"] = ASSISTANT_INTENT_LABELS.get("proof_validation", "Preuves et validations")
        intent["confidence"] = max(float(intent.get("confidence") or 0), 0.72)
        intent["source"] = "intent_override_proof_guidance"
        intent["reason"] = "Question orientee preuve/document/correction pour un traitement."
    elif any(term in q for term in ["cnil", "notification", "72h", "72 h", "notifier"]):
        intent = dict(intent or {})
        intent["label"] = "cnil_notification"
        intent["label_display"] = ASSISTANT_INTENT_LABELS.get("cnil_notification", "Notification CNIL")
        intent["confidence"] = max(float(intent.get("confidence") or 0), 0.75)
        intent["source"] = "intent_override_cnil"
        intent["reason"] = "Question centree sur la notification CNIL."
    elif any(term in q for term in ["conformite", "compliance", "violations", "ecarts"]) and any(term in q for term in ["qalitas", "gmao", "traitement", "traitements"]):
        intent = dict(intent or {})
        intent["label"] = "compliance_summary"
        intent["label_display"] = ASSISTANT_INTENT_LABELS.get("compliance_summary", "Synthese conformite")
        intent["confidence"] = max(float(intent.get("confidence") or 0), 0.7)
        intent["source"] = "intent_override_compliance"
        intent["reason"] = "Question centree sur la conformite de traitements ou d'un systeme."
    elif _question_asks_for_summary(question, "governance"):
        intent = dict(intent or {})
        intent["label"] = "governance_summary"
        intent["label_display"] = ASSISTANT_INTENT_LABELS.get("governance_summary", "Synthese gouvernance")
        intent["confidence"] = max(float(intent.get("confidence") or 0), 0.78)
        intent["source"] = "intent_override_governance"
        intent["reason"] = "Question centree sur les KPI, la maturite ou le pilotage de gouvernance."
    return intent


def _latest_register_entry(entities: dict[str, Any]) -> dict[str, Any] | None:
    rows = crud.get_register_entries(limit=50)
    rows = _filter_system(rows, entities)
    treatment_id = entities.get("treatment_id")
    if treatment_id:
        treatment_norm = _norm(treatment_id)
        for row in rows:
            if treatment_norm in _norm(row.get("id_traitement")) or treatment_norm in _norm(row.get("nom_traitement")):
                return row
    return rows[0] if rows else None


def _latest_treatment_row(entities: dict[str, Any]) -> dict[str, Any] | None:
    rows = _valid_treatment_rows(_filter_system(crud.get_treatments(limit=50), entities))
    treatment_id = entities.get("treatment_id")
    if treatment_id:
        treatment_norm = _norm(treatment_id)
        for row in rows:
            if treatment_norm in _norm(row.get("id_traitement")) or treatment_norm in _norm(row.get("nom_traitement")):
                return row
    return rows[0] if rows else None


def _treatment_from_register(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    source_analysis_id = entry.get("source_analysis_id")
    if source_analysis_id:
        row = crud.get_treatment_by_row_id(int(source_analysis_id))
        if row:
            return row
    treatment_id = entry.get("id_traitement")
    if treatment_id:
        row = crud.get_treatment_by_id(str(treatment_id))
        if row:
            return row
    return None


def _extract_open_gaps(treatment_row: dict[str, Any] | None) -> dict[str, Any]:
    output = _safe_json((treatment_row or {}).get("output_json"), {}) or {}
    q1 = output.get("q1_cartographie", {}) or {}
    q2 = output.get("q2_conformite", {}) or {}
    q3 = output.get("q3_base_legale", {}) or {}
    return {
        "q1_alerts": q1.get("alertes_q1", []) or [],
        "violations": q2.get("violations", []) or [],
        "documentary_points": q2.get("points_documentaires", []) or [],
        "base_legale": q3,
        "score": q2.get("score_conformite_globale"),
        "risk": q2.get("niveau_risque"),
        "treatment_name": q1.get("nom_traitement") or (treatment_row or {}).get("nom_traitement"),
        "systeme": q1.get("systeme") or (treatment_row or {}).get("systeme"),
    }


def _recommended_proof_sections(gaps: dict[str, Any]) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    violations = gaps.get("violations") or []
    doc_points = gaps.get("documentary_points") or []
    q3 = gaps.get("base_legale") or {}
    ids = {str(item.get("id_regle") or "") for item in violations + doc_points}

    if not q3.get("base_legale_confirmee") or {"RGPD-01", "RGPD-05"} & ids:
        sections.append((
            "Base legale",
            "Ajoutez un contrat, une clause, une note interne ou une validation DPO qui prouve la base legale du traitement."
        ))
    if {"RGPD-13B", "RGPD-13C", "RGPD-07", "LOI-TN-03B"} & ids:
        sections.append((
            "Securite / garanties renforcees",
            "Ajoutez une politique de securite, une capture de chiffrement, un journal d'acces, un audit ou une procedure de restriction d'acces."
        ))
    if {"RGPD-03", "LOI-TN-02"} & ids:
        sections.append((
            "Minimisation",
            "Ajoutez une justification ecrite du besoin metier ou corrigez le traitement si certains champs ne sont pas necessaires."
        ))
    if {"RGPD-22", "RGPD-22B"} & ids:
        sections.append((
            "Revue / gouvernance",
            "Ajoutez une procedure de revue periodique, une preuve de mise a jour du registre ou un compte-rendu de revue DPO."
        ))
    if not sections:
        sections.append((
            "Preuve globale",
            "Ajoutez une preuve liee a l'ecart principal du traitement: base legale, retention, securite ou procedure interne."
        ))
    return sections[:4]


def _build_scope_guidance(question: str) -> str:
    q = _norm(question)
    if "consent" in q or "client" in q or "personne" in q:
        return (
            "Rattachement conseille :\n"
            "- personne si la preuve concerne un seul client / employe ;\n"
            "- categorie si la preuve couvre un groupe entier ;\n"
            "- traitement seulement si la preuve est transversale.\n"
            "Une preuve pour une seule personne ne corrige pas tout le traitement."
        )
    return (
        "Rattachement conseille :\n"
        "- traitement pour une politique, une procedure, un contrat cadre ou une mesure transversale ;\n"
        "- categorie pour employes / clients / fournisseurs ;\n"
        "- personne si la preuve ne couvre qu'un dossier individuel."
    )


def _deterministic_proof_answer(question: str, entities: dict[str, Any]) -> tuple[str, list[str]] | None:
    if not _question_requests_proof_guidance(question):
        return None
    if _question_targets_latest_treatment(question):
        treatment_row = _latest_treatment_row(entities)
        register_entry = None
    else:
        register_entry = _latest_register_entry(entities)
        treatment_row = _treatment_from_register(register_entry)
        if not treatment_row:
            treatment_row = _latest_treatment_row(entities)
    gaps = _extract_open_gaps(treatment_row)
    treatment_name = gaps.get("treatment_name") or (register_entry or {}).get("nom_traitement") or "le dernier traitement"
    systeme = gaps.get("systeme") or (register_entry or {}).get("systeme") or "la plateforme"
    sections = _recommended_proof_sections(gaps)
    intro = (
        f"Pour {treatment_name} ({systeme}), le bon document depend d'abord de l'ecart encore ouvert. "
        "Le plus utile est de deposer une preuve qui repond a l'ecart principal, pas un document generique."
    )
    bullets = "\n".join(f"- {title}: {desc}" for title, desc in sections)
    closing = _build_scope_guidance(question)
    score = gaps.get("score")
    risk = gaps.get("risk")
    status_line = ""
    if score is not None or risk:
        status_line = f"\n\nEtat actuel du traitement: score {score if score is not None else 'N/A'} / risque {risk or 'N/A'}."
    answer = (
        intro
        + status_line
        + "\n\nPreuves recommandees maintenant:\n"
        + bullets
        + "\n\n"
        + closing
        + "\n\nValidation finale: la preuve aide la decision, mais elle ne ferme pas automatiquement tous les ecarts."
    )
    return answer, ["treatments", "rgpd_register", "dpo_validations"]


def _deterministic_risk_answer(question: str, entities: dict[str, Any]) -> tuple[str, list[str]] | None:
    if not _question_asks_for_summary(question, "risk"):
        return None
    rows = _dedupe_rows(_valid_treatment_rows(_filter_system(crud.get_treatments(limit=20), entities)), ["nom_traitement", "systeme"])
    scored = []
    for row in rows:
        score = row.get("score_conformite")
        violations = row.get("nb_violations") or 0
        risk = str(row.get("niveau_risque") or "")
        rank = {"critique": 4, "eleve": 3, "moyen": 2, "faible": 1}.get(_norm(risk), 0)
        scored.append((rank, int(violations), float(score or 0), row))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    top = scored[:3]
    if not top:
        return None
    lines = []
    for _, violations, score, row in top:
        lines.append(
            f"- {row.get('nom_traitement') or row.get('id_traitement') or 'Traitement'} "
            f"({row.get('systeme') or 'N/A'}) : risque {row.get('niveau_risque') or 'N/A'}, "
            f"{violations} violation(s), score {score}."
        )
    answer = (
        "Les traitements les plus risques dans la plateforme sont actuellement :\n"
        + "\n".join(lines)
        + "\n\nPriorite: traiter d'abord ceux qui cumulent risque eleve/critique et plusieurs violations."
        + "\n\nValidation finale: cette priorisation reste une aide de pilotage a confirmer par le DPO."
    )
    return answer, ["treatments"]


def _deterministic_cnil_answer(question: str, entities: dict[str, Any]) -> tuple[str, list[str]] | None:
    if not _question_asks_for_summary(question, "cnil"):
        return None
    reviews = _dedupe_rows(_filter_system(crud.get_incident_reviews(limit=25), entities), ["id_incident", "nom_traitement", "systeme"])
    flagged = [r for r in reviews if _norm(r.get("notifier_cnil")) in {"oui", "true", "1"}]
    if flagged:
        lines = [
            f"- {r.get('id_incident') or r.get('incident_id') or 'Incident'} : "
            f"{r.get('nom_traitement') or 'Traitement'}, qualification {r.get('qualification') or 'N/A'}."
            for r in flagged[:5]
        ]
        answer = (
            "Les incidents actuellement marques a notifier a la CNIL dans la plateforme sont :\n"
            + "\n".join(lines)
            + "\n\nVerifiez ensuite le delai de 72h et la completion du dossier CNIL avant validation finale par le DPO."
        )
    else:
        answer = (
            "Je ne vois pas d'incident marque a notifier a la CNIL dans les donnees recentes de la plateforme. "
            "Si vous pensez a un incident precis, donnez son identifiant ou ouvrez la fiche Incidents & risques."
        )
    return answer, ["incident_reviews", "cnil_notifications"]


def _deterministic_pending_proofs_answer(question: str, entities: dict[str, Any]) -> tuple[str, list[str]] | None:
    if not _question_asks_for_summary(question, "proofs"):
        return None
    validations = crud.get_dpo_validations(limit=30)
    pending = _dedupe_rows(
        [v for v in validations if _norm(v.get("decision")) in {"correction_requise", "preuve_fournie"}],
        ["target_type", "target_label", "decision"],
    )
    actions = crud.get_actions(limit=20)
    proof_waiting = _dedupe_rows(
        [a for a in actions if "preuve" in _norm(a.get("proof_status")) and not a.get("has_accepted_proof")],
        ["title", "proof_status"],
    )
    lines = []
    for item in pending[:5]:
        lines.append(
            f"- {item.get('target_type') or 'element'} : {item.get('target_label') or item.get('target_id') or 'N/A'} "
            f"({item.get('decision') or 'en attente'})."
        )
    for item in proof_waiting[:5]:
        lines.append(
            f"- action corrective : {item.get('title') or 'N/A'} "
            f"({item.get('proof_status') or 'preuve a verifier'})."
        )
    if not lines:
        answer = "Je ne vois pas de preuve prioritaire en attente dans les donnees recentes de la plateforme."
    else:
        answer = (
            "Les preuves / validations qui restent a traiter en priorite sont :\n"
            + "\n".join(lines)
            + "\n\nConseil: ouvrez d'abord les elements avec preuve fournie mais non encore revue, puis les actions correctives sans preuve acceptee."
        )
    return answer, ["dpo_validations", "actions", "dpo_proofs"]


def _deterministic_compliance_answer(question: str, entities: dict[str, Any]) -> tuple[str, list[str]] | None:
    if not _question_asks_for_summary(question, "compliance"):
        return None
    rows = _dedupe_rows(_valid_treatment_rows(_filter_system(crud.get_treatments(limit=12), entities)), ["nom_traitement", "systeme"])
    if not rows:
        return None
    avg = round(sum(float(r.get("score_conformite") or 0) for r in rows) / max(len(rows), 1), 1)
    total_violations = sum(int(r.get("nb_violations") or 0) for r in rows)
    worst = sorted(rows, key=lambda r: (-(int(r.get("nb_violations") or 0)), float(r.get("score_conformite") or 0)))[:3]
    worst_lines = "\n".join(
        f"- {r.get('nom_traitement') or r.get('id_traitement') or 'Traitement'} : "
        f"{int(r.get('nb_violations') or 0)} violation(s), score {float(r.get('score_conformite') or 0)}."
        for r in worst
    )
    system_label = entities.get("source_system") or "la plateforme"
    answer = (
        f"Resume conformite pour {system_label} : score moyen {avg}, total {total_violations} violation(s) sur les analyses recentes.\n\n"
        f"Traitements les plus charges en ecarts:\n{worst_lines}\n\n"
        "Pour avancer, commencez par la base legale, la minimisation et les preuves de securite quand ces points sont encore ouverts."
    )
    return answer, ["treatments", "rgpd_register"]


def _deterministic_governance_answer(question: str, entities: dict[str, Any]) -> tuple[str, list[str]] | None:
    if not _question_asks_for_summary(question, "governance"):
        return None

    snapshots = _dedupe_rows(
        _filter_system(crud.get_governance_snapshots(limit=8), entities),
        ["id", "nom_traitement", "systeme", "created_at"],
    )
    actions = crud.get_actions(limit=20)
    dsars = crud.get_dsars(limit=20)
    incidents = _dedupe_rows(
        _filter_system(crud.get_incident_reviews(limit=20), entities),
        ["id_incident", "nom_traitement", "systeme"],
    )

    latest = snapshots[0] if snapshots else None
    score = latest.get("score_maturite_rgpd") if latest else None
    niveau = latest.get("niveau_maturite") if latest else None
    critical_alerts = latest.get("critical_alerts_count") if latest else None
    violations = latest.get("violations_count") if latest else None
    traitement = latest.get("nom_traitement") if latest else None
    systeme = latest.get("systeme") if latest else None

    actions_open = [a for a in actions if _norm(a.get("status")) not in {"cloturee", "closed", "terminee", "done"}]
    actions_critical = [a for a in actions_open if _norm(a.get("severity")) in {"critique", "critical", "elevee", "eleve", "high"}]
    dsars_open = [d for d in dsars if _norm(d.get("statut")) not in {"traitee", "cloturee", "closed", "completee", "complete"}]
    dsars_urgent = [d for d in dsars_open if isinstance(d.get("jours_restants"), (int, float)) and float(d.get("jours_restants")) <= 10]
    incidents_cnil = [r for r in incidents if _norm(r.get("notifier_cnil")) in {"oui", "true", "1"}]

    q = _norm(question)
    if "kpi" in q or "indicateur" in q or "indicateurs" in q:
        answer = (
            "Dans cette plateforme, les KPI sont les indicateurs utilises par le DPO pour suivre l'etat global de la conformite.\n\n"
            f"Les indicateurs visibles actuellement sont : score de maturite RGPD = {score if score is not None else 'N/A'}, "
            f"niveau de maturite = {niveau or 'N/A'}, alertes critiques = {critical_alerts if critical_alerts is not None else 'N/A'}, "
            f"violations consolidees = {violations if violations is not None else 'N/A'}, actions correctives ouvertes = {len(actions_open)}, "
            f"demandes DSAR ouvertes = {len(dsars_open)}, incidents a notifier CNIL = {len(incidents_cnil)}.\n\n"
            "Interpretation : ces indicateurs permettent de voir rapidement le niveau de maitrise, les urgences a traiter et les priorites de pilotage.\n\n"
            "Action recommandee : commencer par les alertes critiques, puis les actions severes encore ouvertes et les DSAR urgentes. "
            "La validation finale des priorites appartient au DPO."
        )
        return answer, ["governance_snapshots", "actions", "dsars", "incident_reviews"]

    if latest:
        intro = (
            f"La derniere synthese de gouvernance disponible concerne {traitement or 'le dernier traitement'}"
            f"{f' ({systeme})' if systeme else ''}."
        )
        details = (
            f" Score de maturite RGPD : {score if score is not None else 'N/A'} ; "
            f"niveau de maturite : {niveau or 'N/A'} ; "
            f"alertes critiques : {critical_alerts if critical_alerts is not None else 'N/A'} ; "
            f"violations consolidees : {violations if violations is not None else 'N/A'}."
        )
    else:
        intro = "Je ne vois pas encore de snapshot de gouvernance enregistre dans la plateforme."
        details = ""

    priorities = []
    if critical_alerts:
        priorities.append(f"{critical_alerts} alerte(s) critique(s) a traiter en premier")
    if actions_critical:
        priorities.append(f"{len(actions_critical)} action(s) corrective(s) severe(s) encore ouverte(s)")
    if dsars_urgent:
        priorities.append(f"{len(dsars_urgent)} demande(s) DSAR urgente(s)")
    if incidents_cnil:
        priorities.append(f"{len(incidents_cnil)} incident(s) avec notification CNIL a verifier")
    if not priorities:
        priorities.append("aucune urgence majeure visible dans les donnees recentes")

    answer = (
        intro
        + details
        + "\n\nPriorites de gouvernance :\n- "
        + "\n- ".join(priorities[:4])
        + "\n\nLecture rapide : cette synthese consolide la maturite, les alertes, les actions et les demandes ouvertes pour aider le pilotage."
        + "\n\nLa validation finale des decisions et des priorites appartient au DPO."
    )
    return answer, ["governance_snapshots", "actions", "dsars", "incident_reviews"]


def _deterministic_answer(question: str, intent_label: str, entities: dict[str, Any]) -> tuple[str, list[str]] | None:
    return (
        _deterministic_proof_answer(question, entities)
        or _deterministic_risk_answer(question, entities)
        or _deterministic_cnil_answer(question, entities)
        or _deterministic_pending_proofs_answer(question, entities)
        or _deterministic_compliance_answer(question, entities)
        or _deterministic_governance_answer(question, entities)
    )


def _summarise_treatments(rows: list[dict], limit: int = 8) -> list[str]:
    lines = []
    for row in rows[:limit]:
        output = _safe_json(row.get("output_json"), {}) or {}
        q1 = output.get("q1_cartographie", {})
        q2 = output.get("q2_conformite", {})
        q3 = output.get("q3_base_legale", {})
        lines.append(
            "- "
            + _row_text({
                "date": row.get("created_at"),
                "traitement": row.get("nom_traitement") or q1.get("nom_traitement") or row.get("id_traitement"),
                "systeme": row.get("systeme") or q1.get("systeme"),
                "module": q1.get("module") or q1.get("source_module"),
                "risque": row.get("niveau_risque") or q2.get("niveau_risque"),
                "violations": row.get("nb_violations") if row.get("nb_violations") is not None else q2.get("nombre_violations"),
                "score": row.get("score_conformite") if row.get("score_conformite") is not None else q2.get("score_normalise"),
                "base_legale": q3.get("base_legale") or q3.get("base_legale_recommandee"),
            }, ["date", "traitement", "systeme", "module", "risque", "violations", "score", "base_legale"])
        )
    return lines


def _summarise_register(rows: list[dict], limit: int = 8) -> list[str]:
    return [
        "- " + _row_text(row, [
            "created_at", "nom_traitement", "systeme", "base_legale",
            "risk_level", "missing_info", "duree_conservation",
        ])
        for row in rows[:limit]
    ]


def _summarise_generic(rows: list[dict], keys: list[str], limit: int = 8) -> list[str]:
    return ["- " + _row_text(row, keys) for row in rows[:limit]]


def build_platform_context(intent: str, entities: dict[str, Any]) -> tuple[str, list[str]]:
    """Collect compact, latest platform data relevant to the detected intent."""
    sections: list[tuple[str, list[str]]] = []
    sources: list[str] = []

    def add(title: str, lines: list[str], source: str):
        if lines:
            sections.append((title, lines))
            sources.append(source)

    include_all = intent in {"governance_summary", "report_summary", "platform_navigation"}

    if intent in {"compliance_summary", "legal_basis_status", "governance_summary", "report_summary"} or include_all:
        treatments = _filter_system(crud.get_treatments(limit=25), entities)
        add("Dernieres analyses traitements", _summarise_treatments(treatments), "treatments")
        register = _filter_system(crud.get_register_entries(limit=25), entities)
        add("Registre RGPD", _summarise_register(register), "rgpd_register")

    if intent in {"risk_summary", "aipd_status", "governance_summary"} or include_all:
        risks = _filter_system(crud.get_risk_reviews(limit=25), entities)
        add("Revues de risques", _summarise_generic(risks, [
            "created_at", "nom_traitement", "systeme", "module",
            "niveau_risque", "nombre_risques", "aipd_decision",
        ]), "risk_reviews")
        dpia = crud.get_dpia_dossiers(limit=12)
        add("Dossiers AIPD", _summarise_generic(dpia, [
            "created_at", "nom_traitement", "niveau_risque", "aipd_decision", "statut",
        ]), "dpia_dossiers")

    if intent in {"incident_summary", "cnil_notification", "governance_summary"} or include_all:
        incidents = crud.get_incident_reviews(id_incident=entities.get("incident_id"), limit=25)
        incidents = _filter_system(incidents, entities)
        add("Revues incidents", _summarise_generic(incidents, [
            "created_at", "id_incident", "nom_traitement", "systeme",
            "qualification", "notifier_cnil", "notifier_personnes",
        ]), "incident_reviews")
        cnil = crud.get_cnil_notifications(limit=12)
        add("Dossiers CNIL", _summarise_generic(cnil, [
            "created_at", "notification_id", "id_incident", "systeme",
            "notifier_autorite", "notifier_personnes", "statut",
        ]), "cnil_notifications")

    if intent in {"dsar_status", "governance_summary"} or include_all:
        dsars = crud.get_dsars(limit=20)
        add("Demandes DSAR", _summarise_generic(dsars, [
            "created_at", "id_demande", "nom_demandeur", "type_droit",
            "systeme_concerne", "date_limite", "statut", "jours_restants",
        ]), "dsars")

    if intent in {"consent_status", "legal_basis_status", "governance_summary"} or include_all:
        consents = crud.get_consents()
        add("Consentements", _summarise_generic(consents, [
            "created_at", "id_consent", "nom_personne", "id_traitement",
            "nom_traitement", "statut", "date_expiration", "date_retrait",
        ], limit=12), "consents")

    if intent in {"proof_validation", "corrective_actions", "governance_summary"} or include_all:
        actions = crud.get_actions(limit=20)
        add("Actions correctives", _summarise_generic(actions, [
            "created_at", "id", "title", "severity", "status", "owner",
            "due_date", "proof_status", "pending_proofs_count",
        ]), "actions")
        validations = crud.get_dpo_validations(limit=20)
        add("Validations DPO", _summarise_generic(validations, [
            "created_at", "target_type", "target_label", "decision",
            "validator", "proof_reference",
        ]), "dpo_validations")
        proofs = crud.get_dpo_proof_history(limit=12)
        add("Preuves DPO", _summarise_generic(proofs, [
            "created_at", "target_type", "target_label", "decision",
            "validator", "proof_reference",
        ]), "dpo_proofs")

    if intent in {"governance_summary", "report_summary"} or include_all:
        snapshots = _filter_system(crud.get_governance_snapshots(limit=8), entities)
        add("Snapshots gouvernance", _summarise_generic(snapshots, [
            "created_at", "id", "nom_traitement", "systeme", "module",
            "score_maturite_rgpd", "niveau_maturite", "violations_count",
            "critical_alerts_count",
        ]), "governance_snapshots")

    if intent == "platform_navigation":
        add("Navigation plateforme", [
            "- Dossier CNIL: Incidents & risques -> Generer dossier CNIL, puis Registre incidents pour l'historique.",
            "- Analyses: Traitements -> Analyses disponibles.",
            "- AIPD: Dossiers d'impact.",
            "- DSAR: Droits des personnes et Historique des droits.",
            "- Preuves/validations: Validations DPO, Actions correctives et Registre traitements.",
            "- Gouvernance: Cockpit DPO / Gouvernance.",
        ], "navigation")

    if not sections:
        treatments = _filter_system(crud.get_treatments(limit=12), entities)
        add("Dernieres analyses traitements", _summarise_treatments(treatments), "treatments")
        snapshots = crud.get_governance_snapshots(limit=5)
        add("Snapshots gouvernance", _summarise_generic(snapshots, [
            "created_at", "id", "systeme", "score_maturite_rgpd",
            "niveau_maturite", "violations_count",
        ]), "governance_snapshots")

    context = []
    for title, lines in sections:
        context.append(f"{title}:\n" + "\n".join(lines))
    return "\n\n".join(context)[:9000], sorted(set(sources))


def retrieve_rag_context(question: str, intent: str) -> tuple[str, list[str]]:
    try:
        global _rag_cache
        if _rag_cache is None:
            from llm.rag_builder import get_rag, search

            index, chunks, model = get_rag()
            _rag_cache = (index, chunks, model, search)
        index, chunks, model, search = _rag_cache
        query = f"{question} {intent} RGPD CNIL droits personnes conformité"
        results = search(query, index, chunks, model, top_k=3)
        lines = []
        sources = []
        for item in results:
            source = item.get("source", "RAG")
            text = str(item.get("text", "")).strip()
            if text:
                lines.append(f"[{source}] {text[:450]}")
                sources.append(source)
        return "\n\n".join(lines), sorted(set(sources))
    except Exception:
        return "", []


def _local_answer(question: str, intent: str, platform_context: str, rag_context: str) -> str:
    if not platform_context.strip():
        return (
            "Je ne trouve pas encore de données enregistrées correspondant à cette demande. "
            "Lancez d'abord une analyse QALITAS/GMAO ou ouvrez un dossier dans la plateforme.\n\n"
            "Cette réponse est une aide à la décision et doit être validée par le DPO."
        )
    excerpt = platform_context[:1600]
    legal = f"\n\nContexte juridique disponible:\n{rag_context[:700]}" if rag_context else ""
    return (
        "Voici une synthèse basée sur les dernières données enregistrées dans la plateforme:\n\n"
        f"{excerpt}{legal}\n\n"
        "Conclusion: utilisez cette synthèse comme aide au pilotage. Les décisions finales "
        "et validations réglementaires doivent rester confirmées par le DPO."
    )


def generate_assistant_answer(question: str, history: list[str] | None = None, user: dict | None = None) -> dict[str, Any]:
    question = str(question or "").strip()
    if not question:
        return {
            "answer": "Posez une question liée à la plateforme RGPD.",
            "scope": "empty",
            "intent": {"label": "out_of_scope", "confidence": 0.0},
            "entities": {},
            "sources": [],
        }

    working_question = _contextualise_question(question, history)
    intent = _maybe_override_intent(question, predict_assistant_intent(question, history=history))
    entities = extract_entities(working_question)
    if intent["label"] == "out_of_scope":
        return {
            "answer": REFUSAL,
            "scope": "out_of_scope",
            "intent": intent,
            "entities": entities,
            "sources": [],
        }

    platform_context, platform_sources = build_platform_context(intent["label"], entities)
    rag_context, rag_sources = retrieve_rag_context(working_question, intent["label"])
    deterministic = _deterministic_answer(question, intent["label"], entities)
    if deterministic:
        answer, forced_sources = deterministic
        merged_sources = sorted(set(platform_sources + forced_sources + ([f"RAG:{src}" for src in rag_sources] if rag_sources else [])))
        return {
            "answer": answer,
            "scope": "platform",
            "intent": intent,
            "entities": entities,
            "sources": merged_sources,
        }

    system_prompt = (
        "Tu es l'assistant DPO interne de la plateforme RGPD de TIM Consulting.\n"
        "Tu reponds uniquement aux questions liees a la plateforme, aux analyses, traitements, "
        "risques, incidents, DSAR, AIPD, consentements, actions, preuves, validations, rapports, "
        "QALITAS, GMAO et au RGPD.\n"
        "Si la question est hors perimetre, refuse poliment.\n"
        "Utilise uniquement le contexte fourni. N'invente pas de donnees.\n"
        "Tu ne prends jamais de decision juridique finale: tu fournis une aide a la decision a valider par le DPO.\n"
        "Reponds en francais, de maniere claire et operationnelle."
    )
    user_prompt = f"""Question DPO:
{question}

Contexte conversation recent:
{history[-3:] if history else "Aucun historique utile."}

Intention NLP detectee:
{intent}

Entites detectees:
{entities}

Contexte plateforme recent:
{platform_context or "Aucune donnee plateforme pertinente trouvee."}

Contexte juridique RAG:
{rag_context or "Aucun extrait RAG disponible."}

Reponse attendue:
- repondre directement a la question;
- indiquer brievement les donnees utilisees si utile;
- si l'information manque, le dire clairement;
- terminer par une phrase courte rappelant que la validation finale appartient au DPO.
"""
    try:
        client = _get_groq()
        response = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=900,
            temperature=0.2,
        )
        answer = response.choices[0].message.content.strip()
    except Exception as exc:
        answer = _local_answer(working_question, intent["label"], platform_context, rag_context)
        answer += f"\n\nNote technique: réponse locale générée car le service LLM est indisponible ({exc})."

    return {
        "answer": answer,
        "scope": "platform",
        "intent": intent,
        "entities": entities,
        "sources": platform_sources + ([f"RAG:{src}" for src in rag_sources] if rag_sources else []),
    }
