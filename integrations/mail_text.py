import html
import re
import unicodedata
from typing import Any


HEADER_REPLY_MARKERS = [
    "\nDe : ",
    "\nDe:",
    "\nFrom: ",
    "\nEnvoye : ",
    "\nEnvoye:",
    "\nSent: ",
    "\nSubject: ",
    "\nObjet : ",
    "\n-----Original Message-----",
    "\n________________________________",
    "\n>",
]

DSAR_STRONG_KEYWORDS = [
    "dsar",
    "droit d acces",
    "droit d effacement",
    "droit a l oubli",
    "droit d opposition",
    "droit a la portabilite",
    "droit a la limitation",
    "decision automatisee",
    "demande rgpd",
    "access request",
    "data subject request",
    "delete my data",
    "erase my data",
]

DSAR_INTENT_KEYWORDS = [
    "copie de mes donnees",
    "copie de toutes les donnees",
    "quelles donnees",
    "quelles informations",
    "informations me concernant",
    "donnees me concernant",
    "transmettre une copie",
    "obtenir une copie",
    "mettre a jour mes donnees",
    "corriger mes donnees",
    "corriger mon",
    "modifier mon",
    "modifier mon numero",
    "je veux modifier",
    "je veut modifier",
    "je souhaite modifier",
    "je souhaite supprimer",
    "je souhaite effacer",
    "effacer toutes les donnees",
    "effacer toutes mes donnees",
    "je m oppose",
    "je souhaite m opposer",
    "format lisible",
    "format structure",
    "exporter mes donnees",
    "exercer mes droits",
    "exercer mon droit",
]

DSAR_CONTEXT_KEYWORDS = [
    "rgpd",
    "donnees personnelles",
    "donnees me concernant",
    "mes donnees",
    "mes informations",
    "me concernant",
    "privacy",
    "personal data",
]

FIRST_PERSON_MARKERS = [
    " je ",
    " j ",
    " moi ",
    " mon ",
    " ma ",
    " mes ",
    " me ",
    " i ",
    " my ",
    " me ",
]

PROMOTIONAL_MARKERS = [
    "view in browser",
    "voir dans le navigateur",
    "open in browser",
    "manage preferences",
    "unsubscribe",
    "desabonner",
    "se desabonner",
    "privacy policy",
    "politique de confidentialite",
    "terms of use",
    "conditions d utilisation",
    "newsletter",
    "digest",
    "weekly update",
    "preferences center",
]

LOW_SIGNAL_LINE_PATTERNS = [
    r"^\s*view in browser\b",
    r"^\s*voir (ce message|dans le navigateur)\b",
    r"^\s*open in browser\b",
    r"^\s*manage preferences\b",
    r"^\s*unsubscribe\b",
    r"^\s*(privacy policy|terms of use|cookie policy)\b",
    r"^\s*this email was sent to\b",
    r"^\s*you received this email because\b",
    r"^\s*sent from my (iphone|android|mobile device)\b",
    r"^\s*https?://\S+\s*$",
]


def normalise_search_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"[^\w\s@']", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_latest_reply_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    cut_positions = [raw.find(marker) for marker in HEADER_REPLY_MARKERS if raw.find(marker) != -1]
    if cut_positions:
        raw = raw[: min(cut_positions)]
    return raw.strip()


def strip_html(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"(?is)<(script|style|head|title|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|td|h1|h2|h3|h4|h5|h6)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def looks_like_html(text: str) -> bool:
    raw = text or ""
    return bool(re.search(r"<[a-z][^>]*>", raw, flags=re.IGNORECASE))


def clean_mail_text(text: str, is_html: bool | None = None) -> str:
    raw = text or ""
    html_hint = looks_like_html(raw) if is_html is None else is_html
    cleaned = strip_html(raw) if html_hint else html.unescape(raw)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for original_line in cleaned.splitlines():
        line = re.sub(r"\s+", " ", original_line).strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        lower = line.lower()
        if any(re.match(pattern, lower, flags=re.IGNORECASE) for pattern in LOW_SIGNAL_LINE_PATTERNS):
            continue
        if "http://" in lower or "https://" in lower:
            if not any(keyword in lower for keyword in DSAR_STRONG_KEYWORDS + DSAR_INTENT_KEYWORDS + DSAR_CONTEXT_KEYWORDS):
                continue
        lines.append(line)
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def classify_dsar_candidate(subject: str, body: str, sender: str = "") -> dict[str, Any]:
    subject_clean = clean_mail_text(subject or "", is_html=False)
    latest_body = extract_latest_reply_text(body or "")
    body_clean = clean_mail_text(latest_body)
    haystack = normalise_search_text(f"{subject_clean} {body_clean}")
    sender_norm = normalise_search_text(sender or "")

    strong_hits = [keyword for keyword in DSAR_STRONG_KEYWORDS if keyword in haystack]
    intent_hits = [keyword for keyword in DSAR_INTENT_KEYWORDS if keyword in haystack]
    context_hits = [keyword for keyword in DSAR_CONTEXT_KEYWORDS if keyword in haystack]
    promo_hits = [keyword for keyword in PROMOTIONAL_MARKERS if keyword in haystack or keyword in sender_norm]
    first_person = any(marker in f" {haystack} " for marker in FIRST_PERSON_MARKERS)

    score = 0
    if strong_hits:
        score += 8 + min(len(strong_hits), 3) * 2
    if intent_hits:
        score += 3 + min(len(intent_hits), 3)
    if context_hits:
        score += 2 + min(len(context_hits), 2)
    if intent_hits and context_hits:
        score += 4
    if first_person:
        score += 2
    if len(body_clean) >= 80:
        score += 1
    if promo_hits:
        score -= 6 + min(len(promo_hits), 2)
    if "no reply" in sender_norm or "noreply" in sender_norm or "newsletter" in sender_norm:
        score -= 5
    if not body_clean:
        score -= 6

    accepted = bool(
        strong_hits
        or (intent_hits and context_hits and first_person)
        or score >= 8
    )

    return {
        "accepted": accepted,
        "score": score,
        "subject": subject_clean,
        "text": body_clean,
        "reasons": {
            "strong_hits": strong_hits,
            "intent_hits": intent_hits,
            "context_hits": context_hits,
            "promo_hits": promo_hits,
            "first_person": first_person,
        },
    }

