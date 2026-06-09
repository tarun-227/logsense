"""
ADK 2.2.0 agent wired to the Elastic MCP server via stdio.
"""

import os
import sys
import uuid

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters
import google.genai.types as genai_types

from .prompts import SYSTEM_PROMPT

load_dotenv()

ES_URL     = os.getenv("ES_URL", "http://localhost:9200")
ES_API_KEY = os.getenv("ES_API_KEY", "")
MODEL      = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
APP_NAME   = "logsense"

_runner: Runner | None = None
_session_service = InMemorySessionService()


def _build_runner() -> Runner:
    mcp_env = {"ES_URL": ES_URL}
    if ES_API_KEY:
        mcp_env["ES_API_KEY"] = ES_API_KEY

    server_script = os.path.join(os.path.dirname(__file__), "..", "elastic_mcp_server.py")

    mcp_toolset = McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=sys.executable,
                args=[server_script],
                env={**os.environ, **mcp_env},
            ),
            timeout=60.0,
        )
    )

    agent = LlmAgent(
        model=MODEL,
        name="logsense_agent",
        instruction=SYSTEM_PROMPT,
        tools=[mcp_toolset],
    )

    return Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=_session_service,
        auto_create_session=True,
    )


async def get_runner() -> Runner:
    global _runner
    if _runner is None:
        _runner = _build_runner()
    return _runner


async def ensure_session(session_id: str, user_id: str = "default"):
    try:
        await _session_service.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
    except Exception:
        await _session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )


async def chat(message: str, session_id: str, user_id: str = "default"):
    runner = await get_runner()
    await ensure_session(session_id, user_id)

    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=message)],
    )

    full_response = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    full_response += part.text

    yield full_response


async def shutdown():
    pass
