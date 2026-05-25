from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

urllib3.disable_warnings(category=InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gmao.connector import GMAO_BASE_URL, GmaoConnector, MODULE_ENDPOINTS as GMAO_MODULE_ENDPOINTS
from integrations.source_connector_base import extract_records_from_payload
from qalitas.connector import QALITAS_BASE_URL, QalitasConnector, MODULE_ENDPOINTS as QALITAS_MODULE_ENDPOINTS


STATIC_PREFIXES = (
    "/assets/",
    "/bundles/",
    "/content/",
    "/css/",
    "/fonts/",
    "/images/",
    "/img/",
    "/js/",
    "/lib/",
    "/scripts/",
    "/signalr/",
)
STATIC_EXTENSIONS = (
    ".css",
    ".gif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".js",
    ".map",
    ".png",
    ".svg",
    ".ttf",
    ".woff",
    ".woff2",
)
BLOCKED_PATH_PARTS = (
    "/account/login",
    "/account/logoff",
    "/account/logout",
    "/logout",
    "/logoff",
    "/connect",
    "/send",
    "/start",
)
ENDPOINT_RE = re.compile(
    r"[\"'`]((?:/[A-Za-z0-9_.-]+)?/[A-Za-z][A-Za-z0-9_]*(?:/[A-Za-z][A-Za-z0-9_]*)+(?:\?[^\"'`<>\s)]*)?)[\"'`]"
)
LINK_RE = re.compile(r"(?:href|src)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)


SOURCE_CONFIG = {
    "gmao": {
        "base_url": GMAO_BASE_URL,
        "connector": GmaoConnector,
        "module_endpoints": GMAO_MODULE_ENDPOINTS,
        "dashboard_paths": ["/", "/Home/Index", "/Dashboard", "/Dashboard/Index"],
    },
    "qalitas": {
        "base_url": QALITAS_BASE_URL,
        "connector": QalitasConnector,
        "module_endpoints": QALITAS_MODULE_ENDPOINTS,
        "dashboard_paths": ["/", "/Home/Index", "/Dashboard", "/Dashboard/Index"],
    },
}


def _base_path(base_url: str) -> str:
    return urlparse(base_url).path.rstrip("/")


def _strip_base_path(path: str, base_url: str) -> str:
    app_path = _base_path(base_url)
    if app_path and path.lower().startswith((app_path + "/").lower()):
        return path[len(app_path):] or "/"
    return path


def normalize_path(raw: str, base_url: str) -> str | None:
    if not raw:
        return None
    raw = raw.strip().strip("\"'")
    if raw.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    parsed = urlparse(urljoin(base_url.rstrip("/") + "/", raw))
    base = urlparse(base_url)
    if parsed.netloc and parsed.netloc.lower() != base.netloc.lower():
        return None
    path = _strip_base_path(parsed.path or "/", base_url)
    if not path.startswith("/"):
        path = "/" + path
    if any(part in path.lower() for part in BLOCKED_PATH_PARTS):
        return None
    if any(path.lower().startswith(prefix) for prefix in STATIC_PREFIXES):
        return None
    if path.lower().endswith(STATIC_EXTENSIONS):
        return None
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{path}{query}"


def normalize_resource_url(raw: str, base_url: str) -> str | None:
    if not raw or raw.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    parsed = urlparse(urljoin(base_url.rstrip("/") + "/", raw.strip()))
    base = urlparse(base_url)
    if parsed.netloc and parsed.netloc.lower() != base.netloc.lower():
        return None
    path = _strip_base_path(parsed.path or "/", base_url)
    if any(part in path.lower() for part in BLOCKED_PATH_PARTS):
        return None
    return parsed.geturl()


def is_probable_api(path: str) -> bool:
    lower = path.lower()
    if any(lower.startswith(prefix) for prefix in STATIC_PREFIXES):
        return False
    if lower.endswith(STATIC_EXTENSIONS):
        return False
    parts = urlparse(path).path.strip("/").split("/")
    if len(parts) < 2:
        return False
    action = parts[-1]
    return bool(re.search(r"^(get|load|list|search|read|find|data|fetch|filter|all)", action, re.IGNORECASE))


def extract_candidates(text: str, base_url: str) -> set[str]:
    found = set()
    for match in ENDPOINT_RE.finditer(text or ""):
        normalized = normalize_path(match.group(1), base_url)
        if normalized and is_probable_api(normalized):
            found.add(normalized)
    return found


def extract_links(text: str, base_url: str) -> set[str]:
    links = set()
    for match in LINK_RE.finditer(text or ""):
        url = normalize_resource_url(match.group(1), base_url)
        if url:
            links.add(url)
    return links


def seed_known_endpoints(module_endpoints: dict[str, list[str]]) -> set[str]:
    return {endpoint for endpoints in module_endpoints.values() for endpoint in endpoints}


def default_params_for_endpoint(connector, endpoint: str, module_endpoints: dict[str, list[str]]) -> dict | None:
    for module, endpoints in module_endpoints.items():
        if endpoint in endpoints and hasattr(connector, "_default_module_params"):
            return connector._default_module_params(module) or None
    return None


def generate_common_candidates(candidates: set[str], limit: int) -> set[str]:
    controllers = set()
    for candidate in candidates:
        parts = urlparse(candidate).path.strip("/").split("/")
        if parts:
            controllers.add(parts[0])
    actions = [
        "GetAll",
        "GetList",
        "GetData",
        "GetForJs",
        "GetSettingsAppForJs",
        "Load",
        "List",
        "Search",
    ]
    generated = set()
    for controller in sorted(controllers):
        plural = controller if controller.endswith("s") else controller + "s"
        for action in actions:
            generated.add(f"/{controller}/{action}")
        generated.add(f"/{controller}/Get{plural}")
        generated.add(f"/{controller}/GetAll{plural}")
        generated.add(f"/{controller}/GetEnabled{plural}")
        if len(generated) >= limit:
            break
    return generated


def fetch_text(session: requests.Session, url: str, timeout: int) -> tuple[str, str, int]:
    response = session.get(url, timeout=timeout, verify=False)
    content_type = response.headers.get("Content-Type", "")
    return response.text or "", content_type, response.status_code


def crawl_resources(connector, base_url: str, dashboard_paths: list[str], max_pages: int, timeout: int) -> dict:
    queue = [urljoin(base_url.rstrip("/") + "/", path.lstrip("/")) for path in dashboard_paths]
    seen = set()
    candidates = set()
    resources = []

    while queue and len(seen) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            text, content_type, status = fetch_text(connector.session, url, timeout)
        except Exception as exc:
            resources.append({"url": url, "error": str(exc)[:250]})
            continue

        resources.append({"url": url, "status": status, "content_type": content_type, "bytes": len(text)})
        if status >= 400:
            continue
        candidates.update(extract_candidates(text, base_url))
        for link in sorted(extract_links(text, base_url)):
            if link in seen or link in queue:
                continue
            lower = urlparse(link).path.lower()
            if lower.endswith(".js") or not lower.endswith(STATIC_EXTENSIONS):
                queue.append(link)

    return {"resources": resources, "candidates": sorted(candidates)}


def parse_json_response(response: requests.Response):
    text = response.text or ""
    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type.lower() and not text.lstrip().startswith(("{", "[")):
        return None
    try:
        return response.json()
    except ValueError:
        return json.loads(text)


def probe_endpoint(connector, base_url: str, endpoint: str, params: dict | None, timeout: int) -> dict:
    parsed = urlparse(endpoint)
    request_params = dict(params or {})
    if parsed.query:
        request_params.update({key: values[-1] for key, values in parse_qs(parsed.query).items()})
    path = parsed.path
    started = time.perf_counter()
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    response = connector.session.get(url, params=request_params or None, timeout=timeout, verify=False)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    result = {
        "endpoint": endpoint,
        "status": response.status_code,
        "content_type": response.headers.get("Content-Type", ""),
        "elapsed_ms": elapsed_ms,
    }
    try:
        payload = parse_json_response(response)
    except Exception as exc:
        result["json_error"] = str(exc)[:250]
        return result
    if payload is None:
        preview = (response.text or "")[:120].replace("\n", " ")
        result["preview"] = preview
        return result
    records = extract_records_from_payload(payload)
    result["json"] = True
    result["records_count"] = len(records)
    if records and isinstance(records[0], dict):
        result["sample_keys"] = list(records[0].keys())[:20]
    elif isinstance(payload, dict):
        result["sample_keys"] = list(payload.keys())[:20]
    return result


def discover_source(
    source: str,
    max_pages: int,
    max_probes: int,
    generated_limit: int,
    timeout: int,
    include_generated: bool,
) -> dict:
    config = SOURCE_CONFIG[source]
    connector = config["connector"]()
    connector.login(force=True)
    try:
        crawled = crawl_resources(connector, config["base_url"], config["dashboard_paths"], max_pages, timeout)
        known = seed_known_endpoints(config["module_endpoints"])
        candidates = set(crawled["candidates"]) | known
        generated = generate_common_candidates(candidates, generated_limit) if include_generated else set()
        all_candidates = sorted(candidates | generated)

        probed = []
        working_json = []
        for endpoint in all_candidates[:max_probes]:
            params = default_params_for_endpoint(connector, endpoint, config["module_endpoints"])
            try:
                result = probe_endpoint(connector, config["base_url"], endpoint, params, timeout)
            except Exception as exc:
                result = {"endpoint": endpoint, "error": str(exc)[:250]}
            probed.append(result)
            if result.get("json") and result.get("status") == 200:
                working_json.append(result)

        return {
            "source": source,
            "base_url": config["base_url"],
            "resources_crawled": crawled["resources"],
            "known_config_endpoints": sorted(known),
            "extracted_candidates_count": len(crawled["candidates"]),
            "generated_candidates_count": len(generated),
            "probed_count": len(probed),
            "working_json_count": len(working_json),
            "working_json_endpoints": working_json,
            "all_candidates": all_candidates,
            "probed": probed,
        }
    finally:
        connector.logout()


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover QALITAS/GMAO JSON endpoints from authenticated HTML/JS.")
    parser.add_argument("--source", choices=["gmao", "qalitas", "both"], default="both")
    parser.add_argument("--max-pages", type=int, default=80)
    parser.add_argument("--max-probes", type=int, default=300)
    parser.add_argument("--generated-limit", type=int, default=160)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--no-generated", action="store_true", help="Only probe extracted + already configured endpoints.")
    parser.add_argument("--output-dir", default=str(ROOT / "data" / "discovery"))
    args = parser.parse_args()

    sources = ["gmao", "qalitas"] if args.source == "both" else [args.source]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for source in sources:
        print(f"[DISCOVERY] Starting {source}...")
        report = discover_source(
            source=source,
            max_pages=args.max_pages,
            max_probes=args.max_probes,
            generated_limit=args.generated_limit,
            timeout=args.timeout,
            include_generated=not args.no_generated,
        )
        output_path = output_dir / f"{source}_endpoints.json"
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        display_path = output_path.relative_to(ROOT).as_posix() if output_path.is_relative_to(ROOT) else output_path.name
        print(
            f"[DISCOVERY] {source}: {report['working_json_count']} working JSON endpoints "
            f"from {report['probed_count']} probes -> {display_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
