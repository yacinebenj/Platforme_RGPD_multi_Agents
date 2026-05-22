# rules/test_rules.py

from rules.schema import valider_schema
from rules.rule_engine import evaluer_conformite
from rules.severity import (
    calculer_score_brut,
    normaliser_score,
    determiner_niveau_risque
)


def scenario_non_conforme():
    """
    Cas fortement non conforme :
    - Pas de base légale
    - Données sensibles sans garanties
    - Violation non notifiée
    - AIPD non réalisée
    """

    return {
        "base_legale": False,
        "finalite_definie": False,
        "donnees_minimisees": False,
        "duree_conservation_definie": False,
        "duree_depassee": True,
        "respect_vie_privee": False,
        "consentement_valide": False,
        "donnees_sensibles": True,
        "garanties_specifiques": False,
        "processus_droits_personnes": False,
        "mesures_securite": False,
        "violation_donnees": True,
        "notification_72h": False,
        "notification_personnes": False,
        "violation_documentee": False,
        "risque_eleve": True,
        "aipd_realisee": False,
        "mise_en_production": True,
        "analyse_risque_avant_production": False,
        "privacy_by_design": False
    }


def scenario_partiellement_conforme():
    """
    Cas intermédiaire :
    - Base légale valide
    - Quelques défauts mineurs
    """

    return {
        "base_legale": "contrat",
        "finalite_definie": True,
        "donnees_minimisees": True,
        "duree_conservation_definie": True,
        "duree_depassee": False,
        "respect_vie_privee": True,
        "consentement_valide": True,
        "donnees_sensibles": False,
        "garanties_specifiques": True,
        "processus_droits_personnes": False,
        "mesures_securite": True,
        "violation_donnees": False,
        "notification_72h": False,
        "notification_personnes": False,
        "violation_documentee": True,
        "risque_eleve": False,
        "aipd_realisee": True,
        "mise_en_production": True,
        "analyse_risque_avant_production": True,
        "privacy_by_design": True
    }


def executer_test(traitement):
    print("\n==============================")
    print("VALIDATION DU SCHÉMA")
    print("==============================")

    valider_schema(traitement)
    print("Schéma valide.")

    print("\n==============================")
    print("ÉVALUATION DES RÈGLES")
    print("==============================")

    violations, _ = evaluer_conformite(traitement)

    for v in violations:
        print(f"{v['id_regle']} - {v['message']} (Gravité: {v['gravite']})")

    print("\n==============================")
    print("CALCUL DU RISQUE")
    print("==============================")

    score_brut = calculer_score_brut(violations)
    score_normalise = normaliser_score(score_brut)
    niveau = determiner_niveau_risque(score_normalise)

    print(f"Score brut : {score_brut}")
    print(f"Score normalisé : {score_normalise}")
    print(f"Niveau de risque : {niveau}")


if __name__ == "__main__":
    print("\n######## SCÉNARIO NON CONFORME ########")
    executer_test(scenario_non_conforme())

    print("\n\n######## SCÉNARIO PARTIELLEMENT CONFORME ########")
    executer_test(scenario_partiellement_conforme())