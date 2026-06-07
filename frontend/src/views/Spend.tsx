import { useState } from "react";
import { api, type SpendByProject, type SpendByRole } from "../api";
import { usePoll } from "../hooks";
import { Sparkline, cls, compact, money } from "../ui";

export function Spend() {
  const spend = usePoll(() => api.spend(), 6000);
  const sp = spend.data;
  const [roleScope, setRoleScope] = useState<"today" | "all">("all");
  const [openP, setOpenP] = useState<Set<string>>(new Set());
  const [openR, setOpenR] = useState<Set<string>>(new Set());

  const toggle = (set: Set<string>, fn: (s: Set<string>) => void, k: string) => {
    const n = new Set(set);
    n.has(k) ? n.delete(k) : n.add(k);
    fn(n);
  };

  return (
    <div>
      <div className="view-head">
        <h1>Spend</h1>
      </div>
      <div className="stats">
        <div className="stat card">
          <span className="stat-ic">$</span>
          <div className="stat-body">
            <span className="num">{money(sp?.daily_cost_usd ?? 0)}</span>
            <span className="label">today</span>
            <span className="sub">{compact(sp?.daily_tokens ?? 0)} tokens</span>
          </div>
        </div>
        <div className="stat card">
          <span className="stat-ic accent">∑</span>
          <div className="stat-body">
            <span className="num">{money(sp?.all_time_cost_usd ?? 0)}</span>
            <span className="label">all-time</span>
            <span className="sub">{compact(sp?.all_time_tokens ?? 0)} tokens</span>
          </div>
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

      <div className="section-title">
        By role
        <div className="seg" style={{ marginLeft: "auto" }}>
          <button className={cls(roleScope === "today" && "active")} onClick={() => setRoleScope("today")}>today</button>
          <button className={cls(roleScope === "all" && "active")} onClick={() => setRoleScope("all")}>all-time</button>
        </div>
      </div>
      <div className="card" style={{ padding: 4, marginBottom: 24 }}>
        <table className="table">
          <thead>
            <tr><th>Role</th><th className="num">Tokens</th><th className="num">Cost</th></tr>
          </thead>
          <tbody>
            {roleScope === "today"
              ? (sp?.by_role ?? []).map((r) => <FlatRow key={r.role} label={r.role} r={r} />)
              : (sp?.all_time_by_role ?? []).map((r) => (
                  <ExpandRow
                    key={r.role}
                    label={r.role}
                    tokens={r.tokens}
                    cost={r.cost_usd}
                    open={openR.has(r.role)}
                    onToggle={() => toggle(openR, setOpenR, r.role)}
                    children={(r.projects ?? []).map((p) => ({ label: p.name, tokens: p.tokens, cost: p.cost_usd }))}
                  />
                ))}
            {!(roleScope === "today" ? sp?.by_role.length : sp?.all_time_by_role.length) && (
              <tr><td colSpan={3} className="empty">no spend {roleScope === "today" ? "today" : "yet"}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="section-title">By project <span className="hint">all-time · click to expand roles</span></div>
      <div className="card" style={{ padding: 4 }}>
        <table className="table">
          <thead>
            <tr><th>Project</th><th className="num">Tokens</th><th className="num">Cost</th></tr>
          </thead>
          <tbody>
            {(sp?.by_project ?? []).map((p) => (
              <ExpandRow
                key={p.project_id}
                label={p.name}
                tokens={p.tokens}
                cost={p.cost_usd}
                open={openP.has(p.project_id)}
                onToggle={() => toggle(openP, setOpenP, p.project_id)}
                children={(p.roles ?? []).map((r) => ({ label: r.role, tokens: r.tokens, cost: r.cost_usd }))}
              />
            ))}
            {!sp?.by_project.length && <tr><td colSpan={3} className="empty">no spend yet</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FlatRow({ label, r }: { label: string; r: SpendByRole | SpendByProject }) {
  return (
    <tr>
      <td>{label}</td>
      <td className="num">{compact(r.tokens)}</td>
      <td className="num">{money(r.cost_usd)}</td>
    </tr>
  );
}

function ExpandRow({
  label,
  tokens,
  cost,
  open,
  onToggle,
  children,
}: {
  label: string;
  tokens: number;
  cost: number;
  open: boolean;
  onToggle: () => void;
  children: { label: string; tokens: number; cost: number }[];
}) {
  return (
    <>
      <tr onClick={onToggle} style={{ cursor: "pointer" }}>
        <td>
          <span style={{ display: "inline-block", width: 16, color: "var(--muted)", transition: "transform .15s", transform: open ? "rotate(90deg)" : "none" }}>›</span>
          {label}
        </td>
        <td className="num">{compact(tokens)}</td>
        <td className="num">{money(cost)}</td>
      </tr>
      {open &&
        children.map((c, i) => (
          <tr key={i} style={{ background: "var(--surface-2)" }}>
            <td style={{ paddingLeft: 38, color: "var(--muted)" }}>{c.label}</td>
            <td className="num" style={{ color: "var(--muted)" }}>{compact(c.tokens)}</td>
            <td className="num" style={{ color: "var(--muted)" }}>{money(c.cost)}</td>
          </tr>
        ))}
      {open && !children.length && (
        <tr style={{ background: "var(--surface-2)" }}>
          <td colSpan={3} style={{ paddingLeft: 38, color: "var(--muted)" }}>no breakdown</td>
        </tr>
      )}
    </>
  );
}
