from unittest.mock import MagicMock, patch

from app.tools import log_lead
from app.tools_lib.zendesk import ZendeskClient


@patch("app.tools_lib.zendesk.requests.get")
@patch("app.tools_lib.zendesk.requests.post")
def test_zendesk_client_create_ticket_with_assignee(mock_post, mock_get):
    # Mock user search endpoint resolution
    mock_get_response = MagicMock()
    mock_get_response.json.return_value = {
        "users": [{"id": 1234567, "email": "support@omniretail.com"}]
    }
    mock_get.return_value = mock_get_response

    # Mock ticket creation endpoint response
    mock_post_response = MagicMock()
    mock_post_response.json.return_value = {"ticket": {"id": 9999}}
    mock_post.return_value = mock_post_response

    # Setup ZendeskClient
    with patch.dict(
        "os.environ",
        {
            "ZENDESK_SUBDOMAIN": "omniretail",
            "ZENDESK_EMAIL": "support@omniretail.com",
            "ZENDESK_TOKEN": "mocktoken",
        },
    ):
        client = ZendeskClient()
        success = client.create_ticket(
            name="Alice Smith",
            email="alice@example.com",
            phone_number="+1234567890",
            summary="Needs order lookup details.",
            status="open",
            urgency="high",
            purchase_order="PO-123",
        )

        assert success is True
        # Verify resolution request was made
        mock_get.assert_called_once()
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        assert payload["ticket"]["assignee_id"] == 1234567
        assert "after_hours" in payload["ticket"]["tags"]
        assert "ai_receptionist" in payload["ticket"]["tags"]
        assert payload["ticket"]["requester"]["name"] == "Alice Smith"
        assert payload["ticket"]["requester"]["email"] == "alice@example.com"
        assert payload["ticket"]["status"] == "open"
        assert payload["ticket"]["priority"] == "high"


@patch("app.tools.SheetsClient")
@patch("app.tools_lib.zendesk.requests.get")
@patch("app.tools_lib.zendesk.requests.post")
def test_log_lead_triggers_ticket_creation(mock_post, mock_get, mock_sheets):
    # Mock sheets logging
    mock_sheets.return_value.upsert_log.return_value = True

    # Mock assignee search & ticket creation
    mock_get_response = MagicMock()
    mock_get_response.json.return_value = {
        "users": [{"id": 1234567, "email": "support@omniretail.com"}]
    }
    mock_get.return_value = mock_get_response

    mock_post_response = MagicMock()
    mock_post_response.json.return_value = {"ticket": {"id": 9999}}
    mock_post.return_value = mock_post_response

    with patch.dict(
        "os.environ",
        {
            "ZENDESK_SUBDOMAIN": "omniretail",
            "ZENDESK_EMAIL": "support@omniretail.com",
            "ZENDESK_TOKEN": "mocktoken",
        },
    ):
        res = log_lead(
            name="Alice Smith",
            phone_number="+1234567890",
            email="alice@example.com",
            intent="Lead Capture",
            urgency="high",
            sentiment="neutral",
            summary="Conversation summary goes here",
        )
        assert res["success"] is True
        assert res["sync_status"] == "synced_all"
        # Confirm ticket creation was triggered because ticket_id was empty but summary was provided
        mock_post.assert_called_once()


@patch("app.tools_lib.zendesk.requests.get")
def test_find_recent_ticket_by_phone_sanitization(mock_get):
    """Test that find_recent_ticket_by_phone sanitizes malicious or messy phone inputs."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"results": []}
    mock_get.return_value = mock_response

    with patch.dict(
        "os.environ",
        {
            "ZENDESK_SUBDOMAIN": "omniretail",
            "ZENDESK_EMAIL": "support@omniretail.com",
            "ZENDESK_TOKEN": "mocktoken",
        },
    ):
        client = ZendeskClient()
        # Input containing spaces, dashes, parentheses, and SQL/injection-like characters
        client.find_recent_ticket_by_phone("+1 (714) 555-0199; DROP TABLE--")

        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        query_param = kwargs["params"]["query"]
        # Assert only valid digits (and leading plus format) are in query, no letters/symbols/semicolons
        assert '"+17145550199"' in query_param
        assert "DROP" not in query_param
        assert ";" not in query_param


@patch("app.tools_lib.zendesk.requests.get")
def test_search_tickets_by_po_sanitization(mock_get):
    """Test that search_tickets_by_po sanitizes special characters and enforces a timeout."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"results": []}
    mock_get.return_value = mock_response

    with patch.dict(
        "os.environ",
        {
            "ZENDESK_SUBDOMAIN": "omniretail",
            "ZENDESK_EMAIL": "support@omniretail.com",
            "ZENDESK_TOKEN": "mocktoken",
        },
    ):
        client = ZendeskClient()
        # Input containing quotes, semicolons, and injection patterns
        client.search_tickets_by_po('PO-10024"; DROP TABLE--')

        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        query_param = kwargs["params"]["query"]
        assert '"PO-10024"' in query_param
        assert "DROP" not in query_param
        assert ";" not in query_param
        assert kwargs.get("timeout") == 10


@patch("app.tools_lib.zendesk.requests.put")
def test_update_ticket_with_summary(mock_put):
    mock_put_response = MagicMock()
    mock_put_response.json.return_value = {"ticket": {"id": 12345}}
    mock_put.return_value = mock_put_response

    with patch.dict(
        "os.environ",
        {
            "ZENDESK_SUBDOMAIN": "omniretail",
            "ZENDESK_EMAIL": "support@omniretail.com",
            "ZENDESK_TOKEN": "mocktoken",
        },
    ):
        client = ZendeskClient()
        success = client.update_ticket_with_summary(
            ticket_id="12345", summary="Updated caller summary", status="pending"
        )
        assert success is True
        mock_put.assert_called_once()
        _, kwargs = mock_put.call_args
        assert "Updated caller summary" in kwargs["json"]["ticket"]["comment"]["body"]
        assert kwargs["json"]["ticket"]["status"] == "pending"


def test_zendesk_client_missing_creds(monkeypatch):
    monkeypatch.delenv("ZENDESK_TOKEN", raising=False)
    monkeypatch.delenv("ZENDESK_API_TOKEN", raising=False)
    monkeypatch.delenv("ZENDESK_API_KEY", raising=False)
    client = ZendeskClient()
    assert client.auth_header is None
    assert client.create_ticket("Name", "email@example.com", "+123", "Summary") is False
