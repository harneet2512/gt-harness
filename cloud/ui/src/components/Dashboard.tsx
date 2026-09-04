import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createSession, listSessions, type SessionStatus } from "../api";

const MODELS = [
  "deepseek/deepseek-v4-flash",
  "deepseek/deepseek-v4-pro",
  "openai/gpt-4o",
  "anthropic/claude-sonnet-4-20250514",
];

const GT_MODES = ["advisory", "engine", "off"];

export default function Dashboard() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<SessionStatus[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const [repo, setRepo] = useState("");
  const [ref, setRef] = useState("main");
  const [task, setTask] = useState("");
  const [model, setModel] = useState(MODELS[0]);
  const [gtMode, setGtMode] = useState("advisory");

  useEffect(() => {
    loadSessions();
    const interval = setInterval(loadSessions, 5000);
    return () => clearInterval(interval);
  }, []);

  async function loadSessions() {
    try {
      setSessions(await listSessions());
    } catch {
      // ignore
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      const session = await createSession({
        repo,
        ref,
        task,
        model,
        gt_mode: gtMode,
        step_limit: 100,
        temperature: 0.0,
      });
      navigate(`/sessions/${session.id}`);
    } catch (err) {
      alert(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  function statusClass(status: string) {
    return `status-badge status-${status}`;
  }

  function formatTime(ts: number) {
    return new Date(ts * 1000).toLocaleTimeString();
  }

  function repoShort(url: string) {
    return url.replace("https://github.com/", "");
  }

  return (
    <div className="page">
      <div className="header">
        <h1>GT Cloud Agent</h1>
        <button className="btn-primary" onClick={() => setShowForm(!showForm)}>
          {showForm ? "Cancel" : "+ New Session"}
        </button>
      </div>

      {showForm && (
        <form className="card" onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Repository URL</label>
            <input
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="https://github.com/owner/repo"
              required
            />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>Ref (branch / tag / SHA)</label>
              <input value={ref} onChange={(e) => setRef(e.target.value)} />
            </div>
            <div className="form-group">
              <label>Model</label>
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                {MODELS.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="form-group">
            <label>Task</label>
            <textarea
              value={task}
              onChange={(e) => setTask(e.target.value)}
              placeholder="Describe the coding task, paste an issue body, etc."
              rows={4}
              required
            />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>GT Mode</label>
              <select
                value={gtMode}
                onChange={(e) => setGtMode(e.target.value)}
              >
                {GT_MODES.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ display: "flex", alignItems: "flex-end" }}>
              <button
                type="submit"
                className="btn-primary"
                disabled={submitting}
                style={{ width: "100%" }}
              >
                {submitting ? "Starting..." : "Start Session"}
              </button>
            </div>
          </div>
        </form>
      )}

      {sessions.length === 0 ? (
        <div className="empty-state">
          <p>No sessions yet. Start one to watch the agent code.</p>
        </div>
      ) : (
        <table className="session-table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Repository</th>
              <th>Task</th>
              <th>Model</th>
              <th>Steps</th>
              <th>Cost</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr
                key={s.id}
                onClick={() => navigate(`/sessions/${s.id}`)}
                style={{ cursor: "pointer" }}
              >
                <td>
                  <span className={statusClass(s.status)}>{s.status}</span>
                </td>
                <td>{repoShort(s.repo)}</td>
                <td style={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {s.task}
                </td>
                <td style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  {s.model}
                </td>
                <td>{s.steps}</td>
                <td>${s.cost.toFixed(3)}</td>
                <td style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  {s.started_at ? formatTime(s.started_at) : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
