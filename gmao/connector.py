"""
gmao/connector.py
================
Authenticates with GMAO PRO WEB and fetches data from supported modules.
Credentials are read from key.env and are never hardcoded.
"""

import logging
import os
import threading
import time
from datetime import datetime

import requests
from dotenv import load_dotenv
from integrations.source_connector_base import (
    extract_csrf_token,
    fetch_records_from_endpoints,
    get_id_from_list,
)

load_dotenv("key.env")

GMAO_BASE_URL = os.getenv("GMAO_URL", "https://timserver.northeurope.cloudapp.azure.com/GmaoPro")
GMAO_USER = os.getenv("GMAO_USER", "")
GMAO_PASSWORD = os.getenv("GMAO_PASSWORD", "")
GMAO_COMPANY = os.getenv("GMAO_COMPANY", "TIM")
GMAO_SITE = os.getenv("GMAO_SITE", "CASA")
GMAO_LOGIN_TIMEOUT = int(os.getenv("GMAO_LOGIN_TIMEOUT", "12"))
GMAO_FETCH_TIMEOUT = int(os.getenv("GMAO_FETCH_TIMEOUT", "25"))
GMAO_RETRY_TIMEOUT = int(os.getenv("GMAO_RETRY_TIMEOUT", "35"))

MODULE_ENDPOINTS = {
    "customers": ["/Customer/GetEnabledCustomers", "/Customer/GetCustomers"],
    "suppliers": ["/Supplier/GetEnabledSuppliers"],
    "resource_needs": ["/ResourceNeeds/GetResourceNeeds"],
    "organization_chart": ["/Actions/GetOrganizationChart"],
    "meeting_actions": ["/Actions/GetAllActionBySource", "/Actions/GetAllActions"],
    "meetings": ["/Meeting/GetMeetings", "/Meetings/GetMeetings"],
    "maintenance_teams": ["/Maintenance/GetMaintenanceTeams", "/MaintenanceTeams/GetMaintenanceTeams"],
    "qualifications": ["/Qualification/GetQualifications", "/Qualifications/GetQualifications"],
    "equipments": ["/Equipment/GetEnabledEquipments", "/Equipments/GetEnabledEquipments", "/Equipment/GetAllEquipments"],
    "toolings": ["/Tooling/GetEnabledToolings", "/Toolings/GetEnabledToolings"],
    "maintenance_operations": ["/Maintenance/GetEnabledMaintenanceOperations", "/MaintenanceOperation/GetEnabledMaintenanceOperations", "/MaintenanceOperation/GetAllMaintenanceOperations"],
    "maintenance_ranges": ["/Maintenance/GetEnabledMaintenanceRanges", "/MaintenanceRange/GetEnabledMaintenanceRanges", "/MaintenanceRange/GetAllMaintenanceRanges"],
    "articles": ["/Article/GetEnabledArticles", "/Articles/GetEnabledArticles"],
    "purchase_requests": ["/PurchaseRequest/GetPurchaseRequest", "/PurchaseRequests/GetPurchaseRequest"],
    "purchase_orders": ["/PurchaseOrder/GetAllPurchaseOrders", "/PurchaseOrders/GetAllPurchaseOrders"],
    "supplier_contracts": ["/Contract/GetAllContracts", "/Contracts/GetAllContracts"],
    "purchase_invoices": ["/PurchaseInvoice/GetAllPurchaseInvoices", "/PurchaseInvoices/GetAllPurchaseInvoices"],
    "calculation_needs": ["/Calculation/GetCalculationNeeds", "/Calculations/GetCalculationNeeds"],
}

MODULE_LABELS = {
    "customers": "Clients",
    "suppliers": "Fournisseurs",
    "resource_needs": "Besoins en ressources",
    "organization_chart": "Organigramme",
    "meeting_actions": "Actions des réunions",
    "meetings": "Réunions",
    "maintenance_teams": "Équipes de maintenance",
    "qualifications": "Qualifications",
    "equipments": "Équipements actifs",
    "toolings": "Outillages actifs",
    "maintenance_operations": "Opérations de maintenance",
    "maintenance_ranges": "Gammes de maintenance",
    "articles": "Articles achetés",
    "purchase_requests": "Demandes d'achat",
    "purchase_orders": "Commandes d'achat",
    "supplier_contracts": "Contrats fournisseurs",
    "purchase_invoices": "Factures d'achat",
    "calculation_needs": "Calcul des besoins",
}


class GmaoConnector:
    _shared_lock = threading.Lock()
    _shared_connector = None

    def __init__(self):
        self.base_url = GMAO_BASE_URL
        self.logged_in = False
        self._login_ids_cache: tuple[str, str] | None = None
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
    def shared(cls) -> "GmaoConnector":
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
        return get_id_from_list(items, name_key, target_name)

    def _fetch_login_ids(self) -> tuple:
        company_id = site_id = ""
        company_endpoints = ["/GetCompanies", "/Account/GetCompanies"]
        site_endpoints = ["/GetSiteByCompany", "/Account/GetSites"]

        def _try_list(endpoints, params=None):
            last_exc = None
            for ep in endpoints:
                try:
                    r = self.session.get(f"{self.base_url}{ep}", params=params, timeout=GMAO_LOGIN_TIMEOUT, verify=False)
                    if r.status_code == 404:
                        continue
                    if r.status_code != 200:
                        continue
                    return r.json()
                except Exception as exc:
                    last_exc = exc
            if last_exc:
                raise last_exc
            return []

        try:
            companies = _try_list(company_endpoints)
            print(f"[GMAO] Companies available: {[c.get('Name', c.get('label', '?')) for c in companies[:5]]}")
            company_id = self._get_id_from_list(companies, "Name", GMAO_COMPANY)
            print(f"[GMAO] Company ID: {company_id}")
        except Exception as e:
            print(f"[GMAO] Companies fetch error: {e}")

        try:
            params = {"companyId": company_id} if company_id else None
            sites = _try_list(site_endpoints, params=params)
            print(f"[GMAO] Sites available: {[s.get('Name', s.get('label', '?')) for s in sites[:5]]}")
            site_id = self._get_id_from_list(sites, "Name", GMAO_SITE)
            print(f"[GMAO] Site ID: {site_id}")
        except Exception as e:
            print(f"[GMAO] Sites fetch error: {e}")

        return company_id, site_id

    def login(self, force: bool = False) -> bool:
        if not GMAO_USER or not GMAO_PASSWORD:
            raise ValueError("GMAO credentials missing in key.env.")
        if self.logged_in and not force:
            return True
        try:
            if force:
                self._reset_session()
            login_page_url = f"{self.base_url}/Account/Login"

            resp = self.session.get(login_page_url, timeout=GMAO_LOGIN_TIMEOUT, verify=False)
            resp.raise_for_status()
            token = self._extract_csrf_token(resp.text)
            print(f"[GMAO] CSRF token found: {bool(token)}")

            company_id, site_id = self._login_ids_cache or ("", "")
            if not any([company_id, site_id]):
                company_id, site_id = self._fetch_login_ids()
                self._login_ids_cache = (company_id, site_id)

            timezone_offset = int(time.timezone / 60)
            login_data = {
                "login": GMAO_USER,
                "psw": GMAO_PASSWORD,
                "companyId": company_id,
                "siteId": site_id,
                "timeZoneOffset": timezone_offset,
                "returnUrl": "",
            }
            if token:
                login_data["__RequestVerificationToken"] = token
            print(f"[GMAO] Posting login form: Company={company_id} Site={site_id} User={GMAO_USER}")
            login_resp = self.session.post(
                login_page_url,
                data=login_data,
                timeout=GMAO_LOGIN_TIMEOUT,
                verify=False,
                allow_redirects=False,
            )
            print(f"[GMAO] POST status: {login_resp.status_code}")
            print(f"[GMAO] Location: {login_resp.headers.get('Location', 'none')}")
            print(f"[GMAO] Cookies after login: {list(self.session.cookies.keys())}")

            if login_resp.status_code in [301, 302, 303]:
                location = login_resp.headers.get("Location", "")
                if not location.startswith("http"):
                    location = self.base_url + location
                final_resp = self.session.get(location, timeout=GMAO_LOGIN_TIMEOUT, verify=False, allow_redirects=True)
                print(f"[GMAO] Final URL: {final_resp.url}")
                if "Account/Login" not in final_resp.url:
                    self.logged_in = True
                    self._last_login_at = time.monotonic()
                    print(f"[GMAO] Logged in successfully as {GMAO_USER}")
                    return True
                print("[GMAO] Login FAILED - still on login page")
                print(f"[GMAO] Page preview: {final_resp.text[200:600]}")
                return False

            if login_resp.status_code == 200:
                body = login_resp.text
                if "Dashboard" in body or "Tableau" in body:
                    self.logged_in = True
                    self._last_login_at = time.monotonic()
                    print(f"[GMAO] Logged in (200 with dashboard) as {GMAO_USER}")
                    return True
                print("[GMAO] Login FAILED (200 but no dashboard)")
                print(f"[GMAO] Page preview: {body[200:600]}")
                return False

            print(f"[GMAO] Unexpected status: {login_resp.status_code}")
            return False
        except Exception as e:
            logging.error(f"[GMAO] Login error: {e}")
            raise

    def _extract_csrf_token(self, html: str) -> str:
        return extract_csrf_token(html)

    @staticmethod
    def _default_module_params(module: str) -> dict | None:
        current_year = datetime.now().year
        today = datetime.now().strftime("%d/%m/%Y")
        if module == "resource_needs":
            return {
                "employeeId": "",
                "datefirst": f"01/01/{current_year - 2}",
                "datelast": f"31/12/{current_year + 1}",
            }
        if module == "organization_chart":
            return {
                "serviceId": "",
                "departmentId": "",
                "level": "6",
            }
        if module == "meeting_actions":
            return {
                "sourceId": "8",
                "sourceNc": "",
                "employeeId": "",
                "roles": "",
                "dateFirst": f"01/01/{current_year - 2}",
                "dateLast": f"31/12/{current_year + 1}",
            }
        if module in {"meetings", "purchase_requests", "purchase_orders", "supplier_contracts", "purchase_invoices"}:
            return {"filter": ""}
        if module == "articles":
            return {
                "articleNature": "1",
                "type": "",
            }
        if module == "calculation_needs":
            return {
                "startDate": today,
            }
        return None

    def fetch(self, module: str, params: dict = None, timeout: int | None = None, retry_timeout: int | None = None) -> list:
        if not self.logged_in:
            self.login()
        endpoints = MODULE_ENDPOINTS.get(module)
        if not endpoints:
            raise ValueError(f"Unknown GMAO module: {module}")
        request_params = dict(self._default_module_params(module) or {})
        if params:
            request_params.update(params)
        effective_timeout = GMAO_FETCH_TIMEOUT if timeout is None else timeout
        effective_retry_timeout = GMAO_RETRY_TIMEOUT if retry_timeout is None else retry_timeout
        return fetch_records_from_endpoints(
            session=self.session,
            base_url=self.base_url,
            module=module,
            endpoints=endpoints,
            request_params=request_params,
            timeout=effective_timeout,
            retry_timeout=effective_retry_timeout,
            source_name="GMAO",
            relogin=lambda: self.login(force=True),
        )

    def logout(self):
        try:
            self.session.get(f"{self.base_url}/Account/LogOff", timeout=GMAO_LOGIN_TIMEOUT, verify=False)
        except Exception:
            pass
        self.session.close()
        self.logged_in = False

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *args):
        self.logout()


def get_connector() -> GmaoConnector:
    return GmaoConnector.shared()
