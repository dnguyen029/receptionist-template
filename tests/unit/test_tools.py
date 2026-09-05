import json
from unittest.mock import MagicMock, patch

from app.tools import (
    end_session,
    log_lead,
    web_fetch_exa,
    web_search_exa,
    wismo_lookup,
)


def test_end_session():
    res = end_session()
    assert res == {"status": "session_terminated", "success": True}


@patch("app.tools.SheetsClient")
@patch("app.tools.ZendeskClient")
def test_log_lead_success_with_summary(mock_zendesk_cls, mock_sheets_cls):
    mock_sheets = MagicMock()
    mock_sheets.upsert_log.return_value = True
    mock_sheets_cls.return_value = mock_sheets

    mock_zendesk = MagicMock()
    mock_zendesk.find_recent_ticket_by_phone.return_value = None
    mock_zendesk.create_ticket.return_value = True
    mock_zendesk_cls.return_value = mock_zendesk

    res = log_lead(
        name="John Doe",
        phone_number="+18005550199",
        email="john@example.com",
        intent="Lead Capture",
        urgency="high",
        sentiment="neutral",
        summary="Customer wants to purchase item",
    )
    assert res["success"] is True
    assert res["sync_status"] == "synced_all"
    mock_zendesk.create_ticket.assert_called_once()


@patch("app.tools.SheetsClient")
@patch("app.tools.ZendeskClient")
def test_log_lead_existing_ticket_update(mock_zendesk_cls, mock_sheets_cls):
    mock_sheets = MagicMock()
    mock_sheets.upsert_log.return_value = True
    mock_sheets_cls.return_value = mock_sheets

    mock_zendesk = MagicMock()
    mock_zendesk.find_recent_ticket_by_phone.return_value = "12345"
    mock_zendesk.update_ticket_with_summary.return_value = True
    mock_zendesk_cls.return_value = mock_zendesk

    res = log_lead(
        name="John Doe",
        phone_number="+18005550199",
        email="john@example.com",
        intent="Lead Capture",
        urgency="low",
        sentiment="neutral",
        summary="Follow up on previous call",
    )
    assert res["success"] is True
    assert res["sync_status"] == "synced_all"
    mock_zendesk.update_ticket_with_summary.assert_called_once()


@patch("app.tools.SheetsClient")
def test_log_lead_error_handling(mock_sheets_cls):
    mock_sheets_cls.side_effect = Exception("Google API Error")
    res = log_lead(
        name="John Doe",
        phone_number="+18005550199",
        email="john@example.com",
        intent="Lead Capture",
        urgency="low",
        sentiment="neutral",
    )
    assert res["success"] is False
    assert "Google API Error" in res["error"]


@patch("app.tools.SheetsClient")
def test_wismo_lookup_success(mock_sheets_cls, monkeypatch):
    monkeypatch.setenv("WISMO_SPREADSHEET_ID", "mock_id")
    mock_sheets = MagicMock()
    mock_sheets.lookup_wismo_mock.return_value = {
        "found": True,
        "verified": True,
        "carrier": "FedEx",
    }
    mock_sheets_cls.return_value = mock_sheets

    res = wismo_lookup(purchase_order="PO-123456", zip_code="90210")
    assert res["success"] is True
    assert res["found"] is True
    assert res["carrier"] == "FedEx"


def test_wismo_lookup_missing_env(monkeypatch):
    monkeypatch.delenv("WISMO_SPREADSHEET_ID", raising=False)
    res = wismo_lookup(purchase_order="PO-123456")
    assert res["success"] is False
    assert "WISMO_SPREADSHEET_ID" in res["error"]


def test_wismo_lookup_cached_state():
    context = MagicMock()
    context.state = {"auto_wismo_result": json.dumps({"cached": True, "found": True})}
    res = wismo_lookup(tool_context=context)
    assert res == {"cached": True, "found": True}


@patch("requests.post")
def test_web_search_exa(mock_post, monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "results": [{"title": "OmniRetail Support", "url": "https://example.com"}]
    }
    mock_post.return_value = mock_response

    res = web_search_exa("products")
    assert "results" in res
    assert res["results"][0]["title"] == "OmniRetail Support"


@patch("requests.post")
def test_web_fetch_exa(mock_post, monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {
                "title": "OmniRetail Support",
                "url": "https://example.com",
                "text": "A" * 2000,
            }
        ]
    }
    mock_post.return_value = mock_response

    res = web_fetch_exa("https://example.com")
    assert len(res["results"]) == 1
    assert len(res["results"][0]["text"]) == 1503  # 1500 + "..."
