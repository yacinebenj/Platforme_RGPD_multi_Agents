from rules.knowledge_base import REGLES

def evaluer_conformite(traitement):
    violations = []
    score_total = 0
    for regle in REGLES:
        resultat = regle.verifier(traitement)
        if resultat:
            violations.append({
                "id_regle": regle.id,
                "source": regle.source,
                "article": regle.article,
                "message": resultat,
                "gravite": regle.gravite
            })
            score_total += regle.gravite
    return violations, score_total
