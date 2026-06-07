import type { ActivityEvent } from "../App";
import { cls, clock } from "../ui";

export function Activity({ events }: { events: ActivityEvent[] }) {
  return (
    <div>
      <div className="view-head">
        <h1>Activity</h1>
      </div>
      <p className="view-sub">Command results + connection events, most recent first.</p>
      <div className="card" style={{ padding: 4 }}>
        <table className="table">
          <thead>
            <tr><th style={{ width: 90 }}>Time</th><th style={{ width: 70 }}>OK</th><th>Detail</th></tr>
          </thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={i}>
                <td style={{ color: "var(--muted)" }}>{clock(e.at)}</td>
                <td className={cls(e.ok ? "" : "")} style={{ color: e.ok ? "var(--ok)" : "var(--danger)" }}>
                  {e.ok ? "✓" : "✗"} {e.intent}
                </td>
                <td style={{ color: "var(--fg-soft)" }}>{e.detail}</td>
              </tr>
            ))}
            {!events.length && <tr><td colSpan={3} className="empty">No activity yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
