from datetime import datetime, timedelta
from database import crud

DROITS_RGPD = {
    "acces": {
        "article": "Art. 15 RGPD",
        "description": "Droit d acces aux donnees personnelles",
        "delai_jours": 30,
        "applicable": True
    },
    "rectification": {
        "article": "Art. 16 RGPD",
        "description": "Droit de rectification des donnees inexactes",
        "delai_jours": 30,
        "applicable": True
    },
    "effacement": {
        "article": "Art. 17 RGPD",
        "description": "Droit a l effacement (droit a l oubli)",
        "delai_jours": 30,
        "applicable": True
    },
    "limitation": {
        "article": "Art. 18 RGPD",
        "description": "Droit a la limitation du traitement",
        "delai_jours": 30,
        "applicable": True
    },
    "portabilite": {
        "article": "Art. 20 RGPD",
        "description": "Droit a la portabilite des donnees",
        "delai_jours": 30,
        "applicable": True
    },
    "opposition": {
        "article": "Art. 21 RGPD",
        "description": "Droit d opposition au traitement",
        "delai_jours": 30,
        "applicable": True
    },
    "decision_automatisee": {
        "article": "Art. 22 RGPD",
        "description": "Droit de ne pas faire l objet d une decision automatisee",
        "delai_jours": 30,
        "applicable": True
    }
}

EXCEPTIONS_LEGALES = {
    "effacement": [
        "Obligation legale de conservation",
        "Exercice de droits en justice",
        "Interet public ou recherche scientifique",
        "Liberte d expression et d information"
    ],
    "portabilite": [
        "Traitement non base sur consentement ou contrat",
        "Traitement necessaire a l execution d une mission d interet public"
    ],
    "opposition": [
        "Motifs legitimes imperieux predominant sur les interets de la personne",
        "Exercice ou defense de droits en justice"
    ],
    "limitation": [
        "Donnees necessaires pour la constatation l exercice ou la defense de droits en justice"
    ]
}

ACTIONS_PAR_DROIT = {
    "acces": [
        "Identifier toutes les donnees de la personne dans QALITAS et GMAO PRO",
        "Preparer une copie complete des donnees dans un format lisible",
        "Inclure les informations sur les finalites les destinataires et les durees",
        "Envoyer la reponse dans le delai de 30 jours"
    ],
    "rectification": [
        "Verifier l identite du demandeur",
        "Identifier les donnees inexactes ou incompletes",
        "Effectuer les corrections dans QALITAS et GMAO PRO",
        "Notifier les tiers destinataires des corrections",
        "Confirmer la rectification au demandeur"
    ],
    "effacement": [
        "Verifier l absence d obligation legale de conservation",
        "Identifier toutes les occurrences des donnees dans les systemes",
        "Supprimer ou anonymiser les donnees concernees",
        "Notifier les sous-traitants et destinataires",
        "Confirmer l effacement au demandeur"
    ],
    "limitation": [
        "Marquer les donnees concernees comme limitees dans le systeme",
        "Suspendre tout traitement actif sur ces donnees",
        "Informer les utilisateurs internes de la limitation",
        "Confirmer la limitation au demandeur"
    ],
    "portabilite": [
        "Extraire les donnees dans un format structure (JSON ou CSV)",
        "Verifier que le format est interoperable et lisible par machine",
        "Transmettre les donnees de maniere securisee",
        "Confirmer la transmission au demandeur"
    ],
    "opposition": [
        "Evaluer l existence de motifs legitimes imperieux",
        "Suspendre le traitement dans l attente de la decision",
        "Informer le DPO et le responsable du traitement",
        "Notifier la decision motivee au demandeur"
    ],
    "decision_automatisee": [
        "Identifier la decision automatisee concernee",
        "Prevoir une intervention humaine dans le processus",
        "Permettre au demandeur d exprimer son point de vue",
        "Reexaminer la decision avec intervention humaine"
    ]
}


MAX_SEARCH_TEXT_CHARS = 4000


def _truncate_text(text, limit=MAX_SEARCH_TEXT_CHARS):
    if text is None:
        return ""
    text = str(text)
    return text if len(text) <= limit else text[:limit]


def _normalize_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        total = 0
        for item in value[:25]:
            part = _normalize_text(item)
            if not part:
                continue
            parts.append(part)
            total += len(part)
            if total >= MAX_SEARCH_TEXT_CHARS:
                break
        return _truncate_text(" ".join(parts)).strip().lower()
    if isinstance(value, dict):
        parts = []
        total = 0
        for key, item in list(value.items())[:25]:
            part = _normalize_text(item)
            if not part:
                continue
            chunk = f"{key} {part}"
            parts.append(chunk)
            total += len(chunk)
            if total >= MAX_SEARCH_TEXT_CHARS:
                break
        return _truncate_text(" ".join(parts)).strip().lower()
    return _truncate_text(value).strip().lower()


def _build_search_terms(demande):
    terms = []

    nom = demande.get("nom_demandeur")
    if nom:
        terms.append(str(nom).strip())

    for item in demande.get("donnees_concernees", []) or []:
        if item:
            terms.append(str(item).strip())

    for key in ["email", "telephone", "cin", "nss", "matricule"]:
        value = demande.get(key)
        if value:
            terms.append(str(value).strip())

    unique = []
    seen = set()
    for term in terms:
        normalized = term.lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(term)
    return unique


def _match_terms(terms, *values):
    matched = []
    for term in terms:
        term_norm = term.lower()
        if any(term_norm in _normalize_text(value) for value in values if value not in (None, "")):
            matched.append(term)
    return matched


def rechercher_donnees_transversales(demande):
    """Q5 - Search saved inventory and file evidence without changing DSAR decision logic."""
    terms = _build_search_terms(demande)
    if not terms:
        return {
            "termes_recherche": [],
            "resume": {
                "inventaire_traitements": 0,
                "inventaire_champs": 0,
                "scans_non_structures": 0,
                "analyses_historiques": 0,
            },
            "matches": {
                "inventory_treatments": [],
                "inventory_fields": [],
                "unstructured_scans": [],
                "treatments_history": [],
            },
        }

    inventory_treatments = crud.get_inventory_treatments(limit=500, lightweight=True)
    inventory_fields = crud.get_inventory_fields(limit=1000, lightweight=True)
    unstructured_scans = crud.get_unstructured_scans(limit=500, lightweight=True)
    # Reuse lightweight summaries here: DSAR search only matches identity fields
    # and does not need the heavy input/output JSON payloads stored per treatment.
    treatments_history = crud.get_treatment_summaries(limit=200)

    matched_inventory_treatments = []
    for item in inventory_treatments:
        matched = _match_terms(
            terms,
            item.get("id_traitement"),
            item.get("nom_traitement"),
            item.get("systeme"),
            item.get("module"),
        )
        if matched:
            matched_inventory_treatments.append({
                "id": item.get("id"),
                "id_traitement": item.get("id_traitement"),
                "nom_traitement": item.get("nom_traitement"),
                "systeme": item.get("systeme"),
                "module": item.get("module"),
                "matched_terms": matched,
            })

    matched_inventory_fields = []
    for item in inventory_fields:
        matched = _match_terms(
            terms,
            item.get("field_name"),
            item.get("origin_module"),
        )
        if matched:
            matched_inventory_fields.append({
                "inventory_treatment_id": item.get("inventory_treatment_id"),
                "field_name": item.get("field_name"),
                "source_kind": item.get("source_kind"),
                "data_type": item.get("data_type"),
                "criticite": item.get("criticite"),
                "matched_terms": matched,
            })

    matched_unstructured_scans = []
    for item in unstructured_scans:
        matched = _match_terms(
            terms,
            item.get("filename"),
            item.get("findings_json"),
            item.get("result_json"),
            item.get("linked_treatment_id"),
            item.get("module"),
        )
        if matched:
            matched_unstructured_scans.append({
                "id": item.get("id"),
                "filename": item.get("filename"),
                "file_type": item.get("file_type"),
                "linked_treatment_id": item.get("linked_treatment_id"),
                "module": item.get("module"),
                "nb_findings": item.get("nb_findings"),
                "criticite_globale": item.get("criticite_globale"),
                "matched_terms": matched,
            })

    matched_treatments_history = []
    for item in treatments_history:
        matched = _match_terms(
            terms,
            item.get("id_traitement"),
            item.get("nom_traitement"),
            item.get("systeme"),
        )
        if matched:
            matched_treatments_history.append({
                "id": item.get("id"),
                "id_traitement": item.get("id_traitement"),
                "nom_traitement": item.get("nom_traitement"),
                "systeme": item.get("systeme"),
                "matched_terms": matched,
            })

    return {
        "termes_recherche": terms,
        "resume": {
            "inventaire_traitements": len(matched_inventory_treatments),
            "inventaire_champs": len(matched_inventory_fields),
            "scans_non_structures": len(matched_unstructured_scans),
            "analyses_historiques": len(matched_treatments_history),
        },
        "matches": {
            "inventory_treatments": matched_inventory_treatments[:50],
            "inventory_fields": matched_inventory_fields[:100],
            "unstructured_scans": matched_unstructured_scans[:50],
            "treatments_history": matched_treatments_history[:50],
        },
    }


def _dedupe_simple(items, key_name):
    unique = []
    seen = set()
    for item in items:
        value = item.get(key_name)
        if value in seen:
            continue
        seen.add(value)
        unique.append(item)
    return unique


def construire_paquet_dsar(demande, qualification, recherche_transversale):
    """Build a usable DSAR package from saved matches without changing decision logic."""
    matches = recherche_transversale.get("matches", {})
    matched_treatments = matches.get("inventory_treatments", [])
    matched_fields = matches.get("inventory_fields", [])
    matched_scans = matches.get("unstructured_scans", [])

    traitements = _dedupe_simple([
        {
            "id_traitement": item.get("id_traitement"),
            "nom_traitement": item.get("nom_traitement"),
            "systeme": item.get("systeme"),
            "module": item.get("module"),
        }
        for item in matched_treatments
    ], "id_traitement")

    champs = _dedupe_simple([
        {
            "field_name": item.get("field_name"),
            "source_kind": item.get("source_kind"),
            "data_type": item.get("data_type"),
            "criticite": item.get("criticite"),
        }
        for item in matched_fields
    ], "field_name")

    fichiers = _dedupe_simple([
        {
            "filename": item.get("filename"),
            "file_type": item.get("file_type"),
            "linked_treatment_id": item.get("linked_treatment_id"),
            "module": item.get("module"),
            "criticite_globale": item.get("criticite_globale"),
            "nb_findings": item.get("nb_findings"),
        }
        for item in matched_scans
    ], "filename")

    type_droit = demande.get("type_droit", "acces")
    qualification_code = qualification[0]
    can_execute = qualification_code == "valide"

    access_payload = {
        "demandeur": demande.get("nom_demandeur"),
        "systeme_concerne": demande.get("systeme_concerne"),
        "termes_recherche": recherche_transversale.get("termes_recherche", []),
        "traitements_trouves": traitements,
        "champs_trouves": champs,
        "fichiers_trouves": fichiers,
        "resume": {
            "nb_traitements": len(traitements),
            "nb_champs": len(champs),
            "nb_fichiers": len(fichiers),
        }
    }

    export_portabilite = {
        "format_recommande": "JSON",
        "contenu": access_payload,
    }

    rectification_targets = [
        {
            "target_type": "field",
            "field_name": item.get("field_name"),
            "source_kind": item.get("source_kind"),
        }
        for item in champs
    ]

    effacement_targets = [
        {
            "target_type": "treatment",
            "id_traitement": item.get("id_traitement"),
            "module": item.get("module"),
        }
        for item in traitements
    ] + [
        {
            "target_type": "file",
            "filename": item.get("filename"),
            "module": item.get("module"),
        }
        for item in fichiers
    ]

    limitation_targets = [
        {
            "id_traitement": item.get("id_traitement"),
            "module": item.get("module"),
        }
        for item in traitements
    ]

    operation_package = {
        "can_execute": can_execute,
        "type_droit": type_droit,
        "access_payload": access_payload if type_droit == "acces" else None,
        "portability_export": export_portabilite if type_droit == "portabilite" else None,
        "rectification_targets": rectification_targets if type_droit == "rectification" else [],
        "erasure_targets": effacement_targets if type_droit == "effacement" else [],
        "restriction_targets": limitation_targets if type_droit == "limitation" else [],
        "opposition_targets": limitation_targets if type_droit == "opposition" else [],
        "automated_decision_targets": traitements if type_droit == "decision_automatisee" else [],
    }

    official_summary = {
        "message": (
            "Aucune execution automatique tant que la demande n est pas valide."
            if not can_execute else
            f"Des donnees reliees au droit '{type_droit}' ont ete retrouvees et preparees pour traitement."
        ),
        "nb_traitements": len(traitements),
        "nb_champs": len(champs),
        "nb_fichiers": len(fichiers),
    }

    return {
        "resume_officiel": official_summary,
        "package_operationnel": operation_package
    }


def qualifier_demande(demande):
    type_droit = demande.get("type_droit", "")
    identite_verifiee = demande.get("identite_verifiee", False)
    demandes_precedentes = demande.get("demandes_precedentes_30j", 0)

    if type_droit not in DROITS_RGPD:
        return "invalide", "Type de droit non reconnu"

    if not identite_verifiee:
        return "en_attente_verification", "Identite du demandeur non verifiee - verification requise avant traitement"

    if demandes_precedentes >= 3:
        return "abusive", "Nombre excessif de demandes dans les 30 derniers jours - demande potentiellement abusive"

    base_legale = demande.get("base_legale_traitement", "")
    if type_droit == "portabilite" and base_legale not in ["consentement", "contrat"]:
        return "non_applicable", "Le droit a la portabilite ne s applique qu aux traitements bases sur le consentement ou le contrat"

    if type_droit == "effacement":
        obligation_legale = demande.get("obligation_legale_conservation", False)
        if obligation_legale:
            return "exception_legale", "Obligation legale de conservation - effacement impossible"

    return "valide", "Demande valide - traitement requis"


def calculer_delais(demande):
    date_reception_str = demande.get("date_reception")
    try:
        date_reception = datetime.strptime(date_reception_str, "%Y-%m-%d")
    except Exception:
        date_reception = datetime.now()

    delai_jours = DROITS_RGPD.get(demande.get("type_droit", "acces"), {}).get("delai_jours", 30)

    date_limite = date_reception + timedelta(days=delai_jours)
    date_alerte_j15 = date_reception + timedelta(days=15)
    date_alerte_j25 = date_reception + timedelta(days=25)
    date_prolongation = date_reception + timedelta(days=60)

    aujourd_hui = datetime.now()
    jours_restants = (date_limite - aujourd_hui).days

    statut_delai = "Dans les delais"
    if jours_restants < 0:
        statut_delai = "DEPASSE - Action urgente requise"
    elif jours_restants <= 5:
        statut_delai = "Critique - moins de 5 jours restants"
    elif jours_restants <= 10:
        statut_delai = "Urgent - moins de 10 jours restants"

    return {
        "date_reception": date_reception.strftime("%Y-%m-%d"),
        "date_limite_30j": date_limite.strftime("%Y-%m-%d"),
        "date_alerte_j15": date_alerte_j15.strftime("%Y-%m-%d"),
        "date_alerte_j25": date_alerte_j25.strftime("%Y-%m-%d"),
        "date_prolongation_max": date_prolongation.strftime("%Y-%m-%d"),
        "jours_restants": jours_restants,
        "statut_delai": statut_delai
    }


def verifier_exceptions(demande):
    type_droit = demande.get("type_droit", "")
    exceptions_applicables = []

    if type_droit in EXCEPTIONS_LEGALES:
        for exception in EXCEPTIONS_LEGALES[type_droit]:
            if type_droit == "effacement" and demande.get("obligation_legale_conservation"):
                exceptions_applicables.append(exception)
                break
            elif type_droit == "portabilite" and demande.get("base_legale_traitement") not in ["consentement", "contrat"]:
                exceptions_applicables.append(exception)
                break

    return exceptions_applicables


def generer_reponse(demande, qualification, delais):
    type_droit = demande.get("type_droit", "acces")
    droit_info = DROITS_RGPD.get(type_droit, {})
    actions = ACTIONS_PAR_DROIT.get(type_droit, [])
    exceptions = verifier_exceptions(demande)

    if qualification[0] in ["invalide", "abusive"]:
        statut_reponse = "Refus motive"
        message_reponse = "Demande refusee : " + qualification[1]
    elif qualification[0] == "non_applicable":
        statut_reponse = "Non applicable"
        message_reponse = qualification[1]
    elif qualification[0] == "exception_legale":
        statut_reponse = "Exception legale"
        message_reponse = "L exercice de ce droit est limite par une exception legale : " + qualification[1]
    elif qualification[0] == "en_attente_verification":
        statut_reponse = "En attente"
        message_reponse = qualification[1]
    else:
        statut_reponse = "A traiter"
        message_reponse = "Demande valide - actions a entreprendre dans le delai imparti"

    return {
        "statut_reponse": statut_reponse,
        "message_reponse": message_reponse,
        "droit_exerce": droit_info.get("description", ""),
        "article_applicable": droit_info.get("article", ""),
        "actions_a_entreprendre": actions if qualification[0] == "valide" else [],
        "exceptions_legales": exceptions,
        "delais": delais
    }


def generer_dossier_dsar(demande, qualification, reponse):
    return {
        "id_demande": demande.get("id_demande", "DSAR-001"),
        "date_reception": demande.get("date_reception", ""),
        "demandeur": demande.get("nom_demandeur", "Anonyme"),
        "type_droit": demande.get("type_droit", ""),
        "systeme_concerne": demande.get("systeme_concerne", ""),
        "donnees_concernees": demande.get("donnees_concernees", []),
        "qualification": qualification[0],
        "motif_qualification": qualification[1],
        "statut": reponse["statut_reponse"],
        "reponse": reponse,
        "preuve_rgpd": {
            "date_enregistrement": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reference": demande.get("id_demande", "DSAR-001"),
            "opposable": True
        }
    }


def predire_intention_dsar(demande):
    """Advisory ML/NLP reading. Agent C rules remain authoritative."""
    texte_parts = [
        demande.get("type_droit"),
        demande.get("description_demande"),
        demande.get("message"),
        demande.get("texte"),
        demande.get("resume"),
        demande.get("nom_demandeur"),
        demande.get("systeme_concerne"),
        " ".join(demande.get("donnees_concernees") or []),
    ]
    texte = " ".join(str(part) for part in texte_parts if part)
    try:
        from ml.dsar_classifier import predict_dsar_intent
        prediction = predict_dsar_intent(texte)
    except Exception as exc:
        prediction = {
            "label": demande.get("type_droit", "acces") or "acces",
            "label_display": "Lecture ML indisponible",
            "confidence": 0.0,
            "source": "ml_unavailable",
            "reason": str(exc),
            "alternatives": [],
        }
    prediction["rule_based_type"] = demande.get("type_droit", "")
    prediction["authoritative_decision"] = "rule_based_agent_c"
    return prediction


def run_agent_c(demande):
    prediction_ml = predire_intention_dsar(demande)
    qualification = qualifier_demande(demande)
    delais = calculer_delais(demande)
    reponse = generer_reponse(demande, qualification, delais)
    dossier = generer_dossier_dsar(demande, qualification, reponse)
    recherche_transversale = rechercher_donnees_transversales(demande)
    paquet_dsar = construire_paquet_dsar(demande, qualification, recherche_transversale)

    return {
        "agent": "C - Gestion des Droits DSAR",
        "q5_droits": {
            "qualification": qualification[0],
            "motif": qualification[1],
            "reponse": reponse,
            "dossier_dsar": dossier,
            "delais": delais,
            "recherche_transversale": recherche_transversale,
            "paquet_dsar": paquet_dsar,
            "prediction_ml": prediction_ml
        }
    }
