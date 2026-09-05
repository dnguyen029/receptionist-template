import json
from unittest.mock import MagicMock, patch

import pytest

from app.agent import (
    apply_caller_id,
    extract_telephony_id,
    load_prompt,
    sub_agent_callback,
    wismo_sub_agent_callback,
)


def test_load_prompt_success():
    content = load_prompt("router.txt")
    assert len(content) > 0


def test_load_prompt_missing():
    with pytest.raises(FileNotFoundError):
        load_prompt("non_existent_prompt.txt")


def test_extract_and_apply_telephony_id():
    state = {}
    apply_caller_id(state, "+18005550199")
    assert state["caller_id"] == "+18005550199"
    assert state["telephony-caller-id"] == "+18005550199"

    extracted = extract_telephony_id(state)
    assert extracted == "+18005550199"


def test_sub_agent_callback():
    context = MagicMock()
    context.state = {"telephony-caller-id": "+18005550199"}
    res = sub_agent_callback(context)
    assert res is None
    assert context.state["caller_id"] == "+18005550199"


@patch("app.tools_lib.SheetsClient")
def test_wismo_sub_agent_callback_prefetch(mock_sheets_cls, monkeypatch):
    monkeypatch.setenv("WISMO_SPREADSHEET_ID", "mock_id")
    mock_sheets = MagicMock()
    mock_sheets.lookup_wismo_mock.return_value = {
        "found": True,
        "verified": False,
        "carrier": "UPS",
    }
    mock_sheets_cls.return_value = mock_sheets

    context = MagicMock()
    context.state = {"caller_id": "+18005550199"}
    res = wismo_sub_agent_callback(context)
    assert res is None
    assert "auto_wismo_result" in context.state
    parsed = json.loads(context.state["auto_wismo_result"])
    assert parsed["success"] is True
    assert parsed["carrier"] == "UPS"


@patch("app.tools_lib.SheetsClient")
def test_wismo_sub_agent_callback_error(mock_sheets_cls, monkeypatch):
    monkeypatch.setenv("WISMO_SPREADSHEET_ID", "mock_id")
    mock_sheets_cls.side_effect = Exception("Sheets error")

    context = MagicMock()
    context.state = {"caller_id": "+18005550199"}
    res = wismo_sub_agent_callback(context)
    assert res is None
