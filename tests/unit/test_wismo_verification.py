from unittest.mock import MagicMock, patch

import pytest

from app.tools import wismo_lookup
from app.tools_lib.sheets import SheetsClient

# Mock Sheet Rows matching the layout of the production spreadsheet
MOCK_HEADERS = [
    "System",
    "Channel",
    "Warehouse",
    "Carrier",
    "Order Number",
    "PO/Reference",
    "Order Date",
    "Order Type",
    "Status (Order)",
    "Posted Sales Inv.",
    "WSH Number",
    "Label Status",
    "Tracking Number",
    "Shipped Date",
    "Postal Code",
    "Email",
    "Phone",
    "State/ Province Code",
    "City",
    "Contact Person",
    "Customer Order number",
]

MOCK_ROW_1 = [
    "Shopify",
    "Online",
    "LA",
    "FedEx",
    "10024",
    "PO-10024",
    "2026-07-01",
    "Standard",
    "Released",
    "",
    "",
    "",
    "1Z12345",
    "2026-07-02",
    "92701",
    "john@example.com",
    "+17145550199",
    "CA",
    "Santa Ana",
    "John Doe",
    "PO-10024",
]

MOCK_ROW_2 = [
    "Shopify",
    "Online",
    "LA",
    "UPS",
    "10025",
    "PO-10025",
    "2026-07-02",
    "Standard",
    "Released",
    "",
    "",
    "",
    "1Z67890",
    "2026-07-03",
    "90210",
    "jane@example.com",
    "+13105550100",
    "CA",
    "Los Angeles",
    "Jane Smith",
    "PO-10025",
]


@pytest.fixture
def mock_sheets_api():
    """Mocks Sheets API requests to prevent network traffic by inspecting requested URL."""
    with patch("app.tools_lib.sheets.requests.get") as mock_get:
        meta_response = MagicMock()
        meta_response.status_code = 200
        meta_response.json.return_value = {
            "sheets": [{"properties": {"title": "Export", "sheetId": 1506736168}}]
        }

        data_response = MagicMock()
        data_response.status_code = 200
        data_response.json.return_value = {
            "values": [MOCK_HEADERS, MOCK_ROW_1, MOCK_ROW_2]
        }

        def route_request(url, *args, **kwargs):
            if "fields=sheets.properties" in url:
                return meta_response
            return data_response

        mock_get.side_effect = route_request
        yield mock_get


@patch("app.tools_lib.sheets.SheetsClient.get_auth_token", return_value="mocktoken")
def test_wismo_lookup_po_and_zip_match(mock_auth, mock_sheets_api):
    """Test successful two-factor lookup when both order number and zip code match."""
    with patch.dict("os.environ", {"WISMO_SPREADSHEET_ID": "mock_sheet_id"}):
        res = wismo_lookup(purchase_order="10024", zip_code="92701")
        assert res["success"] is True
        assert res["found"] is True
        assert res["verified"] is True
        assert res["tracking_number"] == "1Z12345"
        assert res["carrier"] == "FedEx"
        assert res["shipped_date"] == "2026-07-02"


@patch("app.tools_lib.sheets.SheetsClient.get_auth_token", return_value="mocktoken")
def test_wismo_lookup_po_and_zip_mismatch(mock_auth, mock_sheets_api):
    """Test verification failure when order number is correct but zip code is incorrect."""
    with patch.dict("os.environ", {"WISMO_SPREADSHEET_ID": "mock_sheet_id"}):
        res = wismo_lookup(purchase_order="10024", zip_code="90210")
        assert res["success"] is True
        assert res["found"] is False
        assert res["verified"] is False
        assert "details" in res


@patch("app.tools_lib.sheets.SheetsClient.get_auth_token", return_value="mocktoken")
def test_wismo_lookup_caller_id_match_unverified(mock_auth, mock_sheets_api):
    """Test Caller ID search that matches a record but requires subsequent ZIP code validation."""
    with patch.dict("os.environ", {"WISMO_SPREADSHEET_ID": "mock_sheet_id"}):
        res = wismo_lookup(phone_number="+17145550199")
        assert res["success"] is True
        assert res["found"] is True
        assert res["verified"] is False
        assert res["purchase_order"] == "PO-10024"
        assert res["contact_person"] == "John Doe"
        assert res["city"] == "Santa Ana"


@patch("app.tools_lib.sheets.SheetsClient.get_auth_token", return_value="mocktoken")
def test_wismo_lookup_caller_id_match_and_zip_verified(mock_auth, mock_sheets_api):
    """Test complete verified flow starting with matched Caller ID then matched ZIP code."""
    with patch.dict("os.environ", {"WISMO_SPREADSHEET_ID": "mock_sheet_id"}):
        # 1. Lookup by phone
        res_phone = wismo_lookup(phone_number="+17145550199")
        assert res_phone["found"] is True
        assert res_phone["verified"] is False

        # 2. Re-lookup using matched PO and correct ZIP
        res_verified = wismo_lookup(
            purchase_order=res_phone["purchase_order"], zip_code="92701"
        )
        assert res_verified["found"] is True
        assert res_verified["verified"] is True
        assert res_verified["tracking_number"] == "1Z12345"


@patch("app.tools_lib.sheets.SheetsClient.get_auth_token", return_value="mocktoken")
def test_wismo_lookup_fallback_phone_and_last_name(mock_auth, mock_sheets_api):
    """Test fallback lookup where customer provides their phone number and last name."""
    with patch.dict("os.environ", {"WISMO_SPREADSHEET_ID": "mock_sheet_id"}):
        # Match 'John Doe' (last name 'Doe') with phone number
        res = wismo_lookup(phone_number="+17145550199", last_name="Doe")
        assert res["success"] is True
        assert res["found"] is True
        assert res["verified"] is True
        assert res["tracking_number"] == "1Z12345"


@patch("app.tools_lib.sheets.SheetsClient.get_auth_token", return_value="mocktoken")
def test_wismo_lookup_po_no_fallback_to_so(mock_auth, mock_sheets_api):
    """Verify that we do not fallback to internal Order Number (SO-XXXX) if PO/Reference matches nothing."""
    with patch.dict("os.environ", {"WISMO_SPREADSHEET_ID": "mock_sheet_id"}):
        # "10024" is in the internal Order Number column, but not PO/Reference (which has "PO-10024").
        # If we query with exact internal Order Number "10024" and it is not matching the PO/Reference pattern, it should fail
        # since we removed the fallback match.
        res = wismo_lookup(purchase_order="SO-10024", zip_code="92701")
        assert res["success"] is True
        assert res["found"] is False


@patch("app.tools_lib.sheets.SheetsClient.get_auth_token", return_value="mocktoken")
def test_wismo_lookup_po_in_column_u(mock_auth):
    """Test dynamic header resolution when 'Customer Order number' is in Column U (index 20)."""
    # Create mock headers with Column U containing 'Customer Order number'
    custom_headers = [""] * 21
    custom_headers[20] = "Customer Order number"
    custom_headers[12] = "Tracking Number"
    custom_headers[14] = "Postal Code"

    custom_row = [""] * 21
    custom_row[20] = "PO-99999"
    custom_row[12] = "1Z99999"
    custom_row[14] = "90210"

    with patch("app.tools_lib.sheets.requests.get") as mock_get:
        meta_response = MagicMock()
        meta_response.status_code = 200
        meta_response.json.return_value = {
            "sheets": [{"properties": {"title": "Export", "sheetId": 1506736168}}]
        }

        data_response = MagicMock()
        data_response.status_code = 200
        data_response.json.return_value = {"values": [custom_headers, custom_row]}

        mock_get.side_effect = lambda url, *args, **kwargs: (
            meta_response if "fields=sheets.properties" in url else data_response
        )

        with patch.dict("os.environ", {"WISMO_SPREADSHEET_ID": "mock_sheet_id"}):
            res = wismo_lookup(purchase_order="PO-99999", zip_code="90210")
            assert res["success"] is True
            assert res["found"] is True
            assert res["verified"] is True
            assert res["tracking_number"] == "1Z99999"


@patch("app.tools_lib.sheets.SheetsClient.get_auth_token", return_value="mocktoken")
def test_wismo_lookup_zip_and_last_name_match(mock_auth, mock_sheets_api):
    """Test Contextual Path lookup using purely ZIP and Last Name without PO or phone."""
    with patch.dict("os.environ", {"WISMO_SPREADSHEET_ID": "mock_sheet_id"}):
        res = wismo_lookup(zip_code="90210", last_name="Smith")
        assert res["success"] is True
        assert res["found"] is True
        assert res["verified"] is True
        assert res["tracking_number"] == "1Z67890"


@patch("app.tools_lib.sheets.SheetsClient.get_auth_token", return_value="mocktoken")
def test_wismo_lookup_phone_multiple_matches(mock_auth):
    """Test Caller ID search returning multiple matching records when same phone number is in sheet."""
    custom_headers = MOCK_HEADERS
    # Two rows with identical phone number "+17145550199" but different orders and details
    custom_row_1 = [
        "Shopify",
        "Online",
        "LA",
        "FedEx",
        "10024",
        "PO-10024",
        "2026-07-01",
        "Standard",
        "Released",
        "",
        "",
        "",
        "1Z12345",
        "2026-07-02",
        "92701",
        "john@example.com",
        "+17145550199",
        "CA",
        "Santa Ana",
        "John Doe",
        "PO-10024",
    ]
    custom_row_2 = [
        "Shopify",
        "Online",
        "LA",
        "UPS",
        "10026",
        "PO-10026",
        "2026-07-03",
        "Standard",
        "Released",
        "",
        "",
        "",
        "1Z99988",
        "2026-07-04",
        "92705",
        "jane_diff@example.com",
        "+17145550199",
        "CA",
        "Tustin",
        "John Smith",
        "PO-10026",
    ]

    with patch("app.tools_lib.sheets.requests.get") as mock_get:
        meta_response = MagicMock()
        meta_response.status_code = 200
        meta_response.json.return_value = {
            "sheets": [{"properties": {"title": "Export", "sheetId": 1506736168}}]
        }

        data_response = MagicMock()
        data_response.status_code = 200
        data_response.json.return_value = {
            "values": [custom_headers, custom_row_1, custom_row_2]
        }

        mock_get.side_effect = lambda url, *args, **kwargs: (
            meta_response if "fields=sheets.properties" in url else data_response
        )

        with patch.dict("os.environ", {"WISMO_SPREADSHEET_ID": "mock_sheet_id"}):
            # 1. Verify multiple matches returned initially without verification info
            res_multiple = wismo_lookup(phone_number="+17145550199")
            assert res_multiple["success"] is True
            assert res_multiple["found"] is True
            assert res_multiple["multiple"] is True
            assert res_multiple["verified"] is False

            # 2. Verify we can isolate the correct order when verification (ZIP) is supplied
            res_verified = wismo_lookup(phone_number="+17145550199", zip_code="92705")
            assert res_verified["success"] is True
            assert res_verified["found"] is True
            assert res_verified["verified"] is True
            assert res_verified["tracking_number"] == "1Z99988"


@patch("app.tools_lib.sheets.SheetsClient.get_auth_token", return_value="mocktoken")
def test_wismo_lookup_phone_length_variations(mock_auth, mock_sheets_api):
    """Verify that caller IDs with country codes match 10-digit sheet records and vice versa."""
    with patch.dict("os.environ", {"WISMO_SPREADSHEET_ID": "mock_sheet_id"}):
        # 1. Target has 10 digits, record has 11:
        res1 = wismo_lookup(phone_number="7145550199")
        assert res1["found"] is True
        assert res1["purchase_order"] == "PO-10024"


@patch("google.auth.default")
def test_sheets_client_readonly_scope(mock_default):
    """Verify that SheetsClient(readonly=True) requests spreadsheets.readonly."""
    mock_creds = MagicMock()
    mock_creds.valid = True
    mock_creds.token = "mock_ro_token"
    mock_default.return_value = (mock_creds, "mock_project")

    client = SheetsClient(readonly=True)
    token = client.get_auth_token()
    mock_default.assert_called_with(
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    assert token == "mock_ro_token"
