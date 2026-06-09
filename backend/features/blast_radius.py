"""
Feature 2 — Blast Radius Calculator.

Translates a time-bounded incident into business impact numbers by querying
the transactions and app-logs indices directly.
"""

from datetime import datetime, timezone
from elastic.client import get_es

REVENUE_CRITICAL_SERVICES = {"payment-service", "checkout-service"}

# Rough hourly revenue baseline used for projection (can be tuned per deployment)
HOURLY_REVENUE_BASELINE = 850_000  # $850k/hr ≈ a mid-size e-commerce platform


async def calculate(
    start: str,
    end: str,
    affected_services: list[str] | None = None,
) -> dict:
    """
    Returns a blast radius dict for the incident window [start, end].
    start/end are ISO-8601 strings, e.g. "2026-06-09T14:52:00Z"
    """
    es = get_es()

    tx   = await _transaction_impact(es, start, end)
    logs = await _log_impact(es, start, end, affected_services or [])
    proj = _project_revenue(tx["failed_amount"], start, end)

    return {
        "incident_window": {"start": start, "end": end},
        "users_affected":       tx["unique_users"],
        "transactions_failed":  tx["failed_count"],
        "revenue_lost":         tx["failed_amount"],
        "revenue_projected_1h": proj,
        "errors_total":         logs["total_errors"],
        "downstream_failures":  logs["downstream"],
        "services_affected":    logs["services"],
        "p1_errors":            logs["p1_errors"],
    }


async def _transaction_impact(es, start: str, end: str) -> dict:
    resp = await es.search(
        index="transactions",
        size=0,
        query={
            "bool": {
                "filter": [
                    {"range": {"timestamp": {"gte": start, "lte": end}}},
                    {"term": {"status": "failed"}},
                ]
            }
        },
        aggs={
            "unique_users":  {"cardinality": {"field": "user_id"}},
            "failed_amount": {"sum": {"field": "amount"}},
            "failed_count":  {"value_count": {"field": "transaction_id"}},
        },
    )
    aggs = resp["aggregations"]
    return {
        "unique_users":  aggs["unique_users"]["value"],
        "failed_amount": round(aggs["failed_amount"]["value"] or 0, 2),
        "failed_count":  aggs["failed_count"]["value"],
    }


async def _log_impact(es, start: str, end: str, affected: list[str]) -> dict:
    resp = await es.search(
        index="app-logs",
        size=0,
        query={
            "bool": {
                "filter": [
                    {"range": {"timestamp": {"gte": start, "lte": end}}},
                    {"term": {"level": "ERROR"}},
                ]
            }
        },
        aggs={
            "by_service": {"terms": {"field": "service", "size": 20}},
            "p1_5xx": {
                "filter": {"range": {"status_code": {"gte": 500}}},
                "aggs": {"count": {"value_count": {"field": "request_id"}}},
            },
        },
    )
    aggs = resp["aggregations"]
    total = resp["hits"]["total"]["value"]
    services = [b["key"] for b in aggs["by_service"]["buckets"]]
    downstream = [
        s for s in services
        if s not in REVENUE_CRITICAL_SERVICES and s in services
    ]
    return {
        "total_errors": total,
        "services":     services,
        "downstream":   downstream,
        "p1_errors":    aggs["p1_5xx"]["count"]["value"],
    }


def _project_revenue(lost: float, start: str, end: str) -> float:
    """Extrapolates lost revenue to a full hour based on incident duration."""
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        s = datetime.strptime(start, fmt).replace(tzinfo=timezone.utc)
        e = datetime.strptime(end,   fmt).replace(tzinfo=timezone.utc)
        duration_hours = (e - s).total_seconds() / 3600
        if duration_hours <= 0:
            return lost
        return round(lost / duration_hours, 2)
    except Exception:
        return lost
