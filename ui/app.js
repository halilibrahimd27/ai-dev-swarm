// ai-dev-swarm web panel — vanilla JS, no build step, no CDNs.
//
// Pulls SSE events from /sse/projects + /sse/transcript/{project_id}
// + /sse/metrics, and POSTs operator commands to /api/commands. The
// "steer" text box at the bottom of the transcript fires an
// inject_note command (fire-and-forget — see Phase 5 Mandate 3).

(function () {
  "use strict";

  const state = {
    projects: [],
    selectedProject: null,
    transcriptStream: null,
  };

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  document.addEventListener("DOMContentLoaded", () => {
    loadProjects();
    subscribeProjectsStream();
    bindControls();
    bindSteerForm();
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
    } catch (err) {
      log("error loading projects: " + err);
    }
  }

  function renderProjects() {
    const ul = document.getElementById("project-list");
    ul.innerHTML = "";
    for (const p of state.projects) {
      const li = document.createElement("li");
      li.dataset.id = p.id;
      if (state.selectedProject === p.id) li.classList.add("active");
      const name = document.createElement("span");
      name.textContent = p.name;
      const st = document.createElement("span");
      st.className = "state";
      st.textContent = p.state;
      li.appendChild(name);
      li.appendChild(st);
      li.addEventListener("click", () => selectProject(p.id));
      ul.appendChild(li);
    }
  }

  function selectProject(projectId) {
    state.selectedProject = projectId;
    document.getElementById("current-project").textContent = "(" + projectId.slice(0, 8) + ")";
    renderProjects();
    if (state.transcriptStream) state.transcriptStream.close();
    document.getElementById("transcript").innerHTML = "";
    state.transcriptStream = new EventSource("/sse/transcript/" + projectId);
    state.transcriptStream.onmessage = appendTranscript;
    state.transcriptStream.onerror = () => setStatus("disconnected");
    setStatus("connected");
  }

  // ------------------------------------------------------------------
  // SSE: projects topic (project + milestone state changes)
  // ------------------------------------------------------------------

  function subscribeProjectsStream() {
    const es = new EventSource("/sse/projects");
    es.onopen = () => setStatus("connected");
    es.onerror = () => setStatus("disconnected");
    es.onmessage = (e) => {
      try {
        // Any project-topic event triggers a list refresh — cheap
        // for now; ratchet to delta updates if it becomes a problem.
        JSON.parse(e.data);
        loadProjects();
      } catch (err) {
        log("projects sse parse: " + err);
      }
    };
  }

  // ------------------------------------------------------------------
  // Transcript rendering
  // ------------------------------------------------------------------

  function appendTranscript(e) {
    const list = document.getElementById("transcript");
    let entry;
    try {
      entry = JSON.parse(e.data);
    } catch (err) {
      log("transcript parse: " + err);
      return;
    }
    const li = document.createElement("li");
    const k = document.createElement("span");
    k.className = "kind";
    k.textContent = entry.kind || "msg";
    li.appendChild(k);
    if (entry.role) {
      const r = document.createElement("span");
      r.className = "role role-" + entry.role.replace(/\s+/g, ".");
      r.textContent = entry.role;
      li.appendChild(r);
      li.appendChild(document.createTextNode(" "));
    }
    li.appendChild(document.createTextNode(entry.text || ""));
    list.appendChild(li);
    list.scrollTop = list.scrollHeight;
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
      if (!body || !state.selectedProject) return;
      await sendCommand({
        intent: "inject_note",
        project_id: state.selectedProject,
        body,
      });
      input.value = "";
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
      log(payload.intent + " → " + JSON.stringify(body));
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

  function log(line) {
    const out = document.getElementById("result-log");
    const ts = new Date().toISOString().slice(11, 19);
    out.textContent = "[" + ts + "] " + line + "\n" + out.textContent;
  }
})();
