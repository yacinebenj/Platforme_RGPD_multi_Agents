"""
qalitas/connector.py
====================
Authenticates with QALITAS WEB and fetches data from any module.
Credentials are read from key.env and are never hardcoded.
"""

import logging
import os
import threading
import time

import requests
from dotenv import load_dotenv
from integrations.source_connector_base import (
    extract_csrf_token,
    fetch_records_from_endpoints,
    get_id_from_list,
)

load_dotenv("key.env")

QALITAS_BASE_URL = os.getenv("QALITAS_URL", "https://timserver.northeurope.cloudapp.azure.com/QalitasTest")
QALITAS_USER = os.getenv("QALITAS_USER", "")
QALITAS_PASSWORD = os.getenv("QALITAS_PASSWORD", "")
QALITAS_COMPANY = os.getenv("QALITAS_COMPANY", "TIM")
QALITAS_GROUP = os.getenv("QALITAS_GROUP", "Industries")
QALITAS_SITE = os.getenv("QALITAS_SITE", "Tim")
QALITAS_LOGIN_TIMEOUT = int(os.getenv("QALITAS_LOGIN_TIMEOUT", "12"))
QALITAS_FETCH_TIMEOUT = int(os.getenv("QALITAS_FETCH_TIMEOUT", "20"))
QALITAS_RETRY_TIMEOUT = int(os.getenv("QALITAS_RETRY_TIMEOUT", "25"))

MODULE_ENDPOINTS = {
    "customers": ["/Customer/GetEnabledCustomers", "/Customer/GetCustomers"],
    "suppliers": ["/Supplier/GetEnabledSuppliers", "/Supplier/GetSuppliers"],
    "employees": ["/Employee/GetEnabledEmployees", "/Employee/GetEmployees", "/Employee/GetEmployeesForFilter", "/HumanResources/GetEmployees", "/HumanResources/GetEnabledEmployees"],
    "audits": ["/Audit/GetAudits", "/Audit/GetEnabledAudits"],
    "actions": ["/Actions/GetAllActions", "/Action/GetActions", "/Action/GetEnabledActions"],
    "nonconf": ["/NonConformity/GetNonConformities", "/NonConformity/GetEnabledNonConformities"],
    "equipments": ["/Equipment/GetEnabledEquipments", "/Equipment/GetAllEquipments"],
    "processes": ["/Process/GetEnabledProcess", "/Process/GetProcess", "/Process/GetProcessesForFilter"],
    "projects": ["/Project/GetProjects"],
    "companies": ["/Account/GetCompanies", "/Customer/GetCompanies"],
    "sites": ["/Account/GetSites", "/Customer/GetSites"],
}

MODULE_LABELS = {
    "customers": "Clients",
    "suppliers": "Fournisseurs",
    "employees": "Ressources Humaines",
    "audits": "Audits",
    "actions": "Actions Correctives",
    "nonconf": "Non-Conformites",
    "equipments": "Equipements",
    "processes": "Processus",
    "projects": "Projets",
    "companies": "Societes",
    "sites": "Sites",
}


class QalitasConnector:
    _shared_lock = threading.Lock()
    _shared_connector = None

    def __init__(self):
        self.base_url = QALITAS_BASE_URL
        self.logged_in = False
        self._login_ids_cache: tuple[str, str, str] | None = None
        self._last_login_at = 0.0
        self.session = self._new_session()

    @staticmethod
    def _new_session() -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        return session

    def _reset_session(self):
        try:
            self.session.close()
        except Exception:
            pass
        self.session = self._new_session()
        self.logged_in = False

    @classmethod
    def shared(cls) -> "QalitasConnector":
        with cls._shared_lock:
            connector = cls._shared_connector
            if connector is None:
                connector = cls()
                cls._shared_connector = connector
            if not connector.logged_in:
                connector.login(force=True)
            return connector

    @classmethod
    def invalidate_shared(cls):
        with cls._shared_lock:
            connector = cls._shared_connector
            if connector is not None:
                try:
                    connector.logout()
                except Exception:
                    pass
            cls._shared_connector = None

    def _get_id_from_list(self, items: list, name_key: str, target_name: str) -> str:
        """Find ID in a list of dicts by matching target_name against common name keys."""
        return get_id_from_list(items, name_key, target_name)

    def _fetch_login_ids(self) -> tuple:
        """Fetch Company, Group, Site IDs needed for QALITAS login form."""
        company_id = group_id = site_id = ""
        try:
            r = self.session.get(f"{self.base_url}/Account/GetCompanies", timeout=QALITAS_LOGIN_TIMEOUT, verify=False)
            companies = r.json() if r.status_code == 200 else []
            print(f"[QALITAS] Companies available: {[c.get('Name', c.get('label', '?')) for c in companies[:5]]}")
            company_id = self._get_id_from_list(companies, "Name", QALITAS_COMPANY)
            print(f"[QALITAS] Company ID: {company_id}")
        except Exception as e:
            print(f"[QALITAS] Companies fetch error: {e}")

        try:
            r = self.session.get(
                f"{self.base_url}/Account/GetGroups",
                params={"companyId": company_id},
                timeout=QALITAS_LOGIN_TIMEOUT,
                verify=False,
            )
            groups = r.json() if r.status_code == 200 else []
            print(f"[QALITAS] Groups available: {[g.get('Name', g.get('label', '?')) for g in groups[:5]]}")
            group_id = self._get_id_from_list(groups, "Name", QALITAS_GROUP)
            print(f"[QALITAS] Group ID: {group_id}")
        except Exception as e:
            print(f"[QALITAS] Groups fetch error: {e}")

        try:
            r = self.session.get(
                f"{self.base_url}/Account/GetSites",
                params={"groupId": group_id},
                timeout=QALITAS_LOGIN_TIMEOUT,
                verify=False,
            )
            sites = r.json() if r.status_code == 200 else []
            print(f"[QALITAS] Sites available: {[s.get('Name', s.get('label', '?')) for s in sites[:5]]}")
            site_id = self._get_id_from_list(sites, "Name", QALITAS_SITE)
            print(f"[QALITAS] Site ID: {site_id}")
        except Exception as e:
            print(f"[QALITAS] Sites fetch error: {e}")

        return company_id, group_id, site_id

    def login(self, force: bool = False) -> bool:
        """Full QALITAS login: GET page -> fetch IDs -> POST full form -> follow redirect."""
        if not QALITAS_USER or not QALITAS_PASSWORD:
            raise ValueError("QALITAS credentials missing in key.env.")
        if self.logged_in and not force:
            return True
        try:
            if force:
                self._reset_session()
            login_page_url = f"{self.base_url}/Account/Login"

            resp = self.session.get(login_page_url, timeout=QALITAS_LOGIN_TIMEOUT, verify=False)
            resp.raise_for_status()
            token = self._extract_csrf_token(resp.text)
            print(f"[QALITAS] CSRF token found: {bool(token)}")

            company_id, group_id, site_id = self._login_ids_cache or ("", "", "")
            if not any([company_id, group_id, site_id]):
                company_id, group_id, site_id = self._fetch_login_ids()
                self._login_ids_cache = (company_id, group_id, site_id)

            login_data = {
                "__RequestVerificationToken": token,
                "CompanyId": company_id,
                "GroupId": group_id,
                "SiteId": site_id,
                "UserName": QALITAS_USER,
                "Password": QALITAS_PASSWORD,
                "RememberMe": "false",
            }
            print(
                f"[QALITAS] Posting login form: Company={company_id} Group={group_id} "
                f"Site={site_id} User={QALITAS_USER}"
            )
            login_resp = self.session.post(
                login_page_url,
                data=login_data,
                timeout=QALITAS_LOGIN_TIMEOUT,
                verify=False,
                allow_redirects=False,
            )
            print(f"[QALITAS] POST status: {login_resp.status_code}")
            print(f"[QALITAS] Location: {login_resp.headers.get('Location', 'none')}")
            print(f"[QALITAS] Cookies after login: {list(self.session.cookies.keys())}")

            if login_resp.status_code in [301, 302, 303]:
                location = login_resp.headers.get("Location", "")
                if not location.startswith("http"):
                    location = self.base_url + location
                final_resp = self.session.get(location, timeout=QALITAS_LOGIN_TIMEOUT, verify=False, allow_redirects=True)
                print(f"[QALITAS] Final URL: {final_resp.url}")
                if "Account/Login" not in final_resp.url:
                    self.logged_in = True
                    self._last_login_at = time.monotonic()
                    print(f"[QALITAS] Logged in successfully as {QALITAS_USER}")
                    return True
                print("[QALITAS] Login FAILED - still on login page")
                print(f"[QALITAS] Page preview: {final_resp.text[200:600]}")
                return False

            if login_resp.status_code == 200:
                body = login_resp.text
                if "Dashboard" in body or "Tableau" in body:
                    self.logged_in = True
                    self._last_login_at = time.monotonic()
                    print(f"[QALITAS] Logged in (200 with dashboard) as {QALITAS_USER}")
                    return True
                print("[QALITAS] Login FAILED (200 but no dashboard)")
                print(f"[QALITAS] Page preview: {body[200:600]}")
                return False

            print(f"[QALITAS] Unexpected status: {login_resp.status_code}")
            return False
        except Exception as e:
            logging.error(f"[QALITAS] Login error: {e}")
            raise

    def _extract_csrf_token(self, html: str) -> str:
        return extract_csrf_token(html)

    def fetch(
        self,
        module: str,
        params: dict = None,
        timeout: int | None = None,
        retry_timeout: int | None = None,
    ) -> list:
        if not self.logged_in:
            self.login()
        endpoints = MODULE_ENDPOINTS.get(module)
        if not endpoints:
            raise ValueError(f"Unknown module: {module}")
        timeout = QALITAS_FETCH_TIMEOUT if timeout is None else timeout
        retry_timeout = QALITAS_RETRY_TIMEOUT if retry_timeout is None else retry_timeout
        return fetch_records_from_endpoints(
            session=self.session,
            base_url=self.base_url,
            module=module,
            endpoints=endpoints,
            request_params=params,
            timeout=timeout,
            retry_timeout=retry_timeout,
            source_name="QALITAS",
            relogin=lambda: self.login(force=True),
        )

    def logout(self):
        try:
            self.session.get(f"{self.base_url}/Account/LogOff", timeout=QALITAS_LOGIN_TIMEOUT, verify=False)
        except Exception:
            pass
        self.session.close()
        self.logged_in = False

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *args):
        self.logout()


def get_connector() -> QalitasConnector:
    return QalitasConnector.shared()
