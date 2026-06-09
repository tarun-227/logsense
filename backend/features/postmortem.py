"""
Feature 3 — Post-Mortem Generator.
"""

import json
import os
from datetime import datetime, timezone

from google import genai
from dotenv import load_dotenv

from agent.prompts import POSTMORTEM_PROMPT

load_dotenv()
_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY", ""))
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")


async def generate(
    conversation: list[dict],
    blast_radius: dict | None = None,
    incident_start: str = "",
    incident_end: str = "",
    severity: str = "P1",
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    duration = "unknown"
    if incident_start and incident_end:
        try:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            s = datetime.strptime(incident_start, fmt)
            e = datetime.strptime(incident_end,   fmt)
            mins = int((e - s).total_seconds() / 60)
            duration = f"{mins} minutes"
        except Exception:
            pass

    convo_text = "\n".join(
        f"**{msg['role'].title()}:** {msg['content']}"
        for msg in conversation
    )

    incident_data = {
        "start":    incident_start or "unknown",
        "end":      incident_end   or "unknown",
        "duration": duration,
    }

    blast_text = json.dumps(blast_radius, indent=2) if blast_radius else "Not calculated"

    prompt = POSTMORTEM_PROMPT.format(
        incident_data=json.dumps(incident_data, indent=2),
        conversation=convo_text,
        blast_radius=blast_text,
        date=now,
        severity=severity,
        duration=duration,
    )

    response = _client.models.generate_content(model=MODEL, contents=prompt)
    return response.text
