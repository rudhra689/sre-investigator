\# SRE Incident Investigator



A web dashboard that investigates a simulated online-shop outage and names the most likely root cause, with proof.







\## The problem



When a shop breaks at 2 AM, an on-call engineer faces hundreds of alerts at once. Most are symptoms, not causes. The evidence is spread across alerting, logging, metrics and deployment tools, and past fixes live in old documents or in a senior engineer's head.



This project puts that evidence in one place and answers: \*\*which service broke first, what changed, and what fixed it last time.\*\*



\## What it does



1\. \*\*Simulates a shop outage.\*\* Four services (`checkout`, `payment`, `inventory`, `database`) and three scenarios: a bad database migration, a memory leak after a release, and a full database disk (with no deployment involved).

2\. \*\*Groups alerts.\*\* TF-IDF plus agglomerative clustering turns 241 raw alerts into 4 groups.

3\. \*\*Finds the cause.\*\* It ranks services using four signals:



&#x20;  | Signal | Weight | Meaning |

&#x20;  | --- | --- | --- |

&#x20;  | upstream | 0.35 | other broken services depend on this one |

&#x20;  | earliness | 0.25 | it went bad first |

&#x20;  | change | 0.30 | it was deployed shortly before it went bad |

&#x20;  | severity | 0.10 | several of its metrics moved |



&#x20;  A service that depends on another broken service is treated as a likely victim and its score is reduced.

4\. \*\*Searches past postmortems\*\* with TF-IDF similarity and suggests the fix that worked before.

5\. \*\*Explains it in plain language\*\* with the Claude API, using only the detective's evidence. If no API key is set, a template summary is used.



\## Result



`python test\_scenarios.py` runs all three scenarios and compares the detective's answer with the true root cause:



```

slow\_query   expected=database  got=database  PASS

memory\_leak  expected=payment   got=payment   PASS

disk\_full    expected=database  got=database  PASS

3/3 correct

```



The scenarios include two old, unrelated "decoy" deployments that must not be blamed, and one outage with no deployment at all.



\## Limitations



\- The data is simulated, not from a real system.

\- The signal weights are hand-tuned for this simulator.

\- The service dependency map is written by hand.

\- Three scenarios is a small test set, so the 3/3 result does not show how it would perform on real incidents.



\## Tech stack



FastAPI, SQLite, scikit-learn, NetworkX, Claude API, React (Vite), React Flow, Recharts.



\## Run it (Windows PowerShell)



Backend:



```powershell

cd backend

python -m venv venv

venv\\Scripts\\activate

pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000

```



Frontend (second terminal):



```powershell

cd frontend

npm install

npm run dev

```



Open http://localhost:5173, choose a scenario, click \*\*Trigger outage\*\*, then \*\*Investigate\*\*.



Optional: create `backend/.env` containing `ANTHROPIC\_API\_KEY=your-key` for AI-written summaries.



\## Project structure



```

backend/app/

&#x20; db.py          SQLite tables

&#x20; simulator.py   fake shop and outage scenarios

&#x20; detective.py   alert clustering, anomaly detection, cause ranking

&#x20; postmortems.py similar past incidents

&#x20; llm.py         Claude summary with template fallback

&#x20; main.py        API endpoints

backend/test\_scenarios.py

frontend/src/App.jsx

```

