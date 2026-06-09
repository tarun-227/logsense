"""
Feature 1 — Proactive Anomaly Watchdog.

Runs every POLL_INTERVAL seconds.  Compares the last 5-minute error-rate window
against the previous 60-minute rolling baseline using direct ES aggregations.
When Gemini classifies a deviation as anomalous, it kicks off an auto-investigation
and broadcasts the alert to all connected WebSocket clients.
"""

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Callable, Awaitable

from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv

from elastic.client import get_es
from agent.prompts import WATCHDOG_EVAL_PROMPT

load_dotenv()

_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY", ""))
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

POLL_INTERVAL = int(os.getenv("WATCHDOG_INTERVAL", "60"))

AlertCallback = Callable[[dict], Awaitable[None]]
_subscribers: list[AlertCallback] = []


def subscribe(callback: AlertCallback):
    _subscribers.append(callback)


def unsubscribe(callback: AlertCallback):
    try:
        _subscribers.remove(callback)
    except ValueError:
        pass


async def _broadcast(alert: dict):
    for cb in list(_subscribers):
        try:
            await cb(alert)
        except Exception:
            pass


async def _fetch_metrics(es, now: datetime, window_minutes: int) -> dict:
    since = (now - timedelta(minutes=window_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    resp = await es.search(
        index="metrics",
        size=0,
        query={
            "bool": {
                "filter": [
                    {"range": {"timestamp": {"gte": since, "lte": until}}}
                ]
            }
        },
        aggs={
            "by_service": {
                "terms": {"field": "service", "size": 20},
                "aggs": {
                    "avg_error_rate": {"avg": {"field": "error_rate"}},
                    "avg_latency":    {"avg": {"field": "avg_latency_ms"}},
                },
            }
        },
    )

    result = {}
    for bucket in resp["aggregations"]["by_service"]["buckets"]:
        result[bucket["key"]] = {
            "error_rate":     round(bucket["avg_error_rate"]["value"] or 0, 4),
            "avg_latency_ms": round(bucket["avg_latency"]["value"] or 0, 1),
        }
    return result


async def _classify_anomaly(current: dict, baseline: dict) -> dict:
    prompt = WATCHDOG_EVAL_PROMPT.format(
        current=json.dumps(current, indent=2),
        baseline=json.dumps(baseline, indent=2),
    )
    response = _client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json"
        ),
    )
    try:
        return json.loads(response.text)
    except json.JSONDecodeError:
        return {"is_anomaly": False, "severity": "none", "affected_services": [],
                "summary": "Classification failed", "confidence": 0}


async def _auto_investigate(alert: dict) -> str:
    from agent.runner import chat
    services = ", ".join(alert.get("affected_services", ["unknown"]))
    session_id = f"watchdog-auto"
    result = ""
    async for chunk in chat(
        f"Investigate the current incident. Affected services: {services}. "
        f"Severity: {alert.get('severity')}. Summary: {alert.get('summary')}. "
        "Give me: (1) confirmed root cause or top hypothesis, "
        "(2) how many users are affected in the last 10 minutes, "
        "(3) immediate mitigation steps.",
        session_id=session_id,
    ):
        result += chunk
    return result


async def _poll_once():
    es = get_es()
    now = datetime.now(timezone.utc)

    current  = await _fetch_metrics(es, now, window_minutes=5)
    baseline = await _fetch_metrics(es, now, window_minutes=60)

    if not current:
        return

    classification = await _classify_anomaly(current, baseline)

    if not classification.get("is_anomaly") or classification.get("severity") == "none":
        return

    alert = {
        "type":              "anomaly_detected",
        "timestamp":         now.isoformat(),
        "severity":          classification.get("severity", "P3"),
        "affected_services": classification.get("affected_services", []),
        "summary":           classification.get("summary", ""),
        "confidence":        classification.get("confidence", 0),
        "current_metrics":   current,
        "baseline_metrics":  baseline,
        "investigation":     None,
    }

    await _broadcast(alert)
    asyncio.create_task(_run_investigation(alert))


async def _run_investigation(alert: dict):
    try:
        investigation = await _auto_investigate(alert)
        alert["investigation"] = investigation
        alert["type"] = "investigation_complete"
        await _broadcast(alert)
    except Exception as e:
        alert["investigation"] = f"Auto-investigation failed: {e}"
        alert["type"] = "investigation_complete"
        await _broadcast(alert)


async def run_watchdog():
    print(f"Watchdog started (polling every {POLL_INTERVAL}s)")
    while True:
        try:
            await _poll_once()
        except Exception as e:
            print(f"Watchdog poll error: {e}")
        await asyncio.sleep(POLL_INTERVAL)
