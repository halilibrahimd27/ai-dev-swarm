import { api, type CriticScores, type IdeaEvaluation } from "../api";
import { usePoll } from "../hooks";
import { cls } from "../ui";

const CRITERIA: { key: keyof CriticScores; label: string; weight: number }[] = [
  { key: "depth_ambition", label: "Depth & ambition", weight: 0.3 },
  { key: "usefulness_niche", label: "Usefulness / niche", weight: 0.25 },
  { key: "novelty", label: "Novelty", weight: 0.2 },
  { key: "decomposability", label: "Decomposability", weight: 0.15 },
  { key: "buildability", label: "Buildability", weight: 0.1 },
];

function verdict(e: IdeaEvaluation, gate: number): string {
  const sorted = [...CRITERIA].sort((a, b) => e.scores[b.key] - e.scores[a.key]);
  const top = sorted[0];
  const low = sorted[sorted.length - 1];
  if (e.accepted) {
    return `Accepted — cleared the ${gate}-point gate (total ${e.total}) and passed the novelty check. Strongest dimension: ${top.label} (${e.scores[top.key]}).`;
  }
  if (e.rejected_reason) return `Rejected — ${e.rejected_reason}`;
  if (!e.novel) return `Rejected — failed the novelty check (too close to existing/own work).`;
  if (e.total < gate)
    return `Rejected — total ${e.total} is below the ${gate}-point gate. Weakest dimension: ${low.label} (${e.scores[low.key]}).`;
  return `Rejected.`;
}

export function Evaluations() {
  const ideas = usePoll(() => api.ideas(), 10000);
  const settings = usePoll(() => api.settings(), 30000);
  const list = ideas.data ?? [];
  const gate = Number(settings.data?.find((s) => s.key === "ideation_min_score")?.value ?? 80);

  return (
    <div>
      <div className="view-head">
        <h1>Idea evaluations</h1>
      </div>
      <p className="view-sub">
        Every idea the Critic scored, with the rubric-weighted breakdown and why it was accepted
        or rejected. An idea must clear the {gate}-point gate AND be novel to become a project.
      </p>
      <div className="evals">
        {list.map((e) => (
          <div key={e.id} className="eval card">
            <div className="etop">
              <div style={{ minWidth: 0 }}>
                <div className="etitle">{e.title}</div>
                <div className="row" style={{ gap: 6, marginTop: 6 }}>
                  <span className={cls("pill", e.accepted ? "s-done" : "s-blocked")}>
                    {e.accepted ? "accepted" : "rejected"}
                  </span>
                  <span className={cls("pill", e.novel ? "s-building" : "s-awaiting_approval")}>
                    {e.novel ? "novel" : "not novel"}
                  </span>
                  <span style={{ fontSize: 11, color: "var(--muted)" }}>round {e.round}</span>
                </div>
              </div>
              <div className="etotal" style={{ color: e.total >= gate ? "var(--ok)" : "var(--muted)" }}>
                {e.total}
                <span style={{ fontSize: 12, color: "var(--muted)", fontWeight: 500 }}>/{gate}</span>
              </div>
            </div>

            <div className="esum">{e.summary}</div>

            <div className="scorebars">
              {CRITERIA.map((c) => (
                <div className="scorebar" key={c.key}>
                  <span title={`weight ${Math.round(c.weight * 100)}%`}>
                    {c.label} <span style={{ opacity: 0.6 }}>{Math.round(c.weight * 100)}%</span>
                  </span>
                  <div className="bar-track">
                    <div className="bar-fill" style={{ width: e.scores[c.key] + "%" }} />
                  </div>
                  <span style={{ textAlign: "right" }}>{e.scores[c.key]}</span>
                </div>
              ))}
            </div>

            <div className={cls("everdict", e.accepted ? "ok" : "rej")}>
              {e.accepted ? "✓ " : "✗ "}
              {verdict(e, gate)}
            </div>
          </div>
        ))}
        {!list.length && !ideas.loading && <div className="empty">No evaluations yet.</div>}
      </div>
    </div>
  );
}
