import base64
import logging
import os
import re

import requests

logger = logging.getLogger("omniretail.tools.zendesk")


class ZendeskClient:
    def __init__(self):
        self.subdomain = os.getenv("ZENDESK_SUBDOMAIN")
        self.email = os.getenv("ZENDESK_EMAIL")
        self.token = (
            os.getenv("ZENDESK_API_TOKEN")
            or os.getenv("ZENDESK_TOKEN")
            or os.getenv("ZENDESK_API_KEY")
        )

        if not self.subdomain or not self.email or not self.token:
            logger.error("Zendesk configuration missing (SUBDOMAIN/EMAIL/TOKEN)")
            self.auth_header = None
            return

        # Handle formatting token credentials
        formatted_email = (
            self.email if self.email.endswith("/token") else f"{self.email}/token"
        )
        auth_str = f"{formatted_email}:{self.token}"
        auth_bytes = auth_str.encode("utf-8")
        auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")
        self.auth_header = f"Basic {auth_b64}"
        self.base_url = f"https://{self.subdomain}.zendesk.com/api/v2"

    def _resolve_assignee_id(self, email: str) -> int | None:
        """Resolves a Zendesk agent email address to a numeric user ID.

        Calls GET /api/v2/users/search.json?query=email:{email} and returns
        the first matching user's ID, or None if not found or on error.
        """
        if not self.auth_header:
            return None
        headers = {"Authorization": self.auth_header, "Accept": "application/json"}
        url = f"{self.base_url}/users/search.json"
        params = {"query": f"email:{email}"}
        try:
            logger.info(f"Resolving Zendesk assignee ID for email: {email}")
            r = requests.get(url, headers=headers, params=params, timeout=10)
            r.raise_for_status()
            users = r.json().get("users", [])
            if users:
                assignee_id = users[0]["id"]
                logger.info(f"Resolved assignee ID {assignee_id} for {email}")
                return assignee_id
            logger.warning(f"No Zendesk user found for email: {email}")
            return None
        except Exception as e:
            logger.error(f"Failed to resolve Zendesk assignee ID for {email}: {e}")
            return None

    def create_ticket(
        self,
        name: str,
        email: str,
        phone_number: str,
        summary: str,
        status: str = "open",
        urgency: str = "low",
        purchase_order: str = "",
        intent: str = "Lead Capture",
        session_id: str = "",
    ) -> bool:
        """Creates a new Zendesk ticket for an after-hours lead callback.

        Sets the requester from caller details, attaches the AI summary as a
        private internal note, and auto-assigns the ticket to the agent
        configured in ZENDESK_EMAIL (the API credential owner).

        Args:
            name: Caller's name.
            email: Caller's email address.
            phone: Caller's phone number.
            summary: AI-generated conversation summary.
            status: Ticket status — "open" (high urgency) or "pending" (low).
            urgency: "high" or "low" — maps to Zendesk priority field.
            purchase_order: Optional PO number to include in the description.
            intent: Caller's intent (e.g. "Lead Capture", "FAQ", "WISMO").
            session_id: Unique caller session ID used for idempotency.

        Returns:
            True on successful creation, False otherwise.
        """
        if not self.auth_header:
            logger.error("Zendesk credentials not configured")
            return False

        headers = {
            "Authorization": self.auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if session_id:
            headers["Idempotency-Key"] = session_id

        # Resolve assignee: use ZENDESK_ASSIGNEE_EMAIL if set, fall back to
        # ZENDESK_EMAIL (the configured Zendesk API credential owner)
        assignee_email = os.getenv("ZENDESK_ASSIGNEE_EMAIL") or self.email
        assignee_id = self._resolve_assignee_id(assignee_email)

        priority = "high" if urgency == "high" else "normal"
        po_line = f"\nPurchase Order: {purchase_order}" if purchase_order else ""
        description = (
            f"AI Receptionist call captured via RingCentral.\n"
            f"Intent: {intent}\n"
            f"Name: {name}\nPhone: {phone_number}\nEmail: {email}{po_line}\n\n"
            f"AI SUMMARY:\n{summary}"
        )

        ticket_payload: dict = {
            "ticket": {
                "subject": f"AI Receptionist Call: {intent} - {name}",
                "comment": {"body": description, "public": False},
                "status": status,
                "priority": priority,
                "tags": ["after_hours", "ai_receptionist", "ringcentral", "voice"],
                "requester": {"name": name, "email": email, "phone": phone_number},
            }
        }

        # Only set assignee_id if successfully resolved — avoids API rejection
        if assignee_id:
            ticket_payload["ticket"]["assignee_id"] = assignee_id

        # Append async=true for non-blocking background ticket creation to keep webhook low latency
        url = f"{self.base_url}/tickets.json?async=true"
        try:
            logger.info(f"Creating new Zendesk ticket for after-hours lead: {name}")
            r = requests.post(url, headers=headers, json=ticket_payload, timeout=15)
            r.raise_for_status()
            created_id = r.json().get("ticket", {}).get("id", "unknown")
            logger.info(
                f"Created Zendesk ticket #{created_id} "
                f"assigned to {assignee_email} (id={assignee_id})"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to create Zendesk ticket for {name}: {e}")
            return False

    def update_ticket_with_summary(self, ticket_id, summary, status="open"):
        """Performs a single PUT update on Zendesk tickets:
        Appends an internal (private) comment with the summary and updates the ticket status.
        """
        if not self.auth_header:
            logger.error("Zendesk credentials not configured")
            return False

        headers = {
            "Authorization": self.auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            payload = {
                "ticket": {
                    "status": status,
                    "comment": {
                        "body": f"AI RECEPTIONIST SUMMARY:\n{summary}",
                        "public": False,
                    },
                }
            }
            url = f"{self.base_url}/tickets/{ticket_id}.json"
            logger.info(
                f"Syncing note and status '{status}' to Zendesk Ticket {ticket_id}"
            )
            r = requests.put(url, headers=headers, json=payload, timeout=15)
            r.raise_for_status()

            logger.info("Successfully completed consolidated Zendesk PUT Sync")
            return True
        except Exception as e:
            logger.error(f"Zendesk sync failed for ticket {ticket_id}: {e}")
            return False

    def search_tickets_by_po(self, po_number):
        """Searches for tickets matching the given purchase order number."""
        if not self.auth_header:
            logger.error("Zendesk credentials not configured")
            return None

        # Clean PO number for query compatibility (extract first PO token and strip quotes, semicolons, and special characters)
        raw_token = str(po_number).split(";")[0].split('"')[0].strip()
        clean_po = re.sub(r"[^a-zA-Z0-9\-_#]", "", raw_token).strip()
        if not clean_po:
            return []

        headers = {"Authorization": self.auth_header, "Accept": "application/json"}

        url = f"{self.base_url}/search.json"
        params = {"query": f'type:ticket "{clean_po}"'}
        try:
            logger.info(f"Searching Zendesk tickets for PO {clean_po}")
            r = requests.get(url, headers=headers, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            return data.get("results", [])
        except Exception as e:
            logger.error(f"Zendesk search failed for PO {clean_po}: {e}")
            return None

    def find_recent_ticket_by_phone(self, phone_number: str) -> str | None:
        """Searches for open or pending tickets created in the last 15 minutes associated with the phone number.

        Returns the ticket ID if found, otherwise None.
        """
        if not self.auth_header:
            return None

        # Clean phone number for query compatibility (digits and leading plus only)
        clean_phone = re.sub(r"[^\d+]", "", phone_number)
        if not clean_phone:
            return None

        headers = {"Authorization": self.auth_header, "Accept": "application/json"}
        url = f"{self.base_url}/search.json"
        # Search for open/pending tickets containing the phone number
        query = f'type:ticket status<solved "{clean_phone}"'
        params = {"query": query, "sort_by": "created_at", "sort_order": "desc"}

        try:
            logger.info(
                f"Deduplication search: checking for recent tickets with phone: {clean_phone}"
            )
            r = requests.get(url, headers=headers, params=params, timeout=10)
            r.raise_for_status()
            results = r.json().get("results", [])

            # Match tickets created recently (e.g. last 15 minutes)
            import datetime

            now = datetime.datetime.now(datetime.UTC)
            for ticket in results:
                created_at_str = ticket.get("created_at")
                if created_at_str:
                    # Parse ISO format (e.g., '2026-06-15T16:35:00Z')
                    created_at = datetime.datetime.strptime(
                        created_at_str, "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=datetime.UTC)
                    if (now - created_at).total_seconds() < 900:  # 15 minutes
                        logger.info(f"Found recent match: Ticket #{ticket.get('id')}")
                        return str(ticket.get("id"))
            return None
        except Exception as e:
            logger.error(f"Failed to search recent tickets: {e}")
            return None
