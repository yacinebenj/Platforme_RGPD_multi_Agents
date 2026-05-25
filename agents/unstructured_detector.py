"""
agents/unstructured_detector.py
================================
Q1 — Unstructured Data Detection (Cahier des charges §3.1)

Scans arbitrary files (PDF, images, audio) for personal data using:
  • pdfplumber   → PDF text extraction
  • pytesseract  → Image OCR (PNG, JPG, TIFF, BMP)
  • whisper      → Audio transcription (MP3, WAV, M4A, OGG)
  • spacy + regex → NLP-based PII detection

Public API
----------
scan_file(file_path)          → UnstructuredScanResult
scan_bytes(data, filename)    → UnstructuredScanResult  (for uploaded bytes)
scan_records_for_urls(records)→ list[UnstructuredScanResult]  (QALITAS records)
"""

from __future__ import annotations

import io
import os
import re
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Regex patterns for PII ────────────────────────────────────────────────

PII_PATTERNS: dict[str, re.Pattern] = {
    "email":        re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I),
    "phone_intl":   re.compile(r"(?:\+|00)\d{1,3}[\s.\-]?\(?\d{1,4}\)?[\s.\-]?\d{2,4}[\s.\-]?\d{2,4}"),
    "phone_fr":     re.compile(r"0[1-9](?:[\s.\-]?\d{2}){4}"),
    "nss":          re.compile(r"\b[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b"),
    "cin_tn":       re.compile(r"\b[0-9]{8}\b"),
    "gps_coords":   re.compile(r"(?:lat(?:itude)?|lon(?:gitude)?)\s*[:=]\s*[\-+]?\d{1,3}\.\d+", re.I),
    "iban":         re.compile(r"\b[A-Z]{2}\d{2}(?:[\s.\-]?[A-Z0-9]){11,30}\b"),
    "ip_address":   re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "date_naissance": re.compile(
        r"\b(?:né(?:e)?\s+le|date\s+de\s+naissance|dob)\s*[:/]?\s*\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}",
        re.I
    ),
    "nom_prenom":   re.compile(
        r"\b(?:M\.?|Mme\.?|Mlle\.?|Mr\.?|Dr\.?|Prof\.?)\s+[A-ZÉÈÊËÀÂÙÛÜ][a-zéèêëàâùûü]+(?:\s+[A-ZÉÈÊËÀÂÙÛÜ][a-zéèêëàâùûü]+)+",
        re.UNICODE
    ),
    "adresse":      re.compile(
        r"\b\d{1,4}(?:,|\s)\s*(?:rue|avenue|bd|boulevard|allée|chemin|impasse|place|route)\s+[\w\s\-]+",
        re.I | re.UNICODE
    ),
}

# Map pattern key → RGPD criticité
PII_CRITICITE: dict[str, str] = {
    "nss":            "critique",
    "iban":           "critique",
    "gps_coords":     "elevee",
    "date_naissance": "elevee",
    "cin_tn":         "elevee",
    "email":          "moyenne",
    "phone_intl":     "moyenne",
    "phone_fr":       "moyenne",
    "nom_prenom":     "moyenne",
    "adresse":        "moyenne",
    "ip_address":     "faible",
}

PII_TYPE: dict[str, str] = {
    "nss":            "sensible",
    "iban":           "critique",
    "gps_coords":     "sensible",
    "date_naissance": "personnelle",
    "cin_tn":         "personnelle",
    "email":          "personnelle",
    "phone_intl":     "personnelle",
    "phone_fr":       "personnelle",
    "nom_prenom":     "personnelle",
    "adresse":        "personnelle",
    "ip_address":     "personnelle",
}

# ── Result dataclass ─────────────────────────────────────────────────────

@dataclass
class PIIFinding:
    pattern_name: str
    matched_value: str
    type_donnee: str        # personnelle / sensible / critique
    criticite: str          # faible / moyenne / elevee / critique
    context: str = ""       # surrounding text snippet


@dataclass
class UnstructuredScanResult:
    source_file: str
    file_type: str          # pdf / image / audio / unknown
    extraction_method: str  # pdfplumber / tesseract / whisper / none
    raw_text: str = ""
    findings: list[PIIFinding] = field(default_factory=list)
    error: Optional[str] = None

    # ── Derived helpers ──────────────────────────────────────────────────

    @property
    def has_personal_data(self) -> bool:
        return len(self.findings) > 0

    @property
    def criticite_globale(self) -> str:
        order = {"critique": 4, "elevee": 3, "moyenne": 2, "faible": 1}
        if not self.findings:
            return "faible"
        return max(self.findings, key=lambda f: order.get(f.criticite, 0)).criticite

    @property
    def types_detectes(self) -> list[str]:
        return sorted({f.pattern_name for f in self.findings})

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "file_type": self.file_type,
            "extraction_method": self.extraction_method,
            "has_personal_data": self.has_personal_data,
            "criticite_globale": self.criticite_globale,
            "types_detectes": self.types_detectes,
            "nb_findings": len(self.findings),
            "findings": [
                {
                    "pattern": f.pattern_name,
                    "type": f.type_donnee,
                    "criticite": f.criticite,
                    "extrait": f.matched_value[:60] + ("…" if len(f.matched_value) > 60 else ""),
                    "contexte": f.context[:120],
                }
                for f in self.findings
            ],
            "error": self.error,
        }

# ── PII detection on plain text ──────────────────────────────────────────

def _detect_pii_in_text(text: str) -> list[PIIFinding]:
    """Run all regex patterns and optionally spacy NER on extracted text."""
    findings: list[PIIFinding] = []
    seen: set[str] = set()

    for name, pattern in PII_PATTERNS.items():
        for match in pattern.finditer(text):
            val = match.group(0).strip()
            key = (name, val[:40])
            if key in seen:
                continue
            seen.add(key)
            start = max(0, match.start() - 40)
            end   = min(len(text), match.end() + 40)
            ctx   = text[start:end].replace("\n", " ").strip()
            findings.append(PIIFinding(
                pattern_name=name,
                matched_value=val,
                type_donnee=PII_TYPE.get(name, "personnelle"),
                criticite=PII_CRITICITE.get(name, "faible"),
                context=ctx,
            ))

    # Optional: spacy NER (PERSON, LOC entities) — graceful fallback
    try:
        import spacy
        _nlp = spacy.load("fr_core_news_sm")
        doc = _nlp(text[:100_000])  # limit to avoid OOM
        for ent in doc.ents:
            if ent.label_ in ("PER", "PERSON"):
                val = ent.text.strip()
                key = ("spacy_person", val[:40])
                if key not in seen and len(val) > 4:
                    seen.add(key)
                    findings.append(PIIFinding(
                        pattern_name="spacy_personne",
                        matched_value=val,
                        type_donnee="personnelle",
                        criticite="moyenne",
                        context=f"NER spacy — entité: {ent.label_}",
                    ))
    except Exception:
        pass  # spacy not installed or model missing — regex only

    return findings

# ── Extractors ───────────────────────────────────────────────────────────

def _extract_pdf(file_path: str) -> tuple[str, str]:
    """Extract text from PDF using pdfplumber. Returns (text, method)."""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return "\n".join(text_parts), "pdfplumber"
    except ImportError:
        return "", "pdfplumber_missing"
    except Exception as e:
        logger.warning(f"[UnstructuredDetector] PDF extraction error: {e}")
        return "", f"error: {e}"


def _extract_image(file_path: str) -> tuple[str, str]:
    """  an image file using pytesseract. Returns (text, method)."""
    try:
        from PIL import Image
        import pytesseract
        img = Image.open(file_path)
        text = pytesseract.image_to_string(img, lang="fra+eng")
        return text, "tesseract"
    except ImportError:
        return "", "pytesseract_missing"
    except Exception as e:
        logger.warning(f"[UnstructuredDetector] OCR error: {e}")
        return "", f"error: {e}"


def _extract_audio(file_path: str) -> tuple[str, str]:
    """Transcribe audio using openai-whisper. Returns (text, method)."""
    try:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(file_path, language="fr")
        return result.get("text", ""), "whisper"
    except ImportError:
        return "", "whisper_missing"
    except Exception as e:
        logger.warning(f"[UnstructuredDetector] Audio transcription error: {e}")
        return "", f"error: {e}"

# ── Router ───────────────────────────────────────────────────────────────

_EXT_PDF   = {".pdf"}
_EXT_IMG   = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp"}
_EXT_AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}


def scan_file(file_path: str) -> UnstructuredScanResult:
    """
    Scan a single file for personal data.
    Automatically routes to the correct extractor based on file extension.
    """
    path = Path(file_path)
    ext  = path.suffix.lower()
    name = path.name

    if ext in _EXT_PDF:
        file_type = "pdf"
        raw_text, method = _extract_pdf(file_path)
    elif ext in _EXT_IMG:
        file_type = "image"
        raw_text, method = _extract_image(file_path)
    elif ext in _EXT_AUDIO:
        file_type = "audio"
        raw_text, method = _extract_audio(file_path)
    else:
        return UnstructuredScanResult(
            source_file=name, file_type="unknown",
            extraction_method="none",
            error=f"Unsupported file type: {ext}"
        )

    if not raw_text:
        return UnstructuredScanResult(
            source_file=name, file_type=file_type,
            extraction_method=method, raw_text="",
            error="No text extracted"
        )

    findings = _detect_pii_in_text(raw_text)
    return UnstructuredScanResult(
        source_file=name,
        file_type=file_type,
        extraction_method=method,
        raw_text=raw_text[:2000],   # store first 2 kchars only
        findings=findings,
    )


def scan_bytes(data: bytes, filename: str) -> UnstructuredScanResult:
    """
    Scan file content provided as raw bytes (e.g. FastAPI UploadFile).
    Writes to a temp file, scans, then cleans up.
    """
    ext = Path(filename).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        result = scan_file(tmp_path)
        result.source_file = filename
        return result
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def scan_records_for_urls(records: list[dict]) -> list[UnstructuredScanResult]:
    """
    Scan QALITAS records for attachment URLs / file paths and scan each one.
    Looks for fields that typically hold file references:
      FileUrl, AttachmentUrl, DocumentPath, PhotoUrl, AudioUrl, FilePath, ...
    """
    URL_FIELDS = {
        "FileUrl", "AttachmentUrl", "DocumentPath", "PhotoUrl",
        "AudioUrl", "FilePath", "ImageUrl", "ReportPath",
        "fileUrl", "attachmentUrl", "documentPath", "photoUrl",
    }

    results: list[UnstructuredScanResult] = []
    seen_urls: set[str] = set()

    for record in records:
        for field_name, value in record.items():
            if field_name not in URL_FIELDS:
                continue
            if not isinstance(value, str) or not value.strip():
                continue
            url = value.strip()
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Local file path
            if os.path.exists(url):
                results.append(scan_file(url))
            else:
                # Remote URL — attempt download
                try:
                    import requests
                    resp = requests.get(url, timeout=15, verify=False)
                    if resp.status_code == 200:
                        filename = url.split("/")[-1].split("?")[0] or "file"
                        result = scan_bytes(resp.content, filename)
                        result.source_file = url
                        results.append(result)
                    else:
                        results.append(UnstructuredScanResult(
                            source_file=url, file_type="unknown",
                            extraction_method="none",
                            error=f"HTTP {resp.status_code}"
                        ))
                except Exception as e:
                    results.append(UnstructuredScanResult(
                        source_file=url, file_type="unknown",
                        extraction_method="none",
                        error=str(e)
                    ))

    return results


def aggregate_scan_results(results: list[UnstructuredScanResult]) -> dict:
    """
    Aggregate a list of scan results into a summary dict suitable
    for injection into Agent A q1_cartographie output.
    """
    if not results:
        return {
            "fichiers_scannes": 0,
            "fichiers_avec_donnees_personnelles": 0,
            "criticite_globale": "faible",
            "types_detectes": [],
            "details": [],
        }

    order = {"critique": 4, "elevee": 3, "moyenne": 2, "faible": 1}
    all_types: set[str] = set()
    max_crit = "faible"
    files_with_pii = 0

    for r in results:
        if r.has_personal_data:
            files_with_pii += 1
            all_types.update(r.types_detectes)
            if order.get(r.criticite_globale, 0) > order.get(max_crit, 0):
                max_crit = r.criticite_globale

    return {
        "fichiers_scannes": len(results),
        "fichiers_avec_donnees_personnelles": files_with_pii,
        "criticite_globale": max_crit,
        "types_detectes": sorted(all_types),
        "details": [r.to_dict() for r in results],
    }
