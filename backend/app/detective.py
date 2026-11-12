"""The detective: groups alerts, finds what changed, separates cause from symptom."""
import re
import statistics
import time
from collections import Counter, defaultdict

import networkx as nx
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer

from .db import get_conn
from .simulator import DEPENDS_ON, SERVICES

# ---------- helpers ----------


def normalize(msg: str) -> str:
    """Remove noise like (host-3) and (req-1234) so identical alerts match."""
    msg = re.sub(r"\((host|req)-\d+\)", "", msg)
    return re.sub(r"\s+", " ", msg).strip()


def build_graph() -> nx.DiGraph:
    """Edge A -> B means 'A depends on B'."""
    g = nx.DiGraph()
    g.add_nodes_from(SERVICES)
    for svc, deps in DEPENDS_ON.items():
        for d in deps:
            g.add_edge(svc, d)
    return g


# ---------- step 1: alert clustering ----------


def cluster_alerts(alerts: list[dict]) -> list[dict]:
    """Turn hundreds of alerts into a few groups of similar alerts."""
    if not alerts:
        return []

    texts = [f"{a['service']} {normalize(a['message'])}" for a in alerts]
    unique = sorted(set(texts))

    if len(unique) == 1:
        labels = [0]
    else:
        vec = TfidfVectorizer().fit_transform(unique).toarray()
        model = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=0.75,
            metric="cosine",
            linkage="average",
        )
        labels = model.fit_predict(vec)
    label_of = dict(zip(unique, labels))

    groups = defaultdict(list)
    for a, t in zip(alerts, texts):
        groups[label_of[t]].append(a)

    clusters = []
    for items in groups.values():
        services = sorted({a["service"] for a in items})
        sample = Counter(normalize(a["message"]) for a in items).most_common(1)[0][0]
        clusters.append(
            {
                "count": len(items),
                "services": services,
                "first_ts": min(a["ts"] for a in items),
                "sample": sample,
            }
        )
    clusters.sort(key=lambda c: c["first_ts"])
    return clusters
# ---------- step 2: find which services look unhealthy ----------


def service_anomalies(now: float) -> dict:
    """Compare each metric to its own normal level; find when it first went bad."""
    conn = get_conn()
    rows = conn.execute("SELECT ts, service, name, value FROM metrics ORDER BY ts").fetchall()
    conn.close()

    series = defaultdict(list)
    for r in rows:
        series[(r["service"], r["name"])].append((r["ts"], r["value"]))

    result = {}
    for (service, name), points in series.items():
        # the first 20 minutes of data = "normal"
        cutoff = points[0][0] + 20 * 60
        normal = [v for t, v in points if t < cutoff]
        if len(normal) < 5:
            continue
        mean = statistics.mean(normal)
        std = statistics.pstdev(normal)
        limit = max(4 * std, 0.3 * mean)

        onset = None
        streak = 0
        for t, v in points:
            if t >= cutoff and abs(v - mean) > limit:
                streak += 1
                if streak == 2 and onset is None:  # 2 bad points in a row
                    onset = t - 60
            else:
                streak = 0
        if onset is None:
            continue

        peak = max((v for t, v in points if t >= cutoff), key=lambda v: abs(v - mean))
        entry = result.setdefault(service, {"onset": onset, "metrics": []})
        entry["onset"] = min(entry["onset"], onset)
        entry["metrics"].append(
            {"name": name, "normal": round(mean, 1), "peak": round(peak, 1)}
        )
    return result


# ---------- step 3: rank suspects ----------


def rank_causes(anomalies: dict, deployments: list[dict]) -> list[dict]:
    g = build_graph()
    unhealthy = set(anomalies)
    if not unhealthy:
        return []

    onsets = [a["onset"] for a in anomalies.values()]
    first, last = min(onsets), max(onsets)
    spread = max(last - first, 1)

    scored = []
    for svc in unhealthy:
        info = anomalies[svc]

        # 1. How many other broken services depend on me (directly or indirectly)?
        dependents = {n for n in nx.ancestors(g, svc) if n in unhealthy}
        upstream = len(dependents) / max(len(unhealthy) - 1, 1)

        # 2. Did I go bad first?
        earliness = 1 - (info["onset"] - first) / spread

        # 3. Was I changed (deployed) shortly before I went bad?
        change = 0.0
        recent_deploy = None
        for d in deployments:
            gap = info["onset"] - d["ts"]
            if d["service"] == svc and 0 <= gap <= 3600:
                s = 1 - gap / 3600
                if s > change:
                    change, recent_deploy = s, d

        # 4. How badly did my metrics move?
        severity = min(len(info["metrics"]) / 3, 1.0)

        score = 0.35 * upstream + 0.25 * earliness + 0.30 * change + 0.10 * severity

        # A service that depends on another broken service is probably a victim.
        broken_deps = {n for n in nx.descendants(g, svc) if n in unhealthy}
        if broken_deps:
            score *= 0.3

        scored.append(
            {
                "service": svc,
                "score": score,
                "onset": info["onset"],
                "metrics": info["metrics"],
                "deployment": recent_deploy,
                "victim_of": sorted(broken_deps),
                "breakdown": {
                    "upstream": round(upstream, 2),
                    "earliness": round(earliness, 2),
                    "change": round(change, 2),
                    "severity": round(severity, 2),
                },
            }
        )

    total = sum(s["score"] for s in scored) or 1
    for s in scored:
        s["confidence"] = round(s["score"] / total, 2)
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored


# ---------- step 4: gather proof ----------


def top_log_lines(service: str, since: float, limit: int = 4) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT ts, level, message FROM logs WHERE service=? AND ts>=? AND level IN ('ERROR','WARN')",
        (service, since),
    ).fetchall()
    conn.close()
    counter = Counter((r["level"], normalize(r["message"])) for r in rows)
    first_seen = {}
    for r in rows:
        key = (r["level"], normalize(r["message"]))
        first_seen.setdefault(key, r["ts"])
    return [
        {"level": lvl, "message": msg, "count": n, "first_ts": first_seen[(lvl, msg)]}
        for (lvl, msg), n in counter.most_common(limit)
    ]


def investigate() -> dict:
    now = time.time()
    conn = get_conn()
    alerts = [dict(r) for r in conn.execute("SELECT * FROM alerts ORDER BY ts")]
    deployments = [dict(r) for r in conn.execute("SELECT * FROM deployments ORDER BY ts")]
    conn.close()

    if not alerts:
        return {"status": "healthy", "message": "No alerts found. Trigger an outage first."}

    clusters = cluster_alerts(alerts)
    anomalies = service_anomalies(now)
    ranking = rank_causes(anomalies, deployments)
    if not ranking:
        return {"status": "unknown", "message": "Alerts exist but no metric anomaly was found."}

    top = ranking[0]
    evidence_logs = top_log_lines(top["service"], top["onset"] - 60)

    root_alerts = sum(1 for a in alerts if a["service"] == top["service"])
    timeline = sorted(
        [{"service": s, "onset": a["onset"]} for s, a in anomalies.items()],
        key=lambda x: x["onset"],
    )
    all_deploys = [
        {**d, "minutes_before_incident": round((top["onset"] - d["ts"]) / 60, 1)} for d in deployments
    ]

    return {
        "status": "incident",
        "stats": {
            "raw_alerts": len(alerts),
            "alert_groups": len(clusters),
            "symptom_alerts": len(alerts) - root_alerts,
        },
        "root_cause": {
            "service": top["service"],
            "confidence": top["confidence"],
            "deployment": top["deployment"],
            "metrics": top["metrics"],
            "logs": evidence_logs,
            "score_breakdown": top["breakdown"],
        },
        "ranking": [
            {
                "service": r["service"],
                "confidence": r["confidence"],
                "role": "likely cause" if i == 0 else "symptom",
                "reason": (
                    "went bad first and other broken services depend on it"
                    if i == 0
                    else f"depends on broken service(s): {', '.join(r['victim_of'])}"
                    if r["victim_of"]
                    else "unhealthy but not clearly upstream of the others"
                ),
            }
            for i, r in enumerate(ranking)
        ],
        "timeline": timeline,
        "deployments": all_deploys,
        "clusters": clusters,
    }