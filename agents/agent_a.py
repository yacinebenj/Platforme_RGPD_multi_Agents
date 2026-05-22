from rules.rule_engine import evaluer_conformite
from rules.severity import calculer_score_brut, normaliser_score, determiner_niveau_risque
from rules.schema import valider_schema
import os
import json
import logging
import re
from groq import Groq
from dotenv import load_dotenv
from database import crud

try:
    from ml.field_classifier import predict_field_category
except Exception:
    predict_field_category = None

load_dotenv("key.env")
_groq_client = None

def _get_groq():
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _groq_client

# ===============================
# LLM FIELD INFERENCE (Level 2)
# ===============================

INFERENCE_PROMPT = """Tu es un expert RGPD. A partir de la description d un traitement de donnees, infere les champs boolean et textuels suivants.
Reponds UNIQUEMENT avec un objet JSON valide, sans markdown, sans explication.

Description du traitement: {description}
Nom du traitement: {nom}
Systeme: {systeme}

Infere ces champs (true/false sauf indication):
- base_legale: string parmi ["consentement","contrat","obligation_legale","interet_legitime","mission_publique"] ou false
- finalite: string court decrivant la finalite
- donnees_sensibles: bool
- donnees_minimisees: bool
- duree_conservation_definie: bool
- mesures_securite: liste de strings (ex: ["chiffrement","controle_acces"]) ou liste vide
- privacy_by_design: bool
- processus_droits_personnes: bool
- information_personnes_concernees: bool
- transfert_etranger: bool
- risque_eleve: bool
- donnees_collectees: liste de strings identifiant les types de donnees collectees

Sois conservateur: si tu n es pas sur, mets false. Ne complete que ce que la description permet d inferer.
JSON uniquement:"""

def infer_fields_from_description(description: str, nom: str = "", systeme: str = "") -> dict:
    """Level 2: use Groq LLM to auto-infer treatment fields from natural language description."""
    if not description or not description.strip():
        return {}
    try:
        client = _get_groq()
        prompt = INFERENCE_PROMPT.format(
            description=description.strip(),
            nom=nom or "Non specifie",
            systeme=systeme or "Non specifie"
        )
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.1
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        inferred = json.loads(raw.strip())
        return inferred
    except Exception as e:
        logging.warning(f"[Agent A] LLM inference failed: {e}")
        return {}


def merge_with_inferred(traitement: dict, inferred: dict) -> dict:
    """Merge inferred fields into traitement, only filling fields not already provided by user."""
    merged = dict(traitement)
    for key, value in inferred.items():
        # Only fill if field is missing, None, False (default), or empty list
        existing = merged.get(key)
        if existing is None or existing is False or existing == [] or existing == "":
            merged[key] = value
    return merged


# ===============================
# RAG CITATION ENRICHMENT (Level 1)
# ===============================

_rag_instance = None

def _get_rag_safe():
    """Lazy-load RAG. Returns None if unavailable (e.g. index not built yet)."""
    global _rag_instance
    if _rag_instance is not None:
        return _rag_instance
    try:
        from llm.rag_builder import get_rag, search as rag_search
        index, chunks, model = get_rag()
        _rag_instance = (index, chunks, model, rag_search)
        return _rag_instance
    except Exception as e:
        logging.warning(f"[Agent A] RAG unavailable: {e}")
        return None


def enrich_violations_with_rag(violations: list) -> list:
    """Level 1: attach real legal article excerpts to each violation using RAG semantic search."""
    rag = _get_rag_safe()
    if not rag:
        return violations  # graceful fallback

    index, chunks, model, rag_search = rag

    for v in violations:
        query = f"{v.get('article', '')} {v.get('message', '')}"
        try:
            results = rag_search(query, index, chunks, model, top_k=2)
            citations = []
            for r in results:
                citations.append({
                    "source": r.get("source", ""),
                    "extrait": r.get("text", "")[:250]
                })
            v["citations_legales"] = citations
        except Exception:
            v["citations_legales"] = []
    return violations



# ===============================
# Q1 - CARTOGRAPHIE
# ===============================

DONNEES_SENSIBLES = [
    "donnees_sante", "biometrie", "origine_ethnique",
    "religion", "opinion_politique", "vie_sexuelle"
]

DONNEES_CRITIQUES = [
    "localisation_GPS", "mot_de_passe", "donnees_bancaires",
    "Cin", "Passport", "PassportNumber", "RegistrationNumber",
    "CinIssued", "CinIssuedTo", "CinAdresse", "CinAddress"
]

DONNEES_PERSONNELLES = [
    "nom", "prenom", "email", "telephone",
    "adresse", "matricule", "photo"
]

DONNEES_PERSONNELLES_CONTEXTUELLES = {
    "Department Id", "Department Code", "Department Designation", "Department Complet Designation",
    "Certificate Nom", "Certificate Email", "Shared With", "Shared With Names",
    "Compare First Nom", "Compare Last Nom", "Reference + FullName", "SerialNumberFullName",
    "Référence + nom complet"
}

FLOW_ORDER = ["collecte", "utilisation", "stockage", "partage", "archivage_suppression"]

STRUCTURED_CONTENT_PATTERNS = {
    "bank_card": {
        "regex": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
        "canonical_field": "donnees_bancaires",
        "classification": "critique",
        "criticite": "critique",
        "article": "RGPD Art. 32 - Donnees financieres a proteger",
    },
    "iban": {
        "regex": re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b", re.IGNORECASE),
        "canonical_field": "donnees_bancaires",
        "classification": "critique",
        "criticite": "critique",
        "article": "RGPD Art. 32 - IBAN / donnees bancaires",
    },
    "id_card": {
        "regex": re.compile(r"\b(?:CIN|ID|Passport|PassportNumber)[\s:=-]*([A-Z]{1,3}\d{5,10})\b", re.IGNORECASE),
        "canonical_field": "Cin",
        "classification": "personnelle",
        "criticite": "elevee",
        "article": "RGPD Art. 4 - Identifiant officiel",
    },
    "medical_info": {
        "regex": re.compile(r"\b(?:groupe sanguin|allergie|traitement medical|medical|sante|handicap|maladie)\b", re.IGNORECASE),
        "canonical_field": "donnees_sante",
        "classification": "sensible",
        "criticite": "elevee",
        "article": "RGPD Art. 9 - Donnees de sante",
    },
    "gps_coords": {
        "regex": re.compile(r"\b-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}\b"),
        "canonical_field": "localisation_GPS",
        "classification": "sensible",
        "criticite": "elevee",
        "article": "RGPD Art. 9 - Localisation",
    },
    "nss": {
        "regex": re.compile(r"\b[12]\s?\d{2}(?:\s?\d{2}){2}\s?\d{3}\s?\d{3}\s?\d{2}\b"),
        "canonical_field": "nss",
        "classification": "sensible",
        "criticite": "critique",
        "article": "RGPD Art. 9 - NSS / NIR",
    },
}

FIELD_NAME_RISK_HINTS = {
    "donnees_bancaires": ["creditcard", "credit_card", "cardnumber", "card_number", "bankcard", "iban", "rib", "paymentcard", "payment_card"],
    "donnees_sante": ["medical", "health", "sante", "allergy", "allergie", "handicap", "bloodgroup"],
    "localisation_GPS": ["gps", "latitude", "longitude", "location", "geoloc", "geo"],
    "Cin": ["cin", "passport", "passportnumber", "idcard", "identitycard"],
}

# ===============================
# QALITAS FIELD DETECTION
# ===============================

QALITAS_PERSONAL_FIELDS = {
    "Civility", "CivilityLabel", "CivilityStr", "FirstName", "LastName", "FullName",
    "Name", "ContactName", "ContactFirstName",
    "Email", "Phone", "Mobile", "Fax", "Website", "WebSite",
    "Address", "Address1", "Address2", "City", "ZipCode",
    "Latitude", "Longitude", "GPS",
    "JobTitle", "Function", "Department", "EmployeeCode", "MatriculeRH",
    "CIN", "PassportNumber", "RegistrationNumber",
    "SiegeEmail", "SiegePhoneNumber", "SiegeFax", "SiegeAddress",
    "SiegeCity", "SiegeZipCode",
    "FactoryEmail", "FactoryPhoneNumber", "FactoryAddress",
    "FactoryCity", "FactoryZipCode",
    "DeliveryEmail", "DeliveryPhoneNumber", "DeliveryAddress",
    "DeliveryCity", "DeliveryZipCode",
    "BillingEmail", "BillingPhoneNumber", "BillingAddress",
    "BillingCity", "BillingZipCode",
    "SharedWithNames",
    "RequestByFullName", "DecisionMakers", "RequestByEmail",
    "ResponsibleName", "ResponsibleNames", "ResourceNeedsResponsibles",
}

QALITAS_SENSITIVE_FIELDS = {
    "HealthStatus", "MedicalData", "Disability", "AccidentData",
    "SSTData", "WorkAccident", "Fingerprint", "FaceData", "BiometricData",
    "Religion", "PoliticalOpinion", "UnionMembership",
}

QALITAS_NON_PERSONAL = {
    "Id", "id", "Code", "Nature", "Type", "Category", "Status",
    "CreatedAt", "UpdatedAt", "IsActive", "IsEnabled", "SortOrder",
    "CompanyId", "SiteId", "GroupId", "ActivityId", "Version",
    "RowVersion", "IsDeleted", "IsSystem", "Source",
}

QALITAS_MODULE_PROFILES = {
    "customers": {
        "nom_traitement": "Gestion des clients QALITAS",
        "finalite": "Gestion de la relation client, suivi des commandes et reclamations",
        "base_legale": "contrat",
        "personnes_concernees": ["clients", "contacts clients"],
        "risque_eleve": False, "transfert_etranger": False, "donnees_minimisees": True,
    },
    "suppliers": {
        "nom_traitement": "Gestion des fournisseurs QALITAS",
        "finalite": "Gestion des achats, evaluation et suivi des fournisseurs",
        "base_legale": "contrat",
        "personnes_concernees": ["fournisseurs", "contacts fournisseurs"],
        "risque_eleve": False, "transfert_etranger": False, "donnees_minimisees": True,
    },
    "employees": {
        "nom_traitement": "Gestion des ressources humaines QALITAS",
        "finalite": "Gestion du personnel, formations, habilitations et competences",
        "base_legale": "contrat",
        "personnes_concernees": ["employes", "techniciens", "managers"],
        "risque_eleve": False, "transfert_etranger": False,
        "donnees_minimisees": False, "donnees_sensibles": False,
    },
    "audits": {
        "nom_traitement": "Gestion des audits QALITAS",
        "finalite": "Planification et suivi des audits internes et externes",
        "base_legale": "obligation_legale",
        "personnes_concernees": ["auditeurs", "audites", "responsables"],
        "risque_eleve": False, "transfert_etranger": False,
    },
    "actions": {
        "nom_traitement": "Gestion des actions correctives QALITAS",
        "finalite": "Suivi des actions correctives, preventives et d amelioration",
        "base_legale": "interet_legitime",
        "personnes_concernees": ["responsables actions", "pilotes"],
        "risque_eleve": False, "transfert_etranger": False,
    },
    "nonconf": {
        "nom_traitement": "Gestion des non-conformites QALITAS",
        "finalite": "Detection, traitement et suivi des non-conformites",
        "base_legale": "obligation_legale",
        "personnes_concernees": ["detecteurs NC", "responsables traitement"],
        "risque_eleve": False, "transfert_etranger": False,
    },
    "companies": {
        "nom_traitement": "Gestion des societes QALITAS",
        "finalite": "Administration des societes, entites et coordonnees de contact",
        "base_legale": "interet_legitime",
        "personnes_concernees": ["representants societes", "contacts societes"],
        "risque_eleve": False, "transfert_etranger": False, "donnees_minimisees": True,
    },
    "sites": {
        "nom_traitement": "Gestion des sites QALITAS",
        "finalite": "Administration des sites, implantations et coordonnees associees",
        "base_legale": "interet_legitime",
        "personnes_concernees": ["contacts sites", "responsables locaux"],
        "risque_eleve": False, "transfert_etranger": False, "donnees_minimisees": True,
    },
}

GMAO_MODULE_PROFILES = {
    "customers": {
        "nom_traitement": "Gestion des clients GMAO PRO",
        "finalite": "Gestion de la relation client, suivi des interventions et contrats",
        "base_legale": "contrat",
        "personnes_concernees": ["clients", "contacts clients"],
        "risque_eleve": False, "transfert_etranger": False, "donnees_minimisees": True,
    },
    "suppliers": {
        "nom_traitement": "Gestion des fournisseurs GMAO PRO",
        "finalite": "Gestion des fournisseurs, prestataires et sous-traitants de maintenance",
        "base_legale": "contrat",
        "personnes_concernees": ["fournisseurs", "contacts fournisseurs", "sous-traitants"],
        "risque_eleve": False, "transfert_etranger": False, "donnees_minimisees": True,
    },
    "resource_needs": {
        "nom_traitement": "Gestion des besoins en ressources GMAO PRO",
        "finalite": "Pilotage des demandes internes de ressources, affectations et validations de maintenance",
        "base_legale": "interet_legitime",
        "personnes_concernees": ["demandeurs internes", "decideurs", "responsables maintenance"],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": False,
    },
    "organization_chart": {
        "nom_traitement": "Gestion de l'organigramme maintenance GMAO PRO",
        "finalite": "Cartographie des rôles, rattachements hiérarchiques et responsabilités de maintenance",
        "base_legale": "interet_legitime",
        "personnes_concernees": ["managers maintenance", "responsables maintenance", "techniciens", "ouvriers"],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "meeting_actions": {
        "nom_traitement": "Gestion des actions issues des réunions GMAO PRO",
        "finalite": "Suivi des actions, responsables et engagements issus des réunions de maintenance",
        "base_legale": "interet_legitime",
        "personnes_concernees": ["responsables d'actions", "participants aux réunions", "managers maintenance"],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "meetings": {
        "nom_traitement": "Gestion des réunions GMAO PRO",
        "finalite": "Planification des réunions, traçabilité des échanges et suivi des responsables",
        "base_legale": "interet_legitime",
        "personnes_concernees": ["participants", "responsables", "managers maintenance"],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "maintenance_teams": {
        "nom_traitement": "Gestion des équipes de maintenance GMAO PRO",
        "finalite": "Organisation des équipes, rattachement des intervenants et pilotage des commentaires d'équipe",
        "base_legale": "interet_legitime",
        "personnes_concernees": ["techniciens", "chefs d'équipe", "intervenants maintenance"],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "qualifications": {
        "nom_traitement": "Référentiel des qualifications GMAO PRO",
        "finalite": "Référencer les métiers, habilitations et domaines de qualification de maintenance",
        "base_legale": "interet_legitime",
        "personnes_concernees": ["techniciens", "ouvriers", "maintenance"],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "equipments": {
        "nom_traitement": "Gestion des équipements GMAO PRO",
        "finalite": "Inventaire des équipements, machines et identifiants techniques de maintenance",
        "base_legale": "interet_legitime",
        "personnes_concernees": [],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "toolings": {
        "nom_traitement": "Gestion des outillages GMAO PRO",
        "finalite": "Inventaire des outillages et suivi de leur état de disponibilité",
        "base_legale": "interet_legitime",
        "personnes_concernees": [],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "maintenance_operations": {
        "nom_traitement": "Référentiel des opérations de maintenance GMAO PRO",
        "finalite": "Référencer les opérations et catégories de maintenance",
        "base_legale": "interet_legitime",
        "personnes_concernees": [],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "maintenance_ranges": {
        "nom_traitement": "Gestion des gammes de maintenance GMAO PRO",
        "finalite": "Planification des gammes, fréquences et ressources de maintenance",
        "base_legale": "interet_legitime",
        "personnes_concernees": ["responsables maintenance", "techniciens"] ,
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "articles": {
        "nom_traitement": "Catalogue des articles GMAO PRO",
        "finalite": "Gestion du stock d'articles, pièces et références utilisées en maintenance",
        "base_legale": "interet_legitime",
        "personnes_concernees": [],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "purchase_requests": {
        "nom_traitement": "Demandes d'achat GMAO PRO",
        "finalite": "Pilotage des demandes d'achat, demandeurs et circuits de validation",
        "base_legale": "interet_legitime",
        "personnes_concernees": ["demandeurs", "validateurs", "contacts fournisseurs"],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "purchase_orders": {
        "nom_traitement": "Commandes d'achat GMAO PRO",
        "finalite": "Suivi des commandes, références d'achat et fournisseurs de maintenance",
        "base_legale": "contrat",
        "personnes_concernees": ["acheteurs", "contacts fournisseurs"],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "supplier_contracts": {
        "nom_traitement": "Contrats fournisseurs GMAO PRO",
        "finalite": "Suivi des contrats fournisseurs et prestataires de maintenance",
        "base_legale": "contrat",
        "personnes_concernees": ["contacts fournisseurs", "prestataires", "acheteurs"],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "purchase_invoices": {
        "nom_traitement": "Factures d'achat GMAO PRO",
        "finalite": "Suivi comptable des factures et pièces d'achat liées à la maintenance",
        "base_legale": "obligation_legale",
        "personnes_concernees": ["contacts fournisseurs", "acheteurs"],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
    "calculation_needs": {
        "nom_traitement": "Calcul des besoins GMAO PRO",
        "finalite": "Anticipation des besoins de stock et planification des besoins de maintenance",
        "base_legale": "interet_legitime",
        "personnes_concernees": [],
        "risque_eleve": False,
        "transfert_etranger": False,
        "donnees_minimisees": True,
    },
}

QALITAS_ORGANIZATION_MODULES = {"customers", "suppliers", "companies", "sites"}

# GMAO customer/supplier endpoints are organization records by default.
# They enter RGPD scope only when the payload contains explicit person/contact
# fields or a record clearly represents a natural person.
GMAO_ORGANIZATION_MODULES = {"customers", "suppliers"}
GMAO_ASSET_MODULES = {"equipments", "toolings"}
GMAO_REFERENCE_MODULES = {"qualifications", "maintenance_operations", "maintenance_ranges", "articles", "calculation_needs"}
GMAO_WORKFLOW_MODULES = {
    "resource_needs",
    "organization_chart",
    "meeting_actions",
    "meetings",
    "maintenance_teams",
    "purchase_requests",
    "purchase_orders",
    "supplier_contracts",
    "purchase_invoices",
}
GMAO_STRICT_PERSON_MODULES = GMAO_ASSET_MODULES | GMAO_REFERENCE_MODULES | GMAO_WORKFLOW_MODULES
GMAO_EXPLICIT_PERSON_FIELDS = {
    "FirstName", "LastName", "FullName", "ContactName",
    "ContactFirstName", "ContactLastName", "EmployeeName",
    "EmployeeFullName", "TechnicianName", "TechnicianFullName",
    "RequestByFullName", "DecisionMakers", "ResponsibleName",
    "ResponsibleNames", "ResourceNeedsResponsibles", "SharedWithNames",
    "AcceptedBy", "RejectedBy",
}
GMAO_CONTEXT_PERSON_FIELDS = {
    "EmployeeFullName", "EmployeeName", "TechnicianName", "TechnicianFullName",
    "RequestByFullName", "DecisionMakers", "ResponsibleName", "ResponsibleNames",
    "ResourceNeedsResponsibles", "SharedWithNames", "AcceptedBy", "RejectedBy",
    "ContactName", "ContactFirstName", "ContactLastName", "FullName",
    "FirstName", "LastName",
}
GMAO_TECHNICAL_METADATA_FIELDS = {
    "CreatedBy", "UpdatedBy", "CurrentEmployeeId", "CurrentUserId", "CrudFrom",
    "CompanyId", "SiteId", "EmployeeId", "EmployeeSerialNumber", "SharedWith",
    "Id", "ParentId", "Level", "CRUD", "IsShared", "IsSystem", "IsBookmark",
}
GMAO_NON_PERSON_DESIGNATION_FIELDS = {
    "Designation", "FullDesignation", "Name", "DisplayName", "Title",
    "QualificationStr", "Qualifications", "CategoryDesignation",
    "CategoryFullDesignation", "TypesDesignation", "TypesFullDesignation",
    "DomainsDesignation", "DomainsFullDesignation", "OperationDesignation",
    "OperationFullDesignation", "EquipmentDesignation", "EquipmentFullDesignation",
    "StructureDesignation", "StructureFullDesignation", "FamilyDesignation",
    "FamilyFullDesignation", "NatureStr", "Nature", "Description",
}
GMAO_ORGANIZATION_FIELDS = {
    "Designation", "FullDesignation", "Name", "Civility", "CivilityStr",
    "Nature", "NatureStr", "Code", "InternalReference",
    "TypesCode", "TypesDesignation", "TypesFullDesignation",
    "CategoryCode", "CategoryDesignation", "CategoryFullDesignation",
    "Sector", "ExpLocStr",
    "SiegeAddress", "SiegeCity", "SiegeZipCode", "SiegeEmail",
    "SiegePhoneNumber", "SiegeFax",
    "FactoryAddress", "FactoryCity", "FactoryZipCode", "FactoryEmail",
    "FactoryPhoneNumber", "FactoryFax",
    "DeliveryAddress", "DeliveryCity", "DeliveryZipCode", "DeliveryEmail",
    "DeliveryPhoneNumber", "DeliveryFax",
    "BillingAddress", "BillingCity", "BillingZipCode", "BillingEmail",
    "BillingPhoneNumber", "BillingFax",
}
GMAO_DIRECT_PERSON_CONTACT_FIELDS = {"Email", "Phone", "Mobile", "Fax", "Gsm"}
GMAO_ORGANIZATION_FOOTPRINT_FIELDS = {
    "WebSite", "Website", "TaxCode", "Iban", "Rib", "Siret", "VatCode", "BankingDomiciliation",
    "SiegeAddress", "SiegeCity", "SiegeZipCode", "SiegeEmail", "SiegePhoneNumber", "SiegeFax",
    "FactoryAddress", "FactoryCity", "FactoryZipCode", "FactoryEmail", "FactoryPhoneNumber", "FactoryFax",
    "DeliveryAddress", "DeliveryCity", "DeliveryZipCode", "DeliveryEmail", "DeliveryPhoneNumber", "DeliveryFax",
    "BillingAddress", "BillingCity", "BillingZipCode", "BillingEmail", "BillingPhoneNumber", "BillingFax",
}

QALITAS_EXPLICIT_PERSON_FIELDS = {
    "FirstName", "LastName", "FullName", "ContactName", "ContactFirstName",
    "RequestByFullName", "DecisionMakers", "ResponsibleName", "ResponsibleNames",
    "SharedWithNames",
}
QALITAS_CONTACT_CHANNEL_FIELDS = {
    "Email", "Phone", "Mobile", "Fax",
    "Address", "Address1", "Address2", "City", "ZipCode",
    "SiegeEmail", "SiegePhoneNumber", "SiegeFax", "SiegeAddress", "SiegeCity", "SiegeZipCode",
    "FactoryEmail", "FactoryPhoneNumber", "FactoryAddress", "FactoryCity", "FactoryZipCode",
    "DeliveryEmail", "DeliveryPhoneNumber", "DeliveryAddress", "DeliveryCity", "DeliveryZipCode",
    "BillingEmail", "BillingPhoneNumber", "BillingAddress", "BillingCity", "BillingZipCode",
}
QALITAS_ENTITY_LABEL_FIELDS = {"Designation", "FullDesignation", "Name", "Civility", "CivilityStr"}
QALITAS_DIRECT_PERSON_CONTACT_FIELDS = {"Email", "Phone", "Mobile", "Fax"}
QALITAS_ORGANIZATION_FOOTPRINT_FIELDS = {
    "WebSite", "Website", "TaxCode", "Iban", "Rib", "Siret", "VatCode", "BankingDomiciliation",
    "SiegeAddress", "SiegeCity", "SiegeZipCode", "SiegeEmail", "SiegePhoneNumber", "SiegeFax",
    "FactoryAddress", "FactoryCity", "FactoryZipCode", "FactoryEmail", "FactoryPhoneNumber", "FactoryFax",
    "DeliveryAddress", "DeliveryCity", "DeliveryZipCode", "DeliveryEmail", "DeliveryPhoneNumber", "DeliveryFax",
    "BillingAddress", "BillingCity", "BillingZipCode", "BillingEmail", "BillingPhoneNumber", "BillingFax",
}

QALITAS_COMPANY_MARKERS = {
    "SARL", "SA", "STE", "SOCIETE", "COMPANY", "LTD", "LLC",
    "INDUSTRIE", "INDUSTRIEL", "TRANSPORT", "IMPRIMERIE",
    "HOTEL", "CLINIQUE", "SERVICES", "CONSULTING", "GROUP",
    "MEUBLES", "COMPTOIR", "QUINCAILLERIE", "TECHNO", "BATIMENT",
    "FRERES", "EQUIPEMENT", "EQUIPEMENTS", "MAISON", "PACKAGING",
    "MODERNE", "COLLE", "RELIURE", "MATERIAUX", "MATERIEL",
    "TELECOM", "EMBALLAGE", "TUBULAIRE", "ETABLISSEMENTS",
    "INDUSTRIES", "INDUSTRIELLE", "COMMERCIAL", "COMMERCIALE",
    "BUREAU", "VERITAS",
    "MAROC", "COMPAGNIE", "PHOSPHATES", "COFFEE", "HOUSE", "CARS",
    "ETS", "ETABLISSEMENT", "ASSIDON", "MAINTSERVICES", "CPG",
    "SYSTEM", "SYSTEMS", "DIGITAL", "DISTRIBUTION", "MOTOR", "MOTORS",
    "MALL", "INGREDIENTS", "FINANCES", "RECETTE", "TUNISIA",
}

_QALITAS_PERSON_CLASSIFICATION_CACHE = {}
_QALITAS_GROQ_CALLS = 0
_QALITAS_GROQ_MAX_CALLS = 20


def _has_meaningful_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, set, tuple)):
        return len(value) > 0
    return True


def _extract_qalitas_display_name(record: dict) -> str:
    """Return a likely human display name without promoting entity labels to people."""
    candidate_fields = [
        "FullName", "SerialNumberFullName", "ContactName", "ContactFirstName",
        "FirstName", "LastName", "EmployeeFullName", "EmployeeName",
    ]

    full_name = str(record.get("FullName", "")).strip()
    first_name = str(record.get("FirstName", "")).strip()
    last_name = str(record.get("LastName", "")).strip()
    combined = " ".join(part for part in [first_name, last_name] if part).strip()

    if full_name:
        return full_name
    if combined:
        return combined

    for field in candidate_fields:
        value = str(record.get(field, "")).strip()
        if value:
            return value
    return ""


def _extract_gmao_person_display_name(record: dict) -> str:
    """Return a human name from GMAO person/context fields when available."""
    first_name = str(record.get("FirstName", "")).strip()
    last_name = str(record.get("LastName", "")).strip()
    combined = " ".join(part for part in [first_name, last_name] if part).strip()
    if combined and _looks_like_person_name(combined):
        return combined

    direct_fields = [
        "FullName", "EmployeeFullName", "EmployeeName", "TechnicianName",
        "TechnicianFullName", "ContactName", "ContactFirstName",
        "ContactLastName", "RequestByFullName", "ResponsibleName",
    ]
    for field in direct_fields:
        value = record.get(field)
        if not _has_meaningful_value(value):
            continue
        if isinstance(value, str):
            cleaned = _strip_record_code_prefix(_strip_html_tags(value)).strip(" ,;:/|-")
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if _looks_like_person_name(cleaned):
                return cleaned
        names = _collect_person_names_from_value(value)
        if names:
            return names[0]

    contextual_fields = [
        "DecisionMakers", "ResponsibleNames", "ResourceNeedsResponsibles",
        "SharedWithNames", "AcceptedBy", "RejectedBy",
    ]
    for field in contextual_fields:
        names = _collect_person_names_from_value(record.get(field))
        if names:
            return names[0]
    return ""


def _extract_gmao_display_name(record: dict, module: str | None, index: int) -> str:
    """Return a useful business label for GMAO records without surfacing noisy filler text."""
    if module == "resource_needs":
        requester = str(
            record.get("RequestByFullName")
            or record.get("RequestByEmail")
            or ""
        ).strip()
        reference = str(record.get("Reference") or "").strip()
        number = str(record.get("Number") or "").strip()
        if requester and reference:
            return f"{requester} - {reference}"
        if requester and number:
            return f"{requester} - #{number}"
        if requester:
            return requester
        if reference:
            return reference
        if number:
            return f"Demande #{number}"
    if module == "organization_chart":
        person = str(record.get("Description") or "").strip()
        role = str(record.get("Title") or record.get("GroupTitle") or "").strip()
        if person and role:
            return f"{person} - {role}"
        if person:
            return person
        if role:
            return role
    if module == "meeting_actions":
        reference = str(record.get("Reference") or "").strip()
        designation = str(record.get("Designation") or record.get("EntitledSource") or "").strip()
        if designation and reference:
            return f"{designation} - {reference}"
        if designation:
            return designation
        if reference:
            return reference
    if module == "meetings":
        meeting_type = str(record.get("MeetingTypeStr") or record.get("Type") or "").strip()
        reference = str(record.get("Reference") or "").strip()
        if meeting_type and reference:
            return f"{meeting_type} - {reference}"
        if meeting_type:
            return meeting_type
        if reference:
            return reference
    if module == "maintenance_teams":
        designation = str(record.get("Designation") or record.get("Code") or "").strip()
        if designation:
            return designation
    if module in GMAO_ASSET_MODULES:
        designation = str(record.get("Designation") or record.get("FullDesignation") or record.get("Code") or "").strip()
        if designation:
            return designation
    if module in GMAO_REFERENCE_MODULES:
        designation = str(record.get("Designation") or record.get("FullDesignation") or record.get("Reference") or record.get("Code") or "").strip()
        if designation:
            return designation

    return str(
        record.get("Designation")
        or record.get("FullDesignation")
        or _extract_qalitas_display_name(record)
        or record.get("Code")
        or f"Record {index + 1}"
    ).strip()


def _looks_like_person_name(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if any(char.isdigit() for char in text):
        return False
    upper = text.upper()
    upper_words = {
        word.strip(".,:;()[]{}")
        for word in upper.replace("-", " ").split()
        if word.strip(".,:;()[]{}")
    }
    if upper_words & QALITAS_COMPANY_MARKERS:
        return False
    parts = [part for part in text.replace("-", " ").split() if part]
    if len(parts) < 2 or len(parts) > 4:
        return False
    alpha_parts = [part for part in parts if any(ch.isalpha() for ch in part)]
    return len(alpha_parts) == len(parts)


def _strip_html_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", str(value or ""))


def _collect_person_names_from_value(value) -> list[str]:
    names = []

    def _add(candidate):
        text = _strip_record_code_prefix(_strip_html_tags(candidate)).strip(" ,;:/|-")
        text = re.sub(r"\s+", " ", text).strip()
        if _looks_like_person_name(text):
            names.append(text)

    def _walk(item):
        if item is None:
            return
        if isinstance(item, str):
            cleaned = _strip_html_tags(item)
            for part in re.split(r"[,\n;|]+", cleaned):
                _add(part)
            return
        if isinstance(item, dict):
            preferred_keys = [
                "FullName", "Name", "DisplayName", "UserName",
                "RequestByFullName", "ResponsibleName", "ResponsibleNames",
            ]
            for key in preferred_keys:
                if _has_meaningful_value(item.get(key)):
                    _walk(item.get(key))
            return
        if isinstance(item, (list, tuple, set)):
            for sub in item:
                _walk(sub)

    _walk(value)

    unique = []
    seen = set()
    for name in names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            unique.append(name)
    return unique


def _looks_like_email(value: str) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text))


def _looks_like_phone(value: str) -> bool:
    text = re.sub(r"\D+", "", str(value or ""))
    return len(text) >= 8


def _gmao_field_contains_person_names(field_name: str, records: list) -> bool:
    for record in records:
        if not _has_meaningful_value(record.get(field_name)):
            continue
        if _collect_person_names_from_value(record.get(field_name)):
            return True
    return False


def _gmao_field_contains_emails(field_name: str, records: list) -> bool:
    return any(
        _looks_like_email(record.get(field_name))
        for record in records
        if _has_meaningful_value(record.get(field_name))
    )


def _gmao_field_contains_phones(field_name: str, records: list) -> bool:
    return any(
        _looks_like_phone(record.get(field_name))
        for record in records
        if _has_meaningful_value(record.get(field_name))
    )


def _strip_record_code_prefix(value: str) -> str:
    text = str(value or "").strip()
    if ":" in text:
        prefix, rest = text.split(":", 1)
        if any(ch.isdigit() for ch in prefix) or len(prefix.strip()) <= 12:
            return rest.strip()
    return text


def _gmao_record_represents_person(record: dict, module: str | None) -> bool:
    """Classify mixed GMAO entity records without treating companies as people."""
    if module in GMAO_ORGANIZATION_MODULES:
        if _gmao_record_is_legal_entity(record, module):
            return False
        contact_name = str(record.get("ContactName") or record.get("ContactFirstName") or "").strip()
        if contact_name and _looks_like_person_name(contact_name):
            return True
        if any(_has_meaningful_value(record.get(field)) for field in ["FirstName", "LastName", "FullName"]):
            return True

        display_name = _strip_record_code_prefix(
            record.get("Designation") or record.get("Name") or record.get("FullDesignation") or ""
        )
        if not display_name or not _looks_like_person_name(display_name):
            return False
        if _record_words(display_name) & QALITAS_COMPANY_MARKERS:
            return False

        civility = str(record.get("CivilityStr") or record.get("Civility") or "").strip().lower()
        has_direct_person_contact = any(
            _has_meaningful_value(record.get(field))
            for field in GMAO_DIRECT_PERSON_CONTACT_FIELDS
        )
        has_org_footprint = any(
            _has_meaningful_value(record.get(field))
            for field in GMAO_ORGANIZATION_FOOTPRINT_FIELDS
        )
        if civility in {"mr", "m.", "mme", "mlle", "mrs", "ms", "miss"} and has_direct_person_contact and _person_name_word_count(display_name) in {2, 3} and not has_org_footprint:
            return True
        if civility in {"mr", "m.", "mme", "mlle", "mrs", "ms", "miss"} and has_direct_person_contact and _person_name_word_count(display_name) in {2, 3}:
            groq_decision = _groq_person_classification(record, source_system="gmao", module=module)
            return groq_decision is True
        return False

    explicit_person_fields = [
        "FirstName", "LastName", "FullName", "ContactName",
        "ContactFirstName", "ContactLastName", "EmployeeName",
        "TechnicianName", "TechnicianFullName",
    ]
    if any(_has_meaningful_value(record.get(field)) for field in explicit_person_fields):
        return True

    if _extract_gmao_person_display_name(record):
        return True

    # For asset/reference/workflow modules, only explicit person context should
    # promote a record to a physical person. A human-looking designation such as
    # an article label or equipment title is not enough on its own.
    if module in GMAO_STRICT_PERSON_MODULES:
        return False

    display_name = _strip_record_code_prefix(
        record.get("Designation") or record.get("Name") or record.get("FullDesignation") or ""
    )
    if not display_name or not _looks_like_person_name(display_name):
        return False

    words = _record_words(display_name)
    if words & QALITAS_COMPANY_MARKERS:
        return False

    civility = str(record.get("CivilityStr") or record.get("Civility") or "").strip().lower()
    person_civility = {"mr", "m.", "mme", "mlle", "mrs", "ms", "miss"}
    return civility in person_civility or _person_name_word_count(display_name) in {2, 3}


def _qalitas_record_has_person_contact(record: dict) -> bool:
    if any(_has_meaningful_value(record.get(field)) for field in ["FirstName", "LastName", "FullName"]):
        return True

    contact_name = str(record.get("ContactName") or record.get("ContactFirstName") or "").strip()
    if contact_name and _looks_like_person_name(contact_name):
        return True

    responsible_values = [
        record.get("DecisionMakers"),
        record.get("ResponsibleName"),
        record.get("ResponsibleNames"),
        record.get("SharedWithNames"),
    ]
    return any(_collect_person_names_from_value(value) for value in responsible_values if _has_meaningful_value(value))


def _gmao_record_is_legal_entity(record: dict, module: str | None = None) -> bool:
    """Detect obvious organizations before promoting GMAO names to natural persons."""
    if module not in GMAO_ORGANIZATION_MODULES:
        return False

    explicit_person_fields = ["FirstName", "LastName", "FullName"]
    if any(_has_meaningful_value(record.get(field)) for field in explicit_person_fields):
        return False

    contact_name = str(record.get("ContactName") or record.get("ContactFirstName") or "").strip()
    if contact_name and _looks_like_person_name(contact_name):
        return False

    civility_text = str(record.get("CivilityStr") or "").strip().lower()
    civility_code = str(record.get("Civility") or "").strip().lower()
    nature_text = str(record.get("NatureStr") or record.get("Nature") or "").strip().lower()
    designation = " ".join(
        str(record.get(field) or "").strip()
        for field in ["Designation", "FullDesignation", "Name"]
        if _has_meaningful_value(record.get(field))
    ).strip()
    designation_words = _record_words(designation)
    has_org_footprint = any(
        _has_meaningful_value(record.get(field))
        for field in GMAO_ORGANIZATION_FOOTPRINT_FIELDS
    )
    has_direct_person_contact = any(
        _has_meaningful_value(record.get(field))
        for field in GMAO_DIRECT_PERSON_CONTACT_FIELDS
    )
    human_civility = civility_text in {"mr", "mme", "mlle", "mrs", "ms", "m."}

    civility_company_values = {"stÃ©", "ste", "societe", "company", "corp", "inc", "ltd", "llc"}
    nature_company_values = {"company", "societe", "supplier", "customer", "client", "fournisseur"}

    if civility_text in civility_company_values:
        return True
    if civility_code in {"3", "societe", "company"}:
        return True
    if nature_text in nature_company_values:
        return True
    if designation_words & QALITAS_COMPANY_MARKERS:
        return True
    if has_org_footprint and not has_direct_person_contact and not human_civility:
        return True
    if has_org_footprint and designation and not _looks_like_person_name(designation):
        return True
    return False


def _qalitas_person_like_records(records: list, module: str | None) -> list:
    return [
        record for record in records
        if _qalitas_record_represents_person(record, module) or _qalitas_record_has_person_contact(record)
    ]


def _qalitas_person_field_ratio(records: list, field_name: str, module: str | None) -> float:
    non_empty_records = [record for record in records if _has_meaningful_value(record.get(field_name))]
    if not non_empty_records:
        return 0.0
    person_like_records = [
        record for record in non_empty_records
        if _qalitas_record_represents_person(record, module) or _qalitas_record_has_person_contact(record)
    ]
    return len(person_like_records) / max(len(non_empty_records), 1)


def _qalitas_record_is_legal_entity(record: dict, module: str | None = None) -> bool:
    """Detect obvious legal entities before promoting labels to natural persons."""
    if module not in QALITAS_ORGANIZATION_MODULES:
        return False

    explicit_person_fields = ["FirstName", "LastName", "FullName"]
    if any(_has_meaningful_value(record.get(field)) for field in explicit_person_fields):
        return False

    contact_name = str(record.get("ContactName") or record.get("ContactFirstName") or "").strip()
    if contact_name and _looks_like_person_name(contact_name):
        return False

    civility_text = str(record.get("CivilityStr") or "").strip().lower()
    civility_code = str(record.get("Civility") or "").strip().lower()
    nature_text = str(record.get("NatureStr") or record.get("Nature") or "").strip().lower()
    category_text = " ".join(
        str(record.get(field) or "").strip()
        for field in ["CategoryDesignation", "CategoryFullDesignation", "CategoryCode", "Sector"]
        if _has_meaningful_value(record.get(field))
    ).lower()
    designation = " ".join(
        str(record.get(field) or "").strip()
        for field in ["Designation", "FullDesignation", "Name"]
        if _has_meaningful_value(record.get(field))
    ).strip()
    designation_words = _record_words(designation)
    has_org_contact_channels = any(
        _has_meaningful_value(record.get(field))
        for field in QALITAS_ORGANIZATION_FOOTPRINT_FIELDS
    )
    has_direct_person_contact = any(
        _has_meaningful_value(record.get(field))
        for field in QALITAS_DIRECT_PERSON_CONTACT_FIELDS
    )
    human_civility = civility_text in {"mr", "mme", "mlle", "mrs", "ms", "m."}

    civility_company_values = {"sté", "ste", "societe", "company", "corp", "inc", "ltd", "llc"}
    nature_company_values = {"company", "societe", "supplier", "customer", "client", "fournisseur"}
    category_company_markers = {"import", "local", "cat-", "industrie", "industriel"}

    if civility_text in civility_company_values:
        return True
    if civility_code in {"3", "societe", "company"}:
        return True
    if nature_text in nature_company_values:
        return True
    if any(marker in category_text for marker in category_company_markers) and not _looks_like_person_name(designation):
        return True
    if designation_words & QALITAS_COMPANY_MARKERS:
        return True
    if has_org_contact_channels and not has_direct_person_contact and not human_civility:
        return True
    if has_org_contact_channels and designation and not _looks_like_person_name(designation):
        return True
    return False


def _qalitas_record_represents_person(record: dict, module: str | None) -> bool:
    if module == "employees":
        return True

    if _qalitas_record_is_legal_entity(record, module):
        return False

    if _qalitas_record_has_person_contact(record):
        return True

    display_name = (
        str(record.get("FullName") or "").strip()
        or str(record.get("Designation") or "").strip()
        or str(record.get("FullDesignation") or "").strip()
        or str(record.get("Name") or "").strip()
        or _extract_qalitas_display_name(record)
    )
    if not display_name or not _looks_like_person_name(display_name):
        return False

    words = _record_words(display_name)
    if words & QALITAS_COMPANY_MARKERS:
        return False

    civility = str(record.get("CivilityStr") or record.get("Civility") or "").strip().lower()
    has_direct_person_contact = any(
        _has_meaningful_value(record.get(field))
        for field in QALITAS_DIRECT_PERSON_CONTACT_FIELDS
    )
    has_organization_footprint = any(
        _has_meaningful_value(record.get(field))
        for field in QALITAS_ORGANIZATION_FOOTPRINT_FIELDS
    )
    if module not in QALITAS_ORGANIZATION_MODULES and civility in {"mr", "mme", "mlle", "mrs", "ms", "m."}:
        return True
    if module in QALITAS_ORGANIZATION_MODULES and civility in {"mr", "mme", "mlle", "mrs", "ms", "m."} and has_direct_person_contact and _person_name_word_count(display_name) in {2, 3} and not has_organization_footprint:
        return True
    if module in QALITAS_ORGANIZATION_MODULES and civility in {"mr", "mme", "mlle", "mrs", "ms", "m."} and has_direct_person_contact and _person_name_word_count(display_name) in {2, 3}:
        groq_decision = _groq_person_classification(record, source_system="qalitas", module=module)
        return groq_decision is True
    return False


def _field_is_qalitas_personal(field_name: str, records: list, module: str | None = None, source_system: str = "qalitas") -> bool:
    field = (field_name or "").strip()
    lower = field.lower()
    normalized = re.sub(r"[^a-z0-9]+", "", lower)

    normalized_personal_fields = {
        re.sub(r"[^a-z0-9]+", "", personal_field.lower())
        for personal_field in QALITAS_PERSONAL_FIELDS
    }
    personal_field_hints = {
        "fullname", "firstname", "lastname", "contactname", "contactfirstname", "contactlastname",
        "email", "mail", "phone", "telephone", "mobile", "fax", "address", "zipcode", "city",
        "latitude", "longitude", "gps", "jobtitle", "function", "department", "employeecode",
        "matriculerh", "cin", "passportnumber", "registrationnumber", "responsiblename",
        "responsiblenames", "decisionmakers", "requestbyfullname", "requestbyemail",
        "sharedwithnames", "resourceneedsresponsibles", "displayname",
    }

    if field in QALITAS_NON_PERSONAL:
        return False

    if normalized in {re.sub(r"[^a-z0-9]+", "", value.lower()) for value in QALITAS_NON_PERSONAL}:
        return False

    if source_system == "gmao" and field in GMAO_TECHNICAL_METADATA_FIELDS:
        return False

    if source_system == "gmao" and module == "all":
        person_records = [
            record for record in records
            if _gmao_record_represents_person(record, module)
        ]
        if field in GMAO_EXPLICIT_PERSON_FIELDS:
            return any(_has_meaningful_value(record.get(field)) for record in person_records)
        if field in GMAO_ORGANIZATION_FIELDS or field in GMAO_NON_PERSON_DESIGNATION_FIELDS:
            return any(
                _has_meaningful_value(record.get(field))
                and (
                    _has_meaningful_value(record.get("FirstName"))
                    or _has_meaningful_value(record.get("LastName"))
                    or _has_meaningful_value(record.get("FullName"))
                    or _has_meaningful_value(record.get("ContactName"))
                    or _has_meaningful_value(record.get("TechnicianName"))
                    or _has_meaningful_value(record.get("EmployeeName"))
                )
                for record in person_records
            )
        if field in GMAO_CONTEXT_PERSON_FIELDS:
            return any(_has_meaningful_value(record.get(field)) for record in person_records)
        if any(token in lower for token in ["email", "phone", "telephone", "mobile", "fax", "address", "adresse"]):
            return any(_has_meaningful_value(record.get(field)) for record in person_records)

    if source_system == "qalitas" and module in QALITAS_ORGANIZATION_MODULES:
        person_records = _qalitas_person_like_records(records, module)
        if field in QALITAS_EXPLICIT_PERSON_FIELDS:
            return any(
                (_qalitas_record_represents_person(record, module) or _qalitas_record_has_person_contact(record))
                and _has_meaningful_value(record.get(field))
                for record in records
            )
        if field in QALITAS_ENTITY_LABEL_FIELDS:
            ratio = _qalitas_person_field_ratio(records, field, module)
            return ratio >= 0.6 and any(
                _qalitas_record_represents_person(record, module)
                and _has_meaningful_value(record.get(field))
                for record in records
            )
        if field in QALITAS_CONTACT_CHANNEL_FIELDS or any(
            token in lower for token in ["email", "phone", "telephone", "mobile", "fax", "address", "adresse", "city", "ville", "zip", "postal"]
        ):
            return any(
                ((_qalitas_record_represents_person(record, module) or _qalitas_record_has_person_contact(record))
                and record in person_records)
                and _has_meaningful_value(record.get(field))
                for record in records
            )
        return False

    if source_system == "gmao" and module in GMAO_ORGANIZATION_MODULES:
        if field in GMAO_EXPLICIT_PERSON_FIELDS:
            return any(
                _gmao_record_represents_person(record, module) and _has_meaningful_value(record.get(field))
                for record in records
            )
        if field in {"Designation", "FullDesignation", "Name", "Civility", "CivilityStr"}:
            return any(
                _gmao_record_represents_person(record, module) and _has_meaningful_value(record.get(field))
                for record in records
            )
        if any(token in lower for token in ["email", "phone", "telephone", "mobile", "address", "adresse"]):
            return any(
                _gmao_record_represents_person(record, module) and _has_meaningful_value(record.get(field))
                for record in records
            )
        if field in GMAO_ORGANIZATION_FIELDS:
            return False
        return False

    if source_system == "gmao" and module == "organization_chart":
        if field == "Description":
            return any(
                _looks_like_person_name(str(record.get(field, "")).strip())
                for record in records
                if _has_meaningful_value(record.get(field))
            )
        if field in {"Title", "Image"}:
            return any(
                _looks_like_person_name(str(record.get("Description", "")).strip())
                and _has_meaningful_value(record.get(field))
                for record in records
            )
        return False

    if source_system == "gmao" and module in GMAO_STRICT_PERSON_MODULES:
        if field in GMAO_CONTEXT_PERSON_FIELDS:
            return _gmao_field_contains_person_names(field, records)
        if "email" in lower:
            return _gmao_field_contains_emails(field, records)
        if any(token in lower for token in ["phone", "telephone", "mobile", "fax"]):
            return _gmao_field_contains_phones(field, records)
        if field in GMAO_NON_PERSON_DESIGNATION_FIELDS:
            return False
        if normalized in {
            "designation", "fulldesignation", "name", "displayname", "title",
            "qualificationstr", "qualifications", "categorydesignation",
            "categoryfulldesignation", "typesdesignation", "typesfulldesignation",
            "domainsdesignation", "domainsfulldesignation", "operationdesignation",
            "operationfulldesignation", "equipmentdesignation", "equipmentfulldesignation",
            "structuredesignation", "structurefulldesignation", "familydesignation",
            "familyfulldesignation", "description", "sharedwithnames",
        }:
            return False
        return False

    if lower.endswith("country"):
        return False

    if len(normalized) <= 2:
        return False

    if lower in {"number", "filename", "file name", "generatedfilename", "generated file name"}:
        return False
    if "file" in lower and "name" in lower:
        return False

    if field in {"Designation", "FullDesignation", "Name"}:
        sample_values = [record.get(field) for record in records[:20] if _has_meaningful_value(record.get(field))]
        if not sample_values:
            return False
        return any(_looks_like_person_name(str(value)) for value in sample_values)

    if normalized in normalized_personal_fields:
        return True

    return any(hint in normalized for hint in personal_field_hints)


def _field_is_qalitas_sensitive(field_name: str) -> bool:
    field = (field_name or "").strip().lower()
    if field in {"id", "companyid", "siteid", "groupid", "activityid"}:
        return False
    return any(
        field == sensitive_field.lower() or field.endswith(sensitive_field.lower())
        for sensitive_field in QALITAS_SENSITIVE_FIELDS
    )


def _record_words(value: str) -> set[str]:
    return {
        word.strip(".,:;()[]{}")
        for word in str(value or "").upper().replace("-", " ").split()
        if word.strip(".,:;()[]{}")
    }


def _person_name_word_count(value: str) -> int:
    return len([part for part in str(value or "").replace("-", " ").split() if part])


def _groq_person_classification(record: dict, source_system: str = "qalitas", module: str | None = None) -> bool | None:
    """
    Ask Groq only for ambiguous records.
    Returns True / False / None on failure.
    """
    global _QALITAS_GROQ_CALLS
    try:
        source_system = str(source_system or "qalitas").lower()
        common_fields = [
            "Designation", "FullDesignation", "Name", "Civility", "CivilityStr",
            "Nature", "NatureStr", "Code",
            "FirstName", "LastName", "FullName", "ContactName", "ContactFirstName", "ContactLastName",
            "EmployeeName", "EmployeeFullName", "TechnicianName", "TechnicianFullName",
            "Email", "Phone", "Mobile", "Fax", "Gsm",
            "SiegeEmail", "BillingEmail", "SiegePhoneNumber", "BillingPhoneNumber",
            "TaxCode", "Iban", "Rib", "Siret",
        ]
        compact_payload = {
            "source_system": source_system,
            "module": module,
            "record": {
                key: record.get(key)
                for key in common_fields
                if _has_meaningful_value(record.get(key))
            },
        }
        cache_key = json.dumps(compact_payload, ensure_ascii=False, sort_keys=True)
        if cache_key in _QALITAS_PERSON_CLASSIFICATION_CACHE:
            return _QALITAS_PERSON_CLASSIFICATION_CACHE[cache_key]

        if _QALITAS_GROQ_CALLS >= _QALITAS_GROQ_MAX_CALLS:
            return None

        client = _get_groq()
        compact = compact_payload["record"]
        if not compact:
            return None
        _QALITAS_GROQ_CALLS += 1
        prompt = (
            f"Tu classes un enregistrement {source_system.upper()} du module {module or 'inconnu'}.\n"
            "Dis uniquement OUI si l enregistrement represente principalement une personne physique.\n"
            "Dis NON si c est principalement une societe, une organisation, un equipement ou un enregistrement metier.\n"
            "Si tu as un doute, reponds NON.\n"
            "Pas d explication.\n"
            f"Enregistrement: {json.dumps(compact, ensure_ascii=False)}"
        )
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=5,
            temperature=0.0,
        )
        answer = (response.choices[0].message.content or "").strip().upper()
        if "OUI" in answer:
            _QALITAS_PERSON_CLASSIFICATION_CACHE[cache_key] = True
            return True
        if "NON" in answer:
            _QALITAS_PERSON_CLASSIFICATION_CACHE[cache_key] = False
            return False
    except Exception:
        return None
    return None


def _is_qalitas_physical_person(record: dict, module: str | None = None, source_system: str = "qalitas") -> bool:
    """
    Return True only when the record looks like a natural person.
    Companies and unclear entities are excluded from the UI.
    """
    if module == "employees":
        return True

    # GMAO modules expose person context in module-specific fields such as
    # RequestByFullName, DecisionMakers, EmployeeFullName or TechnicianName.
    # Delegate the full decision to the GMAO classifier instead of falling back
    # to the more generic QALITAS-style heuristics.
    if source_system == "gmao":
        return _gmao_record_represents_person(record, module)

    if source_system == "qalitas" and module in QALITAS_ORGANIZATION_MODULES:
        if _qalitas_record_is_legal_entity(record, module):
            return False
        return _qalitas_record_represents_person(record, module)

    if any(_has_meaningful_value(record.get(field)) for field in ["FirstName", "LastName", "FullName"]):
        return True

    display_name = (
        str(record.get("Designation") or "").strip()
        or str(record.get("FullDesignation") or "").strip()
        or _extract_qalitas_display_name(record)
    )
    civility = str(record.get("CivilityStr") or record.get("Civility") or "").strip()
    contact_name = str(record.get("ContactName") or "").strip()
    words = _record_words(display_name)

    if words & QALITAS_COMPANY_MARKERS:
        return False

    if module in {"customers", "suppliers", "companies", "sites"}:
        if contact_name and _looks_like_person_name(contact_name):
            return True
        if any(_has_meaningful_value(record.get(field)) for field in ["FirstName", "LastName", "FullName"]):
            return True
        has_direct_person_contact = any(
            _has_meaningful_value(record.get(field))
            for field in QALITAS_DIRECT_PERSON_CONTACT_FIELDS
        )
        has_organization_footprint = any(
            _has_meaningful_value(record.get(field))
            for field in QALITAS_ORGANIZATION_FOOTPRINT_FIELDS
        )
        if display_name and _looks_like_person_name(display_name):
            civility_norm = civility.lower()
            if has_organization_footprint and not has_direct_person_contact:
                return False
            if civility_norm in {"mr", "mme", "mlle", "mrs", "ms", "m."} and has_direct_person_contact and _person_name_word_count(display_name) in {2, 3} and not has_organization_footprint:
                return True
            if civility_norm in {"mr", "mme", "mlle", "mrs", "ms", "m."} and _person_name_word_count(display_name) in {2, 3}:
                groq_decision = _groq_person_classification(record, source_system="qalitas", module=module)
                return groq_decision is True
        return False

    if contact_name and _looks_like_person_name(contact_name):
        return True

    if display_name and _looks_like_person_name(display_name) and civility and any(
        _has_meaningful_value(record.get(field))
        for field in ["SiegeEmail", "BillingEmail", "Email", "Phone", "BillingPhoneNumber", "SiegePhoneNumber"]
    ):
        return True

    if display_name and _looks_like_person_name(display_name) and civility:
        return True

    return False


def _predict_field_category_safe(field_name: str, module: str | None = None, source_system: str = "qalitas") -> dict:
    """Advisory ML/NLP field category. Rules remain the final RGPD decision layer."""
    if predict_field_category is None:
        return {}
    try:
        prediction = predict_field_category(field_name, module=module, source_system=source_system)
        return prediction if isinstance(prediction, dict) else {}
    except Exception as exc:
        logging.warning(f"[Agent A] Field ML classification failed for {field_name}: {exc}")
        return {
            "field": field_name,
            "module": module,
            "source_system": source_system,
            "label": "unavailable",
            "label_display": "ML indisponible",
            "confidence": 0.0,
            "source": "error",
            "reason": str(exc)[:160],
            "alternatives": [],
            "advisory_only": True,
        }


def _stringify_record_value(value, limit: int = 500) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    elif isinstance(value, list):
        text = " ".join(_stringify_record_value(item, limit=120) for item in value[:10])
    elif isinstance(value, dict):
        text = " ".join(
            f"{key} {_stringify_record_value(item, limit=80)}"
            for key, item in list(value.items())[:10]
        )
    else:
        text = str(value)
    return text[:limit]


def _passes_luhn_checksum(number: str) -> bool:
    total = 0
    reverse_digits = list(reversed(number))
    for index, digit_char in enumerate(reverse_digits):
        digit = int(digit_char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _looks_like_bank_card_number(text: str, field_name: str = "", context_text: str = "") -> bool:
    digits = re.sub(r"\D", "", text or "")
    if not 13 <= len(digits) <= 19:
        return False
    if len(set(digits)) == 1:
        return False
    normalized_field = re.sub(r"[^a-z0-9]", "", str(field_name or "").lower())
    search_text = context_text or text or ""
    has_payment_context = any(
        hint in normalized_field
        for hint in FIELD_NAME_RISK_HINTS.get("donnees_bancaires", [])
    ) or bool(
        re.search(r"\b(?:carte|card|cb|credit|payment|paiement|visa|mastercard|amex)\b", search_text, re.IGNORECASE)
    )
    if not _passes_luhn_checksum(digits):
        return False
    return has_payment_context


def _scan_structured_content(records: list, all_fields: list) -> dict:
    findings = []
    detected_canonical_fields = []
    detected_personal_fields = []
    detected_sensitive_fields = []
    detected_critical_fields = []
    detected_field_names = []
    seen_finding_keys = set()
    seen_canonical = set()
    seen_personal = set()
    seen_sensitive = set()
    seen_critical = set()
    seen_detected_field_names = set()

    for field in all_fields:
        normalized = re.sub(r"[^a-z0-9]", "", str(field or "").lower())
        for canonical_field, hints in FIELD_NAME_RISK_HINTS.items():
            if any(hint in normalized for hint in hints):
                if canonical_field not in seen_canonical:
                    seen_canonical.add(canonical_field)
                    detected_canonical_fields.append(canonical_field)
                if field not in seen_detected_field_names:
                    seen_detected_field_names.add(field)
                    detected_field_names.append(field)
                if canonical_field in DONNEES_SENSIBLES:
                    if field not in seen_sensitive:
                        seen_sensitive.add(field)
                        detected_sensitive_fields.append(field)
                elif canonical_field in DONNEES_CRITIQUES:
                    if field not in seen_critical:
                        seen_critical.add(field)
                        detected_critical_fields.append(field)
                else:
                    if field not in seen_personal:
                        seen_personal.add(field)
                        detected_personal_fields.append(field)

    for index, record in enumerate(records):
        for field in all_fields:
            text = _stringify_record_value(record.get(field))
            if not text:
                continue
            for pattern_name, config in STRUCTURED_CONTENT_PATTERNS.items():
                for match in config["regex"].finditer(text):
                    extrait = match.group(0).strip()
                    if pattern_name == "bank_card" and not _looks_like_bank_card_number(extrait, field, text):
                        continue
                    finding_key = (index, field, pattern_name, extrait[:50])
                    if finding_key in seen_finding_keys:
                        continue
                    seen_finding_keys.add(finding_key)
                    findings.append({
                        "record_index": index,
                        "field_name": field,
                        "pattern": pattern_name,
                        "canonical_field": config["canonical_field"],
                        "classification": config["classification"],
                        "criticite": config["criticite"],
                        "article": config["article"],
                        "excerpt": extrait[:120],
                    })
                    canonical_field = config["canonical_field"]
                    if canonical_field not in seen_canonical:
                        seen_canonical.add(canonical_field)
                        detected_canonical_fields.append(canonical_field)
                    if field not in seen_detected_field_names:
                        seen_detected_field_names.add(field)
                        detected_field_names.append(field)
                    if config["classification"] == "sensible":
                        if field not in seen_sensitive:
                            seen_sensitive.add(field)
                            detected_sensitive_fields.append(field)
                    elif config["classification"] == "critique":
                        if field not in seen_critical:
                            seen_critical.add(field)
                            detected_critical_fields.append(field)
                    else:
                        if field not in seen_personal:
                            seen_personal.add(field)
                            detected_personal_fields.append(field)

    risk_summary = {
        "critical_content_detected": any(
            item.get("classification") == "critique"
            or item.get("canonical_field") in DONNEES_CRITIQUES
            for item in findings
        ),
        "sensitive_content_detected": any(
            item.get("classification") == "sensible"
            or item.get("canonical_field") in DONNEES_SENSIBLES
            for item in findings
        ),
    }
    risk_summary["requires_manual_review"] = (
        risk_summary["critical_content_detected"] or risk_summary["sensitive_content_detected"]
    )

    return {
        "findings": findings,
        "detected_canonical_fields": detected_canonical_fields,
        "detected_personal_fields": detected_personal_fields,
        "detected_sensitive_fields": detected_sensitive_fields,
        "detected_critical_fields": detected_critical_fields,
        "detected_field_names": detected_field_names,
        "risk_summary": risk_summary,
    }


def _summarize_qalitas_record(record: dict, personal_fields: list, sensitive_fields: list, index: int, module: str | None = None, source_system: str = "qalitas", field_ml: dict | None = None) -> dict | None:
    present_personal = [field for field in personal_fields if _has_meaningful_value(record.get(field))]
    present_sensitive = [field for field in sensitive_fields if _has_meaningful_value(record.get(field))]
    if not present_personal and not present_sensitive:
        return None

    effective_module = record.get("_source_module") if isinstance(record, dict) and record.get("_source_module") else module
    is_personne_physique = _is_qalitas_physical_person(record, effective_module, source_system=source_system)
    if source_system == "gmao":
        display_name = (
            _extract_gmao_person_display_name(record)
            if is_personne_physique else ""
        ) or _extract_gmao_display_name(record, effective_module, index)
    else:
        display_name = _extract_qalitas_display_name(record) or record.get("Designation") or record.get("Code") or f"Record {index + 1}"
    preview_values = {}
    for field in (present_personal + present_sensitive)[:6]:
        value = record.get(field)
        if isinstance(value, str):
            preview_values[field] = value[:80]
        else:
            preview_values[field] = value
    ml_subset = {
        field: field_ml[field]
        for field in (present_personal + present_sensitive)
        if field_ml and field in field_ml
    }

    return {
        "record_index": index,
        "display_name": str(display_name).strip(),
        "is_personne_physique": is_personne_physique,
        "personal_fields": present_personal,
        "sensitive_fields": present_sensitive,
        "ml_field_classification": ml_subset,
        "preview_values": preview_values,
    }


def _extract_named_record_label(record: dict, index: int) -> str:
    return str(
        record.get("Designation")
        or record.get("FullDesignation")
        or record.get("Name")
        or record.get("Code")
        or f"Record {index + 1}"
    ).strip()


def _summarize_gmao_organization_record(record: dict, index: int, field_ml: dict | None = None) -> dict | None:
    display_name = _extract_named_record_label(record, index)
    if not display_name:
        return None
    preview_fields = [
        "Code", "Designation", "FullDesignation", "CivilityStr",
        "TypesDesignation", "CategoryDesignation", "SiegeCity", "ExpLocStr",
    ]
    preview_values = {
        field: record.get(field)
        for field in preview_fields
        if _has_meaningful_value(record.get(field))
    }
    organization_fields = list(preview_values.keys())
    ml_subset = {
        field: field_ml[field]
        for field in organization_fields
        if field_ml and field in field_ml
    }
    return {
        "record_index": index,
        "display_name": display_name,
        "is_personne_physique": False,
        "personal_fields": [],
        "sensitive_fields": [],
        "organization_fields": organization_fields,
        "ml_field_classification": ml_subset,
        "preview_values": preview_values,
    }


def detect_qalitas_fields(records: list, module: str | None = None, source_system: str = "qalitas") -> dict:
    """Detect personal/sensitive fields from raw QALITAS records."""
    global _QALITAS_GROQ_CALLS
    _QALITAS_GROQ_CALLS = 0
    if not records:
        return {"personal_fields": [], "sensitive_fields": [], "all_fields": [], 
                "sample_record": {}, "has_individual_names": False, "record_count": 0,
                "records_with_personal_data": 0, "affected_clients": [], "affected_clients_count": 0,
                "named_records": [], "named_records_count": 0,
                "records_details": [], "person_categories": [], "person_categories_display": [],
                "module": module, "source_system": source_system, "ml_field_classification": {}}

    all_fields = []
    seen_fields = set()
    for record in records:
        for key in record.keys():
            if key not in seen_fields:
                seen_fields.add(key)
                all_fields.append(key)

    ml_field_classification = {}
    for field in all_fields:
        prediction = _predict_field_category_safe(field, module=module, source_system=source_system)
        if prediction:
            ml_field_classification[field] = prediction

    detected_personal = []
    detected_sensitive = []
    for field in all_fields:
        f = field.strip()
        if _field_is_qalitas_personal(f, records, module=module, source_system=source_system):
            detected_personal.append(f)
        elif _field_is_qalitas_sensitive(f):
            detected_sensitive.append(f)

    structured_content_scan = _scan_structured_content(records, all_fields)
    for field in structured_content_scan.get("detected_personal_fields", []):
        if field not in detected_personal and field not in detected_sensitive:
            detected_personal.append(field)
    for field in structured_content_scan.get("detected_sensitive_fields", []):
        if field not in detected_sensitive:
            if field in detected_personal:
                detected_personal.remove(field)
            detected_sensitive.append(field)
    for field in structured_content_scan.get("detected_critical_fields", []):
        if field in detected_personal:
            detected_personal.remove(field)
        if field not in detected_sensitive:
            detected_sensitive.append(field)
    for canonical_field in structured_content_scan.get("detected_canonical_fields", []):
        if canonical_field in DONNEES_SENSIBLES:
            if canonical_field not in detected_sensitive:
                detected_sensitive.append(canonical_field)
        elif canonical_field in DONNEES_CRITIQUES:
            if canonical_field not in detected_sensitive:
                detected_sensitive.append(canonical_field)
        else:
            if canonical_field not in detected_personal and canonical_field not in detected_sensitive:
                detected_personal.append(canonical_field)
    has_individual_names = any(
        str(r.get("Civility", r.get("CivilityLabel", ""))).lower()
        in ["mr", "mrs", "ms", "mme", "m.", "mlle"]
        for r in records[:20]
    )

    records_with_personal_data = 0
    affected_clients = []
    named_records = []
    records_details = []
    for index, record in enumerate(records):
        record_summary = _summarize_qalitas_record(
            record,
            detected_personal,
            detected_sensitive,
            index,
            module,
            source_system=source_system,
            field_ml=ml_field_classification,
        )
        if not record_summary and source_system == "gmao" and module in GMAO_ORGANIZATION_MODULES:
            record_summary = _summarize_gmao_organization_record(record, index, field_ml=ml_field_classification)
        if record_summary:
            record_findings = [
                finding for finding in structured_content_scan.get("findings", [])
                if finding.get("record_index") == index
            ]
            if record_findings:
                record_summary["content_findings"] = record_findings[:8]
            if record_summary.get("personal_fields") or record_summary.get("sensitive_fields"):
                records_with_personal_data += 1
            elif record_findings:
                records_with_personal_data += 1
            display_name = record_summary["display_name"]
            if display_name:
                named_records.append(display_name)
                if record_summary.get("is_personne_physique"):
                    affected_clients.append(display_name)
            records_details.append(record_summary)

    unique_affected_clients = []
    seen_clients = set()
    for name in affected_clients:
        normalized = name.lower()
        if normalized not in seen_clients:
            seen_clients.add(normalized)
            unique_affected_clients.append(name)

    unique_named_records = []
    seen_named_records = set()
    for name in named_records:
        normalized = name.lower()
        if normalized not in seen_named_records:
            seen_named_records.add(normalized)
            unique_named_records.append(name)

    profile = QALITAS_MODULE_PROFILES.get(module, {}) if module else {}
    person_categories = profile.get("personnes_concernees", []) or []
    if module == "all":
        person_categories_display = [
            person for person in person_categories
            if person
        ]
    else:
        person_categories_display = []

    return {
        "personal_fields": detected_personal,
        "sensitive_fields": detected_sensitive,
        "all_fields": all_fields,
        "module": module,
        "source_system": source_system,
        "sample_record": records[0],
        "has_individual_names": has_individual_names,
        "record_count": len(records),
        "records_with_personal_data": records_with_personal_data,
        "affected_clients": unique_affected_clients,
        "affected_clients_count": len(unique_affected_clients),
        "named_records": unique_named_records,
        "named_records_count": len(unique_named_records),
        "records_details": records_details,
        "physical_person_records_count": len(unique_affected_clients),
        "person_categories": person_categories,
        "person_categories_display": person_categories_display,
        "ml_field_classification": ml_field_classification,
        "structured_content_findings": structured_content_scan.get("findings", []),
        "structured_content_detected_fields": structured_content_scan.get("detected_field_names", []),
        "structured_content_detected_canonical_fields": structured_content_scan.get("detected_canonical_fields", []),
        "structured_content_risk_summary": structured_content_scan.get("risk_summary", {}),
    }


def _merge_qalitas_profiles(module: str, qalitas_modules: list | None = None) -> dict:
    if module != "all":
        return QALITAS_MODULE_PROFILES.get(module, {})

    merged = {
        "nom_traitement": "Traitement QALITAS multi-modules",
        "finalite": "Analyse consolidee multi-modules QALITAS",
        "base_legale": False,
        "personnes_concernees": [],
        "transfert_etranger": False,
        "risque_eleve": False,
        "donnees_minimisees": True,
        "donnees_sensibles": False,
    }
    module_ids = [m.get("id") for m in (qalitas_modules or []) if m.get("id")]
    base_candidates = set()
    finalites = []
    names = []
    minimization_flags = []
    for module_id in module_ids:
        profile = QALITAS_MODULE_PROFILES.get(module_id, {})
        merged["transfert_etranger"] = merged["transfert_etranger"] or profile.get("transfert_etranger", False)
        merged["risque_eleve"] = merged["risque_eleve"] or profile.get("risque_eleve", False)
        merged["donnees_sensibles"] = merged["donnees_sensibles"] or profile.get("donnees_sensibles", False)
        minimization_flags.append(profile.get("donnees_minimisees", True))
        if profile.get("base_legale"):
            base_candidates.add(profile.get("base_legale"))
        if profile.get("nom_traitement"):
            names.append(profile.get("nom_traitement"))
        if profile.get("finalite"):
            finalites.append(profile.get("finalite"))
        for person in profile.get("personnes_concernees", []) or []:
            if person not in merged["personnes_concernees"]:
                merged["personnes_concernees"].append(person)
    if len(base_candidates) == 1:
        merged["base_legale"] = next(iter(base_candidates))
    if len(set(names)) == 1 and names:
        merged["nom_traitement"] = names[0]
    if len(set(finalites)) == 1 and finalites:
        merged["finalite"] = finalites[0]
    # Keep global source reads neutral when module profiles disagree.
    if minimization_flags and all(flag is False for flag in minimization_flags):
        merged["donnees_minimisees"] = False
    else:
        merged["donnees_minimisees"] = True
    return merged


def _merge_gmao_profiles(module: str, gmao_modules: list | None = None) -> dict:
    if module != "all":
        return GMAO_MODULE_PROFILES.get(module, {})

    merged = {
        "nom_traitement": "Traitement GMAO multi-modules",
        "finalite": "Analyse consolidee multi-modules GMAO PRO",
        "base_legale": False,
        "personnes_concernees": [],
        "transfert_etranger": False,
        "risque_eleve": False,
        "donnees_minimisees": True,
        "donnees_sensibles": False,
    }
    module_ids = [m.get("id") for m in (gmao_modules or []) if m.get("id")]
    base_candidates = set()
    finalites = []
    names = []
    minimization_flags = []
    for module_id in module_ids:
        profile = GMAO_MODULE_PROFILES.get(module_id, {})
        merged["transfert_etranger"] = merged["transfert_etranger"] or profile.get("transfert_etranger", False)
        merged["risque_eleve"] = merged["risque_eleve"] or profile.get("risque_eleve", False)
        merged["donnees_sensibles"] = merged["donnees_sensibles"] or profile.get("donnees_sensibles", False)
        minimization_flags.append(profile.get("donnees_minimisees", True))
        if profile.get("base_legale"):
            base_candidates.add(profile.get("base_legale"))
        if profile.get("nom_traitement"):
            names.append(profile.get("nom_traitement"))
        if profile.get("finalite"):
            finalites.append(profile.get("finalite"))
        for person in profile.get("personnes_concernees", []) or []:
            if person not in merged["personnes_concernees"]:
                merged["personnes_concernees"].append(person)
    if len(base_candidates) == 1:
        merged["base_legale"] = next(iter(base_candidates))
    if len(set(names)) == 1 and names:
        merged["nom_traitement"] = names[0]
    if len(set(finalites)) == 1 and finalites:
        merged["finalite"] = finalites[0]
    if minimization_flags and all(flag is False for flag in minimization_flags):
        merged["donnees_minimisees"] = False
    else:
        merged["donnees_minimisees"] = True
    return merged


def _build_person_categories_display(module: str, profile: dict) -> list:
    if module == "all":
        return profile.get("personnes_concernees", []) or []
    return []


def _default_retention_period(module: str, systeme: str = "") -> str:
    module = (module or "").lower()
    if module == "employees":
        return "5 ans apres fin de relation de travail"
    if module in {"customers", "suppliers", "purchase_orders", "supplier_contracts", "purchase_invoices"}:
        return "5 ans apres fin de relation contractuelle"
    if module in {"audits", "actions", "nonconf"}:
        return "10 ans"
    if module in {"meetings", "meeting_actions", "resource_needs", "maintenance_teams"}:
        return "3 ans"
    if module in {"equipments", "toolings", "maintenance_operations", "maintenance_ranges", "articles", "calculation_needs"}:
        return "Selon cycle de vie de l actif et politique de maintenance"
    if "GMAO" in systeme:
        return "Selon politique de maintenance et obligations contractuelles"
    return "Selon politique interne de conservation"


def _build_source_derived_defaults(
    module: str,
    profile: dict,
    detection: dict,
    systeme: str,
    no_direct_personal_data: bool,
) -> dict:
    content_risk_summary = detection.get("structured_content_risk_summary", {}) or {}
    has_sensitive = len(detection.get("sensitive_fields", []) or []) > 0 or profile.get("donnees_sensibles", False)
    content_review_required = bool(content_risk_summary.get("requires_manual_review"))
    critical_content_detected = bool(content_risk_summary.get("critical_content_detected"))
    sensitive_content_detected = bool(content_risk_summary.get("sensitive_content_detected"))
    workflow_or_people = bool(profile.get("personnes_concernees")) or not no_direct_personal_data
    has_external_processing = bool(profile.get("transfert_etranger")) or module in {"suppliers", "supplier_contracts", "purchase_orders", "purchase_invoices"}
    mesures_securite = ["controle_acces", "journalisation", "sauvegarde"]
    if has_sensitive and not content_review_required:
        mesures_securite.append("chiffrement")
    return {
        # For source-derived treatments, a profile basis is only a presumption until
        # the DPO confirms and documents it. Keeping this field false avoids showing
        # a fully compliant Q2 score while Q3 still says "a valider".
        "base_legale": False,
        "duree_conservation": _default_retention_period(module, systeme),
        "duree_conservation_definie": True,
        "duree_depassee": False,
        "mesures_securite": mesures_securite,
        "privacy_by_design": True,
        "privacy_by_default": True,
        "processus_droits_personnes": workflow_or_people,
        "information_personnes_concernees": workflow_or_people,
        "modalites_droits_accessibles": workflow_or_people,
        "consentement_valide": False,
        "consentement_retire": False,
        "violation_donnees": False,
        "notification_72h": False,
        "notification_personnes": False,
        "violation_documentee": False,
        "aipd_realisee": False,
        "mise_en_production": True,
        "analyse_risque_avant_production": True,
        "garanties_specifiques": has_sensitive and not content_review_required,
        "respect_vie_privee": True,
        "declaration_inpdp": True,
        "registre_traitement": True,
        "politique_protection_donnees": True,
        "revue_periodique_mesures": not content_review_required,
        "tests_securite_reguliers": not content_review_required,
        "controle_acces_physique": True,
        "confidentialite_post_traitement": True,
        "contrat_sous_traitance": True if has_external_processing else False,
        "garanties_sous_traitant": True if has_external_processing else False,
        "collecte_indirecte": False,
        "information_collecte_indirecte": False,
        "consentement_collecte_indirecte": False,
        "information_transfert_fournie": bool(profile.get("transfert_etranger")),
        "opposition_ignoree": False,
        "decision_automatisee": False,
        "garanties_decision_auto": False,
        "dsar_hors_delai": False,
        "traitement_grande_echelle": module == "employees" and detection.get("record_count", 0) >= 100,
        "dpo_designe": True,
        "missions_dpo_garanties": True,
        "adequation_ou_garanties_documentees": not profile.get("transfert_etranger", False),
        "autorisation_inpdp_transfert": not profile.get("transfert_etranger", False),
        "niveau_protection_adequat": True,
        "risque_securite_nationale": False,
        "chiffrement_actif": True if has_sensitive and not content_review_required else False,
        "security_review_required": content_review_required,
        "critical_content_detected": critical_content_detected,
        "sensitive_content_detected": sensitive_content_detected,
    }


def build_traitement_from_qalitas(module: str, records: list, systeme: str = "QALITAS WEB", qalitas_modules: list | None = None) -> dict:
    """Build a complete traitement dict from raw QALITAS records."""
    detection = detect_qalitas_fields(records, module)
    profile   = _merge_qalitas_profiles(module, qalitas_modules)
    no_direct_personal_data = (
        not detection["personal_fields"]
        and not detection["sensitive_fields"]
        and detection.get("records_with_personal_data", 0) == 0
        and detection.get("affected_clients_count", 0) == 0
    )
    if no_direct_personal_data:
        detection["person_categories"] = []
        detection["person_categories_display"] = []
        detection["hors_champ_rgpd"] = True
        detection["scope_reason"] = (
            "Aucune donnee personnelle directe n a ete detectee dans l echantillon analyse. "
            "Le module reste visible comme contexte metier, sans generer d alerte RGPD directe."
        )
    else:
        detection["person_categories"] = profile.get("personnes_concernees", []) or []
        detection["person_categories_display"] = _build_person_categories_display(module, profile)
    donnees_collectees = detection["personal_fields"] + detection["sensitive_fields"]
    donnees_sensibles  = len(detection["sensitive_fields"]) > 0 or profile.get("donnees_sensibles", False)
    defaults = _build_source_derived_defaults(module, profile, detection, systeme, no_direct_personal_data)
    minimisation_ok = profile.get("donnees_minimisees", True) and not defaults["security_review_required"]
    return {
        "id_traitement":    f"QALITAS-{module.upper()}-001",
        "nom_traitement":   profile.get("nom_traitement", f"Traitement QALITAS {module}"),
        "systeme":          systeme,
        "responsable":      "DPO TIM Consulting",
        "finalite":         profile.get("finalite", ""),
        "base_legale":      defaults["base_legale"],
        "_base_legale_presumee": profile.get("base_legale", False),
        "_base_legale_inferred_from_profile": bool(profile.get("base_legale")),
        "personnes_concernees":    [] if no_direct_personal_data else profile.get("personnes_concernees", []),
        "transfert_etranger":      profile.get("transfert_etranger", False),
        "risque_eleve":            False if no_direct_personal_data else profile.get("risque_eleve", False),
        "donnees_minimisees":      minimisation_ok,
        "donnees_collectees":      donnees_collectees,
        "donnees_sensibles":       False if no_direct_personal_data else donnees_sensibles,
        "duree_conservation": defaults["duree_conservation"],
        "duree_conservation_definie": defaults["duree_conservation_definie"], "duree_depassee": defaults["duree_depassee"],
        "mesures_securite": defaults["mesures_securite"], "privacy_by_design": defaults["privacy_by_design"],
        "privacy_by_default": defaults["privacy_by_default"],
        "processus_droits_personnes": defaults["processus_droits_personnes"], "information_personnes_concernees": defaults["information_personnes_concernees"],
        "modalites_droits_accessibles": defaults["modalites_droits_accessibles"],
        "consentement_valide": defaults["consentement_valide"], "consentement_retire": defaults["consentement_retire"],
        "violation_donnees": defaults["violation_donnees"], "notification_72h": defaults["notification_72h"],
        "notification_personnes": defaults["notification_personnes"], "violation_documentee": defaults["violation_documentee"],
        "aipd_realisee": defaults["aipd_realisee"], "mise_en_production": defaults["mise_en_production"],
        "analyse_risque_avant_production": defaults["analyse_risque_avant_production"], "garanties_specifiques": defaults["garanties_specifiques"],
        "respect_vie_privee": defaults["respect_vie_privee"], "declaration_inpdp": defaults["declaration_inpdp"], "registre_traitement": defaults["registre_traitement"],
        "politique_protection_donnees": defaults["politique_protection_donnees"],
        "revue_periodique_mesures": defaults["revue_periodique_mesures"],
        "tests_securite_reguliers": defaults["tests_securite_reguliers"],
        "controle_acces_physique": defaults["controle_acces_physique"],
        "confidentialite_post_traitement": defaults["confidentialite_post_traitement"],
        "contrat_sous_traitance": defaults["contrat_sous_traitance"],
        "garanties_sous_traitant": defaults["garanties_sous_traitant"],
        "collecte_indirecte": defaults["collecte_indirecte"],
        "information_collecte_indirecte": defaults["information_collecte_indirecte"],
        "consentement_collecte_indirecte": defaults["consentement_collecte_indirecte"],
        "information_transfert_fournie": defaults["information_transfert_fournie"],
        "opposition_ignoree": defaults["opposition_ignoree"],
        "decision_automatisee": defaults["decision_automatisee"],
        "garanties_decision_auto": defaults["garanties_decision_auto"],
        "dsar_hors_delai": defaults["dsar_hors_delai"],
        "traitement_grande_echelle": defaults["traitement_grande_echelle"],
        "dpo_designe": defaults["dpo_designe"],
        "missions_dpo_garanties": defaults["missions_dpo_garanties"],
        "adequation_ou_garanties_documentees": defaults["adequation_ou_garanties_documentees"],
        "autorisation_inpdp_transfert": defaults["autorisation_inpdp_transfert"],
        "niveau_protection_adequat": defaults["niveau_protection_adequat"],
        "risque_securite_nationale": defaults["risque_securite_nationale"],
        "chiffrement_actif": defaults["chiffrement_actif"],
        "_security_review_required": defaults["security_review_required"],
        "_critical_content_detected": defaults["critical_content_detected"],
        "_sensitive_content_detected": defaults["sensitive_content_detected"],
        "hors_champ_rgpd": no_direct_personal_data,
        "champ_application_rgpd": "hors_champ" if no_direct_personal_data else "applicable",
        "scope_reason": detection.get("scope_reason"),
        "_source_derived": True,
        "_documentary_unknowns": _initial_documentary_unknowns(),
        "_qalitas_module":  module,
        "_qalitas_modules": qalitas_modules or [],
        "_record_count":    detection["record_count"],
        "_detected_fields": detection,
    }


def build_traitement_from_gmao(module: str, records: list, systeme: str = "GMAO PRO WEB", gmao_modules: list | None = None) -> dict:
    """Build a complete traitement dict from raw GMAO records using QALITAS-style detection."""
    detection = detect_qalitas_fields(records, module, source_system="gmao")
    profile = _merge_gmao_profiles(module, gmao_modules)
    no_direct_personal_data = (
        not detection["personal_fields"]
        and not detection["sensitive_fields"]
        and detection.get("records_with_personal_data", 0) == 0
        and detection.get("affected_clients_count", 0) == 0
    )
    organization_only = no_direct_personal_data and module in GMAO_ORGANIZATION_MODULES
    if no_direct_personal_data:
        detection["person_categories"] = []
        detection["person_categories_display"] = []
        detection["hors_champ_rgpd"] = True
        detection["scope_reason"] = (
            "Module GMAO compose uniquement d organisations : aucune personne physique "
            "ni donnee personnelle directe n a ete detectee."
            if organization_only else
            "Aucune donnee personnelle directe n a ete detectee dans l echantillon analyse. "
            "Le module reste visible comme contexte metier, sans generer d alerte RGPD directe."
        )
    else:
        detection["person_categories"] = profile.get("personnes_concernees", []) or []
        detection["person_categories_display"] = _build_person_categories_display(module, profile)
    donnees_collectees = detection["personal_fields"] + detection["sensitive_fields"]
    donnees_sensibles = len(detection["sensitive_fields"]) > 0 or profile.get("donnees_sensibles", False)
    defaults = _build_source_derived_defaults(module, profile, detection, systeme, no_direct_personal_data)
    minimisation_ok = profile.get("donnees_minimisees", True) and not defaults["security_review_required"]
    return {
        "id_traitement":    f"GMAO-{module.upper()}-001",
        "nom_traitement":   profile.get("nom_traitement", f"Traitement GMAO {module}"),
        "systeme":          systeme,
        "responsable":      "DPO TIM Consulting",
        "finalite":         profile.get("finalite", ""),
        "base_legale":      defaults["base_legale"],
        "_base_legale_presumee": profile.get("base_legale", False),
        "_base_legale_inferred_from_profile": bool(profile.get("base_legale")),
        "personnes_concernees":    [] if no_direct_personal_data else profile.get("personnes_concernees", []),
        "transfert_etranger":      profile.get("transfert_etranger", False),
        "risque_eleve":            False if no_direct_personal_data else profile.get("risque_eleve", False),
        "donnees_minimisees":      minimisation_ok,
        "donnees_collectees":      donnees_collectees,
        "donnees_sensibles":       False if no_direct_personal_data else donnees_sensibles,
        "duree_conservation": defaults["duree_conservation"],
        "duree_conservation_definie": defaults["duree_conservation_definie"], "duree_depassee": defaults["duree_depassee"],
        "mesures_securite": defaults["mesures_securite"], "privacy_by_design": defaults["privacy_by_design"],
        "privacy_by_default": defaults["privacy_by_default"],
        "processus_droits_personnes": defaults["processus_droits_personnes"], "information_personnes_concernees": defaults["information_personnes_concernees"],
        "modalites_droits_accessibles": defaults["modalites_droits_accessibles"],
        "consentement_valide": defaults["consentement_valide"], "consentement_retire": defaults["consentement_retire"],
        "violation_donnees": defaults["violation_donnees"], "notification_72h": defaults["notification_72h"],
        "notification_personnes": defaults["notification_personnes"], "violation_documentee": defaults["violation_documentee"],
        "aipd_realisee": defaults["aipd_realisee"], "mise_en_production": defaults["mise_en_production"],
        "analyse_risque_avant_production": defaults["analyse_risque_avant_production"], "garanties_specifiques": defaults["garanties_specifiques"],
        "respect_vie_privee": defaults["respect_vie_privee"], "declaration_inpdp": defaults["declaration_inpdp"], "registre_traitement": defaults["registre_traitement"],
        "politique_protection_donnees": defaults["politique_protection_donnees"],
        "revue_periodique_mesures": defaults["revue_periodique_mesures"],
        "tests_securite_reguliers": defaults["tests_securite_reguliers"],
        "controle_acces_physique": defaults["controle_acces_physique"],
        "confidentialite_post_traitement": defaults["confidentialite_post_traitement"],
        "contrat_sous_traitance": defaults["contrat_sous_traitance"],
        "garanties_sous_traitant": defaults["garanties_sous_traitant"],
        "collecte_indirecte": defaults["collecte_indirecte"],
        "information_collecte_indirecte": defaults["information_collecte_indirecte"],
        "consentement_collecte_indirecte": defaults["consentement_collecte_indirecte"],
        "information_transfert_fournie": defaults["information_transfert_fournie"],
        "opposition_ignoree": defaults["opposition_ignoree"],
        "decision_automatisee": defaults["decision_automatisee"],
        "garanties_decision_auto": defaults["garanties_decision_auto"],
        "dsar_hors_delai": defaults["dsar_hors_delai"],
        "traitement_grande_echelle": defaults["traitement_grande_echelle"],
        "dpo_designe": defaults["dpo_designe"],
        "missions_dpo_garanties": defaults["missions_dpo_garanties"],
        "adequation_ou_garanties_documentees": defaults["adequation_ou_garanties_documentees"],
        "autorisation_inpdp_transfert": defaults["autorisation_inpdp_transfert"],
        "niveau_protection_adequat": defaults["niveau_protection_adequat"],
        "risque_securite_nationale": defaults["risque_securite_nationale"],
        "chiffrement_actif": defaults["chiffrement_actif"],
        "_security_review_required": defaults["security_review_required"],
        "_critical_content_detected": defaults["critical_content_detected"],
        "_sensitive_content_detected": defaults["sensitive_content_detected"],
        "_gmao_module":  module,
        "_gmao_modules": gmao_modules or [],
        "_record_count":    detection["record_count"],
        "_detected_fields": detection,
        "hors_champ_rgpd": no_direct_personal_data,
        "champ_application_rgpd": "hors_champ" if no_direct_personal_data else "applicable",
        "scope_reason": detection.get("scope_reason"),
        "_source_derived": True,
        "_documentary_unknowns": _initial_documentary_unknowns(),
    }

# (duplicate removed - see detect_qalitas_fields and build_traitement_from_qalitas above)


def classifier_donnees(donnees_collectees: list, donnees_sensibles_flag: bool, field_ml: dict | None = None) -> dict:
    """Q1 - Classify each data field individually by type and criticite"""

    classification = []
    criticite_globale = "faible"

    for donnee in donnees_collectees:
        ml_prediction = (field_ml or {}).get(donnee) or {}
        if donnee in DONNEES_SENSIBLES:
            type_donnee = "sensible"
            criticite = "elevee"
            criticite_globale = "elevee"
        elif donnee in DONNEES_CRITIQUES:
            type_donnee = "critique"
            criticite = "elevee"
            criticite_globale = "elevee"
        elif donnee in DONNEES_PERSONNELLES_CONTEXTUELLES:
            type_donnee = "personnelle"
            criticite = "faible"
        elif donnee in DONNEES_PERSONNELLES:
            type_donnee = "personnelle"
            criticite = "moyenne"
            if criticite_globale == "faible":
                criticite_globale = "moyenne"
        else:
            type_donnee = "personnelle"
            criticite = "faible"

        classification.append({
            "donnee": donnee,
            "type": type_donnee,
            "criticite": criticite,
            "categorie_rgpd": "Art.9" if type_donnee == "sensible" else "Art.4",
            "ml_category": ml_prediction.get("label"),
            "ml_label": ml_prediction.get("label_display"),
            "ml_confidence": ml_prediction.get("confidence"),
            "ml_source": ml_prediction.get("source"),
        })

    return {
        "classification": classification,
        "criticite_globale": criticite_globale
    }


def _infer_person_category(traitement: dict) -> str:
    personnes = " ".join(traitement.get("personnes_concernees", []) or []).lower()
    if any(token in personnes for token in ["employe", "technicien", "manager", "rh"]):
        return "employe"
    if any(token in personnes for token in ["fournisseur", "sous-traitant", "prestataire"]):
        return "fournisseur"
    if any(token in personnes for token in ["client", "contact client"]):
        return "client"
    return "personne_concernee"


def _infer_business_users(traitement: dict) -> list:
    personnes = " ".join(traitement.get("personnes_concernees", []) or []).lower()
    module = (traitement.get("_qalitas_module") or traitement.get("module") or "").lower()
    if module == "employees" or any(token in personnes for token in ["employe", "technicien", "manager", "rh"]):
        return ["Service RH", "Administration du personnel", "Responsables habilites"]
    if module in {"customers", "companies", "sites"} or "client" in personnes:
        return ["Service commercial", "Service client", "Utilisateurs habilites"]
    if module == "suppliers" or "fournisseur" in personnes:
        return ["Achats", "Qualite fournisseurs", "Responsables habilites"]
    if module in {"audits", "nonconf", "actions"}:
        return ["Responsables metier", "Qualite", "Utilisateurs habilites"]
    return ["Utilisateurs habilites"]


def _group_field_family(field_name: str) -> str:
    field = (field_name or "").lower()
    if any(token in field for token in ["firstname", "lastname", "fullname", "prenom", "nom", "civility"]):
        return "Identite"
    if any(token in field for token in ["email", "phone", "telephone", "mobile", "fax"]):
        return "Coordonnees"
    if any(token in field for token in ["address", "city", "zipcode", "zip", "adresse", "ville", "postal"]):
        return "Adresse"
    if any(token in field for token in ["cin", "passport", "registrationnumber"]):
        return "Piece d identite"
    if any(token in field for token in ["department", "jobtitle", "function", "employeecode", "matricule", "certificate", "sharedwith"]):
        return "Rattachement professionnel"
    return "Autres donnees personnelles"


def _infer_flow_criticite(traitement: dict, classification: dict, has_external_target: bool = False) -> str:
    if traitement.get("donnees_sensibles") or classification.get("criticite_globale") == "elevee":
        return "elevee"
    if has_external_target or traitement.get("transfert_etranger"):
        return "moyenne"
    return classification.get("criticite_globale", "faible")


def construire_flux_donnees(traitement: dict, classification: dict) -> list:
    """Build a simple but explicit Q1 data flow map."""
    donnees = [item.get("donnee") for item in classification.get("classification", []) if item.get("donnee")]
    systeme = traitement.get("systeme", "Systeme")
    module = traitement.get("_qalitas_module") or traitement.get("module") or "general"
    utilisateurs_metier = _infer_business_users(traitement)
    utilisateur_cible = ", ".join(utilisateurs_metier)
    finalite = traitement.get("finalite") or "Finalite non definie"
    destinataires = traitement.get("destinataires", []) or []
    flux = []

    flux.append({
        "etape": "collecte",
        "source": f"{_infer_person_category(traitement)} / service declarant",
        "cible": systeme,
        "flux_type": "interne",
        "criticite": _infer_flow_criticite(traitement, classification),
        "module": module,
        "outil": systeme,
        "description": f"Collecte initiale des donnees dans le cadre de {finalite}",
        "donnees": donnees,
    })
    flux.append({
        "etape": "utilisation",
        "source": systeme,
        "cible": utilisateur_cible,
        "flux_type": "interne",
        "criticite": _infer_flow_criticite(traitement, classification),
        "module": module,
        "outil": systeme,
        "description": "Utilisation metier des donnees par les equipes habilitees",
        "donnees": donnees,
    })
    flux.append({
        "etape": "stockage",
        "source": systeme,
        "cible": f"Base {systeme}",
        "flux_type": "interne",
        "criticite": _infer_flow_criticite(traitement, classification),
        "module": module,
        "outil": "Base locale / applicative",
        "description": "Stockage des donnees et des preuves associees",
        "donnees": donnees,
    })

    if destinataires:
        for destinataire in destinataires:
            flux.append({
                "etape": "partage",
                "source": systeme,
                "cible": destinataire,
                "flux_type": "externe",
                "criticite": _infer_flow_criticite(traitement, classification, has_external_target=True),
                "module": module,
                "outil": systeme,
                "description": "Partage ou communication a un destinataire declare",
                "donnees": donnees,
            })
    else:
        flux.append({
            "etape": "partage",
            "source": systeme,
            "cible": "Aucun destinataire externe documente",
            "flux_type": "interne",
            "criticite": _infer_flow_criticite(traitement, classification),
            "module": module,
            "outil": systeme,
            "description": "Aucun partage externe n a ete documente dans la plateforme",
            "donnees": donnees,
        })

    flux.append({
        "etape": "archivage_suppression",
        "source": f"Base {systeme}",
        "cible": "Archivage / suppression",
        "flux_type": "interne",
        "criticite": _infer_flow_criticite(traitement, classification),
        "module": module,
        "outil": "Politique de retention",
        "description": "Fin de cycle de vie des donnees selon la duree de conservation",
        "donnees": donnees,
    })
    return flux


def _find_previous_inventory_snapshot(traitement: dict) -> dict:
    systeme = traitement.get("systeme")
    module = traitement.get("_qalitas_module") or traitement.get("module")
    id_traitement = traitement.get("id_traitement")
    nom_traitement = traitement.get("nom_traitement")
    for row in crud.get_inventory_treatments(systeme=systeme, module=module, limit=200):
        if id_traitement and row.get("id_traitement") == id_traitement:
            return row
        if not id_traitement and nom_traitement and row.get("nom_traitement") == nom_traitement:
            return row
    return {}


def _infer_retention_status(traitement: dict) -> str:
    if traitement.get("hors_champ_rgpd"):
        return "non_applicable"
    if traitement.get("duree_depassee"):
        return "depassee"
    if not traitement.get("duree_conservation_definie"):
        return "non_definie"
    return "definie"


def _build_q1_coverage(traitement: dict, flux: list, unstructured_details: list, alerts: list) -> dict:
    coverage = {
        "structured_detection": bool(traitement.get("donnees_collectees")),
        "unstructured_detection": bool(unstructured_details),
        "attachments_supported": [],
        "attachments_partial": [],
        "lifecycle_steps": [flow.get("etape") for flow in flux if flow.get("etape")],
        "retention_status": _infer_retention_status(traitement),
        "responsible_documented": bool(traitement.get("responsable")),
        "finalite_documented": bool(traitement.get("finalite")),
        "security_measures_documented": bool(traitement.get("mesures_securite")),
        "alerts_open_count": len([a for a in alerts if a.get("statut", "ouverte") != "cloturee"]),
    }

    for result in unstructured_details:
        file_type = result.get("file_type") or "unknown"
        method = result.get("extraction_method") or "unknown"
        if method in {"pdfplumber", "tesseract", "text-fallback"}:
            coverage["attachments_supported"].append(file_type)
        else:
            coverage["attachments_partial"].append({
                "file_type": file_type,
                "method": method,
                "error": result.get("error"),
            })

    coverage["attachments_supported"] = sorted(set(coverage["attachments_supported"]))
    return coverage


def generer_alertes_q1(traitement: dict, classification: dict, unstructured_details: list) -> list:
    """Generate operational Q1 alerts aligned with the cahier des charges."""
    alerts = []
    previous_snapshot = _find_previous_inventory_snapshot(traitement)
    previous_fields = set()
    if previous_snapshot:
        for field in crud.get_inventory_fields(previous_snapshot.get("id"), limit=1000):
            if field.get("field_name"):
                previous_fields.add(field["field_name"])

    current_fields = {item.get("donnee") for item in classification.get("classification", []) if item.get("donnee")}
    current_fields.update(
        finding.get("pattern")
        for result in unstructured_details
        for finding in result.get("findings", [])
        if finding.get("pattern")
    )
    detected_fields = traitement.get("_detected_fields") or {}
    structured_content_findings = detected_fields.get("structured_content_findings", []) or []
    current_fields.update(
        finding.get("field_name")
        for finding in structured_content_findings
        if finding.get("field_name")
    )
    current_fields.update(
        finding.get("canonical_field")
        for finding in structured_content_findings
        if finding.get("canonical_field")
    )

    if not traitement.get("responsable"):
        alerts.append({
            "code": "Q1-ORPHAN-OWNER",
            "titre": "Donnees sans responsable explicite",
            "message": "Le traitement contient des donnees personnelles sans responsable clairement defini.",
            "severity": "elevee",
            "statut": "ouverte",
            "metadata": {"champ": "responsable"}
        })
    if current_fields and not traitement.get("finalite"):
        alerts.append({
            "code": "Q1-NO-FINALITE",
            "titre": "Donnees non rattachees a une finalite claire",
            "message": "Des donnees personnelles ont ete detectees sans finalite documentaire suffisamment claire.",
            "severity": "elevee",
            "statut": "ouverte",
            "metadata": {"fields_count": len(current_fields)}
        })
    if traitement.get("duree_depassee"):
        alerts.append({
            "code": "Q1-RETENTION-EXPIRED",
            "titre": "Conservation depassee",
            "message": "Des donnees semblent conservees au dela de la duree de conservation declaree.",
            "severity": "critique",
            "statut": "ouverte",
            "metadata": {"duree_conservation": traitement.get("duree_conservation")}
        })
    if not traitement.get("duree_conservation_definie"):
        alerts.append({
            "code": "Q1-RETENTION-MISSING",
            "titre": "Duree de conservation non definie",
            "message": "La duree de conservation n est pas definie pour ce traitement.",
            "severity": "elevee",
            "statut": "ouverte",
            "metadata": {}
        })
    has_special_category = any(item.get("type") in {"sensible", "critique"} for item in classification.get("classification", []))
    if has_special_category and not (
        traitement.get("chiffrement_actif") or traitement.get("garanties_specifiques")
    ):
        alerts.append({
            "code": "Q1-SENSITIVE-UNPROTECTED",
            "titre": "Donnees sensibles ou critiques sans protection renforcee",
            "message": "Des donnees sensibles ou critiques ont ete detectees sans garanties renforcees ni chiffrement documente.",
            "severity": "critique",
            "statut": "ouverte",
            "metadata": {"mesures_securite": traitement.get("mesures_securite", [])}
        })
    if not traitement.get("donnees_minimisees") and len(current_fields) >= 5:
        alerts.append({
            "code": "Q1-DATA-MINIMIZATION",
            "titre": "Collecte potentiellement excessive",
            "message": "Le volume de donnees detectees n est pas justifie par un principe de minimisation active.",
            "severity": "moyenne",
            "statut": "ouverte",
            "metadata": {"fields_count": len(current_fields)}
        })

    new_fields = sorted(current_fields - previous_fields) if previous_fields else []
    if new_fields:
        alerts.append({
            "code": "Q1-NEW-FIELDS",
            "titre": "Nouveaux champs non declares",
            "message": "De nouveaux champs ou motifs de donnees ont ete detectes depuis la derniere cartographie.",
            "severity": "moyenne",
            "statut": "ouverte",
            "metadata": {"new_fields": new_fields[:20]}
        })

    partial_attachment_results = [
        {
            "filename": result.get("filename"),
            "file_type": result.get("file_type"),
            "method": result.get("extraction_method"),
            "error": result.get("error"),
        }
        for result in unstructured_details
        if (result.get("error") or result.get("extraction_method") in {"metadata-only", "unsupported"})
    ]
    if partial_attachment_results:
        alerts.append({
            "code": "Q1-ATTACHMENTS-PARTIAL",
            "titre": "Pieces jointes partiellement analysees",
            "message": "Certaines pieces jointes n ont ete couvertes que partiellement et necessitent une verification manuelle.",
            "severity": "moyenne",
            "statut": "ouverte",
            "metadata": {"items": partial_attachment_results[:10]}
        })

    if structured_content_findings:
        highest = max(
            structured_content_findings,
            key=lambda item: {"faible": 1, "moyenne": 2, "elevee": 3, "critique": 4}.get(item.get("criticite", "faible"), 1)
        )
        alerts.append({
            "code": "Q1-STRUCTURED-CONTENT-DETECTED",
            "titre": "Contenu personnel sensible ou critique detecte dans les valeurs lues",
            "message": "Le scan des contenus des colonnes a detecte de nouvelles donnees personnelles, sensibles ou critiques dans les enregistrements lus.",
            "severity": "critique" if highest.get("criticite") == "critique" else "elevee",
            "statut": "ouverte",
            "metadata": {
                "patterns": [
                    {
                        "field_name": item.get("field_name"),
                        "pattern": item.get("pattern"),
                        "canonical_field": item.get("canonical_field"),
                        "criticite": item.get("criticite"),
                        "excerpt": item.get("excerpt"),
                    }
                    for item in structured_content_findings[:12]
                ]
            }
        })

    if unstructured_details and not any(result.get("nb_findings", 0) for result in unstructured_details):
        alerts.append({
            "code": "Q1-UNSTRUCTURED-NO-PII",
            "titre": "Pieces jointes analysees sans resultat",
            "message": "Des fichiers non structures ont ete analyses sans detection claire de donnees personnelles.",
            "severity": "faible",
            "statut": "ouverte",
            "metadata": {"files_scanned": len(unstructured_details)}
        })

    return alerts


def construire_indicateurs_q1(classification: dict, unstructured_details: list, alerts: list, flux: list) -> dict:
    classifs = classification.get("classification", []) or []
    return {
        "nombre_donnees": len(classifs),
        "nombre_donnees_sensibles": sum(1 for item in classifs if item.get("type") == "sensible"),
        "nombre_donnees_critiques": sum(1 for item in classifs if item.get("type") == "critique"),
        "fichiers_scannes": len(unstructured_details),
        "findings_non_structures": sum(item.get("nb_findings", 0) for item in unstructured_details),
        "nombre_alertes_q1": len(alerts),
        "nombre_flux": len(flux),
    }


def construire_associations_q1(traitement: dict, classification: dict) -> list:
    """Business-readable Q1 association map grouped by data family."""
    module = traitement.get("_qalitas_module") or traitement.get("module") or "general"
    processus = traitement.get("nom_traitement") or traitement.get("finalite") or "Processus non defini"
    utilisateurs = _infer_business_users(traitement)
    outil = traitement.get("systeme") or "Systeme non defini"
    personne_type = _infer_person_category(traitement)
    families = {}
    for item in classification.get("classification", []) or []:
        donnee = item.get("donnee")
        if not donnee:
            continue
        family = _group_field_family(donnee)
        bucket = families.setdefault(family, {"fields": [], "criticite": "faible"})
        bucket["fields"].append(donnee)
        if item.get("criticite") == "elevee":
            bucket["criticite"] = "elevee"
        elif item.get("criticite") == "moyenne" and bucket["criticite"] == "faible":
            bucket["criticite"] = "moyenne"

    associations = []
    for family, payload in families.items():
        associations.append({
            "donnee": family,
            "processus": processus,
            "module": module,
            "utilisateur": ", ".join(utilisateurs),
            "outil": outil,
            "personne_concernee_type": personne_type,
            "categorie_rgpd": "Art.9" if payload["criticite"] == "elevee" and family == "Piece d identite" and traitement.get("donnees_sensibles") else "Art.4",
            "criticite": payload["criticite"],
            "fields": payload["fields"],
        })
    return associations


def _build_q1_proof_bundle(traitement: dict, cartographie: dict) -> dict:
    unstructured = cartographie.get("donnees_non_structurees", {}) or {}
    coverage = cartographie.get("couverture_cartographie", {}) or {}
    return {
        "article_30_ready": not cartographie.get("alertes_q1"),
        "inventory_key": {
            "id_traitement": cartographie.get("id_traitement"),
            "systeme": cartographie.get("systeme"),
            "module": traitement.get("_qalitas_module") or traitement.get("_gmao_module") or traitement.get("module"),
        },
        "evidence_counts": {
            "structured_fields": len(cartographie.get("classification_donnees", []) or []),
            "data_flows": len(cartographie.get("flux_donnees", []) or []),
            "alerts": len(cartographie.get("alertes_q1", []) or []),
            "unstructured_files": unstructured.get("fichiers_scannes", 0),
            "unstructured_findings": cartographie.get("indicateurs", {}).get("findings_non_structures", 0),
        },
        "retention_status": coverage.get("retention_status"),
        "coverage": coverage,
    }


def cartographier_donnees(traitement: dict) -> dict:
    """Q1 - Cartographie intelligente des donnees"""

    if traitement.get("hors_champ_rgpd"):
        cartographie = {
            "id_traitement": traitement.get("id_traitement"),
            "nom_traitement": traitement.get("nom_traitement"),
            "systeme": traitement.get("systeme"),
            "responsable": traitement.get("responsable"),
            "donnees_collectees": [],
            "classification_donnees": [],
            "criticite_globale": "hors_champ_rgpd",
            "categories_donnees": [],
            "donnees_sensibles": False,
            "personnes_concernees": [],
            "destinataires": [],
            "transfert_etranger": False,
            "duree_conservation": "Non applicable RGPD",
            "flux_donnees": [],
            "associations_traitement": [],
            "alertes_q1": [],
            "indicateurs": {
                "nombre_donnees": 0,
                "nombre_donnees_sensibles": 0,
                "nombre_donnees_critiques": 0,
                "fichiers_scannes": 0,
                "findings_non_structures": 0,
                "nombre_alertes_q1": 0,
                "nombre_flux": 0,
            },
            "donnees_non_structurees": {
                "fichiers_scannes": 0,
                "fichiers_avec_donnees_personnelles": 0,
                "criticite_globale": "hors_champ_rgpd",
                "types_detectes": [],
                "details": [],
            },
            "hors_champ_rgpd": True,
            "scope_reason": traitement.get("scope_reason"),
        }
        cartographie["couverture_cartographie"] = {
            "structured_detection": False,
            "unstructured_detection": False,
            "attachments_supported": [],
            "attachments_partial": [],
            "lifecycle_steps": [],
            "retention_status": "non_applicable",
            "responsible_documented": bool(traitement.get("responsable")),
            "finalite_documented": bool(traitement.get("finalite")),
            "security_measures_documented": bool(traitement.get("mesures_securite")),
            "alerts_open_count": 0,
        }
        cartographie["preuves_q1"] = _build_q1_proof_bundle(traitement, cartographie)
        return cartographie

    detected_fields = traitement.get("_detected_fields") or {}
    field_ml = detected_fields.get("ml_field_classification") or {}
    classification = classifier_donnees(
        traitement.get("donnees_collectees", []),
        traitement.get("donnees_sensibles", False),
        field_ml=field_ml
    )

    unstructured_details = traitement.get("unstructured_scan_results", []) or []
    files_with_pii = [item for item in unstructured_details if item.get("nb_findings", 0) > 0]
    detected_types = []
    seen_types = set()
    for item in unstructured_details:
        for finding in item.get("findings", []):
            pattern = finding.get("pattern")
            if pattern and pattern not in seen_types:
                seen_types.add(pattern)
                detected_types.append(pattern)

    unstructured_criticite = "faible"
    if unstructured_details:
        order = {"faible": 1, "moyenne": 2, "elevee": 3, "critique": 4}
        unstructured_criticite = max(
            (item.get("criticite_globale", "faible") for item in unstructured_details),
            key=lambda x: order.get(x, 1)
        )
    flux_donnees = construire_flux_donnees(traitement, classification)
    alertes_q1 = generer_alertes_q1(traitement, classification, unstructured_details)
    indicateurs = construire_indicateurs_q1(classification, unstructured_details, alertes_q1, flux_donnees)
    associations = construire_associations_q1(traitement, classification)
    couverture = _build_q1_coverage(traitement, flux_donnees, unstructured_details, alertes_q1)

    cartographie = {
        "id_traitement": traitement.get("id_traitement"),
        "nom_traitement": traitement.get("nom_traitement"),
        "systeme": traitement.get("systeme"),
        "responsable": traitement.get("responsable"),
        "donnees_collectees": traitement.get("donnees_collectees", []),
        "classification_donnees": classification["classification"],
        "criticite_globale": classification["criticite_globale"],
        "categories_donnees": traitement.get("categories_donnees", []),
        "donnees_sensibles": traitement.get("donnees_sensibles", False),
        "personnes_concernees": traitement.get("personnes_concernees", []),
        "destinataires": traitement.get("destinataires", []),
        "transfert_etranger": traitement.get("transfert_etranger", False),
        "duree_conservation": traitement.get("duree_conservation", "Non definie"),
        "flux_donnees": flux_donnees,
        "associations_traitement": associations,
        "alertes_q1": alertes_q1,
        "indicateurs": indicateurs,
        "donnees_non_structurees": {
            "fichiers_scannes": len(unstructured_details),
            "fichiers_avec_donnees_personnelles": len(files_with_pii),
            "criticite_globale": unstructured_criticite,
            "types_detectes": detected_types,
            "details": unstructured_details,
        },
        "couverture_cartographie": couverture,
    }
    cartographie["preuves_q1"] = _build_q1_proof_bundle(traitement, cartographie)
    return cartographie


# ===============================
# Q2 - CONFORMITE
# ===============================

RECOMMANDATIONS = {
    # A. Principes generaux
    "RGPD-01":    "Definir une base legale valide parmi : contrat, consentement, obligation legale, interet vital, mission publique ou interet legitime (Art.6 RGPD).",
    "RGPD-02":    "Documenter une finalite claire, explicite et legitime. La finalite doit etre determinee avant la collecte et non modifiable sans nouveau consentement.",
    "RGPD-03":    "Limiter la collecte aux donnees strictement necessaires a la finalite declaree. Supprimer tout champ non justifie.",
    "RGPD-04":    "Definir une duree de conservation par categorie de donnees et mettre en place des purges automatiques a l expiration.",
    "RGPD-04B":   "Mettre en place une procedure de verification et mise a jour reguliere des donnees. Corriger ou supprimer toute donnee inexacte.",
    "RGPD-04C":   "Implementer le chiffrement et des controles d acces stricts pour garantir l integrite et la confidentialite des donnees sensibles.",
    "RGPD-04D":   "Designer formellement un responsable du traitement et documenter ses coordonnees dans le registre des traitements.",
    "LOI-TN-01":  "S assurer que le traitement respecte la dignite humaine et la vie privee. Aucune donnee ne doit etre utilisee pour porter atteinte a la personne.",
    "LOI-TN-02":  "Reduire les donnees collectees au strict necessaire au regard des finalites. Supprimer tout champ superflu du formulaire de collecte.",
    "LOI-TN-02B": "Definir et documenter une finalite licite, determinee et explicite avant toute collecte de donnees (Art.10 Loi 2004-63).",
    "LOI-TN-02C": "Deposer une declaration prealable aupres de l Instance Nationale de Protection des Donnees a Caractere Personnel (INPDP) avant tout traitement.",
    # B. Bases legales
    "RGPD-05":    "Associer une base legale documentee a chaque traitement. Pour les employes privilegier le contrat de travail. Pour les clients privilegier le contrat ou l obligation legale.",
    "RGPD-06":    "Obtenir un consentement libre, specifique, eclaire et univoque. Documenter la preuve du consentement et prevoir un mecanisme de retrait simple.",
    "RGPD-06B":   "Suspendre immediatement le traitement suite au retrait du consentement. Mettre en place une procedure automatisee de suspension.",
    "RGPD-06C":   "Ne jamais conditionner la fourniture d un service a l acceptation d un traitement de donnees non necessaire a ce service.",
    "RGPD-07":    "Mettre en place des garanties specifiques pour les donnees sensibles : consentement explicite, mesures de securite renforcees, acces restreint.",
    "RGPD-07B":   "Le traitement de donnees penales necessite une autorisation expresse de l autorite de controle. Cesser le traitement sans cette autorisation.",
    "LOI-TN-03":  "Documenter une finalite legitime, determinee et explicite conformement a l Art.10 de la Loi 2004-63.",
    "LOI-TN-03B": "Obtenir le consentement expres ecrit de la personne concernee ou l autorisation de l INPDP avant tout traitement de donnees sensibles.",
    "LOI-TN-03C": "Dissocier completement la prestation de service de l acceptation du traitement des donnees personnelles.",
    # C. Transparence et information
    "RGPD-08":    "Informer les personnes concernees au moment de la collecte : identite du responsable, finalites, base legale, duree de conservation et droits disponibles.",
    "RGPD-08B":   "Lors de collecte indirecte, informer la personne concernee dans un delai d un mois incluant la source des donnees et ses droits.",
    "RGPD-08C":   "Informer explicitement les personnes concernees de tout transfert international, du pays destinataire et des garanties mises en place.",
    "RGPD-08D":   "Rendre les modalites d exercice des droits accessibles, comprehensibles et disponibles sur tous les canaux de communication.",
    "LOI-TN-04B": "Informer systematiquement la personne concernee de l identite du responsable du traitement, des finalites et de ses droits (Art.27 Loi 2004-63).",
    "LOI-TN-04C": "Obtenir le consentement de la personne concernee avant toute collecte de donnees aupres de tiers.",
    # D. Droits des personnes
    "RGPD-09":    "Mettre en place un processus formel de gestion des droits : acces, rectification, effacement, portabilite, limitation. Designer un responsable DSAR.",
    "RGPD-09B":   "Traiter toute opposition dans les meilleurs delais. En absence de motif legitime imperieux, cesser le traitement immediatement.",
    "RGPD-09C":   "Pour toute decision automatisee a effets juridiques, prevoir une intervention humaine, la possibilite de contester et une explication de la logique utilisee.",
    "RGPD-09D":   "Repondre a toute demande DSAR dans un delai de 30 jours. Mettre en place des alertes automatiques de suivi des delais.",
    "LOI-TN-04":  "Tracer et archiver toutes les demandes d exercice des droits avec leur date de reception, traitement et cloture.",
    "LOI-TN-04D": "Respecter immediatement toute opposition au traitement. L opposition suspend le traitement sans delai (Art.42 Loi 2004-63).",
    "LOI-TN-04E": "Corriger, completer ou supprimer toute donnee inexacte dans un delai de deux mois et notifier la personne concernee par ecrit.",
    # E. Securite et violations
    "RGPD-13":    "Mettre en oeuvre des mesures de securite techniques et organisationnelles : chiffrement, controle d acces, journalisation, sauvegardes, formation du personnel.",
    "RGPD-13B":   "Chiffrer toutes les donnees sensibles au repos et en transit. Utiliser AES-256 pour le stockage et TLS 1.3 pour les communications.",
    "RGPD-13C":   "Planifier des tests de penetration et audits de securite au minimum une fois par an. Documenter les resultats et les correctifs appliques.",
    "RGPD-14":    "Notifier toute violation de donnees a l autorite de controle dans les 72 heures suivant sa decouverte. Preparer un modele de notification.",
    "RGPD-14B":   "Tenir un registre interne de toutes les violations de donnees incluant nature, impact, mesures prises et decision de notification.",
    "RGPD-15":    "Informer sans delai les personnes concernees de toute violation presentant un risque eleve pour leurs droits et libertes.",
    "LOI-TN-05":  "Mettre en place des mesures techniques empechant tout acces, modification ou consultation non autorises des donnees.",
    "LOI-TN-06":  "Documenter systematiquement toute violation de donnees : date, nature, impact, mesures prises (Art.18 Loi 2004-63).",
    "LOI-TN-06B": "Implementer des mesures physiques et techniques : controle d acces aux locaux, verrouillage des postes, sauvegardes securisees, tracabilite des acces.",
    "LOI-TN-06C": "Faire signer des engagements de confidentialite a tous les agents ayant acces aux donnees, valables apres la fin de leur mission.",
    # F. AIPD et Privacy by Design
    "RGPD-16":    "Realiser une Analyse d Impact relative a la Protection des Donnees (AIPD) avant tout traitement a risque eleve. Documenter les mesures d attenuation.",
    "RGPD-17":    "Effectuer une analyse de risque complete avant toute mise en production. Valider les mesures de securite avant le lancement.",
    "RGPD-18":    "Integrer la protection des donnees des la phase de conception : minimisation, pseudonymisation, controles d acces, durees de conservation.",
    "RGPD-18B":   "Configurer les systemes pour ne traiter que les donnees minimales par defaut. L utilisateur doit activement choisir de partager plus.",
    "RGPD-18C":   "Consulter l autorite de controle avant le lancement si le risque residuel reste eleve apres l AIPD et les mesures d attenuation.",
    # G. Sous-traitants et registre
    "RGPD-19":    "Signer un contrat de traitement des donnees (DPA) conforme Art.28 RGPD avec chaque sous-traitant avant tout partage de donnees.",
    "RGPD-19B":   "Tenir et maintenir a jour un registre des activites de traitement (Art.30 RGPD) accessible a l autorite de controle sur demande.",
    "RGPD-19C":   "Verifier les garanties de chaque sous-traitant avant selection : certifications, politiques de securite, references, clauses contractuelles.",
    "LOI-TN-07":  "Choisir scrupuleusement le sous-traitant et verifier qu il dispose des moyens techniques necessaires pour proteger les donnees (Art.20 Loi 2004-63).",
    "LOI-TN-07B": "Exiger un engagement de confidentialite formel et ecrit de chaque sous-traitant, applicable pendant et apres la prestation.",
    # H. Transferts internationaux
    "RGPD-20":    "Encadrer tout transfert international par une decision d adequation, des clauses contractuelles types (CCT) ou des regles d entreprise contraignantes.",
    "RGPD-20B":   "Documenter la base juridique de chaque transfert international et conserver les preuves de garanties appropriees.",
    "LOI-TN-08":  "Obtenir l autorisation obligatoire de l INPDP avant tout transfert de donnees personnelles vers l etranger (Art.52 Loi 2004-63).",
    "LOI-TN-08B": "Evaluer l impact de chaque transfert sur la securite publique et les interets nationaux. Tout transfert a risque est strictement interdit.",
    "LOI-TN-08C": "Verifier que le pays destinataire assure un niveau de protection adequat ou mettre en place des garanties compensatoires documentees.",
    # I. DPO
    "RGPD-21":    "Designer un Delegue a la Protection des Donnees (DPO) pour les traitements a grande echelle ou systematiques de donnees sensibles.",
    "RGPD-21B":   "Garantir les missions du DPO : l informer de tous les traitements, lui allouer les ressources necessaires et assurer son acces direct a la direction.",
    # J. Responsabilite et politiques
    "RGPD-22":    "Rediger et diffuser une politique de protection des donnees documentee, approuvee par la direction et connue de tous les collaborateurs.",
    "RGPD-22B":   "Planifier une revue annuelle de toutes les mesures de conformite et mettre a jour le registre des traitements en consequence.",
    # Legacy
    "RGPD-44-49": "Obtenir une autorisation et mettre en place des garanties appropriees pour tout transfert de donnees hors du pays.",
}


def verifier_transfert_etranger(traitement: dict) -> dict:
    """Q2 - Flag foreign transfer as violation (Art. 44-49)"""
    if traitement.get("transfert_etranger", False):
        return {
            "id_regle": "RGPD-44-49",
            "source": "RGPD",
            "article": "Art.44-49",
            "message": "Transfert de donnees hors du pays sans autorisation verifiee.",
            "gravite": 3
        }
    return None


def ajouter_recommandations(violations: list) -> list:
    """Q2 - Add recommendation to each violation"""
    for v in violations:
        v["recommandation"] = RECOMMANDATIONS.get(v["id_regle"], "Consulter le DPO.")
    return violations


def build_structured_gaps(violations: list) -> list:
    """Convert raw violations into action-ready gaps."""
    severity_map = {3: "Critique", 2: "Eleve", 1: "Moyen"}
    gaps = []
    for v in violations:
        sev = severity_map.get(v.get("gravite", 1), "Moyen")
        gaps.append({
            "id_regle": v.get("id_regle"),
            "title": f"{v.get('id_regle', 'RGPD')} - correction requise",
            "message": v.get("message", ""),
            "severity": sev,
            "source": v.get("source"),
            "article": v.get("article"),
            "recommendation": v.get("recommandation", "Consulter le DPO.")
        })
    return gaps


DOCUMENTARY_UNKNOWN_FIELDS = (
    "base_legale",
    "duree_conservation_definie",
    "information_personnes_concernees",
    "modalites_droits_accessibles",
    "processus_droits_personnes",
    "mesures_securite",
    "tests_securite_reguliers",
    "controle_acces_physique",
    "analyse_risque_avant_production",
    "privacy_by_design",
    "privacy_by_default",
    "registre_traitement",
    "politique_protection_donnees",
    "revue_periodique_mesures",
    "contrat_sous_traitance",
    "garanties_sous_traitant",
    "adequation_ou_garanties_documentees",
    "autorisation_inpdp_transfert",
    "niveau_protection_adequat",
    "dpo_designe",
    "missions_dpo_garanties",
)


DOCUMENTARY_RULE_FIELD_MAP = {
    "RGPD-01": ("base_legale",),
    "RGPD-04": ("duree_conservation_definie",),
    "RGPD-05": ("base_legale",),
    "RGPD-08": ("information_personnes_concernees",),
    "RGPD-08C": ("adequation_ou_garanties_documentees",),
    "RGPD-08D": ("modalites_droits_accessibles",),
    "RGPD-09": ("processus_droits_personnes",),
    "RGPD-13": ("mesures_securite",),
    "RGPD-13C": ("tests_securite_reguliers",),
    "RGPD-16": ("aipd_realisee",),
    "RGPD-17": ("analyse_risque_avant_production",),
    "RGPD-18": ("privacy_by_design",),
    "RGPD-18B": ("privacy_by_default",),
    "RGPD-19": ("contrat_sous_traitance",),
    "RGPD-19B": ("registre_traitement",),
    "RGPD-19C": ("garanties_sous_traitant",),
    "RGPD-20B": ("adequation_ou_garanties_documentees",),
    "RGPD-21": ("dpo_designe",),
    "RGPD-21B": ("missions_dpo_garanties",),
    "RGPD-22": ("politique_protection_donnees",),
    "RGPD-22B": ("revue_periodique_mesures",),
    "LOI-TN-04": ("processus_droits_personnes",),
    "LOI-TN-04B": ("information_personnes_concernees",),
    "LOI-TN-05": ("mesures_securite",),
    "LOI-TN-06B": ("controle_acces_physique", "mesures_securite"),
    "LOI-TN-07": ("garanties_sous_traitant",),
    "LOI-TN-07B": ("contrat_sous_traitance",),
    "LOI-TN-08": ("autorisation_inpdp_transfert",),
    "LOI-TN-08C": ("niveau_protection_adequat",),
}


DOCUMENTARY_POINT_MESSAGES = {
    "RGPD-01": "Base legale a confirmer : la source analysee ne contient pas encore de preuve formelle de la base retenue.",
    "RGPD-04": "Duree de conservation a confirmer : la source analysee ne documente pas encore la regle de retention applicable.",
    "RGPD-05": "Base legale a formaliser : une base plausible existe, mais elle n est pas encore documentee dans la source lue.",
    "RGPD-08": "Information des personnes a confirmer : la source analysee ne prouve pas la remise d une notice d information.",
    "RGPD-08D": "Modalites d exercice des droits a confirmer : la source analysee ne prouve pas encore leur publication.",
    "RGPD-09": "Processus DSAR a confirmer : aucun element probant n a ete lu dans la source analysee.",
    "RGPD-13": "Mesures de securite a confirmer : la source analysee n expose pas encore les protections mises en place.",
    "RGPD-13C": "Tests de securite a confirmer : aucune preuve de revue ou de test n est visible dans la source analysee.",
    "RGPD-17": "Analyse de risque a confirmer : la source analysee ne prouve pas encore la revue avant mise en production.",
    "RGPD-18": "Privacy by Design a confirmer : la source analysee ne documente pas encore les mesures de conception.",
    "RGPD-18B": "Privacy by Default a confirmer : la source analysee ne documente pas encore les parametrages par defaut.",
    "RGPD-19B": "Registre a confirmer : la source analysee ne prouve pas encore l existence d un registre a jour.",
    "RGPD-22": "Politique de protection des donnees a confirmer : la source analysee ne fournit pas de preuve documentaire.",
    "RGPD-22B": "Revue periodique a confirmer : la source analysee ne contient pas encore de preuve de reevaluation reguliere.",
}


def _is_source_derived_treatment(traitement: dict) -> bool:
    return bool(traitement.get("_source_derived") or traitement.get("_qalitas_module") or traitement.get("_gmao_module"))


def _initial_documentary_unknowns() -> dict:
    return {field: True for field in DOCUMENTARY_UNKNOWN_FIELDS}


def _reconcile_documentary_unknowns(traitement: dict) -> dict:
    unknowns = dict(traitement.get("_documentary_unknowns") or {})
    if not unknowns and not _is_source_derived_treatment(traitement):
        return {}

    for field in DOCUMENTARY_UNKNOWN_FIELDS:
        value = traitement.get(field)
        if field == "base_legale":
            unknowns[field] = not bool(value)
        elif isinstance(value, list):
            unknowns[field] = len(value) == 0
        else:
            unknowns[field] = not bool(value)
    return unknowns


def _build_documentary_point(violation: dict, base_legale_analyse: dict) -> dict:
    point = dict(violation)
    rule_id = point.get("id_regle")
    point["status"] = "a_confirmer"
    point["message_original"] = point.get("message", "")
    point["message"] = DOCUMENTARY_POINT_MESSAGES.get(
        rule_id,
        "Point documentaire a confirmer : la source analysee ne prouve pas encore cet element de conformite."
    )
    if rule_id in {"RGPD-01", "RGPD-05"}:
        basis = (
            base_legale_analyse.get("base_legale_presumee")
            or base_legale_analyse.get("base_legale_recommandee")
            or "a confirmer"
        )
        point["recommandation"] = (
            "Documenter la base legale retenue, conserver la preuve de validation DPO "
            f"et rattacher le support formel. Base actuelle : {basis}."
        )
    return point


def split_compliance_findings(violations: list, traitement: dict, base_legale_analyse: dict) -> tuple[list, list]:
    if not violations:
        return [], []
    if not _is_source_derived_treatment(traitement):
        return violations, []

    unknowns = _reconcile_documentary_unknowns(traitement)
    traitement["_documentary_unknowns"] = unknowns
    hard_violations = []
    documentary_points = []

    for violation in violations:
        rule_id = violation.get("id_regle")
        mapped_fields = DOCUMENTARY_RULE_FIELD_MAP.get(rule_id, ())
        should_confirm = False

        if rule_id in {"RGPD-01", "RGPD-05"}:
            should_confirm = (
                not base_legale_analyse.get("base_legale_confirmee")
                and bool(
                    base_legale_analyse.get("base_legale_presumee")
                    or base_legale_analyse.get("base_legale_recommandee")
                )
            )

        if not should_confirm and mapped_fields:
            should_confirm = any(unknowns.get(field, False) for field in mapped_fields)

        if should_confirm:
            documentary_points.append(_build_documentary_point(violation, base_legale_analyse))
        else:
            hard_violations.append(violation)

    return hard_violations, documentary_points


def build_register_entry(traitement: dict, cartographie: dict, base_legale_analyse: dict, niveau_risque: str) -> dict:
    """Build operational Article 30 snapshot."""
    categories = cartographie.get("categories_donnees", [])
    if not categories:
        categories = list({c.get("type") for c in cartographie.get("classification_donnees", []) if c.get("type")})

    mesures = traitement.get("mesures_securite", []) or []
    if traitement.get("hors_champ_rgpd"):
        missing_info = False
    else:
        missing_info = any([
            not base_legale_analyse.get("base_legale"),
            not cartographie.get("finalite"),
            not cartographie.get("duree_conservation") or cartographie.get("duree_conservation") == "Non definie",
            len(mesures) == 0
        ])

    return {
        "id_traitement": cartographie.get("id_traitement"),
        "nom_traitement": cartographie.get("nom_traitement"),
        "systeme": cartographie.get("systeme"),
        "module": traitement.get("_qalitas_module") or traitement.get("_gmao_module") or traitement.get("module"),
        "responsable": cartographie.get("responsable"),
        "finalite": base_legale_analyse.get("finalite"),
        "base_legale": base_legale_analyse.get("base_legale"),
        "base_legale_recommandee": base_legale_analyse.get("base_legale_recommandee"),
        "coherence_base_legale": base_legale_analyse.get("coherence_base_legale"),
        "base_legale_memoire_dpo": base_legale_analyse.get("memoire_dpo", {}),
        "categories_donnees": categories,
        "personnes_concernees": cartographie.get("personnes_concernees", []),
        "destinataires": cartographie.get("destinataires", []),
        "duree_conservation": cartographie.get("duree_conservation"),
        "retention_status": _infer_retention_status(traitement),
        "mesures_securite": mesures,
        "risk_level": niveau_risque,
        "missing_info": missing_info,
        "last_checked": None,
        "flux_resume": [flow.get("etape") for flow in cartographie.get("flux_donnees", [])],
        "alertes_q1_count": len(cartographie.get("alertes_q1", [])),
        "indicateurs_q1": cartographie.get("indicateurs", {}),
        "preuves_q1": cartographie.get("preuves_q1", {}),
        "registre_consentements": base_legale_analyse.get("registre_consentements", {}),
        "traitement_a_bloquer": base_legale_analyse.get("traitement_a_bloquer", False),
        "motifs_blocage": base_legale_analyse.get("motifs_blocage", []),
        "hors_champ_rgpd": bool(traitement.get("hors_champ_rgpd")),
        "scope_reason": traitement.get("scope_reason"),
    }


# ===============================
# Q3 - BASES LEGALES & CONSENTEMENTS
# ===============================

LEGAL_BASIS_MEMORY_VALUES = {
    "consentement",
    "contrat",
    "obligation_legale",
    "interet_legitime",
    "mission_publique",
    "interet_vital",
}


def _memory_text(values) -> str:
    if values is None:
        return ""
    if isinstance(values, dict):
        return " ".join(_memory_text(v) for v in values.values())
    if isinstance(values, (list, tuple, set)):
        return " ".join(_memory_text(v) for v in values)
    return str(values)


def _normalise_legal_basis_value(value):
    if not value:
        return None
    text = str(value).strip().lower()
    replacements = {
        "intérêt": "interet",
        "légitime": "legitime",
        "legitime": "legitime",
        "obligation légale": "obligation_legale",
        "obligation legale": "obligation_legale",
        "mission publique": "mission_publique",
        "intérêt vital": "interet_vital",
        "interet vital": "interet_vital",
        "execution d'un contrat": "contrat",
        "exécution d'un contrat": "contrat",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.replace(" ", "_").replace("-", "_")
    return text if text in LEGAL_BASIS_MEMORY_VALUES else None


def _memory_source_system(traitement: dict):
    if traitement.get("systeme"):
        return traitement.get("systeme")
    if traitement.get("gmao_module"):
        return "GMAO PRO"
    if traitement.get("qalitas_module"):
        return "QALITAS WEB"
    return None


def _memory_source_module(traitement: dict):
    return traitement.get("qalitas_module") or traitement.get("gmao_module") or traitement.get("module")


def _build_treatment_memory_query(traitement: dict) -> str:
    detected = traitement.get("_detected_fields") or {}
    fragments = [
        traitement.get("id_traitement"),
        traitement.get("nom_traitement"),
        traitement.get("systeme"),
        _memory_source_module(traitement),
        traitement.get("finalite"),
        traitement.get("personnes_concernees"),
        traitement.get("donnees_collectees"),
        traitement.get("destinataires"),
    ]
    if isinstance(detected, dict):
        fragments.append(detected.get("personal_fields"))
        fragments.append(detected.get("sensitive_fields"))
    return " | ".join(_memory_text(fragment) for fragment in fragments if _memory_text(fragment))


def _summarize_dpo_memory(precedents: list, expected_value=None, value_normalizer=None) -> dict:
    if not precedents:
        return {
            "available": False,
            "count": 0,
            "impact": "aucun",
            "confidence": "aucune",
            "guidance": "Aucun precedent DPO similaire trouve.",
            "examples": [],
        }

    examples = []
    for item in precedents[:3]:
        examples.append({
            "id": item.get("id"),
            "target_type": item.get("target_type"),
            "target_label": item.get("target_label"),
            "source_system": item.get("source_system"),
            "source_module": item.get("source_module"),
            "decision": item.get("decision"),
            "final_value": item.get("final_value"),
            "justification": item.get("justification"),
            "match_score": item.get("match_score", 0),
            "created_at": item.get("created_at"),
        })

    best = examples[0]
    score = best.get("match_score", 0) or 0
    if score >= 10:
        confidence = "forte"
    elif score >= 6:
        confidence = "moyenne"
    else:
        confidence = "faible"

    normalizer = value_normalizer or (lambda v: str(v).strip().lower() if v else None)
    expected_norm = normalizer(expected_value)
    best_norm = normalizer(best.get("final_value"))
    if expected_norm and best_norm and expected_norm == best_norm:
        impact = "precedent_confirmant"
        guidance = "Un precedent DPO similaire confirme la recommandation actuelle."
    elif expected_norm and best_norm and expected_norm != best_norm:
        impact = "precedent_different"
        guidance = "Un precedent DPO similaire propose une valeur differente : revue DPO recommandee."
    else:
        impact = "precedent_disponible"
        guidance = "Un precedent DPO similaire existe et peut aider la decision, sans remplacer la validation."

    return {
        "available": True,
        "count": len(precedents),
        "confidence": confidence,
        "impact": impact,
        "guidance": guidance,
        "best_match": best,
        "examples": examples,
    }


def _find_dpo_memory_for_treatment(traitement: dict, target_type: str, expected_value=None, value_normalizer=None) -> dict:
    try:
        precedents = crud.find_similar_dpo_memory(
            target_type=target_type,
            source_system=_memory_source_system(traitement),
            source_module=_memory_source_module(traitement),
            query=_build_treatment_memory_query(traitement),
            limit=5,
        )
    except Exception:
        precedents = []
    return _summarize_dpo_memory(precedents, expected_value=expected_value, value_normalizer=value_normalizer)


def analyser_base_legale(traitement: dict) -> dict:
    """Q3 - Bases legales et consentements"""

    if traitement.get("hors_champ_rgpd"):
        return {
            "base_legale": False,
            "base_legale_confirmee": False,
            "base_legale_presumee": False,
            "base_legale_recommandee": False,
            "finalite": traitement.get("finalite", "Non definie"),
            "consentement_valide": False,
            "consentement_retire": False,
            "coherence_base_legale": "non_applicable",
            "alertes_base_legale": [],
            "hors_champ_rgpd": True,
            "decision_champ_application": "Base legale RGPD non requise : aucune donnee personnelle directe n a ete detectee.",
            "scope_reason": traitement.get("scope_reason"),
            "memoire_dpo": _find_dpo_memory_for_treatment(traitement, "legal_basis"),
            "registre_consentements": {
                "nombre_total": 0,
                "actifs": 0,
                "retires": 0,
                "expires": 0,
                "expirations_proches": 0,
            }
        }

    source_derived_basis = bool(
        traitement.get("_source_derived") and traitement.get("_base_legale_inferred_from_profile")
    )
    base_confirmee = False if source_derived_basis else traitement.get("base_legale")
    base_presumee = traitement.get("_base_legale_presumee", False) or (
        traitement.get("base_legale") if source_derived_basis else False
    )
    finalite = traitement.get("finalite", "Non definie")
    consentement = traitement.get("consentement_valide", False)
    consentement_retire = traitement.get("consentement_retire", False)
    id_traitement = traitement.get("id_traitement")

    alertes = []
    coherence = "coherente"
    base_recommandee = base_confirmee or base_presumee or False
    consentements = crud.get_consents(id_traitement=id_traitement) if id_traitement else []
    actifs = [c for c in consentements if c.get("statut") == "actif"]
    retires = [c for c in consentements if c.get("statut") == "retire"]
    expires = [c for c in consentements if c.get("statut") == "expire"]
    expirations_proches = [
        c for c in crud.get_expiring_consents(days_ahead=30)
        if not id_traitement or c.get("id_traitement") == id_traitement
    ]

    if not base_confirmee:
        alertes.append("Aucune base legale confirmee.")
        coherence = "incomplete"
    if base_presumee and traitement.get("_base_legale_inferred_from_profile"):
        system_label = "GMAO" if "GMAO" in str(traitement.get("systeme", "")).upper() else "QALITAS"
        alertes.append(f"Base legale presumee deduite du profil {system_label} : confirmation documentaire necessaire.")
        coherence = "incomplete"
    if base_confirmee == "consentement":
        if traitement.get("type_relation") in ["employe", "sous-traitant", "contrat"]:
            alertes.append(
                "Mauvais usage du consentement : une base contractuelle est plus adaptee."
            )
            coherence = "incoherente"
            base_recommandee = "contrat"
        if not consentement:
            alertes.append("Base legale = consentement mais consentement invalide ou absent.")
            coherence = "incomplete"

    if not base_recommandee:
        personnes = " ".join(traitement.get("personnes_concernees", []) or []).lower()
        if any(token in personnes for token in ["employe", "technicien", "manager"]):
            base_recommandee = "contrat"
        elif any(token in personnes for token in ["client", "contact client", "fournisseur", "contact fournisseur"]):
            base_recommandee = "contrat"
        elif traitement.get("transfert_etranger"):
            base_recommandee = "interet_legitime"

    if consentement_retire:
        alertes.append(
            "Consentement retire : le traitement doit etre suspendu immediatement."
        )
        coherence = "incoherente"

    if not finalite or finalite == "Non definie":
        alertes.append("Finalite du traitement non definie.")
        coherence = "incomplete"
    if traitement.get("_critical_content_detected"):
        alertes.append(
            "De nouvelles donnees critiques ont ete detectees dans la source lue : la base legale, la minimisation et les mesures de protection doivent etre revalidees."
        )
        coherence = "incomplete"
    elif traitement.get("_sensitive_content_detected"):
        alertes.append(
            "De nouvelles donnees sensibles ont ete detectees dans la source lue : une revue DPO des garanties et de la base legale est recommandee."
        )
        coherence = "incomplete"

    base_active = base_confirmee or base_presumee
    if base_active == "consentement" and not consentements:
        alertes.append("Aucune preuve de consentement enregistree dans le registre des consentements.")
        coherence = "incomplete"
    if base_active != "consentement" and actifs:
        alertes.append("Des consentements existent, mais la base legale active n est pas le consentement. Verification recommandee.")
    if expirations_proches:
        alertes.append("Des consentements vont expirer dans les 30 prochains jours.")
    preuves_consentement = [c for c in actifs if (c.get("preuve") or "").strip()]
    consentement_preuve_disponible = len(preuves_consentement) > 0
    if base_active == "consentement" and actifs and not consentement_preuve_disponible:
        alertes.append("Des consentements actifs existent sans preuve textuelle exploitable.")
        coherence = "incomplete"

    memoire_dpo = _find_dpo_memory_for_treatment(
        traitement,
        "legal_basis",
        expected_value=base_recommandee,
        value_normalizer=_normalise_legal_basis_value,
    )
    precedent_value = _normalise_legal_basis_value((memoire_dpo.get("best_match") or {}).get("final_value"))
    if precedent_value:
        memoire_dpo["recommended_value"] = precedent_value
        current_value = _normalise_legal_basis_value(base_recommandee)
        if not base_recommandee:
            base_recommandee = precedent_value
            alertes.append("Memoire DPO : base legale proposee depuis un precedent similaire, a confirmer.")
            coherence = "incomplete"
        elif current_value and precedent_value != current_value:
            memoire_dpo["conflict"] = True
            alertes.append(
                "Memoire DPO : precedent similaire avec une base legale differente. Revue DPO recommandee."
            )

    motifs_blocage = []
    if consentement_retire:
        motifs_blocage.append("Consentement retire")
    if base_active == "consentement" and (not consentement or not actifs):
        motifs_blocage.append("Consentement requis mais non exploitable")
    if coherence == "incoherente":
        motifs_blocage.append("Base legale incoherente avec le traitement")
    if traitement.get("donnees_sensibles") and not traitement.get("garanties_specifiques"):
        motifs_blocage.append("Donnees sensibles sans garanties specifiques")
    if traitement.get("_critical_content_detected"):
        motifs_blocage.append("Nouvelles donnees critiques detectees sans revalidation DPO")

    actions_recommandees = []
    if not base_confirmee:
        actions_recommandees.append("Confirmer et documenter la base legale retenue.")
    if base_active == "consentement" and not actifs:
        actions_recommandees.append("Collecter un consentement valide et opposable avant poursuite du traitement.")
    if base_active == "consentement" and expirations_proches:
        actions_recommandees.append("Renouveler les consentements proches de l expiration.")
    if consentement_retire:
        actions_recommandees.append("Suspendre le traitement et tracer le retrait du consentement.")
    if traitement.get("donnees_sensibles") and not traitement.get("garanties_specifiques"):
        actions_recommandees.append("Mettre en place des garanties renforcees avant toute poursuite du traitement.")
    if traitement.get("_critical_content_detected"):
        actions_recommandees.append("Revalider immediatement le perimetre du traitement, le registre et les mesures de securite avec le DPO.")
    elif traitement.get("_sensitive_content_detected"):
        actions_recommandees.append("Confirmer les garanties specifiques et mettre a jour le registre du traitement.")

    return {
        "base_legale": base_confirmee,
        "base_legale_confirmee": base_confirmee,
        "base_legale_presumee": base_presumee,
        "base_legale_recommandee": base_recommandee,
        "finalite": finalite,
        "consentement_valide": consentement,
        "consentement_retire": consentement_retire,
        "coherence_base_legale": coherence,
        "alertes_base_legale": alertes,
        "memoire_dpo": memoire_dpo,
        "preuve_consentement_requise": base_active == "consentement",
        "preuve_consentement_disponible": consentement_preuve_disponible,
        "traitement_a_bloquer": len(motifs_blocage) > 0,
        "motifs_blocage": motifs_blocage,
        "actions_recommandees": actions_recommandees,
        "registre_consentements": {
            "nombre_total": len(consentements),
            "actifs": len(actifs),
            "retires": len(retires),
            "expires": len(expires),
            "expirations_proches": len(expirations_proches),
            "preuves_disponibles": len(preuves_consentement),
        }
    }


def construire_resume_axes_conformite(traitement: dict, violations: list, documentary_points: list | None = None) -> list:
    """Group Q2 into readable compliance axes for the UI and DPO follow-up."""
    ids = {v.get("id_regle") for v in violations}
    documentary_ids = {v.get("id_regle") for v in (documentary_points or [])}
    axes = [
        ("finalite_minimisation", "Finalite et minimisation", ["RGPD-02", "RGPD-03"]),
        ("conservation", "Conservation", ["RGPD-04"]),
        ("transparence", "Information des personnes", ["RGPD-08", "RGPD-08D", "LOI-TN-04B"]),
        ("droits", "Droits des personnes", ["RGPD-09", "LOI-TN-04"]),
        ("securite", "Securite", ["RGPD-13", "RGPD-13C", "LOI-TN-05", "LOI-TN-06B"]),
        ("privacy", "Privacy by design/default", ["RGPD-18", "RGPD-18B"]),
        ("registre", "Registre et gouvernance", ["RGPD-19B", "RGPD-22"]),
        ("transferts", "Transferts internationaux", ["RGPD-44-49", "RGPD-20", "LOI-TN-08"]),
    ]
    result = []
    for code, label, related in axes:
        matched = [rule for rule in related if rule in ids]
        confirmations = [rule for rule in related if rule in documentary_ids]
        if matched:
            status = "a_corriger"
        elif confirmations:
            status = "a_confirmer"
        else:
            status = "conforme"
        result.append({
            "code": code,
            "label": label,
            "status": status,
            "violations_count": len(matched),
            "confirmation_count": len(confirmations),
            "related_rules": matched,
            "documentary_rules": confirmations,
        })
    return result


REFERENTIAL_RULESETS = {
    "RGPD": ["RGPD-01", "RGPD-02", "RGPD-03", "RGPD-04", "RGPD-08", "RGPD-09", "RGPD-13", "RGPD-18", "RGPD-19B", "RGPD-20", "RGPD-22"],
    "Loi_tunisienne_2004_63": ["LOI-TN-01", "LOI-TN-02", "LOI-TN-03", "LOI-TN-04", "LOI-TN-05", "LOI-TN-06", "LOI-TN-07", "LOI-TN-08"],
    "ISO_27001": ["RGPD-13", "RGPD-13B", "RGPD-13C", "LOI-TN-05", "LOI-TN-06B"],
    "ISO_9001": ["RGPD-02", "RGPD-19B", "RGPD-22", "RGPD-22B"],
    "ISO_45001": ["RGPD-07", "LOI-TN-03B", "LOI-TN-05"],
}


def construire_matrice_referentiels(violations: list, documentary_points: list | None = None) -> list:
    violations = violations or []
    documentary_points = documentary_points or []
    violation_ids = {item.get("id_regle") for item in violations}
    documentary_ids = {item.get("id_regle") for item in documentary_points}
    matrix = []
    for referentiel, related_rules in REFERENTIAL_RULESETS.items():
        hard = [rule for rule in related_rules if rule in violation_ids]
        docs = [rule for rule in related_rules if rule in documentary_ids]
        if hard:
            statut = "non_conforme"
        elif docs:
            statut = "a_confirmer"
        else:
            statut = "couvre"
        coverage = round(((len(related_rules) - len(hard) - len(docs)) / max(len(related_rules), 1)) * 100)
        matrix.append({
            "referentiel": referentiel,
            "statut": statut,
            "coverage_percent": max(0, min(100, coverage)),
            "violations": hard,
            "points_documentaires": docs,
        })
    return matrix


def construire_preuves_conformite(
    traitement: dict,
    cartographie: dict,
    base_legale_analyse: dict,
    documentary_points: list | None = None,
) -> dict:
    """Build a simple evidence summary for Q2/Q3 decisions."""
    return {
        "hors_champ_rgpd": bool(traitement.get("hors_champ_rgpd")),
        "scope_reason": traitement.get("scope_reason"),
        "registre_disponible": bool(traitement.get("registre_traitement")),
        "mesures_securite_documentees": traitement.get("mesures_securite", []) or [],
        "nombre_flux_cartographies": len(cartographie.get("flux_donnees", []) or []),
        "nombre_alertes_q1": len(cartographie.get("alertes_q1", []) or []),
        "base_legale": base_legale_analyse.get("base_legale"),
        "base_legale_recommandee": base_legale_analyse.get("base_legale_recommandee"),
        "consentements_enregistres": (base_legale_analyse.get("registre_consentements") or {}).get("nombre_total", 0),
        "nombre_points_documentaires": len(documentary_points or []),
        "traitement_a_bloquer": base_legale_analyse.get("traitement_a_bloquer", False),
        "motifs_blocage": base_legale_analyse.get("motifs_blocage", []),
    }


def ajuster_violations_base_legale(violations: list, base_legale_analyse: dict) -> list:
    """Soften legal-basis violations when a basis is presumed/recommended but not yet documented."""
    base_confirmee = base_legale_analyse.get("base_legale_confirmee")
    base_presumee = base_legale_analyse.get("base_legale_presumee")
    base_recommandee = base_legale_analyse.get("base_legale_recommandee")
    coherence = base_legale_analyse.get("coherence_base_legale")

    has_candidate_basis = bool(base_presumee or base_recommandee)
    has_coherent_candidate = has_candidate_basis and coherence in ("coherente", "incomplete")

    if base_confirmee or not has_coherent_candidate:
        return violations

    adjusted = []
    for violation in violations:
        current = dict(violation)
        if current.get("id_regle") == "RGPD-01":
            current["gravite"] = min(current.get("gravite", 3), 2)
            current["message"] = (
                "Base legale non documentee a ce stade : une base plausible existe, "
                "mais elle n est pas encore confirmee par une preuve formelle."
            )
            current["recommandation"] = (
                "Formaliser et documenter la base legale du traitement. "
                f"Base presumee actuelle : {base_presumee or base_recommandee}."
            )
        elif current.get("id_regle") == "RGPD-05":
            current["gravite"] = min(current.get("gravite", 3), 2)
            current["message"] = (
                "Base legale plausible mais non confirmee documentairment : "
                "la base recommandee doit etre rattachee a un support formel."
            )
            current["recommandation"] = (
                "Associer une base legale documentee a ce traitement et conserver "
                "la preuve de validation interne ou contractuelle."
            )
        adjusted.append(current)
    return adjusted


# ===============================
# AGENT A - MAIN
# ===============================

def run_agent_a(traitement: dict) -> dict:
    """
    Agent A - Data Mapper & Compliance (Q1 + Q2 + Q3)
    Intelligent version:
      - Level 2: if 'description' is provided, LLM infers missing fields automatically
      - Level 1: violations are enriched with real RGPD/Loi2004 article citations via RAG
    """

    # === TYPE 2: QALITAS RAW RECORDS — auto-detect fields ===
    if "qalitas_records" in traitement:
        module  = traitement.get("qalitas_module", "unknown")
        records = traitement.get("qalitas_records") or []
        detected = build_traitement_from_qalitas(
            module,
            records,
            qalitas_modules=traitement.get("qalitas_modules")
        )
        traitement = {**detected, **{k: v for k, v in traitement.items()
                                     if k not in ("qalitas_records", "qalitas_module")
                                     and v not in (None, False, [], "")}}
        print(f"[Agent A] QALITAS mode: {module} — {detected['_record_count']} records — "
              f"{len(detected['_detected_fields']['personal_fields'])} personal fields detected")

    # === TYPE 2: GMAO RAW RECORDS — reuse QALITAS detection ===
    if "gmao_records" in traitement:
        module = traitement.get("gmao_module", "unknown")
        records = traitement.get("gmao_records") or []
        detected = build_traitement_from_gmao(
            module,
            records,
            gmao_modules=traitement.get("gmao_modules")
        )
        traitement = {**detected, **{k: v for k, v in traitement.items()
                                     if k not in ("gmao_records", "gmao_module")
                                     and v not in (None, False, [], "")}}
        print(f"[Agent A] GMAO mode: {module} — {detected['_record_count']} records — "
              f"{len(detected['_detected_fields']['personal_fields'])} personal fields detected")

    base_legale_analyse = analyser_base_legale(traitement)

    compliance_input = {
        # A. Principes generaux
        "base_legale": traitement.get("base_legale", False),
        "base_legale_confirmee": base_legale_analyse.get("base_legale_confirmee", False),
        "base_legale_presumee": base_legale_analyse.get("base_legale_presumee", False),
        "base_legale_recommandee": base_legale_analyse.get("base_legale_recommandee", False),
        "coherence_base_legale": base_legale_analyse.get("coherence_base_legale", "incomplete"),
        "finalite_definie": bool(traitement.get("finalite")),
        "donnees_minimisees": traitement.get("donnees_minimisees", False),
        "duree_conservation_definie": traitement.get("duree_conservation_definie", False),
        "duree_depassee": traitement.get("duree_depassee", False),
        "donnees_exactes": traitement.get("donnees_exactes", True),
        "mesures_securite": bool(traitement.get("mesures_securite")),
        "donnees_sensibles": traitement.get("donnees_sensibles", False),
        "responsable": traitement.get("responsable", None),
        "respect_vie_privee": traitement.get("respect_vie_privee", True),
        "declaration_inpdp": traitement.get("declaration_inpdp", True),
        # B. Bases legales
        "consentement_valide": traitement.get("consentement_valide", False),
        "consentement_retire": traitement.get("consentement_retire", False),
        "consentement_conditionne_service": traitement.get("consentement_conditionne_service", False),
        "service_conditionne_consentement": traitement.get("service_conditionne_consentement", False),
        "garanties_specifiques": traitement.get("garanties_specifiques", False),
        "donnees_penales": traitement.get("donnees_penales", False),
        "autorisation_donnees_penales": traitement.get("autorisation_donnees_penales", False),
        # C. Transparence et information
        "information_personnes_concernees": traitement.get("information_personnes_concernees", False),
        "collecte_indirecte": traitement.get("collecte_indirecte", False),
        "information_collecte_indirecte": traitement.get("information_collecte_indirecte", False),
        "transfert_etranger": traitement.get("transfert_etranger", False),
        "information_transfert_fournie": traitement.get("information_transfert_fournie", False),
        "modalites_droits_accessibles": traitement.get("modalites_droits_accessibles", False),
        "consentement_collecte_indirecte": traitement.get("consentement_collecte_indirecte", False),
        # D. Droits des personnes
        "processus_droits_personnes": traitement.get("processus_droits_personnes", False),
        "opposition_ignoree": traitement.get("opposition_ignoree", False),
        "decision_automatisee": traitement.get("decision_automatisee", False),
        "garanties_decision_auto": traitement.get("garanties_decision_auto", False),
        "dsar_hors_delai": traitement.get("dsar_hors_delai", False),
        # E. Securite et violations
        "chiffrement_actif": traitement.get("chiffrement_actif", False),
        "tests_securite_reguliers": traitement.get("tests_securite_reguliers", False),
        "controle_acces_physique": traitement.get("controle_acces_physique", False),
        "confidentialite_post_traitement": traitement.get("confidentialite_post_traitement", True),
        "violation_donnees": traitement.get("violation_donnees", False),
        "notification_72h": traitement.get("notification_72h", False),
        "notification_personnes": traitement.get("notification_personnes", False),
        "violation_documentee": traitement.get("violation_documentee", False),
        # F. AIPD et Privacy by Design
        "risque_eleve": traitement.get("risque_eleve", False),
        "aipd_obligatoire": traitement.get("aipd_obligatoire", False),
        "aipd_realisee": traitement.get("aipd_realisee", False),
        "mise_en_production": traitement.get("mise_en_production", False),
        "analyse_risque_avant_production": traitement.get("analyse_risque_avant_production", False),
        "privacy_by_design": traitement.get("privacy_by_design", False),
        "privacy_by_default": traitement.get("privacy_by_default", False),
        "consultation_autorite_si_risque_residuel": traitement.get("consultation_autorite_si_risque_residuel", True),
        # G. Sous-traitants et registre
        "destinataires": traitement.get("destinataires", []),
        "contrat_sous_traitance": traitement.get("contrat_sous_traitance", False),
        "garanties_sous_traitant": traitement.get("garanties_sous_traitant", False),
        "registre_traitement": traitement.get("registre_traitement", False),
        # H. Transferts internationaux
        "adequation_ou_garanties_documentees": traitement.get("adequation_ou_garanties_documentees", False),
        "autorisation_inpdp_transfert": traitement.get("autorisation_inpdp_transfert", False),
        "niveau_protection_adequat": traitement.get("niveau_protection_adequat", False),
        "risque_securite_nationale": traitement.get("risque_securite_nationale", False),
        # I. DPO
        "traitement_grande_echelle": traitement.get("traitement_grande_echelle", False),
        "dpo_designe": traitement.get("dpo_designe", False),
        "missions_dpo_garanties": traitement.get("missions_dpo_garanties", False),
        # J. Politiques
        "politique_protection_donnees": traitement.get("politique_protection_donnees", False),
        "revue_periodique_mesures": traitement.get("revue_periodique_mesures", False),
    }

    # === LEVEL 2: LLM AUTO-INFERENCE ===
    # If user provided a description, infer missing fields before rule engine runs
    description = traitement.get("description", "")
    if description:
        inferred = infer_fields_from_description(
            description,
            nom=traitement.get("nom_traitement", ""),
            systeme=traitement.get("systeme", "")
        )
        if inferred:
            traitement = merge_with_inferred(traitement, inferred)
            compliance_input = {k: traitement.get(k, compliance_input[k])
                                for k in compliance_input}

    if _is_source_derived_treatment(traitement):
        traitement["_documentary_unknowns"] = _reconcile_documentary_unknowns(traitement)

    valider_schema(compliance_input)

    # Q1
    cartographie = cartographier_donnees(traitement)

    # Q2
    if traitement.get("hors_champ_rgpd"):
        violations = []
        documentary_points = []
    else:
        violations, _ = evaluer_conformite(compliance_input)
        transfert_violation = verifier_transfert_etranger(traitement)
        if transfert_violation:
            violations.append(transfert_violation)
        violations = ajouter_recommandations(violations)
        violations = ajuster_violations_base_legale(violations, base_legale_analyse)
        # === LEVEL 1: RAG CITATION ENRICHMENT ===
        if not traitement.get("fast_source_analysis"):
            violations = enrich_violations_with_rag(violations)
        violations, documentary_points = split_compliance_findings(
            violations,
            traitement,
            base_legale_analyse,
        )
    score_brut = calculer_score_brut(violations)
    score_normalise = normaliser_score(score_brut)
    score_documentaire_brut = calculer_score_brut(documentary_points)
    score_documentaire = normaliser_score(score_documentaire_brut)
    if traitement.get("hors_champ_rgpd"):
        score_vigilance = 0
        score_conformite_globale = 100
        niveau = "Hors champ RGPD"
    else:
        score_vigilance = min(100, score_normalise + round(score_documentaire * 0.6))
        score_conformite_globale = max(0, 100 - score_vigilance)
        niveau = determiner_niveau_risque(score_vigilance)
    nombre_alertes_total = len(violations) + len(documentary_points)

    # Q3
    q2_gaps = build_structured_gaps(violations)
    q2_documentary_gaps = build_structured_gaps(documentary_points)
    q2_axes = construire_resume_axes_conformite(traitement, violations, documentary_points)
    q2_referentiels = construire_matrice_referentiels(violations, documentary_points)
    q2_preuves = construire_preuves_conformite(traitement, cartographie, base_legale_analyse, documentary_points)
    register_entry = build_register_entry(traitement, cartographie, base_legale_analyse, niveau)

    return {
        "agent": "A - Data Mapper & Compliance",
        "intelligence": {
            "description_fournie": bool(description),
            "champs_inferes": list(inferred.keys()) if description and 'inferred' in dir() and inferred else [],
            "rag_actif": _rag_instance is not None,
            "rag_ignore_mode_source": bool(traitement.get("fast_source_analysis")),
            "qalitas_mode": bool(traitement.get("_qalitas_module")),
            "qalitas_module": traitement.get("_qalitas_module"),
            "qalitas_records": traitement.get("_record_count"),
            "qalitas_detected_fields": traitement.get("_detected_fields") if traitement.get("_qalitas_module") else None,
            "gmao_mode": bool(traitement.get("_gmao_module")),
            "gmao_module": traitement.get("_gmao_module"),
            "gmao_records": traitement.get("_record_count"),
            "gmao_detected_fields": traitement.get("_detected_fields") if traitement.get("_gmao_module") else None,
            "hors_champ_rgpd": bool(traitement.get("hors_champ_rgpd")),
            "scope_reason": traitement.get("scope_reason"),
        },
        "q1_cartographie": cartographie,
        "q2_conformite": {
            "violations": violations,
            "nombre_violations": len(violations),
            "points_documentaires": documentary_points,
            "nombre_points_documentaires": len(documentary_points),
            "nombre_alertes_total": nombre_alertes_total,
            "score_brut": score_brut,
            "score_normalise": score_normalise,
            "score_documentaire_brut": score_documentaire_brut,
            "score_documentaire": score_documentaire,
            "score_vigilance": score_vigilance,
            "score_conformite_globale": score_conformite_globale,
            "niveau_risque": niveau,
            "niveau_vigilance": niveau,
            "hors_champ_rgpd": bool(traitement.get("hors_champ_rgpd")),
            "scope_reason": traitement.get("scope_reason"),
            "gaps": q2_gaps,
            "gaps_documentaires": q2_documentary_gaps,
            "axes_conformite": q2_axes,
            "matrice_referentiels": q2_referentiels,
            "preuves_conformite": q2_preuves
        },
        "q3_base_legale": base_legale_analyse,
        "q1_register": register_entry
    }
