"""Shared pytest fixtures for Darwin SRE test suite."""
import json
import pytest
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

# ---------------------------------------------------------------------------
# Sample data fixtures (in-memory, no file I/O required for unit tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_incident():
    """Generic incident fixture used by RAG, degradation, and other unit tests."""
    return {
        "id": "inc_unit_001",
        "title": "Redis cache miss rate spiking to 90%",
        "description": "The checkout-service is experiencing cache miss rates of 90%+. Response times are degrading.",
        "service": "checkout-service",
        "environment": "production",
        "category": "cache",
        "is_edge_case": False,
        "edge_case_family": None,
        "log_id": "log_unit_001",
        "kb_refs": [],
        "metrics": {"cache_miss_rate": 0.90, "p99_latency_ms": 3200},
        "ground_truth": {
            "root_cause": "Redis keyspace eviction policy set to allkeys-lru; memory limit hit during traffic spike",
            "severity": "P2",
            "remediation_steps": ["Increase Redis maxmemory", "Switch eviction to volatile-lru", "Flush stale keys"],
        },
    }


@pytest.fixture
def sample_skill():
    return {
        "id": "skill_001",
        "name": "Cache Eviction Diagnosis",
        "guidance": "When cache miss rates spike, check eviction policy and memory limits before assuming network issues.",
        "tags": ["cache", "redis", "CCF-3"],
        "active": True,
        "created_by_generation": 1,
        "use_count": 2,
    }


@pytest.fixture
def sample_normal_incident():
    return {
        "id": "train_001",
        "title": "PostgreSQL connection pool exhausted",
        "description": "The payment-service is throwing connection timeout errors. DB connection pool is at 100% utilization. Queries are queuing up.",
        "service": "payment-service",
        "environment": "production",
        "category": "database",
        "is_edge_case": False,
        "edge_case_family": None,
        "log_id": "log_train_001",
        "kb_refs": ["kb_001", "kb_002"],
        "metrics": {"db_connections": 100, "query_latency_ms": 4500},
        "ground_truth": {
            "root_cause": "Connection pool size set to 100; traffic spike from flash sale event exhausted all connections",
            "severity": "P1",
            "remediation_steps": [
                "Increase max_connections in postgresql.conf",
                "Restart pgbouncer to apply pool settings",
                "Add circuit breaker in payment-service DB client",
            ],
        },
    }


@pytest.fixture
def sample_edge_case_incident():
    return {
        "id": "prod_031",
        "title": "auth-api returning 503 errors — database unreachable",
        "description": "Auth service logs show DB connection failures. Users cannot log in. Metrics show DB CPU at 2%.",
        "service": "auth-api",
        "environment": "production",
        "category": "database",
        "is_edge_case": True,
        "edge_case_family": "CCF-1",
        "log_id": "log_prod_031",
        "kb_refs": [],
        "metrics": {"auth_error_rate": 0.94, "db_cpu": 0.02, "upstream_latency_ms": 8200},
        "ground_truth": {
            "root_cause": "Upstream user-directory-service is timing out; auth-api depends on it for session hydration, not the DB",
            "severity": "P1",
            "remediation_steps": [
                "Check user-directory-service health endpoint",
                "Add timeout + fallback in auth-api upstream call",
                "Escalate to user-directory team",
            ],
        },
    }


@pytest.fixture
def sample_log():
    return {
        "id": "log_train_001",
        "incident_id": "train_001",
        "lines": [
            {"ts": "2026-06-27T14:00:01Z", "level": "WARN", "msg": "DB pool at 80% capacity", "service": "payment-service"},
            {"ts": "2026-06-27T14:00:15Z", "level": "ERROR", "msg": "Connection timeout after 30s", "service": "payment-service"},
            {"ts": "2026-06-27T14:00:20Z", "level": "ERROR", "msg": "Pool exhausted — rejecting request", "service": "payment-service"},
        ],
        "summary": "Connection pool exhaustion leading to request rejection",
    }


@pytest.fixture
def sample_kb_article():
    return {
        "id": "kb_001",
        "title": "Runbook: PostgreSQL connection pool exhaustion",
        "body": "Connection pool exhaustion occurs when all available DB connections are in use. Diagnose with SELECT count(*) FROM pg_stat_activity. Increase max_connections in postgresql.conf and restart. Add pgbouncer for connection pooling. Set pool_size = max_connections * 0.8 as a safe ceiling.",
        "service": "general",
        "tags": ["database", "postgresql", "connection-pool"],
        "source": "seed",
        "created_by_generation": None,
    }


@pytest.fixture
def sample_resolution():
    return {
        "root_cause": "Connection pool exhausted due to traffic spike",
        "severity": "P2",
        "remediation_steps": ["Increase pool size", "Add pgbouncer"],
        "estimated_resolution_minutes": 15,
        "confidence": 0.85,
    }


@pytest.fixture
def loaded_training_data():
    """Load real generated training data if it exists, else return empty list."""
    path = DATA_DIR / "incidents_training.json"
    if path.exists():
        return json.loads(path.read_text())
    return []


@pytest.fixture
def loaded_production_data():
    path = DATA_DIR / "incidents_production.json"
    if path.exists():
        return json.loads(path.read_text())
    return []


@pytest.fixture
def loaded_logs():
    path = DATA_DIR / "logs.json"
    if path.exists():
        return json.loads(path.read_text())
    return []


@pytest.fixture
def loaded_kb():
    path = DATA_DIR / "knowledge_base.json"
    if path.exists():
        return json.loads(path.read_text())
    return []
