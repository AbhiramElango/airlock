"use strict";
// All model/file text is rendered with textContent, never innerHTML.

const $ = (id) => document.getElementById(id);
const KEYWORDS = new Set(("False None True and as assert async await break class continue def del elif else " +
  "except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield").split(" "));
const TOKEN = /(#.*$)|("""[\s\S]*?"""|'''[\s\S]*?'''|"""|'''|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_]\w*)\b/g;

let current = null;   // open file path
let lastJob = 0;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

async function api(path, body) {
  const opts = body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function highlight(line, into) {
  let last = 0, prevWord = "";
  for (const m of line.matchAll(TOKEN)) {
    if (m.index > last) into.append(line.slice(last, m.index));
    const [text, com, str, num, word] = m;
    let cls = com ? "com" : str ? "str" : num ? "num" : KEYWORDS.has(word) ? "kw" : prevWord === "def" ? "fn" : null;
    into.append(cls ? el("span", cls, text) : text);
    if (word) prevWord = word;
    last = m.index + text.length;
  }
  if (last < line.length) into.append(line.slice(last));
}

async function loadFiles() {
  const { files } = await api("/api/files");
  const list = $("files");
  list.replaceChildren();
  for (const f of files) {
    const li = el("li", f === current ? "active" : "", f);
    li.onclick = () => openFile(f);
    list.append(li);
  }
  if (!current) {
    const first = files.find((f) => f.endsWith(".py") && !f.split("/").pop().startsWith("test_")) || files[0];
    if (first) openFile(first);
  }
}

async function openFile(path, flash = []) {
  const { text } = await api("/api/file?path=" + encodeURIComponent(path));
  current = path;
  $("filename").textContent = path;
  $("run").disabled = !path.endsWith(".py");
  $("run").textContent = "▶ Run " + path.split("/").pop();
  for (const li of $("files").children) li.classList.toggle("active", li.textContent === path);
  const code = $("code");
  code.replaceChildren();
  text.replace(/\n$/, "").split("\n").forEach((line, i) => {
    const row = el("div", "line");
    row.append(el("span", "ln", String(i + 1)));
    const src = el("span");
    highlight(line, src);
    row.append(src);
    if (flash.includes(i + 1)) {
      row.classList.add("flash");
      setTimeout(() => row.classList.remove("flash"), 1500);
    }
    code.append(row);
  });
}

function showTab(name) {
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t.dataset.tab === name);
  $("output").hidden = name !== "output";
  $("terminal").hidden = name !== "terminal";
  if (name === "terminal") $("term-input").focus();
}

const termHistory = [];
let termPos = 0;

async function termRun(cmd) {
  const log = $("term-log");
  log.append(el("div", "cmd", "$ " + cmd));
  termHistory.push(cmd);
  termPos = termHistory.length;
  if (cmd.trim() === "clear") { log.replaceChildren(); return; }
  const input = $("term-input");
  input.disabled = true;
  input.placeholder = "running…";
  try {
    const r = await api("/api/term", { cmd });
    if (r.output) log.append(el("div", r.status === "ok" ? "" : r.status === "denied" ? "denied" : "err", r.output));
  } catch (e) {
    log.append(el("div", "err", e.message));
  } finally {
    input.disabled = false;
    input.placeholder = "";
    input.focus();
    $("terminal").scrollTop = $("terminal").scrollHeight;
  }
}

async function runCurrent() {
  showTab("output");
  const out = $("output");
  out.className = "output";
  out.textContent = "running " + current + " …";
  try {
    const { result } = await api("/api/run", { path: current });
    out.textContent = (result.output || "").trimEnd() || "(no output)";
    if (result.status !== "ok") {
      out.classList.add("err");
      out.textContent += `\n[exit ${result.returncode ?? result.status}]`;
    }
  } catch (e) {
    out.classList.add("err");
    out.textContent = e.message;
  }
}

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    const net = $("net");
    net.className = "badge " + (s.online ? "online" : "offline");
    net.textContent = s.online ? "● ONLINE" : "● OFFLINE";
    $("mem").textContent = s.mem_available_mb == null ? "" : `RAM free ${(s.mem_available_mb / 1024).toFixed(1)} GB`;
    $("reset").hidden = !s.can_reset;
  } catch {
    $("net").className = "badge";
    $("net").textContent = "server unreachable";
  }
}

function addedLines(diff, path) {
  const lines = [];
  let n = 0, mine = false;
  for (const l of diff.split("\n")) {
    if (l.startsWith("+++ ")) { mine = l === `+++ b/${path}`; continue; }
    if (!mine) continue;
    const h = l.match(/^@@ -\d+(?:,\d+)? \+(\d+)/);
    if (h) { n = Number(h[1]); continue; }
    if (l.startsWith("+++") || l.startsWith("---")) continue;
    if (l.startsWith("+")) { lines.push(n); n++; }
    else if (l.startsWith(" ")) n++;
  }
  return lines;
}

function splitDiff(diff) {
  // One entry per file: { path, lines } (lines start at the first @@).
  const files = [];
  for (const l of diff.replace(/\n$/, "").split("\n")) {
    if (l.startsWith("--- ")) continue;
    if (l.startsWith("+++ ")) { files.push({ path: l.slice(6), lines: [] }); continue; }
    if (files.length && !l.startsWith("\\")) files[files.length - 1].lines.push(l);
  }
  return files;
}

function renderDiff(diff) {
  // One card per changed file: name + stats header, then rows with old/new line numbers.
  const wrap = el("div", "diff-files");
  for (const { path, lines } of splitDiff(diff)) {
    const added = lines.filter((l) => l.startsWith("+")).length;
    const removed = lines.filter((l) => l.startsWith("-")).length;
    const head = el("div", "diff-head");
    head.append(el("span", "diff-name", path), el("span", "ok", `+${added}`), el("span", "bad", `−${removed}`));
    head.title = `Open ${path}`;
    head.onclick = () => openFile(path).catch(() => {});
    const body = el("div", "diff");
    let oldN = 0, newN = 0;
    for (const l of lines) {
      const h = l.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)/);
      if (h) {
        [oldN, newN] = [Number(h[1]), Number(h[2])];
        if (body.childElementCount) body.append(el("div", "gap", "⋯"));
        continue;
      }
      const kind = l[0] === "+" ? "add" : l[0] === "-" ? "del" : "";
      const row = el("div", "row " + kind);
      row.append(el("span", "ln", kind === "add" ? "" : String(oldN)),
                 el("span", "ln", kind === "del" ? "" : String(newN)),
                 el("span", "sign", kind === "add" ? "+" : kind === "del" ? "−" : " "),
                 el("span", "", l.slice(1)));
      body.append(row);
      if (kind !== "add") oldN++;
      if (kind !== "del") newN++;
    }
    const card = el("div", "diff-file");
    card.append(head, body);
    wrap.append(card);
  }
  return wrap;
}

function prose(text) {
  // `backticked` parts become inline code; everything stays textContent.
  const div = el("div");
  text.split("`").forEach((part, i) => div.append(i % 2 ? el("code", "", part) : part));
  return div;
}

function chat(cls, text) {
  const m = el("div", "msg " + cls, text);
  $("chat").append(m);
  m.scrollIntoView({ block: "end" });
  return m;
}

async function ask(task) {
  chat("you", task);
  const msg = chat("agent", "");
  const steps = el("div", "steps");
  msg.append(steps);
  $("question").disabled = true;
  try {
    const job = await api("/api/ask", { task });
    await follow(job.id, msg, steps);
  } catch (e) {
    msg.classList.add("err");
    msg.append(e.message);
  } finally {
    $("question").disabled = false;
    $("question").focus();
  }
}

async function follow(id, msg, steps) {
  lastJob = id;
  const thinking = el("div", "thinking", "Thinking");
  steps.after(thinking);
  try {
    for (;;) {
      const job = await api("/api/job");
      steps.textContent = (job.steps || []).map((s) => "• " + s.text).join("\n");
      if (job.status !== "working") return showResult(job, msg);
      await new Promise((r) => setTimeout(r, 1000));
    }
  } finally {
    thinking.remove();
  }
}

function showResult(job, msg) {
  if (job.status === "error") {
    msg.classList.add("err");
    msg.append(el("div", "", job.error || job.outcome));
    return;
  }
  if (job.status === "answered") {
    msg.append(prose(job.answer));
    msg.scrollIntoView({ block: "end" });
    return;
  }
  if (job.status !== "pending") return;
  if (job.explanation) msg.append(prose(job.explanation));
  msg.append(renderDiff(job.diff));
  const actions = el("div", "actions");
  const yes = el("button", "", "✓ Approve");
  const no = el("button", "secondary", "✕ Reject");
  actions.append(yes, no);
  msg.append(actions);
  msg.scrollIntoView({ block: "end" });
  const decide = async (approve) => {
    yes.disabled = no.disabled = true;
    try {
      const res = await api(approve ? "/api/approve" : "/api/reject", {});
      if (!approve) {
        actions.replaceWith(el("div", "", "Rejected. No files were changed."));
        return;
      }
      const t = res.tests_after;
      const line = el("div", t && t.status === "passed" ? "ok" : "bad",
        `Applied to ${res.targets.join(", ")}. Tests: ${t ? t.summary : "not run"}`);
      actions.replaceWith(line);
      await openFile(res.targets[0], addedLines(res.diff, res.targets[0]));
      $("output").className = "output";
      $("output").textContent = `${res.targets.join(", ")} changed. Click ▶ Run to see the new result.`;
    } catch (e) {
      actions.replaceWith(el("div", "bad", e.message));
    }
  };
  yes.onclick = () => decide(true);
  no.onclick = () => decide(false);
}

function showModel(m) {
  const sel = $("model");
  if (sel.options.length !== m.available.length) {
    sel.replaceChildren(...m.available.map((name) => Object.assign(el("option", "", name), { value: name })));
  }
  sel.value = m.name;
  sel.disabled = m.loading;
  $("model-state").className = "meta" + (m.error ? " bad" : "");
  $("model-state").textContent = m.loading ? "loading model…" : m.error || "";
}

async function loadModels() {
  try { showModel(await api("/api/models")); } catch {}
}

async function switchModel() {
  const name = $("model").value;
  try {
    let m = await api("/api/model", { name });
    showModel(m);
    while (m.loading) {
      await new Promise((r) => setTimeout(r, 1500));
      m = await api("/api/models");
      showModel(m);
    }
    chat("sys", m.error ? m.error : `Now using ${m.name}`);
  } catch (e) {
    chat("err", e.message);
    loadModels();
  }
}

async function resetDemo() {
  $("reset").disabled = true;
  try {
    await api("/api/reset", {});
    const keep = current;
    current = null;
    await loadFiles();
    if (keep) await openFile(keep).catch(() => {});
    $("output").className = "output";
    $("output").textContent = "";
    $("chat").replaceChildren();
  } catch (e) {
    chat("err", e.message);
  } finally {
    $("reset").disabled = false;
  }
}

$("run").onclick = runCurrent;
for (const t of document.querySelectorAll(".tab")) t.onclick = () => showTab(t.dataset.tab);
$("terminal").onclick = () => $("term-input").focus();
$("term-form").onsubmit = (e) => {
  e.preventDefault();
  const cmd = $("term-input").value;
  $("term-input").value = "";
  if (cmd.trim()) termRun(cmd);
};
$("term-input").onkeydown = (e) => {
  if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
  e.preventDefault();
  termPos = Math.max(0, Math.min(termHistory.length, termPos + (e.key === "ArrowUp" ? -1 : 1)));
  $("term-input").value = termHistory[termPos] || "";
};
$("reset").onclick = resetDemo;
$("model").onchange = switchModel;
$("model").onfocus = loadModels;
$("ask").onsubmit = (e) => {
  e.preventDefault();
  const q = $("question").value.trim();
  if (!q) return;
  $("question").value = "";
  ask(q);
};

(async function init() {
  refreshStatus();
  setInterval(refreshStatus, 10000);
  loadModels();
  await loadFiles();
  const job = await api("/api/job");
  if (job.status === "pending" || job.status === "working") {
    const msg = chat("agent", "");
    const steps = el("div", "steps");
    msg.append(steps);
    chat("you", job.task).after(msg);
    await follow(job.id, msg, steps);
  }
  $("question").focus();
})();
