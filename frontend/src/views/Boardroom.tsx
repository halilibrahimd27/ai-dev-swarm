import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Actions } from "../App";
import { usePoll } from "../hooks";
import { cls, clock } from "../ui";

export function Boardroom({ projectId, actions }: { projectId: string | null; actions: Actions }) {
  const board = usePoll(() => api.boardroom(projectId!), 4000, [projectId]);
  const [msg, setMsg] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const entries = board.data ?? [];

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries.length]);

  if (!projectId) {
    return <div className="empty">Select a project from the Dashboard to sit in on its boardroom.</div>;
  }

  const speak = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!msg.trim()) return;
    const ok = await actions.act({ intent: "inject_note", project_id: projectId, body: msg, role: null });
    if (ok) {
      setMsg("");
      board.refresh();
    }
  };

  return (
    <div>
      <div className="view-head">
        <h1>Boardroom</h1>
        <div className="row">
          <button className="btn primary" onClick={() => actions.act({ intent: "approve", project_id: projectId })}>Approve plan</button>
          <button className="btn" onClick={() => actions.act({ intent: "pause_project", project_id: projectId })}>Pause</button>
          <button className="btn" onClick={() => actions.act({ intent: "resume_project", project_id: projectId })}>Resume</button>
        </div>
      </div>
      <p className="view-sub">
        The company meeting: PM, Architect, Reviewer and the Finance officer talk through the key
        calls on this project — high-signal decisions only. Drop in to steer them.
      </p>

      <ol className="stream">
        {entries.map((e) => (
          <li key={e.id} className={cls("decision", "card", "r-" + e.role)}>
            <div className="dhead">
              <span className="drole">{e.role}</span>
              <span className="dts">{clock(e.at)}</span>
            </div>
            <div className="dbody">{e.text}</div>
          </li>
        ))}
        {!entries.length && <div className="empty">No decisions yet — they’ll appear as the staff make calls.</div>}
        <div ref={endRef} />
      </ol>

      <form className="composer" onSubmit={speak}>
        <input className="input" placeholder="speak in the boardroom — steer the team…" value={msg} onChange={(e) => setMsg(e.target.value)} />
        <button className="btn primary" type="submit">Speak</button>
      </form>
    </div>
  );
}
