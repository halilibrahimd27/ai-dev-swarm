// Small shared presentational helpers used across views.
import type { ProjectState } from "./api";

export function cls(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function money(n: number | null | undefined): string {
  if (n == null) return "—";
  return "$" + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function compact(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
  return String(n);
}

export function initials(role: string): string {
  const r = role.trim();
  if (!r) return "?";
  return r.slice(0, 2).toUpperCase();
}

export function timeAgo(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function StatePill({ state }: { state: ProjectState | string }) {
  return <span className={cls("pill", "s-" + state)}>{state.replace(/_/g, " ")}</span>;
}

export function ProgressBar({ done, total }: { done: number; total: number }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="progress">
      <div className="pmeta">
        <span>
          {done}/{total} milestones
        </span>
        <span>{pct}%</span>
      </div>
      <div className="bar-track">
        <div className="bar-fill" style={{ width: pct + "%" }} />
      </div>
    </div>
  );
}

/** Build an SVG path string for a sparkline over a numeric series. */
export function Sparkline({ values, height = 86 }: { values: number[]; height?: number }) {
  const w = 100;
  const max = Math.max(...values, 0.000001);
  const n = values.length;
  const pts = values.map((v, i) => {
    const x = n > 1 ? (i / (n - 1)) * w : 0;
    const y = height - 6 - (v / max) * (height - 16);
    return [x, y] as const;
  });
  const line = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = `${line} L ${w} ${height} L 0 ${height} Z`;
  const last = pts[pts.length - 1];
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none">
      <path className="area" d={area} />
      <path className="line" d={line} vectorEffect="non-scaling-stroke" />
      {last && <circle className="pt" cx={last[0]} cy={last[1]} r={1.6} />}
    </svg>
  );
}
