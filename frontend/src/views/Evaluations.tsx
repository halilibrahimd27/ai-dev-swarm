import { api, type CriticScores } from "../api";
import { usePoll } from "../hooks";
import { cls } from "../ui";

const CRITERIA: { key: keyof CriticScores; label: string }[] = [
  { key: "depth_ambition", label: "Depth" },
  { key: "usefulness_niche", label: "Usefulness" },
  { key: "novelty", label: "Novelty" },
  { key: "decomposability", label: "Decompose" },
  { key: "buildability", label: "Buildability" },
];

export function Evaluations() {
  const ideas = usePoll(() => api.ideas(), 10000);
  const list = ideas.data ?? [];

  return (
    <div>
      <div className="view-head">
        <h1>Idea evaluations</h1>
      </div>
      <p className="view-sub">
        Every idea the Critic scored. An idea must clear the score gate AND be novel to become a
        project — the recomputed total (from the sub-scores) is what the gate uses.
      </p>
      <div className="evals">
        {list.map((e) => (
          <div key={e.id} className="eval card">
            <div className="etop">
              <div>
                <div className="etitle">{e.title}</div>
                <span className={cls("pill", e.accepted ? "s-done" : "s-blocked")}>
                  {e.accepted ? "accepted" : "rejected"}
                </span>
                {!e.novel && <span className="pill s-awaiting_approval" style={{ marginLeft: 6 }}>not novel</span>}
              </div>
              <div className="etotal" style={{ color: e.total >= 80 ? "var(--ok)" : "var(--muted)" }}>{e.total}</div>
            </div>
            <div className="esum">{e.summary}</div>
            <div className="scorebars">
              {CRITERIA.map((c) => (
                <div className="scorebar" key={c.key}>
                  <span>{c.label}</span>
                  <div className="bar-track">
                    <div className="bar-fill" style={{ width: e.scores[c.key] + "%" }} />
                  </div>
                  <span style={{ textAlign: "right" }}>{e.scores[c.key]}</span>
                </div>
              ))}
            </div>
            {e.rejected_reason && <div className="esum" style={{ color: "var(--danger)" }}>{e.rejected_reason}</div>}
          </div>
        ))}
        {!list.length && !ideas.loading && <div className="empty">No evaluations yet.</div>}
      </div>
    </div>
  );
}
