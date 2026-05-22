"""Seed questions for the DPO assistant intent classifier.

The assistant can accept free text. These labeled examples teach the NLP layer
which platform context should be retrieved before Groq writes the final answer.
"""

ASSISTANT_INTENT_LABELS = {
    "compliance_summary": "Synthese conformite",
    "risk_summary": "Synthese risques",
    "incident_summary": "Synthese incidents",
    "cnil_notification": "Notification CNIL",
    "dsar_status": "Statut DSAR",
    "aipd_status": "Statut AIPD",
    "legal_basis_status": "Bases legales",
    "consent_status": "Consentements",
    "proof_validation": "Preuves et validations",
    "corrective_actions": "Actions correctives",
    "governance_summary": "Gouvernance DPO",
    "report_summary": "Rapports DPO",
    "platform_navigation": "Navigation plateforme",
    "out_of_scope": "Hors perimetre plateforme",
}


SEED_ASSISTANT_INTENT_CASES = [
    # Compliance
    {"text": "Resume la conformite de la plateforme", "label": "compliance_summary"},
    {"text": "Quels traitements ne sont pas conformes ?", "label": "compliance_summary"},
    {"text": "Donne moi les ecarts RGPD principaux", "label": "compliance_summary"},
    {"text": "Pourquoi QALITAS a des violations ?", "label": "compliance_summary"},
    {"text": "Quel est le score de conformite GMAO ?", "label": "compliance_summary"},
    {"text": "Liste les traitements avec violations", "label": "compliance_summary"},
    {"text": "Explique les non conformites detectees", "label": "compliance_summary"},
    {"text": "Quels modules ont un probleme de minimisation ?", "label": "compliance_summary"},
    {"text": "How can I improve personal data handling in my software?", "label": "compliance_summary"},
    {"text": "Give me platform advice to improve personal data compliance", "label": "compliance_summary"},

    # Risks
    {"text": "Quels traitements sont les plus risques ?", "label": "risk_summary"},
    {"text": "Resume les risques critiques", "label": "risk_summary"},
    {"text": "Quels risques GMAO sont ouverts ?", "label": "risk_summary"},
    {"text": "Explique le niveau de risque du traitement RH", "label": "risk_summary"},
    {"text": "Quels sont les risques eleves de QALITAS ?", "label": "risk_summary"},
    {"text": "Donne moi la matrice des risques", "label": "risk_summary"},
    {"text": "Quels traitements presentent un risque pour les droits et libertes ?", "label": "risk_summary"},
    {"text": "Priorise les risques RGPD", "label": "risk_summary"},

    # Incidents
    {"text": "Quels incidents sont ouverts ?", "label": "incident_summary"},
    {"text": "Resume les incidents RGPD", "label": "incident_summary"},
    {"text": "Liste les violations de donnees personnelles", "label": "incident_summary"},
    {"text": "Quels incidents concernent QALITAS ?", "label": "incident_summary"},
    {"text": "Explique l incident INC-001", "label": "incident_summary"},
    {"text": "Quels incidents touchent des donnees sensibles ?", "label": "incident_summary"},

    # CNIL
    {"text": "Quels incidents doivent etre notifies a la CNIL ?", "label": "cnil_notification"},
    {"text": "Est ce que l incident INC-001 doit etre notifie ?", "label": "cnil_notification"},
    {"text": "Montre les dossiers CNIL generes", "label": "cnil_notification"},
    {"text": "Quels dossiers de notification sont disponibles ?", "label": "cnil_notification"},
    {"text": "Explique la regle des 72 heures CNIL", "label": "cnil_notification"},
    {"text": "Doit on informer les personnes concernees ?", "label": "cnil_notification"},

    # DSAR
    {"text": "Quelles demandes DSAR sont en retard ?", "label": "dsar_status"},
    {"text": "Resume les droits des personnes", "label": "dsar_status"},
    {"text": "Combien de demandes d acces sont ouvertes ?", "label": "dsar_status"},
    {"text": "Liste les demandes d effacement", "label": "dsar_status"},
    {"text": "Quelles demandes doivent etre traitees en priorite ?", "label": "dsar_status"},
    {"text": "Explique la derniere demande de droit", "label": "dsar_status"},

    # AIPD
    {"text": "Quels traitements necessitent une AIPD ?", "label": "aipd_status"},
    {"text": "Resume les dossiers d impact", "label": "aipd_status"},
    {"text": "Quels DPIA sont ouverts ?", "label": "aipd_status"},
    {"text": "Explique pourquoi une AIPD est requise", "label": "aipd_status"},
    {"text": "Montre les traitements avec risque eleve et AIPD", "label": "aipd_status"},
    {"text": "Quels dossiers d impact doivent etre valides ?", "label": "aipd_status"},

    # Legal basis
    {"text": "Quels traitements n ont pas de base legale ?", "label": "legal_basis_status"},
    {"text": "Resume les bases legales", "label": "legal_basis_status"},
    {"text": "Quelle base legale pour les donnees RH ?", "label": "legal_basis_status"},
    {"text": "Quels consentements sont utilises a tort ?", "label": "legal_basis_status"},
    {"text": "Explique la base legale proposee", "label": "legal_basis_status"},
    {"text": "Quels traitements doivent etre valides par le DPO ?", "label": "legal_basis_status"},

    # Consent
    {"text": "Quels consentements sont actifs ?", "label": "consent_status"},
    {"text": "Quels consentements ont ete retires ?", "label": "consent_status"},
    {"text": "Liste les consentements expires", "label": "consent_status"},
    {"text": "Resume la gestion des consentements", "label": "consent_status"},
    {"text": "Quels traitements reposent sur le consentement ?", "label": "consent_status"},

    # Proofs and validations
    {"text": "Quelles preuves restent a valider ?", "label": "proof_validation"},
    {"text": "Resume les validations DPO", "label": "proof_validation"},
    {"text": "Quels traitements attendent une validation ?", "label": "proof_validation"},
    {"text": "Montre l historique des preuves", "label": "proof_validation"},
    {"text": "Qui a valide la derniere base legale ?", "label": "proof_validation"},
    {"text": "Quelles preuves sont opposables ?", "label": "proof_validation"},
    {"text": "Pour le dernier traitement, quel document dois je fournir pour le corriger ?", "label": "proof_validation"},
    {"text": "Quelle preuve dois je uploader pour le dernier traitement ?", "label": "proof_validation"},
    {"text": "Quel justificatif faut il deposer pour ce traitement ?", "label": "proof_validation"},
    {"text": "Comment corriger un traitement avec une preuve ou un document ?", "label": "proof_validation"},

    # Corrective actions
    {"text": "Quelles actions correctives sont prioritaires ?", "label": "corrective_actions"},
    {"text": "Liste les actions ouvertes", "label": "corrective_actions"},
    {"text": "Quels responsables ont des actions en retard ?", "label": "corrective_actions"},
    {"text": "Resume le plan d action RGPD", "label": "corrective_actions"},
    {"text": "Quelles actions sont en attente de preuve ?", "label": "corrective_actions"},

    # Governance
    {"text": "Prepare un resume pour la reunion avec Mme Nahla", "label": "governance_summary"},
    {"text": "Donne moi une synthese DPO", "label": "governance_summary"},
    {"text": "Quel est le niveau de maturite RGPD ?", "label": "governance_summary"},
    {"text": "Resume le cockpit DPO", "label": "governance_summary"},
    {"text": "Quels sont les points critiques pour la direction ?", "label": "governance_summary"},
    {"text": "Donne moi les priorites pour le suivi hebdomadaire", "label": "governance_summary"},
    {"text": "Prepare a short follow up summary for the supervisor", "label": "governance_summary"},

    # Reports
    {"text": "Resume le dernier rapport DPO", "label": "report_summary"},
    {"text": "Quel rapport est affiche dans gouvernance ?", "label": "report_summary"},
    {"text": "Montre les derniers snapshots de gouvernance", "label": "report_summary"},
    {"text": "Explique le rapport DPO QALITAS", "label": "report_summary"},
    {"text": "Prepare un paragraphe de rapport pour le superviseur", "label": "report_summary"},

    # Navigation
    {"text": "Ou trouver le dossier CNIL ?", "label": "platform_navigation"},
    {"text": "Comment acceder aux validations DPO ?", "label": "platform_navigation"},
    {"text": "Ou sont les registres ?", "label": "platform_navigation"},
    {"text": "Comment lancer une analyse QALITAS ?", "label": "platform_navigation"},
    {"text": "Ou puis je voir les demandes DSAR ?", "label": "platform_navigation"},
    {"text": "Ou trouver les dossiers d impact ?", "label": "platform_navigation"},
    {"text": "Where can I find the CNIL dossier in the platform?", "label": "platform_navigation"},
    {"text": "That was in the platform", "label": "platform_navigation"},

    # Out of scope
    {"text": "Quel est le prix du bitcoin ?", "label": "out_of_scope"},
    {"text": "Qui a gagne le match hier ?", "label": "out_of_scope"},
    {"text": "Raconte moi une blague", "label": "out_of_scope"},
    {"text": "Ecris une recette de couscous", "label": "out_of_scope"},
    {"text": "Quelle est la meteo demain ?", "label": "out_of_scope"},
    {"text": "Fais moi un plan de voyage", "label": "out_of_scope"},
    {"text": "Explique la guerre en Ukraine", "label": "out_of_scope"},
    {"text": "Donne moi des conseils pour investir", "label": "out_of_scope"},
]
