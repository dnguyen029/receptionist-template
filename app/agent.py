# ruff: noqa: E402
import json
import logging
import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field

logger = logging.getLogger("receptionist.agent")

# Clean Path Resolution & Standard Environment Loading
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv()

import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

# Setup environments for Vertex AI
try:
    _, project_id = google.auth.default()
except Exception as e:
    logger.debug(f"Could not load default GCP credentials: {e}")
    project_id = None

project_id = (
    project_id
    or os.environ.get("PROJECT_ID")
    or os.environ.get("GOOGLE_CLOUD_PROJECT")
    or "your-gcp-project-id"
)
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ.get(
    "GOOGLE_CLOUD_LOCATION", "us-central1"
)
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

# Import custom tools
from app.tools import (
    end_session,
    log_lead,
    web_fetch_exa,
    web_search_exa,
    wismo_lookup,
)

# Exa MCP Toolset initialization removed to use native Python tools directly.

model_name = "gemini-3.1-flash-live"
if os.environ.get("INTEGRATION_TEST") == "TRUE":
    model_name = "gemini-2.5-flash"

# Ensure API keys don't interfere with Vertex AI OAuth authentication
if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") == "True":
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("GOOGLE_API_KEY", None)

model = Gemini(
    model=model_name,
    retry_options=types.HttpRetryOptions(attempts=3),
)


def load_prompt(filename: str) -> str:
    """Helper to dynamically load instructions from the local agents/ directory."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    prompt_path = os.path.join(script_dir, "agents", filename)
    try:
        with open(prompt_path, encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        raise FileNotFoundError(
            f"Failed to load agent prompt from {prompt_path}: {e}"
        ) from e


class ReceptionistInput(BaseModel):
    caller_id: str | None = Field(
        default=None,
        description="The E.164 incoming phone number/caller ID of the session.",
    )


def extract_telephony_id(state: dict) -> str | None:
    """Helper to extract telephony caller ID from canonical ADK session state."""
    return state.get("caller_id") or state.get("telephony-caller-id")


def apply_caller_id(state: dict, caller_id: str) -> None:
    """Write caller ID into canonical state keys for ADK tools and prompt interpolation."""
    state["caller_id"] = caller_id
    state["telephony-caller-id"] = caller_id


def sub_agent_callback(callback_context):
    """Ensures caller_id propagates into sub-agent state at entry.
    Primary defense is in wismo_lookup via ToolContext, but this callback
    guards against edge cases where the router's callback hasn't run
    (e.g., direct CES playbook activation without ADK router).
    """
    state = callback_context.state
    caller_id = extract_telephony_id(state)
    if caller_id:
        apply_caller_id(state, caller_id)
    return None


def wismo_sub_agent_callback(callback_context):
    """Extended sub-agent callback for wismo_receptionist.
    Pre-fetches the WISMO result from Google Sheets before the LLM runs
    and stores it in state['auto_wismo_result'] as a JSON string.
    """
    state = callback_context.state
    caller_id = extract_telephony_id(state)
    if caller_id:
        apply_caller_id(state, caller_id)

        if not state.get("auto_wismo_result"):
            try:
                from app.tools_lib import SheetsClient

                mock_sheet_id = os.environ.get("WISMO_SPREADSHEET_ID")
                if mock_sheet_id:
                    sheets = SheetsClient(spreadsheet_id=mock_sheet_id, readonly=True)
                    result = sheets.lookup_wismo_mock(phone_number=caller_id)
                    if result is not None:
                        wismo_res = json.dumps({"success": True, **result})
                        state["auto_wismo_result"] = wismo_res
            except Exception as e:
                logger.warning(f"WISMO pre-fetch failed in callback: {e}")
    return None


# 1. After Hours / Lead Capture Specialist
after_hours_receptionist = Agent(
    name="after_hours_receptionist",
    model=model,
    description="Captures customer name, phone number, and email address to log callback requests.",
    input_schema=ReceptionistInput,
    before_agent_callback=sub_agent_callback,
    tools=[log_lead],
    instruction=load_prompt("receptionist.txt"),
)

# 2. WISMO (Where Is My Order) Specialist
wismo_receptionist = Agent(
    name="wismo_receptionist",
    model=model,
    description="Helps callers track their shipping status and look up order information using Purchase Order (PO) numbers.",
    input_schema=ReceptionistInput,
    before_agent_callback=wismo_sub_agent_callback,
    tools=[wismo_lookup, log_lead],
    instruction=load_prompt("wismo_receptionist.txt"),
)


# 3. FAQ Specialist
faq_instruction = load_prompt("faq_receptionist.txt")
faq_data_paths = [os.path.join(SCRIPT_DIR, "agents", "faq_data.json")]

faq_db = None
for path in faq_data_paths:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                faq_db = json.load(f)
                logger.info(f"Loaded static FAQ database from {path}")
                break
        except json.JSONDecodeError as e:
            logger.warning(f"Static FAQ file at {path} is malformed: {e}")

if faq_db:
    faq_formatted = "\n".join(
        [
            f"Q: {item['question']}\nA: {item['answer']}"
            for item in faq_db.get("faqs", [])
        ]
    )
    faq_instruction += (
        f"\n\n<static_knowledge_base>\n{faq_formatted}\n</static_knowledge_base>"
    )
else:
    logger.warning("Agent booting without optional static FAQ knowledge base.")

faq_receptionist = Agent(
    name="faq_receptionist",
    model=model,
    description="Answers general inquiries, return policy questions, and product specifications using live website search.",
    tools=[web_search_exa, web_fetch_exa, log_lead],
    instruction=faq_instruction,
)

# 4. Exit / Closing Specialist
exit_receptionist = Agent(
    name="exit_receptionist",
    model=model,
    description="Handles polite conversation closure, delivers the final goodbye greeting, and terminates the call.",
    instruction=load_prompt("exit_agent.txt"),
    tools=[end_session],
)


# 5. Root Router Agent (Entry point)
def before_agent_callback(callback_context):
    state = callback_context.state

    caller_id = extract_telephony_id(state)
    if caller_id:
        current_id = state.get("caller_id")
        if not current_id or current_id != caller_id:
            apply_caller_id(state, caller_id)
    return None


router_agent = Agent(
    name="router_agent",
    model=model,
    instruction=load_prompt("router.txt"),
    before_agent_callback=before_agent_callback,
    sub_agents=[
        after_hours_receptionist,
        wismo_receptionist,
        faq_receptionist,
        exit_receptionist,
    ],
)

root_agent = router_agent

app = App(
    root_agent=root_agent,
    name="app",
)
