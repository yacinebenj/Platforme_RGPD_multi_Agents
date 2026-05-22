import json
from orchestrator.workflow import run_workflow

traitement = {
    "id_traitement": "TRT-002",
    "nom_traitement": "Gestion audits QALITAS",
    "systeme": "QALITAS",
    "responsable": "Responsable QHSE",
    "donnees_collectees": ["nom", "email", "donnees_sante"],
    "categories_donnees": ["identite", "sante"],
    "donnees_sensibles": True,
    "finalite": "",
    "finalite_definie": False,
    "base_legale": False,
    "consentement_valide": False,
    "consentement_retire": False,
    "type_relation": None,
    "personnes_concernees": ["employes"],
    "destinataires": ["prestataire_externe"],
    "transfert_etranger": True,
    "duree_conservation": "Non definie",
    "duree_conservation_definie": False,
    "duree_depassee": True,
    "donnees_minimisees": False,
    "respect_vie_privee": False,
    "garanties_specifiques": False,
    "mesures_securite": [],
    "privacy_by_design": False,
    "processus_droits_personnes": False,
    "violation_donnees": True,
    "notification_72h": False,
    "notification_personnes": False,
    "violation_documentee": False,
    "risque_eleve": True,
    "aipd_realisee": False,
    "mise_en_production": True,
    "analyse_risque_avant_production": False
}

incident = {
    "id_incident": "INC-001",
    "date_detection": "2026-02-23",
    "type_incident": "acces_non_autorise",
    "description": "Acces non autorise aux donnees employes",
    "donnees_affectees": ["nom", "email", "donnees_sante"],
    "nombre_personnes_affectees": 150,
    "gravite_incident": 3,
    "donnees_sensibles_impliquees": True,
    "donnees_chiffrees": False
}

demande_dsar = {
    "id_demande": "DSAR-001",
    "nom_demandeur": "Mohamed Ben Ali",
    "date_reception": "2026-02-20",
    "type_droit": "effacement",
    "systeme_concerne": "QALITAS",
    "donnees_concernees": ["nom", "email", "donnees_sante"],
    "identite_verifiee": True,
    "demandes_precedentes_30j": 0,
    "base_legale_traitement": "obligation_legale",
    "obligation_legale_conservation": False
}

if __name__ == "__main__":
    print("Running RGPD workflow...")
    result = run_workflow(traitement, incident, demande_dsar)

    print("\n######## STATUT WORKFLOW ########")
    print("Statut:", result["statut"])
    print("Erreurs:", result["erreurs"])

    print("\n######## AGENT A - SCORE CONFORMITE ########")
    print("Score:", result["agent_a"]["q2_conformite"]["score_normalise"], "%")
    print("Niveau:", result["agent_a"]["q2_conformite"]["niveau_risque"])

    print("\n######## AGENT B - RISQUES ########")
    print("Nombre risques:", result["agent_b"]["q4_risques_aipd"]["nombre_risques"])
    print("Risques critiques:", result["agent_b"]["q4_risques_aipd"]["risques_critiques"])
    print("Notification CNIL:", result["agent_b"]["q6_incidents"]["notification"]["notifier_cnil"])

    print("\n######## AGENT C - DSAR ########")
    print("Qualification:", result["agent_c"]["q5_droits"]["qualification"])
    print("Jours restants:", result["agent_c"]["q5_droits"]["delais"]["jours_restants"])

    print("\n######## AGENT D - SYNTHESE FINALE ########")
    print(json.dumps(result["agent_d"]["synthese"], indent=2, ensure_ascii=False))
    print("\n######## RAPPORT DPO GENERE PAR GROQ ########")
    print(result["agent_d"].get("rapport_dpo", "Non disponible"))
