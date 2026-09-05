from unittest.mock import MagicMock, patch

from app.tools_lib.sheets import SheetsClient


@patch("app.tools_lib.sheets.execute_with_retry")
@patch.object(SheetsClient, "get_auth_token")
def test_sheets_upsert_log_new_session(mock_token, mock_exec):
    mock_token.return_value = "mock_token"
    # Mock GET for session check: no matching row
    mock_get_resp = MagicMock()
    mock_get_resp.json.return_value = {"values": [["other-session"]]}

    # Mock POST for append
    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"updates": {"updatedRows": 1}}

    mock_exec.side_effect = [mock_get_resp, mock_post_resp]

    client = SheetsClient(spreadsheet_id="mock_log_sheet_id", readonly=False)
    payload = {
        "name": "Jane Doe",
        "phone_number": "+18005550199",
        "email": "jane@example.com",
        "intent": "Lead Capture",
        "urgency": "low",
        "sentiment": "neutral",
        "product_type": "vanity",
        "purchase_order": "PO-9999",
        "ticket_id": "123",
        "summary": "Customer callback lead",
        "session_id": "new-session",
    }
    res = client.upsert_log(payload)
    assert res is True
    assert mock_exec.call_count == 2
    # Verify RAW mode was passed to append in args
    call_args, call_kwargs = mock_exec.call_args_list[1]
    params = call_args[3] if len(call_args) > 3 else call_kwargs.get("params", {})
    assert params.get("valueInputOption") == "RAW"


@patch("app.tools_lib.sheets.execute_with_retry")
@patch.object(SheetsClient, "get_auth_token")
def test_sheets_upsert_log_existing_session_update(mock_token, mock_exec):
    mock_token.return_value = "mock_token"
    # Mock GET returning match at index 0 (row 1)
    mock_get_resp = MagicMock()
    mock_get_resp.json.return_value = {"values": [["existing-session"]]}

    # Mock PUT for update
    mock_put_resp = MagicMock()
    mock_put_resp.json.return_value = {"updatedRows": 1}

    mock_exec.side_effect = [mock_get_resp, mock_put_resp]

    client = SheetsClient(spreadsheet_id="mock_log_sheet_id", readonly=False)
    payload = {
        "name": "Jane Doe",
        "session_id": "existing-session",
    }
    res = client.upsert_log(payload)
    assert res is True
    assert mock_exec.call_count == 2
    call_args, call_kwargs = mock_exec.call_args_list[1]
    assert call_args[0] == "PUT"
    assert "Sheet1!A1" in call_args[1]
    params = (
        call_kwargs.get("params", {})
        if "params" in call_kwargs
        else (call_args[3] if len(call_args) > 3 else {})
    )
    assert params.get("valueInputOption") == "RAW"


@patch.object(SheetsClient, "get_auth_token")
def test_sheets_append_error_handling(mock_token):
    mock_token.side_effect = Exception("Auth failure")
    client = SheetsClient(spreadsheet_id="mock_log_sheet_id", readonly=False)
    res = client.append_log({"name": "Test"})
    assert res is False
