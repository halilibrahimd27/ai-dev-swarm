import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { Actions } from "../App";
import { usePoll, useTranscript } from "../hooks";
import { StatePill, cls, clock, initials, money } from "../ui";

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
          <div className="row" style={{ gap: 8, marginTop: 4 }}>
            {p && <StatePill state={p.state} />}
            <span className="view-sub" style={{ margin: 0 }}>{p?.status_detail}</span>
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

      {spend && (
        <div className="row" style={{ gap: 16, marginBottom: 12, color: "var(--muted)", fontSize: 13 }}>
          <span>{money(spend.cost_so_far)} spent</span>
          <span>·</span>
          <span>~{money(spend.projected_total)} projected</span>
          <span>·</span>
          <span>{spend.done}/{spend.total} milestones</span>
        </div>
      )}

      <ol className="timeline">
        {milestones.map((m) => (
          <li key={m.id} className={cls("tl", "m-" + m.state)}>
            <span className="tlmark">{MARK[m.state] ?? ""}</span>
            <span className="tltitle">{m.title}</span>
            {m.retry_count > 0 && <span className="tlstate" style={{ color: "var(--warn)" }}>retry {m.retry_count}</span>}
            <span className="tlstate">{m.state}</span>
          </li>
        ))}
      </ol>

      <div className="toolbar">
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
        <span className="chip">{status === "open" ? "● streaming" : status === "connecting" ? "… connecting" : "○ idle"}</span>
      </div>

      <ol className={cls("stream", "mode-" + mode)}>
        {shown.map((e) => (
          <li key={e.id} className={cls("msg", "k-" + e.kind)}>
            <span className="avatar" title={e.role}>{initials(e.role)}</span>
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
        {!shown.length && <div className="empty">No messages yet — the agents will speak as they work.</div>}
        <div ref={endRef} />
      </ol>

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
