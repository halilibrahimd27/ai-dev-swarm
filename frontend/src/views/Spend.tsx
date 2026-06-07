import { api } from "../api";
import { usePoll } from "../hooks";
import { Sparkline, compact, money } from "../ui";

export function Spend() {
  const spend = usePoll(() => api.spend(), 6000);
  const sp = spend.data;

  return (
    <div>
      <div className="view-head">
        <h1>Spend</h1>
      </div>
      <div className="stats">
        <div className="stat card">
          <span className="label">Today</span>
          <span className="num">{money(sp?.daily_cost_usd ?? 0)}</span>
          <span className="label">{compact(sp?.daily_tokens ?? 0)} tokens</span>
        </div>
        <div className="stat card accent">
          <span className="label">All-time</span>
          <span className="num">{money(sp?.all_time_cost_usd ?? 0)}</span>
          <span className="label">{compact(sp?.all_time_tokens ?? 0)} tokens</span>
        </div>
      </div>

      <div className="insight card" style={{ marginBottom: 24 }}>
        <div className="insight-head">
          <span className="t">Daily spend</span>
          <span className="s">last 14 days</span>
        </div>
        {sp && sp.daily_series.length > 0 ? (
          <Sparkline values={sp.daily_series.map((d) => d.cost)} height={120} />
        ) : (
          <div className="empty">no spend yet</div>
        )}
      </div>

      <div className="section-title">By role <span className="hint">today</span></div>
      <div className="card" style={{ padding: 4, marginBottom: 24 }}>
        <table className="table">
          <thead>
            <tr><th>Role</th><th className="num">Tokens</th><th className="num">Cost</th></tr>
          </thead>
          <tbody>
            {(sp?.by_role ?? []).map((r) => (
              <tr key={r.role}><td>{r.role}</td><td className="num">{compact(r.tokens)}</td><td className="num">{money(r.cost_usd)}</td></tr>
            ))}
            {!sp?.by_role.length && <tr><td colSpan={3} className="empty">no spend today</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="section-title">By project <span className="hint">all-time</span></div>
      <div className="card" style={{ padding: 4 }}>
        <table className="table">
          <thead>
            <tr><th>Project</th><th className="num">Tokens</th><th className="num">Cost</th></tr>
          </thead>
          <tbody>
            {(sp?.by_project ?? []).map((r) => (
              <tr key={r.project_id}><td>{r.name}</td><td className="num">{compact(r.tokens)}</td><td className="num">{money(r.cost_usd)}</td></tr>
            ))}
            {!sp?.by_project.length && <tr><td colSpan={3} className="empty">no spend yet</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
