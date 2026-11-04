(() => {
'use strict';
/* VECTOR Investigation Command Center. Every node, count and event below is
   rendered from recorded pipeline data. Stages without recorded events stay dim
   and are never animated as active. */
const svgNS = 'http://www.w3.org/2000/svg';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

const ACTORS = {
  vector_plan: ['VECTOR', 'Plan · hypothesis + tool tasks'],
  scope: ['SCOPE', 'Atlas-driven failure discovery'],
  circuit: ['CIRCUIT', 'Connectivity + subsystem discovery'],
  scenario: ['SCENARIO', 'Startup / fault / environment discovery'],
  ledger: ['Evidence Ledger', 'Merged hypotheses + provenance'],
  origin: ['ORIGIN', 'Product-model review'],
  doubt: ['DOUBT', 'Blind independent review'],
  fault: ['FAULT', 'Skeptical review'],
  gauge: ['GAUGE', 'Confidence audit'],
  verdict: ['VERDICT', 'Evidence judgment · Gate A'],
};
const STATUS_COLOR = { ok: '#4fae9d', warn: '#d9a441', bad: '#c25e4e', idle: '#57657a' };

let D = null;            // full investigation payload
let claimsById = {};
let nodes = [], edges = [];
let seqMax = 0, seqCur = 0, playing = false, playTimer = 0;
let packets = [];
let selected = null;
let live = false;
let isDemo = false;

async function load() {
  // Recorded demo data must never masquerade as the active project's
  // investigation: the fixture loads only on explicit ?demo=1.
  const demo = new URLSearchParams(location.search).has('demo');
  if (window.__COMMAND_DATA__) {  // preloaded (standalone saved report)
    D = window.__COMMAND_DATA__; live = false;
  } else {
    try {
      const r = await fetch('/api/investigation');
      const j = await r.json();
      if (j && j.status !== 'empty' && j.pipeline) { D = j; live = true; }
      // An explicit empty response resets the view (e.g. project switched to
      // one with no recorded run). Network failures keep the last good state.
      else if (j && j.status === 'empty') { D = null; live = false; }
    } catch (e) { /* fall through below */ }
  }
  isDemo = false;
  if (!D && demo) {
    const r = await fetch('/command-replay.json');
    D = await r.json();
    live = false;
    isDemo = true;
  }
  if (!D) {
    // No recorded run: still render the real workflow topology with every
    // stage dimmed. A dim stage never implies its agent executed.
    D = { pipeline: { status: 'empty', steps: [], tasks: [], limitations: [],
      ledger: { claims: [], events: [] }, replay: [], coverage_proof: { by_status: {}, by_origin: {} },
      elapsed_seconds: 0 }, result: { actions: [], questions: [] }, model: '', usage: {} };
    live = false;
  }
  for (const c of D.pipeline.ledger.claims) claimsById[c.id] = c;
  seqMax = D.pipeline.replay.length;
  seqCur = seqMax;
  buildModel();
  renderRail();
  renderGraph();
  setSeq(seqMax, true);
  const pstatus = (D.pipeline && D.pipeline.status) || '';
  $('src-banner').textContent =
    (!live && pstatus === 'empty') ? 'No investigation recorded yet — run a Vector review.'
    : live && (pstatus === 'failed' || pstatus === 'unavailable' || pstatus === 'limited')
    ? `Live · investigation ${pstatus} — recorded events are shown below`
    : live ? 'Live · current project investigation'
    : isDemo ? 'Replay · recorded investigation (demo data)'
    : 'Replay · recorded investigation';
}

function replayKinds() {
  const k = {};
  for (const e of D.pipeline.replay) k[e.kind] = (k[e.kind] || 0) + 1;
  return k;
}
function actorEvents(actor) {
  return D.pipeline.replay.filter(e => e.actor === actor);
}
function originClaims(origin) {
  return D.pipeline.ledger.claims.filter(c => c.origin === origin);
}

function buildModel() {
  const kinds = replayKinds();
  const byOrigin = D.pipeline.coverage_proof.by_origin;
  nodes = []; edges = [];
  const N = (id, x, y, r, status, count) => {
    const [label, sub] = ACTORS[id] || [id, ''];
    const n = { id, label, sub, x, y, r, status, count, actor: id };
    nodes.push(n); return n;
  };
  // Discovery column: only origins with real claims become live nodes.
  N('vector_plan', 100, 120, 30, kinds.hypothesized ? 'ok' : 'idle', `${byOrigin.vector_plan || 0} claim`);
  N('scope', 100, 300, 44, (byOrigin.scope ? 'ok' : 'idle'), `${byOrigin.scope || 0} claims`);
  N('circuit', 100, 480, 34, (byOrigin.circuit ? 'ok' : 'idle'), `${byOrigin.circuit || 0} claims`);
  N('scenario', 100, 640, 34, (byOrigin.scenario ? 'ok' : 'idle'), `${byOrigin.scenario || 0} claims`);
  // Task cluster: two columns of four.
  const shortTool = { read_datasheet: 'datasheet', trace_connections: 'trace', calculate_limits: 'calc', inspect_layout: 'layout' };
  D.pipeline.tasks.forEach((t, i) => {
    nodes.push({ id: 'task:' + t.id, label: t.id, sub: t.tool, x: 285 + (i % 2) * 150, y: 130 + Math.floor(i / 2) * 180,
      r: 26, status: t.status === 'evidence_gathered' ? 'ok' : 'warn',
      count: shortTool[t.tool] || t.tool, count2: t.status === 'evidence_gathered' ? 'evidence' : t.status, kind: 'task', task: t });
  });
  // Consults: skipped, never executed -> idle honesty node.
  nodes.push({ id: 'consults', label: 'CONSULTS', sub: 'SOURCE · CALC specialists', x: 360, y: 745,
    r: 26, status: 'idle', count: `${kinds.consult_skipped || 0} skipped`, count2: '0 ran', kind: 'consults' });
  // Ledger hub.
  const nev = (D.pipeline.ledger.events || D.pipeline.replay || []).length;
  const ledger = N('ledger', 660, 385, 58, 'ok', `${D.pipeline.ledger.claims.length} claims`);
  ledger.count2 = `${nev} events`;
  // Review column.
  N('origin', 905, 130, 30, kinds.origin_reviewed ? 'ok' : 'idle', kinds.origin_reviewed ? 'reviewed' : 'not run');
  N('doubt', 905, 290, 32, kinds.doubt_assessed ? 'ok' : 'idle', `${byOrigin.doubt || 0} claims`);
  N('fault', 905, 450, 30, kinds.fault_unavailable ? 'bad' : (actorEvents('fault').length ? 'ok' : 'idle'),
    kinds.fault_unavailable ? 'unavailable' : 'reviewed');
  N('gauge', 905, 610, 30, kinds.gauge_unavailable ? 'bad' : (actorEvents('gauge').length ? 'ok' : 'idle'),
    kinds.gauge_unavailable ? 'unavailable' : 'audited');
  // Verdict + outcomes.
  const gateA = actorEvents('verdict').filter(e => e.kind === 'gate_a_completed').length;
  const verdict = N('verdict', 1130, 140, 30, gateA ? 'warn' : 'idle', 'Gate A');
  verdict.count2 = gateA ? 'partial' : 'not run';
  const byStatus = D.pipeline.coverage_proof.by_status;
  const acts = (D.result.actions || []).length, qs = (D.result.questions || []).length;
  nodes.push({ id: 'out:supported', label: 'Supported', sub: 'Evidence-backed concerns', x: 1130, y: 300, r: 28, status: 'ok', count: String(byStatus.supported || 0), kind: 'outcome', outcome: 'supported' });
  nodes.push({ id: 'out:unresolved', label: 'Unresolved', sub: 'Insufficient evidence', x: 1130, y: 440, r: 28, status: 'warn', count: String(byStatus.unresolved || 0), kind: 'outcome', outcome: 'unresolved' });
  nodes.push({ id: 'out:actions', label: 'Actions', sub: 'Investigations to run', x: 1130, y: 570, r: 26, status: 'warn', count: String(acts), kind: 'actions' });
  nodes.push({ id: 'out:questions', label: 'Questions', sub: 'Needs designer input', x: 1130, y: 690, r: 26, status: 'idle', count: String(qs), kind: 'questions' });
  const E = (a, b) => edges.push({ a, b });
  E('vector_plan', 'scope'); E('vector_plan', 'circuit'); E('vector_plan', 'scenario');
  for (const o of ['scope', 'circuit', 'scenario']) E(o, 'ledger');
  for (const t of D.pipeline.tasks) E('task:' + t.id, 'ledger');
  E('consults', 'ledger');
  for (const r of ['origin', 'doubt', 'fault', 'gauge']) E('ledger', r);
  E('doubt', 'ledger');
  for (const r of ['origin', 'doubt', 'fault', 'gauge']) E(r, 'verdict');
  E('verdict', 'out:supported'); E('verdict', 'out:unresolved');
  E('verdict', 'out:actions'); E('verdict', 'out:questions');
}

function renderRail() {
  const p = D.pipeline, stats = [
    ['Pipeline', p.status],
    ['Model', (D.model || '').replace('meta/', '')],
    ['Claims', `${p.ledger.claims.length} · ${(((p.coverage_proof || {}).by_status) || {}).supported || 0} supported`],
    ['Events', String(p.replay.length)],
    ['Usage', `${((D.usage || {}).total_tokens || 0).toLocaleString()} tok`],
    ['Requests', `${(D.request_usage || {}).requests ?? '?'} / ${(D.request_usage || {}).request_limit ?? '?'}`],
    ['Elapsed', `${Math.round(p.elapsed_seconds || 0)} s`],
  ];
  $('rail-stats').innerHTML = stats.map(([k, v]) =>
    `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('');
}

/* ---------- graph ---------- */
let gRoot, gEdges, gNodes, gPackets, view = { x: -40, y: -20, w: 1300, h: 800 };
function renderGraph() {
  const svg = $('graph');
  svg.innerHTML = '';
  svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
  gRoot = el('g', {});
  gEdges = el('g', {}); gPackets = el('g', {}); gNodes = el('g', {});
  gRoot.append(gEdges, gPackets, gNodes);
  svg.append(gRoot);
  const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
  for (const e of edges) {
    const a = byId[e.a], b = byId[e.b];
    const mx = (a.x + b.x) / 2;
    const d = `M ${a.x} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x} ${b.y}`;
    const p = el('path', { d, class: 'edge' });
    p.dataset.a = e.a; p.dataset.b = e.b;
    gEdges.append(p);
    e.path = p;
  }
  for (const n of nodes) {
    const g = el('g', { class: 'node' + (n.status === 'idle' || n.status === 'bad' ? ' dim' : ''), tabindex: '0', role: 'button', 'aria-label': n.label });
    g.dataset.id = n.id;
    const c = STATUS_COLOR[n.status];
    g.append(el('circle', { cx: n.x, cy: n.y, r: n.r + 7, class: 'halo', stroke: c }));
    g.append(el('circle', { cx: n.x, cy: n.y, r: n.r, class: 'core', fill: '#0d1218', stroke: c }));
    const t1 = el('text', { x: n.x, y: n.y - 1, 'text-anchor': 'middle', class: 'cap' });
    t1.textContent = n.label;
    const t2 = el('text', { x: n.x, y: n.y + (n.count2 ? 10 : 14), 'text-anchor': 'middle', class: 'sub2' });
    t2.textContent = n.count;
    g.append(t1, t2);
    if (n.count2) {
      const t3 = el('text', { x: n.x, y: n.y + 23, 'text-anchor': 'middle', class: 'sub2' });
      t3.textContent = n.count2;
      g.append(t3);
    }
    g.addEventListener('click', () => select(n.id));
    g.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); select(n.id); } });
    gNodes.append(g);
    n.g = g;
  }
  wireView(svg);
}
function el(tag, attrs) {
  const e = document.createElementNS(svgNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
function wireView(svg) {
  let pan = null;
  const apply = () => svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
  const scale = () => view.w / svg.clientWidth;
  svg.addEventListener('wheel', e => {
    e.preventDefault();
    const f = e.deltaY > 0 ? 1.12 : 1 / 1.12;
    const nw = Math.min(2600, Math.max(400, view.w * f));
    const k = nw / view.w;
    const r = svg.getBoundingClientRect();
    const mx = view.x + (e.clientX - r.left) * scale();
    const my = view.y + (e.clientY - r.top) * scale();
    view.x = mx - (mx - view.x) * k; view.y = my - (my - view.y) * k; view.w = nw;
    view.h = view.w * r.height / r.width;
    apply();
  }, { passive: false });
  svg.addEventListener('pointerdown', e => { pan = { x: e.clientX, y: e.clientY, active: false, id: e.pointerId }; });
  svg.addEventListener('pointermove', e => {
    if (!pan) return;
    if (!pan.active) {
      if (Math.hypot(e.clientX - pan.x, e.clientY - pan.y) < 4) return;
      pan.active = true; svg.classList.add('panning');
      try { svg.setPointerCapture(pan.id); } catch (err) { /* no capture; drag still pans */ }
    }
    view.x -= (e.clientX - pan.x) * scale(); view.y -= (e.clientY - pan.y) * scale();
    pan = { x: e.clientX, y: e.clientY, active: true, id: pan.id }; apply();
  });
  const endPan = () => { pan = null; svg.classList.remove('panning'); };
  svg.addEventListener('pointerup', endPan);
  svg.addEventListener('pointercancel', endPan);
  $('zin').onclick = () => { view.w = Math.max(400, view.w / 1.25); view.h = view.h / 1.25; apply(); };
  $('zout').onclick = () => { view.w = Math.min(2600, view.w * 1.25); view.h = view.h * 1.25; apply(); };
  $('zreset').onclick = () => { view = { x: -40, y: -20, w: 1300, h: 800 }; apply(); };
}

/* ---------- replay ---------- */
function activeActors(seq) {
  const s = new Set();
  for (const e of D.pipeline.replay) {
    if (e.seq > seq) break;
    s.add(e.actor === 'vector_plan' ? 'vector_plan' : e.actor);
  }
  if (seq > 0) s.add('ledger');
  return s;
}
function setSeq(seq, silent) {
  seqCur = Math.max(0, Math.min(seqMax, seq));
  const act = activeActors(seqCur);
  for (const n of nodes) {
    const on = act.has(n.actor) && n.status !== 'idle';
    n.g.classList.toggle('active', on);
  }
  for (const e of edges) {
    const lit = act.has(edgeActor(e.a)) && act.has(edgeActor(e.b)) && seqCur > 0;
    e.path.classList.toggle('lit', lit);
  }
  $('scrub').max = seqMax;
  if (document.activeElement !== $('scrub')) $('scrub').value = seqCur;
  const cur = D.pipeline.replay[seqCur - 1];
  $('tick').textContent = seqCur === 0 ? 'start · no events yet'
    : `#${cur.seq} ${ACTORS[cur.actor] ? ACTORS[cur.actor][0] : cur.actor} · ${cur.title} — ${(cur.detail || '').slice(0, 90)}`;
  if (!silent && selected) select(selected, true);
}
function edgeActor(id) {
  if (id.startsWith('task:')) return 'vector';
  if (id.startsWith('out:')) return 'verdict';
  if (id === 'consults') return 'vector';
  return id;
}
function tick() {
  if (!playing) return;
  if (seqCur >= seqMax) { pause(); return; }
  const prev = activeActors(seqCur);
  setSeq(seqCur + 3);
  if (!reduced) {
    const now = activeActors(seqCur);
    for (const e of edges) {
      if (now.has(edgeActor(e.b)) && !prev.has(edgeActor(e.b)) && packets.length < 26) {
        packets.push({ path: e.path, t: 0, dot: el('circle', { r: 3.5, class: 'packet' }) });
        gPackets.append(packets[packets.length - 1].dot);
      }
    }
  }
  playTimer = setTimeout(tick, 120);
}
function play() {
  if (!D) return;
  if (seqCur >= seqMax) { packets = []; gPackets.innerHTML = ''; setSeq(0); }
  playing = true; $('play').textContent = '⏸'; $('play').setAttribute('aria-label', 'Pause replay');
  tick(); requestAnimationFrame(animate);
}
function pause() {
  playing = false; clearTimeout(playTimer);
  $('play').textContent = '▶'; $('play').setAttribute('aria-label', 'Play replay');
}
function animate() {
  if (!playing && packets.length === 0) return;
  const L = 260;
  packets = packets.filter(p => {
    p.t += 22 / L;
    if (p.t >= 1) { p.dot.remove(); return false; }
    try {
      const pt = p.path.getPointAtLength(p.t * p.path.getTotalLength());
      p.dot.setAttribute('cx', pt.x); p.dot.setAttribute('cy', pt.y);
    } catch (e) { p.dot.remove(); return false; }
    return true;
  });
  if (playing || packets.length) requestAnimationFrame(animate);
}

/* ---------- inspector ---------- */
function claimHtml(c) {
  const refs = [...(c.scope.components || []), ...(c.scope.nets || []).map(n => 'net:' + n)].slice(0, 14).join(' ');
  return `<div class="claim"><header><span class="id">${esc(c.id)}</span>` +
    `<span class="st">${esc(c.status)}${c.agent_sureness ? ' · ' + c.agent_sureness + '%' : ''}</span></header>` +
    `<div>${esc(c.statement)}</div>` +
    (refs ? `<div class="refs">${esc(refs)}</div>` : '') + `</div>`;
}
function eventHtml(e) {
  const c = e.claim_id ? claimsById[e.claim_id] : null;
  return `<div class="ev"><span class="seq">#${e.seq} · ${esc(e.kind)}${e.claim_id ? ' · ' + esc(e.claim_id) : ''}</span>` +
    `<p><strong style="color:var(--ink)">${esc(e.title)}</strong> — ${esc(e.detail || '')}</p>` +
    (c ? `<p>Claim: ${esc(c.statement.slice(0, 160))}</p>` : '') + `</div>`;
}
function select(id, keepSeq) {
  selected = id;
  for (const n of nodes) n.g.classList.toggle('selected', n.id === id);
  const n = nodes.find(n => n.id === id);
  const box = $('inspector');
  const pill = `<span class="pill ${n.status}">${esc(n.status)}</span>`;
  let h = `<h2>${esc(n.label)}</h2><p class="sub">${esc(n.sub)}</p>${pill}<span class="pill">${esc(n.count)}</span>`;
  if (n.kind === 'task') {
    const t = n.task;
    h += `<dl class="kv"><dt>Tool</dt><dd>${esc(t.tool)}</dd><dt>Status</dt><dd>${esc(t.status)}</dd>` +
      `<dt>Refs</dt><dd>${esc((t.references || []).join(' '))}</dd></dl>` +
      `<div class="claim"><div><strong>Question.</strong> ${esc(t.question)}</div>` +
      `<div style="margin-top:6px"><strong>Why.</strong> ${esc(t.reason || '')}</div></div>` +
      (t.limitations || []).map(l => `<div class="ev"><p>${esc(l)}</p></div>`).join('');
  } else if (n.kind === 'consults') {
    const sk = D.pipeline.replay.filter(e => e.kind === 'consult_skipped');
    h += `<p class="sub">No specialist consultation executed in this run. ${sk.length} scheduled consult${sk.length === 1 ? ' was' : 's were'} skipped.</p>` +
      sk.slice(0, 12).map(eventHtml).join('');
  } else if (n.id === 'ledger') {
    const cp = D.pipeline.coverage_proof;
    h += `<dl class="kv">` + Object.entries(cp.by_status).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('') +
      `</dl><p class="sub">Failure modes under investigation</p>` +
      `<dl class="kv">` + Object.entries(cp.by_mode).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('') + `</dl>`;
  } else if (n.kind === 'outcome') {
    const list = D.pipeline.ledger.claims.filter(c => c.status === n.outcome);
    h += `<p class="sub">${list.length} claims reached ${n.outcome}.</p>` + list.map(claimHtml).join('');
  } else if (n.kind === 'actions') {
    h += (D.result.actions || []).map(a =>
      `<div class="claim"><header><span class="id">${esc(a.group_id || '')}</span><span class="st">${esc(a.disposition || '')}</span></header>` +
      `<div>${esc(a.reason || '')}</div>` +
      `<div class="refs">${esc((a.steps || []).join(' › ').slice(0, 400))}</div></div>`).join('') ||
      `<p class="sub">No actions recorded.</p>`;
  } else if (n.kind === 'questions') {
    h += (D.result.questions || []).map(q =>
      `<div class="claim"><div>${esc(typeof q === 'string' ? q : (q.question || q.text || JSON.stringify(q)))}</div></div>`).join('') ||
      `<p class="sub">No open questions.</p>`;
  } else {
    const evs = actorEvents(n.actor).filter(e => e.seq <= seqCur);
    const cls = originClaims(n.actor === 'ledger' ? '' : n.actor);
    if (cls.length) h += `<p class="sub">${cls.length} originated claims</p>` + cls.map(claimHtml).join('');
    if (evs.length) h += `<p class="sub">${evs.length} recorded events (up to replay position)</p>` +
      evs.slice(-40).map(eventHtml).join('');
    if (!cls.length && !evs.length) h += `<p class="sub">No recorded claims or events for this stage in this run.</p>`;
  }
  box.innerHTML = h;
  box.scrollTop = 0;
}

$('play').onclick = () => playing ? pause() : play();
$('scrub').addEventListener('input', e => { pause(); if (!D) return; packets = []; gPackets.innerHTML = ''; setSeq(+e.target.value); });
// The main app embeds this same renderer in its Workflow tab and calls back here.
window.refreshCommandCenter = load;
load();
setInterval(async () => {  // live refresh only; replay fixture never changes
  if (!live || playing || document.hidden) return;
  try {
    const r = await fetch('/api/investigation');
    const j = await r.json();
    if (j && j.status === 'empty' && D && D.pipeline.status !== 'empty') {
      D = null; live = false; await load(); return;
    }
    if (j && j.pipeline && j.pipeline.replay.length !== seqMax) {
      D = j; claimsById = {};
      for (const c of D.pipeline.ledger.claims) claimsById[c.id] = c;
      seqMax = D.pipeline.replay.length; seqCur = seqMax;
      buildModel(); renderRail(); renderGraph(); setSeq(seqMax, true);
    }
  } catch (e) { /* keep showing last good state */ }
}, 15000);

})();
