import { useState } from "react";
import { api } from "../api";
import type { Actions } from "../App";
import { usePoll } from "../hooks";
import { ProgressBar, Sparkline, StatTile, StatePill, money } from "../ui";

const INFLIGHT = new Set(["planning", "building", "replanning", "integration"]);

export function Dashboard({ actions }: { actions: Actions }) {
  const dash = usePoll(() => api.dashboard(), 4000);
  const spend = usePoll(() => api.spend(), 8000);
  const [showForm, setShowForm] = useState(false);

  const cards = dash.data?.projects ?? [];
  const total = cards.length;
  const inflight = cards.filter((c) => INFLIGHT.has(c.state)).length;
  const awaiting = cards.filter((c) => c.state === "awaiting_approval").length;
  const blocked = cards.filter((c) => c.state === "blocked").length;
  const sp = spend.data;
  const roleMax = Math.max(1, ...(sp?.by_role.map((r) => r.cost_usd) ?? [0]));

  return (
    <div>
      <div className="view-head">
        <h1>Dashboard</h1>
        <div className="row">
          <button className="btn" onClick={() => setShowForm((s) => !s)}>
            + New project
          </button>
          <button className="btn" onClick={() => actions.act({ intent: "ideate_now" })}>
            Ideate now
          </button>
          <button
            className="btn danger"
            onClick={() => actions.act({ intent: "kill_switch", reason: "operator kill", confirmed: true })}
          >
            Kill switch
          </button>
        </div>
      </div>

      <div className="stats">
        <StatTile ic="▦" label="Projects" value={total} />
        <StatTile ic="⚡" label="In flight" value={inflight} tone="accent" />
        <StatTile ic="⏳" label="Awaiting approval" value={awaiting} tone={awaiting > 0 ? "warn" : undefined} />
        <StatTile ic="■" label="Blocked" value={blocked} tone={blocked > 0 ? "danger" : undefined} />
        <StatTile ic="$" label="Spend today" value={money(sp?.daily_cost_usd ?? 0)} tone="ok" />
      </div>

      {showForm && <NewProjectForm actions={actions} onClose={() => setShowForm(false)} />}

      <div className="insights">
        <div className="insight card">
          <div className="insight-head">
            <span className="t">Daily spend</span>
            <span className="s">last 14 days · {money(sp?.all_time_cost_usd)} all-time</span>
          </div>
          {sp && sp.daily_series.length > 0 ? (
            <Sparkline values={sp.daily_series.map((d) => d.cost)} />
          ) : (
            <div className="empty">no spend yet</div>
          )}
        </div>
        <div className="insight card">
          <div className="insight-head">
            <span className="t">Spend by role</span>
            <span className="s">today</span>
          </div>
          <div className="bars">
            {(sp?.by_role ?? []).slice(0, 6).map((r) => (
              <div className="bar-row" key={r.role}>
                <span className="lbl">{r.role}</span>
                <div className="bar-track">
                  <div className="bar-fill" style={{ width: (r.cost_usd / roleMax) * 100 + "%" }} />
                </div>
                <span className="val">{money(r.cost_usd)}</span>
              </div>
            ))}
            {!sp?.by_role.length && <div className="empty">no spend today</div>}
          </div>
        </div>
      </div>

      <div className="section-title">
        Projects <span className="hint">click one to watch its agents work</span>
      </div>
      {dash.error && <div className="banner">⚠ {dash.error}</div>}
      <div className="projects">
        {cards.map((c) => (
          <div key={c.id} className="pcard card" onClick={() => actions.select(c.id)}>
            <div className="top">
              <span className="pname">{c.name}</span>
              <StatePill state={c.state} />
            </div>
            <div className="detail">{c.status_detail || "—"}</div>
            <ProgressBar done={c.done} total={c.total} />
            <div className="foot">
              <span>{money(c.cost)} spent</span>
              {c.github_repo ? <span>↗ repo</span> : <span>local</span>}
            </div>
          </div>
        ))}
        {!cards.length && !dash.loading && (
          <div className="empty">No projects yet — “Ideate now” or “New project”.</div>
        )}
      </div>
    </div>
  );
}

function NewProjectForm({ actions, onClose }: { actions: Actions; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [rationale, setRationale] = useState("");
  const [stack, setStack] = useState("");
  const [tags, setTags] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const ok = await actions.act({
      intent: "submit_idea",
      title,
      summary,
      rationale,
      stack: stack.split(",").map((s) => s.trim()).filter(Boolean),
      tags: tags.split(",").map((s) => s.trim()).filter(Boolean),
    });
    if (ok) onClose();
  };

  return (
    <form className="card" style={{ padding: 20, marginBottom: 24 }} onSubmit={submit}>
      <div className="section-title" style={{ marginTop: 0 }}>
        Tell the swarm what to build
        <span className="hint">bypasses ideation — goes straight to planning</span>
      </div>
      <div className="field">
        <label>Title</label>
        <input className="input" maxLength={120} required value={title} onChange={(e) => setTitle(e.target.value)} placeholder="OpenAPI Spec Linter for Generated Stubs" />
      </div>
      <div className="field">
        <label>Summary</label>
        <textarea className="input" maxLength={600} required rows={2} value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="What is it, who uses it, why does it exist?" />
      </div>
      <div className="field">
        <label>Rationale <span className="opt">(optional)</span></label>
        <textarea className="input" maxLength={600} rows={2} value={rationale} onChange={(e) => setRationale(e.target.value)} />
      </div>
      <div className="row" style={{ gap: 12 }}>
        <div className="field" style={{ flex: 1 }}>
          <label>Stack <span className="opt">comma-sep</span></label>
          <input className="input" value={stack} onChange={(e) => setStack(e.target.value)} placeholder="python, typer, sqlite" />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>Tags <span className="opt">comma-sep</span></label>
          <input className="input" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="cli, linter" />
        </div>
      </div>
      <div className="row">
        <button className="btn primary" type="submit">Submit idea</button>
        <button className="btn ghost" type="button" onClick={onClose}>Cancel</button>
      </div>
    </form>
  );
}
