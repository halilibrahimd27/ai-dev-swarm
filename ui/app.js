// ai-dev-swarm web panel — vanilla JS, no build step, no CDNs.
//
// App shell: a left nav routes between views (Dashboard / Transcript /
// Evaluations / Spend / Settings / Activity) via the URL hash. Data comes
// from SSE (/sse/transcript/{id}) + polled REST (/api/projects, /api/spend,
// /api/ideas, /api/settings) and commands POST to /api/commands.

(function () {
  "use strict";

  const ROUTES = ["dashboard", "transcript", "evaluations", "spend", "settings", "activity"];

  const state = {
    projects: [],
    selected: null,
    route: "dashboard",
    transcriptStream: null,
    roleFilter: "",
    autoscroll: true,
    mode: "conversation", // "conversation" (clean) | "technical" (raw firehose)
    streamingNode: null,
    streamingRole: null,
    knownRoles: new Set(),
    seenIds: new Set(),
    settingsLoaded: false,
    fails: 0,
    lastError: "",
  };

  const POLL_OK = 5000;
  const POLL_MAX = 30000;

  // Build markers carry a milestone/CI/review status, not agent chatter.
  const MARKER_KINDS = new Set(["milestone_start", "ci_passed", "ci_failed", "review_done"]);

  document.addEventListener("DOMContentLoaded", () => {
    bindNav();
    bindControls();
    bindSteerForm();
    bindToolbar();
    bindNewProjectForm();
    window.addEventListener("hashchange", () => route(currentHashRoute()));
    route(currentHashRoute());
    tick();
  });

  // ------------------------------------------------------------------
  // Routing
  // ------------------------------------------------------------------

  function currentHashRoute() {
    const r = (location.hash || "").replace(/^#\/?/, "");
    return ROUTES.includes(r) ? r : "dashboard";
  }

  function bindNav() {
    document.querySelectorAll(".navitem").forEach((btn) => {
      btn.addEventListener("click", () => {
        location.hash = "#/" + btn.dataset.route;
      });
    });
  }

  function route(name) {
    state.route = name;
    document.querySelectorAll(".navitem").forEach((b) => {
      b.classList.toggle("active", b.dataset.route === name);
    });
    document.querySelectorAll(".view").forEach((v) => {
      v.hidden = v.id !== "view-" + name;
    });
    if (name === "evaluations") loadIdeas();
    if (name === "settings" && !state.settingsLoaded) loadSettings();
    if (name === "transcript") scrollIfPinned();
  }

  function go(name) {
    location.hash = "#/" + name;
  }

  // ------------------------------------------------------------------
  // Poll loop (projects + spend) with backoff
  // ------------------------------------------------------------------

  async function tick() {
    const ok = await refreshAll();
    state.fails = ok ? 0 : state.fails + 1;
    const delay = ok ? POLL_OK : Math.min(POLL_MAX, POLL_OK * Math.pow(2, state.fails));
    setTimeout(tick, delay);
  }

  async function refreshAll() {
    try {
      const [projects, spend] = await Promise.all([
        fetchJson("/api/projects"),
        fetchJson("/api/spend"),
      ]);
      state.projects = projects;
      renderProjects();
      renderSpend(spend);
      if (state.selected) renderProjectBar(state.selected);
      if (state.route === "evaluations") await loadIdeas();
      setStatus("connected");
      state.lastError = "";
      return true;
    } catch (err) {
      setStatus("reconnecting…");
      logOnce("backend unreachable — retrying (" + err + ")");
      return false;
    }
  }

  async function fetchJson(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(url + ": " + res.status);
    return res.json();
  }

  // ------------------------------------------------------------------
  // Dashboard: project list
  // ------------------------------------------------------------------

  function renderProjects() {
    const ul = document.getElementById("project-list");
    ul.innerHTML = "";
    renderDashboardStats();
    if (!state.projects.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "no projects yet — hit “ideate now” or “new project”.";
      ul.appendChild(li);
      return;
    }
    for (const p of state.projects) {
      ul.appendChild(buildProjectCard(p));
    }
  }

  function renderDashboardStats() {
    const projects = state.projects;
    const inFlight = projects.filter((p) =>
      ["planning", "building", "replanning", "integration"].includes(p.state)
    ).length;
    const awaiting = projects.filter((p) => p.state === "awaiting_approval").length;
    const blocked = projects.filter((p) => p.state === "blocked").length;
    setText("stat-total", projects.length);
    setText("stat-active", inFlight);
    setText("stat-await", awaiting);
    setText("stat-blocked", blocked);
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = String(value);
  }

  function buildProjectCard(p) {
    const li = document.createElement("li");
    li.className = "project-card" + (state.selected === p.id ? " active" : "");
    const top = document.createElement("div");
    top.className = "pc-top";
    const name = document.createElement("span");
    name.className = "project-name";
    name.textContent = p.name;
    const badge = document.createElement("span");
    badge.className = "badge state-" + p.state;
    badge.textContent = p.state.replace(/_/g, " ");
    top.appendChild(name);
    top.appendChild(badge);
    li.appendChild(top);
    if (p.status_detail) {
      const why = document.createElement("div");
      why.className = "why" + (p.state === "blocked" ? " why-blocked" : "");
      why.textContent = p.status_detail;
      li.appendChild(why);
    }
    li.addEventListener("click", () => selectProject(p.id));
    return li;
  }

  function projectById(id) {
    return state.projects.find((p) => p.id === id) || null;
  }

  // ------------------------------------------------------------------
  // Project selection → Transcript view
  // ------------------------------------------------------------------

  async function selectProject(id) {
    state.selected = id;
    state.streamingNode = null;
    state.streamingRole = null;
    state.seenIds = new Set();
    renderProjects();
    renderProjectBar(id);
    go("transcript");
    document.getElementById("transcript-empty").hidden = true;
    if (state.transcriptStream) state.transcriptStream.close();
    const list = document.getElementById("transcript");
    list.innerHTML = "";
    // 1) Replay persisted history so a refresh shows the whole project.
    try {
      const history = await fetchJson("/api/transcript/" + id);
      if (state.selected !== id) return;
      for (const entry of history) renderEntry(entry);
    } catch (err) {
      /* non-fatal */
    }
    if (state.selected !== id) return;
    // 2) Attach the live stream for NEW entries (de-duped by id).
    state.transcriptStream = new EventSource("/sse/transcript/" + id);
    state.transcriptStream.onmessage = appendTranscript;
  }

  async function renderProjectBar(id) {
    const p = projectById(id);
    const bar = document.getElementById("project-bar");
    if (!p) {
      bar.hidden = true;
      return;
    }
    bar.hidden = false;
    document.getElementById("pb-name").textContent = p.name;
    const status = document.getElementById("pb-status");
    status.innerHTML = "";
    const badge = document.createElement("span");
    badge.className = "badge state-" + p.state;
    badge.textContent = p.state.replace(/_/g, " ");
    status.appendChild(badge);
    if (p.status_detail) {
      const why = document.createElement("span");
      why.className = "why" + (p.state === "blocked" ? " why-blocked" : "");
      why.textContent = p.status_detail;
      status.appendChild(why);
    }
    try {
      const body = await fetchJson("/api/projects/" + id);
      renderMilestones(body.milestones || []);
    } catch (err) {
      /* non-fatal */
    }
  }

  function renderMilestones(milestones) {
    const list = document.getElementById("pb-milestones");
    list.innerHTML = "";
    if (!milestones.length) return;
    const done = milestones.filter((m) => m.state === "done").length;
    const head = document.createElement("li");
    head.className = "ms-head";
    head.textContent = `milestones ${done}/${milestones.length} done`;
    list.appendChild(head);
    for (const m of milestones) {
      const li = document.createElement("li");
      li.className = "milestone state-" + m.state;
      const dot = document.createElement("span");
      dot.className = "dot";
      const title = document.createElement("span");
      title.className = "ms-title";
      title.textContent = m.title;
      const st = document.createElement("span");
      st.className = "ms-state";
      st.textContent = m.state;
      li.appendChild(dot);
      li.appendChild(title);
      li.appendChild(st);
      list.appendChild(li);
    }
  }

  // ------------------------------------------------------------------
  // Transcript (chat) — SSE + history replay
  // ------------------------------------------------------------------

  function appendTranscript(e) {
    let entry;
    try {
      entry = JSON.parse(e.data);
    } catch (err) {
      return;
    }
    renderEntry(entry);
  }

  function renderEntry(entry) {
    if (entry.id) {
      if (state.seenIds.has(entry.id)) return;
      state.seenIds.add(entry.id);
    }
    registerRole(entry.role);
    if (entry.kind === "llm_chunk" && state.streamingNode && state.streamingRole === entry.role) {
      state.streamingNode.querySelector(".msg-body").textContent += entry.text || "";
      scrollIfPinned();
      return;
    }
    const li = buildMessage(entry);
    if (state.roleFilter && entry.role && entry.role !== state.roleFilter) li.hidden = true;
    document.getElementById("transcript").appendChild(li);
    if (entry.kind === "llm_chunk") {
      state.streamingNode = li;
      state.streamingRole = entry.role;
    } else {
      state.streamingNode = null;
      state.streamingRole = null;
    }
    scrollIfPinned();
  }

  function buildMessage(entry) {
    const kind = entry.kind || "msg";
    if (MARKER_KINDS.has(kind)) return buildMarker(entry, kind);

    const li = document.createElement("li");
    li.className = "msg kind-" + kind + (entry.role ? " has-role" : "");
    if (entry.role) li.dataset.role = entry.role;

    const head = document.createElement("div");
    head.className = "msg-head";
    if (entry.role) {
      const av = document.createElement("span");
      av.className = "avatar role-" + entry.role.replace(/\s+/g, "-");
      av.textContent = entry.role.slice(0, 1).toUpperCase();
      head.appendChild(av);
      const chip = document.createElement("span");
      chip.className = "role-chip role-" + entry.role.replace(/\s+/g, "-");
      chip.textContent = entry.role;
      head.appendChild(chip);
    }
    const badge = document.createElement("span");
    badge.className = "kind-badge";
    badge.textContent = humanKind(kind);
    head.appendChild(badge);
    const ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = fmtTime(entry.at);
    head.appendChild(ts);
    li.appendChild(head);

    const body = document.createElement("div");
    body.className = "msg-body";
    if (kind === "tool_use" || kind === "tool_done") {
      const human = document.createElement("span");
      human.className = "tool-human";
      human.textContent = humanizeTool(entry.text, entry.extra && entry.extra.args);
      body.appendChild(human);
      const raw = document.createElement("span");
      raw.className = "tool-raw";
      const chip = document.createElement("code");
      chip.className = "tool-chip";
      chip.textContent = "🔧 " + (entry.text || "tool");
      raw.appendChild(chip);
      if (entry.extra && entry.extra.args) {
        const a = document.createElement("span");
        a.className = "tool-args";
        a.textContent = " " + entry.extra.args;
        raw.appendChild(a);
      }
      body.appendChild(raw);
    } else {
      body.textContent = entry.text || "";
    }
    li.appendChild(body);
    return li;
  }

  function buildMarker(entry, kind) {
    const li = document.createElement("li");
    li.className = "marker kind-" + kind;
    const pill = document.createElement("span");
    pill.className = "marker-pill";
    pill.textContent = markerIcon(kind) + " " + (entry.text || humanKind(kind));
    li.appendChild(pill);
    const ts = document.createElement("span");
    ts.className = "marker-ts";
    ts.textContent = fmtTime(entry.at);
    li.appendChild(ts);
    return li;
  }

  function markerIcon(kind) {
    if (kind === "ci_passed" || kind === "review_done") return "✓";
    if (kind === "ci_failed") return "✗";
    return "▶";
  }

  function humanKind(kind) {
    const map = {
      assistant: "says",
      thinking: "thinking",
      tool_use: "action",
      tool_done: "action",
      tool_result: "result",
      llm_chunk: "says",
    };
    return map[kind] || kind.replace(/_/g, " ");
  }

  function humanizeTool(name, argsStr) {
    const n = (name || "tool").trim();
    const args = argsStr || "";
    const file = matchFirst(args, /"(?:file_path|path|notebook_path)"\s*:\s*"([^"]+)"/);
    const cmd = matchFirst(args, /"command"\s*:\s*"((?:[^"\\]|\\.)*)"/);
    const pat = matchFirst(args, /"(?:pattern|query)"\s*:\s*"([^"]+)"/);
    const base = (p) => (p ? p.split("/").pop() : "");
    switch (n) {
      case "Read":
        return "📖 read " + (base(file) || "a file");
      case "Write":
        return "📝 wrote " + (base(file) || "a file");
      case "Edit":
      case "MultiEdit":
        return "✏️ edited " + (base(file) || "a file");
      case "Bash":
        return "⚙️ ran: " + truncate(unescapeJson(cmd) || "a command", 80);
      case "Glob":
        return "🔎 found files " + (pat || "");
      case "Grep":
        return "🔎 searched for " + (pat || "");
      default:
        return "🔧 " + n;
    }
  }

  function matchFirst(text, re) {
    const m = re.exec(text || "");
    return m ? m[1] : "";
  }

  function unescapeJson(s) {
    return (s || "")
      .replace(/\\n/g, " ")
      .replace(/\\t/g, " ")
      .replace(/\\"/g, '"')
      .replace(/\\\\/g, "\\");
  }

  function truncate(s, n) {
    s = (s || "").trim();
    return s.length > n ? s.slice(0, n) + "…" : s;
  }

  function registerRole(role) {
    if (!role || state.knownRoles.has(role)) return;
    state.knownRoles.add(role);
    const opt = document.createElement("option");
    opt.value = role;
    opt.textContent = role;
    document.getElementById("role-filter").appendChild(opt);
  }

  function applyRoleFilter() {
    document.querySelectorAll("#transcript .msg").forEach((li) => {
      li.hidden = state.roleFilter !== "" && (li.dataset.role || "") !== state.roleFilter;
    });
  }

  function scrollIfPinned() {
    if (!state.autoscroll) return;
    const list = document.getElementById("transcript");
    if (list) list.scrollTop = list.scrollHeight;
  }

  // ------------------------------------------------------------------
  // Idea evaluations
  // ------------------------------------------------------------------

  const CRITERIA = [
    ["depth_ambition", "depth"],
    ["usefulness_niche", "useful"],
    ["novelty", "novelty"],
    ["decomposability", "decomp"],
    ["buildability", "build"],
  ];

  async function loadIdeas() {
    let ideas;
    try {
      ideas = await fetchJson("/api/ideas");
    } catch (err) {
      return;
    }
    const wrap = document.getElementById("eval-list");
    wrap.innerHTML = "";
    if (!ideas.length) {
      const p = document.createElement("p");
      p.className = "empty-hint";
      p.textContent = "No evaluations yet. Hit “ideate now” to score some ideas.";
      wrap.appendChild(p);
      return;
    }
    for (const ev of ideas) wrap.appendChild(buildEvalCard(ev));
  }

  function buildEvalCard(ev) {
    const card = document.createElement("div");
    card.className = "eval-card " + (ev.accepted ? "accepted" : "rejected");
    const head = document.createElement("div");
    head.className = "eval-head";
    const verdict = document.createElement("span");
    verdict.className = "eval-verdict " + (ev.accepted ? "v-accepted" : "v-rejected");
    verdict.textContent = ev.accepted ? "ACCEPTED" : "rejected";
    const title = document.createElement("span");
    title.className = "eval-title";
    title.textContent = ev.title;
    const total = document.createElement("span");
    total.className = "eval-total";
    total.textContent = ev.total + "/100";
    head.appendChild(verdict);
    head.appendChild(title);
    head.appendChild(total);
    card.appendChild(head);

    const meta = document.createElement("div");
    meta.className = "eval-meta";
    meta.textContent = "round " + ev.round + (ev.novel ? " · novel" : " · not novel");
    card.appendChild(meta);

    const bars = document.createElement("div");
    bars.className = "eval-bars";
    for (const [key, label] of CRITERIA) {
      const v = (ev.scores && ev.scores[key]) || 0;
      const row = document.createElement("div");
      row.className = "bar-row";
      const lab = document.createElement("span");
      lab.className = "bar-label";
      lab.textContent = label;
      const track = document.createElement("span");
      track.className = "bar-track";
      const fill = document.createElement("span");
      fill.className = "bar-fill";
      fill.style.width = v + "%";
      track.appendChild(fill);
      const num = document.createElement("span");
      num.className = "bar-num";
      num.textContent = v;
      row.appendChild(lab);
      row.appendChild(track);
      row.appendChild(num);
      bars.appendChild(row);
    }
    card.appendChild(bars);

    if (ev.summary) {
      const sm = document.createElement("div");
      sm.className = "eval-summary";
      sm.textContent = ev.summary;
      card.appendChild(sm);
    }
    if (ev.rejected_reason) {
      const rr = document.createElement("div");
      rr.className = "eval-reason";
      rr.textContent = "why rejected: " + ev.rejected_reason;
      card.appendChild(rr);
    }
    return card;
  }

  // ------------------------------------------------------------------
  // Spend
  // ------------------------------------------------------------------

  function renderSpend(s) {
    const today = (s.daily_cost_usd || 0).toFixed(2);
    const all = (s.all_time_cost_usd || 0).toFixed(2);
    document.getElementById("spend-today").textContent = "today $" + today;
    document.getElementById("spend-all").textContent = "all-time $" + all;
    document.getElementById("spend-today-big").textContent = "$" + today;
    document.getElementById("spend-all-big").textContent = "$" + all;
    setText("stat-spend-today", "$" + today);
    fillSpendRows("spend-role-rows", (s.by_role || []).map((r) => [r.role, r.tokens, r.cost_usd]));
    fillSpendRows(
      "spend-project-rows",
      (s.by_project || []).map((r) => [r.name, r.tokens, r.cost_usd])
    );
  }

  function fillSpendRows(tbodyId, rows) {
    const tb = document.getElementById(tbodyId);
    tb.innerHTML = "";
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.className = "sp-empty";
      td.colSpan = 3;
      td.textContent = "—";
      tr.appendChild(td);
      tb.appendChild(tr);
      return;
    }
    for (const [label, tokens, cost] of rows) {
      const tr = document.createElement("tr");
      const a = document.createElement("td");
      a.className = "sp-role";
      a.textContent = label;
      const b = document.createElement("td");
      b.className = "sp-tok";
      b.textContent = fmtTokens(tokens);
      const c = document.createElement("td");
      c.className = "sp-cost";
      c.textContent = "$" + (cost || 0).toFixed(2);
      tr.appendChild(a);
      tr.appendChild(b);
      tr.appendChild(c);
      tb.appendChild(tr);
    }
  }

  // ------------------------------------------------------------------
  // Settings (operator-editable operational knobs)
  // ------------------------------------------------------------------

  async function loadSettings() {
    let rows;
    try {
      rows = await fetchJson("/api/settings");
    } catch (err) {
      return;
    }
    state.settingsLoaded = true;
    const form = document.getElementById("settings-form");
    form.innerHTML = "";
    const groups = {};
    for (const r of rows) (groups[r.group] = groups[r.group] || []).push(r);
    for (const group of Object.keys(groups)) {
      const fs = document.createElement("fieldset");
      const lg = document.createElement("legend");
      lg.textContent = group;
      fs.appendChild(lg);
      for (const item of groups[group]) fs.appendChild(buildSettingRow(item));
      form.appendChild(fs);
    }
  }

  function buildSettingRow(item) {
    const row = document.createElement("div");
    row.className = "setting-row";

    const label = document.createElement("label");
    label.className = "setting-label";
    label.textContent = item.label;
    if (item.restart_required) {
      const tag = document.createElement("span");
      tag.className = "restart-tag";
      tag.textContent = "restart";
      label.appendChild(tag);
    }

    const ctrlWrap = document.createElement("div");
    ctrlWrap.className = "setting-ctrl";
    buildSettingControl(ctrlWrap, item);

    const help = document.createElement("div");
    help.className = "setting-help";
    help.textContent = item.help || "";

    row.appendChild(label);
    row.appendChild(ctrlWrap);
    row.appendChild(help);
    return row;
  }

  function buildSettingControl(wrap, item) {
    if (item.kind === "bool" || item.kind === "enum") {
      const sel = document.createElement("select");
      const choices = item.kind === "bool" ? ["true", "false"] : item.choices;
      for (const c of choices) {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = c;
        if (String(item.value) === c) opt.selected = true;
        sel.appendChild(opt);
      }
      sel.addEventListener("change", () => saveSetting(item.key, sel.value));
      wrap.appendChild(sel);
      return;
    }
    // int / float — number input + explicit save (no save-per-keystroke).
    const input = document.createElement("input");
    input.type = "number";
    input.value = item.value;
    if (item.minimum !== null && item.minimum !== undefined) input.min = item.minimum;
    if (item.maximum !== null && item.maximum !== undefined) input.max = item.maximum;
    if (item.kind === "float") input.step = "0.1";
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "save";
    save.addEventListener("click", () => saveSetting(item.key, input.value));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") saveSetting(item.key, input.value);
    });
    wrap.appendChild(input);
    wrap.appendChild(save);
  }

  async function saveSetting(key, value) {
    await sendCommand({ intent: "update_setting", key, value: String(value) });
    state.settingsLoaded = false;
    loadSettings(); // reflect the coerced/echoed value
  }

  // ------------------------------------------------------------------
  // Controls + steering
  // ------------------------------------------------------------------

  function bindControls() {
    document.querySelectorAll("button[data-intent]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const intent = btn.dataset.intent;
        const payload = buildPayload(intent);
        if (payload === null) return;
        const destructive = btn.classList.contains("danger");
        if (destructive && !window.confirm("confirm " + intent + "?")) return;
        if (destructive) payload.confirmed = true;
        await sendCommand(payload);
      });
    });
  }

  function buildPayload(intent) {
    switch (intent) {
      case "ideate_now":
      case "kill_switch":
        return { intent };
      case "approve":
      case "pause_project":
      case "resume_project":
      case "abort_project":
        if (!state.selected) return reqProject();
        return { intent, project_id: state.selected };
      case "rescope": {
        const scope = document.getElementById("rescope-input").value.trim();
        if (!state.selected || !scope) {
          log("select a project + type a new scope");
          return null;
        }
        return { intent, project_id: state.selected, new_scope: scope };
      }
      default:
        return null;
    }
  }

  function reqProject() {
    log("select a project first (Dashboard)");
    return null;
  }

  function bindSteerForm() {
    document.getElementById("steer-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = document.getElementById("steer-input");
      const body = input.value.trim();
      if (!body) return;
      if (!state.selected) return reqProject();
      await sendCommand({ intent: "inject_note", project_id: state.selected, body });
      input.value = "";
    });
  }

  function bindNewProjectForm() {
    document.getElementById("toggle-new-project").addEventListener("click", () => {
      toggleNewProjectForm();
    });
    document.getElementById("np-cancel").addEventListener("click", () => {
      toggleNewProjectForm(false);
    });
    document.getElementById("new-project-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const payload = {
        intent: "submit_idea",
        title: val("np-title"),
        summary: val("np-summary"),
        rationale: val("np-rationale"),
        stack: splitCsv(val("np-stack")),
        tags: splitCsv(val("np-tags")),
      };
      const result = await sendCommand(payload);
      if (result && result.ok) {
        clearNewProjectForm();
        toggleNewProjectForm(false);
      }
    });
  }

  function toggleNewProjectForm(show) {
    const form = document.getElementById("new-project-form");
    const open = typeof show === "boolean" ? show : form.hidden;
    form.hidden = !open;
    if (open) document.getElementById("np-title").focus();
  }

  function clearNewProjectForm() {
    ["np-title", "np-summary", "np-rationale", "np-stack", "np-tags"].forEach((id) => {
      document.getElementById(id).value = "";
    });
  }

  function val(id) {
    return document.getElementById(id).value.trim();
  }

  function splitCsv(s) {
    return (s || "")
      .split(",")
      .map((x) => x.trim())
      .filter((x) => x.length > 0);
  }

  function bindToolbar() {
    document.getElementById("autoscroll").addEventListener("change", (e) => {
      state.autoscroll = e.target.checked;
    });
    document.getElementById("role-filter").addEventListener("change", (e) => {
      state.roleFilter = e.target.value;
      applyRoleFilter();
    });
    document.getElementById("clear-transcript").addEventListener("click", () => {
      document.getElementById("transcript").innerHTML = "";
      state.streamingNode = null;
      state.seenIds = new Set();
    });
    document.querySelectorAll(".seg-btn").forEach((btn) => {
      btn.addEventListener("click", () => setMode(btn.dataset.mode));
    });
    setMode(state.mode);
  }

  function setMode(mode) {
    state.mode = mode;
    document.querySelectorAll(".seg-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.mode === mode);
    });
    const list = document.getElementById("transcript");
    list.classList.toggle("mode-conversation", mode === "conversation");
    list.classList.toggle("mode-technical", mode === "technical");
    scrollIfPinned();
  }

  async function sendCommand(payload) {
    try {
      const res = await fetch("/api/commands", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json();
      log(payload.intent + " → " + (body.detail || JSON.stringify(body)));
      refreshAll();
      return body;
    } catch (err) {
      log("command failed: " + err);
      return null;
    }
  }

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------

  function setStatus(s) {
    const el = document.getElementById("ws-status");
    el.textContent = s;
    el.className = "status " + (s === "connected" ? "connected" : "disconnected");
  }

  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d.getTime()) ? "" : d.toTimeString().slice(0, 8);
  }

  function fmtTokens(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
    return String(n);
  }

  function log(line) {
    const out = document.getElementById("result-log");
    const ts = new Date().toISOString().slice(11, 19);
    out.textContent = "[" + ts + "] " + line + "\n" + out.textContent;
  }

  function logOnce(line) {
    if (line === state.lastError) return;
    state.lastError = line;
    log(line);
  }
})();
