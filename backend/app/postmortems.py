"""Memory of past incidents. Finds the most similar ones using TF-IDF."""
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .db import get_conn

SEED = [
    (
        "Checkout outage after index migration",
        "database query latency high slow query sequential scan connection pool exhausted payment timeout waiting for database",
        "A migration dropped an index, so queries scanned the whole table.",
        "Roll back the migration or re-create the index (CREATE INDEX CONCURRENTLY). Add index checks to migration review.",
    ),
    (
        "Payment pods restarting after release",
        "payment memory usage high OOMKilled OutOfMemoryError heap space exhausted garbage collection latency high",
        "A new in-memory cache never evicted entries, so memory grew until the pod was killed.",
        "Roll back the release, then add a size limit and eviction policy to the cache. Add a memory alert at 80%.",
    ),
    (
        "Database stopped accepting writes",
        "database disk usage above 95% No space left on device WAL write failed disk full write errors read-only",
        "Logs and old WAL files filled the disk because cleanup jobs had stopped.",
        "Free space by removing old WAL/logs, restart the cleanup job, and alert on disk at 80%.",
    ),
    (
        "Login failures from expired TLS certificate",
        "TLS handshake failed certificate expired SSL error login failures 502",
        "A certificate expired because auto-renewal silently failed.",
        "Renew the certificate and fix the renewal job. Add a 30-day expiry alert.",
    ),
    (
        "Slow pages caused by cache stampede",
        "redis cache miss spike database CPU high latency high after cache flush",
        "A cache flush made every request hit the database at once.",
        "Warm the cache gradually and add request coalescing for hot keys.",
    ),
    (
        "Intermittent timeouts from DNS failure",
        "DNS resolution failed upstream host not found timeout intermittent errors",
        "The DNS provider had a regional outage.",
        "Fail over to the secondary resolver and cache DNS results locally.",
    ),
]


def seed_postmortems():
    conn = get_conn()
    if conn.execute("SELECT COUNT(*) FROM postmortems").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO postmortems (title, symptoms, root_cause, fix) VALUES (?,?,?,?)", SEED
        )
        conn.commit()
    conn.close()


def find_similar(query: str, k: int = 2, min_score: float = 0.2) -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM postmortems")]
    conn.close()
    if not rows:
        return []

    docs = [r["symptoms"] + " " + r["title"] for r in rows]
    vec = TfidfVectorizer(stop_words="english")
    matrix = vec.fit_transform(docs + [query])
    sims = cosine_similarity(matrix[-1], matrix[:-1])[0]

    ranked = sorted(zip(sims, rows), key=lambda x: x[0], reverse=True)
    return [
        {
            "title": r["title"],
            "similarity": round(float(s), 2),
            "root_cause": r["root_cause"],
            "fix": r["fix"],
        }
        for s, r in ranked[:k]
        if s >= min_score
    ]