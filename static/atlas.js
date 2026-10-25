// ATLAS UI — vanilla JS, no external dependencies.
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(path, opts = {}) {
  const r = await fetch("/api" + path, {headers: {"Content-Type": "application/json"}, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined});
  if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
  return r.json();
}
const hhmm = iso => (iso || "").slice(11, 16).replace(":", "");

// ---- tabs
$$(".tabs button").forEach(b => b.onclick = () => {
  $$(".tabs button").forEach(x => x.classList.toggle("on", x === b));
  $$(".tab").forEach(t => t.classList.toggle("on", t.id === "tab-" + b.dataset.tab));
  ({notebook: loadNotebook, protocol: loadProtocol, tasks: loadTasks, workspace: loadWs, quarantine: loadQ, world: loadWorld, vision: loadVision})[b.dataset.tab]?.();
});

// ---- chat
function addMsg(role, text, meta) {
  const d = document.createElement("div"); d.className = "msg " + role;
  d.innerHTML = esc(text) + (meta ? `<span class="meta">${esc(meta)}</span>` : "");
  $("#msgs").appendChild(d); $("#msgs").scrollTop = 1e9;
}
async function loadTranscript() {
  $("#msgs").innerHTML = "";
  (await api("/transcript")).forEach(t => addMsg(t.role, t.text, hhmm(t.ts) + (t.role === "atlas" ? " · " + t.source : "")));
}
$("#chatForm").onsubmit = async e => {
  e.preventDefault(); const text = $("#chatIn").value.trim(); if (!text) return;
  $("#chatIn").value = ""; addMsg("user", text);
  const pend = document.createElement("div"); pend.className = "msg atlas"; pend.textContent = "Working."; $("#msgs").appendChild(pend);
  try {
    const r = await api("/chat", {method: "POST", body: {text}}); pend.remove();
    addMsg("atlas", r.text, "now · " + r.source);
    if (r.council) showCouncil(r.council);
    refreshAll();
  } catch (err) { pend.textContent = "Link fault. " + err.message; }
};

// ---- notebook
async function loadNotebook() {
  const rows = await api("/notebook");
  $$(".quad").forEach(q => {
    q.querySelector(".list").innerHTML = rows.filter(r => r.quadrant === q.dataset.q).map(r =>
      `<div class="card"><span class="acts"><select class="mv" data-id="${r.id}">${["known","unknown","risk","opportunity"].map(o=>`<option ${o===r.quadrant?"selected":""}>${o}</option>`).join("")}</select> <button class="x" data-del="${r.id}" title="Remove">✕</button></span><b>${esc(r.title)}</b><div class="body">${esc(r.body)}</div></div>`).join("") || '<div class="small">Empty.</div>';
  });
  $$("[data-del]").forEach(b => b.onclick = async () => { if (confirm("Remove entry?")) { await api("/notebook/" + b.dataset.del, {method: "DELETE"}); loadNotebook(); } });
  $$(".mv").forEach(s => s.onchange = async () => { await api("/notebook/" + s.dataset.id, {method: "PATCH", body: {quadrant: s.value}}); loadNotebook(); });
}
$("#nbForm").onsubmit = async e => { e.preventDefault();
  await api("/notebook", {method: "POST", body: {quadrant: $("#nbQuad").value, title: $("#nbTitle").value, body: $("#nbBody").value}});
  $("#nbTitle").value = $("#nbBody").value = ""; loadNotebook(); };

// ---- protocol
async function loadProtocol() {
  const p = await api("/protocol");
  const a = $("#assess"); a.textContent = p.assessment; a.className = "assess " + (p.withdraw ? "bad" : p.go ? "good" : "");
  $("#checks").innerHTML = p.checks.map(c => `<div class="check"><span class="lbl">${esc(c.label)} ${c.critical ? '<span class="crit">CRITICAL</span>' : ""}</span>
    <span class="seg">${["pending","pass","fail"].map(s => `<button class="${s} ${c.status===s?"on":""}" data-c="${c.id}" data-s="${s}">${s}</button>`).join("")}</span></div>`).join("");
  $$("[data-c]").forEach(b => b.onclick = async () => { await api(`/protocol/checks/${b.dataset.c}`, {method: "PATCH", body: {status: b.dataset.s}}); loadProtocol(); refreshStatus(); });
  $("#reserves").innerHTML = p.reserves.map(r => { const low = r.value < r.minimum, pctv = Math.min(100, r.value / Math.max(r.minimum * 2, 1) * 100);
    return `<div class="res ${low?"low":""}"><b>${esc(r.label)}</b> <span class="small">min ${r.minimum} ${esc(r.unit)}</span><div class="row"><input type="number" step="any" value="${r.value}" data-r="${r.key}"> ${esc(r.unit)}</div><div class="bar"><i style="width:${pctv}%"></i></div></div>`; }).join("");
  $$("[data-r]").forEach(i => i.onchange = async () => { await api("/protocol/reserves/" + i.dataset.r, {method: "PATCH", body: {value: parseFloat(i.value)}}); loadProtocol(); refreshStatus(); });
}
$("#checkForm").onsubmit = async e => { e.preventDefault(); await api("/protocol/checks", {method: "POST", body: {label: $("#checkLabel").value, critical: $("#checkCrit").checked}}); $("#checkLabel").value = ""; loadProtocol(); };
$("#resetChecks").onclick = async () => { await api("/protocol/reset", {method: "POST"}); loadProtocol(); };

// ---- tasks
async function loadTasks() {
  $("#tasks").innerHTML = (await api("/tasks")).map(t => `<div class="task ${t.status}"><input type="checkbox" data-t="${t.id}" ${t.status==="done"?"checked":""}><span class="grow">${esc(t.title)}</span><span class="o">${esc(t.origin)}</span><button class="x" data-td="${t.id}">✕</button></div>`).join("") || '<div class="small">No tasks.</div>';
  $$("[data-t]").forEach(c => c.onchange = async () => { await api("/tasks/" + c.dataset.t, {method: "PATCH", body: {status: c.checked ? "done" : "pending"}}); loadTasks(); });
  $$("[data-td]").forEach(b => b.onclick = async () => { await api("/tasks/" + b.dataset.td, {method: "DELETE"}); loadTasks(); });
}
$("#taskForm").onsubmit = async e => { e.preventDefault(); await api("/tasks", {method: "POST", body: {title: $("#taskIn").value}}); $("#taskIn").value = ""; loadTasks(); };

// ---- workspace
let wsCur = null;
async function loadWs() {
  $("#wsList").innerHTML = (await api("/workspace")).map(f => `<div data-f="${esc(f.name)}" class="${f.name===wsCur?"on":""}">${esc(f.name)}</div>`).join("") || '<div class="small">Empty.</div>';
  $$("[data-f]").forEach(d => d.onclick = () => openWs(d.dataset.f));
}
async function openWs(name) { const f = await api("/workspace/" + encodeURIComponent(name)); wsCur = f.name; $("#wsName").textContent = f.name; $("#wsText").value = f.content; loadWs(); }
$("#wsCreate").onclick = async () => { const n = $("#wsNew").value.trim(); if (!n) return; const r = await api("/workspace", {method: "PUT", body: {name: n, content: ""}}); $("#wsNew").value = ""; openWs(r.name); };
$("#wsSave").onclick = async () => { if (wsCur) await api("/workspace", {method: "PUT", body: {name: wsCur, content: $("#wsText").value}}); };
$("#wsDel").onclick = async () => { if (wsCur && confirm("Delete " + wsCur + "?")) { await api("/workspace/" + encodeURIComponent(wsCur), {method: "DELETE"}); wsCur = null; $("#wsText").value = ""; $("#wsName").textContent = "—"; loadWs(); } };

// ---- quarantine (content shown as inert text only)
async function loadQ() {
  const items = await api("/quarantine");
  $("#qList").innerHTML = items.map(f => `<div><span data-q="${esc(f.name)}">${esc(f.name)}</span><br><span class="small">${esc(f.origin)} · ${f.size} B</span><br>
    <button class="btn sm" data-qp="${esc(f.name)}">Promote</button> <button class="btn sm ghost" data-qx="${esc(f.name)}">Purge</button></div>`).join("") || '<div class="small">Sandbox empty.</div>';
  $$("[data-q]").forEach(s => s.onclick = async () => { $("#qView").textContent = (await api("/quarantine/" + encodeURIComponent(s.dataset.q))).content; });
  $$("[data-qp]").forEach(b => b.onclick = async () => { if (confirm("Promote " + b.dataset.qp + " to workspace? It will be treated as trusted text.")) { await api(`/quarantine/${encodeURIComponent(b.dataset.qp)}/promote?confirm=true`, {method: "POST"}); loadQ(); } });
  $$("[data-qx]").forEach(b => b.onclick = async () => { await api("/quarantine/" + encodeURIComponent(b.dataset.qx), {method: "DELETE"}); $("#qView").textContent = ""; loadQ(); });
}
$("#qForm").onsubmit = async e => { e.preventDefault(); await api("/quarantine", {method: "POST", body: {name: $("#qName").value, origin: $("#qOrigin").value || "manual", content: $("#qContent").value}}); e.target.reset(); loadQ(); };

// ---- recall
$("#recallForm").onsubmit = async e => { e.preventDefault();
  const hits = await api("/recall?q=" + encodeURIComponent($("#recallIn").value));
  $("#recallOut").innerHTML = hits.length ? hits.map(h => `<div class="hit"><div class="src">${esc(h.source)} · ${esc(h.ref)} · score ${h.score}</div>${esc(h.snippet)}</div>`).join("") : '<div class="small">No match.</div>'; };

// ---- council
$("#councilBtn").onclick = () => $("#council").classList.add("open");
$("#councilClose").onclick = () => $("#council").classList.remove("open");
$$("#councilForm button").forEach(b => b.onclick = async e => { e.preventDefault();
  showCouncil(await api("/council", {method: "POST", body: {question: $("#councilQ").value || "current situation", mode: b.dataset.mode}})); });
function showCouncil(c) {
  $("#council").classList.add("open");
  $("#councilOut").innerHTML = (c.mode === "council" ? `<div class="synth">${esc(c.synthesis)}</div>` : "") +
    c.seats.map(s => `<div class="seat ${s.seat}"><h4>${s.seat} · ${s.confidence}%</h4><ol>${s.options.map(o => `<li>${esc(o)}</li>`).join("")}</ol></div>`).join("") +
    `<h3>Ranked</h3><ol>${c.ranked.map(r => `<li>${esc(r.option)} <span class="small">${r.score}</span></li>`).join("")}</ol>`;
}

// ---- events (SSE)
function addEvent(ev, top = true) {
  const d = document.createElement("div"); d.className = "ev " + ev.level;
  d.innerHTML = `<span class="t">${hhmm(ev.ts)}</span> <span class="k">${esc(ev.kind)}</span><br><span class="m">${esc(ev.message)}</span>`;
  top ? $("#events").prepend(d) : $("#events").appendChild(d);
  while ($("#events").children.length > 300) $("#events").lastChild.remove();
}
async function loadEvents() { $("#events").innerHTML = ""; (await api("/events?limit=150")).forEach(e => addEvent(e, false)); }
let es;
function connect() {
  es = new EventSource("/api/stream");
  es.onopen = () => { $("#conn").textContent = "link online"; $("#conn").className = "pill ok"; };
  es.onerror = () => { $("#conn").textContent = "link lost"; $("#conn").className = "pill bad"; };
  es.onmessage = m => { const ev = JSON.parse(m.data); addEvent(ev);
    if (ev.kind.startsWith("protocol")) loadProtocol();
    if (ev.kind.startsWith("notebook")) loadNotebook();
    if (ev.kind.startsWith("task")) loadTasks();
    if (ev.kind.startsWith("predict") || ev.kind.startsWith("world")) loadPreds();
    if (ev.kind === "atlas.speaks") addMsg("atlas", ev.message, "now · ambient");
    if (ev.kind.startsWith("vision")) loadVision();
    if (ev.kind.startsWith("sandbox") || ev.kind.startsWith("protocol")) refreshStatus(); };
}

// ---- status
async function refreshStatus() {
  const s = await api("/status"), t = s.time;
  $("#clock").textContent = t.time; $("#phase").textContent = t.phase + (t.fifo ? " · " + t.fifo : "");
  const q = s.sandbox.quarantined; $("#sbx").textContent = `sandbox armed · ${q} held`; $("#sbx").className = "pill " + (q ? "warn" : "ok");
  const cls = x => x.ok ? "ok" : (x.state === "disabled" ? "off" : "bad");
  $("#strip").innerHTML = `<span>BUS <b class="ok">${esc(s.bus.state)}</b></span><span>DB <b class="${cls(s.db)}">${esc(s.db.state)}</b></span>
    <span>OLLAMA <b class="${cls(s.ollama)}">${esc(s.ollama.state)}</b></span><span>TIME <b class="ok">${esc(t.tz)} ${esc(t.date)}</b></span>
    <span>CHECK-IN <b>${esc(t.checkin)} (${Math.floor(t.minutes_to_checkin/60)}h ${t.minutes_to_checkin%60}m)</b></span>
    <span>SANDBOX <b class="ok">${esc(s.sandbox.state)}</b></span><span class="grow"></span><span class="${s.assessment.withdraw ? "bad" : ""}">${esc(s.assessment.line)}</span>`;
}
function refreshAll() { loadPreds(); loadNotebook(); loadTasks(); loadProtocol(); refreshStatus(); }

loadTranscript(); loadEvents(); refreshAll(); connect();
setInterval(refreshStatus, 15000);

// ---- predictions / world / vision
async function loadPreds() {
  const p = await api("/predictions");
  $("#preds").innerHTML = '<span class="small">PREDICTED NEEDS</span>' + (p.map(x => `<span class="pred ${x.priority?"pri":""}" title="${esc(x.basis)}">${esc(x.need)}<i>${x.confidence}%</i></span>`).join("") || '<span class="small">None above threshold.</span>');
}
async function loadWorld() {
  const w = await api("/world");
  $("#worldSum").innerHTML = Object.entries(w.summary).map(([k,v]) => `<span class="pill">${esc(k)} ${v}</span>`).join("") || '<span class="small">Empty.</span>';
  $("#routines").innerHTML = w.routines.map(r => `${esc(r.subject)} — near ${String(r.hour).padStart(2,"0")}00, seen ${r.count}×`).join("<br>") || "Insufficient data.";
  $("#obs").innerHTML = w.observations.map(o => `<div class="obs"><button class="x" style="float:right" data-wd="${o.id}">✕</button><span class="k">${esc(o.kind)}</span><b>${esc(o.subject)}</b> ${o.inferred?'<span class="inf">inferred</span>':""}<div class="small">${esc(o.source)} · ${hhmm(o.ts)} · ${Math.round(o.confidence*100)}%</div></div>`).join("");
  $$("[data-wd]").forEach(b => b.onclick = async () => { await api("/world/" + b.dataset.wd, {method: "DELETE"}); loadWorld(); });
}
$("#worldForm").onsubmit = async e => { e.preventDefault(); await api("/world", {method: "POST", body: {kind: $("#wKind").value, subject: $("#wSubj").value, detail: $("#wDet").value}}); e.target.reset(); loadWorld(); };
async function loadVision() {
  const v = await api("/vision"), c = v.config;
  $("#vEnabled").checked = c.enabled; $("#vCont").checked = c.hyperlapse; $("#vSource").value = c.source; $("#vFps").value = String(c.interval_s);
  $("#led").textContent = "eye " + v.led; $("#led").className = "pill " + ({SEEING: "see", LEARNING: "learn"}[v.led] || "");
  if (v.last) { $("#vImg").src = "/api/vision/last.jpg?t=" + Date.now(); $("#vCap").textContent = v.last.caption; $("#vInfo").textContent = `${v.last.source} · ${v.last.device_id} · ${v.last.captioner} · ${v.frames} stills this session` + (c.hyperlapse ? ` · hyperlapse every ${c.interval_s} s` : ""); }
  else { $("#vImg").removeAttribute("src"); $("#vCap").textContent = "No frame."; $("#vInfo").textContent = "webcam: " + v.webcam; }
}
const vSave = async () => { await api("/vision/config", {method: "PUT", body: {enabled: $("#vEnabled").checked, hyperlapse: $("#vCont").checked, source: $("#vSource").value, interval_s: parseInt($("#vFps").value) || 120}}); loadVision(); };
["#vEnabled", "#vCont", "#vSource", "#vFps"].forEach(id => $(id).onchange = vSave);
$("#vSend").onclick = () => { const f = $("#vFile").files[0]; if (!f) return; const r = new FileReader();
  r.onload = async () => { try { await api("/vision/frame", {method: "POST", body: {image_b64: r.result, source: "upload"}}); loadVision(); loadPreds(); } catch (e) { alert(e.message); } }; r.readAsDataURL(f); };
$("#vClear").onclick = async () => { if (confirm("Delete all stored frames?")) { await api("/vision", {method: "DELETE"}); loadVision(); } };
loadVision();
