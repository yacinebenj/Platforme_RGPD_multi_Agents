"""
database/seed.py
================
Seeding script to populate the RGPD platform with realistic mock data.
Uses the scenarios defined in `tests/scenarios_tim.py`.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.db import init_db
from database import crud
from tests.scenarios_tim import SCENARIOS
from agents.agent_a import run_agent_a
from agents.agent_b import run_agent_b
from agents.agent_c import run_agent_c
from datetime import datetime, timedelta

def seed_data():
    print("[SEED] Initializing database...")
    init_db()

    print(f"[SEED] Found {len(SCENARIOS)} scenarios to process.")

    for i, scenario in enumerate(SCENARIOS):
        print(f"[SEED] Processing Scenario {i+1}: {scenario['id_traitement']} - {scenario['nom_traitement']}")
        
        # 1. Run Agent A (Conformité)
        agent_a_res = run_agent_a(scenario)
        crud.save_treatment(scenario, agent_a_res)
        
        # 2. Run Agent B (Risques)
        # We create a dummy incident for some scenarios
        incident = None
        if i % 2 == 1: # Add incidents to half of the scenarios
            incident = {
                "id_incident": f"INC-{scenario['id_traitement']}",
                "date_detection": datetime.now().strftime("%Y-%m-%d"),
                "type_incident": "perte_vol_donnees" if i == 1 else "acces_non_autorise",
                "description": f"Incident simulé pour le traitement {scenario['nom_traitement']}",
                "donnees_affectees": scenario["donnees_collectees"][:2],
                "nombre_personnes_affectees": 50 * (i + 1),
                "gravite_incident": (i % 3) + 1,
                "donnees_sensibles_impliquees": scenario["donnees_sensibles"],
                "donnees_chiffrees": False
            }
        
        agent_b_res = run_agent_b(scenario, agent_a_res, incident)
        if incident:
            crud.save_violation(incident, agent_b_res)

    # 3. Add some Mock DSARs (Agent C)
    print("[SEED] Adding mock DSARs...")
    dsar_requests = [
        {
            "id_demande": "DSAR-SEED-001",
            "nom_demandeur": "Alice Martin",
            "date_reception": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
            "type_droit": "acces",
            "systeme_concerne": "QALITAS WEB",
            "donnees_concernees": ["nom", "email"],
            "base_legale_traitement": "contrat",
            "demandes_precedentes_30j": 0,
            "identite_verifiee": True,
            "obligation_legale_conservation": False
        },
        {
            "id_demande": "DSAR-SEED-002",
            "nom_demandeur": "Bob Smith",
            "date_reception": datetime.now().strftime("%Y-%m-%d"),
            "type_droit": "effacement",
            "systeme_concerne": "GMAO PRO",
            "donnees_concernees": ["localisation_GPS"],
            "base_legale_traitement": "consentement",
            "demandes_precedentes_30j": 1,
            "identite_verifiee": True,
            "obligation_legale_conservation": True
        }
    ]

    for dsar in dsar_requests:
        res_c = run_agent_c(dsar)
        crud.save_dsar(dsar, res_c)

    # 4. Add some Mock Consents
    print("[SEED] Adding mock consents...")
    crud.save_consent(
        nom_personne="Charles Aznavour",
        email="charles@test.com",
        id_traitement="TRT-QALITAS-001",
        nom_traitement="Gestion des audits QHSE",
        finalite="Analyses statistiques qualité",
        date_expiration=(datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")
    )
    
    print("[SEED] Database seeding completed successfully!")

if __name__ == "__main__":
    seed_data()
