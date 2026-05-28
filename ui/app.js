// ai-dev-swarm web panel — vanilla JS, no build step, no CDNs.
//
// Streams SSE from /sse/projects + /sse/transcript/{id} + /sse/metrics,
// polls /api/projects + /api/spend, and POSTs operator commands to
// /api/commands. The transcript renders the agent-to-agent conversation
// as a readable chat log: streamed llm chunks coalesce into one bubble
// per agent turn, tool calls render as compact chips, and each role gets
// a distinct colour so you can follow who is talking.

(function () {
  "use strict";

  const state = {
    projects: [],
    selectedProject: null,
    transcriptStream: null,
    roleFilter: "",
    autoscroll: true,
    // The DOM node of the in-progress streamed bubble, so consecutive
    // llm_chunk events from the same role append instead of spamming.
    streamingNode: null,
    streamingRole: null,
    knownRoles: new Set(),
  };

  // Kinds that carry conversational text worth a full bubble.
  const PROSE_KINDS = new Set([
    "agent_start",
    "agent_done",
    "task_start",
    "task_done",
    "llm_chunk",
  ]);

  document.addEventListener("DOMContentLoaded", () => {
    loadProjects();
    loadSpend();
    subscribeProjectsStream();
    subscribeMetricsStream();
    bindControls();
    bindSteerForm();
    bindTranscriptToolbar();
    // SSE may miss the ideation-landed event (the crew runs off-loop),
    // so poll as a belt-and-braces refresh.
    setInterval(loadProjects, 5000);
    setInterval(loadSpend, 7000);
  });

  // ------------------------------------------------------------------
  // Projects pane
  // ------------------------------------------------------------------

  async function loadProjects() {
    try {
      const res = await fetch("/api/projects");
      if (!res.ok) throw new Error(`/api/projects: ${res.status}`);
      state.projects = await res.json();
      renderProjects();
      if (state.selectedProject) loadMilestones(state.selectedProject);
    } catch (err) {
      log("error loading projects: " + err);
    }
  }

  function renderProjects() {
    const ul = document.getElementById("project-list");
    ul.innerHTML = "";
    if (state.projects.length === 0) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "no projects yet — hit “ideate now”.";
      ul.appendChild(li);
      return;
    }
    for (const p of state.projects) {
      const li = document.createElement("li");
      li.dataset.id = p.id;
      li.className = "project-card";
      if (state.selectedProject === p.id) li.classList.add("active");
      const name = document.createElement("span");
      name.className = "project-name";
      name.textContent = p.name;
      const badge = document.createElement("span");
      badge.className = "badge state-" + p.state;
      badge.textContent = p.state.replace(/_/g, " ");
      li.appendChild(name);
      li.appendChild(badge);
      li.addEventListener("click", () => selectProject(p.id));
      ul.appendChild(li);
    }
  }

  async function loadMilestones(projectId) {
    try {
      const res = await fetch("/api/projects/" + projectId);
      if (!res.ok) return;
      const body = await res.json();
      renderMilestones(body.milestones || []);
    } catch (err) {
      /* non-fatal */
    }
  }

  function renderMilestones(milestones) {
    const wrap = document.getElementById("milestones");
    const list = document.getElementById("milestone-list");
    const count = document.getElementById("milestone-count");
    list.innerHTML = "";
    if (!milestones.length) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    const done = milestones.filter((m) => m.state === "done").length;
    count.textContent = `(${done}/${milestones.length} done)`;
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

  function selectProject(projectId) {
    state.selectedProject = projectId;
    state.streamingNode = null;
    state.streamingRole = null;
    document.getElementById("current-project").textContent =
      "(" + projectId.slice(0, 8) + ")";
    document.getElementById("transcript-empty").hidden = true;
    renderProjects();
    loadMilestones(projectId);
    if (state.transcriptStream) state.transcriptStream.close();
    document.getElementById("transcript").innerHTML = "";
    state.transcriptStream = new EventSource("/sse/transcript/" + projectId);
    state.transcriptStream.onmessage = appendTranscript;
    state.transcriptStream.onerror = () => setStatus("disconnected");
    setStatus("connected");
  }

  // ------------------------------------------------------------------
  // SSE: projects topic (state transitions) + metrics topic
  // ------------------------------------------------------------------

  function subscribeProjectsStream() {
    const es = new EventSource("/sse/projects");
    es.onopen = () => setStatus("connected");
    es.onerror = () => setStatus("disconnected");
    es.onmessage = () => {
      // Any project-topic event => refresh the list + spend.
      loadProjects();
      loadSpend();
    };
  }

  function subscribeMetricsStream() {
    const es = new EventSource("/sse/metrics");
    es.onmessage = (e) => {
      try {
        const entry = JSON.parse(e.data);
        if (entry.kind === "llm_done" || entry.kind === "llm_started") {
          const model = (entry.extra && entry.extra.model) || "";
          pulseSpendChip(model);
        }
      } catch (err) {
        /* ignore */
      }
    };
  }

  // ------------------------------------------------------------------
  // Transcript rendering (chat style)
  // ------------------------------------------------------------------

  function appendTranscript(e) {
    let entry;
    try {
      entry = JSON.parse(e.data);
    } catch (err) {
      log("transcript parse: " + err);
      return;
    }
    registerRole(entry.role);

    // Coalesce streamed chunks from the same role into one bubble.
    if (entry.kind === "llm_chunk" && state.streamingNode && state.streamingRole === entry.role) {
      const body = state.streamingNode.querySelector(".msg-body");
      body.textContent += entry.text || "";
      scrollIfPinned();
      return;
    }

    const li = buildMessage(entry);
    if (state.roleFilter && entry.role && entry.role !== state.roleFilter) {
      li.hidden = true;
    }
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
    const li = document.createElement("li");
    const kind = entry.kind || "msg";
    li.className = "msg kind-" + kind + (entry.role ? " has-role" : "");
    if (entry.role) li.dataset.role = entry.role;

    const head = document.createElement("div");
    head.className = "msg-head";

    if (entry.role) {
      const chip = document.createElement("span");
      chip.className = "role-chip role-" + roleClass(entry.role);
      chip.textContent = entry.role;
      head.appendChild(chip);
    }
    const badge = document.createElement("span");
    badge.className = "kind-badge";
    badge.textContent = prettyKind(kind);
    head.appendChild(badge);

    const ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = fmtTime(entry.at);
    head.appendChild(ts);

    li.appendChild(head);

    const body = document.createElement("div");
    body.className = "msg-body";
    if (kind === "tool_use" || kind === "tool_done") {
      const chipEl = document.createElement("code");
      chipEl.className = "tool-chip";
      chipEl.textContent = "🔧 " + (entry.text || "tool");
      body.appendChild(chipEl);
      if (entry.extra && entry.extra.args) {
        const args = document.createElement("span");
        args.className = "tool-args";
        args.textContent = " " + entry.extra.args;
        body.appendChild(args);
      }
    } else {
      body.textContent = entry.text || "";
    }
    li.appendChild(body);
    return li;
  }

  function registerRole(role) {
    if (!role || state.knownRoles.has(role)) return;
    state.knownRoles.add(role);
    const sel = document.getElementById("role-filter");
    const opt = document.createElement("option");
    opt.value = role;
    opt.textContent = role;
    sel.appendChild(opt);
  }

  function applyRoleFilter() {
    document.querySelectorAll("#transcript .msg").forEach((li) => {
      const role = li.dataset.role || "";
      li.hidden = state.roleFilter !== "" && role !== state.roleFilter;
    });
  }

  function scrollIfPinned() {
    if (!state.autoscroll) return;
    const list = document.getElementById("transcript");
    list.scrollTop = list.scrollHeight;
  }

  // ------------------------------------------------------------------
  // Spend pane
  // ------------------------------------------------------------------

  async function loadSpend() {
    try {
      const res = await fetch("/api/spend");
      if (!res.ok) return;
      const s = await res.json();
      renderSpend(s);
    } catch (err) {
      /* non-fatal */
    }
  }

  function renderSpend(s) {
    const cost = (s.daily_cost_usd || 0).toFixed(2);
    const tokens = fmtTokens(s.daily_tokens || 0);
    document.getElementById("spend-chip").textContent = "$" + cost + " today";
    document.getElementById("spend-total").textContent = "$" + cost + " · " + tokens + " tokens";
    const tbody = document.querySelector("#spend-table tbody");
    tbody.innerHTML = "";
    for (const row of s.by_role || []) {
      const tr = document.createElement("tr");
      const r = document.createElement("td");
      r.className = "sp-role";
      r.textContent = row.role;
      const t = document.createElement("td");
      t.className = "sp-tok";
      t.textContent = fmtTokens(row.tokens);
      const c = document.createElement("td");
      c.className = "sp-cost";
      c.textContent = "$" + (row.cost_usd || 0).toFixed(2);
      tr.appendChild(r);
      tr.appendChild(t);
      tr.appendChild(c);
      tbody.appendChild(tr);
    }
  }

  let pulseTimer = null;
  function pulseSpendChip(model) {
    const chip = document.getElementById("spend-chip");
    chip.classList.add("active");
    if (model) chip.title = "last model: " + model;
    if (pulseTimer) clearTimeout(pulseTimer);
    pulseTimer = setTimeout(() => chip.classList.remove("active"), 1500);
  }

  // ------------------------------------------------------------------
  // Controls pane
  // ------------------------------------------------------------------

  function bindControls() {
    document.querySelectorAll("button[data-intent]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const intent = btn.dataset.intent;
        const payload = buildCommandPayload(intent);
        if (payload === null) return;
        const isDestructive = btn.classList.contains("danger");
        if (isDestructive && !window.confirm("confirm " + intent + "?")) return;
        if (isDestructive) payload.confirmed = true;
        await sendCommand(payload);
      });
    });
  }

  function buildCommandPayload(intent) {
    switch (intent) {
      case "ideate_now":
        return { intent };
      case "approve":
      case "pause_project":
      case "resume_project":
      case "abort_project":
        if (!state.selectedProject) {
          log("select a project first");
          return null;
        }
        return { intent, project_id: state.selectedProject };
      case "rescope": {
        const scope = document.getElementById("rescope-input").value.trim();
        if (!state.selectedProject || !scope) {
          log("select a project + type a new scope");
          return null;
        }
        return { intent, project_id: state.selectedProject, new_scope: scope };
      }
      case "kill_switch":
        return { intent };
      default:
        log("unknown intent: " + intent);
        return null;
    }
  }

  function bindSteerForm() {
    const form = document.getElementById("steer-form");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = document.getElementById("steer-input");
      const body = input.value.trim();
      if (!body || !state.selectedProject) {
        log("select a project first to steer it");
        return;
      }
      await sendCommand({
        intent: "inject_note",
        project_id: state.selectedProject,
        body,
      });
      input.value = "";
    });
  }

  function bindTranscriptToolbar() {
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
      state.streamingRole = null;
    });
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
    } catch (err) {
      log("command failed: " + err);
    }
  }

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------

  function setStatus(s) {
    const el = document.getElementById("ws-status");
    el.textContent = s;
    el.className = "status " + s;
  }

  function roleClass(role) {
    return role.replace(/\s+/g, "-");
  }

  function prettyKind(kind) {
    return kind.replace(/_/g, " ");
  }

  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.toTimeString().slice(0, 8);
  }

  function fmtTokens(n) {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
    return String(n);
  }

  function log(line) {
    const out = document.getElementById("result-log");
    const ts = new Date().toISOString().slice(11, 19);
    out.textContent = "[" + ts + "] " + line + "\n" + out.textContent;
  }
})();
