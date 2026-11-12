"""Fake online shop. Generates alerts, logs, metrics and deployments for an outage.

The detective never reads the "expected_root" value; it is only returned so YOU can
check whether the detective got the right answer.
"""
import random
import time

from .db import get_conn, reset_incident_data

SERVICES = ["checkout", "payment", "inventory", "database"]

# "X depends on Y" means: if Y breaks, X breaks too.
DEPENDS_ON = {
    "checkout": ["payment", "inventory"],
    "payment": ["database"],
    "inventory": ["database"],
    "database": [],
}

# Normal (healthy) metric values.
BASELINE = {"latency_ms": 120, "error_rate": 0.5, "cpu": 35, "memory": 50, "disk": 40}

SCENARIOS = {
    "slow_query": {
        "title": "Bad database migration drops an index",
        "expected_root": "database",
        "deploy": {
            "service": "database",
            "version": "migration-042",
            "description": "Migration 042: dropped index on payments.user_id",
        },
        "affected": {
            "database": {
                "delay": 0,
                "metrics": {"latency_ms": 2500, "cpu": 92},
                "alerts": [
                    "database query latency high",
                    "database CPU above 90%",
                    "database connection pool exhausted",
                ],
                "logs": [
                    ("WARN", "slow query: SELECT * FROM payments WHERE user_id=? took 2400ms (seq scan)"),
                    ("ERROR", "connection pool exhausted: 100/100 connections in use"),
                ],
            },
            "payment": {
                "delay": 60,
                "metrics": {"latency_ms": 5000, "error_rate": 45},
                "alerts": [
                    "payment error rate high",
                    "payment latency high",
                    "payment timeout waiting for database",
                ],
                "logs": [
                    ("ERROR", "timeout waiting for database connection after 5000ms"),
                    ("ERROR", "payment failed: upstream database timeout"),
                ],
            },
            "inventory": {
                "delay": 90,
                "metrics": {"latency_ms": 3500, "error_rate": 20},
                "alerts": ["inventory latency high", "inventory timeout waiting for database"],
                "logs": [("ERROR", "stock lookup failed: database timeout")],
            },
            "checkout": {
                "delay": 120,
                "metrics": {"latency_ms": 6000, "error_rate": 55},
                "alerts": [
                    "checkout error rate high",
                    "checkout latency high",
                    "payment failures at checkout",
                ],
                "logs": [("ERROR", "checkout failed: payment service returned 504")],
            },
        },
    },
    "memory_leak": {
        "title": "Payment release leaks memory",
        "expected_root": "payment",
        "deploy": {
            "service": "payment",
            "version": "v3.2.0",
            "description": "Added in-memory cache for payment tokens",
        },
        "affected": {
            "payment": {
                "delay": 0,
                "metrics": {"memory": 97, "latency_ms": 3000, "cpu": 80},
                "alerts": [
                    "payment memory usage above 95%",
                    "payment pod restarting (OOMKilled)",
                    "payment latency high",
                ],
                "logs": [
                    ("WARN", "heap usage at 96%, garbage collection running constantly"),
                    ("ERROR", "OutOfMemoryError: heap space exhausted"),
                    ("INFO", "container restarted: OOMKilled"),
                ],
            },
            "checkout": {
                "delay": 90,
                "metrics": {"latency_ms": 4000, "error_rate": 40},
                "alerts": ["checkout error rate high", "payment failures at checkout"],
                "logs": [("ERROR", "checkout failed: payment service unavailable")],
            },
        },
    },
    "disk_full": {
        "title": "Database disk fills up (no deployment involved)",
        "expected_root": "database",
        "deploy": None,
        "affected": {
            "database": {
                "delay": 0,
                "metrics": {"disk": 99, "latency_ms": 1500, "error_rate": 30},
                "alerts": [
                    "database disk usage above 95%",
                    "database write errors",
                    "database replication lag high",
                ],
                "logs": [
                    ("ERROR", "could not extend file: No space left on device"),
                    ("ERROR", "WAL write failed: disk full"),
                ],
            },
            "payment": {
                "delay": 45,
                "metrics": {"error_rate": 50, "latency_ms": 2500},
                "alerts": ["payment error rate high", "payment write failed"],
                "logs": [("ERROR", "payment failed: database write error")],
            },
            "inventory": {
                "delay": 60,
                "metrics": {"error_rate": 35, "latency_ms": 2000},
                "alerts": ["inventory update failed"],
                "logs": [("ERROR", "stock update failed: database is read-only")],
            },
            "checkout": {
                "delay": 90,
                "metrics": {"error_rate": 45, "latency_ms": 4500},
                "alerts": ["checkout error rate high", "payment failures at checkout"],
                "logs": [("ERROR", "checkout failed: payment service returned 500")],
            },
        },
    },
}

# Old, unrelated releases. A good detective must NOT blame these.
DECOY_DEPLOYS = [
    (-5 * 3600, "checkout", "v5.1.0", "Updated checkout page button colors"),
    (-3 * 3600, "inventory", "v1.8.2", "Fixed typo in stock labels"),
]

HEALTHY_LOGS = {
    "checkout": "GET /cart 200 in 110ms",
    "payment": "POST /pay 200 in 130ms",
    "inventory": "GET /stock 200 in 90ms",
    "database": "checkpoint complete, 0 slow queries",
}


def run_scenario(name: str) -> dict:
    sc = SCENARIOS[name]
    rng = random.Random(7)  # fixed seed so demos are repeatable
    reset_incident_data()

    now = time.time()
    start = now - 10 * 60  # the incident started 10 minutes ago
    conn = get_conn()
    cur = conn.cursor()

    # --- deployments (decoys + the real one) ---
    for offset, service, version, desc in DECOY_DEPLOYS:
        cur.execute(
            "INSERT INTO deployments (ts, service, version, description) VALUES (?,?,?,?)",
            (now + offset, service, version, desc),
        )
    if sc["deploy"]:
        d = sc["deploy"]
        cur.execute(
            "INSERT INTO deployments (ts, service, version, description) VALUES (?,?,?,?)",
            (start - 3 * 60, d["service"], d["version"], d["description"]),
        )

    # --- metrics: one point per minute per service for the last 40 minutes ---
    for minute in range(-40, 1):
        t = now + minute * 60
        for service in SERVICES:
            bad = sc["affected"].get(service)
            for metric, base in BASELINE.items():
                value = base * rng.uniform(0.9, 1.1)
                if bad and metric in bad["metrics"] and t >= start + bad["delay"]:
                    ramp = min(1.0, (t - (start + bad["delay"])) / 120)
                    value = base + (bad["metrics"][metric] - base) * ramp
                    value *= rng.uniform(0.95, 1.05)
                cur.execute(
                    "INSERT INTO metrics (ts, service, name, value) VALUES (?,?,?,?)",
                    (t, service, metric, round(value, 1)),
                )

    # --- healthy logs before the incident ---
    t = now - 40 * 60
    while t < start:
        for service in SERVICES:
            cur.execute(
                "INSERT INTO logs (ts, service, level, message) VALUES (?,?,?,?)",
                (t, service, "INFO", HEALTHY_LOGS[service]),
            )
        t += rng.uniform(20, 40)

    # --- incident logs and alerts ---
    for service, info in sc["affected"].items():
        t_begin = start + info["delay"]

        t = t_begin
        while t < now:
            level, msg = rng.choice(info["logs"])
            cur.execute(
                "INSERT INTO logs (ts, service, level, message) VALUES (?,?,?,?)",
                (t, service, level, msg + f" (req-{rng.randint(1000, 9999)})"),
            )
            t += rng.uniform(4, 9)

        for template in info["alerts"]:
            t = t_begin + rng.uniform(0, 10)
            while t < now:
                severity = "critical" if rng.random() < 0.6 else "warning"
                cur.execute(
                    "INSERT INTO alerts (ts, service, severity, message) VALUES (?,?,?,?)",
                    (t, service, severity, f"{template} (host-{rng.randint(1, 4)})"),
                )
                t += rng.uniform(15, 35)

    conn.commit()
    n_alerts = cur.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    conn.close()
    return {
        "scenario": name,
        "title": sc["title"],
        "alerts_generated": n_alerts,
        "incident_started_at": start,
        "expected_root": sc["expected_root"],  # for your self-testing only
    }