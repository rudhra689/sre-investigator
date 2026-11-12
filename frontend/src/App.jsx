import { useEffect, useState } from 'react'
import ReactFlow from 'reactflow'
import 'reactflow/dist/style.css'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import './App.css'

const API = 'http://localhost:8000/api'

const POSITIONS = {
  checkout: { x: 160, y: 0 },
  payment: { x: 0, y: 110 },
  inventory: { x: 320, y: 110 },
  database: { x: 160, y: 220 },
}

const time = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

function DependencyMap({ graph, ranking }) {
  const role = {}
  ranking?.forEach((r) => (role[r.service] = r.role))
  const colors = { 'likely cause': '#dc2626', symptom: '#f59e0b' }

  const nodes = graph.nodes.map((id) => ({
    id,
    position: POSITIONS[id],
    data: { label: id },
    style: {
      background: colors[role[id]] || '#16a34a',
      color: 'white',
      fontWeight: 600,
      border: 'none',
      borderRadius: 8,
      width: 110,
    },
  }))
  const edges = graph.edges.map((e) => ({
    id: `${e.from}-${e.to}`,
    source: e.from,
    target: e.to,
    animated: role[e.to] === 'likely cause',
    label: 'depends on',
  }))

  return (
    <div style={{ height: 320 }}>
      <ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false} zoomOnScroll={false} />
    </div>
  )
}

export default function App() {
  const [scenarios, setScenarios] = useState([])
  const [scenario, setScenario] = useState('slow_query')
  const [sim, setSim] = useState(null)
  const [result, setResult] = useState(null)
  const [graph, setGraph] = useState(null)
  const [series, setSeries] = useState([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([
      fetch(`${API}/scenarios`).then((r) => r.json()),
      fetch(`${API}/graph`).then((r) => r.json()),
    ])
      .then(([s, g]) => {
        setScenarios(s)
        setGraph(g)
      })
      .catch(() => setError('Cannot reach the backend. Is uvicorn running on port 8000?'))
  }, [])

  async function trigger() {
    setBusy('trigger')
    setResult(null)
    setSeries([])
    setError('')
    try {
      const res = await fetch(`${API}/simulate/${scenario}`, { method: 'POST' })
      setSim(await res.json())
    } catch {
      setError('Could not trigger the outage.')
    }
    setBusy('')
  }

  async function investigate() {
    setBusy('investigate')
    setError('')
    try {
      const res = await fetch(`${API}/investigate`, { method: 'POST' })
      const data = await res.json()
      setResult(data)
      if (data.status === 'incident') {
        const m = data.root_cause.metrics[0]
        const pts = await fetch(`${API}/metrics/${data.root_cause.service}?name=${m.name}`).then((r) => r.json())
        setSeries(pts.map((p) => ({ time: time(p.ts), value: p.value, metric: m.name })))
      }
    } catch {
      setError('Investigation failed. Check the backend terminal for errors.')
    }
    setBusy('')
  }

  const incident = result?.status === 'incident'
  const rc = result?.root_cause

  return (
    <div className="page">
      <header>
        <h1>SRE Incident Investigator</h1>
        <p>Break the fake shop, then let the detective find the root cause.</p>
      </header>

      {error && <div className="error">{error}</div>}

      <section className="card controls">
        <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title}
            </option>
          ))}
        </select>
        <button className="danger" onClick={trigger} disabled={busy}>
          {busy === 'trigger' ? 'Breaking...' : 'Trigger outage'}
        </button>
        <button className="primary" onClick={investigate} disabled={busy || !sim}>
          {busy === 'investigate' ? 'Investigating...' : 'Investigate'}
        </button>
        {sim && <span className="muted">{sim.alerts_generated} alerts fired</span>}
      </section>

      {result && !incident && <div className="card">{result.message}</div>}

      {incident && (
        <>
          <section className="card stats">
            <div>
              <b>{result.stats.raw_alerts}</b>
              <span>alerts fired</span>
            </div>
            <div>
              <b>{result.stats.alert_groups}</b>
              <span>groups</span>
            </div>
            <div>
              <b>{result.stats.symptom_alerts}</b>
              <span>are only symptoms</span>
            </div>
          </section>

          <section className="card suspect">
            <h2>Most likely cause</h2>
            <div className="big">
              {rc.service} <span className="badge">{Math.round(rc.confidence * 100)}% confidence</span>
            </div>
            {rc.deployment ? (
              <p>
                Changed right before the incident: <b>{rc.deployment.version}</b> ({rc.deployment.description}),{' '}
                {Math.round((result.timeline[0].onset - rc.deployment.ts) / 60)} min earlier.
              </p>
            ) : (
              <p>No recent deployment found. This looks like a resource or infrastructure problem.</p>
            )}
          </section>

          <section className="card">
            <h2>AI summary</h2>
            <p className="summary">{result.summary.text.replaceAll('**', '')}</p>
            <small className="muted">Source: {result.summary.source}</small>
          </section>

          <div className="grid">
            <section className="card">
              <h2>Dependency map</h2>
              {graph && <DependencyMap graph={graph} ranking={result.ranking} />}
            </section>

            <section className="card">
              <h2>Proof</h2>
              <h3>Logs from {rc.service}</h3>
              <ul>
                {rc.logs.map((l, i) => (
                  <li key={i}>
                    <code>{l.level}</code> {l.message} <span className="muted">x{l.count}</span>
                  </li>
                ))}
              </ul>
              <h3>Metrics</h3>
              <ul>
                {rc.metrics.map((m) => (
                  <li key={m.name}>
                    {m.name}: normal {m.normal}, peak <b>{m.peak}</b>
                  </li>
                ))}
              </ul>
            </section>
          </div>

          <section className="card">
            <h2>
              {rc.service}: {series[0]?.metric}
            </h2>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={series}>
                <XAxis dataKey="time" minTickGap={30} />
                <YAxis />
                <Tooltip />
                <Line type="monotone" dataKey="value" stroke="#dc2626" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </section>

          <div className="grid">
            <section className="card">
              <h2>Who broke first</h2>
              <ol>
                {result.timeline.map((t) => (
                  <li key={t.service}>
                    {time(t.onset)} - {t.service}
                  </li>
                ))}
              </ol>
              <h3>Suspect ranking</h3>
              <ul>
                {result.ranking.map((r) => (
                  <li key={r.service}>
                    <b>{r.service}</b> ({Math.round(r.confidence * 100)}%) - {r.reason}
                  </li>
                ))}
              </ul>
            </section>

            <section className="card">
              <h2>Similar past incidents</h2>
              {result.similar_incidents.length === 0 && <p className="muted">No similar incident found.</p>}
              {result.similar_incidents.map((s) => (
                <div key={s.title} className="past">
                  <b>{s.title}</b> <span className="muted">({Math.round(s.similarity * 100)}% match)</span>
                  <p>Cause: {s.root_cause}</p>
                  <p>Fix: {s.fix}</p>
                </div>
              ))}
            </section>
          </div>

          {sim && (
            <p className="muted">
              Self-check: the simulator's real root cause is <b>{sim.expected_root}</b>, the detective said{' '}
              <b>{rc.service}</b> {sim.expected_root === rc.service ? '(correct)' : '(wrong)'}.
            </p>
          )}
        </>
      )}
    </div>
  )
}