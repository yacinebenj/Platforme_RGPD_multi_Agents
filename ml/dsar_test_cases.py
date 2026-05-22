"""Seed DSAR examples used before enough DPO validation history exists.

These are intentionally simple, auditable training examples. They let the
classifier start useful, then DPO decisions can enrich the dataset over time.
"""

SEED_DSAR_CASES = [
    {
        "text": "Bonjour, je souhaite recevoir une copie de toutes les donnees que vous avez sur moi.",
        "label": "acces",
    },
    {
        "text": "Pouvez-vous me transmettre mes informations personnelles stockees dans QALITAS ?",
        "label": "acces",
    },
    {
        "text": "Je veux savoir quelles donnees personnelles sont conservees a mon sujet.",
        "label": "acces",
    },
    {
        "text": "Merci de me fournir l'historique de mes donnees et les destinataires associes.",
        "label": "acces",
    },
    {
        "text": "Mon numero de telephone est faux, merci de le corriger dans votre systeme.",
        "label": "rectification",
    },
    {
        "text": "Je demande la rectification de mon adresse email et de mon adresse postale.",
        "label": "rectification",
    },
    {
        "text": "Veuillez modifier mon nom mal orthographie dans votre base.",
        "label": "rectification",
    },
    {
        "text": "Je souhaite mettre a jour mes informations de contact.",
        "label": "rectification",
    },
    {
        "text": "Je veux que vous supprimiez toutes mes donnees personnelles.",
        "label": "effacement",
    },
    {
        "text": "Merci d'effacer mon compte et les informations liees.",
        "label": "effacement",
    },
    {
        "text": "J'exerce mon droit a l'oubli et demande la suppression de mes donnees.",
        "label": "effacement",
    },
    {
        "text": "Retirez mes informations de vos fichiers et archives non obligatoires.",
        "label": "effacement",
    },
    {
        "text": "Je demande de limiter temporairement le traitement de mes donnees.",
        "label": "limitation",
    },
    {
        "text": "Veuillez bloquer l'utilisation de mes informations le temps de verifier leur exactitude.",
        "label": "limitation",
    },
    {
        "text": "Je souhaite suspendre le traitement de mes donnees personnelles.",
        "label": "limitation",
    },
    {
        "text": "Merci de geler mes donnees et de ne plus les utiliser provisoirement.",
        "label": "limitation",
    },
    {
        "text": "Je veux recuperer mes donnees dans un format portable.",
        "label": "portabilite",
    },
    {
        "text": "Merci de m'envoyer un export CSV ou JSON de mes informations.",
        "label": "portabilite",
    },
    {
        "text": "Je demande la portabilite de mes donnees vers un autre prestataire.",
        "label": "portabilite",
    },
    {
        "text": "Pouvez-vous exporter mes donnees dans un format structure et lisible par machine ?",
        "label": "portabilite",
    },
    {
        "text": "Je m'oppose au traitement de mes donnees personnelles.",
        "label": "opposition",
    },
    {
        "text": "Je ne veux plus que mes informations soient utilisees pour ce traitement.",
        "label": "opposition",
    },
    {
        "text": "Arretez d'utiliser mes donnees pour vos communications.",
        "label": "opposition",
    },
    {
        "text": "Je refuse que mes donnees soient traitees a cette fin.",
        "label": "opposition",
    },
    {
        "text": "Je conteste une decision automatisee prise a partir de mes donnees.",
        "label": "decision_automatisee",
    },
    {
        "text": "Je veux une intervention humaine concernant le profilage automatique.",
        "label": "decision_automatisee",
    },
    {
        "text": "Expliquez-moi la decision automatique et l'algorithme utilise.",
        "label": "decision_automatisee",
    },
    {
        "text": "Je ne veux pas faire l'objet d'une decision uniquement automatisee.",
        "label": "decision_automatisee",
    },
]

