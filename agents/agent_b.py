from rules.severity import determiner_niveau_risque
from database import crud

SCENARIOS_RISQUES = [
    {
        "id": "R01",
        "scenario": "Acces non autorise",
        "conditions": lambda t: not t.get("mesures_securite"),
        "gravite": 3,
        "vraisemblance": 3,
        "mesures": [
            "Mettre en place un controle d acces strict",
            "Activer les logs d acces et surveiller les connexions",
            "Chiffrer les donnees sensibles au repos et en transit"
        ]
    },
    {
        "id": "R02",
        "scenario": "Fuite ou perte de donnees",
        "conditions": lambda t: t.get("transfert_etranger") or (
            not t.get("mesures_securite")
            and any(
                field in (t.get("donnees_collectees") or [])
                for field in ["Cin", "CinIssued", "CinIssuedTo", "Cin Adresse", "CinAddress", "Passport", "PassportNumber"]
            )
        ),
        "gravite": 3,
        "vraisemblance": 2,
        "mesures": [
            "Chiffrer toutes les transmissions de donnees",
            "Encadrer les transferts par des clauses contractuelles",
            "Auditer les acces des sous-traitants"
        ]
    },
    {
        "id": "R03",
        "scenario": "Detournement de finalite",
        "conditions": lambda t: not t.get("finalite_definie"),
        "gravite": 2,
        "vraisemblance": 2,
        "mesures": [
            "Documenter la finalite de chaque traitement",
            "Limiter l acces aux donnees selon la finalite declaree",
            "Mettre en place des revues periodiques des usages"
        ]
    },
    {
        "id": "R04",
        "scenario": "Sous-traitants non maitrises",
        "conditions": lambda t: t.get("transfert_etranger") and not t.get("garanties_specifiques"),
        "gravite": 3,
        "vraisemblance": 2,
        "mesures": [
            "Signer des DPA avec tous les sous-traitants",
            "Auditer regulierement les sous-traitants",
            "Verifier les certifications de securite des prestataires"
        ]
    },
    {
        "id": "R05",
        "scenario": "Donnees sensibles sans protection renforcee",
        "conditions": lambda t: t.get("donnees_sensibles") and not t.get("garanties_specifiques"),
        "gravite": 3,
        "vraisemblance": 2,
        "mesures": [
            "Appliquer un chiffrement fort sur toutes les donnees sensibles",
            "Restreindre l acces aux seules personnes habilitees",
            "Evaluer formellement la necessite d une AIPD au regard des criteres de risque"
        ]
    },
    {
        "id": "R06",
        "scenario": "Absence de Privacy by Design",
        "conditions": lambda t: not t.get("privacy_by_design"),
        "gravite": 2,
        "vraisemblance": 2,
        "mesures": [
            "Integrer la protection des donnees des la conception",
            "Minimiser la collecte de donnees par defaut",
            "Effectuer des revues de securite avant chaque mise en production"
        ]
    },
]

AIPD_TRIGGERS = [
    ("donnees_sensibles", "Donnees sensibles (Art. 9 RGPD)"),
    ("transfert_etranger", "Transfert de donnees hors du pays"),
    ("traitement_grande_echelle", "Traitement a grande echelle"),
    ("risque_eleve", "Risque eleve pour les droits et libertes"),
]

AIPD_COLLECTEES_TRIGGERS = [
    ("localisation_GPS", "Geolocalisation des personnes"),
    ("biometrie", "Donnees biometriques"),
    ("donnees_sante", "Donnees de sante"),
]

INCIDENT_DIMENSIONS = {
    "confidentialite": {
        "types": {"acces_non_autorise", "divulgation", "fuite", "exposition", "phishing", "perte_terminal"},
        "label": "Confidentialite"
    },
    "integrite": {
        "types": {"alteration", "modification", "corruption", "suppression_indue"},
        "label": "Integrite"
    },
    "disponibilite": {
        "types": {"indisponibilite", "ransomware", "blocage", "panne", "destruction"},
        "label": "Disponibilite"
    }
}


def _resolve_treatment_identity(traitement, agent_a_output):
    q1 = agent_a_output.get("q1_cartographie", {}) if agent_a_output else {}
    q1_register = agent_a_output.get("q1_register", {}) if agent_a_output else {}
    intelligence = agent_a_output.get("intelligence", {}) if agent_a_output else {}
    return {
        "id_traitement": q1_register.get("id_traitement") or q1.get("id_traitement") or traitement.get("id_traitement"),
        "systeme": q1.get("systeme") or traitement.get("systeme"),
        "module": (
            intelligence.get("qalitas_module")
            or intelligence.get("gmao_module")
            or traitement.get("qalitas_module")
            or traitement.get("gmao_module")
            or traitement.get("module")
        ),
        "nom_traitement": q1_register.get("nom_traitement") or q1.get("nom_traitement") or traitement.get("nom_traitement"),
    }


def _load_inventory_context(traitement, agent_a_output):
    identity = _resolve_treatment_identity(traitement, agent_a_output)
    treatment_id = identity.get("id_traitement")
    systeme = identity.get("systeme")
    module = identity.get("module")

    inventory_candidates = crud.get_inventory_treatments(systeme=systeme, module=module, limit=200)
    inventory_match = None
    for item in inventory_candidates:
        if treatment_id and item.get("id_traitement") == treatment_id:
            inventory_match = item
            break
    if not inventory_match and inventory_candidates:
        inventory_match = inventory_candidates[0]

    inventory_fields = []
    if inventory_match:
        inventory_fields = crud.get_inventory_fields(
            inventory_treatment_id=inventory_match.get("id"),
            limit=500
        )

    unstructured_scans = crud.get_unstructured_scans(
        linked_treatment_id=treatment_id,
        systeme=systeme,
        module=module,
        limit=100
    )

    structured_sensitive_count = len([f for f in inventory_fields if f.get("is_sensitive")])
    unstructured_sensitive_count = len([
        f for f in inventory_fields
        if f.get("source_kind") == "unstructured" and f.get("is_sensitive")
    ])

    return {
        "identity": identity,
        "inventory_treatment": inventory_match,
        "inventory_fields": inventory_fields,
        "unstructured_scans": unstructured_scans,
        "summary": {
            "inventory_found": bool(inventory_match),
            "structured_fields_count": len([f for f in inventory_fields if f.get("source_kind") == "structured"]),
            "unstructured_fields_count": len([f for f in inventory_fields if f.get("source_kind") == "unstructured"]),
            "structured_sensitive_count": structured_sensitive_count,
            "unstructured_sensitive_count": unstructured_sensitive_count,
            "unstructured_scans_count": len(unstructured_scans),
            "unstructured_findings_count": sum(s.get("nb_findings", 0) for s in unstructured_scans),
        }
    }


def _build_risk_evidence(context):
    summary = context.get("summary", {})
    identity = context.get("identity", {})
    inventory = context.get("inventory_treatment") or {}
    return {
        "id_traitement": identity.get("id_traitement"),
        "nom_traitement": identity.get("nom_traitement"),
        "systeme": identity.get("systeme"),
        "module": identity.get("module"),
        "inventory_found": summary.get("inventory_found", False),
        "structured_fields_count": summary.get("structured_fields_count", 0),
        "unstructured_fields_count": summary.get("unstructured_fields_count", 0),
        "unstructured_scans_count": summary.get("unstructured_scans_count", 0),
        "unstructured_findings_count": summary.get("unstructured_findings_count", 0),
        "risk_level_inventory": inventory.get("risk_level"),
        "duree_conservation_inventory": inventory.get("duree_conservation"),
        "inventory_snapshot_id": inventory.get("id"),
    }


def _memory_text(values) -> str:
    if values is None:
        return ""
    if isinstance(values, dict):
        return " ".join(_memory_text(v) for v in values.values())
    if isinstance(values, (list, tuple, set)):
        return " ".join(_memory_text(v) for v in values)
    return str(values)


def _normalise_aipd_memory_decision(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if any(token in text for token in ["non requ", "pas requ", "non obligatoire", "non_applicable"]):
        return "non_requise"
    if any(token in text for token in ["obligatoire", "requise", "requi", "valider_aipd", "aipd valide"]):
        return "requise"
    return None


def _build_q4_memory_query(identity, traitement, risques, residual, context):
    summary = context.get("summary", {})
    risk_names = [r.get("scenario") for r in risques]
    risk_levels = [r.get("niveau") for r in risques]
    fragments = [
        identity.get("id_traitement"),
        identity.get("nom_traitement"),
        identity.get("systeme"),
        identity.get("module"),
        traitement.get("finalite"),
        traitement.get("personnes_concernees"),
        traitement.get("donnees_collectees"),
        traitement.get("destinataires"),
        risk_names,
        risk_levels,
        residual.get("niveau_residuel") if residual else None,
        summary,
    ]
    return " | ".join(_memory_text(fragment) for fragment in fragments if _memory_text(fragment))


def _summarize_aipd_dpo_memory(precedents, expected_decision):
    if not precedents:
        return {
            "available": False,
            "count": 0,
            "impact": "aucun",
            "confidence": "aucune",
            "guidance": "Aucun precedent DPO similaire trouve pour la decision AIPD.",
            "examples": [],
        }

    examples = []
    for item in precedents[:3]:
        examples.append({
            "id": item.get("id"),
            "target_type": item.get("target_type"),
            "target_label": item.get("target_label"),
            "source_system": item.get("source_system"),
            "source_module": item.get("source_module"),
            "decision": item.get("decision"),
            "final_value": item.get("final_value"),
            "justification": item.get("justification"),
            "match_score": item.get("match_score", 0),
            "created_at": item.get("created_at"),
        })

    best = examples[0]
    score = best.get("match_score", 0) or 0
    if score >= 10:
        confidence = "forte"
    elif score >= 6:
        confidence = "moyenne"
    else:
        confidence = "faible"

    best_norm = _normalise_aipd_memory_decision(best.get("final_value") or best.get("justification") or best.get("decision"))
    if expected_decision and best_norm and expected_decision == best_norm:
        impact = "precedent_confirmant"
        guidance = "Un precedent DPO similaire confirme la decision AIPD actuelle."
    elif expected_decision and best_norm and expected_decision != best_norm:
        impact = "precedent_different"
        guidance = "Un precedent DPO similaire contient une decision AIPD differente : revue DPO recommandee."
    else:
        impact = "precedent_disponible"
        guidance = "Un precedent DPO similaire existe et peut aider la revue, sans remplacer la decision courante."

    return {
        "available": True,
        "count": len(precedents),
        "confidence": confidence,
        "impact": impact,
        "guidance": guidance,
        "best_match": best,
        "examples": examples,
        "recommended_value": best_norm,
    }


def _find_dpo_memory_for_aipd(identity, traitement, risques, residual, context, aipd):
    expected = "requise" if aipd.get("aipd_requise") else "non_requise"
    try:
        precedents = crud.find_similar_dpo_memory(
            target_type="dpia",
            source_system=identity.get("systeme"),
            source_module=identity.get("module"),
            query=_build_q4_memory_query(identity, traitement, risques, residual, context),
            limit=5,
        )
    except Exception:
        precedents = []
    return _summarize_aipd_dpo_memory(precedents, expected)


def _build_incident_evidence(context, incident):
    summary = context.get("summary", {})
    identity = context.get("identity", {})
    scans = context.get("unstructured_scans", [])
    sample_files = [s.get("filename") for s in scans[:5] if s.get("filename")]
    return {
        "id_traitement": identity.get("id_traitement"),
        "nom_traitement": identity.get("nom_traitement"),
        "systeme": identity.get("systeme"),
        "module": identity.get("module"),
        "incident_type": incident.get("type_incident"),
        "donnees_affectees_declared": incident.get("donnees_affectees", []),
        "inventory_found": summary.get("inventory_found", False),
        "structured_sensitive_count": summary.get("structured_sensitive_count", 0),
        "unstructured_sensitive_count": summary.get("unstructured_sensitive_count", 0),
        "unstructured_scans_count": summary.get("unstructured_scans_count", 0),
        "unstructured_findings_count": summary.get("unstructured_findings_count", 0),
        "sample_files": sample_files,
    }


def _measure_coverage(traitement, context):
    summary = context.get("summary", {})
    return {
        "has_sensitive_data": bool(
            traitement.get("donnees_sensibles")
            or summary.get("structured_sensitive_count", 0) > 0
            or summary.get("unstructured_sensitive_count", 0) > 0
        ),
        "has_security_measures": bool(traitement.get("mesures_securite")),
        "has_strong_guarantees": bool(
            traitement.get("chiffrement_actif")
            or traitement.get("garanties_specifiques")
            or traitement.get("tests_securite_reguliers")
        ),
        "has_third_parties": bool(traitement.get("destinataires")),
        "has_retention_defined": bool(traitement.get("duree_conservation_definie")),
        "has_privacy_by_design": bool(traitement.get("privacy_by_design")),
        "has_privacy_by_default": bool(traitement.get("privacy_by_default")),
    }


def _compute_residual_risk(risques, traitement, context):
    coverage = _measure_coverage(traitement, context)
    score_initial = sum(r["score"] for r in risques)
    mitigation = 0
    if coverage["has_security_measures"]:
        mitigation += 2
    if coverage["has_strong_guarantees"]:
        mitigation += 2
    if coverage["has_retention_defined"]:
        mitigation += 1
    if coverage["has_privacy_by_design"]:
        mitigation += 1
    if coverage["has_privacy_by_default"]:
        mitigation += 1
    if coverage["has_sensitive_data"]:
        mitigation = max(0, mitigation - 1)
    if traitement.get("transfert_etranger"):
        mitigation = max(0, mitigation - 1)
    if coverage["has_third_parties"] and not traitement.get("garanties_sous_traitant"):
        mitigation = max(0, mitigation - 1)

    score_residuel = max(score_initial - mitigation, 0)
    if score_residuel >= 10:
        niveau = "Critique"
    elif score_residuel >= 6:
        niveau = "Eleve"
    elif score_residuel >= 3:
        niveau = "Moyen"
    else:
        niveau = "Faible"

    if niveau in {"Critique", "Eleve"}:
        decision = "Risque residuel eleve - mesures complementaires obligatoires"
    elif niveau == "Moyen":
        decision = "Risque residuel acceptable sous conditions"
    else:
        decision = "Risque residuel acceptable"

    return {
        "score_initial": score_initial,
        "score_residuel": score_residuel,
        "niveau_residuel": niveau,
        "decision_residuelle": decision,
        "facteurs_mitigation": coverage,
        "points_mitigation": mitigation,
    }


def _build_aipd_measures(risques, traitement, residual):
    seen = set()
    mesures = []
    for risque in risques:
        for mesure in risque.get("mesures", []):
            if mesure not in seen:
                seen.add(mesure)
                mesures.append({
                    "mesure": mesure,
                    "priorite": "haute" if risque.get("niveau") == "Critique" else "moyenne",
                    "type": "technique" if any(k in mesure.lower() for k in ["chiffr", "log", "acces"]) else "organisationnelle"
                })
    if traitement.get("destinataires") and not traitement.get("contrat_sous_traitance"):
        mesures.append({
            "mesure": "Formaliser les clauses contractuelles avec les sous-traitants avant tout partage",
            "priorite": "haute",
            "type": "contractuelle"
        })
    if residual.get("niveau_residuel") in {"Critique", "Eleve"}:
        mesures.append({
            "mesure": "Valider la mise en oeuvre effective des mesures avant exploitation complete du traitement",
            "priorite": "haute",
            "type": "organisationnelle"
        })
    return mesures


def _infer_incident_dimensions(incident):
    incident_type = (incident.get("type_incident") or "").strip().lower().replace(" ", "_")
    dimensions = []
    for _, cfg in INCIDENT_DIMENSIONS.items():
        if incident_type in cfg["types"]:
            dimensions.append(cfg["label"])
    if not dimensions:
        if incident.get("donnees_chiffrees"):
            dimensions.append("Disponibilite")
        else:
            dimensions.append("Confidentialite")
    return dimensions


def _assess_incident_risk(incident, context):
    summary = context.get("summary", {})
    sensitive_volume = summary.get("structured_sensitive_count", 0) + summary.get("unstructured_sensitive_count", 0)
    people = incident.get("nombre_personnes_affectees", 0)
    severity = incident.get("gravite_incident", 1)
    score = severity * 2
    if people >= 100:
        score += 3
    elif people > 0:
        score += 1
    if incident.get("donnees_sensibles_impliquees") or sensitive_volume > 0:
        score += 3
    if summary.get("unstructured_findings_count", 0) > 0:
        score += 1
    if incident.get("donnees_chiffrees"):
        score = max(score - 3, 0)

    if score >= 8:
        niveau = "eleve"
    elif score >= 4:
        niveau = "moyen"
    else:
        niveau = "faible"

    return {
        "score_incident": score,
        "niveau_incident": niveau,
        "dimensions": _infer_incident_dimensions(incident),
        "sensitive_volume_detected": sensitive_volume,
    }

def evaluer_risques(traitement):
    risques = []
    for scenario in SCENARIOS_RISQUES:
        try:
            if scenario["conditions"](traitement):
                score = scenario["gravite"] * scenario["vraisemblance"]
                if score >= 6:
                    niveau = "Critique"
                elif score >= 4:
                    niveau = "Eleve"
                elif score >= 2:
                    niveau = "Moyen"
                else:
                    niveau = "Faible"
                risques.append({
                    "id": scenario["id"],
                    "scenario": scenario["scenario"],
                    "gravite": scenario["gravite"],
                    "vraisemblance": scenario["vraisemblance"],
                    "score": score,
                    "niveau": niveau,
                    "mesures": scenario["mesures"]
                })
        except Exception:
            pass
    return risques

def decider_aipd(traitement, risques, residual=None):
    raisons = []
    strong_reasons = 0
    aipd_trigger_immediat = False
    for champ, raison in AIPD_TRIGGERS:
        if traitement.get(champ):
            raisons.append(raison)
            strong_reasons += 1
            if champ in {"donnees_sensibles", "traitement_grande_echelle", "risque_eleve"}:
                aipd_trigger_immediat = True
    donnees = traitement.get("donnees_collectees", [])
    for champ, raison in AIPD_COLLECTEES_TRIGGERS:
        if champ in donnees:
            raisons.append(raison)
            strong_reasons += 1
            aipd_trigger_immediat = True
    critiques = [r for r in risques if r["niveau"] == "Critique"]
    if critiques:
        raisons.append(str(len(critiques)) + " risque(s) critique(s) detecte(s)")
        if len(critiques) >= 2 and (traitement.get("risque_eleve") or traitement.get("transfert_etranger")):
            strong_reasons += 1
    if residual and residual.get("niveau_residuel") in {"Critique", "Eleve"}:
        raisons.append(f"Risque residuel {residual.get('niveau_residuel').lower()} apres mesures")
        strong_reasons += 1
    if traitement.get("risque_eleve") and (traitement.get("donnees_sensibles") or len(critiques) >= 2):
        raisons.append("Risque eleve confirme par la nature du traitement et les risques identifies")
        strong_reasons += 1
    aipd_requise = aipd_trigger_immediat or strong_reasons >= 2
    if aipd_requise:
        if traitement.get("aipd_realisee"):
            decision = "AIPD realisee - Verifier la mise a jour et le risque residuel"
        else:
            decision = "AIPD obligatoire - Non realisee - Action urgente requise"
    else:
        decision = "AIPD non automatiquement obligatoire - evaluation documentee recommandee"
    return {
        "aipd_requise": aipd_requise,
        "aipd_obligatoire": aipd_requise,
        "raisons_declenchement": raisons,
        "aipd_realisee": traitement.get("aipd_realisee", False),
        "decision": decision,
        "risque_residuel": residual or {},
    }

def run_q4(traitement, agent_a_output):
    context = _load_inventory_context(traitement, agent_a_output)
    enriched_traitement = dict(traitement)
    summary = context.get("summary", {})
    if summary.get("structured_sensitive_count", 0) > 0 or summary.get("unstructured_sensitive_count", 0) > 0:
        enriched_traitement["donnees_sensibles"] = True
    if summary.get("unstructured_scans_count", 0) > 0 and not enriched_traitement.get("mesures_securite"):
        enriched_traitement["mesures_securite"] = enriched_traitement.get("mesures_securite", [])
    risques = evaluer_risques(enriched_traitement)
    residual = _compute_residual_risk(risques, enriched_traitement, context)
    aipd = decider_aipd(enriched_traitement, risques, residual)
    aipd["memoire_dpo"] = _find_dpo_memory_for_aipd(
        context.get("identity", {}),
        enriched_traitement,
        risques,
        residual,
        context,
        aipd,
    )
    if aipd["memoire_dpo"].get("impact") == "precedent_different":
        aipd["alerte_memoire_dpo"] = "Memoire DPO : precedent similaire avec une decision AIPD differente."
    enriched_traitement["aipd_obligatoire"] = aipd.get("aipd_obligatoire", False)
    return {
        "risques_identifies": risques,
        "nombre_risques": len(risques),
        "risques_critiques": len([r for r in risques if r["niveau"] == "Critique"]),
        "risques_eleves": len([r for r in risques if r["niveau"] == "Eleve"]),
        "aipd": aipd,
        "mesures_prioritaires": _build_aipd_measures(risques, enriched_traitement, residual),
        "residual_risk": residual,
        "evidence": _build_risk_evidence(context)
    }

def qualifier_incident(incident, context=None):
    assessment = _assess_incident_risk(incident, context or {})
    gravite = incident.get("gravite_incident", 1)
    donnees_sensibles = incident.get("donnees_sensibles_impliquees", False)
    nombre_personnes = incident.get("nombre_personnes_affectees", 0)
    if assessment["niveau_incident"] == "eleve" or donnees_sensibles or nombre_personnes > 100 or gravite >= 3:
        return "Violation averee - Risque eleve", assessment
    elif assessment["niveau_incident"] == "moyen" or nombre_personnes > 0 or gravite >= 2:
        return "Violation averee - Risque limite", assessment
    else:
        return "Incident securite - Pas de violation RGPD", assessment

def decider_notification(incident, qualification, assessment=None):
    notifier_cnil = False
    notifier_personnes = False
    raisons_cnil = []
    raisons_personnes = []
    assessment = assessment or {}
    if "Violation averee" in qualification:
        notifier_cnil = True
        raisons_cnil.append("Violation de donnees personnelles averee (Art. 33 RGPD)")
    if incident.get("donnees_sensibles_impliquees"):
        notifier_cnil = True
        notifier_personnes = True
        raisons_cnil.append("Donnees sensibles impliquees")
        raisons_personnes.append("Donnees sensibles exposees - risque eleve")
    if incident.get("nombre_personnes_affectees", 0) > 100:
        notifier_personnes = True
        raisons_personnes.append("Nombre eleve de personnes affectees")
    if assessment.get("niveau_incident") == "eleve":
        notifier_personnes = True
        raisons_personnes.append("Niveau de risque incident eleve pour les droits et libertes")
    if "Confidentialite" in assessment.get("dimensions", []) and not incident.get("donnees_chiffrees", False):
        notifier_cnil = True
        raisons_cnil.append("Atteinte potentielle a la confidentialite")
    if incident.get("donnees_chiffrees", False):
        notifier_cnil = False
        notifier_personnes = False
        raisons_cnil = ["Donnees chiffrees - notification non obligatoire"]
        raisons_personnes = ["Donnees chiffrees - notification non obligatoire"]
    return {
        "notifier_cnil": notifier_cnil,
        "notifier_personnes": notifier_personnes,
        "raisons_cnil": raisons_cnil,
        "raisons_personnes": raisons_personnes,
        "delai_notification": "72 heures depuis la detection" if notifier_cnil else "Non applicable"
    }

def generer_dossier_incident(incident, qualification, notification, assessment=None):
    assessment = assessment or {}
    return {
        "id_incident": incident.get("id_incident", "INC-001"),
        "date_detection": incident.get("date_detection", "Non renseignee"),
        "type_incident": incident.get("type_incident", "Non renseigne"),
        "description": incident.get("description", "Non renseignee"),
        "donnees_affectees": incident.get("donnees_affectees", []),
        "nombre_personnes": incident.get("nombre_personnes_affectees", 0),
        "qualification": qualification,
        "dimensions_violation": assessment.get("dimensions", []),
        "score_incident": assessment.get("score_incident"),
        "niveau_incident": assessment.get("niveau_incident"),
        "notification": notification,
        "statut": "Ouvert",
        "actions_immediates": [
            "Isoler le systeme ou le traitement concerne",
            "Informer le DPO immediatement",
            "Documenter tous les elements de l incident",
            "Evaluer l etendue de la violation",
            "Qualifier les donnees et personnes touchees avant notification"
        ]
    }

def run_q6(incident, context=None):
    if not incident:
        return {
            "incident_declare": False,
            "message": "Aucun incident declare pour ce traitement"
        }
    qualification, assessment = qualifier_incident(incident, context)
    notification = decider_notification(incident, qualification, assessment)
    dossier = generer_dossier_incident(incident, qualification, notification, assessment)
    return {
        "incident_declare": True,
        "qualification": qualification,
        "evaluation_risque_incident": assessment,
        "notification": notification,
        "dossier_incident": dossier,
        "evidence": _build_incident_evidence(context or {}, incident)
    }

def run_agent_b(traitement, agent_a_output, incident=None):
    context = _load_inventory_context(traitement, agent_a_output)
    return {
        "agent": "B - Risk & Incident Manager",
        "q4_risques_aipd": run_q4(traitement, agent_a_output),
        "q6_incidents": run_q6(incident, context),
        "context": {
            "inventory_summary": context.get("summary", {}),
            "identity": context.get("identity", {}),
        }
    }
