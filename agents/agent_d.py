from datetime import datetime
from llm.report_generator import generate_report
from database import crud

# ===============================
# Q7 - DPO GOVERNANCE & COCKPIT
# ===============================

PRIORITES_ACTIONS = {
    "critique": 1,
    "eleve": 2,
    "moyen": 3,
    "faible": 4
}

MATURITE_SEUILS = {
    "Initial": (0, 25),
    "En developpement": (26, 50),
    "Defini": (51, 70),
    "Gere": (71, 85),
    "Optimise": (86, 100)
}


def _build_local_dpo_report(agent_a, agent_b, agent_c, q7_result, q8_result, error=None):
    """Return a deterministic DPO report when the LLM provider is unavailable."""
    q1 = agent_a.get("q1_cartographie", {})
    q3 = agent_a.get("q3_base_legale", {})
    consolidation = q7_result.get("consolidation", {})
    conformite = consolidation.get("conformite", {})
    risques = consolidation.get("risques", {})
    dsar = consolidation.get("dsar", {})
    priorites = q7_result.get("plan_actions_prioritaires", [])[:5]
    recommandations = q8_result.get("recommandations_amelioration", [])[:4]
    alertes = q7_result.get("alertes_critiques", []) + q7_result.get("alertes_elevees", [])

    lines = [
        "Rapport DPO - Gouvernance RGPD",
        "",
        "1. Synthese executive",
        (
            f"Le traitement \"{q1.get('nom_traitement') or q1.get('id_traitement') or 'Non specifie'}\" "
            f"sur le systeme {q1.get('systeme') or 'Plateforme RGPD'} presente un score de maturite "
            f"de {q7_result.get('score_maturite_rgpd', 0)}/100, niveau {q7_result.get('niveau_maturite', 'Non defini')}."
        ),
        (
            f"La base legale retenue est {q3.get('base_legale') or q3.get('base_legale_recommandee') or 'a confirmer'} "
            f"et {conformite.get('nombre_alertes_total', conformite.get('nombre_violations', 0))} point(s) de vigilance sont consolides."
        ),
        "",
        "2. Analyse de conformite",
        (
            f"Le controle couvre les principes de liceite, minimisation, duree de conservation, securite, "
            f"droits des personnes et Privacy by Design. Le niveau de conformite observe est "
            f"{conformite.get('niveau_conformite', 'Non defini')}."
        ),
        "",
        "3. Risques et incidents",
        (
            f"La matrice consolide {risques.get('nombre_risques', 0)} risque(s), dont "
            f"{risques.get('risques_critiques', 0)} critique(s) et {risques.get('risques_eleves', 0)} eleve(s). "
            f"Statut DSAR: {dsar.get('statut', 'aucune demande ouverte')}."
        ),
        "",
        "4. Plan d actions prioritaire",
    ]

    if priorites:
        lines.extend([
            f"- P{p.get('priorite', '-')}: {p.get('action', 'Action a definir')} "
            f"({p.get('domaine', 'gouvernance')}, delai: {p.get('delai', 'a definir')})"
            for p in priorites
        ])
    else:
        lines.append("- Aucune action critique immediate n est requise. Maintenir la surveillance et la preuve.")

    lines.extend(["", "5. Recommandations d amelioration"])
    if recommandations:
        for reco in recommandations:
            recs = reco.get("recommandations", [])[:2]
            lines.append(f"- {reco.get('faiblesse', 'Axe d amelioration')}: " + "; ".join(recs))
    else:
        lines.append("- Poursuivre la mise a jour du registre, la validation DPO et la collecte de preuves opposables.")

    lines.extend(["", "6. Conclusion DPO"])
    if alertes:
        lines.append("Des alertes doivent etre traitees en priorite: " + " | ".join(alertes[:4]))
    else:
        lines.append("Aucune alerte critique n est consolidee dans ce rapport. La gouvernance reste a surveiller dans la duree.")
    if error:
        lines.append("")
        lines.append(f"Note technique: rapport genere localement car le service IA externe etait indisponible ({error}).")
    return "\n".join(lines)


def _resolve_governance_identity(agent_a, agent_b):
    q1 = agent_a.get("q1_cartographie", {})
    q1_register = agent_a.get("q1_register", {})
    agent_b_identity = agent_b.get("context", {}).get("identity", {}) if agent_b else {}
    return {
        "id_traitement": q1_register.get("id_traitement") or q1.get("id_traitement") or agent_b_identity.get("id_traitement"),
        "nom_traitement": q1_register.get("nom_traitement") or q1.get("nom_traitement") or agent_b_identity.get("nom_traitement"),
        "systeme": q1_register.get("systeme") or q1.get("systeme") or agent_b_identity.get("systeme"),
        "module": agent_b_identity.get("module") or agent_a.get("intelligence", {}).get("qalitas_module")
    }


def _build_fallback_agent_b(agent_a):
    """Use a neutral risk context when Governance is launched before a Q4 analysis exists."""
    q1 = agent_a.get("q1_cartographie", {})
    identity = {
        "id_traitement": q1.get("id_traitement"),
        "nom_traitement": q1.get("nom_traitement"),
        "systeme": q1.get("systeme"),
        "module": agent_a.get("intelligence", {}).get("qalitas_module"),
    }
    return {
        "context": {"identity": identity},
        "q4_risques_aipd": {
            "nombre_risques": 0,
            "risques_critiques": 0,
            "risques_eleves": 0,
            "aipd": {
                "aipd_requise": False,
                "aipd_realisee": False,
            },
        },
        "q6_incidents": {
            "incident_declare": False,
            "notification": {"notifier_cnil": False},
        },
    }


def _compute_metric_trend(current, previous, higher_is_better=False):
    current = current or 0
    previous = previous or 0
    delta = current - previous
    if delta == 0:
        direction = "stable"
        label = "Stable"
    elif delta > 0:
        direction = "hausse"
        label = "En hausse"
    else:
        direction = "baisse"
        label = "En baisse"

    if delta == 0:
        appreciation = "neutre"
    elif higher_is_better:
        appreciation = "positif" if delta > 0 else "negatif"
    else:
        appreciation = "positif" if delta < 0 else "negatif"

    return {
        "current": current,
        "previous": previous,
        "delta": delta,
        "direction": direction,
        "label": label,
        "appreciation": appreciation,
    }


def _build_governance_trends(historique, score_maturite, conformite, dsar):
    snapshots = historique.get("snapshots", []) if historique else []
    previous_snapshot = snapshots[0] if snapshots else {}
    summary = historique.get("summary", {}) if historique else {}

    return {
        "snapshot_reference_date": previous_snapshot.get("created_at"),
        "maturite": _compute_metric_trend(
            score_maturite,
            previous_snapshot.get("score_maturite_rgpd", score_maturite),
            higher_is_better=True
        ),
        "violations": _compute_metric_trend(
            conformite.get("nombre_alertes_total", conformite.get("nombre_violations", 0)),
            previous_snapshot.get("violations_count", summary.get("violations_count", 0)),
            higher_is_better=False
        ),
        "actions_ouvertes": _compute_metric_trend(
            summary.get("actions_open_count", 0),
            previous_snapshot.get("actions_open_count", summary.get("actions_open_count", 0)),
            higher_is_better=False
        ),
        "dsars_ouvertes": _compute_metric_trend(
            summary.get("dsars_open_count", dsar.get("dsar_declare", False) and 1 or 0),
            previous_snapshot.get("dsars_open_count", summary.get("dsars_open_count", 0)),
            higher_is_better=False
        )
    }


def charger_historique_gouvernance(agent_a, agent_b, agent_c):
    identity = _resolve_governance_identity(agent_a, agent_b)
    systeme = identity.get("systeme")
    module = identity.get("module")
    id_traitement = identity.get("id_traitement")
    nom_traitement = identity.get("nom_traitement")

    register_entries = crud.get_register_entries(systeme=systeme, limit=200) if systeme else crud.get_register_entries(limit=200)
    actions = crud.get_actions(limit=300)
    if id_traitement:
        actions = [a for a in actions if a.get("linked_treatment_id") == id_traitement]

    risk_reviews = crud.get_risk_reviews(systeme=systeme, module=module, limit=100)
    if id_traitement:
        risk_reviews = [r for r in risk_reviews if r.get("id_traitement") == id_traitement]

    incident_reviews = crud.get_incident_reviews(systeme=systeme, limit=100) if systeme else crud.get_incident_reviews(limit=100)
    if id_traitement:
        incident_reviews = [r for r in incident_reviews if r.get("id_traitement") == id_traitement]

    dsars = crud.get_dsars(limit=200)
    if systeme:
        dsars = [d for d in dsars if (d.get("systeme_concerne") or "").upper().startswith(systeme.split()[0].upper())]

    dsar_executions = crud.get_dsar_executions(limit=200)
    dsars_open = [d for d in dsars if d.get("statut") != "Cloture"]
    dsar_ids = {d.get("id_demande") for d in dsars if d.get("id_demande")}
    if dsar_ids:
        dsar_executions = [e for e in dsar_executions if e.get("id_demande") in dsar_ids]

    dpia_dossiers = crud.get_dpia_dossiers(limit=100)
    if nom_traitement:
        dpia_dossiers = [d for d in dpia_dossiers if d.get("nom_traitement") == nom_traitement]

    cnil_notifications = crud.get_cnil_notifications(limit=100)
    if systeme:
        cnil_notifications = [n for n in cnil_notifications if n.get("systeme") == systeme]

    governance_snapshots = crud.get_governance_snapshot_summaries(
        id_traitement=id_traitement,
        systeme=systeme,
        module=module,
        limit=20
    )

    consents = crud.get_consents(id_traitement=id_traitement) if id_traitement else crud.get_consents()
    violations = crud.get_violations(limit=200)

    active_consents = [c for c in consents if c.get("statut") == "actif"]
    withdrawn_consents = [c for c in consents if c.get("statut") == "retire"]
    actions_open = [a for a in actions if a.get("status") != "Cloturee"]
    actions_closed = [a for a in actions if a.get("status") == "Cloturee"]

    return {
        "identity": identity,
        "summary": {
            "register_entries_count": len(register_entries),
            "actions_open_count": len(actions_open),
            "actions_closed_count": len(actions_closed),
            "risk_reviews_count": len(risk_reviews),
            "incident_reviews_count": len(incident_reviews),
            "violations_count": len(violations),
            "dsars_count": len(dsars),
            "dsars_open_count": len(dsars_open),
            "dsar_executions_count": len(dsar_executions),
            "dpia_dossiers_count": len(dpia_dossiers),
            "cnil_notifications_count": len(cnil_notifications),
            "consents_active_count": len(active_consents),
            "consents_withdrawn_count": len(withdrawn_consents),
            "governance_snapshots_count": len(governance_snapshots),
        },
        "recent": {
            "actions": actions[:5],
            "risk_reviews": risk_reviews[:5],
            "incident_reviews": incident_reviews[:5],
            "dsars": dsars[:5],
            "dpia_dossiers": dpia_dossiers[:5],
            "cnil_notifications": cnil_notifications[:5],
            "consents": consents[:5],
            "governance_snapshots": governance_snapshots[:5],
        },
        "snapshots": governance_snapshots
    }


def consolider_conformite(agent_a):
    q2 = agent_a.get("q2_conformite", {})
    q3 = agent_a.get("q3_base_legale", {})
    q1 = agent_a.get("q1_cartographie", {})
    axes = q2.get("axes_conformite", []) or []
    axe_by_code = {ax.get("code"): ax for ax in axes}
    violations = q2.get("violations", [])
    score_vigilance = q2.get("score_vigilance", q2.get("score_normalise", 0))
    niveau = q2.get("niveau_vigilance", q2.get("niveau_risque", "Inconnu"))
    nombre_points_documentaires = q2.get("nombre_points_documentaires", 0)
    nombre_alertes_total = q2.get("nombre_alertes_total", q2.get("nombre_violations", 0) + nombre_points_documentaires)

    violations_critiques = [v for v in violations if v.get("gravite") == 3]
    violations_elevees = [v for v in violations if v.get("gravite") == 2]

    alertes = []
    if not q3.get("base_legale_confirmee"):
        alertes.append("CRITIQUE: Aucune base legale confirmee pour ce traitement")
    if q3.get("consentement_retire"):
        alertes.append("CRITIQUE: Consentement retire - traitement doit etre suspendu immediatement")
    if len(violations_critiques) > 3:
        alertes.append("ELEVE: Nombre eleve de violations critiques detectees (" + str(len(violations_critiques)) + ")")

    return {
        "score_conformite": score_vigilance,
        "niveau_conformite": niveau,
        "nombre_violations": q2.get("nombre_violations", 0),
        "nombre_points_documentaires": nombre_points_documentaires,
        "nombre_alertes_total": nombre_alertes_total,
        "violations_critiques": len(violations_critiques),
        "violations_elevees": len(violations_elevees),
        "alertes_conformite": alertes,
        "base_legale": q3.get("base_legale_confirmee", "Non definie"),
        "base_legale_confirmee": q3.get("base_legale_confirmee"),
        "base_legale_presumee": q3.get("base_legale_presumee"),
        "alertes_base_legale": q3.get("alertes_base_legale", []),
        "q1_alertes_count": len(q1.get("alertes_q1", []) or []),
        "duree_conservation_definie": (q1.get("duree_conservation") or "").strip().lower() not in {"", "non definie", "non définie"},
        "processus_droits_personnes": (axe_by_code.get("droits", {}) or {}).get("status") == "conforme",
        "preuves_securite_count": len(q2.get("preuves_conformite", {}).get("mesures_securite_documentees", []) or []),
        "privacy_by_design": (axe_by_code.get("privacy", {}) or {}).get("status") == "conforme",
    }


def consolider_risques(agent_b):
    agent_b = agent_b or _build_fallback_agent_b({})
    q4 = agent_b.get("q4_risques_aipd", {})
    q6 = agent_b.get("q6_incidents", {})
    aipd = q4.get("aipd", {})

    alertes = []
    if aipd.get("aipd_requise") and not aipd.get("aipd_realisee"):
        alertes.append("CRITIQUE: AIPD obligatoire non realisee - Action urgente requise")
    if q4.get("risques_critiques", 0) > 0:
        alertes.append("CRITIQUE: " + str(q4.get("risques_critiques")) + " risque(s) critique(s) identifie(s)")
    if q6.get("incident_declare") and q6.get("notification", {}).get("notifier_cnil"):
        alertes.append("CRITIQUE: Notification CNIL requise dans les 72 heures")

    return {
        "nombre_risques": q4.get("nombre_risques", 0),
        "risques_critiques": q4.get("risques_critiques", 0),
        "risques_eleves": q4.get("risques_eleves", 0),
        "aipd_requise": aipd.get("aipd_requise", False),
        "aipd_realisee": aipd.get("aipd_realisee", False),
        "incident_declare": q6.get("incident_declare", False),
        "notification_cnil_requise": q6.get("notification", {}).get("notifier_cnil", False),
        "alertes_risques": alertes
    }


def consolider_dsar(agent_c):
    if not agent_c:
        return {
            "dsar_declare": False,
            "alertes_dsar": []
        }

    q5 = agent_c.get("q5_droits", {})
    delais = q5.get("delais", {})
    alertes = []

    jours_restants = delais.get("jours_restants", 30)
    if jours_restants < 0:
        alertes.append("CRITIQUE: Delai DSAR depasse - risque de sanction CNIL")
    elif jours_restants <= 5:
        alertes.append("CRITIQUE: Delai DSAR critique - moins de 5 jours restants")
    elif jours_restants <= 10:
        alertes.append("ELEVE: Delai DSAR urgent - moins de 10 jours restants")

    if q5.get("qualification") == "abusive":
        alertes.append("INFO: Demande DSAR qualifiee comme abusive - refus motive genere")

    return {
        "dsar_declare": True,
        "qualification": q5.get("qualification"),
        "statut": q5.get("reponse", {}).get("statut_reponse"),
        "jours_restants": jours_restants,
        "statut_delai": delais.get("statut_delai"),
        "alertes_dsar": alertes
    }


def generer_priorites(conformite, risques, dsar):
    priorites = []

    if risques.get("aipd_requise") and not risques.get("aipd_realisee"):
        priorites.append({
            "priorite": 1,
            "niveau": "Critique",
            "action": "Realiser l AIPD obligatoire immediatement",
            "domaine": "Risques",
            "delai": "Immediat"
        })

    if risques.get("notification_cnil_requise"):
        priorites.append({
            "priorite": 1,
            "niveau": "Critique",
            "action": "Notifier la CNIL dans les 72 heures",
            "domaine": "Incidents",
            "delai": "72 heures"
        })

    if not conformite.get("base_legale") or conformite.get("base_legale") == "Non definie":
        priorites.append({
            "priorite": 1,
            "niveau": "Critique",
            "action": "Definir une base legale valide pour le traitement",
            "domaine": "Conformite",
            "delai": "Immediat"
        })

    if conformite.get("violations_critiques", 0) > 0:
        priorites.append({
            "priorite": 2,
            "niveau": "Eleve",
            "action": "Corriger les " + str(conformite.get("violations_critiques")) + " violation(s) critique(s) detectee(s)",
            "domaine": "Conformite",
            "delai": "7 jours"
        })

    if risques.get("risques_critiques", 0) > 0:
        priorites.append({
            "priorite": 2,
            "niveau": "Eleve",
            "action": "Traiter les " + str(risques.get("risques_critiques")) + " risque(s) critique(s) identifie(s)",
            "domaine": "Risques",
            "delai": "7 jours"
        })

    if dsar.get("dsar_declare") and dsar.get("jours_restants", 30) <= 10:
        priorites.append({
            "priorite": 2,
            "niveau": "Eleve",
            "action": "Traiter la demande DSAR en urgence",
            "domaine": "Droits personnes",
            "delai": str(dsar.get("jours_restants")) + " jours"
        })

    if conformite.get("violations_elevees", 0) > 0:
        priorites.append({
            "priorite": 3,
            "niveau": "Moyen",
            "action": "Corriger les " + str(conformite.get("violations_elevees")) + " violation(s) de gravite elevee",
            "domaine": "Conformite",
            "delai": "30 jours"
        })

    priorites.sort(key=lambda x: x["priorite"])
    return priorites


def calculer_score_maturite(conformite, risques, dsar):
    score = 100

    # Penalite de fond basee sur le niveau de non-conformite global.
    score -= round((conformite.get("score_conformite", 0) or 0) * 0.45)

    # Penalites complementaires, mais avec plafonds plus modérés pour éviter l effondrement à 0.
    score -= min(conformite.get("nombre_violations", 0) * 0.5, 12)
    score -= min(conformite.get("violations_critiques", 0) * 3, 9)
    score -= min(conformite.get("violations_elevees", 0) * 1, 6)

    # Risques critiques / élevés : pondération gouvernance plus réaliste.
    score -= min(risques.get("risques_critiques", 0) * 3, 9)
    score -= min(risques.get("risques_eleves", 0) * 1.5, 6)

    if not conformite.get("base_legale_confirmee"):
        score -= 6
    elif conformite.get("base_legale_presumee") and conformite.get("base_legale_presumee") != conformite.get("base_legale_confirmee"):
        score -= 2

    if not conformite.get("duree_conservation_definie"):
        score -= 5

    if conformite.get("preuves_securite_count", 0) == 0:
        score -= 5

    if not conformite.get("processus_droits_personnes"):
        score -= 5

    score -= min(conformite.get("q1_alertes_count", 0) * 1.5, 6)

    if not conformite.get("privacy_by_design"):
        score -= 3

    if risques.get("aipd_requise") and not risques.get("aipd_realisee"):
        score -= 6

    if risques.get("notification_cnil_requise"):
        score -= 5

    if dsar.get("dsar_declare") and dsar.get("jours_restants", 30) < 0:
        score -= 5

    # Eviter les scores artificiellement nuls pour des traitements faibles mais pas "morts".
    if (
        score < 15
        and (
            conformite.get("nombre_violations", 0) > 0
            or risques.get("nombre_risques", 0) > 0
            or conformite.get("q1_alertes_count", 0) > 0
        )
    ):
        score = 15

    score = max(0, min(100, round(score)))

    niveau_maturite = "Initial"
    for niveau, (min_s, max_s) in MATURITE_SEUILS.items():
        if min_s <= score <= max_s:
            niveau_maturite = niveau
            break

    return score, niveau_maturite


def run_q7(agent_a, agent_b, agent_c, historique=None):
    conformite = consolider_conformite(agent_a)
    risques = consolider_risques(agent_b)
    dsar = consolider_dsar(agent_c)
    priorites = generer_priorites(conformite, risques, dsar)
    score_maturite, niveau_maturite = calculer_score_maturite(conformite, risques, dsar)
    historique = historique or {"summary": {}, "recent": {}, "identity": {}}

    toutes_alertes = (
        conformite.get("alertes_conformite", []) +
        risques.get("alertes_risques", []) +
        dsar.get("alertes_dsar", [])
    )

    if historique["summary"].get("actions_open_count", 0) > 5:
        toutes_alertes.append("ELEVE: Plusieurs actions correctives restent ouvertes")
    if historique["summary"].get("incident_reviews_count", 0) > 1:
        toutes_alertes.append("ELEVE: Plusieurs revues d incidents ont ete enregistrees")
    if historique["summary"].get("dsars_open_count", 0) > 0:
        toutes_alertes.append("INFO: Des demandes DSAR restent ouvertes")
    alertes_critiques = [a for a in toutes_alertes if a.startswith("CRITIQUE")]
    alertes_elevees = [a for a in toutes_alertes if a.startswith("ELEVE")]
    tendances = _build_governance_trends(historique, score_maturite, conformite, dsar)
    tendances["alertes_critiques"] = _compute_metric_trend(
        len(alertes_critiques),
        (historique.get("snapshots") or [{}])[0].get("critical_alerts_count", len(alertes_critiques)),
        higher_is_better=False
    )

    return {
        "consolidation": {
            "conformite": conformite,
            "risques": risques,
            "dsar": dsar
        },
        "historique": historique,
        "tendances": tendances,
        "score_maturite_rgpd": score_maturite,
        "niveau_maturite": niveau_maturite,
        "alertes_critiques": alertes_critiques,
        "alertes_elevees": alertes_elevees,
        "plan_actions_prioritaires": priorites
    }


# ===============================
# Q8 - CONTINUOUS IMPROVEMENT
# ===============================

RECOMMANDATIONS_AMELIORATION = {
    "securite": {
        "faiblesse": "Mesures de securite insuffisantes",
        "recommandations": [
            "Mettre en place un chiffrement AES-256 pour toutes les donnees sensibles",
            "Implementer une authentification multi-facteurs (MFA)",
            "Effectuer des audits de securite trimestriels",
            "Former les employes aux bonnes pratiques de securite"
        ],
        "norme": "ISO 27001"
    },
    "privacy_by_design": {
        "faiblesse": "Absence de Privacy by Design",
        "recommandations": [
            "Integrer la protection des donnees des la conception de chaque nouveau traitement",
            "Adopter le principe de minimisation des donnees par defaut",
            "Documenter les choix de conception lies a la protection des donnees",
            "Effectuer des revues Privacy by Design avant chaque mise en production"
        ],
        "norme": "Art. 25 RGPD"
    },
    "base_legale": {
        "faiblesse": "Base legale manquante ou inappropriee",
        "recommandations": [
            "Auditer tous les traitements et attribuer une base legale documentee",
            "Privilegier le contrat ou l obligation legale au lieu du consentement pour les employes",
            "Mettre a jour le registre des traitements (Art. 30)",
            "Former les responsables de traitement aux bases legales RGPD"
        ],
        "norme": "Art. 6 RGPD"
    },
    "conservation": {
        "faiblesse": "Durees de conservation non definies ou depassees",
        "recommandations": [
            "Definir une politique de conservation pour chaque categorie de donnees",
            "Mettre en place des purges automatiques a l expiration des durees",
            "Documenter les bases legales justifiant les durees de conservation",
            "Effectuer un audit annuel des donnees conservees"
        ],
        "norme": "Art. 5(1)(e) RGPD"
    },
    "droits_personnes": {
        "faiblesse": "Processus de gestion des droits insuffisant",
        "recommandations": [
            "Mettre en place un formulaire DSAR centralise et accessible",
            "Definir une procedure interne de traitement des demandes en moins de 30 jours",
            "Former les equipes a la gestion des droits RGPD",
            "Automatiser les alertes de delais pour les demandes en cours"
        ],
        "norme": "Art. 15-22 RGPD"
    },
    "transferts": {
        "faiblesse": "Transferts internationaux non encadres",
        "recommandations": [
            "Identifier tous les transferts de donnees hors du pays",
            "Signer des clauses contractuelles types (CCT) avec les destinataires",
            "Verifier les adequations reconnues par la CNIL",
            "Documenter les garanties appropriees pour chaque transfert"
        ],
        "norme": "Art. 44-49 RGPD"
    }
}

VEILLE_REGLEMENTAIRE = [
    {
        "source": "CNIL",
        "sujet": "Recommandations sur les cookies et traceurs",
        "impact": "Verification des consentements pour les cookies dans QALITAS et GMAO PRO",
        "priorite": "Moyen"
    },
    {
        "source": "EDPB",
        "sujet": "Lignes directrices sur les transferts de donnees post-Brexit",
        "impact": "Verification des transferts vers le Royaume-Uni",
        "priorite": "Faible"
    },
    {
        "source": "IA Act (UE)",
        "sujet": "Reglement europeen sur l intelligence artificielle",
        "impact": "Evaluation des systemes IA utilises dans QALITAS et GMAO PRO",
        "priorite": "Eleve"
    },
    {
        "source": "NIS2",
        "sujet": "Directive sur la securite des reseaux et des systemes d information",
        "impact": "Renforcement des mesures de securite IT pour les systemes critiques",
        "priorite": "Eleve"
    }
]


def analyser_faiblesses(agent_a, agent_b, agent_c):
    faiblesses = []
    violations = agent_a.get("q2_conformite", {}).get("violations", [])
    q3 = agent_a.get("q3_base_legale", {})
    q4 = agent_b.get("q4_risques_aipd", {})
    q1 = agent_a.get("q1_cartographie", {})

    regles_violees = [v.get("id_regle") for v in violations]

    if not agent_a.get("q1_cartographie", {}).get("mesures_securite") or        any(r in regles_violees for r in ["RGPD-05", "RGPD-06", "RGPD-07"]):
        faiblesses.append("securite")

    if not q1.get("privacy_by_design") or "RGPD-08" in regles_violees:
        faiblesses.append("privacy_by_design")

    if not q3.get("base_legale") or "RGPD-01" in regles_violees:
        faiblesses.append("base_legale")

    if "RGPD-03" in regles_violees or "RGPD-04" in regles_violees:
        faiblesses.append("conservation")

    if not agent_a.get("q1_cartographie", {}).get("processus_droits_personnes"):
        faiblesses.append("droits_personnes")

    if agent_a.get("q1_cartographie", {}).get("transfert_etranger") and        "RGPD-44-49" in regles_violees:
        faiblesses.append("transferts")

    return list(set(faiblesses))


def generer_recommandations(faiblesses):
    recommandations = []
    for faiblesse in faiblesses:
        if faiblesse in RECOMMANDATIONS_AMELIORATION:
            rec = RECOMMANDATIONS_AMELIORATION[faiblesse].copy()
            rec["categorie"] = faiblesse
            recommandations.append(rec)
    return recommandations


def calculer_indice_maturite(score_maturite, faiblesses, agent_b):
    indice = score_maturite
    nb_faiblesses = len(faiblesses)
    # Max 2 points par faiblesse, plafonne a 12
    indice -= min(nb_faiblesses * 2, 12)
    indice = max(0, min(100, round(indice)))

    tendance = "Stable"
    if indice < 30:
        tendance = "Deterioration critique"
    elif indice < 50:
        tendance = "A ameliorer"
    elif indice < 70:
        tendance = "En progression"
    else:
        tendance = "Bonne maturite"

    return indice, tendance


def run_q8(agent_a, agent_b, agent_c, score_maturite, historique=None):
    faiblesses = analyser_faiblesses(agent_a, agent_b, agent_c)
    recommandations = generer_recommandations(faiblesses)
    indice_maturite, tendance = calculer_indice_maturite(score_maturite, faiblesses, agent_b)
    historique = historique or {"summary": {}}
    faiblesses_recurrentes = []
    if historique["summary"].get("incident_reviews_count", 0) >= 2:
        faiblesses_recurrentes.append("Incidents RGPD recurrents")
    if historique["summary"].get("actions_open_count", 0) >= 3:
        faiblesses_recurrentes.append("Accumulation d actions correctives ouvertes")
    if historique["summary"].get("dsars_open_count", 0) >= 2:
        faiblesses_recurrentes.append("Traitement DSAR a fluidifier")
    if historique["summary"].get("risk_reviews_count", 0) >= 2:
        faiblesses_recurrentes.append("Risques RGPD recurrents sur le meme perimetre")

    return {
        "faiblesses_identifiees": faiblesses,
        "faiblesses_recurrentes": faiblesses_recurrentes,
        "nombre_faiblesses": len(faiblesses),
        "recommandations_amelioration": recommandations,
        "indice_maturite_rgpd": indice_maturite,
        "tendance_maturite": tendance,
        "indicateurs_historique": {
            "actions_ouvertes": historique["summary"].get("actions_open_count", 0),
            "revues_risques": historique["summary"].get("risk_reviews_count", 0),
            "revues_incidents": historique["summary"].get("incident_reviews_count", 0),
            "dsars_ouvertes": historique["summary"].get("dsars_open_count", 0),
            "dossiers_aipd": historique["summary"].get("dpia_dossiers_count", 0),
            "consentements_actifs": historique["summary"].get("consents_active_count", 0),
            "consentements_retires": historique["summary"].get("consents_withdrawn_count", 0),
        },
        "veille_reglementaire": VEILLE_REGLEMENTAIRE,
        "plan_amelioration": {
            "court_terme": [r for r in recommandations if r["categorie"] in ["base_legale", "securite"]],
            "moyen_terme": [r for r in recommandations if r["categorie"] in ["privacy_by_design", "conservation"]],
            "long_terme": [r for r in recommandations if r["categorie"] in ["droits_personnes", "transferts"]]
        }
    }


# ===============================
# AGENT D - MAIN
# ===============================

def run_agent_d(agent_a, agent_b, agent_c=None):
    agent_b = agent_b or _build_fallback_agent_b(agent_a)
    historique = charger_historique_gouvernance(agent_a, agent_b, agent_c)
    q7_result = run_q7(agent_a, agent_b, agent_c, historique)
    q8_result = run_q8(agent_a, agent_b, agent_c, q7_result["score_maturite_rgpd"], historique)

    toutes_alertes = q7_result.get("alertes_critiques", []) + q7_result.get("alertes_elevees", [])

    # Generate natural language DPO report
    try:
        rapport_llm = generate_report(agent_a, agent_b, agent_c, {
            "synthese": {
                "score_maturite_global": q7_result["score_maturite_rgpd"],
                "niveau_maturite": q7_result["niveau_maturite"],
                "tendance": q8_result.get("tendance_maturite")
            },
            "q7_gouvernance": q7_result,
            "q8_amelioration": q8_result
        })
    except Exception as e:
        rapport_llm = _build_local_dpo_report(agent_a, agent_b, agent_c, q7_result, q8_result, str(e))

    return {
        "agent": "D - DPO Governance & Amelioration Continue",
        "date_analyse": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "q7_gouvernance": q7_result,
        "q8_amelioration": q8_result,
        "rapport_dpo": rapport_llm,
        "synthese": {
            "score_maturite_global": q7_result["score_maturite_rgpd"],
            "niveau_maturite": q7_result["niveau_maturite"],
            "nombre_alertes_critiques": len(q7_result.get("alertes_critiques", [])),
            "nombre_priorites": len(q7_result.get("plan_actions_prioritaires", [])),
            "nombre_recommandations": len(q8_result.get("recommandations_amelioration", [])),
            "tendance": q8_result.get("tendance_maturite")
        }
    }
