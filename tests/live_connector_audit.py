import sys
from pathlib import Path
import json
import traceback
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents import agent_a
from agents.agent_a import build_traitement_from_gmao, build_traitement_from_qalitas, run_agent_a
from gmao.connector import MODULE_ENDPOINTS as GMAO_MODULE_ENDPOINTS
from gmao.connector import get_connector as get_gmao_connector
from qalitas.connector import MODULE_ENDPOINTS as QALITAS_MODULE_ENDPOINTS
from qalitas.connector import get_connector as get_qalitas_connector


def _empty_memory(*args, **kwargs):
    return {
        "available": False,
        "count": 0,
        "impact": "aucun",
        "confidence": "aucune",
        "guidance": "Aucun precedent DPO similaire trouve.",
        "examples": [],
    }


agent_a.enrich_violations_with_rag = lambda violations: violations
agent_a._find_dpo_memory_for_treatment = _empty_memory


def _analyze_qalitas(module: str, records: list) -> dict:
    traitement = build_traitement_from_qalitas(module, records[:50])
    result = run_agent_a(traitement)
    return {
        "score": result["q2_conformite"]["score_conformite_globale"],
        "niveau": result["q2_conformite"]["niveau_risque"],
        "q1_alertes": len(result["q1_cartographie"]["alertes_q1"]),
        "q2_violations": len(result["q2_conformite"]["violations"]),
        "q2_documentary": len(result["q2_conformite"].get("points_documentaires", [])),
        "base_legale": result["q3_base_legale"].get("base_legale_recommandee"),
        "blocked": result["q3_base_legale"].get("traitement_a_bloquer"),
        "hors_champ_rgpd": result["q2_conformite"].get("hors_champ_rgpd"),
    }


def _analyze_gmao(module: str, records: list) -> dict:
    traitement = build_traitement_from_gmao(module, records[:50])
    result = run_agent_a(traitement)
    return {
        "score": result["q2_conformite"]["score_conformite_globale"],
        "niveau": result["q2_conformite"]["niveau_risque"],
        "q1_alertes": len(result["q1_cartographie"]["alertes_q1"]),
        "q2_violations": len(result["q2_conformite"]["violations"]),
        "q2_documentary": len(result["q2_conformite"].get("points_documentaires", [])),
        "base_legale": result["q3_base_legale"].get("base_legale_recommandee"),
        "blocked": result["q3_base_legale"].get("traitement_a_bloquer"),
        "hors_champ_rgpd": result["q2_conformite"].get("hors_champ_rgpd"),
    }


def _audit_source(source_name: str, connector_factory, modules: dict, analyzer) -> dict:
    report = {
        "source": source_name,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "modules": [],
        "source_error": None,
    }
    try:
        connector = connector_factory()
    except Exception as exc:
        report["source_error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "trace": traceback.format_exc(limit=2),
        }
        return report
    for module in modules.keys():
        item = {
            "module": module,
            "status": "unknown",
            "records_count": None,
            "analysis": None,
            "error": None,
        }
        try:
            records = connector.fetch(module)
            item["status"] = "ok"
            item["records_count"] = len(records)
            if records:
                item["analysis"] = analyzer(module, records)
            else:
                item["analysis"] = {
                    "score": None,
                    "niveau": "no_data",
                    "q1_alertes": 0,
                    "q2_violations": 0,
                    "q2_documentary": 0,
                    "base_legale": None,
                    "blocked": False,
                    "hors_champ_rgpd": True,
                }
        except Exception as exc:
            item["status"] = "error"
            item["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "trace": traceback.format_exc(limit=2),
            }
        report["modules"].append(item)
    return report


def main():
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "qalitas": _audit_source(
            "QALITAS WEB",
            get_qalitas_connector,
            QALITAS_MODULE_ENDPOINTS,
            _analyze_qalitas,
        ),
        "gmao": _audit_source(
            "GMAO PRO WEB",
            get_gmao_connector,
            GMAO_MODULE_ENDPOINTS,
            _analyze_gmao,
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
