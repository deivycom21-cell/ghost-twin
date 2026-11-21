// ghost-twin live dashboard — vanilla JS.
// SECURITY: findings contain attack payloads (XSS markers…). Never use innerHTML with data;
// everything goes through h()/textContent.
"use strict";

const $ = (id) => document.getElementById(id);
const PERSONA_EMOJI = { normal_user: "🙂", chaos_gremlin: "😈", security_prober: "🕵️", api_fuzzer: "🔌", health_check: "❤️" };
const SEVERITIES = ["critical", "high", "medium", "low"];
const state = { runs: [], runId: null, detail: null, agents: new Map(), refreshTimer: null };

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  return el;
}
const api = (p) => fetch(p).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString() : "-";
const toast = (msg) => { const t = $("toast"); t.textContent = msg; t.classList.remove("hidden"); setTimeout(() => t.classList.add("hidden"), 1800); };

// ------------------------------------------------------------------ runs
async function loadRuns() {
  state.runs = await api("/api/runs");
  const ul = $("runs");
  ul.replaceChildren(...state.runs.map((r) => h("li", { class: r.id === state.runId ? "active" : "", onclick: () => { $("follow").checked = false; selectRun(r.id); } },
    h("div", { class: "r-top" }, h("span", { class: "r-target" }, `#${r.id} ${r.target}`), h("span", { class: `badge ${r.status}` }, r.status)),
    h("div", { class: "r-sub" }, `${r.findings} findings · ${r.sessions} agents · ${fmtTime(r.started_at)}`))));
  $("empty").classList.toggle("hidden", state.runs.length > 0);
  if (!state.runId && state.runs.length) selectRun(state.runs[0].id);
}

async function selectRun(id) {
  state.runId = id;
  $("run").classList.remove("hidden");
  $("dl-report").href = `/api/runs/${id}/report`;
  for (const li of $("runs").children) li.classList.remove("active");
  const idx = state.runs.findIndex((r) => r.id === id);
  if (idx >= 0 && $("runs").children[idx]) $("runs").children[idx].classList.add("active");
  $("feed").replaceChildren();
  await refreshDetail();
  try {
    const events = await api(`/api/runs/${id}/events?limit=150`);
    if (state.runId === id) events.forEach((e) => onEvent(e, true));
  } catch { /* feed stays empty */ }
}

function scheduleRefresh() {
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(() => { refreshDetail(); loadRuns(); }, 600);
}

async function refreshDetail() {
  if (!state.runId) return;
  const d = await api(`/api/runs/${state.runId}`);
  state.detail = d;
  const run = d.run;
  $("run-target").textContent = run.target;
  const st = $("run-status"); st.className = `badge ${run.status}`; st.textContent = run.status;
  const dur = run.finished_at ? `${Math.round(run.finished_at - run.started_at)}s` : "running…";
  $("run-meta").textContent = `Run #${run.id} · started ${fmtTime(run.started_at)} · ${dur} · seed ${run.seed} · ${d.raw_findings} raw findings`;
  renderSeverities(d.bugs);
  state.agents.clear();
  for (const s of d.sessions) state.agents.set(s.id, { ...s, status: s.status, step: s.last_step });
  renderAgents();
  renderBugs(d.bugs);
}

function renderSeverities(bugs) {
  const counts = Object.fromEntries(SEVERITIES.map((s) => [s, 0]));
  bugs.forEach((b) => counts[b.severity]++);
  $("sev-row").replaceChildren(...SEVERITIES.map((s) => h("div", { class: `sev ${s}` }, h("div", { class: "n" }, counts[s]), h("div", { class: "l" }, s))));
}

function renderAgents() {
  const cards = [...state.agents.values()].map((a) => h("div", { class: `agent ${a.status}`, id: `agent-${a.id}` },
    h("div", { class: "a-head" }, h("span", { class: "a-name" }, `${PERSONA_EMOJI[a.persona] || "👻"} ${a.persona}`), h("span", { class: `badge ${a.status}` }, a.status)),
    h("div", { class: "a-step" }, a.step || "…"),
    h("div", { class: "a-stats" }, h("span", {}, h("b", {}, a.steps ?? 0), " steps"), h("span", {}, h("b", {}, a.findings ?? 0), " findings"))));
  $("agents").replaceChildren(...cards);
}

function renderBugs(bugs) {
  $("bug-count").textContent = `(${bugs.length})`;
  $("bugs").replaceChildren(...bugs.map((b) => h("li", { onclick: () => openBug(b) },
    h("span", { class: `badge ${b.severity}` }, b.severity),
    h("div", { class: "b-main" }, h("div", { class: "b-title" }, b.title), h("div", { class: "b-kind" }, `${b.id} · ${b.kind} · ${b.personas.join(", ")}`)),
    h("span", { class: "b-hits" }, `×${b.hits}`))));
}

// ------------------------------------------------------------------ drawer
async function openBug(b) {
  const kv = (k, v) => (v === null || v === undefined || v === "") ? [] : [h("dt", {}, k), h("dd", {}, String(v))];
  const body = [
    h("span", { class: `badge ${b.severity}` }, b.severity), " ", h("span", { class: "muted" }, `${b.id} · ${b.kind}`),
    h("h2", {}, b.title),
    h("dl", { class: "kv" }, ...kv("URL", b.url), ...kv("Method", b.method), ...kv("HTTP status", b.status), ...kv("Parameter", b.param), ...kv("Payload", b.payload), ...kv("Found by", b.personas.join(", ")), ...kv("Occurrences", b.hits)),
  ];
  const stepsBox = h("ol", { class: "steps" });
  if (b.session_id && b.step_idx !== null && b.step_idx !== undefined) {
    body.push(h("h3", {}, "Steps to reproduce"), stepsBox);
    api(`/api/sessions/${b.session_id}/steps?upto=${b.step_idx}`).then((steps) => {
      stepsBox.replaceChildren(...steps.slice(-10).map((s) => h("li", {}, s.description)));
    }).catch(() => {});
  }
  if (b.curl) body.push(h("h3", {}, "Reproduce with curl"), h("pre", { class: "ev" }, b.curl));
  if (b.evidence) body.push(h("h3", {}, "Evidence"), h("pre", { class: "ev" }, b.evidence));
  if (b.hint) body.push(h("h3", {}, "Suggested fix"), h("p", {}, b.hint));
  const media = h("div", { class: "media" });
  if (b.screenshot) media.append(h("h3", {}, "Screenshot"), h("a", { href: `/artifacts/${b.screenshot}`, target: "_blank" }, h("img", { src: `/artifacts/${b.screenshot}`, alt: "screenshot at failure" })));
  for (const v of b.videos || []) media.append(h("h3", {}, "Session video"), h("video", { src: `/artifacts/${v}`, controls: "", preload: "metadata" }));
  body.push(media);
  $("drawer-body").replaceChildren(...body);
  $("drawer").classList.remove("hidden");
}
$("drawer-close").onclick = () => $("drawer").classList.add("hidden");
$("drawer").onclick = (e) => { if (e.target.id === "drawer") $("drawer").classList.add("hidden"); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("drawer").classList.add("hidden"); });

$("copy-report").onclick = async () => {
  if (!state.runId) return;
  const text = await fetch(`/api/runs/${state.runId}/report`).then((r) => r.text());
  try { await navigator.clipboard.writeText(text); toast("BUG_REPORT.md copied — paste it into Claude Code"); }
  catch { toast("Clipboard blocked — use Download"); }
};

// ------------------------------------------------------------------ live
function feed(cls, who, text) {
  const ol = $("feed");
  ol.prepend(h("li", { class: cls }, h("span", { class: "who" }, who), h("span", {}, text)));
  while (ol.children.length > 300) ol.lastChild.remove();
}

function onEvent(e, replay = false) {
  const d = e.data || {};
  if (replay) {  // history: only feed the log, the REST detail already has the state
    const persona = d.persona || state.agents.get(e.session_id)?.persona || "run";
    const who = `${PERSONA_EMOJI[persona] || "👻"} #${e.session_id ?? "-"}`;
    if (e.type === "step") feed("", who, d.description);
    else if (e.type === "finding") feed(`finding ${d.severity}`, who, `[${d.severity}] ${d.title}`);
    else if (e.type === "session_end") feed("log", who, `finished: ${d.status} (${d.findings} findings)`);
    else if (e.type === "run_end") feed("log", "run", `run ${d.status}`);
    else if (e.type === "log") feed("log", who, d.message || "");
    return;
  }
  if (e.type === "run_start") {
    loadRuns().then(() => { if ($("follow").checked) selectRun(e.run_id); });
    return;
  }
  if (e.run_id !== state.runId) { if (e.type === "run_end") loadRuns(); return; }
  const persona = d.persona || state.agents.get(e.session_id)?.persona || "run";
  const who = `${PERSONA_EMOJI[persona] || "👻"} #${e.session_id ?? "-"}`;
  switch (e.type) {
    case "session_start":
      state.agents.set(d.session_id, { id: d.session_id, persona: d.persona, status: "running", steps: 0, findings: 0, step: "starting…" });
      renderAgents(); feed("log", who, `${d.persona} joined`); break;
    case "step": {
      const a = state.agents.get(e.session_id);
      if (a) {
        a.step = d.description; a.steps = d.idx + 1;
        const card = $(`agent-${a.id}`);
        if (card) { card.querySelector(".a-step").textContent = d.description; card.querySelector(".a-stats b").textContent = a.steps; }
      }
      feed("", who, d.description); break;
    }
    case "finding": {
      const a = state.agents.get(e.session_id);
      if (a) { a.findings = (a.findings || 0) + 1; const card = $(`agent-${a.id}`); if (card) { card.querySelectorAll(".a-stats b")[1].textContent = a.findings; card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash"); } }
      feed(`finding ${d.severity}`, who, `[${d.severity}] ${d.title}`); scheduleRefresh(); break;
    }
    case "session_end":
      feed("log", who, `finished: ${d.status} (${d.findings} findings)`); scheduleRefresh(); break;
    case "run_end":
      feed("log", "run", `run ${d.status}`); scheduleRefresh(); break;
    case "log":
      feed("log", who, d.message || ""); break;
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { $("conn-dot").className = "dot on"; $("conn-text").textContent = "live"; };
  ws.onmessage = (m) => {
    const e = JSON.parse(m.data);
    if (e.type === "hello") { $("version").textContent = `v${e.version}`; return; }
    onEvent(e);
  };
  ws.onclose = () => { $("conn-dot").className = "dot off"; $("conn-text").textContent = "reconnecting…"; setTimeout(connect, 1500); };
}

loadRuns().catch(() => {});
connect();
