import os
import re
import tempfile
from typing import Dict, List

import pdfplumber
import pytesseract
from PIL import Image


PATTERNS = {
    "nom_prenom": {
        "regex": re.compile(
            r"(?:^|\n)\s*(?:Nom|Name)\s*:\s*([A-Za-zÀ-ÿ'-]+(?:\s+[A-Za-zÀ-ÿ'-]+)+)",
            re.IGNORECASE,
        ),
        "type": "personnelle",
        "criticite": "moyenne",
        "article": "RGPD Art. 4 - Identite de la personne",
    },
    "adresse": {
        "regex": re.compile(r"\b(?:Adresse|Address)\s*:\s*([^\n]+)", re.IGNORECASE),
        "type": "personnelle",
        "criticite": "moyenne",
        "article": "RGPD Art. 4 - Adresse personnelle",
    },
    "email": {
        "regex": re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"),
        "type": "personnelle",
        "criticite": "moyenne",
        "article": "RGPD Art. 4 - Donnee a caractere personnel",
    },
    "id_card": {
        "regex": re.compile(r"\b(?:Numero\s+de\s+carte\s+d[' ]identite|Carte\s+d[' ]identite|CIN)\s*:\s*([A-Z]{1,3}\d{5,10})", re.IGNORECASE),
        "type": "personnelle",
        "criticite": "elevee",
        "article": "RGPD Art. 5 - Donnees d'identification a proteger",
    },
    "nss": {
        "regex": re.compile(r"\b(?:Numero\s+de\s+securite\s+sociale|NSS|NIR)\s*:\s*([12]\s?\d{2}(?:\s?\d{2}){2}\s?\d{3}\s?\d{3}\s?\d{2})", re.IGNORECASE),
        "type": "sensible",
        "criticite": "critique",
        "article": "RGPD Art. 9 - Donnee sensible / protection sociale",
    },
    "iban": {
        "regex": re.compile(r"\b(?:IBAN\s*:?\s*)?([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7})\b", re.IGNORECASE),
        "type": "critique",
        "criticite": "critique",
        "article": "RGPD Art. 32 - Protection renforcee des donnees financieres",
    },
    "medical_info": {
        "regex": re.compile(r"\b(?:Situation\s+medicale|Informations?\s+medicales?|Sante)\s*:\s*([^\n]+)", re.IGNORECASE),
        "type": "sensible",
        "criticite": "elevee",
        "article": "RGPD Art. 9 - Donnees de sante",
    },
    "date_naissance": {
        "regex": re.compile(r"(?:Date\s+de\s+naissance)\s*:\s*((?:0?[1-9]|[12][0-9]|3[01])(?:[/-]\d{1,2}[/-](?:19|20)\d{2}|\s+(?:janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre)\s+(?:19|20)\d{2}))", re.IGNORECASE),
        "type": "personnelle",
        "criticite": "moyenne",
        "article": "RGPD Art. 4 - Donnee personnelle",
    },
    "gps_coords": {
        "regex": re.compile(r"\b-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}\b"),
        "type": "sensible",
        "criticite": "elevee",
        "article": "RGPD Art. 9 - Localisation sensible",
    },
    "ip_address": {
        "regex": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        "type": "personnelle",
        "criticite": "faible",
        "article": "RGPD Art. 4 - Identifiant en ligne",
    },
    "phone_intl": {
        "regex": re.compile(r"\+\d{1,3}(?:[\s.-]?\d{1,3}){3,6}"),
        "type": "personnelle",
        "criticite": "moyenne",
        "article": "RGPD Art. 4 - Coordonnees personnelles",
    },
}


SEVERITY_ORDER = {"faible": 1, "moyenne": 2, "elevee": 3, "critique": 4}
PATTERN_PRIORITY = {
    "nss": 100,
    "iban": 95,
    "medical_info": 90,
    "id_card": 85,
    "date_naissance": 80,
    "gps_coords": 75,
    "adresse": 70,
    "nom_prenom": 65,
    "email": 60,
    "ip_address": 50,
    "phone_intl": 10,
}


def _context(text: str, start: int, end: int, size: int = 45) -> str:
    left = max(0, start - size)
    right = min(len(text), end + size)
    return " ".join(text[left:right].replace("\n", " ").split())


def _dedupe_findings(findings: List[Dict]) -> List[Dict]:
    unique = []
    seen = set()
    for item in findings:
        key = (item.get("pattern"), item.get("extrait"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _overlaps(existing_ranges: List[tuple], start: int, end: int) -> bool:
    for a, b in existing_ranges:
        if start < b and end > a:
            return True
    return False


def _max_criticite(findings: List[Dict]) -> str:
    if not findings:
        return "faible"
    return max(findings, key=lambda x: SEVERITY_ORDER.get(x.get("criticite", "faible"), 1)).get("criticite", "faible")


def _recommendation(findings: List[Dict], file_type: str, extraction_method: str, error: str = "") -> Dict:
    criticite = _max_criticite(findings)
    if error and not findings:
        return {"action_requise": False, "recommandation": f"Analyse partielle. {error}"}
    if not findings:
        return {"action_requise": False, "recommandation": f"Aucune donnee personnelle evidente detectee via {extraction_method}."}
    if criticite in {"critique", "elevee"}:
        return {
            "action_requise": True,
            "recommandation": f"Verifier ce fichier {file_type}, limiter le partage et definir une mesure de protection adaptee.",
        }
    return {
        "action_requise": False,
        "recommandation": "Des donnees personnelles ont ete detectees. Verifier la finalite, la conservation et l'acces.",
    }


def _extract_text_from_pdf(path: str) -> str:
    parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def _extract_text_from_image(path: str) -> str:
    with Image.open(path) as img:
        return pytesseract.image_to_string(img, lang="eng+fra")


def _detect_file_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".gif"}:
        return "image"
    if ext in {".mp3", ".wav", ".m4a", ".ogg", ".flac"}:
        return "audio"
    return "unknown"


def _find_label_value(text: str, labels: List[str]) -> str:
    for label in labels:
        match = re.search(rf"{label}\s*:\s*([^\n]+)", text, re.IGNORECASE)
        if match:
            return " ".join(match.group(1).split()).strip()
    return ""


def _extract_named_name(text: str) -> List[Dict]:
    value = _find_label_value(text, ["Nom", "Name"])
    if not value:
        return []

    # Keep the name part only when the OCR/PDF line continues with another field label.
    split_labels = [
        "Date de naissance",
        "Adresse",
        "Email",
        "Numero de telephone",
        "Numéro de téléphone",
        "Telephone",
        "Téléphone",
        "Données sensibles",
        "Donnees sensibles",
        "Numero de carte",
        "Numéro de carte",
        "Numero de securite sociale",
        "Numéro de sécurité sociale",
        "IBAN",
        "Situation medicale",
        "Situation médicale",
        "Date",
    ]

    for marker in split_labels:
        parts = re.split(rf"\b{marker}\b", value, maxsplit=1, flags=re.IGNORECASE)
        if parts:
            value = parts[0].strip(" ,;:-")

    # Remove obvious legal-document filler that is not part of a person's name.
    value = re.sub(
        r"\b(?:document|contrat|confidentialite|confidentialité|exemple|fictif|clause|legal|légal)\b",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = " ".join(value.split()).strip(" ,;:-")

    if not re.fullmatch(r"[A-Za-zÀ-ÿ'-]+(?:\s+[A-Za-zÀ-ÿ'-]+)+", value or ""):
        return []

    match = re.search(rf"(?:Nom|Name)\s*:\s*{re.escape(value)}", text, re.IGNORECASE)
    if not match:
        return []

    start, end = match.span()
    return [{
        "pattern": "nom_prenom",
        "type": PATTERNS["nom_prenom"]["type"],
        "criticite": PATTERNS["nom_prenom"]["criticite"],
        "extrait": value,
        "contexte": _context(text, start, end),
        "article": PATTERNS["nom_prenom"]["article"],
    }]


def _extract_text(path: str, file_type: str, filename: str, content: bytes) -> Dict:
    if file_type == "pdf":
        return {"text": _extract_text_from_pdf(path), "method": "pdfplumber", "error": ""}
    if file_type == "image":
        return {"text": _extract_text_from_image(path), "method": "tesseract", "error": ""}
    if file_type == "audio":
        return {"text": "", "method": "metadata-only", "error": "Audio transcription is not connected yet in this version."}
    if os.path.splitext(filename)[1].lower() in {".txt", ".csv", ".json", ".log"}:
        return {"text": content.decode("utf-8", errors="ignore"), "method": "text-fallback", "error": ""}
    return {"text": "", "method": "unsupported", "error": "Unsupported file type."}


def scan_unstructured_file(filename: str, content: bytes) -> Dict:
    file_type = _detect_file_type(filename)
    suffix = os.path.splitext(filename)[1] or ".bin"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        try:
            extracted = _extract_text(tmp_path, file_type, filename, content)
        except Exception as extraction_error:
            extracted = {"text": "", "method": "failed", "error": f"Extraction failed: {str(extraction_error)}"}

        text = extracted["text"] or ""
        extraction_method = extracted["method"]
        error = extracted["error"]

        findings = _extract_named_name(text)
        occupied_ranges = []
        for item in findings:
            excerpt = item["extrait"]
            match = re.search(rf"(?:Nom|Name)\s*:\s*{re.escape(excerpt)}", text, re.IGNORECASE)
            if match:
                occupied_ranges.append(match.span())
        ordered_patterns = sorted(PATTERNS.items(), key=lambda item: PATTERN_PRIORITY.get(item[0], 0), reverse=True)
        for pattern_name, meta in ordered_patterns:
            if pattern_name == "nom_prenom":
                continue
            for match in meta["regex"].finditer(text):
                start, end = match.span()
                if _overlaps(occupied_ranges, start, end):
                    continue
                excerpt = match.group(1) if match.lastindex else match.group(0)
                excerpt = " ".join(str(excerpt).split())
                findings.append({
                    "pattern": pattern_name,
                    "type": meta["type"],
                    "criticite": meta["criticite"],
                    "extrait": excerpt,
                    "contexte": _context(text, start, end),
                    "article": meta["article"],
                })
                occupied_ranges.append((start, end))

        findings = _dedupe_findings(findings)
        criticite_globale = _max_criticite(findings)
        return {
            "filename": filename,
            "file_type": file_type,
            "nb_findings": len(findings),
            "criticite_globale": criticite_globale,
            "extraction_method": extraction_method,
            "findings": findings,
            "rgpd_impact": _recommendation(findings, file_type, extraction_method, error),
            "error": error,
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
