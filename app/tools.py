import json
import logging
import os

from google.adk.tools import ToolContext

from app.tools_lib import SheetsClient, ZendeskClient

logger = logging.getLogger("receptionist.tools")


def log_lead(
    name: str,
    phone_number: str,
    email: str,
    intent: str,
    urgency: str,
    sentiment: str,
    purchase_order: str | None = "",
    product_type: str | None = "",
    ticket_id: str | None = "",
    summary: str | None = "",
    session_id: str | None = "",
) -> dict:
    """Logs the captured customer lead callback details to Google Sheets and optionally updates Zendesk.

    Args:
        name: The customer's name.
        phone_number: The customer's phone number in E.164 format (e.g. +1XXXXXXXXXX).
        email: The customer's verified email address.
        intent: The reason for calling (e.g. "Lead Capture", "FAQ", "WISMO").
        urgency: The urgency level of the request ("high" or "low").
        sentiment: The customer's sentiment ("neutral" or "negative").
        purchase_order: The optional purchase order number.
        product_type: The optional product category (e.g. electronics, furniture, appliances, apparel).
        ticket_id: The optional Zendesk ticket ID if associated with a ticket.
        summary: The optional summary of the conversation to sync to Zendesk.
        session_id: The optional session ID of the caller.

    Returns:
        A dictionary with "success" (bool) and "sync_status" (str).
    """
    logger.info(f"log_lead tool called for name: {name}, phone_number: {phone_number}")
    try:
        sheets = SheetsClient()
        payload = {
            "name": name,
            "phone_number": phone_number,
            "email": email,
            "purchase_order": purchase_order,
            "intent": intent,
            "urgency": urgency,
            "sentiment": sentiment,
            "product_type": product_type,
            "ticket_id": ticket_id,
            "summary": summary,
            "session_id": session_id,
            "timestamp": "",
        }
        zendesk_success = False
        if summary:
            zendesk = ZendeskClient()
            status = "open" if urgency == "high" else "pending"

            # Resolve ticket_id: check if a ticket was recently created for this caller's phone
            active_ticket_id = ticket_id
            if not active_ticket_id and phone_number:
                active_ticket_id = zendesk.find_recent_ticket_by_phone(phone_number)

            if active_ticket_id:
                # Update existing ticket with AI summary (no change to assignment)
                zendesk_success = zendesk.update_ticket_with_summary(
                    ticket_id=active_ticket_id, summary=summary, status=status
                )
                payload["ticket_id"] = active_ticket_id
            else:
                # After-hours lead — create a new ticket assigned to the configured agent
                zendesk_success = zendesk.create_ticket(
                    name=name,
                    email=email,
                    phone_number=phone_number,
                    summary=summary,
                    status=status,
                    urgency=urgency,
                    purchase_order=purchase_order or "",
                    intent=intent,
                    session_id=session_id,
                )

        sheets_success = sheets.upsert_log(payload)
        sync_status = (
            "synced_all" if (sheets_success and zendesk_success) else "synced_partial"
        )
        return {"success": sheets_success, "sync_status": sync_status}
    except Exception as e:
        logger.error(f"Error in log_lead tool: {e}")
        return {"success": False, "error": str(e)}


def wismo_lookup(
    purchase_order: str | None = None,
    zip_code: str | None = None,
    phone_number: str | None = None,
    last_name: str | None = None,
    tool_context: ToolContext = None,
) -> dict:
    """Looks up order shipping status and details.
    Queries the mock Google Sheet exclusively.

    Args:
        purchase_order: The purchase order number to lookup (e.g. PO-XXXXXX).
        zip_code: The shipping ZIP/postal code for verification.
        phone_number: The customer phone number.
        last_name: The customer last name.

    Returns:
        A dictionary containing order status, tracking, and details.
    """
    # Cache-hit path: if no explicit lookup args were supplied (i.e. this is the
    # initial CallerID check), and the callback already pre-fetched the result,
    # return it immediately without making any Google Sheets API calls.
    if not any([purchase_order, zip_code, last_name, phone_number]):
        if tool_context and tool_context.state:
            cached = tool_context.state.get("auto_wismo_result")
            if cached:
                try:
                    return json.loads(cached)
                except (json.JSONDecodeError, TypeError):
                    pass

    if not phone_number and tool_context and tool_context.state:
        phone_number = tool_context.state.get("caller_id") or None

    logger.info(
        f"wismo_lookup tool called (PO: {purchase_order}, ZIP: {zip_code}, Phone: {phone_number}, Last Name: {last_name})"
    )

    try:
        mock_sheet_id = os.environ.get("WISMO_SPREADSHEET_ID")
        if not mock_sheet_id:
            logger.error("WISMO_SPREADSHEET_ID is not configured in the environment.")
            return {
                "success": False,
                "error": "WISMO_SPREADSHEET_ID environment variable is missing.",
            }

        sheets = SheetsClient(spreadsheet_id=mock_sheet_id, readonly=True)
        mock_data = sheets.lookup_wismo_mock(
            purchase_order=purchase_order,
            zip_code=zip_code,
            phone_number=phone_number,
            last_name=last_name,
        )

        if mock_data:
            return {"success": True, **mock_data}
        else:
            return {
                "success": True,
                "found": False,
                "verified": False,
                "details": "No matching order found.",
            }
    except Exception as e:
        logger.error(f"Error in wismo_lookup tool: {e}")
        return {"success": False, "error": str(e)}


def _get_exa_api_key() -> str:
    """Helper to fetch Exa API key from environment."""
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        raise ValueError("EXA_API_KEY environment variable is not configured.")
    return api_key


def web_search_exa(query: str) -> dict:
    """Search the web using Exa search engine.

    Args:
        query: The search query string.

    Returns:
        A dictionary containing the search results.
    """
    import requests

    try:
        api_key = _get_exa_api_key()
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        payload = {"query": query, "useAutoprompt": True, "numResults": 5}
        response = requests.post(
            "https://api.exa.ai/search", json=payload, headers=headers, timeout=10
        )
        response.raise_for_status()
        data = response.json()

        # Optimize payload for LLM to prevent token explosion
        results = data.get("results", [])
        optimized_results = [
            {"title": r.get("title"), "url": r.get("url")} for r in results
        ]
        return {"results": optimized_results}
    except Exception as e:
        logger.warning(f"Exa search failed for query '{query}': {e}")
        return {"results": [], "error": str(e)}


def web_fetch_exa(url: str) -> dict:
    """Fetch the contents of a specific URL using Exa.

    Args:
        url: The URL to fetch contents for.

    Returns:
        A dictionary containing the page contents.
    """
    import requests

    try:
        api_key = _get_exa_api_key()
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        payload = {"ids": [url]}
        response = requests.post(
            "https://api.exa.ai/contents", json=payload, headers=headers, timeout=10
        )
        response.raise_for_status()
        data = response.json()

        # Heavily truncate text to prevent token quota exhaustion
        results = data.get("results", [])
        optimized_results = []
        for r in results:
            text = r.get("text", "")
            # Truncate text to max 1500 characters
            truncated_text = text[:1500] + ("..." if len(text) > 1500 else "")
            optimized_results.append(
                {"title": r.get("title"), "url": r.get("url"), "text": truncated_text}
            )
        return {"results": optimized_results}
    except Exception as e:
        logger.warning(f"Exa fetch failed for URL '{url}': {e}")
        return {"results": [], "error": str(e)}


def end_session() -> dict:
    """Terminates the call session and hangs up the line.

    Returns:
        A dictionary indicating the session termination status.
    """
    logger.info("end_session tool called")
    return {"status": "session_terminated", "success": True}
