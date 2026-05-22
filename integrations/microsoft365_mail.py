import os
from typing import Any

import requests

from integrations.mail_text import classify_dsar_candidate


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class Microsoft365MailConnector:
    """Lightweight Microsoft Graph mail connector for DSAR mailbox reading."""

    def __init__(self):
        self.tenant_id = os.getenv("M365_TENANT_ID", "").strip()
        self.client_id = os.getenv("M365_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("M365_CLIENT_SECRET", "").strip()
        self.timeout = int(os.getenv("M365_TIMEOUT_SECONDS", "20"))

    def is_configured(self) -> bool:
        return bool(self.tenant_id and self.client_id and self.client_secret)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.is_configured(),
            "tenant_id_set": bool(self.tenant_id),
            "client_id_set": bool(self.client_id),
            "client_secret_set": bool(self.client_secret),
            "auth_mode": "client_credentials",
            "graph_scope": GRAPH_SCOPE,
        }

    def get_access_token(self) -> str:
        if not self.is_configured():
            raise RuntimeError("Microsoft 365 Graph credentials are not configured.")

        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        response = requests.post(
            token_url,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": GRAPH_SCOPE,
                "grant_type": "client_credentials",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Microsoft Graph token response did not include access_token.")
        return token

    def list_inbox_messages(self, mailbox_address: str, top: int = 10) -> list[dict[str, Any]]:
        token = self.get_access_token()
        response = requests.get(
            f"{GRAPH_BASE_URL}/users/{mailbox_address}/mailFolders/Inbox/messages",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            params={
                "$top": max(1, min(top, 50)),
                "$select": "id,subject,receivedDateTime,from,bodyPreview",
                "$orderby": "receivedDateTime DESC",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json().get("value", [])

    def extract_dsar_candidates(self, mailbox_address: str, top: int = 10) -> list[dict[str, Any]]:
        messages = self.list_inbox_messages(mailbox_address=mailbox_address, top=top)
        extractions = []
        for message in messages:
            subject = message.get("subject") or ""
            sender = ((message.get("from") or {}).get("emailAddress") or {}).get("address")
            classification = classify_dsar_candidate(subject, message.get("bodyPreview") or "", sender=sender or "")
            if not classification.get("accepted"):
                continue
            extractions.append({
                "message_id": message.get("id"),
                "subject": subject,
                "received_at": message.get("receivedDateTime"),
                "from": sender,
                "text": classification.get("text"),
                "relevance_score": classification.get("score", 0),
                "match_reasons": classification.get("reasons", {}),
            })
        extractions.sort(key=lambda item: item.get("relevance_score", 0), reverse=True)
        return extractions
