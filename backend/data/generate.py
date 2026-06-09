"""
Generates a realistic e-commerce log dataset and ingests it into Elasticsearch.

Incident baked in:
  - 14:51 UTC  — payment-service v2.3.1 → v2.3.2 deployed
  - 14:52–15:08 — connection pool exhaustion → 80-90% error rate on checkout/payment
  - 15:09       — pod manually restarted, errors resolve
  - Downstream: notification-service + inventory-service cascade failures
"""

import os
import random
import uuid
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from elasticsearch import Elasticsearch, helpers

load_dotenv()

ES_URL = os.getenv("ES_URL", "http://localhost:9200")
ES_API_KEY = os.getenv("ES_API_KEY")

SERVICES = ["auth-service", "payment-service", "product-service",
            "checkout-service", "notification-service", "inventory-service"]

ENDPOINTS = {
    "auth-service":         ["/api/auth/login", "/api/auth/logout", "/api/auth/refresh"],
    "payment-service":      ["/api/payment/process", "/api/payment/refund", "/api/payment/status"],
    "product-service":      ["/api/products", "/api/products/{id}", "/api/products/search"],
    "checkout-service":     ["/api/checkout/initiate", "/api/checkout/confirm", "/api/checkout/cancel"],
    "notification-service": ["/api/notify/email", "/api/notify/sms", "/api/notify/push"],
    "inventory-service":    ["/api/inventory/reserve", "/api/inventory/release", "/api/inventory/check"],
}

ERROR_MESSAGES = {
    "payment-service": [
        "Connection pool exhausted: no connections available",
        "Timeout waiting for connection from pool",
        "JDBC connection timeout after 5000ms",
        "HikariPool-1 - Connection is not available, request timed out after 30000ms",
    ],
    "notification-service": [
        "Upstream payment-service unavailable",
        "Failed to send payment confirmation: payment status unknown",
        "Circuit breaker OPEN for payment-service",
    ],
    "inventory-service": [
        "Cannot confirm reservation: payment not confirmed",
        "Saga rollback triggered: payment step failed",
        "Inventory reservation timed out waiting for payment confirmation",
    ],
}

NORMAL_ERROR_MESSAGES = [
    "Request validation failed: missing required field",
    "Rate limit exceeded for user",
    "Invalid authentication token",
    "Resource not found",
]

INCIDENT_START = datetime(2026, 6, 9, 14, 52, 0, tzinfo=timezone.utc)
INCIDENT_END   = datetime(2026, 6, 9, 15,  9, 0, tzinfo=timezone.utc)
DEPLOY_TIME    = datetime(2026, 6, 9, 14, 51, 0, tzinfo=timezone.utc)
DATA_START     = datetime(2026, 6, 9, 12,  0, 0, tzinfo=timezone.utc)
DATA_END       = datetime(2026, 6, 9, 16,  0, 0, tzinfo=timezone.utc)

NUM_USERS = 10_000
USER_IDS  = [f"user_{i:05d}" for i in range(NUM_USERS)]

PAYMENT_POD_OLD = "payment-service-v2-3-1-d4f9b2"
PAYMENT_POD_NEW = "payment-service-v2-3-2-a8c7e1"


def es_client() -> Elasticsearch:
    if ES_API_KEY:
        return Elasticsearch(ES_URL, api_key=ES_API_KEY)
    return Elasticsearch(ES_URL)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _during_incident(dt: datetime) -> bool:
    return INCIDENT_START <= dt < INCIDENT_END


def _incident_severity(dt: datetime) -> float:
    """Returns error probability 0–1 during incident window."""
    if not _during_incident(dt):
        return 0.02  # normal ~2% error rate
    elapsed = (dt - INCIDENT_START).total_seconds()
    # Ramps up fast, stays high, resolves at pod restart
    return min(0.88, 0.3 + elapsed / 60 * 0.1)


def generate_app_logs():
    """Yields log documents for app-logs-* index."""
    dt = DATA_START
    while dt < DATA_END:
        interval = timedelta(seconds=random.uniform(0.05, 0.3))
        dt += interval

        service  = random.choice(SERVICES)
        endpoint = random.choice(ENDPOINTS[service])
        user_id  = random.choice(USER_IDS)
        req_id   = str(uuid.uuid4())
        error_p  = _incident_severity(dt)

        if service == "payment-service" and _during_incident(dt):
            pod = PAYMENT_POD_NEW
        elif service == "payment-service":
            pod = PAYMENT_POD_OLD
        else:
            pod = f"{service}-{random.randint(1,3)}"

        is_error = random.random() < error_p and service in (
            "payment-service", "checkout-service"
        )

        # Cascade: notification + inventory fail when payment is broken
        if service in ("notification-service", "inventory-service") and _during_incident(dt):
            is_error = random.random() < 0.6

        if is_error:
            msgs = ERROR_MESSAGES.get(service, NORMAL_ERROR_MESSAGES)
            status = random.choice([500, 502, 503, 504])
            level  = "ERROR"
            duration = random.randint(3000, 8000)
            msg    = random.choice(msgs)
        else:
            status   = 200
            level    = "INFO"
            duration = random.randint(20, 350)
            msg      = f"Request completed successfully"

        yield {
            "_index": "app-logs",
            "_source": {
                "timestamp":   _ts(dt),
                "service":     service,
                "level":       level,
                "message":     msg,
                "request_id":  req_id,
                "user_id":     user_id,
                "endpoint":    endpoint,
                "status_code": status,
                "duration_ms": duration,
                "pod":         pod,
                "version":     "2.3.2" if (service == "payment-service" and _during_incident(dt)) else "2.3.1",
            },
        }


def generate_transactions():
    """Yields transaction documents (for blast radius calculation)."""
    dt = DATA_START
    while dt < DATA_END:
        dt += timedelta(seconds=random.uniform(1, 5))
        user_id = random.choice(USER_IDS)
        amount  = round(random.uniform(10, 500), 2)
        req_id  = str(uuid.uuid4())
        txn_id  = f"txn_{uuid.uuid4().hex[:12]}"

        if _during_incident(dt):
            status     = "failed" if random.random() < 0.87 else "success"
            error_code = "CONNECTION_POOL_EXHAUSTED" if status == "failed" else None
        else:
            status     = "success" if random.random() < 0.98 else "failed"
            error_code = "GENERIC_ERROR" if status == "failed" else None

        yield {
            "_index": "transactions",
            "_source": {
                "timestamp":      _ts(dt),
                "transaction_id": txn_id,
                "user_id":        user_id,
                "amount":         amount,
                "status":         status,
                "service":        "payment-service",
                "request_id":     req_id,
                "error_code":     error_code,
            },
        }


def generate_metrics():
    """Yields per-minute aggregated service metrics."""
    dt = DATA_START
    while dt < DATA_END:
        for service in SERVICES:
            error_p = _incident_severity(dt)
            if service not in ("payment-service", "checkout-service", "notification-service", "inventory-service"):
                error_p = 0.02

            base_rps = {"payment-service": 120, "checkout-service": 80,
                        "notification-service": 60, "inventory-service": 55,
                        "auth-service": 200, "product-service": 300}.get(service, 100)

            error_rate   = error_p + random.uniform(-0.01, 0.01)
            avg_latency  = 180 + error_p * 4000 + random.randint(-20, 20)
            pool_usage   = 0.3 + error_p * 0.68 if service == "payment-service" else random.uniform(0.1, 0.4)

            if service == "payment-service" and _during_incident(dt):
                pod = PAYMENT_POD_NEW
            elif service == "payment-service":
                pod = PAYMENT_POD_OLD
            else:
                pod = f"{service}-1"

            yield {
                "_index": "metrics",
                "_source": {
                    "timestamp":            _ts(dt),
                    "service":              service,
                    "error_rate":           round(max(0, min(1, error_rate)), 4),
                    "avg_latency_ms":       round(avg_latency),
                    "request_count":        int(base_rps * 60 * random.uniform(0.8, 1.2)),
                    "connection_pool_usage": round(max(0, min(1, pool_usage)), 4),
                    "pod":                  pod,
                },
            }
        dt += timedelta(minutes=1)


def generate_deployments():
    """Yields deployment events."""
    events = [
        {
            "timestamp":    _ts(DEPLOY_TIME),
            "service":      "payment-service",
            "from_version": "2.3.1",
            "to_version":   "2.3.2",
            "deployed_by":  "ci-pipeline",
            "pod":          PAYMENT_POD_NEW,
            "environment":  "production",
            "commit_sha":   "a8c7e1f",
            "changelog":    "Refactor connection pool config; bump HikariCP version",
        },
        {
            "timestamp":    _ts(datetime(2026, 6, 9, 15, 9, 0, tzinfo=timezone.utc)),
            "service":      "payment-service",
            "event_type":   "pod_restart",
            "pod":          PAYMENT_POD_NEW,
            "triggered_by": "sre-oncall",
            "reason":       "Manual restart to resolve connection pool exhaustion",
            "environment":  "production",
        },
        {
            "timestamp":    _ts(datetime(2026, 6, 9, 12, 30, 0, tzinfo=timezone.utc)),
            "service":      "product-service",
            "from_version": "1.8.4",
            "to_version":   "1.8.5",
            "deployed_by":  "ci-pipeline",
            "environment":  "production",
            "changelog":    "Fix product image URL generation",
        },
    ]
    for e in events:
        yield {"_index": "deployments", "_source": e}


def create_index_mappings(es: Elasticsearch):
    indices = {
        "app-logs": {
            "mappings": {
                "properties": {
                    "timestamp":   {"type": "date"},
                    "service":     {"type": "keyword"},
                    "level":       {"type": "keyword"},
                    "message":     {"type": "text"},
                    "request_id":  {"type": "keyword"},
                    "user_id":     {"type": "keyword"},
                    "endpoint":    {"type": "keyword"},
                    "status_code": {"type": "integer"},
                    "duration_ms": {"type": "integer"},
                    "pod":         {"type": "keyword"},
                    "version":     {"type": "keyword"},
                }
            }
        },
        "transactions": {
            "mappings": {
                "properties": {
                    "timestamp":      {"type": "date"},
                    "transaction_id": {"type": "keyword"},
                    "user_id":        {"type": "keyword"},
                    "amount":         {"type": "float"},
                    "status":         {"type": "keyword"},
                    "service":        {"type": "keyword"},
                    "request_id":     {"type": "keyword"},
                    "error_code":     {"type": "keyword"},
                }
            }
        },
        "metrics": {
            "mappings": {
                "properties": {
                    "timestamp":             {"type": "date"},
                    "service":               {"type": "keyword"},
                    "error_rate":            {"type": "float"},
                    "avg_latency_ms":        {"type": "float"},
                    "request_count":         {"type": "integer"},
                    "connection_pool_usage": {"type": "float"},
                    "pod":                   {"type": "keyword"},
                }
            }
        },
        "deployments": {
            "mappings": {
                "properties": {
                    "timestamp":    {"type": "date"},
                    "service":      {"type": "keyword"},
                    "from_version": {"type": "keyword"},
                    "to_version":   {"type": "keyword"},
                    "deployed_by":  {"type": "keyword"},
                    "pod":          {"type": "keyword"},
                    "environment":  {"type": "keyword"},
                    "event_type":   {"type": "keyword"},
                    "triggered_by": {"type": "keyword"},
                    "reason":       {"type": "text"},
                    "changelog":    {"type": "text"},
                }
            }
        },
    }
    for name, body in indices.items():
        try:
            if es.indices.exists(index=name).body:
                es.indices.delete(index=name)
        except Exception:
            pass
        try:
            es.indices.create(index=name, mappings=body["mappings"])
            print(f"Created index: {name}")
        except Exception as e:
            # Serverless auto-creates indices on first ingest — safe to continue
            print(f"Index {name} will be auto-created on ingest ({e})")


def ingest(es: Elasticsearch):
    print("Generating and ingesting synthetic data (this takes ~1 min)...")

    generators = [
        ("app-logs",     generate_app_logs()),
        ("transactions", generate_transactions()),
        ("metrics",      generate_metrics()),
        ("deployments",  generate_deployments()),
    ]

    for label, gen in generators:
        count, _ = helpers.bulk(es, gen, chunk_size=500, request_timeout=60)
        print(f"  {label}: {count:,} documents ingested")

    es.indices.refresh(index="app-logs,transactions,metrics,deployments")
    print("Done. All indices refreshed.")


if __name__ == "__main__":
    es = es_client()
    create_index_mappings(es)
    ingest(es)
