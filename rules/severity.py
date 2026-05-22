from rules.knowledge_base import REGLES

# Calcul dynamique du score maximum theorique
# Se met a jour automatiquement quand on ajoute des regles
MAX_THEORIQUE = sum(r.gravite for r in REGLES)

def calculer_score_brut(violations):
    return sum(v["gravite"] for v in violations)

def normaliser_score(score_brut, max_theorique=None):
    if max_theorique is None:
        max_theorique = MAX_THEORIQUE
    if max_theorique == 0:
        return 0
    return min(int((score_brut / max_theorique) * 100), 100)

def determiner_niveau_risque(score_normalise):
    if score_normalise >= 40:
        return "Critique"
    elif score_normalise >= 25:
        return "Eleve"
    elif score_normalise >= 10:
        return "Moyen"
    else:
        return "Faible"
