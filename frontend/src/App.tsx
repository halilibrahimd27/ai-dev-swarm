import { useCallback, useEffect, useMemo, useState } from "react";
import { api, sendCommand, type Command } from "./api";
import { usePoll, useTheme } from "./hooks";
import { cls, money } from "./ui";
import { Dashboard } from "./views/Dashboard";
import { ProjectView } from "./views/ProjectView";
import { Boardroom } from "./views/Boardroom";
import { Evaluations } from "./views/Evaluations";
import { Spend } from "./views/Spend";
import { Settings } from "./views/Settings";
import { Activity } from "./views/Activity";

export type Route =
  | "dashboard"
  | "project"
  | "boardroom"
  | "evaluations"
  | "spend"
  | "settings"
  | "activity";

const ROUTES: Route[] = ["dashboard", "project", "boardroom", "evaluations", "spend", "settings", "activity"];

export interface ActivityEvent {
  ok: boolean;
  intent: string;
  detail: string;
  at: string;
}

export interface Actions {
  /** Send a command; toast + log the result. Returns true on success. */
  act: (command: Command) => Promise<boolean>;
  /** Select a project and jump to its live view. */
  select: (id: string) => void;
  goto: (route: Route) => void;
}

interface Toast {
  id: number;
  ok: boolean;
  text: string;
}

function readHash(): Route {
  const h = window.location.hash.replace("#", "") as Route;
  return ROUTES.includes(h) ? h : "dashboard";
}

const NAV: { group: string; items: { route: Route; label: string; ic: string }[] }[] = [
  {
    group: "Overview",
    items: [
      { route: "dashboard", label: "Dashboard", ic: "▦" },
      { route: "boardroom", label: "Boardroom", ic: "⬡" },
    ],
  },
  {
    group: "Project",
    items: [
      { route: "project", label: "Transcript", ic: "▤" },
      { route: "evaluations", label: "Evaluations", ic: "★" },
    ],
  },
  {
    group: "Operations",
    items: [
      { route: "spend", label: "Spend", ic: "$" },
      { route: "settings", label: "Settings", ic: "⚙" },
      { route: "activity", label: "Activity", ic: "≣" },
    ],
  },
];

export function App() {
  const [theme, toggleTheme] = useTheme();
  const [route, setRoute] = useState<Route>(readHash);
  const [selected, setSelected] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const spend = usePoll(() => api.spend(), 8000);
  const dash = usePoll(() => api.dashboard(), 6000);
  const cards = dash.data?.projects ?? [];
  const blocked = cards.filter((c) => c.state === "blocked").length;

  useEffect(() => {
    const onHash = () => setRoute(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const goto = useCallback((r: Route) => {
    window.location.hash = r;
    setRoute(r);
  }, []);

  const toast = useCallback((ok: boolean, text: string) => {
    const id = Date.now() + Math.floor(performance.now());
    setToasts((t) => [...t, { id, ok, text }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  const act = useCallback(
    async (command: Command): Promise<boolean> => {
      const res = await sendCommand(command);
      const line = `${res.intent}: ${res.detail || (res.ok ? "ok" : "failed")}`;
      toast(res.ok, line);
      setEvents((e) => [{ ok: res.ok, intent: res.intent, detail: res.detail, at: new Date().toISOString() }, ...e].slice(0, 200));
      dash.refresh();
      return res.ok;
    },
    [toast, dash],
  );

  const select = useCallback((id: string) => {
    setSelected(id);
    goto("project");
  }, [goto]);

  const actions: Actions = useMemo(() => ({ act, select, goto }), [act, select, goto]);

  // ⌘K / Ctrl-K command palette.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      } else if (e.key === "Escape") {
        setPaletteOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="app">
      <div className="brand">
        <span className="mark">ai</span>
        <div>
          <div className="name">ai-dev-swarm</div>
          <div className="sub">autonomous dev swarm</div>
        </div>
      </div>

      <header className="topbar">
        <button className="btn ghost sm" onClick={() => setPaletteOpen(true)} title="Command palette (⌘K)">⌘K</button>
        <span className="spacer" />
        <span className="chip">today {money(spend.data?.daily_cost_usd ?? 0)}</span>
        <span className="chip">all-time {money(spend.data?.all_time_cost_usd ?? 0)}</span>
        <button
          className="icon-btn"
          title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          onClick={toggleTheme}
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>
        <span className={cls("chip", "live")} title="SSE/API connection">
          <span className="dot" /> connected
        </span>
      </header>

      <nav className="sidenav">
        {NAV.map((g) => (
          <div key={g.group}>
            <div className="nav-group">{g.group}</div>
            {g.items.map((it) => (
              <button key={it.route} className={cls("navitem", route === it.route && "active")} onClick={() => goto(it.route)}>
                <span className="ic">{it.ic}</span>
                {it.label}
                {it.route === "dashboard" && blocked > 0 && <span className="count" style={{ color: "var(--danger)" }}>{blocked}</span>}
              </button>
            ))}
          </div>
        ))}
      </nav>

      <main className="content">
        {route === "dashboard" && <Dashboard actions={actions} />}
        {route === "project" && <ProjectView projectId={selected} actions={actions} />}
        {route === "boardroom" && <Boardroom projectId={selected} actions={actions} />}
        {route === "evaluations" && <Evaluations />}
        {route === "spend" && <Spend />}
        {route === "settings" && <Settings actions={actions} />}
        {route === "activity" && <Activity events={events} />}
      </main>

      {paletteOpen && (
        <CommandPalette
          cards={cards.map((c) => ({ id: c.id, name: c.name }))}
          actions={actions}
          theme={theme}
          toggleTheme={toggleTheme}
          close={() => setPaletteOpen(false)}
        />
      )}

      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={cls("toast", t.ok ? "ok" : "err")}>{t.text}</div>
        ))}
      </div>
    </div>
  );
}

interface PaletteItem {
  label: string;
  hint?: string;
  run: () => void;
}

function CommandPalette({
  cards,
  actions,
  theme,
  toggleTheme,
  close,
}: {
  cards: { id: string; name: string }[];
  actions: Actions;
  theme: string;
  toggleTheme: () => void;
  close: () => void;
}) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);

  const items: PaletteItem[] = useMemo(() => {
    const nav: PaletteItem[] = ROUTES.map((r) => ({
      label: "Go to " + r,
      hint: "view",
      run: () => actions.goto(r),
    }));
    const cmds: PaletteItem[] = [
      { label: "Ideate now", hint: "command", run: () => actions.act({ intent: "ideate_now" }) },
      { label: `Switch to ${theme === "dark" ? "light" : "dark"} theme`, hint: "theme", run: toggleTheme },
      { label: "Kill switch (stop everything)", hint: "danger", run: () => actions.act({ intent: "kill_switch", reason: "operator", confirmed: true }) },
    ];
    const projects: PaletteItem[] = cards.map((c) => ({
      label: "Open " + c.name,
      hint: "project",
      run: () => actions.select(c.id),
    }));
    return [...cmds, ...nav, ...projects];
  }, [cards, actions, theme, toggleTheme]);

  const filtered = items.filter((it) => it.label.toLowerCase().includes(q.toLowerCase()));

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, filtered.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); filtered[sel]?.run(); close(); }
  };

  return (
    <div className="palette-bg" onClick={close}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input
          autoFocus
          placeholder="Type a command or project…"
          value={q}
          onChange={(e) => { setQ(e.target.value); setSel(0); }}
          onKeyDown={onKey}
        />
        <div className="opts">
          {filtered.map((it, i) => (
            <div
              key={it.label}
              className={cls("opt", i === sel && "sel")}
              onMouseEnter={() => setSel(i)}
              onClick={() => { it.run(); close(); }}
            >
              {it.label}
              {it.hint && <span className="k">{it.hint}</span>}
            </div>
          ))}
          {!filtered.length && <div className="empty">no matches</div>}
        </div>
      </div>
    </div>
  );
}
