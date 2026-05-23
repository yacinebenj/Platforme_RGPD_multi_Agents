"""
Shared helper functions for source-system web connectors.

This module keeps login/fetch plumbing separate from RGPD detection logic.
Both QALITAS and GMAO use the same patterns:
- extract CSRF tokens
- resolve company/site IDs from list payloads
- fetch JSON from one or more fallback endpoints
- detect expired sessions and retry after re-login
"""

from __future__ import annotations

import json
import logging
import re
from typing import Callable

import requests


def extract_csrf_token(html: str) -> str:
    match = re.search(r'<input[^>]*name="__RequestVerificationToken"[^>]*value="([^"]+)"', html or "")
    if match:
        return match.group(1)
    match = re.search(r'<meta[^>]*name="__RequestVerificationToken"[^>]*content="([^"]+)"', html or "")
    if match:
        return match.group(1)
    return ""


def get_id_from_list(items: list, name_key: str, target_name: str) -> str:
    id_keys = ["Id", "id", "Value", "value", "ID"]
    name_keys = [name_key, "Name", "name", "Label", "label", "text", "Text"]
    for item in items or []:
        for key in name_keys:
            value = str(item.get(key, ""))
            if target_name.lower() in value.lower():
                for id_key in id_keys:
                    if id_key in item:
                        return str(item[id_key])
    if items:
        for id_key in id_keys:
            if id_key in items[0]:
                return str(items[0][id_key])
    return ""


def decode_json_payload(text: str, response: requests.Response, endpoint: str, source_name: str):
    try:
        return response.json()
    except ValueError:
        stripped = (text or "").lstrip()
        if not stripped:
            return []
        json_start = min([index for index in [stripped.find("{"), stripped.find("[")] if index >= 0], default=-1)
        if json_start >= 0:
            try:
                return json.loads(stripped[json_start:])
            except Exception as exc:  # pragma: no cover - defensive branch
                raise ValueError(
                    f"{source_name} non-JSON response from {endpoint}: {stripped[:200]}"
                ) from exc
        raise ValueError(f"{source_name} non-JSON response from {endpoint}: {stripped[:200]}")


def extract_records_from_payload(payload) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ["data", "Data", "items", "Items", "result", "Result", "value"]:
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        return [payload]
    return []


def fetch_records_from_endpoints(
    *,
    session: requests.Session,
    base_url: str,
    module: str,
    endpoints: list[str],
    request_params: dict | None,
    timeout: int,
    retry_timeout: int | None,
    source_name: str,
    relogin: Callable[[], None],
) -> list:
    last_error = None
    preferred_error = None

    for endpoint in endpoints:
        url = f"{base_url}{endpoint}"
        try:
            response = session.get(url, params=request_params, timeout=timeout, verify=False)
            if response.status_code == 404:
                last_error = requests.HTTPError(f"404 Client Error: Not Found for url: {url}")
                continue
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            text = response.text or ""
            print(f"[{source_name}] Fetch {module} via {endpoint} - Content-Type: {content_type}")
            print(f"[{source_name}] Response preview: {text[:300]}")

            if "html" in content_type.lower() or text.lstrip().startswith("<"):
                print(f"[{source_name}] Got HTML - session expired, re-logging in")
                relogin()
                response = session.get(url, params=request_params, timeout=timeout, verify=False)
                if response.status_code == 404:
                    last_error = requests.HTTPError(f"404 Client Error: Not Found for url: {url}")
                    continue
                response.raise_for_status()
                text = response.text or ""

            payload = decode_json_payload(text, response, endpoint, source_name)
            return extract_records_from_payload(payload)
        except Exception as exc:
            last_error = exc
            if preferred_error is None:
                preferred_error = exc
            logging.warning(f"[{source_name}] Fetch attempt failed for {module} via {endpoint}: {exc}")
            if retry_timeout and "timed out" in str(exc).lower():
                try:
                    response = session.get(url, params=request_params, timeout=retry_timeout, verify=False)
                    if response.status_code == 404:
                        last_error = requests.HTTPError(f"404 Client Error: Not Found for url: {url}")
                        continue
                    response.raise_for_status()
                    payload = decode_json_payload(response.text or "", response, endpoint, source_name)
                    return extract_records_from_payload(payload)
                except Exception as retry_err:
                    last_error = retry_err

    final_error = preferred_error or last_error
    logging.error(f"[{source_name}] Fetch error for {module}: {final_error}")
    raise final_error
