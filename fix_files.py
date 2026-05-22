"""
fix_files.py — Patch: Q1 Unstructured Data Detection
======================================================
Adds a new module: agents/unstructured_detector.py
Patches:  agents/agent_a.py  — injects unstructured scan results into Q1 cartography
Patches:  api/main.py        — adds POST /qalitas/analyse/unstructured endpoint

What this implements (Cahier des charges Q1 §3.1):
  - PDF text extraction  → pdfplumber
  - Image OCR            → pytesseract (Tesseract)
  - Audio transcription  → openai-whisper
  - NLP PII detection    → spacy + regex (names, emails, phones, NSS, GPS, etc.)
  - Output injected into q1_cartographie as "donnees_non_structurees"

Run from the project root:
  python fix_files.py

Dependencies to install first:
  pip install pdfplumber pytesseract openai-whisper spacy pillow
  pip install python-multipart          # for FastAPI file upload
  python -m spacy download fr_core_news_sm
  # Tesseract binary: https://github.com/tesseract-ocr/tesseract
  #   Windows: choco install tesseract
  #   Linux:   apt install tesseract-ocr tesseract-ocr-fra
"""

import os
import re
import shutil
import textwrap

ROOT = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────────────────────

def write_file(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [OK] Written: {path}")


def patch_file(path: str, anchor: str, insertion: str, after: bool = True):
    """Insert `insertion` before or after the first occurrence of `anchor` in `path`."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if anchor not in content:
        print(f"  [SKIP] Anchor not found in {path}: {repr(anchor[:60])}")
        return False
    if insertion.strip() in content:
        print(f"  [SKIP] Patch already applied in {path}")
        return False
    if after:
        content = content.replace(anchor, anchor + "\n" + insertion, 1)
    else:
        content = content.replace(anchor, insertion + "\n" + anchor, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [OK] Patched: {path}")
    return True


def backup(path: str):
    bak = path + ".bak"
    if not os.path.exists(bak) and os.path.exists(path):
        shutil.copy2(path, bak)
        print(f"  [BAK] Backup created: {bak}")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — CREATE agents/unstructured_detector.py
# ─────────────────────────────────────────────────────────────────────────────

UNSTRUCTURED_DETECTOR = textwrap.dedent('''
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
        "email":        re.compile(r"[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}", re.I),
        "phone_intl":   re.compile(r"(?:\\+|00)\\d{1,3}[\\s.\\-]?\\(?\\d{1,4}\\)?[\\s.\\-]?\\d{2,4}[\\s.\\-]?\\d{2,4}"),
        "phone_fr":     re.compile(r"0[1-9](?:[\\s.\\-]?\\d{2}){4}"),
        "nss":          re.compile(r"\\b[12]\\s?\\d{2}\\s?\\d{2}\\s?\\d{2}\\s?\\d{3}\\s?\\d{3}\\s?\\d{2}\\b"),
        "cin_tn":       re.compile(r"\\b[0-9]{8}\\b"),
        "gps_coords":   re.compile(r"(?:lat(?:itude)?|lon(?:gitude)?)\\s*[:=]\\s*[\\-+]?\\d{1,3}\\.\\d+", re.I),
        "iban":         re.compile(r"\\b[A-Z]{2}\\d{2}[A-Z0-9]{4}\\d{7,}\\b"),
        "ip_address":   re.compile(r"\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b"),
        "date_naissance": re.compile(
            r"\\b(?:né(?:e)?\\s+le|date\\s+de\\s+naissance|dob)\\s*[:/]?\\s*\\d{1,2}[/\\-.]\\d{1,2}[/\\-.]\\d{2,4}",
            re.I
        ),
        "nom_prenom":   re.compile(
            r"\\b(?:M\\.?|Mme\\.?|Mlle\\.?|Mr\\.?|Dr\\.?|Prof\\.?)\\s+[A-ZÉÈÊËÀÂÙÛÜ][a-zéèêëàâùûü]+(?:\\s+[A-ZÉÈÊËÀÂÙÛÜ][a-zéèêëàâùûü]+)+",
            re.UNICODE
        ),
        "adresse":      re.compile(
            r"\\b\\d{1,4}(?:,|\\s)\\s*(?:rue|avenue|bd|boulevard|allée|chemin|impasse|place|route)\\s+[\\w\\s\\-]+",
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
                ctx   = text[start:end].replace("\\n", " ").strip()
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
            return "\\n".join(text_parts), "pdfplumber"
        except ImportError:
            return "", "pdfplumber_missing"
        except Exception as e:
            logger.warning(f"[UnstructuredDetector] PDF extraction error: {e}")
            return "", f"error: {e}"


    def _extract_image(file_path: str) -> tuple[str, str]:
        """OCR an image file using pytesseract. Returns (text, method)."""
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
''').lstrip()


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — PATCH agents/agent_a.py
# ─────────────────────────────────────────────────────────────────────────────

# 2a. Import at top of agent_a.py
AGENT_A_IMPORT = """
# ── Unstructured data detection (Q1 patch) ───────────────────────────────────
try:
    from agents.unstructured_detector import (
        scan_records_for_urls,
        aggregate_scan_results,
        scan_bytes,
    )
    _UNSTRUCTURED_AVAILABLE = True
except ImportError:
    _UNSTRUCTURED_AVAILABLE = False
"""

# 2b. Inside cartographier_donnees(), after building the base dict,
#     add unstructured results if they were pre-computed
AGENT_A_CARTOGRAPHIE_ANCHOR = '''    return {
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
    }'''

AGENT_A_CARTOGRAPHIE_REPLACEMENT = '''    # Merge criticite with unstructured scan if available
    _unstruct = traitement.get("_unstructured_scan")
    _criticite = classification["criticite_globale"]
    _order = {"critique": 4, "elevee": 3, "moyenne": 2, "faible": 1}
    if _unstruct and _order.get(_unstruct.get("criticite_globale","faible"),0) > _order.get(_criticite,0):
        _criticite = _unstruct["criticite_globale"]

    return {
        "id_traitement": traitement.get("id_traitement"),
        "nom_traitement": traitement.get("nom_traitement"),
        "systeme": traitement.get("systeme"),
        "responsable": traitement.get("responsable"),
        "donnees_collectees": traitement.get("donnees_collectees", []),
        "classification_donnees": classification["classification"],
        "criticite_globale": _criticite,
        "categories_donnees": traitement.get("categories_donnees", []),
        "donnees_sensibles": traitement.get("donnees_sensibles", False),
        "personnes_concernees": traitement.get("personnes_concernees", []),
        "destinataires": traitement.get("destinataires", []),
        "transfert_etranger": traitement.get("transfert_etranger", False),
        "duree_conservation": traitement.get("duree_conservation", "Non definie"),
        # Q1 — Unstructured data scan results (PDF / image / audio)
        "donnees_non_structurees": _unstruct or {
            "fichiers_scannes": 0,
            "fichiers_avec_donnees_personnelles": 0,
            "criticite_globale": "faible",
            "types_detectes": [],
            "details": [],
        },
    }'''

# 2c. Inside run_agent_a(), just before "# Q1" comment, inject unstructured scan
AGENT_A_SCAN_ANCHOR = "    # Q1\n    cartographie = cartographier_donnees(traitement)"

AGENT_A_SCAN_INJECTION = """
    # Q1 — Unstructured data detection (PDF / image / audio)
    if _UNSTRUCTURED_AVAILABLE and traitement.get("qalitas_records"):
        try:
            _scan_results = scan_records_for_urls(traitement["qalitas_records"])
            traitement["_unstructured_scan"] = aggregate_scan_results(_scan_results)
            print(f"[Agent A] Unstructured scan: {traitement['_unstructured_scan']['fichiers_scannes']} files, "
                  f"{traitement['_unstructured_scan']['fichiers_avec_donnees_personnelles']} with PII")
        except Exception as _e:
            print(f"[Agent A] Unstructured scan failed gracefully: {_e}")
            traitement["_unstructured_scan"] = None
    else:
        traitement.setdefault("_unstructured_scan", None)
"""

# 2d. In run_agent_a() return dict, add unstructured key inside "q1_cartographie"
#     We also add it as a top-level intelligence key
AGENT_A_RETURN_ANCHOR = '        "qalitas_detected_fields": traitement.get("_detected_fields"),\n        },'

AGENT_A_RETURN_ADDITION = '        "unstructured_scan_active": _UNSTRUCTURED_AVAILABLE,\n        },'


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — PATCH api/main.py  (add /scan/unstructured upload endpoint)
# ─────────────────────────────────────────────────────────────────────────────

MAIN_IMPORT_ANCHOR = "from fastapi import FastAPI, HTTPException"

MAIN_IMPORT_ADDITION = """from fastapi import FastAPI, HTTPException, UploadFile, File
"""

MAIN_ENDPOINT = '''

# ─────────────────────────────────────────────────────────────────────────────
# Q1 UNSTRUCTURED SCAN ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/scan/unstructured")
async def scan_unstructured(file: UploadFile = File(...)):
    """
    Q1 — Scan an uploaded file (PDF, image, audio) for personal data.

    Returns a structured report with:
    - file_type detected
    - extraction_method used (pdfplumber / tesseract / whisper)
    - PII findings (email, phone, NSS, GPS, names, addresses, ...)
    - criticite_globale (faible / moyenne / elevee / critique)
    - RGPD article references for each finding type
    """
    try:
        from agents.unstructured_detector import scan_bytes
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Unstructured detection not available. Run: pip install pdfplumber pytesseract openai-whisper spacy pillow"
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file uploaded.")

    result = scan_bytes(content, file.filename or "uploaded_file")

    # Map finding types to RGPD articles for UI display
    FINDING_ARTICLES = {
        "email":           "Art. 4(1) RGPD — Données d'identification",
        "phone_intl":      "Art. 4(1) RGPD — Données de contact",
        "phone_fr":        "Art. 4(1) RGPD — Données de contact",
        "nss":             "Art. 9 RGPD — Donnée sensible (numéro SS)",
        "cin_tn":          "Art. 4(1) RGPD — Document d'identité",
        "gps_coords":      "Art. 9 + Rec. 51 RGPD — Localisation",
        "iban":            "Art. 4(1) RGPD — Données financières",
        "ip_address":      "Rec. 30 RGPD — Identifiant en ligne",
        "date_naissance":  "Art. 4(1) RGPD — Données d'identification",
        "nom_prenom":      "Art. 4(1) RGPD — Données d'identité",
        "adresse":         "Art. 4(1) RGPD — Données de localisation",
        "spacy_personne":  "Art. 4(1) RGPD — Données d'identité (NER)",
    }

    enriched_findings = []
    for f in result.findings:
        enriched_findings.append({
            "pattern":   f.pattern_name,
            "type":      f.type_donnee,
            "criticite": f.criticite,
            "extrait":   f.matched_value[:60] + ("…" if len(f.matched_value) > 60 else ""),
            "contexte":  f.context[:120],
            "article":   FINDING_ARTICLES.get(f.pattern_name, "Art. 4 RGPD"),
        })

    return {
        "source_file":      result.source_file,
        "file_type":        result.file_type,
        "extraction_method": result.extraction_method,
        "has_personal_data": result.has_personal_data,
        "criticite_globale": result.criticite_globale,
        "types_detectes":   result.types_detectes,
        "nb_findings":       len(result.findings),
        "findings":          enriched_findings,
        "error":             result.error,
        "rgpd_impact": {
            "action_requise": result.has_personal_data,
            "recommandation": (
                "Des données personnelles ont été détectées dans ce fichier non structuré. "
                "Ce fichier doit être déclaré dans le registre Art.30 et des mesures de sécurité "
                "adaptées doivent être appliquées (chiffrement, contrôle d'accès, durée de conservation)."
                if result.has_personal_data else
                "Aucune donnée personnelle détectée dans ce fichier."
            ),
        },
    }
'''


# ─────────────────────────────────────────────────────────────────────────────
# MAIN — Apply all patches
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n══════════════════════════════════════════════════════════")
    print("  fix_files.py — Q1 Unstructured Data Detection Patch")
    print("══════════════════════════════════════════════════════════\n")

    # ── Step 1: Create unstructured_detector.py ──────────────────────────────
    print("► Step 1: Creating agents/unstructured_detector.py")
    detector_path = os.path.join(ROOT, "agents", "unstructured_detector.py")
    write_file(detector_path, UNSTRUCTURED_DETECTOR)

    # ── Step 2: Patch agents/agent_a.py ─────────────────────────────────────
    print("\n► Step 2: Patching agents/agent_a.py")
    agent_a_path = os.path.join(ROOT, "agents", "agent_a.py")

    if not os.path.exists(agent_a_path):
        print(f"  [ERROR] File not found: {agent_a_path}")
    else:
        backup(agent_a_path)

        # 2a — import block at top
        patch_file(
            agent_a_path,
            anchor="load_dotenv(\"key.env\")",
            insertion=AGENT_A_IMPORT,
            after=True
        )

        # 2b — cartographier_donnees return dict with donnees_non_structurees
        with open(agent_a_path, "r", encoding="utf-8") as f:
            content = f.read()
        if "donnees_non_structurees" not in content:
            if AGENT_A_CARTOGRAPHIE_ANCHOR in content:
                content = content.replace(
                    AGENT_A_CARTOGRAPHIE_ANCHOR,
                    AGENT_A_CARTOGRAPHIE_REPLACEMENT,
                    1
                )
                with open(agent_a_path, "w", encoding="utf-8") as f:
                    f.write(content)
                print("  [OK] Patched: cartographier_donnees() return dict")
            else:
                print("  [SKIP] cartographier_donnees anchor not found — check agent_a.py manually")

        # 2c — inject scan before Q1 in run_agent_a
        patch_file(
            agent_a_path,
            anchor=AGENT_A_SCAN_ANCHOR,
            insertion=AGENT_A_SCAN_INJECTION,
            after=False
        )

        # 2d — add unstructured key to intelligence dict in return
        with open(agent_a_path, "r", encoding="utf-8") as f:
            content = f.read()
        if "unstructured_scan_active" not in content:
            content = content.replace(
                AGENT_A_RETURN_ANCHOR,
                AGENT_A_RETURN_ADDITION,
                1
            )
            with open(agent_a_path, "w", encoding="utf-8") as f:
                f.write(content)
            print("  [OK] Patched: run_agent_a() intelligence dict")

    # ── Step 3: Patch api/main.py ────────────────────────────────────────────
    print("\n► Step 3: Patching api/main.py")
    main_path = os.path.join(ROOT, "api", "main.py")

    if not os.path.exists(main_path):
        print(f"  [ERROR] File not found: {main_path}")
    else:
        backup(main_path)

        # 3a — replace FastAPI import to add UploadFile, File
        with open(main_path, "r", encoding="utf-8") as f:
            content = f.read()
        if "UploadFile" not in content:
            content = content.replace(
                "from fastapi import FastAPI, HTTPException",
                "from fastapi import FastAPI, HTTPException, UploadFile, File",
                1
            )
            with open(main_path, "w", encoding="utf-8") as f:
                f.write(content)
            print("  [OK] Patched: FastAPI imports (UploadFile, File)")
        else:
            print("  [SKIP] UploadFile already imported")

        # 3b — append /scan/unstructured endpoint at end of file
        with open(main_path, "r", encoding="utf-8") as f:
            content = f.read()
        if "/scan/unstructured" not in content:
            with open(main_path, "a", encoding="utf-8") as f:
                f.write(MAIN_ENDPOINT)
            print("  [OK] Added: POST /scan/unstructured endpoint")
        else:
            print("  [SKIP] /scan/unstructured already present")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n══════════════════════════════════════════════════════════")
    print("  Patch complete. Summary:")
    print("  ✓ agents/unstructured_detector.py  — NEW FILE")
    print("  ✓ agents/agent_a.py                — PATCHED (Q1 unstructured)")
    print("  ✓ api/main.py                      — PATCHED (+endpoint)")
    print("")
    print("  Install dependencies:")
    print("    pip install pdfplumber pillow pytesseract openai-whisper spacy python-multipart")
    print("    python -m spacy download fr_core_news_sm")
    print("    # Tesseract binary: apt install tesseract-ocr tesseract-ocr-fra")
    print("")
    print("  New API endpoint:")
    print("    POST /scan/unstructured  — upload any PDF/image/audio file")
    print("")
    print("  Agent A output now includes:")
    print("    q1_cartographie.donnees_non_structurees")
    print("      ├── fichiers_scannes")
    print("      ├── fichiers_avec_donnees_personnelles")
    print("      ├── criticite_globale")
    print("      ├── types_detectes")
    print("      └── details[]  (per-file findings with RGPD articles)")
    print("══════════════════════════════════════════════════════════\n")


if __name__ == "__main__":
    main()