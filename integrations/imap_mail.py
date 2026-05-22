import email
import imaplib
from email.header import decode_header
from typing import Any

from integrations.mail_text import classify_dsar_candidate


class ImapMailConnector:
    """Simple IMAP inbox reader for DSAR email extraction."""

    def __init__(
        self,
        host: str,
        port: int = 993,
        username: str = "",
        password: str = "",
        folder: str = "INBOX",
        use_ssl: bool = True,
    ):
        self.host = host
        self.port = int(port or 993)
        self.username = username
        self.password = password
        self.folder = folder or "INBOX"
        self.use_ssl = use_ssl

    def _connect(self):
        if not self.host or not self.username or not self.password:
            raise RuntimeError("IMAP host, username or password is missing.")
        client = imaplib.IMAP4_SSL(self.host, self.port) if self.use_ssl else imaplib.IMAP4(self.host, self.port)
        client.login(self.username, self.password)
        client.select(self.folder)
        return client

    @staticmethod
    def _decode_value(value: bytes | str | None) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return str(value)

    @staticmethod
    def _decode_header_value(value: str | None) -> str:
        if not value:
            return ""
        parts = decode_header(value)
        out = []
        for text, enc in parts:
            if isinstance(text, bytes):
                out.append(text.decode(enc or "utf-8", errors="replace"))
            else:
                out.append(text)
        return "".join(out).strip()

    @staticmethod
    def _extract_text_from_message(message) -> str:
        if message.is_multipart():
            for part in message.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition") or "")
                if content_type == "text/plain" and "attachment" not in disposition.lower():
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    return (payload or b"").decode(charset, errors="replace").strip()
            for part in message.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    return (payload or b"").decode(charset, errors="replace").strip()
            return ""
        payload = message.get_payload(decode=True)
        charset = message.get_content_charset() or "utf-8"
        return (payload or b"").decode(charset, errors="replace").strip()

    def extract_dsar_candidates(self, top: int = 10) -> list[dict[str, Any]]:
        client = self._connect()
        try:
            status, data = client.search(None, "ALL")
            if status != "OK":
                return []
            msg_ids = data[0].split()[-max(1, min(top, 50)) :]
            msg_ids.reverse()
            results = []
            for msg_id in msg_ids:
                fetch_status, payload = client.fetch(msg_id, "(RFC822)")
                if fetch_status != "OK" or not payload or not payload[0]:
                    continue
                raw = payload[0][1]
                message = email.message_from_bytes(raw)
                subject = self._decode_header_value(message.get("Subject"))
                sender = self._decode_header_value(message.get("From"))
                body = self._extract_text_from_message(message)
                classification = classify_dsar_candidate(subject, body, sender=sender)
                if not classification.get("accepted"):
                    continue
                results.append({
                    "message_id": self._decode_value(msg_id),
                    "subject": subject,
                    "from": sender,
                    "text": str(classification.get("text") or "")[:4000],
                    "relevance_score": classification.get("score", 0),
                    "match_reasons": classification.get("reasons", {}),
                })
            results.sort(key=lambda item: item.get("relevance_score", 0), reverse=True)
            return results
        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass
