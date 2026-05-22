import copy
import json
import unittest
from unittest.mock import patch

from agents.agent_a import build_traitement_from_gmao, build_traitement_from_qalitas, run_agent_a
from agents.agent_b import run_agent_b
from agents.agent_c import run_agent_c
from agents.agent_d import run_agent_d
from api.main import (
    AnalyseComplete,
    DSARExecutionRequest,
    DemandeDSAR,
    GovernanceFromAgents,
    Incident,
    Traitement,
    TraitementAvecIncident,
    analyser,
    droits,
    execute_droits,
    gouvernance_from_agents,
    risques,
)
from rules.rule_engine import evaluer_conformite
from rules.schema import valider_schema
from rules.severity import calculer_score_brut, determiner_niveau_risque, normaliser_score
from tests.scenarios_tim import SCENARIOS


def _empty_memory(*args, **kwargs):
    return {
        "available": False,
        "count": 0,
        "impact": "aucun",
        "confidence": "aucune",
        "guidance": "Aucun precedent DPO similaire trouve.",
        "examples": [],
    }


class RuleEngineTests(unittest.TestCase):
    def test_non_conforming_case_scores_high_risk(self):
        traitement = {
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
            "privacy_by_design": False,
            "privacy_by_default": False,
            "transfert_etranger": False,
            "registre_traitement": False,
            "politique_protection_donnees": False,
            "revue_periodique_mesures": False,
            "contrat_sous_traitance": False,
            "garanties_sous_traitant": False,
            "traitement_grande_echelle": False,
            "dpo_designe": False,
            "missions_dpo_garanties": False,
            "chiffrement_actif": False,
            "tests_securite_reguliers": False,
            "controle_acces_physique": False,
            "confidentialite_post_traitement": False,
            "collecte_indirecte": False,
            "information_collecte_indirecte": False,
            "consentement_collecte_indirecte": False,
            "information_personnes_concernees": False,
            "information_transfert_fournie": False,
            "modalites_droits_accessibles": False,
            "opposition_ignoree": False,
            "decision_automatisee": False,
            "garanties_decision_auto": False,
            "dsar_hors_delai": False,
            "adequation_ou_garanties_documentees": False,
            "autorisation_inpdp_transfert": False,
            "niveau_protection_adequat": False,
            "risque_securite_nationale": False,
            "declaration_inpdp": False,
            "autorisation_donnees_penales": False,
            "donnees_penales": False,
            "consentement_retire": False,
            "consentement_conditionne_service": False,
            "service_conditionne_consentement": False,
        }
        valider_schema(traitement)
        violations, _ = evaluer_conformite(traitement)
        score = normaliser_score(calculer_score_brut(violations))
        self.assertGreaterEqual(len(violations), 10)
        self.assertIn(determiner_niveau_risque(score), {"Eleve", "Critique"})


class AgentATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patchers = [
            patch("agents.agent_a.enrich_violations_with_rag", side_effect=lambda violations: violations),
            patch("agents.agent_a._find_dpo_memory_for_treatment", side_effect=_empty_memory),
        ]
        for patcher in cls.patchers:
            patcher.start()

    @classmethod
    def tearDownClass(cls):
        for patcher in reversed(cls.patchers):
            patcher.stop()

    def test_tim_scenarios_all_produce_complete_agent_a_outputs(self):
        for scenario in SCENARIOS:
            with self.subTest(id_traitement=scenario["id_traitement"]):
                result = run_agent_a(copy.deepcopy(scenario))
                self.assertIn("q1_cartographie", result)
                self.assertIn("q2_conformite", result)
                self.assertIn("q3_base_legale", result)
                self.assertIn("q1_register", result)
                self.assertIsInstance(result["q2_conformite"]["violations"], list)
                self.assertGreaterEqual(result["q2_conformite"]["score_conformite_globale"], 0)
                self.assertLessEqual(result["q2_conformite"]["score_conformite_globale"], 100)

    def test_compliant_treatment_stays_high_confidence_and_not_blocked(self):
        traitement = {
            "id_traitement": "TRT-CLEAN-001",
            "nom_traitement": "Gestion relation client",
            "systeme": "QALITAS WEB",
            "responsable": "DPO",
            "donnees_collectees": ["nom", "email", "telephone"],
            "categories_donnees": ["identite", "contact"],
            "donnees_sensibles": False,
            "donnees_penales": False,
            "finalite": "Gestion du compte client",
            "finalite_definie": True,
            "base_legale": "contrat",
            "personnes_concernees": ["clients"],
            "destinataires": ["support"],
            "transfert_etranger": False,
            "duree_conservation": "5 ans apres fin de relation contractuelle",
            "duree_conservation_definie": True,
            "duree_depassee": False,
            "donnees_minimisees": True,
            "donnees_exactes": True,
            "respect_vie_privee": True,
            "declaration_inpdp": True,
            "garanties_specifiques": True,
            "mesures_securite": ["controle_acces", "chiffrement"],
            "chiffrement_actif": True,
            "tests_securite_reguliers": True,
            "controle_acces_physique": True,
            "confidentialite_post_traitement": True,
            "privacy_by_design": True,
            "privacy_by_default": True,
            "processus_droits_personnes": True,
            "information_personnes_concernees": True,
            "modalites_droits_accessibles": True,
            "collecte_indirecte": False,
            "information_collecte_indirecte": False,
            "consentement_collecte_indirecte": False,
            "information_transfert_fournie": False,
            "opposition_ignoree": False,
            "decision_automatisee": False,
            "garanties_decision_auto": False,
            "dsar_hors_delai": False,
            "violation_donnees": False,
            "notification_72h": False,
            "notification_personnes": False,
            "violation_documentee": True,
            "risque_eleve": False,
            "aipd_realisee": True,
            "mise_en_production": True,
            "analyse_risque_avant_production": True,
            "consultation_autorite_si_risque_residuel": True,
            "contrat_sous_traitance": True,
            "garanties_sous_traitant": True,
            "registre_traitement": True,
            "traitement_grande_echelle": False,
            "dpo_designe": True,
            "missions_dpo_garanties": True,
            "politique_protection_donnees": True,
            "revue_periodique_mesures": True,
            "adequation_ou_garanties_documentees": True,
            "autorisation_inpdp_transfert": True,
            "niveau_protection_adequat": True,
            "risque_securite_nationale": False,
            "consentement_valide": False,
            "consentement_retire": False,
            "consentement_conditionne_service": False,
            "service_conditionne_consentement": False,
            "autorisation_donnees_penales": False,
        }
        result = run_agent_a(traitement)
        self.assertEqual(result["q1_cartographie"]["alertes_q1"], [])
        self.assertEqual(result["q3_base_legale"]["traitement_a_bloquer"], False)
        self.assertGreaterEqual(result["q2_conformite"]["score_conformite_globale"], 90)

    def test_source_derived_qalitas_detects_new_critical_content_and_blocks(self):
        records = [{
            "FullName": "Test User",
            "Email": "test@example.com",
            "Notes": "Carte bancaire: 4111 1111 1111 1111 ; CIN: AA123456",
        }]
        traitement = build_traitement_from_qalitas("customers", records)
        result = run_agent_a(traitement)
        alert_codes = {item["code"] for item in result["q1_cartographie"]["alertes_q1"]}
        violation_ids = {item["id_regle"] for item in result["q2_conformite"]["violations"]}
        self.assertIn("Q1-STRUCTURED-CONTENT-DETECTED", alert_codes)
        self.assertIn("Q1-SENSITIVE-UNPROTECTED", alert_codes)
        self.assertTrue(result["q3_base_legale"]["traitement_a_bloquer"])
        self.assertIn("RGPD-13B", violation_ids)
        self.assertIn("RGPD-07", violation_ids)

    def test_source_derived_gmao_detects_hidden_text_content(self):
        records = [{
            "TechnicianName": "Ali Ben Salah",
            "WorkNote": "Carte bancaire: 4111 1111 1111 1111 ; GPS 36.8065, 10.1815 ; CIN: AA123456",
            "CurrentUserId": "00000000-0000-0000-0000-000000000123",
        }]
        traitement = build_traitement_from_gmao("maintenance_operations", records)
        findings = traitement["_detected_fields"]["structured_content_findings"]
        patterns = {item["pattern"] for item in findings}
        self.assertEqual(
            [item for item in findings if item["field_name"] == "CurrentUserId" and item["pattern"] == "bank_card"],
            [],
        )
        self.assertTrue({"bank_card", "gps_coords", "id_card"}.issubset(patterns))


class AgentBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patchers = [
            patch("agents.agent_a.enrich_violations_with_rag", side_effect=lambda violations: violations),
            patch("agents.agent_a._find_dpo_memory_for_treatment", side_effect=_empty_memory),
            patch("agents.agent_b._find_dpo_memory_for_aipd", side_effect=_empty_memory),
        ]
        for patcher in cls.patchers:
            patcher.start()

    @classmethod
    def tearDownClass(cls):
        for patcher in reversed(cls.patchers):
            patcher.stop()

    def test_high_risk_scenario_requests_aipd(self):
        traitement = copy.deepcopy(SCENARIOS[1])
        with patch("agents.agent_b.crud.get_inventory_treatments", return_value=[]), \
             patch("agents.agent_b.crud.get_inventory_fields", return_value=[]), \
             patch("agents.agent_b.crud.get_inventory_flows", return_value=[]), \
             patch("agents.agent_b.crud.get_inventory_alerts", return_value=[]), \
             patch("agents.agent_b.crud.get_unstructured_scans", return_value=[]):
            agent_a = run_agent_a(copy.deepcopy(traitement))
            result = run_agent_b(traitement, agent_a, incident=None)
        self.assertTrue(result["q4_risques_aipd"]["aipd"]["aipd_obligatoire"])
        self.assertGreaterEqual(result["q4_risques_aipd"]["nombre_risques"], 1)

    def test_encrypted_incident_suppresses_notifications(self):
        traitement = copy.deepcopy(SCENARIOS[0])
        incident = {
            "id_incident": "INC-CRYPT-001",
            "type_incident": "Perte terminal",
            "description": "Telephone perdu mais chiffre",
            "donnees_affectees": ["nom", "email"],
            "nombre_personnes_affectees": 200,
            "gravite_incident": 3,
            "donnees_sensibles_impliquees": True,
            "donnees_chiffrees": True,
        }
        with patch("agents.agent_b.crud.get_inventory_treatments", return_value=[]), \
             patch("agents.agent_b.crud.get_inventory_fields", return_value=[]), \
             patch("agents.agent_b.crud.get_inventory_flows", return_value=[]), \
             patch("agents.agent_b.crud.get_inventory_alerts", return_value=[]), \
             patch("agents.agent_b.crud.get_unstructured_scans", return_value=[]):
            agent_a = run_agent_a(copy.deepcopy(traitement))
            result = run_agent_b(traitement, agent_a, incident=incident)
        notification = result["q6_incidents"]["notification"]
        self.assertFalse(notification["notifier_cnil"])
        self.assertFalse(notification["notifier_personnes"])


class AgentCTests(unittest.TestCase):
    def test_valid_access_request_is_accepted(self):
        demande = {
            "id_demande": "DSAR-ACCESS-001",
            "nom_demandeur": "Jamel Badri",
            "date_reception": "2026-04-20",
            "type_droit": "acces",
            "systeme_concerne": "QALITAS WEB",
            "donnees_concernees": ["email"],
            "identite_verifiee": True,
            "demandes_precedentes_30j": 0,
            "base_legale_traitement": "contrat",
            "obligation_legale_conservation": False,
        }
        with patch("agents.agent_c.crud.get_inventory_treatments", return_value=[]), \
             patch("agents.agent_c.crud.get_inventory_fields", return_value=[]), \
             patch("agents.agent_c.crud.get_unstructured_scans", return_value=[]), \
             patch("agents.agent_c.crud.get_treatments", return_value=[]):
            result = run_agent_c(demande)
        self.assertEqual(result["q5_droits"]["qualification"], "valide")
        self.assertEqual(result["q5_droits"]["reponse"]["statut_reponse"], "A traiter")

    def test_portability_is_rejected_without_contract_or_consent(self):
        demande = {
            "id_demande": "DSAR-PORT-001",
            "nom_demandeur": "Samir",
            "date_reception": "2026-04-20",
            "type_droit": "portabilite",
            "systeme_concerne": "QALITAS WEB",
            "donnees_concernees": ["email"],
            "identite_verifiee": True,
            "demandes_precedentes_30j": 0,
            "base_legale_traitement": "obligation_legale",
            "obligation_legale_conservation": False,
        }
        with patch("agents.agent_c.crud.get_inventory_treatments", return_value=[]), \
             patch("agents.agent_c.crud.get_inventory_fields", return_value=[]), \
             patch("agents.agent_c.crud.get_unstructured_scans", return_value=[]), \
             patch("agents.agent_c.crud.get_treatments", return_value=[]):
            result = run_agent_c(demande)
        self.assertEqual(result["q5_droits"]["qualification"], "non_applicable")

    def test_erasure_respects_legal_exception(self):
        demande = {
            "id_demande": "DSAR-DEL-001",
            "nom_demandeur": "Samir",
            "date_reception": "2026-04-20",
            "type_droit": "effacement",
            "systeme_concerne": "GMAO PRO",
            "donnees_concernees": ["email"],
            "identite_verifiee": True,
            "demandes_precedentes_30j": 0,
            "base_legale_traitement": "contrat",
            "obligation_legale_conservation": True,
        }
        with patch("agents.agent_c.crud.get_inventory_treatments", return_value=[]), \
             patch("agents.agent_c.crud.get_inventory_fields", return_value=[]), \
             patch("agents.agent_c.crud.get_unstructured_scans", return_value=[]), \
             patch("agents.agent_c.crud.get_treatments", return_value=[]):
            result = run_agent_c(demande)
        self.assertEqual(result["q5_droits"]["qualification"], "exception_legale")


class AgentDTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patchers = [
            patch("agents.agent_a.enrich_violations_with_rag", side_effect=lambda violations: violations),
            patch("agents.agent_a._find_dpo_memory_for_treatment", side_effect=_empty_memory),
            patch("agents.agent_b._find_dpo_memory_for_aipd", side_effect=_empty_memory),
        ]
        for patcher in cls.patchers:
            patcher.start()

    @classmethod
    def tearDownClass(cls):
        for patcher in reversed(cls.patchers):
            patcher.stop()

    def test_governance_agent_returns_summary_and_recommendations(self):
        traitement = copy.deepcopy(SCENARIOS[0])
        demande = {
            "id_demande": "DSAR-001",
            "nom_demandeur": "Jamel",
            "date_reception": "2026-04-20",
            "type_droit": "acces",
            "systeme_concerne": "GMAO PRO",
            "donnees_concernees": ["email"],
            "identite_verifiee": True,
            "demandes_precedentes_30j": 0,
            "base_legale_traitement": "contrat",
            "obligation_legale_conservation": False,
        }
        with patch("agents.agent_c.crud.get_inventory_treatments", return_value=[]), \
             patch("agents.agent_c.crud.get_inventory_fields", return_value=[]), \
             patch("agents.agent_c.crud.get_unstructured_scans", return_value=[]), \
             patch("agents.agent_c.crud.get_treatments", return_value=[]), \
             patch("agents.agent_d.charger_historique_gouvernance", return_value={"summary": {}, "recent": {}, "identity": {}, "snapshots": [{}]}), \
             patch("agents.agent_d.generate_report", return_value="rapport test"):
            agent_a = run_agent_a(copy.deepcopy(traitement))
            agent_b = run_agent_b(traitement, agent_a)
            agent_c = run_agent_c(demande)
            result = run_agent_d(agent_a, agent_b, agent_c)
        self.assertIn("q7_gouvernance", result)
        self.assertIn("q8_amelioration", result)
        self.assertIn("synthese", result)
        self.assertIsInstance(result["synthese"]["score_maturite_global"], int)


class APIFunctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patchers = [
            patch("agents.agent_a.enrich_violations_with_rag", side_effect=lambda violations: violations),
            patch("agents.agent_a._find_dpo_memory_for_treatment", side_effect=_empty_memory),
            patch("agents.agent_b._find_dpo_memory_for_aipd", side_effect=_empty_memory),
        ]
        for patcher in cls.patchers:
            patcher.start()

    @classmethod
    def tearDownClass(cls):
        for patcher in reversed(cls.patchers):
            patcher.stop()

    def test_analyser_endpoint_function_returns_agent_a(self):
        payload = Traitement(**copy.deepcopy(SCENARIOS[3]))
        with patch("api.main.crud.save_treatment", return_value=101), \
             patch("api.main.persist_register_and_actions", return_value=None):
            result = analyser(payload, user={"role": "dpo"})
        self.assertIn("q2_conformite", result)

    def test_risques_endpoint_function_returns_persistence_ids(self):
        payload = TraitementAvecIncident(
            traitement=Traitement(**copy.deepcopy(SCENARIOS[0])),
            incident=Incident(
                id_incident="INC-001",
                type_incident="Vol de donnees",
                description="Test",
                donnees_affectees=["nom"],
                nombre_personnes_affectees=120,
                gravite_incident=3,
                donnees_sensibles_impliquees=True,
                donnees_chiffrees=False,
            ),
        )
        with patch("api.main.crud.save_treatment", return_value=11), \
             patch("api.main.persist_register_and_actions", return_value=None), \
             patch("api.main.crud.save_risk_review", return_value=22), \
             patch("api.main.crud.save_violation", return_value=33), \
             patch("api.main.crud.save_incident_review", return_value=44):
            result = risques(payload, user={"role": "dpo"})
        self.assertEqual(result["persistence"]["analysis_id"], 11)
        self.assertEqual(result["persistence"]["risk_review_id"], 22)
        self.assertEqual(result["persistence"]["incident_review_id"], 44)

    def test_droits_endpoint_trims_search_payload(self):
        payload = DemandeDSAR(
            id_demande="DSAR-API-001",
            nom_demandeur="Nadia",
            date_reception="2026-04-20",
            type_droit="acces",
            systeme_concerne="QALITAS WEB",
            donnees_concernees=["email"],
            identite_verifiee=True,
            demandes_precedentes_30j=0,
            base_legale_traitement="contrat",
            obligation_legale_conservation=False,
        )
        with patch("agents.agent_c.crud.get_inventory_treatments", return_value=[]), \
             patch("agents.agent_c.crud.get_inventory_fields", return_value=[]), \
             patch("agents.agent_c.crud.get_unstructured_scans", return_value=[]), \
             patch("agents.agent_c.crud.get_treatments", return_value=[]), \
             patch("api.main.crud.save_dsar", return_value=1):
            result = droits(payload, user={"role": "dpo"})
        self.assertIn("recherche_resume", result["q5_droits"])
        self.assertNotIn("recherche_transversale", result["q5_droits"])

    def test_execute_droits_safe_log_blocks_non_executable_package(self):
        saved_output = {
            "q5_droits": {
                "qualification": "en_attente_verification",
                "paquet_dsar": {
                    "resume_officiel": {},
                    "package_operationnel": {
                        "can_execute": False,
                        "type_droit": "acces",
                        "rectification_targets": [],
                        "erasure_targets": [],
                        "restriction_targets": [],
                        "opposition_targets": [],
                        "automated_decision_targets": [],
                    },
                },
            }
        }
        payload = DSARExecutionRequest(id_demande="DSAR-001", executor="DPO", mode_execution="safe_log")
        with patch("api.main.crud.get_dsar_by_id", return_value={
            "input_json": json.dumps({"type_droit": "acces"}),
            "output_json": json.dumps(saved_output),
        }), \
             patch("api.main.crud.save_dsar_execution", return_value=901):
            result = execute_droits(payload, user={"role": "dpo"})
        self.assertEqual(result["decision"], "blocked")
        self.assertEqual(result["statut"], "bloque")

    def test_gouvernance_from_agents_endpoint_returns_agent_d(self):
        traitement = copy.deepcopy(SCENARIOS[0])
        with patch("agents.agent_c.crud.get_inventory_treatments", return_value=[]), \
             patch("agents.agent_c.crud.get_inventory_fields", return_value=[]), \
             patch("agents.agent_c.crud.get_unstructured_scans", return_value=[]), \
             patch("agents.agent_c.crud.get_treatments", return_value=[]), \
             patch("agents.agent_d.charger_historique_gouvernance", return_value={"summary": {}, "recent": {}, "identity": {}, "snapshots": [{}]}), \
             patch("agents.agent_d.generate_report", return_value="rapport test"), \
             patch("api.main.persist_governance_snapshot", return_value=None):
            agent_a = run_agent_a(copy.deepcopy(traitement))
            agent_b = run_agent_b(traitement, agent_a)
            agent_c = run_agent_c({
                "id_demande": "DSAR-001",
                "nom_demandeur": "Jamel",
                "date_reception": "2026-04-20",
                "type_droit": "acces",
                "systeme_concerne": "GMAO PRO",
                "donnees_concernees": ["email"],
                "identite_verifiee": True,
                "demandes_precedentes_30j": 0,
                "base_legale_traitement": "contrat",
                "obligation_legale_conservation": False,
            })
            payload = GovernanceFromAgents(agent_a=agent_a, agent_b=agent_b, agent_c=agent_c)
            result = gouvernance_from_agents(payload, user={"role": "dpo"})
        self.assertIn("rapport_dpo", result)
        self.assertIn("synthese", result)


if __name__ == "__main__":
    unittest.main()
