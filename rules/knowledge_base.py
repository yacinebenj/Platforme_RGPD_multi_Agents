# rules/knowledge_base.py
# Version 2.0 - Expanded rule set based on:
# - RGPD (UE) 2016/679
# - Loi organique tunisienne n° 2004-63 du 27 juillet 2004

class Regle:
    def __init__(self, id_regle, source, article, description, gravite, fonction_verification):
        self.id = id_regle
        self.source = source
        self.article = article
        self.description = description
        self.gravite = gravite  # 1 = faible, 2 = moyenne, 3 = elevee
        self.verifier = fonction_verification


# ==============================================================
# A. PRINCIPES GENERAUX (Art.5 RGPD / Art.9-11 Loi 2004-63)
# ==============================================================

def rgpd_01(traitement):
    if not traitement.get("base_legale"):
        return "Traitement non licite : aucune base legale definie (Art.5 RGPD)"
    return None

def rgpd_02(traitement):
    if not traitement.get("finalite_definie"):
        return "Finalite non definie ou non explicite (Art.5 RGPD)"
    return None

def rgpd_03(traitement):
    if not traitement.get("donnees_minimisees"):
        return "Principe de minimisation non respecte : donnees excessives (Art.5 RGPD)"
    return None

def rgpd_04(traitement):
    if not traitement.get("duree_conservation_definie") or traitement.get("duree_depassee"):
        return "Violation de la limitation de conservation : duree non definie ou depassee (Art.5 RGPD)"
    return None

def rgpd_04b(traitement):
    if not traitement.get("donnees_exactes", True):
        return "Donnees inexactes ou non mises a jour (Art.5(1)(d) RGPD)"
    return None

def rgpd_04c(traitement):
    if not traitement.get("mesures_securite") and traitement.get("donnees_sensibles"):
        return "Integrite et confidentialite non garanties pour donnees sensibles (Art.5(1)(f) RGPD)"
    return None

def rgpd_04d(traitement):
    if not traitement.get("responsable"):
        return "Responsable du traitement non designe (Art.5(2) RGPD)"
    return None

def loi_tn_01(traitement):
    if not traitement.get("respect_vie_privee", True):
        return "Atteinte a la dignite ou a la vie privee de la personne concernee (Art.1 et Art.9 Loi 2004-63)"
    return None

def loi_tn_02(traitement):
    if not traitement.get("donnees_minimisees"):
        return "Donnees excessives au regard des finalites du traitement (Art.11 Loi 2004-63)"
    return None

def loi_tn_02b(traitement):
    if not traitement.get("finalite_definie"):
        return "Finalite du traitement non licite, non determinee ou non explicite (Art.10 Loi 2004-63)"
    return None

def loi_tn_02c(traitement):
    if traitement.get("donnees_sensibles") and not traitement.get("declaration_inpdp", True):
        return "Traitement non declare a l Instance Nationale de Protection des Donnees (Art.7 Loi 2004-63)"
    return None


# ==============================================================
# B. BASES LEGALES (Art.6-9 RGPD / Art.13-15 Loi 2004-63)
# ==============================================================

def rgpd_05(traitement):
    if not traitement.get("base_legale"):
        return "Absence de base legale valide parmi : consentement, contrat, obligation legale, interet vital, mission publique, interet legitime (Art.6 RGPD)"
    return None

def rgpd_06(traitement):
    if traitement.get("base_legale") == "consentement" and not traitement.get("consentement_valide"):
        return "Consentement invalide : doit etre libre, specifique, eclaire et univoque (Art.7 RGPD)"
    return None

def rgpd_06b(traitement):
    if traitement.get("consentement_retire") and traitement.get("base_legale") == "consentement":
        return "Traitement poursuivi malgre le retrait du consentement (Art.7(3) RGPD)"
    return None

def rgpd_06c(traitement):
    if traitement.get("consentement_conditionne_service"):
        return "Consentement conditionne a la prestation d un service : pratique interdite (Art.7(4) RGPD)"
    return None

def rgpd_07(traitement):
    if traitement.get("donnees_sensibles") and not traitement.get("garanties_specifiques"):
        return "Traitement de donnees sensibles sans garanties specifiques (Art.9 RGPD)"
    return None

def rgpd_07b(traitement):
    if traitement.get("donnees_penales") and not traitement.get("autorisation_donnees_penales"):
        return "Traitement de donnees relatives aux condamnations penales sans autorisation (Art.10 RGPD)"
    return None

def loi_tn_03(traitement):
    if not traitement.get("finalite_definie"):
        return "Finalite non legitime ou non explicite (Art.10 Loi 2004-63)"
    return None

def loi_tn_03b(traitement):
    if traitement.get("donnees_sensibles") and not traitement.get("consentement_valide") and not traitement.get("garanties_specifiques"):
        return "Traitement de donnees sensibles sans consentement expres ni autorisation INPDP (Art.14 Loi 2004-63)"
    return None

def loi_tn_03c(traitement):
    if traitement.get("service_conditionne_consentement"):
        return "Service conditionne a l acceptation du traitement : pratique expressement interdite (Art.17 Loi 2004-63)"
    return None


# ==============================================================
# C. TRANSPARENCE ET INFORMATION (Art.12-14 RGPD / Art.27-28 Loi 2004-63)
# ==============================================================

def rgpd_08(traitement):
    if not traitement.get("information_personnes_concernees"):
        return "Personnes concernees non informees lors de la collecte : identite responsable, finalites, base legale, duree conservation, droits (Art.13 RGPD)"
    return None

def rgpd_08b(traitement):
    if traitement.get("collecte_indirecte") and not traitement.get("information_collecte_indirecte"):
        return "Personnes concernees non informees lors de collecte indirecte des donnees (Art.14 RGPD)"
    return None

def rgpd_08c(traitement):
    if traitement.get("transfert_etranger") and not traitement.get("information_transfert_fournie"):
        return "Personnes non informees des transferts internationaux et des garanties associees (Art.13(1)(f) RGPD)"
    return None

def rgpd_08d(traitement):
    if not traitement.get("modalites_droits_accessibles"):
        return "Modalites d exercice des droits non accessibles : doit etre concis, transparent, comprehensible (Art.12 RGPD)"
    return None

def loi_tn_04b(traitement):
    if not traitement.get("information_personnes_concernees"):
        return "Personne concernee non informee de l identite du responsable, finalites et droits (Art.27 Loi 2004-63)"
    return None

def loi_tn_04c(traitement):
    if traitement.get("collecte_indirecte") and not traitement.get("consentement_collecte_indirecte"):
        return "Collecte indirecte sans consentement de la personne concernee (Art.44 Loi 2004-63)"
    return None


# ==============================================================
# D. DROITS DES PERSONNES (Art.15-22 RGPD / Art.35-43 Loi 2004-63)
# ==============================================================

def rgpd_09(traitement):
    if not traitement.get("processus_droits_personnes"):
        return "Absence de processus de gestion des droits des personnes (Art.15-20 RGPD)"
    return None

def rgpd_09b(traitement):
    if traitement.get("opposition_ignoree"):
        return "Opposition de la personne concernee ignoree sans motif legitime imperieux (Art.21 RGPD)"
    return None

def rgpd_09c(traitement):
    if traitement.get("decision_automatisee") and not traitement.get("garanties_decision_auto"):
        return "Prise de decision entierement automatisee sans garanties ni intervention humaine (Art.22 RGPD)"
    return None

def rgpd_09d(traitement):
    if traitement.get("dsar_hors_delai"):
        return "Delai de reponse aux demandes DSAR depasse (30 jours maximum) (Art.12(3) RGPD)"
    return None

def loi_tn_04(traitement):
    if not traitement.get("processus_droits_personnes"):
        return "Demandes d exercice des droits non tracees ou non traitees (Art.35-40 Loi 2004-63)"
    return None

def loi_tn_04d(traitement):
    if traitement.get("opposition_ignoree"):
        return "Opposition au traitement ignoree : l opposition suspend immediatement le traitement (Art.42 Loi 2004-63)"
    return None

def loi_tn_04e(traitement):
    if not traitement.get("donnees_exactes", True):
        return "Donnees inexactes non corrigees dans les delais (Art.21 Loi 2004-63)"
    return None


# ==============================================================
# E. SECURITE ET VIOLATIONS (Art.32-34 RGPD / Art.18-19 Loi 2004-63)
# ==============================================================

def rgpd_13(traitement):
    if not traitement.get("mesures_securite"):
        return "Mesures de securite techniques et organisationnelles insuffisantes (Art.32 RGPD)"
    return None

def rgpd_13b(traitement):
    if traitement.get("donnees_sensibles") and not traitement.get("chiffrement_actif"):
        return "Donnees sensibles non chiffrees ni pseudonymisees (Art.32(1)(a) RGPD)"
    return None

def rgpd_13c(traitement):
    if not traitement.get("tests_securite_reguliers"):
        return "Absence de tests et evaluations reguliers des mesures de securite (Art.32(1)(d) RGPD)"
    return None

def rgpd_14(traitement):
    if traitement.get("violation_donnees") and not traitement.get("notification_72h"):
        return "Violation de donnees non notifiee a l autorite de controle dans les 72 heures (Art.33 RGPD)"
    return None

def rgpd_14b(traitement):
    if traitement.get("violation_donnees") and not traitement.get("violation_documentee"):
        return "Violation de donnees non documentee (Art.33(5) RGPD)"
    return None

def rgpd_15(traitement):
    if traitement.get("violation_donnees") and traitement.get("risque_eleve") and not traitement.get("notification_personnes"):
        return "Personnes concernees non informees d une violation presentant un risque eleve (Art.34 RGPD)"
    return None

def loi_tn_05(traitement):
    if not traitement.get("mesures_securite"):
        return "Protection insuffisante contre acces, modification ou consultation non autorisee (Art.18 Loi 2004-63)"
    return None

def loi_tn_06(traitement):
    if traitement.get("violation_donnees") and not traitement.get("violation_documentee"):
        return "Violation de donnees non documentee (Art.18 Loi 2004-63)"
    return None

def loi_tn_06b(traitement):
    if not traitement.get("mesures_securite") and not traitement.get("controle_acces_physique"):
        return "Absence de mesures physiques et techniques de securite (Art.19 Loi 2004-63)"
    return None

def loi_tn_06c(traitement):
    if not traitement.get("confidentialite_post_traitement", True):
        return "Confidentialite des donnees non garantie apres la fin du traitement (Art.23 Loi 2004-63)"
    return None


# ==============================================================
# F. AIPD ET PRIVACY BY DESIGN (Art.25 et Art.35-36 RGPD)
# ==============================================================

def rgpd_16(traitement):
    if traitement.get("aipd_obligatoire") and not traitement.get("aipd_realisee"):
        return "AIPD obligatoire non realisee pour traitement a risque eleve (Art.35 RGPD)"
    return None

def rgpd_17(traitement):
    if traitement.get("mise_en_production") and not traitement.get("analyse_risque_avant_production"):
        return "Mise en production sans analyse de risque prealable (Art.35 RGPD)"
    return None

def rgpd_18(traitement):
    if not traitement.get("privacy_by_design"):
        return "Privacy by Design non applique : protection non integree des la conception (Art.25(1) RGPD)"
    return None

def rgpd_18b(traitement):
    if not traitement.get("privacy_by_default"):
        return "Privacy by Default non applique : seules les donnees necessaires doivent etre traitees par defaut (Art.25(2) RGPD)"
    return None

def rgpd_18c(traitement):
    if traitement.get("risque_eleve") and traitement.get("aipd_realisee") and not traitement.get("consultation_autorite_si_risque_residuel", True):
        return "Risque residuel eleve apres AIPD sans consultation prealable de l autorite (Art.36 RGPD)"
    return None


# ==============================================================
# G. SOUS-TRAITANTS ET REGISTRE (Art.28-30 RGPD / Art.20 Loi 2004-63)
# ==============================================================

def rgpd_19(traitement):
    if traitement.get("destinataires") and not traitement.get("contrat_sous_traitance"):
        return "Sous-traitants utilises sans contrat de traitement des donnees conforme Art.28 RGPD (DPA manquant)"
    return None

def rgpd_19b(traitement):
    if not traitement.get("registre_traitement"):
        return "Absence de registre des activites de traitement (Art.30 RGPD)"
    return None

def rgpd_19c(traitement):
    if traitement.get("destinataires") and not traitement.get("garanties_sous_traitant"):
        return "Sous-traitant selectionne sans verification des garanties suffisantes (Art.28(1) RGPD)"
    return None

def loi_tn_07(traitement):
    if traitement.get("destinataires") and not traitement.get("garanties_sous_traitant"):
        return "Sous-traitant non choisi scrupuleusement ou sans moyens techniques necessaires (Art.20 Loi 2004-63)"
    return None

def loi_tn_07b(traitement):
    if traitement.get("destinataires") and not traitement.get("contrat_sous_traitance"):
        return "Sous-traitant sans engagement de confidentialite documente (Art.23 Loi 2004-63)"
    return None


# ==============================================================
# H. TRANSFERTS INTERNATIONAUX (Art.44-49 RGPD / Art.50-52 Loi 2004-63)
# ==============================================================

def rgpd_20(traitement):
    if traitement.get("transfert_etranger") and not traitement.get("garanties_specifiques"):
        return "Transfert international sans garanties appropriees : decision d adequation, CCT ou regles contraignantes requis (Art.44-46 RGPD)"
    return None

def rgpd_20b(traitement):
    if traitement.get("transfert_etranger") and not traitement.get("adequation_ou_garanties_documentees"):
        return "Transfert vers pays tiers sans decision d adequation ni documentation des garanties (Art.45-46 RGPD)"
    return None

def loi_tn_08(traitement):
    if traitement.get("transfert_etranger") and not traitement.get("autorisation_inpdp_transfert"):
        return "Transfert international sans autorisation obligatoire de l INPDP (Art.52 Loi 2004-63)"
    return None

def loi_tn_08b(traitement):
    if traitement.get("transfert_etranger") and traitement.get("risque_securite_nationale"):
        return "Transfert susceptible de porter atteinte a la securite publique ou aux interets vitaux de la Tunisie (Art.50 Loi 2004-63)"
    return None

def loi_tn_08c(traitement):
    if traitement.get("transfert_etranger") and not traitement.get("niveau_protection_adequat"):
        return "Transfert vers pays sans niveau de protection adequat sans garanties compensatoires (Art.51 Loi 2004-63)"
    return None


# ==============================================================
# I. DELEGATION A LA PROTECTION DES DONNEES (Art.37-39 RGPD)
# ==============================================================

def rgpd_21(traitement):
    if traitement.get("traitement_grande_echelle") and not traitement.get("dpo_designe"):
        return "DPO non designe pour traitement a grande echelle de donnees sensibles (Art.37 RGPD)"
    return None

def rgpd_21b(traitement):
    if traitement.get("dpo_designe") and not traitement.get("missions_dpo_garanties"):
        return "Missions du DPO non garanties : ressources insuffisantes ou acces direction non assure (Art.38 RGPD)"
    return None


# ==============================================================
# J. RESPONSABILITE ET POLITIQUES (Art.24 RGPD)
# ==============================================================

def rgpd_22(traitement):
    if not traitement.get("politique_protection_donnees"):
        return "Absence de politique documentee de protection des donnees (Art.24 RGPD)"
    return None

def rgpd_22b(traitement):
    if not traitement.get("revue_periodique_mesures"):
        return "Mesures de conformite non reexaminees ni actualisees periodiquement (Art.24(1) RGPD)"
    return None


# ==============================================================
# REGISTRE DES REGLES
# ==============================================================

REGLES = [
    # A. Principes generaux
    Regle("RGPD-01",    "RGPD",        "Art.5(1)(a)",   "Liceite",                          3, rgpd_01),
    Regle("RGPD-02",    "RGPD",        "Art.5(1)(b)",   "Finalite determinee",              2, rgpd_02),
    Regle("RGPD-03",    "RGPD",        "Art.5(1)(c)",   "Minimisation",                     2, rgpd_03),
    Regle("RGPD-04",    "RGPD",        "Art.5(1)(e)",   "Conservation",                     3, rgpd_04),
    Regle("RGPD-04B",   "RGPD",        "Art.5(1)(d)",   "Exactitude",                       2, rgpd_04b),
    Regle("RGPD-04C",   "RGPD",        "Art.5(1)(f)",   "Integrite et confidentialite",     3, rgpd_04c),
    Regle("RGPD-04D",   "RGPD",        "Art.5(2)",      "Responsabilite accountability",    2, rgpd_04d),
    Regle("LOI-TN-01",  "Loi 2004-63", "Art.1 Art.9",   "Respect vie privee et dignite",    3, loi_tn_01),
    Regle("LOI-TN-02",  "Loi 2004-63", "Art.11",        "Non-exces des donnees",            2, loi_tn_02),
    Regle("LOI-TN-02B", "Loi 2004-63", "Art.10",        "Finalite licite",                  2, loi_tn_02b),
    Regle("LOI-TN-02C", "Loi 2004-63", "Art.7",         "Declaration INPDP",                3, loi_tn_02c),

    # B. Bases legales
    Regle("RGPD-05",    "RGPD",        "Art.6",         "Base legale valide",               3, rgpd_05),
    Regle("RGPD-06",    "RGPD",        "Art.7",         "Consentement valide",              2, rgpd_06),
    Regle("RGPD-06B",   "RGPD",        "Art.7(3)",      "Retrait consentement",             3, rgpd_06b),
    Regle("RGPD-06C",   "RGPD",        "Art.7(4)",      "Consentement non conditionne",     2, rgpd_06c),
    Regle("RGPD-07",    "RGPD",        "Art.9",         "Donnees sensibles",                3, rgpd_07),
    Regle("RGPD-07B",   "RGPD",        "Art.10",        "Donnees penales",                  3, rgpd_07b),
    Regle("LOI-TN-03",  "Loi 2004-63", "Art.10",        "Finalite legitime",               2, loi_tn_03),
    Regle("LOI-TN-03B", "Loi 2004-63", "Art.14",        "Donnees sensibles INPDP",          3, loi_tn_03b),
    Regle("LOI-TN-03C", "Loi 2004-63", "Art.17",        "Service non conditionne",          2, loi_tn_03c),

    # C. Transparence et information
    Regle("RGPD-08",    "RGPD",        "Art.13",        "Information collecte directe",     2, rgpd_08),
    Regle("RGPD-08B",   "RGPD",        "Art.14",        "Information collecte indirecte",   2, rgpd_08b),
    Regle("RGPD-08C",   "RGPD",        "Art.13(1)(f)",  "Info transferts internationaux",   2, rgpd_08c),
    Regle("RGPD-08D",   "RGPD",        "Art.12",        "Modalites droits accessibles",     2, rgpd_08d),
    Regle("LOI-TN-04B", "Loi 2004-63", "Art.27",        "Information personne concernee",   2, loi_tn_04b),
    Regle("LOI-TN-04C", "Loi 2004-63", "Art.44",        "Collecte directe",                 2, loi_tn_04c),

    # D. Droits des personnes
    Regle("RGPD-09",    "RGPD",        "Art.15-20",     "Gestion droits personnes",         3, rgpd_09),
    Regle("RGPD-09B",   "RGPD",        "Art.21",        "Droit opposition",                 2, rgpd_09b),
    Regle("RGPD-09C",   "RGPD",        "Art.22",        "Decision automatisee",             3, rgpd_09c),
    Regle("RGPD-09D",   "RGPD",        "Art.12(3)",     "Delai reponse DSAR",               3, rgpd_09d),
    Regle("LOI-TN-04",  "Loi 2004-63", "Art.35-40",     "Tracabilite demandes droits",      2, loi_tn_04),
    Regle("LOI-TN-04D", "Loi 2004-63", "Art.42",        "Opposition suspendue immediate",   2, loi_tn_04d),
    Regle("LOI-TN-04E", "Loi 2004-63", "Art.21",        "Exactitude et mise a jour",        2, loi_tn_04e),

    # E. Securite et violations
    Regle("RGPD-13",    "RGPD",        "Art.32",        "Mesures securite",                 3, rgpd_13),
    Regle("RGPD-13B",   "RGPD",        "Art.32(1)(a)",  "Chiffrement donnees sensibles",    3, rgpd_13b),
    Regle("RGPD-13C",   "RGPD",        "Art.32(1)(d)",  "Tests securite reguliers",         2, rgpd_13c),
    Regle("RGPD-14",    "RGPD",        "Art.33",        "Notification autorite 72h",        3, rgpd_14),
    Regle("RGPD-14B",   "RGPD",        "Art.33(5)",     "Documentation violations",         2, rgpd_14b),
    Regle("RGPD-15",    "RGPD",        "Art.34",        "Notification personnes",           3, rgpd_15),
    Regle("LOI-TN-05",  "Loi 2004-63", "Art.18",        "Protection acces non autorise",    3, loi_tn_05),
    Regle("LOI-TN-06",  "Loi 2004-63", "Art.18",        "Documentation violation",          2, loi_tn_06),
    Regle("LOI-TN-06B", "Loi 2004-63", "Art.19",        "Mesures physiques et techniques",  2, loi_tn_06b),
    Regle("LOI-TN-06C", "Loi 2004-63", "Art.23",        "Confidentialite post-traitement",  2, loi_tn_06c),

    # F. AIPD et Privacy by Design
    Regle("RGPD-16",    "RGPD",        "Art.35",        "AIPD obligatoire",                 3, rgpd_16),
    Regle("RGPD-17",    "RGPD",        "Art.35",        "Analyse risque prealable",         2, rgpd_17),
    Regle("RGPD-18",    "RGPD",        "Art.25(1)",     "Privacy by Design",               2, rgpd_18),
    Regle("RGPD-18B",   "RGPD",        "Art.25(2)",     "Privacy by Default",              2, rgpd_18b),
    Regle("RGPD-18C",   "RGPD",        "Art.36",        "Consultation prealable autorite",  2, rgpd_18c),

    # G. Sous-traitants et registre
    Regle("RGPD-19",    "RGPD",        "Art.28",        "Contrat sous-traitance DPA",       3, rgpd_19),
    Regle("RGPD-19B",   "RGPD",        "Art.30",        "Registre activites traitement",    2, rgpd_19b),
    Regle("RGPD-19C",   "RGPD",        "Art.28(1)",     "Garanties sous-traitant",          2, rgpd_19c),
    Regle("LOI-TN-07",  "Loi 2004-63", "Art.20",        "Choix sous-traitant",              2, loi_tn_07),
    Regle("LOI-TN-07B", "Loi 2004-63", "Art.23",        "Confidentialite sous-traitant",    2, loi_tn_07b),

    # H. Transferts internationaux
    Regle("RGPD-20",    "RGPD",        "Art.44-46",     "Transfert sans garanties",         3, rgpd_20),
    Regle("RGPD-20B",   "RGPD",        "Art.45-46",     "Adequation documentee",            2, rgpd_20b),
    Regle("LOI-TN-08",  "Loi 2004-63", "Art.52",        "Autorisation INPDP transfert",     3, loi_tn_08),
    Regle("LOI-TN-08B", "Loi 2004-63", "Art.50",        "Protection interets nationaux",    3, loi_tn_08b),
    Regle("LOI-TN-08C", "Loi 2004-63", "Art.51",        "Protection adequate pays tiers",   2, loi_tn_08c),

    # I. DPO
    Regle("RGPD-21",    "RGPD",        "Art.37",        "DPO designe",                      2, rgpd_21),
    Regle("RGPD-21B",   "RGPD",        "Art.38-39",     "Missions DPO garanties",           2, rgpd_21b),

    # J. Responsabilite et politiques
    Regle("RGPD-22",    "RGPD",        "Art.24",        "Politique protection donnees",     2, rgpd_22),
    Regle("RGPD-22B",   "RGPD",        "Art.24(1)",     "Revue periodique mesures",         1, rgpd_22b),
]

NOMBRE_REGLES = len(REGLES)
NOMBRE_REGLES_RGPD = len([r for r in REGLES if r.source == "RGPD"])
NOMBRE_REGLES_LOI_TN = len([r for r in REGLES if r.source == "Loi 2004-63"])
