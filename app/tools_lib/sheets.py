import logging
import os
import sys
import time

import google.auth
import requests
from google.auth.transport.requests import Request

logger = logging.getLogger("omniretail.tools.sheets")

_SHEET_TITLE_CACHE = {}
_SHARED_CREDENTIALS = {}  # maps scope_key -> credentials
_ROWS_CACHE = {}  # maps (spreadsheet_id, target_gid) -> {"data": rows, "timestamp": timestamp}
CACHE_TTL_SECONDS = 60.0


def execute_with_retry(
    method, url, headers, params=None, json_data=None, max_attempts=3
) -> requests.Response:
    """Executes HTTP request with exponential backoff for transient errors (502, 503, 429)."""
    backoff = 1.0
    for attempt in range(1, max_attempts + 1):
        try:
            if method.upper() == "POST":
                resp = requests.post(
                    url, headers=headers, params=params, json=json_data, timeout=10
                )
            elif method.upper() == "PUT":
                resp = requests.put(
                    url, headers=headers, params=params, json=json_data, timeout=10
                )
            else:
                resp = requests.get(url, headers=headers, params=params, timeout=10)

            # If transient error, trigger retry block
            if resp.status_code in [429, 502, 503] and attempt < max_attempts:
                logger.warning(
                    f"Transient HTTP {resp.status_code} on attempt {attempt}. Retrying in {backoff}s..."
                )
                time.sleep(backoff)
                backoff *= 2
                continue

            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            if attempt == max_attempts:
                logger.error(f"HTTP request failed after {max_attempts} attempts: {e}")
                raise e
            logger.warning(
                f"Request exception on attempt {attempt}: {e}. Retrying in {backoff}s..."
            )
            time.sleep(backoff)
            backoff *= 2


class SheetsClient:
    def __init__(self, spreadsheet_id=None, readonly=False):
        self.spreadsheet_id = (
            spreadsheet_id
            or os.getenv("SPREADSHEET_ID")
            or os.getenv("WISMO_SPREADSHEET_ID")
        )
        self.readonly = readonly

    def get_auth_token(self):
        """Obtains Google Access Token using Application Default Credentials.
        Uses a module-level cache per scope so token refresh only happens on expiry
        (~once per hour) rather than on every tool invocation.
        """
        global _SHARED_CREDENTIALS
        scope_key = "readonly" if self.readonly else "readwrite"
        scopes = (
            ["https://www.googleapis.com/auth/spreadsheets.readonly"]
            if self.readonly
            else [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.file",
            ]
        )
        if scope_key not in _SHARED_CREDENTIALS:
            creds, _ = google.auth.default(scopes=scopes)
            _SHARED_CREDENTIALS[scope_key] = creds

        # Refresh token if expired
        creds = _SHARED_CREDENTIALS[scope_key]
        if not creds.valid:
            creds.refresh(Request())

        return creds.token

    def append_log(self, data, range_name="Sheet1!A1"):
        """Appends a new row to Google Sheets."""
        try:
            token = self.get_auth_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            # Row mapping exactly like the old sheets_client.js
            row_values = [
                data.get("name", ""),
                data.get("phone_number", ""),
                data.get("email", ""),
                data.get("purchase_order", ""),
                data.get("intent", "General Inquiry"),
                data.get("summary", ""),
                data.get("urgency", "low"),
                data.get("timestamp", ""),
                data.get("call_duration_seconds", ""),
                data.get("ticket_id", ""),
                data.get("session_id", "N/A"),
                data.get("crm_sync_status", "processing"),
                data.get("error_message", ""),
                data.get("sentiment", "neutral"),
                data.get("product_type", ""),
            ]

            url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/{range_name}:append"
            params = {
                "valueInputOption": "RAW",
                "insertDataOption": "INSERT_ROWS",
            }
            payload = {"values": [row_values]}

            execute_with_retry("POST", url, headers, params, payload)
            logger.info("Successfully logged lead to Google Sheets")
            return True
        except Exception as e:
            logger.error(f"Failed to write to Google Sheets: {e}")
            return False

    def upsert_log(self, data):
        """Checks for existing session_id in Column K and updates or appends."""
        session_id = data.get("session_id")
        if not session_id:
            return self.append_log(data)

        try:
            token = self.get_auth_token()
            headers = {"Authorization": f"Bearer {token}"}

            url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/Sheet1!K:K"
            response = execute_with_retry("GET", url, headers)

            rows = response.json().get("values", [])
            row_index = -1
            for idx, row in enumerate(rows):
                if row and row[0] == session_id:
                    row_index = idx
                    break

            if row_index != -1:
                sheet_row = row_index + 1
                logger.info(f"Existing session found at row {sheet_row}, updating...")

                # Perform update on specific row
                row_values = [
                    data.get("name", ""),
                    data.get("phone_number", ""),
                    data.get("email", ""),
                    data.get("purchase_order", ""),
                    data.get("intent", "General Inquiry"),
                    data.get("summary", ""),
                    data.get("urgency", "low"),
                    data.get("timestamp", ""),
                    data.get("call_duration_seconds", ""),
                    data.get("ticket_id", ""),
                    data.get("session_id", "N/A"),
                    data.get("crm_sync_status", "updated"),
                    data.get("error_message", ""),
                    data.get("sentiment", "neutral"),
                    data.get("product_type", ""),
                ]

                update_url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/Sheet1!A{sheet_row}"
                update_params = {"valueInputOption": "RAW"}
                update_payload = {"values": [row_values]}

                execute_with_retry(
                    "PUT",
                    update_url,
                    headers={**headers, "Content-Type": "application/json"},
                    params=update_params,
                    json_data=update_payload,
                )
                return True

            return self.append_log(data)
        except Exception as e:
            logger.error(f"Upsert failed, falling back to append: {e}")
            return self.append_log(data)

    def log_failure(self, data, error_message):
        """Logs failures to the DLQ (FAILURES tab)."""
        failure_data = {
            **data,
            "crm_sync_status": "failed",
            "error_message": error_message,
        }
        return self.append_log(failure_data, range_name="FAILURES!A1")

    def lookup_wismo_mock(
        self,
        purchase_order: str | None = None,
        zip_code: str | None = None,
        phone_number: str | None = None,
        last_name: str | None = None,
        target_gid: int | None = None,
    ) -> dict | None:
        """Looks up and verifies order information in a mock Google Sheet by purchase order, phone, zip, or name."""
        try:
            if target_gid is None:
                target_gid = int(os.environ.get("WISMO_SHEET_GID", "1506736168"))

            token = self.get_auth_token()
            headers = {"Authorization": f"Bearer {token}"}

            # Step 1: Resolve sheet title by GID (using in-memory cache)
            cache_key = (self.spreadsheet_id, target_gid)
            sheet_title = _SHEET_TITLE_CACHE.get(cache_key)

            if not sheet_title:
                logger.info(
                    f"Cache miss: Resolving sheet title for GID {target_gid}..."
                )
                meta_url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}?fields=sheets.properties"
                meta_res = execute_with_retry("GET", meta_url, headers)

                sheets = meta_res.json().get("sheets", [])
                for s in sheets:
                    prop = s.get("properties", {})
                    if prop.get("sheetId") == target_gid:
                        sheet_title = prop.get("title")
                        _SHEET_TITLE_CACHE[cache_key] = sheet_title
                        break

                # Fallback handling
                if not sheet_title and sheets:
                    sheet_title = sheets[0].get("properties", {}).get("title", "Sheet1")
                elif not sheet_title:
                    sheet_title = "Sheet1"

            # Step 2: Fetch spreadsheet rows (using key-scoped 60s Row Cache)
            now = time.time()
            cached_entry = _ROWS_CACHE.get(cache_key)

            # Bypass cache in tests to avoid mock data contamination
            bypass_cache = "pytest" in sys.modules

            if (
                not bypass_cache
                and cached_entry
                and (now - cached_entry["timestamp"] < CACHE_TTL_SECONDS)
            ):
                rows = cached_entry["data"]
            else:
                data_url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/{sheet_title}!A:Z"
                data_res = execute_with_retry("GET", data_url, headers)
                rows = data_res.json().get("values", [])
                _ROWS_CACHE[cache_key] = {"data": rows, "timestamp": now}
            if not rows or len(rows) < 2:
                return None

            # Step 3: Map headers dynamically
            headers_row = [str(cell).strip().lower() for cell in rows[0]]
            (
                po_idx,
                status_idx,
                carrier_idx,
                tracking_idx,
                details_idx,
                shipped_date_idx,
            ) = -1, -1, -1, -1, -1, -1
            ref_idx, zip_idx, phone_idx, name_idx, city_idx = -1, -1, -1, -1, -1

            for idx, cell in enumerate(headers_row):
                if "carrier" in cell:
                    carrier_idx = idx
                elif "tracking" in cell:
                    tracking_idx = idx
                elif "status" in cell:
                    if status_idx == -1 or "order" in cell or cell == "status":
                        status_idx = idx
                elif any(x in cell for x in ["detail", "info", "comment", "note"]):
                    details_idx = idx
                elif any(
                    x in cell for x in ["po/reference", "po number", "purchase order"]
                ):
                    ref_idx = idx
                elif (
                    any(
                        x in cell
                        for x in ["customer order number", "customer_order_number"]
                    )
                    and ref_idx == -1
                ):
                    ref_idx = idx
                elif "shipped date" in cell or "shipped_date" in cell:
                    shipped_date_idx = idx
                elif "postal code" in cell or "zip" in cell:
                    zip_idx = idx
                elif "phone" in cell:
                    phone_idx = idx
                elif "city" in cell:
                    city_idx = idx
                elif (
                    "contact person" in cell
                    or "contact_person" in cell
                    or "customer name" in cell
                ):
                    name_idx = idx

            # Locate Primary Order Number column (Internal SO-XXXX)
            for idx, cell in enumerate(headers_row):
                if cell in ["order number", "order_number", "order id", "order_id"]:
                    po_idx = idx
                    break

            def normalize(val: str) -> str:
                return "".join(c for c in str(val).lower() if c.isalnum())

            target_po = normalize(purchase_order) if purchase_order else ""
            target_zip = normalize(zip_code)[:5] if zip_code else ""
            target_phone = normalize(phone_number) if phone_number else ""
            target_last_name = normalize(last_name) if last_name else ""

            # Phase 1: Collect matching rows
            matched_rows = []
            for row in rows[1:]:
                match_found = False

                # Case A: Check Customer PO/Reference column and Order Number column if PO is given
                if target_po:
                    if ref_idx != -1 and len(row) > ref_idx:
                        val_norm = normalize(row[ref_idx])
                        if val_norm == target_po or (
                            len(target_po) >= 5 and val_norm.endswith(target_po)
                        ):
                            match_found = True
                    if not match_found and po_idx != -1 and len(row) > po_idx:
                        val_norm = normalize(row[po_idx])
                        if val_norm == target_po or (
                            len(target_po) >= 5 and val_norm.endswith(target_po)
                        ):
                            match_found = True

                # Case B: Check Phone column if phone number is given
                elif target_phone and phone_idx != -1 and len(row) > phone_idx:
                    val_norm = normalize(row[phone_idx])
                    if (
                        val_norm == target_phone
                        or (len(val_norm) >= 7 and target_phone.endswith(val_norm))
                        or (len(target_phone) >= 7 and val_norm.endswith(target_phone))
                    ):
                        match_found = True

                # Case C: Check ZIP + Last Name direct query if no phone/PO is provided
                elif (
                    target_zip
                    and target_last_name
                    and not target_phone
                    and not target_po
                ):
                    record_zip = (
                        normalize(row[zip_idx])[:5]
                        if (zip_idx != -1 and len(row) > zip_idx)
                        else ""
                    )
                    record_name = (
                        str(row[name_idx]).strip().lower()
                        if (name_idx != -1 and len(row) > name_idx)
                        else ""
                    )
                    record_last_name = (
                        normalize(record_name.split()[-1]) if record_name else ""
                    )
                    if (
                        record_zip == target_zip
                        and record_last_name == target_last_name
                    ):
                        match_found = True

                if match_found:
                    matched_rows.append(row)

            # Phase 2: Resolve matched results
            if not matched_rows:
                return {
                    "found": False,
                    "verified": False,
                    "details": "No matching order found.",
                }

            # Helper to format successful verified details
            def format_verified_row(row):
                return {
                    "found": True,
                    "verified": True,
                    "status": str(row[status_idx]).strip()
                    if (status_idx != -1 and len(row) > status_idx)
                    else "Pending",
                    "carrier": str(row[carrier_idx]).strip()
                    if (carrier_idx != -1 and len(row) > carrier_idx)
                    else "FedEx",
                    "tracking_number": str(row[tracking_idx]).strip()
                    if (tracking_idx != -1 and len(row) > tracking_idx)
                    else "N/A",
                    "shipped_date": str(row[shipped_date_idx]).strip()
                    if (shipped_date_idx != -1 and len(row) > shipped_date_idx)
                    else "",
                    "details": str(row[details_idx]).strip()
                    if (details_idx != -1 and len(row) > details_idx)
                    else "Order lookup successful.",
                }

            # Helper to extract cell details safely
            def get_cell(row, idx, default="N/A"):
                return (
                    str(row[idx]).strip() if (idx != -1 and len(row) > idx) else default
                )

            # If we matched by ZIP + Last Name directly, we are automatically verified
            if target_zip and target_last_name and not target_phone and not target_po:
                return format_verified_row(matched_rows[0])

            # If multiple matching rows exist, filter or return multiple status
            if len(matched_rows) > 1:
                # If they supplied verification, try to find the one matching
                if zip_code or last_name:
                    for row in matched_rows:
                        record_zip = (
                            normalize(row[zip_idx])[:5]
                            if (zip_idx != -1 and len(row) > zip_idx)
                            else ""
                        )
                        record_name = (
                            str(row[name_idx]).strip().lower()
                            if (name_idx != -1 and len(row) > name_idx)
                            else ""
                        )
                        record_last_name = (
                            normalize(record_name.split()[-1]) if record_name else ""
                        )

                        if (zip_code and target_zip == record_zip) or (
                            last_name and target_last_name == record_last_name
                        ):
                            return format_verified_row(row)

                # If neither is provided or no match succeeded, return multiple matches status
                return {
                    "found": True,
                    "multiple": True,
                    "verified": False,
                    "details": "Multiple orders found matching phone number.",
                }

            # Single match handling
            row = matched_rows[0]
            record_zip = (
                normalize(row[zip_idx])[:5]
                if (zip_idx != -1 and len(row) > zip_idx)
                else ""
            )
            record_name = (
                str(row[name_idx]).strip().lower()
                if (name_idx != -1 and len(row) > name_idx)
                else ""
            )
            record_last_name = normalize(record_name.split()[-1]) if record_name else ""

            # Check if verification is satisfied
            verified = False
            if zip_code and target_zip == record_zip:
                verified = True
            elif last_name and target_last_name == record_last_name:
                verified = True

            # Automatic callerID lookup path (not verified yet)
            if not zip_code and not last_name:
                return {
                    "found": True,
                    "multiple": False,
                    "verified": False,
                    "contact_person": get_cell(row, name_idx, "Customer"),
                    "city": get_cell(row, city_idx, "your delivery city"),
                    "purchase_order": get_cell(row, ref_idx, "N/A"),
                    "details": "Order found matching phone number. Verification required.",
                }

            if verified:
                return format_verified_row(row)
            else:
                return {
                    "found": False,
                    "verified": False,
                    "details": "Order details mismatched or verification failed.",
                }
        except Exception as e:
            logger.error(
                f"Error looking up mock WISMO in sheet {self.spreadsheet_id}: {e}"
            )
            return None
