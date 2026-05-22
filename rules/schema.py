# rules/schema.py

REQUIRED_FIELDS = {
    "base_legale": (str, bool),
    "finalite_definie": bool,
    "donnees_minimisees": bool,
    "duree_conservation_definie": bool,
    "duree_depassee": bool,
    "respect_vie_privee": bool,
    "consentement_valide": bool,
    "donnees_sensibles": bool,
    "garanties_specifiques": bool,
    "processus_droits_personnes": bool,
    "mesures_securite": (bool, list),
    "violation_donnees": bool,
    "notification_72h": bool,
    "notification_personnes": bool,
    "violation_documentee": bool,
    "risque_eleve": bool,
    "aipd_realisee": bool,
    "mise_en_production": bool,
    "analyse_risque_avant_production": bool,
    "privacy_by_design": bool
}

ALLOWED_BASES_LEGALES = [
    "consentement",
    "contrat",
    "obligation_legale",
    "interet_legitime",
    "mission_publique",
    None,
    False
]

def valider_schema(traitement: dict):
    if not isinstance(traitement, dict):
        raise TypeError("Le traitement doit etre un dictionnaire.")
    champs_manquants = [c for c in REQUIRED_FIELDS if c not in traitement]
    if champs_manquants:
        raise ValueError(f"Champs manquants : {champs_manquants}")
    for champ, type_attendu in REQUIRED_FIELDS.items():
        valeur = traitement[champ]
        if not isinstance(valeur, type_attendu):
            raise TypeError(f"Type invalide pour '{champ}'.")
    if traitement["base_legale"] not in ALLOWED_BASES_LEGALES:
        raise ValueError("Base legale invalide.")
    return True
