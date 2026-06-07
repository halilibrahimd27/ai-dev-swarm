import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { Actions } from "../App";
import { usePoll, useTranscript } from "../hooks";
import { StatTile, StatePill, cls, clock, initials, money, roleColor } from "../ui";

const MARK: Record<string, string> = { done: "✓", building: "•", failed: "!", pending: "" };

export function ProjectView({ projectId, actions }: { projectId: string | null; actions: Actions }) {
  const detail = usePoll(() => api.project(projectId!), 4000, [projectId]);
  const { entries, status } = useTranscript(projectId);
  const [mode, setMode] = useState<"clean" | "technical">("clean");
  const [roleFilter, setRoleFilter] = useState("");
  const [autoscroll, setAutoscroll] = useState(true);
  const [note, setNote] = useState("");
  const [scope, setScope] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  const roles = useMemo(() => Array.from(new Set(entries.map((e) => e.role))).sort(), [entries]);
  const shown = useMemo(
    () => entries.filter((e) => !roleFilter || e.role === roleFilter),
    [entries, roleFilter],
  );

  useEffect(() => {
    if (autoscroll) endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [shown.length, autoscroll]);

  if (!projectId) {
    return <div className="empty">Select a project from the Dashboard to watch its agents work.</div>;
  }

  const p = detail.data?.project;
  const milestones = detail.data?.milestones ?? [];
  const spend = detail.data?.spend;
  const pct = spend && spend.total > 0 ? Math.round((spend.done / spend.total) * 100) : 0;
  const remaining =
    spend && spend.projected_total != null ? Math.max(0, spend.projected_total - spend.cost_so_far) : null;
  const building = milestones.find((m) => m.state === "building");

  const steer = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!note.trim()) return;
    const ok = await actions.act({ intent: "inject_note", project_id: projectId, body: note, role: null });
    if (ok) setNote("");
  };
  const rescope = async () => {
    if (!scope.trim()) return;
    const ok = await actions.act({ intent: "rescope", project_id: projectId, new_scope: scope, confirmed: true });
    if (ok) setScope("");
  };

  return (
    <div>
      <div className="view-head">
        <div>
          <h1>{p?.name ?? "…"}</h1>
          <div className="row" style={{ gap: 8, marginTop: 6 }}>
            {p && <StatePill state={p.state} />}
            <span style={{ color: "var(--muted)", fontSize: 13 }}>{p?.status_detail}</span>
          </div>
        </div>
        <div className="row">
          {p?.state === "awaiting_approval" && (
            <button className="btn primary" onClick={() => actions.act({ intent: "approve", project_id: projectId })}>Approve</button>
          )}
          <button className="btn" onClick={() => actions.act({ intent: "pause_project", project_id: projectId })}>Pause</button>
          <button className="btn" onClick={() => actions.act({ intent: "resume_project", project_id: projectId })}>Resume</button>
          <button className="btn danger" onClick={() => actions.act({ intent: "abort_project", project_id: projectId, reason: "operator abort", confirmed: true })}>Abort</button>
        </div>
      </div>

      {/* Prominent project economics — spent + projected finish cost. */}
      <div className="stats">
        <StatTile ic="$" label="Spent so far" value={money(spend?.cost_so_far ?? 0)} tone="accent" />
        <StatTile ic="◴" label="Projected total" value={spend?.projected_total != null ? money(spend.projected_total) : "—"} tone="warn" sub="at recent per-milestone rate" />
        <StatTile ic="→" label="Est. remaining" value={remaining != null ? money(remaining) : "—"} tone="ok" sub="to finish" />
        <StatTile ic="▦" label="Progress" value={`${spend?.done ?? 0}/${spend?.total ?? 0}`} sub={`${pct}% complete`} />
      </div>

      <div className="section-title">Milestones {building && <span className="hint">building: {building.title}</span>}</div>
      <ol className="timeline card">
        {milestones.map((m) => (
          <li key={m.id} className={cls("tl", "m-" + m.state)}>
            <span className="tlmark">{MARK[m.state] ?? ""}</span>
            <span className="tltitle">{m.title}</span>
            {m.retry_count > 0 && <span className="tlstate" style={{ color: "var(--warn)" }}>retry {m.retry_count}</span>}
            <span className="tlstate">{m.state}</span>
          </li>
        ))}
        {!milestones.length && <div className="empty">No milestones yet — they appear once planning finishes.</div>}
      </ol>

      <div className="section-title">
        Transcript
        <span className="hint">live agent conversation</span>
      </div>
      <div className="card transcript-card">
        <div className="toolbar" style={{ margin: 0, padding: "12px 16px", borderBottom: "1px solid var(--border-soft)" }}>
          <div className="seg">
            <button className={cls(mode === "clean" && "active")} onClick={() => setMode("clean")}>Conversation</button>
            <button className={cls(mode === "technical" && "active")} onClick={() => setMode("technical")}>Technical</button>
          </div>
          <select className="input" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
            <option value="">all roles</option>
            {roles.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <label className="row" style={{ fontSize: 12.5, color: "var(--muted)", gap: 6 }}>
            <input type="checkbox" checked={autoscroll} onChange={(e) => setAutoscroll(e.target.checked)} /> autoscroll
          </label>
          <span className="spacer" />
          <span className="chip">{shown.length} msgs</span>
          <span className={cls("chip", status === "open" && "live")}>
            <span className="dot" /> {status === "open" ? "streaming" : status === "connecting" ? "connecting" : "idle"}
          </span>
        </div>
        <ol className={cls("stream", "mode-" + mode)} style={{ padding: 16, maxHeight: "56vh", overflowY: "auto" }}>
          {shown.map((e) => (
            <li key={e.id} className={cls("msg", "k-" + e.kind)}>
              <span className="avatar" title={e.role} style={{ background: roleColor(e.role) }}>{initials(e.role)}</span>
              <div className="body">
                <div className="mhead">
                  <span className="mrole">{e.role}</span>
                  <span className="mkind">{e.kind}</span>
                  <span className="mts">{clock(e.at)}</span>
                </div>
                <div className="mtext">{e.text}</div>
              </div>
            </li>
          ))}
          {!shown.length && (
            <div className="empty">
              {status === "open"
                ? "Connected — the agents will speak here as they work."
                : "No messages yet."}
            </div>
          )}
          <div ref={endRef} />
        </ol>
      </div>

      <div className="row" style={{ marginTop: 16, gap: 8 }}>
        <input className="input" style={{ flex: 1 }} placeholder="rescope this project…" value={scope} onChange={(e) => setScope(e.target.value)} />
        <button className="btn danger" onClick={rescope}>Rescope</button>
      </div>
      <form className="composer" onSubmit={steer}>
        <input className="input" placeholder="steer the agents — drop a note (fire-and-forget)…" value={note} onChange={(e) => setNote(e.target.value)} />
        <button className="btn primary" type="submit">Send</button>
      </form>
    </div>
  );
}
