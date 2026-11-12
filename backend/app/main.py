from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .db import get_conn, init_db
from .detective import investigate
from .llm import summarize
from .postmortems import find_similar, seed_postmortems
from .simulator import DEPENDS_ON, SCENARIOS, SERVICES, run_scenario

app = FastAPI(title="SRE Incident Investigator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    seed_postmortems()


@app.get("/api/scenarios")
def scenarios():
    return [{"id": k, "title": v["title"]} for k, v in SCENARIOS.items()]


@app.get("/api/graph")
def graph():
    return {
        "nodes": SERVICES,
        "edges": [{"from": a, "to": b} for a, deps in DEPENDS_ON.items() for b in deps],
    }


@app.post("/api/simulate/{scenario}")
def simulate(scenario: str):
    if scenario not in SCENARIOS:
        raise HTTPException(404, "Unknown scenario")
    return run_scenario(scenario)


@app.get("/api/alerts")
def alerts(limit: int = 50):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/metrics/{service}")
def metrics(service: str, name: str = "latency_ms"):
    conn = get_conn()
    rows = conn.execute(
        "SELECT ts, value FROM metrics WHERE service=? AND name=? ORDER BY ts", (service, name)
    ).fetchall()
    conn.close()
    return [{"ts": r["ts"], "value": r["value"]} for r in rows]


@app.post("/api/investigate")
def run_investigation():
    result = investigate()
    if result["status"] != "incident":
        return result

    rc = result["root_cause"]
    query = " ".join(
        [rc["service"]]
        + [c["sample"] for c in result["clusters"]]
        + [log["message"] for log in rc["logs"]]
        + ([rc["deployment"]["description"]] if rc["deployment"] else [])
    )
    similar = find_similar(query)
    result["similar_incidents"] = similar
    result["summary"] = summarize(result, similar)
    return result