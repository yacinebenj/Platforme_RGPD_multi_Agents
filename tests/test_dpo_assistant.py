import json
import unittest
from unittest.mock import patch

from llm.dpo_assistant import generate_assistant_answer


class DPOAssistantTests(unittest.TestCase):
    def test_proof_guidance_for_latest_treatment_is_specific(self):
        register_rows = [{
            "id": 204,
            "source_analysis_id": 264,
            "id_traitement": "GMAO-ALL-001",
            "nom_traitement": "Traitement GMAO multi-modules",
            "systeme": "GMAO PRO",
            "base_legale": "",
            "missing_info": "base_legale,securite",
        }]
        treatment_row = {
            "id": 264,
            "id_traitement": "GMAO-ALL-001",
            "nom_traitement": "Traitement GMAO multi-modules",
            "systeme": "GMAO PRO",
            "output_json": json.dumps({
                "q1_cartographie": {"nom_traitement": "Traitement GMAO multi-modules", "systeme": "GMAO PRO"},
                "q2_conformite": {
                    "score_conformite_globale": 88,
                    "niveau_risque": "Moyen",
                    "violations": [
                        {"id_regle": "RGPD-03"},
                        {"id_regle": "RGPD-13B"},
                    ],
                    "points_documentaires": [{"id_regle": "RGPD-01"}],
                },
                "q3_base_legale": {"base_legale_confirmee": False},
            }, ensure_ascii=False),
        }
        base_intent = {
            "label": "cnil_notification",
            "label_display": "Notification CNIL",
            "confidence": 0.45,
            "source": "ml",
            "reason": "test",
            "alternatives": [],
            "advisory_only": True,
        }
        with patch("llm.dpo_assistant.predict_assistant_intent", return_value=base_intent), \
             patch("llm.dpo_assistant.crud.get_register_entries", return_value=register_rows), \
             patch("llm.dpo_assistant.crud.get_treatment_by_row_id", return_value=treatment_row), \
             patch("llm.dpo_assistant.crud.get_treatments", return_value=[]), \
             patch("llm.dpo_assistant.retrieve_rag_context", return_value=("", [])), \
             patch("llm.dpo_assistant._get_groq", side_effect=AssertionError("LLM should not be called")):
            result = generate_assistant_answer(
                "pour le dernier traitement, je pense d'un document, que je dois faire pour que le cnil ne me penalise pas"
            )
        self.assertEqual(result["intent"]["label"], "proof_validation")
        self.assertIn("le bon document depend d'abord de l'ecart", result["answer"].lower())
        self.assertIn("base legale", result["answer"].lower())
        self.assertIn("rattachement conseille", result["answer"].lower())
        self.assertIn("personne", result["answer"].lower())

    def test_latest_treatment_question_prefers_latest_analysis_over_register(self):
        latest_treatment = {
            "id": 267,
            "id_traitement": "UNSCAN-1777461099",
            "nom_traitement": "Analyse documentaire - sample_unstructured_personal_data",
            "systeme": "DOCUMENT IMPORTE",
            "created_at": "2026-04-29 11:11:40",
            "output_json": json.dumps({
                "q1_cartographie": {
                    "nom_traitement": "Analyse documentaire - sample_unstructured_personal_data",
                    "systeme": "DOCUMENT IMPORTE",
                },
                "q2_conformite": {
                    "score_conformite_globale": 68,
                    "niveau_risque": "Eleve",
                    "violations": [{"id_regle": "RGPD-03"}, {"id_regle": "RGPD-13B"}],
                    "points_documentaires": [{"id_regle": "RGPD-01"}],
                },
                "q3_base_legale": {"base_legale_confirmee": False},
            }, ensure_ascii=False),
        }
        stale_register = [{
            "id": 204,
            "source_analysis_id": 264,
            "id_traitement": "QALITAS-ALL-001",
            "nom_traitement": "Traitement QALITAS multi-modules",
            "systeme": "QALITAS WEB",
        }]
        with patch("llm.dpo_assistant.predict_assistant_intent", return_value={
            "label": "proof_validation", "label_display": "Preuves et validations", "confidence": 0.6, "source": "test", "reason": "test", "alternatives": [], "advisory_only": True
        }), \
             patch("llm.dpo_assistant.crud.get_treatments", return_value=[latest_treatment]), \
             patch("llm.dpo_assistant.crud.get_register_entries", return_value=stale_register), \
             patch("llm.dpo_assistant.retrieve_rag_context", return_value=("", [])), \
             patch("llm.dpo_assistant._get_groq", side_effect=AssertionError("LLM should not be called")):
            result = generate_assistant_answer("pour le dernier traitement, quel document dois je fournir ?")
        self.assertIn("Analyse documentaire - sample_unstructured_personal_data", result["answer"])
        self.assertIn("DOCUMENT IMPORTE", result["answer"])
        self.assertNotIn("Traitement QALITAS multi-modules", result["answer"])

    def test_risk_summary_lists_top_treatments(self):
        treatments = [
            {"nom_traitement": "Traitement A", "systeme": "QALITAS WEB", "niveau_risque": "Eleve", "nb_violations": 4, "score_conformite": 71},
            {"nom_traitement": "Traitement B", "systeme": "GMAO PRO", "niveau_risque": "Moyen", "nb_violations": 2, "score_conformite": 84},
            {"nom_traitement": "Traitement C", "systeme": "QALITAS WEB", "niveau_risque": "Critique", "nb_violations": 5, "score_conformite": 63},
        ]
        with patch("llm.dpo_assistant.predict_assistant_intent", return_value={
            "label": "risk_summary", "label_display": "Synthese risques", "confidence": 0.8, "source": "test", "reason": "test", "alternatives": [], "advisory_only": True
        }), \
             patch("llm.dpo_assistant.crud.get_treatments", return_value=treatments), \
             patch("llm.dpo_assistant.crud.get_register_entries", return_value=[]), \
             patch("llm.dpo_assistant.retrieve_rag_context", return_value=("", [])), \
             patch("llm.dpo_assistant._get_groq", side_effect=AssertionError("LLM should not be called")):
            result = generate_assistant_answer("Quels traitements sont les plus risqués ?")
        self.assertIn("Traitement C", result["answer"])
        self.assertIn("Traitement A", result["answer"])
        self.assertIn("priorite", result["answer"].lower())

    def test_cnil_summary_mentions_flagged_incidents(self):
        incident_reviews = [
            {"id_incident": "INC-001", "nom_traitement": "Traitement RH", "qualification": "Violation averee", "notifier_cnil": "Oui", "systeme": "QALITAS WEB"},
            {"id_incident": "INC-002", "nom_traitement": "Traitement Sites", "qualification": "Pas de violation RGPD", "notifier_cnil": "Non", "systeme": "GMAO PRO"},
        ]
        with patch("llm.dpo_assistant.predict_assistant_intent", return_value={
            "label": "cnil_notification", "label_display": "Notification CNIL", "confidence": 0.82, "source": "test", "reason": "test", "alternatives": [], "advisory_only": True
        }), \
             patch("llm.dpo_assistant.crud.get_incident_reviews", return_value=incident_reviews), \
             patch("llm.dpo_assistant.crud.get_treatments", return_value=[]), \
             patch("llm.dpo_assistant.crud.get_register_entries", return_value=[]), \
             patch("llm.dpo_assistant.retrieve_rag_context", return_value=("", [])), \
             patch("llm.dpo_assistant._get_groq", side_effect=AssertionError("LLM should not be called")):
            result = generate_assistant_answer("Quels incidents doivent etre notifies a la CNIL ?")
        self.assertIn("INC-001", result["answer"])
        self.assertIn("72h", result["answer"].lower())

    def test_pending_proofs_answer_mentions_actions_and_validations(self):
        validations = [
            {"target_type": "legal_basis", "target_label": "Base legale TRT-001", "decision": "preuve_fournie"},
            {"target_type": "treatment", "target_label": "Traitement RH", "decision": "valide"},
        ]
        actions = [
            {"title": "Mettre a jour la politique de conservation", "proof_status": "Preuve a verifier", "has_accepted_proof": False},
        ]
        with patch("llm.dpo_assistant.predict_assistant_intent", return_value={
            "label": "proof_validation", "label_display": "Preuves et validations", "confidence": 0.77, "source": "test", "reason": "test", "alternatives": [], "advisory_only": True
        }), \
             patch("llm.dpo_assistant.crud.get_dpo_validations", return_value=validations), \
             patch("llm.dpo_assistant.crud.get_actions", return_value=actions), \
             patch("llm.dpo_assistant.crud.get_treatments", return_value=[]), \
             patch("llm.dpo_assistant.crud.get_register_entries", return_value=[]), \
             patch("llm.dpo_assistant.retrieve_rag_context", return_value=("", [])), \
             patch("llm.dpo_assistant._get_groq", side_effect=AssertionError("LLM should not be called")):
            result = generate_assistant_answer("Quelles preuves restent a valider ?")
        self.assertIn("Base legale TRT-001", result["answer"])
        self.assertIn("politique de conservation", result["answer"].lower())


if __name__ == "__main__":
    unittest.main()
