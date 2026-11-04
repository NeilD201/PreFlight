/* PreFlight LiveScan workspace: breadboard projects, guided capture, intent,
   questions, findings and the 3D twin. Structured data lives in the
   Breadboard model; this file renders panels and talks to the backend. */
(function () {
'use strict';

let Booted = false;
// Build tag shown in the Add Photos panel footer. Bump on every user-facing
// frontend change so a stale cached page is visually obvious.
const BUILD = '2026-10-30r b1-rank+b2-anchor+b3-geowire+b4-settle+b5-savesafe+b6-sweepguard+b7-colorends+b8-partialreg+b9-watchdog';
const LS = {
  project: null, history: null, twin: null, backend: true, openSeq: 0,
  images: [], // {name, url, w, h, quality}
  selected: null, saveTimer: 0, stream: null, devices: [],
  rotation: 0, // camera orientation fix (0/90/180/270 clockwise)
};
// Draw img into ctx rotated clockwise by rot degrees. Size ctx.canvas to
// orientedSize(sw, sh, rot) first. One code path feeds the preview sample,
// still captures, and registration — so pose tracking always sees the
// same orientation the user sees.
function orientedSize(sw, sh, rot) {
  return (rot === 90 || rot === 270) ? [sh, sw] : [sw, sh];
}
function blitOriented(ctx, img, sw, sh, rot) {
  if (rot === 90) { ctx.translate(ctx.canvas.width, 0); ctx.rotate(Math.PI / 2); }
  else if (rot === 180) { ctx.translate(ctx.canvas.width, ctx.canvas.height); ctx.rotate(Math.PI); }
  else if (rot === 270) { ctx.translate(0, ctx.canvas.height); ctx.rotate(-Math.PI / 2); }
  ctx.drawImage(img, 0, 0, sw, sh);
}
window.LiveScanTest = { orientedSize, rotLum90, orientDecideQuad, quadToOverlay, guideBoxFor, updateTrackPose, registerStill, autoPlaceFromReg, captureAuto, finishCapture, remountTwin: mountTwin, fitGuideBox, ingestFile, reconstructAll, openQuizManual, openProjectById: async (id) => { const my = ++LS.openSeq; const doc = await api('/api/livescan/projects/' + encodeURIComponent(id)); if (my !== LS.openSeq) return null; openProject(doc); return doc; }, testInjectImage: (e) => { LS.images.push(e); LS.project.images.push({ name: e.name, w: e.w, h: e.h, captured: new Date().toISOString() }); renderPhotos(); }, testProject: () => LS.project, testQuizList: () => buildQuizList().map((q) => q.key), testProposeWireHoles: (id, holes, view) => BB().proposeWireHoles(LS.project, id, holes, view), testAddModule: (id) => BB().addComponent(LS.project, { id, kind: 'esp32devkit' }), testAnchorVote: (id, quad, w, h, rot, view, box) => voteModuleAnchor(id, quad, w, h, rot, view, box), testReconcile: (targets, records) => reconcilePhotoRecords(targets, records),
  testMarkDirty: () => markDirty(),
  testAnisotropy: (H) => lockAnisotropyOk(H),
  testValidateSave: (doc, urlId) => validateProjectForSave(doc, urlId),
  testSaveNow: () => saveNow(true),
  testEndsFromLock: (H, box, w, h) => projectEndsToHoles({ reg: { H }, w, h }, wireEndsFromBox(box, w, h) || [[-1, -1], [-1, -1]]),
  testUseProject: (p) => { LS.project = p; return !!LS.project; },
  testWirePlugs: (entry, sightings, moduleBox) => placeWirePlugs(entry, sightings, moduleBox),
  testWireColorEnds: (rgb, w, h, bbox, color) => SM().wireColorEnds(rgb, w, h, bbox, color),
  testModuleSpan: () => moduleSpanFromProject(),
  testForceSettle: () => forceSettleStragglers(),
  testArmWatch: (ids) => { SCAN.phase = 'reconstruct'; SCAN.settleWatchFor = { pid: LS.project && LS.project.id, ids }; },
  testGeoWire: (quad, box, w, h, rot) => {
      const rows = BB().rowsOf(LS.project), BW = BB().BOARD_WIDTH, BL = rows * BB().PITCH_MM + 12;
      const r = ((rot | 0) % 4 + 4) % 4;
      const base = [[-BW / 2, -BL / 2], [BW / 2, -BL / 2], [BW / 2, BL / 2], [-BW / 2, BL / 2]];
      const mm = [0, 1, 2, 3].map((j) => base[(((j - r) % 4) + 4) % 4]);
      const H = SM().homography(mm.map(([x, z]) => ({ x, y: z })), quad.map((p) => ({ x: p.x, y: p.y })));
      if (!H) return null;
      const ends = wireEndsFromBox(box, w, h);
      if (!ends) return null;
      return projectEndsToHoles({ reg: { H }, w, h }, ends);
    }, testState: () => ({ images: LS.images.length, components: LS.project.components.length, wires: (LS.project.wires || []).length }), testQuiz: () => { const q = QUIZ.list[QUIZ.idx]; return q ? { kind: q.kind, target: q.target } : null; }, testWires: () => (LS.project.wires || []).map((w) => ({ id: w.id, color: w.color })), startCamera, recoverCamera, startScan, stopScan, scanTick, setRotation: (r) => { LS.rotation = r; try { localStorage.setItem('ls-rotation', String(r)); } catch (_) {} applyRotationUI(); }, scanState: () => ({ step: SCAN.step, active: SCAN.active, recoveries: LS.recoveries || 0, rotation: LS.rotation || 0, pose: SCAN.poseState }) };
function applyRotationUI() {
  for (const id of ['ls-video', 'ls-track-video']) {
    const v = document.getElementById(id);
    if (v && v.style) v.style.transform = LS.rotation ? `rotate(${LS.rotation}deg)` : '';
  }
}
// Six guided views are the capture target (NOT proof of reconstruction):
// full board, left, right, left 45°, right 45°, far end 45°. Steps advance
// on sharp stable auto-captures; tapping the step pill confirms manually.
const STEPS = [
  { short: 'Full board', title: 'Hold directly above — show the whole board' },
  { short: 'Left', title: 'Left section — fill the view with the left half' },
  { short: 'Right', title: 'Right section — fill the view with the right half' },
  { short: 'Left 45°', title: 'Left side — tilt about 45° from the left' },
  { short: 'Right 45°', title: 'Right side — tilt about 45° from the right' },
  { short: 'End 45°', title: 'Far end — tilt about 45° from the opposite end' },
];
function stepAdvance(manual) {
  if (!SCAN.active || SCAN.step >= 6) return;
  SCAN.step++;
  refreshRecord(); updateProgress();
  if (SCAN.step >= 6 && !SCAN.finishing) {
    SCAN.finishing = true;
    scanSay('All six views captured ✓ — building your questions…');
    setTimeout(() => { if (SCAN.active) finishCapture(); }, 1500);
  } else if (!manual) {
    scanSay(`View ${SCAN.step} of 6 captured ✓ — next: ${STEPS[Math.min(SCAN.step, 5)].title}.`);
  }
}
// 90° clockwise luminance transpose (w,h -> h,w). Lets the lock attempt
// both sensor mountings without touching the user's frames.
function rotLum90(lum, w, h) {
  const out = new Float32Array(lum.length);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++)
    out[x * h + (h - 1 - y)] = lum[y * w + x];
  return { lum: out, w: h, h: w };
}
// Automatic orientation from the LOCKED QUAD's geometry: the board's long
// axis must lie horizontal in oriented space. Lock-contrast comparison was
// tried and rejected — the detector is rotation-invariant (verified: one
// real photo scores 103.8 at 0/90/180/270), so scores can never
// discriminate. A clearly portrait quad (w/h <= 1/1.3) with solid contrast
// rotates +90° after two consecutive confirmations. Square-ish tilted
// views keep the stable default; upside-down (symmetric) views are
// indistinguishable and also keep it. The first capture pins orientation.
const ORIENT_MIN = 60, ORIENT_PORTRAIT = 1.3, ORIENT_CONFIRM = 2;
function orientDecideQuad(quadWH, contrast, current, st, pinned) {
  if (pinned) { st.orientWins = 0; return current; }
  const target = (current + 90) % 360;
  if (contrast >= ORIENT_MIN && quadWH <= 1 / ORIENT_PORTRAIT) {
    if (st.orientTarget !== target) { st.orientTarget = target; st.orientWins = 0; }
    st.orientWins = (st.orientWins || 0) + 1;
    if (st.orientWins >= ORIENT_CONFIRM) { st.orientWins = 0; return target; }
  } else {
    st.orientWins = 0;
  }
  return current;
}
// Returns true when the rotation changed (sample space is new; the caller
// must re-lock next tick instead of storing old-space corners).
function autoOrientFromLock(hyp) {
  if (!hyp || !hyp.corners || hyp.corners.length < 4) return false;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of hyp.corners) {
    x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y);
    x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y);
  }
  const qw = x1 - x0, qh = y1 - y0;
  if (qw < 1 || qh < 1) return false;
  const next = orientDecideQuad(qw / qh, hyp.contrast || 0, LS.rotation || 0,
    SCAN, (SCAN.step || 0) !== 0);
  if (next === (LS.rotation || 0)) return false;
  LS.rotation = next;
  try { localStorage.setItem('ls-rotation', String(next)); } catch (_) { /* private mode */ }
  applyRotationUI();
  // Sample space changed: drop the track and the seconds-old coverage
  // grid; captures, steps and progress are untouched (none exist yet).
  SCAN.lastHyp = null; SCAN.lastHypSize = null; SCAN.poseState = 'hold';
  if (SCAN.trackFilter) SCAN.trackFilter.reset();
  if (SM()) SCAN.coverage = SM().newCoverage(6, 4);
  return true;
}

function BB() { return globalThis.Breadboard; }
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

window.initializeLiveScan = function () {
  if (Booted) { renderAll(); return; }
  Booted = true;
  renderShell();
  loadProjectList().then(renderProjectPicker).catch((e) => {
    LS.backend = false;
    renderProjectPicker([]);
    showError(new Error('LiveScan saving needs the latest app server. Capture and 3D editing still work locally.'));
  });
};

/* ---------- shell ---------- */
function renderShell() {
  const root = document.getElementById('livescan-root');
  root.innerHTML = '';
  const bar = node('div', 'ls-top');
  const back = node('button', 'secondary', '← All breadboard projects');
  back.type = 'button'; back.id = 'ls-back';
  back.addEventListener('click', () => { stopCamera(); LS.project = null; renderAll(); });
  const title = node('div', 'ls-title');
  title.append(node('strong', '', ''), node('span', 'muted', ''));
  title.firstChild.id = 'ls-proj-name'; title.lastChild.id = 'ls-proj-meta';
  const actions = node('div', 'ls-actions');
  const undo = node('button', 'secondary', 'Undo'); undo.type = 'button'; undo.id = 'ls-undo';
  const redo = node('button', 'secondary', 'Redo'); redo.type = 'button'; redo.id = 'ls-redo';
  const save = node('button', 'primary', 'Save'); save.type = 'button'; save.id = 'ls-save';
  const dirty = node('span', 'muted', ''); dirty.id = 'ls-dirty';
  undo.addEventListener('click', () => doUndo(false));
  redo.addEventListener('click', () => doUndo(true));
  save.addEventListener('click', () => saveNow());
  actions.append(undo, redo, save, dirty);
  bar.append(back, title, actions);
  const grid = node('div', 'ls-grid');
  grid.id = 'ls-grid';
  root.append(bar, grid);
}

function renderAll() {
  if (!LS.project) { renderProjectPicker(LS.projectList || null); return; }
  document.getElementById('ls-proj-name').textContent = LS.project.name;
  updateMeta();
  renderWorkspace();
}

function updateMeta() {
  if (!LS.project) return;
  const n = LS.project;
  document.getElementById('ls-proj-meta').textContent =
    `${n.components.length} parts · ${n.wires.length} wires · ${n.images.length} photos`;
  document.getElementById('ls-dirty').textContent = LS.dirty ? 'Unsaved changes' : LS.backend ? '' : 'Local only (restart the app server to save)';
}

/* ---------- project picker ---------- */
async function loadProjectList() {
  const res = await api('/api/livescan/projects');
  LS.projectList = res.projects || [];
  return LS.projectList;
}

function renderProjectPicker(list) {
  const grid = document.getElementById('ls-grid');
  grid.innerHTML = '';
  document.getElementById('ls-proj-name').textContent = 'Breadboard projects';
  document.getElementById('ls-proj-meta').textContent = '';
  const wrap = node('div', 'ls-picker');
  const intro = node('p', 'muted', 'Scan a physical breadboard, rebuild it as an interactive 3D twin, and verify the wiring before it becomes a PCB.');
  wrap.append(intro);
  const form = node('form', 'panel ls-new');
  form.innerHTML = `<h3>Start a breadboard project</h3>
    <label>Project name<input id="ls-new-name" maxlength="120" placeholder="e.g. Door sensor prototype"></label>
    <label>Breadboard size<select id="ls-new-size"><option value="full">Full-size (830 tie-points)</option><option value="half">Half-size (400 tie-points)</option></select></label>
    <div><button class="primary" type="submit">Create project</button></div>`;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = form.querySelector('#ls-new-name').value.trim() || 'Breadboard build';
    const size = form.querySelector('#ls-new-size').value;
    try {
      const my = ++LS.openSeq;
      const project = await api('/api/livescan/projects', { name, size });
      if (my !== LS.openSeq) return; // a newer open won while creating; drop this stale result
      openProject(project);
    } catch (err) {
      // Offline fallback: local-only project until the server restarts.
      LS.backend = false;
      openProject(BB().newProject(name, { size }));
      showError(new Error('Server unreachable — working locally. Restart the app to save.'));
    }
  });
  wrap.append(form);
  if (list === null) {
    wrap.append(node('p', 'muted', 'Loading projects…'));
  } else if (!list.length) {
    wrap.append(node('p', 'muted', 'No breadboard projects yet.'));
  } else {
    const cards = node('div', 'ls-cards');
    for (const p of list) {
      const card = node('button', 'ls-card', '');
      card.type = 'button';
      card.innerHTML = `<strong>${esc(p.name)}</strong><span class="muted">${p.components} parts · ${p.wires} wires · ${p.images} photos</span><span class="muted">Updated ${esc((p.updated || '').slice(0, 10))}</span>`;
      card.addEventListener('click', async () => {
        const my = ++LS.openSeq;
        try {
          const doc = await api('/api/livescan/projects/' + encodeURIComponent(p.id));
          if (my !== LS.openSeq) return; // a newer open won; never clobber it with this stale response
          openProject(doc);
        }
        catch (err) { showError(err); }
      });
      cards.append(card);
    }
    wrap.append(cards);
  }
  grid.append(wrap);
}

function openProject(project) {
  stopCamera();
  // Same-name records (one photo uploaded twice) collapse to a single slot:
  // keep the most-progressed record so a stale twin can never wedge the
  // settle gate at 6/7 with no failures. Persists on the next save.
  {
    const seen = new Map();
    const score = (x) => (x && x.interpreted ? 2 : x && x.failed ? 1 : 0);
    for (const im of project.images || []) {
      const cur = seen.get(im.name);
      if (!cur || score(im) > score(cur)) seen.set(im.name, im);
    }
    project.images = [...seen.values()];
  }
  LS.project = project;
  LS.history = BB().makeHistory(project);
  LS.images = (project.images || []).map((im) => ({
    name: im.name, url: LS.backend ? `/api/livescan/projects/${encodeURIComponent(project.id)}/images/${encodeURIComponent(im.name)}` : null,
    w: im.w || 0, h: im.h || 0, quality: im.quality || null,
    interpreted: !!im.interpreted, failed: !!im.failed, failMsg: im.failMsg || '', obsCount: im.obsCount || 0,
  }));
  LS.selected = null; LS.dirty = false;
  // Merge rule-based questions with persisted ones.
  for (const q of BB().suggestQuestions(project)) {
    if (!project.questions.some((x) => x.key === q.key && x.status === 'open')) project.questions.push(q);
  }
  renderAll();
}

/* ---------- workspace ---------- */
function renderWorkspace() {
  const grid = document.getElementById('ls-grid');
  grid.innerHTML = '';
  const left = node('div', 'ls-col');
  const right = node('div', 'ls-col');
  // intentPanel() is intentionally unmounted: the popup quiz asks the
  // purpose question directly, and the report shows the saved answer.
  left.append(investigationPanel(), capturePanel());
  right.append(twinPanel(), reportPanel(), partsPanel(), wirePanel(), questionPanel(), findingPanel());
  grid.append(left, right);
  mountTwin();
  refreshInvestigation();
  refreshReport();
  refreshQuestions();
  refreshFindings();
  renderParts();
  renderPhotos();
  // Paint after attach: getElementById cannot see the detached panel tree.
  drawCoverage();
  updateProgress();
}

/* ----- investigation: structured understanding, not a status box ----- */
function investigationPanel() {
  const box = node('section', 'panel');
  box.id = 'ls-investigate';
  box.innerHTML = '<h3>Circuit investigation</h3>';
  const body = node('div', 'ls-list');
  body.id = 'ls-investigate-body';
  box.append(body);
  return box;
}

function refreshInvestigation() {
  const body = document.getElementById('ls-investigate-body');
  if (!body || !LS.project || !BB()) return;
  const rep = BB().buildReport(LS.project);
  const prog = BB().reportProgress(LS.project);
  const known = rep.components.filter((c) => c.status !== 'unknown');
  const unk = rep.components.filter((c) => c.status === 'unknown');
  const func = rep.intent.text || rep.intent.purposes.map((p) => `${p.id}: ${p.purpose}`).join('; ');
  const top = rep.evidence.missing.slice(0, 3);
  body.innerHTML =
    `<p><strong>${esc(rep.identity.status === 'resolved' ? 'Investigation complete' : rep.identity.status === 'investigating' ? 'Investigating…' : 'Upload photos to begin')}</strong>` +
    ` <span class="muted">${prog.resolved} of ${prog.total} facts resolved · ${rep.identity.openQuestions} open questions</span></p>` +
    `<p>Components: ${known.length} identified${unk.length ? ` · <strong>${unk.length} unknown (${unk.map((c) => esc(c.id)).join(', ')})</strong>` : ' · none unknown'}</p>` +
    `<p>Function: ${func ? esc(func.length > 140 ? func.slice(0, 140) + '…' : func) : '<span class="muted">not established yet</span>'}</p>` +
    (top.length ? `<p class="muted">Needs input: ${top.map((q) => esc(q.text.length > 90 ? q.text.slice(0, 90) + '…' : q.text)).join(' — ')}</p>`
      : '<p class="muted">Nothing needs your input right now.</p>');
}

function reportPanel() {
  const det = document.createElement('details');
  det.className = 'panel';
  det.id = 'ls-report';
  const sum = document.createElement('summary');
  sum.innerHTML = '<strong>Project Report</strong> <span class="muted">internal engineering record</span>';
  const body = node('div', 'ls-list');
  body.id = 'ls-report-body';
  det.append(sum, body);
  det.addEventListener('toggle', () => { if (det.open) refreshReport(); });
  return det;
}

function refreshReport() {
  const body = document.getElementById('ls-report-body');
  if (!body || !LS.project || !BB()) return;
  const r = BB().buildReport(LS.project);
  const li = (x) => `<li>${x}</li>`;
  const compLines = r.components.map((c) =>
    `${esc(c.id)} — ${esc(c.label)} · ${c.status}` +
    (c.mpn ? ` · MPN ${esc(c.mpn)}` : '') +
    (c.purpose ? ` · “${esc(c.purpose.slice(0, 60))}”` : '') +
    ` · pins ${c.pinsPlaced}/${c.pinsTotal}` +
    (c.sightings.length ? ` · seen in ${c.sightings.map(esc).join(', ')}` : '')).map(li).join('');
  const netLines = r.electrical.nets.slice(0, 24).map((n) =>
    `${n.holes.slice(0, 8).map(esc).join(', ')}${n.holes.length > 8 ? ` +${n.holes.length - 8}` : ''} (${n.certainty ? 'inferred' : 'confirmed physics'})`).map(li).join('');
  const histLines = r.history.slice(-12).map((h) =>
    `${esc(h.key)} → “${esc(h.answer.slice(0, 80))}”${h.deferred ? ' (unknown kept)' : ''}`).map(li).join('');
  body.innerHTML =
    `<p class="muted">Revision ${r.revision} · generated ${esc(r.generated.slice(0, 19).replace('T', ' '))} · inferred items are labeled, never stated as fact.</p>` +
    `<h4>Identity</h4><p>${esc(r.identity.name)} — ${r.identity.purpose ? esc(r.identity.purpose) : '<span class="muted">purpose not yet described</span>'} (${esc(r.identity.status)})</p>` +
    `<h4>Components (${r.components.length}${r.unknownComponents.length ? `, ${r.unknownComponents.length} unknown` : ''})</h4><ul>${compLines || '<li>none yet</li>'}</ul>` +
    `<h4>Electrical (${r.electrical.nets.length} nets, ${r.electrical.confirmedConnections} connected wires, ${r.electrical.unresolvedEnds} loose ends)</h4><ul>${netLines || '<li>no nets yet</li>'}</ul>` +
    `<h4>Physical</h4><p>${esc(r.physical.size)} board · ${r.physical.rows} rows · ${r.physical.images.length} photos (${r.physical.images.map(esc).join(', ') || 'none'})${r.physical.offBoard.length ? ` · off-board: ${r.physical.offBoard.map(esc).join(', ')}` : ''}</p>` +
    (r.history.length ? `<h4>History</h4><ul>${histLines}</ul>` : '') +
    (r.evidence.missing.length ? `<h4>Still missing (${r.evidence.missing.length})</h4><ul>${r.evidence.missing.slice(0, 8).map((m) => esc(m.text)).map(li).join('')}</ul>` : '');
}

function intentPanel() {
  const box = node('section', 'panel');
  box.innerHTML = `<h3>Design intent</h3><p class="field-help">What are you building, and what is it supposed to do?</p>`;
  const ta = node('textarea');
  ta.rows = 3; ta.maxLength = 2000; ta.placeholder = 'e.g. A smart door sensor with an ESP32. It should detect when a door opens and send a notification.';
  ta.value = LS.project.intent.text || '';
  ta.setAttribute('aria-label', 'What are you building, and what is it supposed to do?');
  const save = node('button', 'secondary', 'Save intent');
  save.type = 'button';
  const status = node('span', 'muted', LS.project.intent.updated ? 'Saved ' + LS.project.intent.updated.slice(0, 10) : '');
  save.addEventListener('click', () => {
    checkpoint();
    LS.project.intent.text = ta.value.trim();
    LS.project.intent.updated = new Date().toISOString();
    // Answer any open intent question with the same text.
    for (const q of LS.project.questions) {
      if (q.status === 'open' && q.kind === 'intent') BB().answerQuestion(LS.project, q.id, ta.value.trim());
    }
    markDirty();
    status.textContent = 'Saved just now';
    refreshQuestions();
  });
  const row = node('div', 'ls-row'); row.append(save, status);
  box.append(ta, row);
  return box;
}

/* ----- capture: camera preview, stills, uploads, local quality signals ----- */
function capturePanel() {
  const box = node('section', 'panel');
  box.id = 'ls-capture';
  box.innerHTML = `<h3>Add photos</h3>
    <p class="field-help">Upload breadboard photos — overhead plus close-ups of dense areas. Each photo is analyzed, merged into the 3D reconstruction, and folded into the investigation below.</p>`;
  const video = document.createElement('video');
  video.id = 'ls-video'; video.playsInline = true; video.muted = true;
  video.setAttribute('aria-label', 'Camera preview');
  const canvas = document.createElement('canvas');
  canvas.id = 'ls-frame'; canvas.hidden = true;
  const controls = node('div', 'ls-row');
  const devSel = node('select'); devSel.id = 'ls-camera-sel'; devSel.setAttribute('aria-label', 'Camera');
  const startBtn = node('button', 'secondary', 'Start camera'); startBtn.type = 'button';
  const snapBtn = node('button', 'primary', 'Capture still'); snapBtn.type = 'button'; snapBtn.disabled = true; snapBtn.id = 'ls-snap';
  const upInput = document.createElement('input');
  upInput.type = 'file'; upInput.accept = 'image/png,image/jpeg,image/webp'; upInput.multiple = true;
  upInput.hidden = true;
  upInput.addEventListener('change', () => { for (const f of upInput.files) ingestFile(f); upInput.value = ''; });
  startBtn.addEventListener('click', () => startCamera(devSel.value));
  snapBtn.addEventListener('click', captureStill);
  const stopBtn = node('button', 'secondary', 'Stop scan'); stopBtn.type = 'button';
  stopBtn.title = 'Abort the scan loop and clear tracking.';
  stopBtn.addEventListener('click', () => stopScan());
  controls.append(devSel, startBtn, snapBtn, stopBtn);
  // Primary photo workflow (always visible): Add Photos first, dropzone,
  // then Reconstruct. Camera stays behind the disclosure below.
  const primary = node('div', 'ls-row');
  const addBtn = node('button', 'primary', 'Add Photos'); addBtn.type = 'button'; addBtn.id = 'ls-add-photos';
  addBtn.addEventListener('click', () => upInput.click());
  const recBtn = node('button', 'primary', 'Reconstruct Breadboard'); recBtn.type = 'button'; recBtn.id = 'ls-reconstruct';
  recBtn.addEventListener('click', () => reconstructAll());
  const ansBtn = node('button', 'secondary', '❓ Answer questions'); ansBtn.type = 'button'; ansBtn.id = 'ls-answer';
  ansBtn.title = 'Open the question popups — answers route wires and place parts immediately.';
  ansBtn.addEventListener('click', () => openQuizManual());
  ansBtn.hidden = true;
  primary.append(addBtn, recBtn, ansBtn, upInput);
  const drop = node('div', 'dropzone'); drop.id = 'ls-dropzone';
  drop.innerHTML = `<span class="upload-icon">▤</span><strong>Drop breadboard photos here</strong><span>PNG, JPEG or WebP — overhead plus close-ups</span>`;
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('dragover'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault(); drop.classList.remove('dragover');
    const files = [...(e.dataTransfer ? e.dataTransfer.files : [])].filter((f) => /^image\//.test(f.type));
    if (!files.length) { showError(new Error('Drop image files (PNG, JPEG or WebP).')); return; }
    files.forEach((f) => ingestFile(f));
  });
  const prog = node('p', 'muted'); prog.id = 'ls-rec-progress';
  const photos = node('div'); photos.id = 'ls-photos';
  const buildTag = node('p', 'muted'); buildTag.id = 'ls-build';
  buildTag.textContent = `LiveScan build ${BUILD} — reload the page (Cmd+R) if this line is missing or older than the announced build.`;
  box.append(primary, drop, photos, prog, buildTag);
  try { LS.rotation = Number(localStorage.getItem('ls-rotation')) || 0; } catch (_) { LS.rotation = 0; }
  if (![0, 90, 180, 270].includes(LS.rotation)) LS.rotation = 0;
  applyRotationUI();
  // Board-placement checklist: where to put the board for each of the 6
  // views. Current step highlighted; steps advance on saved captures only.
  const PLACE = [
    'Center the whole board — all edges visible',
    'Board left half fills the view',
    'Board right half fills the view',
    'Tilt phone left ~45°, board centered',
    'Tilt phone right ~45°, board centered',
    'Tilt from the far end ~45°, board centered',
  ];
  const stepsBox = node('ol', 'ls-steps');
  stepsBox.id = 'ls-steps';
  STEPS.forEach((s, i) => {
    const li = node('li', '', `${s.short} — ${PLACE[i]}`);
    stepsBox.append(li);
  });
  const covWrap = node('div', 'ls-cov');
  const cov = document.createElement('canvas');
  cov.id = 'ls-coverage'; cov.width = 120; cov.height = 80;
  cov.setAttribute('aria-label', 'Scan coverage map');
  cov.title = 'Green cells were captured sharply; outlined cell needs attention.';
  covWrap.append(cov, node('span', 'muted', 'Coverage'));
  const guide = node('div', 'ls-guide'); guide.id = 'ls-guide';
  guide.append(node('p', 'muted', 'Camera off. Upload photos above or start the camera.'));
  // Live camera and the 6-view checklist stay available behind a compact
  // disclosure; the photo workflow above is the primary interface.
  const adv = document.createElement('details');
  adv.id = 'ls-advanced';
  const sum = document.createElement('summary');
  sum.textContent = 'Camera & manual capture';
  adv.append(sum, stepsBox, video, canvas, controls, covWrap, guide);
  box.append(adv);
  listCameras(devSel);
  return box;
}

function preferContinuity(devices) {
  // macOS exposes the iPhone as a Continuity Camera video input (USB alone
  // does not guarantee this — labels only appear after permission).
  return devices.find((d) => /continuity|iphone/i.test(d.label || ''))
    || devices.find((d) => /usb|external/i.test(d.label || ''))
    || devices[0] || null;
}
async function listCameras(sel) {
  sel.innerHTML = '';
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput');
    LS.devices = devices;
    if (!devices.length) sel.append(node('option', '', 'No camera found'));
    devices.forEach((d, i) => {
      const o = node('option', '', d.label || `Camera ${i + 1}`);
      o.value = d.deviceId;
      sel.append(o);
    });
    // Auto-prefer iPhone Continuity Camera; keep an explicit user choice.
    // A previously chosen device that vanished (unplugged iPhone) falls
    // back to a fresh pick so the next start does not aim at a ghost.
    const pick = preferContinuity(devices);
    if (pick && (!sel.value || !devices.some((d) => d.deviceId === sel.value))) {
      sel.value = pick.deviceId; LS.deviceId = pick.deviceId;
    }
    if (navigator.mediaDevices && !LS.watchSetup) {
      LS.watchSetup = true;
      try {
        navigator.mediaDevices.ondevicechange = () => {
          listCameras(document.getElementById('ls-camera-sel'));
          recoverCamera();
        };
      } catch (_) { /* older browsers */ }
    }
  } catch (e) { /* labels need permission; selection still works */ }
}
// Reacquire after unplug/sleep/track-death WITHOUT losing coverage or
// progress: the camera restarts, the scan loop resumes preserving state.
// Bounded: after 3 consecutive failures the scan parks with guidance and
// the user resumes with Record (progress is never discarded).
async function recoverCamera(reason) {
  if (!SCAN.active || LS.recovering) return;
  LS.recovering = true;
  try {
    LS.recoveries = (LS.recoveries || 0) + 1;
    if (LS.recoveries > 3) {
      stopScan(true);
      setScanStatus('Camera lost', 'bad');
      scanSay('The camera keeps dropping — your progress and photos are kept. '
        + 'Wake the iPhone, keep it nearby on the same Wi-Fi with Bluetooth on, then press Record to resume.');
      return;
    }
    setScanStatus('Reconnecting', 'live');
    scanSay((reason ? reason + ' ' : '') + `Reconnecting the camera (${LS.recoveries}/3) — progress is kept…`);
    clearInterval(SCAN.timer); SCAN.timer = 0;
    // Drop the dead stream without touching scan state (coverage, steps,
    // captures all survive).
    if (LS.stream) {
      try { LS.stream.getTracks().forEach((t) => t.stop()); } catch (_) {}
      LS.stream = null;
    }
    const dead = document.getElementById('ls-video');
    if (dead) dead.srcObject = null;
    await startCamera(LS.deviceId);
    const video = document.getElementById('ls-video');
    if (video && video.videoWidth) {
      startScan(true);
    } else {
      // Stay active so the tick keeps driving bounded retries; restart a
      // bare timer since the stop above cleared it.
      SCAN.active = true; SCAN.paused = false;
      setScanStatus('Reconnecting', 'live');
      clearInterval(SCAN.timer);
      SCAN.timer = setInterval(scanTick, 1200);
    }
  } finally { LS.recovering = false; }
}

async function startCamera(deviceId, attempt) {
  stopCamera();
  attempt = attempt || 1;
  deviceId = deviceId || LS.deviceId || '';
  LS.deviceId = deviceId;
  const video = document.getElementById('ls-video');
  const guide = document.getElementById('ls-guide');
  const say = (t) => { const vp = document.getElementById('ls-vp-guide'); if (vp) vp.textContent = t; };
  const fail = (why) => {
    guide.replaceChildren(node('p', 'error', 'Camera unavailable: ' + why + ' Upload photos instead.'));
    say('Camera unavailable: ' + why);
    setScanStatus('No camera', 'bad');
  };
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      const err = new Error('This browser does not expose cameras.');
      err.name = 'NotSupportedError';
      throw err;
    }
    const constraints = { video: deviceId ? { deviceId: { exact: deviceId } } : { facingMode: 'environment', width: { ideal: 1920 } }, audio: false };
    guide.replaceChildren(node('p', '', 'Waiting for camera permission… Accept the browser prompt, or upload photos instead.'));
    say('Waiting for camera permission…');
    // Never hang: an undecided permission prompt leaves the promise pending forever.
    LS.stream = await Promise.race([
      navigator.mediaDevices.getUserMedia(constraints),
      new Promise((_, reject) => setTimeout(() => reject(new Error('the camera did not respond within 12 seconds (check the browser permission prompt)')), 12000)),
    ]);
    video.srcObject = LS.stream;
    try {
      await video.play();
    } catch (e) {
      const err = new Error('the video element could not play (' + ((e && e.message) || 'play failed') + ')');
      err.name = 'PlayFailed';
      throw err;
    }
    // Dead-track watchdog: Continuity links die silently (sleep, Wi-Fi
    // drop, another app grabbing the phone). The ended event restarts
    // acquisition; the scan tick separately notices frozen frames.
    try {
      for (const t of LS.stream.getTracks()) t.onended = () => recoverCamera('The camera stream ended.');
    } catch (_) { /* older browsers */ }
    LS.recoveries = 0;
    try {
      const trk = LS.stream.getVideoTracks()[0];
      LS.facing = (trk && trk.getSettings && trk.getSettings().facingMode) || '';
    } catch (_) { LS.facing = ''; }
    document.getElementById('ls-snap').disabled = false;
    guide.replaceChildren(node('p', '', 'Aim at the breadboard. Capture a full-board view first, then close-ups of markings and wiring.'
      + (LS.facing === 'user' ? ' Front camera active — the rear camera scans more naturally.' : '')));
    say('Aim at the breadboard, then press Record to scan.');
    listCameras(document.getElementById('ls-camera-sel'));
  } catch (e) {
    // Never leave a half-acquired stream holding the camera open: that
    // would make PreFlight itself the competing application.
    if (LS.stream) {
      try { LS.stream.getTracks().forEach((t) => t.stop()); } catch (_) {}
      LS.stream = null;
    }
    if (video) video.srcObject = null;
    const name = (e && e.name) || '';
    // Stale exact deviceId (Continuity IDs change across reconnects):
    // re-enumerate once and retry with a fresh pick.
    if ((name === 'OverconstrainedError' || name === 'NotFoundError') && deviceId && attempt < 2) {
      try {
        const devs = ((await navigator.mediaDevices.enumerateDevices()) || []).filter((d) => d.kind === 'videoinput');
        LS.devices = devs;
        const pick = preferContinuity(devs);
        if (pick) {
          const sel = document.getElementById('ls-camera-sel');
          if (sel) sel.value = pick.deviceId;
          return startCamera(pick.deviceId, attempt + 1);
        }
      } catch (_) { /* fall through to failure */ }
    }
    if (name === 'NotReadableError' || name === 'TrackStartError') {
      fail('another app (FaceTime, Zoom, Teams, or another browser tab) is using the camera — quit it and press Record again');
      return;
    }
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      fail('camera permission was denied — allow it in the browser address bar, then press Record');
      return;
    }
    const none = LS.devices && !LS.devices.length;
    const why = none ? 'macOS has not made a camera available — keep the iPhone nearby and awake with Wi-Fi and Bluetooth on, the same Apple Account on both, and Continuity Camera on (System Settings → General → AirDrop & Handoff), then press Record'
      : ((e && e.message) || 'unknown camera error');
    fail(why);
  }
}

function stopCamera() {
  stopScan(true);
  if (LS.stream) { LS.stream.getTracks().forEach((t) => t.stop()); LS.stream = null; }
  const video = document.getElementById('ls-video');
  if (video) video.srcObject = null;
}

function grabFrameCanvas(maxDim) {
  const video = document.getElementById('ls-video');
  if (!video || !video.videoWidth) return null;
  const rot = LS.rotation || 0;
  const canvas = document.getElementById('ls-frame');
  const scale = Math.min(1, maxDim / Math.max(video.videoWidth, video.videoHeight));
  const sw = Math.max(2, Math.round(video.videoWidth * scale));
  const sh = Math.max(2, Math.round(video.videoHeight * scale));
  const [w, h] = orientedSize(sw, sh, rot);
  canvas.width = w; canvas.height = h;
  blitOriented(canvas.getContext('2d'), video, sw, sh, rot);
  return canvas;
}
async function captureStill() {
  const canvas = grabFrameCanvas(2560);
  if (!canvas) return;
  const blob = await new Promise((r) => canvas.toBlob(r, 'image/jpeg', 0.92));
  if (!blob) return;
  const entry = await ingestFile(new File([blob], `scan-${Date.now()}.jpg`, { type: 'image/jpeg' }));
  attachRegistration(entry, canvas);
}
// Still-only board lock at 480px-equivalent resolution (the geometry is
// calibrated for scan res). Returns {corners, rot} in canvas pixels or null.
function lockStillLum(canvas) {
  if (!canvas || !canvas.width || !LS.project || !SM()) return null;
  const rows = BB().rowsOf(LS.project);
  const BW = BB().BOARD_WIDTH, BL = rows * BB().PITCH_MM + 12;
  const sw = 480, sh = Math.max(60, Math.round((canvas.height * sw) / canvas.width));
  const sc = document.createElement('canvas');
  sc.width = sw; sc.height = sh;
  const sctx = sc.getContext('2d', { willReadFrequently: true });
  if (!sctx) return null;
  sctx.drawImage(canvas, 0, 0, sw, sh);
  const slum = SM().luminance(sctx.getImageData(0, 0, sw, sh).data, sw, sh);
  // Non-finite samples (broken canvas impls) can never lock; bail fast.
  if (!isFinite(slum[0]) || !isFinite(slum[slum.length >> 1]) || !isFinite(slum[slum.length - 1])) return null;
  const still = SM().estimateBoardPose(slum, sw, sh, BW, BL,
    { verify: { lumHi: slum, wHi: sw, hHi: sh } });
  if (!still.length) return null;
  const kq = canvas.width / sw;
  return { corners: still[0].corners.map((p) => ({ x: p.x * kq, y: p.y * kq })), rot: still[0].rot || 0 };
}
// Verify a canvas-px quad against the hole lattice and build the
// registration (homography maps board mm to canvas pixels). The shared
// ScanMath core runs the identical math in the browser and in evaluation.
function buildReg(canvas, quadCanvasPx, rot, tracked) {
  if (!canvas || !quadCanvasPx || !LS.project || !SM() || !BB()) return null;
  const rows = BB().rowsOf(LS.project);
  const maxW = 960, k0 = Math.min(1, maxW / canvas.width);
  const w = Math.max(80, Math.round(canvas.width * k0));
  const h = Math.max(60, Math.round(canvas.height * k0));
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  if (!ctx) return null;
  // No rotation here: callers pass already-oriented pixels (single source
  // of truth, never double-rotated).
  ctx.drawImage(canvas, 0, 0, w, h);
  const lum = SM().luminance(ctx.getImageData(0, 0, w, h).data, w, h);
  const ks = w / canvas.width;
  const quad = quadCanvasPx.map((p) => ({ x: p.x * ks, y: p.y * ks }));
  // NOTE: snapQuadToBoardEdges is intentionally NOT wired in here. It helps
  // coarse locks on synthetic boards, but on real tilted views the snapped
  // quad verified an aliased fit (span-61 module vote; overlay-proven drift),
  // converting an honest abstention into a confident misplacement. Re-wire
  // only with an alias guard the lattice contrast cannot provide alone.
  // Rotation sweep: the structural rot (groove/edge evidence) is blind to
  // 180-degree flips and can disagree with the lattice. Try the preferred
  // rot first, then its flip, then the rest — each through the UNCHANGED
  // lattice gate (contrast + edge-ratio). First verified lock wins, so
  // behavior is identical whenever the preferred rot verifies; the extra
  // tries only run on views that would otherwise abstain. Never forced.
  // Alias guard: a 90-degree lattice alias maps the square hole grid onto
  // itself locally and can pass contrast while stretching one board axis an
  // order of magnitude more than the other (observed: 10.6x on a real
  // close-up, overlay-proven wrong). True perspective — even steep — keeps
  // the axis scales within a small factor (verified locks measure <=1.3).
  // Bound of 4 rejects the proven alias class with wide margin.
  const geom = { BW: BB().BOARD_WIDTH, BL: rows * BB().PITCH_MM + 12, rows,
    holeXY: (id) => BB().holeXY(LS.project, id), PITCH: BB().PITCH_MM };
  const rotOrder = [];
  for (const r of [rot, (rot + 2) % 4, (rot + 1) % 4, (rot + 3) % 4])
    if (!rotOrder.includes(r)) rotOrder.push(r);
  let reg = null;
  for (const r of rotOrder) {
    const cand = SM().registerBoard(lum, w, h, quad, r, geom, canvas.width / w);
    if (cand && lockAnisotropyOk(cand.H)) { reg = cand; break; }
  }
  if (!reg) return null;
  return { H: reg.H, iw: canvas.width, ih: canvas.height, conf: reg.conf, tracked: !!tracked };
}
// Perspective-plausibility bound for a board homography (flat row-major 3x3).
// Compares axis scales at board center; true fits score near 1.
function lockAnisotropyOk(H) {
  if (!Array.isArray(H) || H.length < 9) return false;
  const w = H[8] || 1;
  const ax = (H[0] - H[6] * H[2] / w) / w, ay = (H[1] - H[7] * H[2] / w) / w;
  const zx = (H[3] - H[6] * H[5] / w) / w, zy = (H[4] - H[7] * H[5] / w) / w;
  const sx = Math.hypot(ax, ay), sz = Math.hypot(zx, zy);
  if (!isFinite(sx) || !isFinite(sz) || sx <= 0 || sz <= 0) return false;
  return Math.max(sx, sz) / Math.min(sx, sz) <= 4;
}
// Image-to-board registration for a capture, from the live track pose.
// Verified against the hole lattice; null when unverifiable (never faked).
function registerCapture(canvas) {
  try {
    if (!SCAN.lastHyp || !SCAN.lastHypSize || !LS.project || !SM()) return null;
    // Re-lock on the still when it carries more detail than the preview:
    // preview-res locks fit the textured region (blur hides the smooth
    // rail bands, biasing the top edge down by tens of pixels), while a
    // higher-res still shows the rails. Full lock (no track gate: the
    // preview bias exceeds the track bound), falling back to the scaled
    // preview quad when the still does not lock.
    const maxW = 960, k0 = Math.min(1, maxW / canvas.width);
    const w = Math.max(80, Math.round(canvas.width * k0));
    let qc, rot;
    if (w > SCAN.lastHypSize.w * 1.4) {
      const lock = lockStillLum(canvas);
      if (lock) { qc = lock.corners; rot = lock.rot; }
    }
    if (!qc) {
      const ks = canvas.width / SCAN.lastHypSize.w;
      qc = SCAN.lastHyp.corners.map((p) => ({ x: p.x * ks, y: p.y * ks }));
      rot = SCAN.lastHyp.rot || 0;
    }
    return buildReg(canvas, qc, rot, true);
  } catch (e) { return null; }
}
// Registration for an UPLOADED photo: no live track exists, so the still
// locks on its own pixels and verifies against the hole lattice. img is the
// loaded upload; entry.w/h describe the uploaded pixels the mapping uses.
function registerStill(entry, img) {
  try {
    if (!entry || !img || !img.naturalWidth || !LS.project || !SM() || !BB()) return null;
    const c = document.createElement('canvas');
    c.width = entry.w; c.height = entry.h;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, entry.w, entry.h);
    const lock = lockStillLum(c);
    if (!lock) return null;
    return buildReg(c, lock.corners, lock.rot, false);
  } catch (e) { return null; }
}
function attachRegistration(entry, canvas) {
  if (!entry || !canvas) return;
  const reg = registerCapture(canvas);
  if (!reg) return;
  entry.reg = reg;
  const meta = LS.project.images.find((m) => m.name === entry.name);
  if (meta) { meta.reg = reg; markDirty(); }
}
// Absolute row span of the devkit from project evidence (placed pins win;
// agreeing outline votes are the fallback). Feeds the partial-end fitter's
// module-landmark hypotheses. Null when nothing is placed or voted yet.
function moduleSpanFromProject() {
  try {
    if (!LS.project) return null;
    for (const c of LS.project.components) {
      const rs = (c.pins || []).map((p) => {
        const m = /^t(\d+)[A-J]$/i.exec(String((p && p.hole) || ''));
        return m ? +m[1] : null;
      }).filter((n) => Number.isFinite(n));
      if (rs.length >= 8) return { start: Math.min(...rs), end: Math.max(...rs) };
    }
    for (const c of LS.project.components) {
      const vs = ((c && c.anchorVotes) || []).map((v) => v && v.start).filter((n) => Number.isFinite(n));
      if (vs.length >= 2 && Math.max(...vs) - Math.min(...vs) <= 1) {
        const n = Math.max(2, Math.round(((c.pins || []).length || 4) / 2));
        const s = Math.round(vs.reduce((a, b) => a + b, 0) / vs.length);
        return { start: s, end: s + n - 1 };
      }
    }
  } catch (e) { /* no span: the fitter proceeds without the landmark */ }
  return null;
}
// Partial-board registration fallback, BEHIND full-board locks (never ahead
// of them): when the whole outline is cut off, fit the visible rail lattice
// plus printed numerals to the board end instead of abstaining. Calibration
// comes from the project's best locked view (same physical board); stripes
// come from the still's own pixels. Every acceptance gate lives in the
// fitter (score, ambiguity margin, lattice verify, anisotropy); livescan
// re-applies the alias bound before adopting. Returns the reg or null.
async function ensurePartialReg(entry) {
  try {
    if (!entry || entry.reg || !entry.w || !entry.h || !SM() || !BB() || !LS.project) return null;
    if (!SM().fitPartialEndLock || !SM().calibratePartialEnd) return null;
    const locked = (LS.project.images || []).filter((im) => im && im.reg && im.reg.H && im.name !== entry.name)
      .sort((a, b) => ((b.reg && b.reg.conf) || 0) - ((a.reg && a.reg.conf) || 0))[0];
    if (!locked) return null;
    const serverUrl = (name) => `/api/livescan/projects/${encodeURIComponent(LS.project.id)}/images/${encodeURIComponent(name)}`;
    const loadWork = async (url, natW, natH) => {
      const img = await new Promise((resolve, reject) => {
        const im = new Image();
        im.onload = () => resolve(im);
        im.onerror = () => reject(new Error('partial-reg image load failed'));
        im.src = url;
      });
      if (!img.naturalWidth) return null;
      const workW = Math.min(natW, 1280), workH = Math.max(1, Math.round((workW * natH) / Math.max(1, natW)));
      const c = document.createElement('canvas');
      c.width = workW; c.height = workH;
      const ctx = c.getContext('2d', { willReadFrequently: true });
      if (!ctx) return null;
      ctx.drawImage(img, 0, 0, workW, workH);
      const d = ctx.getImageData(0, 0, workW, workH).data;
      const lum = SM().luminance(d, workW, workH);
      const rgb = new Uint8Array(workW * workH * 3);
      for (let i = 0, j = 0; i < d.length; i += 4, j += 3) {
        rgb[j] = d[i]; rgb[j + 1] = d[i + 1]; rgb[j + 2] = d[i + 2];
      }
      return { lum, rgb, w: workW, h: workH, k: natW / workW };
    };
    const L = await loadWork(serverUrl(locked.name), locked.w || 1600, locked.h || 1200);
    if (!L) return null;
    const s = L.w / Math.max(1, (locked.w || 1600));
    const H0 = locked.reg.H;
    const geom = { PITCH: BB().PITCH_MM, rows: BB().rowsOf(LS.project),
      holeXY: (id) => BB().holeXY(LS.project, id),
      railInner: BB().RAIL_INNER_X, railOuter: BB().RAIL_OUTER_X };
    const Hwork = [H0[0], H0[1], H0[2] * s, H0[3], H0[4], H0[5] * s, H0[6], H0[7], H0[8]];
    let cal = null;
    try { cal = SM().calibratePartialEnd(L.lum, L.w, L.h, Hwork, geom); } catch (e) { cal = null; }
    const E = await loadWork(entry.url || serverUrl(entry.name), entry.w, entry.h);
    if (!E) return null;
    const res = SM().fitPartialEndLock(E.lum, E.w, E.h, E.rgb, geom, cal,
      { moduleSpan: moduleSpanFromProject(), k: E.k });
    if (!res || !res.H || !lockAnisotropyOk(res.H)) return null;
    entry.reg = { H: res.H, iw: entry.w, ih: entry.h, conf: res.conf, tracked: false, partial: true, rows: res.rows };
    const meta = LS.project.images.find((m) => m && m.name === entry.name);
    if (meta) { meta.reg = entry.reg; markDirty(); }
    return entry.reg;
  } catch (e) { return null; }
}

async function ingestFile(file) {
  if (!/^image\/(png|jpeg|webp)$/.test(file.type)) { showError(new Error('Only PNG, JPEG or WebP photos.')); return; }
  if (file.size > 30 * 1024 * 1024) { showError(new Error('Photo exceeds 30 MB.')); return; }
  // Data URLs work under the app CSP (img-src 'self' data:) on any server
  // version; the entry switches to the served file URL after upload.
  let url;
  try {
    url = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('Could not read that image.'));
      reader.readAsDataURL(file);
    });
  } catch (e) { showError(e); return; }
  const img = new Image();
  // Resolves with the photo entry once the server upload finishes (or
  // immediately with the local entry when offline), so auto-scan can chain
  // interpretation after capture.
  return new Promise((resolve) => {
  img.onload = async () => {
    const quality = measureQuality(img);
    // Vision reads stored bytes with a 2 MB cap; downscale large uploads so
    // real iPhone photos stay readable instead of failing interpretation.
    // Entry dimensions always describe the UPLOADED pixels (bbox mapping).
    let upFile = file, upW = img.naturalWidth, upH = img.naturalHeight;
    if (file.size > 1800 * 1024 && img.naturalWidth > 0) {
      try {
        const scale = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
        if (scale < 1) {
          const c = document.createElement('canvas');
          c.width = Math.max(1, Math.round(img.naturalWidth * scale));
          c.height = Math.max(1, Math.round(img.naturalHeight * scale));
          c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
          const blob = await new Promise((r) => c.toBlob(r, 'image/jpeg', 0.85));
          if (blob && blob.size > 0 && blob.size < file.size) {
            upFile = new File([blob], file.name.replace(/\.[^.]*$/, '') + '.jpg', { type: 'image/jpeg' });
            upW = c.width; upH = c.height;
          }
        }
      } catch (e) { /* keep the original; the server enforces its own limit */ }
    }
    const entry = { name: upFile.name, url, w: upW, h: upH, quality };
    const slot = LS.project.images.findIndex((x) => x && x.name === upFile.name);
    if (slot >= 0) {
      // Same name, new pixels: replace the slot instead of stacking a twin
      // record (duplicate names wedge the settle gate at 6/7 forever).
      try { if (LS.images[slot] && LS.images[slot].url !== entry.url) URL.revokeObjectURL(LS.images[slot].url); } catch (_) {}
      LS.images[slot] = entry;
      LS.project.images[slot] = { name: upFile.name, w: entry.w, h: entry.h, captured: new Date().toISOString(), quality };
    } else {
      LS.images.push(entry);
      LS.project.images.push({ name: upFile.name, w: entry.w, h: entry.h, captured: new Date().toISOString(), quality });
    }
    checkpoint();
    const recIdx = slot >= 0 ? slot : LS.project.images.length - 1;
    // Uploaded photos register on their own pixels (no live track): without
    // this, nothing from an upload could ever be placed on the hole grid.
    try {
      const stillReg = registerStill(entry, img);
      if (stillReg) {
        entry.reg = stillReg;
        LS.project.images[recIdx].reg = stillReg;
      }
    } catch (e) { /* unregistered uploads still merge as unlocated evidence */ }
    // Partial-board fallback at upload time (cheap abstain when no locked
    // view exists yet; the reconstruct loop retries with all views present).
    if (!entry.reg) {
      try { await ensurePartialReg(entry); } catch (e) { /* abstains honestly */ }
    }
    markDirty();
    renderPhotos();
    updateGuide();
    if (LS.backend) {
      try {
        const res = await fetch(`/api/livescan/projects/${encodeURIComponent(LS.project.id)}/images`, {
          method: 'POST', headers: { 'X-PCB-Token': app.config.csrf_token, 'X-Filename': encodeURIComponent(upFile.name) },
          body: upFile,
        });
        const saved = await res.json();
        if (!res.ok) throw new Error(saved.error || 'Upload failed.');
        entry.name = saved.name; entry.url = `/api/livescan/projects/${encodeURIComponent(LS.project.id)}/images/${encodeURIComponent(saved.name)}`;
        // Rename the record this upload actually wrote (slot or append):
        // renaming the last record instead wedges the settle gate at 6/7
        // when a photo replaces its own slot.
        const meta = LS.project.images[recIdx];
        if (meta) meta.name = saved.name;
        markDirty(); saveNow(true); renderPhotos();
      } catch (e) { showError(e); }
    }
    resolve(entry);
  };
  img.onerror = () => { showError(new Error('Could not read that image.')); resolve(null); };
  img.src = url;
  });
}

// Local, measurable quality signals. No AI, no network.
function measureQuality(img) {
  try {
    const w = 160, h = Math.max(1, Math.round((160 * img.naturalHeight) / img.naturalWidth));
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, w, h);
    const d = ctx.getImageData(0, 0, w, h).data;
    const lum = new Float32Array(w * h);
    let sum = 0, dark = 0, bright = 0;
    for (let i = 0; i < w * h; i++) {
      const l = 0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2];
      lum[i] = l; sum += l;
      if (l < 18) dark++; if (l > 238) bright++;
    }
    const mean = sum / (w * h);
    let lap = 0;
    for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const v = Math.abs(4 * lum[i] - lum[i - 1] - lum[i + 1] - lum[i - w] - lum[i + w]);
      lap += v;
    }
    lap /= (w - 2) * (h - 2);
    return {
      sharpness: Math.round(lap * 10) / 10,
      exposure: Math.round(mean),
      darkFrac: Math.round((dark / (w * h)) * 1000) / 1000,
      brightFrac: Math.round((bright / (w * h)) * 1000) / 1000,
    };
  } catch (e) { return null; }
}

function updateGuide() {
  const guide = document.getElementById('ls-guide');
  if (!guide) return;
  const tips = [];
  const shots = LS.images.slice(-4);
  for (const s of shots) {
    if (!s.quality) continue;
    if (s.quality.sharpness < 12) tips.push(`“${s.name}” looks blurry — hold steady and capture again.`);
    if (s.quality.exposure < 55 || s.quality.darkFrac > 0.35) tips.push(`“${s.name}” is dark — improve the lighting.`);
    if (s.quality.exposure > 205 || s.quality.brightFrac > 0.25) tips.push(`“${s.name}” is washed out — reduce glare.`);
  }
  if (LS.images.length === 1) tips.push('One angle captured — add another side to resolve hidden wires.');
  const unknown = LS.project.components.filter((c) => c.status === 'unknown').length;
  if (unknown) tips.push(`${unknown} part${unknown > 1 ? 's are' : ' is'} unidentified — add a close-up of the markings.`);
  if (!tips.length) tips.push(LS.images.length ? 'Photos look usable. Reconstruct below, or capture more angles.' : 'Capture a full-board view first.');
  // Keep camera hint plus the latest two tips.
  const keep = tips.slice(-2);
  guide.replaceChildren(...keep.map((t) => node('p', '', t)));
  const note = node('p', 'muted', 'Capture guidance is local (sharpness, exposure). “Analyze” interprets a photo with Meta AI; findings merge as inferred until you confirm them.');
  guide.append(note);
}

/* ----- auto-scan: guided loop, auto-capture, AI interpretation, merge ----- */
const SCAN = { active: false, paused: false, timer: 0, lastLum: null, stable: 0,
  captureLum: null, captureAt: 0, interpreting: false, queue: [],
  coverage: null, metaHint: '', autoN: 0, similar: 0,
  trackFilter: null, trackOn: true, poseState: 'off', everLocked: false, lastHyp: null };
function SM() { return globalThis.ScanMath; }

function setScanStatus(text, cls) {
  const s = document.getElementById('ls-scan-status');
  if (s) { s.textContent = text; s.className = 'ls-pill' + (cls ? ' ' + cls : ''); }
  refreshRecord();
}
function startScan(preserve) {
  const video = document.getElementById('ls-video');
  if (!video || !video.videoWidth) {
    setScanStatus('No camera', 'bad');
    scanSay('Press Record to start the camera, then scan.');
    return;
  }
  if (!SM() || !LS.project) {
    setScanStatus('Engine missing', 'bad');
    scanSay('The scan engine (scanmath.js) did not load. Reload the app on the latest server.');
    return;
  }
  stopScan(true);
  SCAN.active = true; SCAN.paused = false; SCAN.stable = 0; SCAN.similar = 0;
  SCAN.phase = 'capture'; SCAN.sessionIds = [];
  SCAN.lastLum = null; SCAN.captureLum = null; SCAN.captureAt = 0;
  // Recovery preserves coverage/progress/auto-count/steps; a fresh start resets.
  if (!preserve || !SCAN.coverage) { SCAN.coverage = SM().newCoverage(6, 4); SCAN.metaHint = ''; }
  if (!preserve) { SCAN.autoN = 0; SCAN.step = 0; SCAN.finishing = false; }
  SCAN.trackFilter = SM().makeTrackFilter({ alpha: 0.35, maxJump: 0.6 });
  SCAN.poseState = 'hold'; SCAN.everLocked = false; SCAN.lastHyp = null;
  showTrackFeed(true);
  setScanStatus('Scanning', 'live');
  updateProgress();
  SCAN.timer = setInterval(scanTick, 600);
  scanSay('Move the camera slowly around the breadboard. I capture sharp, stable, new views automatically.');
}
function togglePauseScan() {
  if (!SCAN.active) return;
  SCAN.paused = !SCAN.paused;
  SCAN.stable = 0;
  setScanStatus(SCAN.paused ? 'Paused' : 'Scanning', SCAN.paused ? '' : 'live');
  scanSay(SCAN.paused ? 'Paused — no captures while paused. Press Resume to continue.'
    : 'Resumed — hold the camera over the board.');
}
function stopScan(silent) {
  clearInterval(SCAN.timer);
  const was = SCAN.active;
  SCAN.active = false; SCAN.paused = false; SCAN.timer = 0;
  SCAN.queue = []; SCAN.metaHint = '';
  SCAN.trackFilter = null; SCAN.poseState = 'off'; SCAN.lastHyp = null;
  showTrackFeed(false);
  if (LS.twin) LS.twin.setBoardPose(null);
  updateTrackUI();
  if (was && !silent) { setScanStatus('Idle'); updateGuide(); updateProgress(); }
  else refreshRecord();
}
function toggleTrackPose() {
  SCAN.trackOn = !SCAN.trackOn;
  if (!SCAN.trackOn && LS.twin) LS.twin.setBoardPose(null);
  if (SCAN.trackFilter) SCAN.trackFilter.reset();
  SCAN.poseState = SCAN.trackOn ? 'hold' : 'off';
  updateTrackUI();
}
function updateTrackUI() {
  const btn = document.getElementById('ls-track-toggle');
  if (!btn) return;
  btn.textContent = !SCAN.trackOn ? 'Track: off'
    : SCAN.poseState === 'locked' ? 'Track: locked' : 'Track: hold';
  btn.classList.toggle('ls-on', SCAN.trackOn && SCAN.poseState === 'locked');
  drawTrackOverlay();
}
// Positioning boxes per guided step, as fractions [x, y, w, h] of the
// viewport. Full-board is largest; close-ups and angled views are tighter
// windows. The box is a passive frame — captures still require the sharp +
// stable + novelty verdict, so it can never count as a detector by itself.
const GUIDE_FIT = [
  { aspect: 2.9, ax: 0.50, ay: 0.50, fill: 0.92 }, // full board, overhead
  { aspect: 1.5, ax: 0.30, ay: 0.50, fill: 0.62 }, // left section close-up
  { aspect: 1.5, ax: 0.70, ay: 0.50, fill: 0.62 }, // right section close-up
  { aspect: 2.2, ax: 0.42, ay: 0.50, fill: 0.86 }, // left side, angled
  { aspect: 2.2, ax: 0.58, ay: 0.50, fill: 0.86 }, // right side, angled
  { aspect: 1.7, ax: 0.50, ay: 0.50, fill: 0.72 }, // far end, angled
];
// Fit a step's target pixel aspect into vw×vh, anchored at (ax,ay). A
// full-size breadboard is ~3:1 landscape; fixed normalized fractions go
// wrong on every viewport that is not 4:3. Returns normalized [x,y,w,h].
function fitGuideBox(step, vw, vh) {
  const g = GUIDE_FIT[Math.min(step || 0, 5)];
  let w = vw * g.fill, h = w / g.aspect;
  const maxH = vh * 0.94;
  if (h > maxH) { h = maxH; w = h * g.aspect; }
  const maxW = vw * 0.96;
  if (w > maxW) { w = maxW; h = w / g.aspect; }
  const x = Math.min(Math.max(g.ax * vw - w / 2, vw * 0.02), vw * 0.98 - w);
  const y = Math.min(Math.max(g.ay * vh - h / 2, vh * 0.02), vh * 0.98 - h);
  return [x / vw, y / vh, w / vw, h / vh];
}
// Verified-lock outline in feed space: sample coords -> video px ->
// cover-fit element px, then the same rotation the preview CSS applies.
// Only a locked quad draws; anything else would invite false confidence.
function drawTrackOverlay() {
  const ov = document.getElementById('ls-track-overlay');
  if (!ov || !ov.getContext) return;
  const ctx = ov.getContext('2d');
  if (!ctx || !ctx.beginPath) return;
  const mount = document.getElementById('ls-twin');
  const r = mount && mount.getBoundingClientRect ? mount.getBoundingClientRect() : null;
  ov.width = Math.max(2, Math.round(r ? r.width : 300));
  ov.height = Math.max(2, Math.round(r ? r.height : 200));
  ctx.clearRect(0, 0, ov.width, ov.height);
  // Step positioning box: drawn whenever capturing, large and clear.
  if (SCAN.active && r && r.width) {
    const det = (SCAN.poseState === 'locked' && SCAN.lastHyp && SCAN.lastHyp.corners && SCAN.lastHypSize)
      ? quadToOverlay(SCAN.lastHyp.corners, SCAN.lastHypSize) : null;
    const g = guideBoxFor(SCAN.step, ov.width, ov.height, det);
    const gx = g[0] * ov.width, gy = g[1] * ov.height, gw = g[2] * ov.width, gh = g[3] * ov.height;
    ctx.save();
    if (ctx.setLineDash) ctx.setLineDash([10, 7]);
    ctx.strokeStyle = 'rgba(255,255,255,.92)'; ctx.lineWidth = 3;
    ctx.strokeRect(gx, gy, gw, gh);
    const c = Math.min(gw, gh) * 0.12;
    ctx.beginPath();
    [[gx, gy, 1, 1], [gx + gw, gy, -1, 1], [gx, gy + gh, 1, -1], [gx + gw, gy + gh, -1, -1]].forEach(([x, y, dx, dy]) => {
      ctx.moveTo(x + dx * c, y); ctx.lineTo(x, y); ctx.lineTo(x, y + dy * c);
    });
    ctx.strokeStyle = '#ffd76a'; ctx.lineWidth = 5;
    ctx.stroke();
    ctx.restore();
    if (ctx.fillText) {
      ctx.font = '600 15px system-ui, sans-serif';
      ctx.fillStyle = 'rgba(16,21,18,.78)';
      const label = `Step ${Math.min((SCAN.step || 0) + 1, 6)} of 6`
        + (SCAN.poseState === 'locked' ? '' : ' · align inside box');
      const tw = ctx.measureText ? ctx.measureText(label).width : 90;
      ctx.fillRect(gx + 10, gy + 10, tw + 20, 28);
      ctx.fillStyle = '#fff';
      ctx.fillText(label, gx + 20, gy + 30);
    }
  }
  if (SCAN.poseState !== 'locked' || !SCAN.lastHyp || !SCAN.lastHyp.corners || !SCAN.lastHypSize) return;
  const pts = quadToOverlay(SCAN.lastHyp.corners, SCAN.lastHypSize);
  if (!pts) return;
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  ctx.closePath();
  ctx.strokeStyle = '#2fae5f'; ctx.lineWidth = 3;
  ctx.stroke();
}
// Verified-lock quad in overlay px: sample space already carries the blit
// rotation, so invert it back to sensor space (matches blitOriented's
// transform math), then cover-fit, then the same rotation the preview CSS
// applies. Null unless every input is valid.
function quadToOverlay(corners, size) {
  const ov = document.getElementById('ls-track-overlay');
  const feed = document.getElementById('ls-track-video');
  if (!ov || !feed || !feed.videoWidth || !corners || !size || !size.w) return null;
  const rot = LS.rotation || 0;
  if (rot !== 0 && rot !== 90 && rot !== 180 && rot !== 270) return null;
  const vw = feed.videoWidth, vh = feed.videoHeight;
  // Sample dims live in ROTATED space: at 90/270 the sample width spans
  // the sensor height (a vw-based scale would stretch the quad).
  const frameW = (rot === 90 || rot === 270) ? vh : vw;
  const ks = frameW / size.w;
  let pts = corners.map((p) => ({ x: p.x * ks, y: p.y * ks }));
  if (rot === 90 || rot === 270) {
    pts = pts.map((p) => (rot === 90
      ? { x: p.y, y: vh - p.x }
      : { x: vw - p.y, y: p.x }));
  }
  // Cover-fit always uses SENSOR dims: CSS applies object-fit:cover in the
  // unrotated layout box, then the rotate() transform spins the rendered
  // result about the element center (mirrored below).
  const sc = Math.max(ov.width / vw, ov.height / vh);
  const ox = (ov.width - vw * sc) / 2, oy = (ov.height - vh * sc) / 2;
  pts = pts.map((p) => ({ x: ox + p.x * sc, y: oy + p.y * sc }));
  const cx = ov.width / 2, cy = ov.height / 2;
  if (rot === 90) pts = pts.map((p) => ({ x: cx + (p.y - cy), y: cy - (p.x - cx) }));
  else if (rot === 270) pts = pts.map((p) => ({ x: cx - (p.y - cy), y: cy + (p.x - cx) }));
  else if (rot === 180) pts = pts.map((p) => ({ x: 2 * cx - p.x, y: 2 * cy - p.y }));
  return pts;
}
// Positioning guide: the detected quad's bbox + 8% margin when a verified
// lock exists (detection, not the frame, sets the shape; sane aspects
// only); otherwise the static aspect-aware default for the step.
function guideBoxFor(step, vwpx, vhpx, detPts) {
  if (detPts && detPts.length >= 4) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of detPts) {
      x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y);
      x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y);
    }
    let w = x1 - x0, h = y1 - y0;
    const aspect = Math.max(w, h) / Math.max(1, Math.min(w, h));
    if (aspect >= 1.05 && aspect <= 4.5 && w > 8 && h > 8) {
      x0 -= w * 0.08; y0 -= h * 0.08; w *= 1.16; h *= 1.16;
      w = Math.min(w, vwpx); h = Math.min(h, vhpx);
      x0 = Math.max(0, Math.min(x0, vwpx - w));
      y0 = Math.max(0, Math.min(y0, vhpx - h));
      return [x0 / vwpx, y0 / vhpx, w / vwpx, h / vhpx];
    }
  }
  return fitGuideBox(step, vwpx, vhpx);
}
// Live camera feed behind the 3D reconstruction (same stream, second element).
function showTrackFeed(on) {
  const feed = document.getElementById('ls-track-video');
  if (!feed) return;
  if (on && LS.stream) {
    feed.srcObject = LS.stream;
    feed.hidden = false;
    feed.play().catch(() => {});
  } else {
    if (typeof feed.pause === 'function') { try { feed.pause(); } catch (_) { /* already stopped */ } }
    if (typeof feed.removeAttribute === 'function') feed.removeAttribute('src');
    feed.srcObject = null;
    feed.hidden = true;
  }
}
function updateTrackPose(s) {
  // Detection runs with or without the 3D engine: the lock drives the
  // overlay, the guide box and auto-capture gating; the twin pose applies
  // only when the engine mounted (setBoardPose stays guarded).
  if (!SCAN.trackOn || !SCAN.trackFilter || !LS.project || !SM()) return;
  const rows = BB().rowsOf(LS.project);
  // Verified path: structural lock when lost, relative hill-climb from the
  // locked quad when tracking (corners rescaled to this frame's size).
  let track = null;
  if (SCAN.lastHyp && SCAN.lastHyp.corners && SCAN.lastHypSize) {
    const sx = s.w / SCAN.lastHypSize.w, sy = s.h / SCAN.lastHypSize.h;
    track = SCAN.lastHyp.corners.map((p) => ({ x: p.x * sx, y: p.y * sy }));
  }
  const hyps = SM().estimateBoardPose(s.lum, s.w, s.h,
    BB().BOARD_WIDTH, rows * BB().PITCH_MM + 12,
    { verify: { lumHi: s.lum, wHi: s.w, hHi: s.h, track } });
  // Slow-cadence re-verify: every 45th update, attempt a fresh absolute
  // lock and adopt it when it lands near the tracked quad (heals drift);
  // a fresh lock far from track is ignored until track is lost, so a
  // passing white rectangle cannot hijack the pose.
  SCAN.reverifyTick = (SCAN.reverifyTick || 0) + 1;
  if (SCAN.reverifyTick % 45 === 0) {
    const fresh = SM().estimateBoardPose(s.lum, s.w, s.h,
      BB().BOARD_WIDTH, rows * BB().PITCH_MM + 12,
      { verify: { lumHi: s.lum, wHi: s.w, hHi: s.h } });
    if (fresh.length && track) {
      let d = 0;
      for (let i = 0; i < 4; i++)
        d += Math.hypot(fresh[0].corners[i].x - track[i].x, fresh[0].corners[i].y - track[i].y);
      if (d / 4 < Math.max(12, s.w * 0.05)) hyps.push(fresh[0]);
    } else if (fresh.length && !track) {
      hyps.push(fresh[0]);
    }
  }
  const res = SCAN.trackFilter.update(hyps);
  if (res.state === 'lock' || res.state === 'track') {
    if (autoOrientFromLock(res.hyp)) { updateTrackUI(); return; }
    SCAN.poseState = 'locked'; SCAN.everLocked = true;
    SCAN.lastHyp = res.hyp || null; SCAN.lastHypSize = { w: s.w, h: s.h };
    if (LS.twin) LS.twin.setBoardPose(res.euler);
  } else {
    SCAN.poseState = 'hold'; // freeze: never move without evidence
  }
  updateTrackUI();
}
function isEmptyConfirmed() {
  return !!LS.project && LS.project.questions.some((q) => q.kind === 'empty'
    && q.status === 'answered' && /yes/i.test(q.answer || ''));
}
function updateProgress() {
  const fill = document.getElementById('ls-progress-fill');
  const label = document.getElementById('ls-progress-label');
  const sub = document.getElementById('ls-progress-sub');
  if (!fill || !label || !fill.style || !LS.project || !SM()) return;
  // During a guided scan the bar tracks completed VIEWS (evidence, not
  // time); six views are the capture target, not an electrical sign-off.
  if (SCAN.active) {
    const done = Math.min(SCAN.step || 0, 6);
    const pct = Math.round((100 * done) / 6);
    fill.style.width = pct + '%';
    fill.classList.toggle('done', false);
    label.textContent = pct + '%';
    if (sub) sub.textContent = `Step ${Math.min(done + 1, 6)} of 6`;
    return;
  }
  // Photo workflow: the bar shows 3D generation state, never a scan-coverage
  // percentage. A user-confirmed done state beats a coverage estimate.
  if (SCAN.batchActive || LS.project.components.length || LS.project.wires.length) {
    const ids = (SCAN.sessionIds || []).length ? SCAN.sessionIds : LS.images.map((im) => im.name);
    const n = ids.length || 1;
    if (SCAN.batchActive) {
      // Re-runs must not display the previous run's done state while the
      // new analysis is still in flight: show an honest working state.
      const elapsed = SCAN.batchStart ? Math.round((Date.now() - SCAN.batchStart) / 1000) : 0;
      fill.style.width = '100%'; fill.classList.toggle('done', false);
      label.textContent = '…';
      if (sub) sub.textContent = `Re-analyzing ${n} photo(s)… (${elapsed}s elapsed)`;
      return;
    }
    const done = LS.project.images.filter((im) => im.interpreted).length;
    const failed = LS.project.images.filter((im) => im.failed);
    const settled = done + failed.length >= n && n > 0;
    setGenProgress(done, n, settled,
      LS.project.components.length, LS.project.wires.length,
      failed.length ? failed.map((im) => im.name).join(', ') : '');
    return;
  }
  const prog = BB().reportProgress(LS.project);
  const openQ = LS.project.questions.filter((q) => q.status === 'open').length;
  const r = SM().scanProgress({
    coverage: SCAN.coverage ? SM().coverageScore(SCAN.coverage) : 0,
    components: LS.project.components.map((c) => ({
      pinsTotal: c.pins.length, pinsPlaced: c.pins.filter((p) => p.hole).length,
      identified: c.kind !== 'generic' || c.status === 'confirmed' })),
    wires: LS.project.wires.map((w) => ({ fromSet: !!w.from, toSet: !!w.to })),
    openQuestions: openQ, emptyConfirmed: isEmptyConfirmed(),
  });
  fill.style.width = r.pct + '%';
  fill.classList.toggle('done', r.complete);
  label.textContent = r.pct + '%';
  if (sub) sub.textContent = r.complete ? 'scan complete'
    : `${prog.resolved} of ${prog.total} facts resolved · ${r.unresolved} unresolved`;
}
function scanSay(main, sub) {
  // The viewport line is the primary instruction; the capture-panel guide
  // keeps the same message plus detail lines.
  const vp = document.getElementById('ls-vp-guide');
  if (vp) vp.textContent = main;
  const guide = document.getElementById('ls-guide');
  if (!guide) return;
  guide.replaceChildren(node('p', 'ls-scanline', main));
  const bits = [];
  if (SCAN.coverage) bits.push(`coverage ${Math.round(SM().coverageScore(SCAN.coverage) * 100)}%`);
  if (SCAN.autoN) bits.push(`${SCAN.autoN} auto-captures`);
  if (sub) bits.push(sub);
  if (SCAN.metaHint) bits.push('AI: ' + SCAN.metaHint);
  if (bits.length) guide.append(node('p', 'muted', bits.join(' · ')));
}
function sampleLum() {
  const video = document.getElementById('ls-video');
  if (!video || !video.videoWidth) return null;
  const rot = LS.rotation || 0;
  const [w, h] = orientedSize(160, Math.max(1, Math.round((160 * video.videoHeight) / video.videoWidth)), rot);
  let c = document.getElementById('ls-sample');
  if (!c || c.tagName !== 'CANVAS' || !c.getContext) {
    c = document.createElement('canvas'); c.id = 'ls-sample'; c.hidden = true;
    document.getElementById('ls-capture').append(c);
  }
  c.width = w; c.height = h;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  blitOriented(ctx, video, video.videoWidth, video.videoHeight, rot);
  const d = ctx.getImageData(0, 0, w, h);
  return { lum: SM().luminance(d.data, w, h), w, h };
}
function scanTick() {
  // Never capture unless the loop is actively running.
  if (!SCAN.active || SCAN.paused || !SM()) return;
  const s = sampleLum();
  if (!s) { recoverCamera('The camera feed stopped.'); return; }
  // Frozen frames keep their dimensions: a track that is no longer live
  // must trigger the same bounded recovery (missing readyState on older
  // browsers counts as live — only an explicit 'ended' condemns).
  const tracks = (LS.stream && LS.stream.getTracks && LS.stream.getTracks()) || [];
  const live = tracks.length ? tracks.some((t) => t.readyState == null || t.readyState === 'live') : true;
  if (!live) {
    SCAN.deadTicks = (SCAN.deadTicks || 0) + 1;
    if (SCAN.deadTicks >= 3) { SCAN.deadTicks = 0; recoverCamera('The camera feed froze.'); }
    return;
  }
  SCAN.deadTicks = 0;
  // Dimension change (orientation flip, camera switch): the pose lives in
  // old coordinates, so drop the track — coverage and progress survive.
  if (SCAN.vidSize && (SCAN.vidSize[0] !== s.w || SCAN.vidSize[1] !== s.h)) {
    SCAN.lastHyp = null;
    if (SCAN.trackFilter) SCAN.trackFilter.reset();
  }
  SCAN.vidSize = [s.w, s.h];
  const sharp = SM().sharpness(s.lum, s.w, s.h);
  const expo = SM().exposure(s.lum);
  const verdict = SM().qualityVerdict({ sharpness: sharp, exposure: expo });
  SM().updateCoverage(SCAN.coverage, SM().sharpnessGrid(s.lum, s.w, s.h, 6, 4));
  drawCoverage();
  updateTrackPose(s);
  updateProgress();
  // Exposure first: a too-dark frame cannot lock tracking or register holes.
  if (expo.mean < 28) {
    SCAN.stable = 0;
    scanSay('Too dark — improve the lighting, then re-aim at the breadboard.');
    return;
  }
  // Before the first pose lock, steer to a full-board view (close-ups come later).
  if (!SCAN.everLocked) {
    SCAN.noLockN = (SCAN.noLockN || 0) + 1;
    const framing = SM().detectBoardFraming(s.lum, s.w, s.h);
    if (framing.state !== 'full') {
      SCAN.stable = 0;
      scanSay(framing.state === 'none'
        ? 'Aim the camera at the breadboard to lock tracking.'
        : 'Board is cut off — pull back to show the whole board, then move closer.');
      return;
    }
    // Prolonged failure with a full view: likely a sideways sensor. Say so
    // once in a while instead of guessing rotations every frame.
    if (SCAN.noLockN > 40 && SCAN.noLockN % 20 < 10) {
      SCAN.stable = 0;
      scanSay('Still no lock — hold the board horizontal and fill the view. Turn the phone to landscape if the board looks sideways.');
      return;
    }
  }
  const move = SM().frameDiff(SCAN.lastLum, s.lum);
  SCAN.lastLum = s.lum;
  if (!verdict.ok) {
    SCAN.stable = 0;
    const tip = verdict.tips[0];
    scanSay(tip === 'blurry' ? 'Hold steady — the image is blurry.'
      : tip === 'dark' ? 'Too dark — improve the lighting on the board.'
      : 'Glare is washing out the board — shade it and try again.');
    return;
  }
  if (move >= SM().STABLE_DIFF) {
    SCAN.stable = 0; SCAN.similar = 0;
    scanSay('Moving — hold still over one view to capture it.');
    return;
  }
  SCAN.stable++;
  if (SCAN.stable < 2) { scanSay('Hold still — capturing…'); return; }
  if (SCAN.interpreting || SCAN.queue.length >= 2) {
    scanSay('View looks good — reading the last capture, hold position…');
    return;
  }
  const wait = 8000 - (Date.now() - SCAN.captureAt);
  if (wait > 0) { scanSay(`Stabilized — next auto-capture in ${Math.ceil(wait / 1000)}s…`); return; }
  const novelty = SM().frameDiff(SCAN.captureLum, s.lum);
  if (SCAN.captureLum && novelty < SM().NOVEL_DIFF) {
    SCAN.similar++;
    const weak = SM().weakestCell(SCAN.coverage);
    scanSay(SCAN.similar > 2 && weak.value < SM().SHARP_MIN
      ? `This view is captured — move toward the ${weak.zone}, it is not scanned sharply yet.`
      : 'This view is already captured — try a new angle or a close-up.');
    return;
  }
  SCAN.similar = 0;
  captureAuto(s.lum);
}
function drawCoverage() {
  const cov = document.getElementById('ls-coverage');
  if (!cov || !cov.getContext) return;
  const ctx = cov.getContext('2d');
  if (!ctx) return;
  const cells = (SCAN.coverage && SCAN.coverage.cells) || new Array(24).fill(0);
  const weak = SCAN.coverage && SM() ? SM().weakestCell(SCAN.coverage).index : -1;
  const cw = cov.width / 6, ch = cov.height / 4;
  ctx.clearRect(0, 0, cov.width, cov.height);
  cells.forEach((v, i) => {
    const t = SM() ? Math.min(1, v / SM().SHARP_MIN) : 0;
    ctx.fillStyle = t >= 1 ? '#2fae5f' : t > 0.4 ? '#d9b81f' : '#c9c4b8';
    ctx.fillRect((i % 6) * cw + 1, Math.floor(i / 6) * ch + 1, cw - 2, ch - 2);
    if (i === weak && v < (SM() ? SM().SHARP_MIN : 1)) {
      ctx.strokeStyle = '#c23b2e'; ctx.lineWidth = 2;
      ctx.strokeRect((i % 6) * cw + 1, Math.floor(i / 6) * ch + 1, cw - 2, ch - 2);
    }
  });
}
async function captureAuto(lum) {
  const canvas = grabFrameCanvas(1280);
  if (!canvas) return;
  const blob = await new Promise((r) => canvas.toBlob(r, 'image/jpeg', 0.88));
  if (!blob) return;
  // Confirm first: the success message and step advance must only follow a
  // genuinely saved image, never an unreadable frame.
  const viewN = SCAN.autoN + 1;
  const entry = await ingestFile(new File([blob], `scan-auto-${viewN}.jpg`, { type: 'image/jpeg' }));
  if (!entry) { scanSay('Capture failed — the frame was unreadable. Hold steady and retry.'); return; }
  SCAN.autoN = viewN;
  SCAN.captureLum = lum ? lum.slice() : null;
  SCAN.captureAt = Date.now();
  const panel = document.getElementById('ls-capture');
  if (panel) { panel.classList.add('ls-flash'); setTimeout(() => panel.classList.remove('ls-flash'), 350); }
  scanSay(`Captured view ${SCAN.autoN} ✓ — reading it now…`);
  SCAN.sessionIds.push(entry.name);
  stepAdvance(false); // sharp stable capture confirmed: next guided view
  attachRegistration(entry, canvas);
  if (!LS.backend || String(entry.url || '').startsWith('data:')) {
    scanSay('Capture saved locally. Connect the backend to interpret it with AI.');
    return;
  }
  if (SCAN.queue.length < 2) { SCAN.queue.push(entry); pumpQueue(); }
}
// Place merged parts from verified image registration. Any lattice-verified
// registration places (live captures and still-locked uploads alike); pins
// stay inferred until the user confirms them. Wires are NEVER auto-ended
// from bboxes: a wire's box spans its whole length including off-board runs,
// so its corners are not endpoints — the quiz asks instead.
function autoPlaceFromReg(entry, ids, boxes) {
  const out = { placed: 0, suggested: 0, replaced: 0 };
  if (!entry || !entry.reg || !entry.reg.H || !SM() || !BB()) return out;
  const conf = entry.reg.conf || 0;
  const Hinv = SM().invertHomography(entry.reg.H);
  if (!Hinv) return out;
  const px2mm = (x, y) => {
    const p = SM().applyHomography(Hinv, x, y);
    return { x: p.x, z: p.y };
  };
  for (const id of ids || []) {
    const c = BB().getComponent(LS.project, id);
    // Best-view placement: each part is placed from its highest-confidence
    // registered view; a better view replaces an inferred placement. Never
    // touch user-confirmed pins. Per-view boxes (not the adopted latest)
    // keep every mapping in its own view's frame.
    if (!c) continue;
    const box = (boxes && boxes[id]) || c.bbox;
    if (!box) continue;
    if ((c.bestConf || 0) >= conf && c.placedView) continue;
    if (c.placedView && c.status === 'confirmed') continue;
    // NOTE: the previous placement is cleared only AFTER the new proposal
    // succeeds below — a failed proposal must never destroy a good one.
    const wasPlaced = !!c.placedView;
    if (c.kind === 'esp32devkit' && c.pins.length >= 30) {
      // Groove-straddling module: place the full 2x15 footprint. A 180°
      // registration flip mirrors the span, so placement always ships with
      // an orientation question (answer "flip" to mirror it back).
      const mod = BB().proposeModuleHoles(LS.project, px2mm, entry.w, entry.h, box, 15);
      if (mod && mod.pins.length === c.pins.length) {
        let okAll = true;
        const prev = c.pins.map((p) => p.hole);
        const prevView = c.placedView, prevConf = c.bestConf || 0;
        if (wasPlaced) BB().clearPlacement(LS.project, id);
        mod.pins.forEach((h, i) => { if (!BB().setPinHole(LS.project, id, i, h).ok) okAll = false; });
        if (!okAll && wasPlaced) {
          prev.forEach((h, i) => { if (h) BB().setPinHole(LS.project, id, i, h); });
          c.placedView = prevView; c.bestConf = prevConf;
        }
        if (okAll) {
          c.placedView = entry.name;
          c.bestConf = conf; c.anchorLock = false; // verified registration supersedes outline votes
          c.notes += `; pins from image registration (inferred, ${entry.name})`;
          if (!LS.project.questions.some((q) => q.key === 'orient:' + id)) {
            LS.project.questions.push({ id: 'Q' + Math.random().toString(36).slice(2, 8),
              key: 'orient:' + id, kind: 'confirm',
              text: `${id} was placed spanning ${mod.span} (pin 1 toward the lowest column). If it is mirrored end-to-end, answer "flip".`,
              target: id, options: [], status: 'open', answer: null, created: new Date().toISOString() });
          }
          out.placed++;
          if (wasPlaced) out.replaced++;
        }
      } else {
        c.notes += `; registration in ${entry.name} could not place the module footprint`;
        out.suggested++;
      }
      continue;
    }
    const prop = BB().proposeHolesFromBox(LS.project, px2mm, entry.w, entry.h, box, c.pins.length);
    if (prop && prop.holes) {
      const prev = [c.pins[0] && c.pins[0].hole, c.pins[1] && c.pins[1].hole];
      const prevView = c.placedView, prevConf = c.bestConf || 0;
      if (wasPlaced) BB().clearPlacement(LS.project, id);
      const r0 = BB().setPinHole(LS.project, id, 0, prop.holes[0]);
      const r1 = BB().setPinHole(LS.project, id, 1, prop.holes[1]);
      if ((!r0.ok || !r1.ok) && wasPlaced) {
        prev.forEach((h, i) => { if (h) BB().setPinHole(LS.project, id, i, h); });
        c.placedView = prevView; c.bestConf = prevConf;
      }
      if (r0.ok && r1.ok) {
        c.placedView = entry.name;
        c.bestConf = conf;
        c.notes += '; pins from image registration (inferred)';
        if (c.kind === 'led' || c.kind === 'diode') c.notes += ' (polarity from a single photo — confirm)';
        out.placed++;
        if (wasPlaced) out.replaced++;
      }
    } else if (prop && prop.suggest) {
      c.notes += `; image suggests near ${prop.suggest}`;
      out.suggested++;
    }
  }
  return out;
}
// Guided rail-plug placement for wire sightings in one registered view.
// For each wire bbox overlapping a rail band, vision's own endpoint words
// pick an image region, pixel evidence finds dark-width plug candidates in
// the bbox∩band window, and the nearest candidate cluster becomes the wire's
// plug hypothesis (side + column ± spread; lane stays unknown for the quiz).
// First claim wins per wire; later views corroborate or note conflicts.
// Best-effort: never throws, never places holes, never invents lanes.
async function placeWirePlugs(entry, wireSightings, moduleBox) {
  const out = { placed: 0, corroborated: 0 };
  try {
    if (!entry || !entry.reg || !entry.reg.H || !entry.url || !SM() || !BB()) return out;
    if (!Array.isArray(wireSightings) || !wireSightings.length) return out;
    const Hinv = SM().invertHomography(entry.reg.H);
    if (!Hinv) return out;
    const img = await new Promise((resolve, reject) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => reject(new Error('plug image load failed'));
      im.src = entry.url;
    });
    if (!img.naturalWidth) return out;
    const c = document.createElement('canvas');
    c.width = entry.w; c.height = entry.h;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    if (!ctx) return out;
    ctx.drawImage(img, 0, 0, entry.w, entry.h);
    const lum = SM().luminance(ctx.getImageData(0, 0, entry.w, entry.h).data, entry.w, entry.h);
    const rows = BB().rowsOf(LS.project), pitch = BB().PITCH_MM;
    const zRow = (r) => (r - (rows + 1) / 2) * pitch;
    const px2mm = (x, y) => {
      const p = SM().applyHomography(Hinv, x, y);
      return { x: p.x, z: p.y };
    };
    const colOfZ = (z) => Math.round(z / pitch + (rows + 1) / 2);
    // Per-clause sides in this view's frame (words describe the image, so
    // the mapping — not fixed columns — absorbs registration flips).
    const clauseSides = (endpoints) => BB().railHintFromText(endpoints || '').map((h) => {
      if (h.offboard) return { side: 'off', u: null };
      if (h.module) {
        // "Near the module" is a column hint with an ambiguous side: the
        // module sits between the bands, so mapping a fabricated v would
        // snap it to the wrong band. Callers use u with the band's own row.
        const u = Array.isArray(moduleBox) && moduleBox.length === 4
          ? (moduleBox[0] + moduleBox[2]) / 2 : null;
        return { side: null, u };
      }
      const u = h.u, v = h.v;
      if (!Number.isFinite(u) || !Number.isFinite(v)) return { side: null, u: Number.isFinite(u) ? u : null };
      const m = px2mm(u * entry.w, v * entry.h);
      if (!m || !Number.isFinite(m.x)) return { side: null, u };
      const side2 = m.x < -16.5 ? 'L' : m.x > 16.5 ? 'R' : null;
      return { side: side2, u };
    });
    for (const ws of wireSightings) {
      const w = LS.project.wires.find((x) => x.id === ws.id);
      if (!w || (w.from && w.to)) continue;
      const box = ws.bbox;
      if (!Array.isArray(box) || box.length !== 4) continue;
      const clauses = clauseSides(ws.endpoints);
      const boardClauses = clauses.filter((c) => c.side === 'L' || c.side === 'R' || c.side === null);
      for (const side of ['L', 'R']) {
        // Side veto: a band no clause allows is not searched. Wires
        // described as fully off-board (all clauses off) search nothing;
        // hint-less wires (no clauses at all) search every band.
        const allows = !clauses.length || clauses.some((c) => c.side === null || c.side === side);
        if (!allows) continue;
        const lx = [BB().railLaneX(side, '+'), BB().railLaneX(side, '-')].sort((a, b) => a - b);
        const win = BB().bandWindowFromBox(px2mm, entry.w, entry.h, box, lx[0], lx[1], [zRow(1), zRow(rows)]);
        if (!win) continue;
        const innerX = Math.abs(lx[0]) < Math.abs(lx[1]) ? lx[0] : lx[1];
        const outerX = innerX === lx[0] ? lx[1] : lx[0];
        const cands = SM().railCandidates(lum, entry.w, entry.h, entry.reg.H,
          innerX, outerX, win[0], win[1], pitch, rows, zRow);
        if (!cands.length) continue;
        // Column hints via the band's own image row: project the band
        // centerline at the window middle to get v, then map (hintU, v) to
        // a column. Word-guessed v values are viewpoint garbage for columns
        // (only the side mapping above uses them).
        let hintCol = null;
        // Side-exact clauses steer before ambiguous ones (a module clause
        // about the other end must not outvote this band's own clause).
        const ordered = boardClauses.filter((c) => c.side === side)
          .concat(boardClauses.filter((c) => c.side === null));
        const uHint = ordered.map((c) => c.u).find((u) => Number.isFinite(u));
        if (Number.isFinite(uHint)) {
          const mid = SM().applyHomography(entry.reg.H, (lx[0] + lx[1]) / 2, (win[0] + win[1]) / 2);
          const vBand = mid && entry.h ? mid.y / entry.h : 0.5;
          const m = px2mm(uHint * entry.w, vBand * entry.h);
          if (m && Number.isFinite(m.z)) hintCol = colOfZ(m.z);
        }
        // Lane is still unknown, so clusters respect the union of both
        // lanes' rail gaps on this side (never merge across copper breaks).
        const gaps = ['+', '-'].flatMap((pol) =>
          LS.project.breadboard.railGaps['r' + side + pol] || []);
        const sel = BB().selectRailPlug(cands, hintCol, gaps);
        if (!sel) continue;
        w.plugs = Array.isArray(w.plugs) ? w.plugs : [];
        const same = w.plugs.find((p) => p && p.side === side);
        if (same) {
          if (Math.abs(same.pos - sel.pos) <= 4) {
            same.views = (same.views || 1) + 1;
            w.notes += `; rail end confirmed by ${entry.name}`;
            out.corroborated++;
          } else {
            w.notes += `; ${entry.name} suggests ${side} rail col ${sel.pos} (kept ${side}${same.pos})`;
          }
          continue;
        }
        if (w.plugs.length >= 2) continue;
        const taken = new Set(w.plugs.map((p) => p.end));
        const end = ['from', 'to'].find((e) => !w[e] && !taken.has(e)) || null;
        w.plugs.push({ side, pos: sel.pos, spread: sel.spread, view: entry.name, end });
        w.notes += `; board end near ${side} rail col ${sel.pos} (±${Math.ceil(sel.spread / 2)}) [${entry.name}]`;
        out.placed++;
      }
    }
  } catch (e) { /* guided plugs are advisory; the quiz covers the rest */ }
  return out;
}
function knownSummary() {
  // User-confirmed facts the model must not contradict on re-analysis:
  // confirmed identities, corrected values, answered questions. This is how
  // the reconstruction learns from mistakes within the project.
  const bits = LS.project.components.map((c) => `${c.id} ${c.kind}@${c.region || '?'}${c.status === 'confirmed' ? ' (user-confirmed)' : ''}`);
  for (const [key, cf] of Object.entries(LS.project.confirmations || {})) {
    const v = cf && typeof cf.value !== 'undefined' ? String(cf.value).slice(0, 60) : '';
    if (v) bits.push(`confirmed ${key}=${v}`);
  }
  for (const q of LS.project.questions || []) {
    if (q.status === 'answered' && q.answer) {
      bits.push(`Q: ${String(q.text || '').slice(0, 80)} A: ${String(q.answer).slice(0, 80)}`);
    }
  }
  return bits.join(' | ').slice(0, 1500);
}
// Entry pixels as flat RGB triplets at entry.w/h (for color segmentation).
// Loaded lazily: only views with unplaced wire ends pay for the decode.
async function entryRgb(entry) {
  try {
    if (!entry || !entry.url || !entry.w || !entry.h) return null;
    const img = await new Promise((resolve, reject) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => reject(new Error('wire image load failed'));
      im.src = entry.url;
    });
    if (!img.naturalWidth) return null;
    const c = document.createElement('canvas');
    c.width = entry.w; c.height = entry.h;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, entry.w, entry.h);
    const d = ctx.getImageData(0, 0, entry.w, entry.h).data;
    const rgb = new Uint8Array(entry.w * entry.h * 3);
    for (let i = 0, j = 0; i < d.length; i += 4, j += 3) {
      rgb[j] = d[i]; rgb[j + 1] = d[i + 1]; rgb[j + 2] = d[i + 2];
    }
    return { rgb, w: entry.w, h: entry.h };
  } catch (e) { return null; }
}
// AI-proposed hole IDs from close-up/registered views, verified against the
// view's own registration: each proposed hole must project inside the wire's
// bbox (expanded by tolerance). Verified ends are recorded as inferred
// proposals; both ends verified raises a confirm-style question. Never sets
// from/to directly; unregistered views cannot verify, so they only note.
async function placeWireHoleEnds(entry, wireSightings) {
  const out = { verified: 0, asked: 0 };
  try {
    if (!entry || !entry.reg || !entry.reg.H || !SM() || !BB()) return out;
    if (!Array.isArray(wireSightings) || !wireSightings.length) return out;
    if (!entry.w || !entry.h) return out;
    const TOL = 0.06;
    for (const ws of wireSightings) {
      const hs = Array.isArray(ws.hole_ends) ? ws.hole_ends
        : (Array.isArray(ws.holeEnds) ? ws.holeEnds : []);
      if (!hs.length) continue;
      const box = ws.bbox;
      if (!Array.isArray(box) || box.length !== 4) continue;
      const ok = [null, null];
      for (let i = 0; i < Math.min(2, hs.length); i++) {
        const h = String(hs[i] || '').trim();
        if (!BB().validHole(LS.project, h)) continue;
        const mm = BB().holeXY(LS.project, h);
        if (!mm) continue;
        const px = SM().applyHomography(entry.reg.H, mm.x, mm.z);
        if (!px || !isFinite(px.x) || !isFinite(px.y)) continue;
        const u = px.x / entry.w, v = px.y / entry.h;
        if (u >= box[0] - TOL && u <= box[2] + TOL && v >= box[1] - TOL && v <= box[3] + TOL) ok[i] = h;
      }
      if (!ok[0] && !ok[1]) continue;
      const r = BB().proposeWireHoles(LS.project, ws.id, ok, entry.name);
      if (r && r.ok) { out.verified++; if (r.asked) out.asked++; }
    }
    // Geometric proposal: project each wire's visible ends through the
    // view's registration to candidate holes. End PIXELS come from color
    // segmentation of the named harness color first (the conductor itself,
    // proved on real pixels); bbox extremes are the fallback for dark
    // neutrals and segmentation misses. Scale-aware gate (pitch from the
    // homography): nearest hole within 0.75 pitches, else UNKNOWN for that
    // end. Fills only ends the model did not verify; both-ends agreement
    // raises the same confirm question. Never sets from/to directly.
    let rgbCache = null, rgbTried = false;
    for (const ws of wireSightings) {
      const box = ws.bbox;
      if (!Array.isArray(box) || box.length !== 4) continue;
      const wire = (LS.project.wires || []).find((x) => x && x.id === ws.id);
      if (!wire || (wire.from && wire.to)) continue;
      let ends = null;
      if (wire.color && SM() && SM().wireColorEnds) {
        try {
          if (!rgbTried) { rgbTried = true; rgbCache = await entryRgb(entry); }
          if (rgbCache) {
            const ce = SM().wireColorEnds(rgbCache.rgb, rgbCache.w, rgbCache.h, box, wire.color);
            if (ce && ce.ends) ends = ce.ends;
          }
        } catch (e) { ends = null; }
      }
      if (!ends) ends = wireEndsFromBox(box, entry.w, entry.h);
      if (!ends) continue;
      const got = projectEndsToHoles(entry, ends);
      if (!got[0] && !got[1]) continue;
      // Don't overwrite model-verified proposals; fill only empty slots.
      const cur = wire.holeProps && Array.isArray(wire.holeProps.ends) ? wire.holeProps.ends : [null, null];
      const fill = [cur[0] || got[0], cur[1] || got[1]];
      if (!fill[0] && !fill[1]) continue;
      const r = BB().proposeWireHoles(LS.project, ws.id, fill, entry.name + ' (geometry)');
      if (r && r.ok) { out.verified++; if (r.asked) out.asked++; }
    }
  } catch (e) { /* hole proposals are advisory; the quiz covers the rest */ }
  return out;
}
// Wire bbox (fractions) -> the two end pixels (full-res [x, y]) along the
// long axis. Returns null for degenerate boxes.
function wireEndsFromBox(box, imgW, imgH) {
  try {
    const x0 = box[0] * imgW, y0 = box[1] * imgH, x1 = box[2] * imgW, y1 = box[3] * imgH;
    const w = x1 - x0, h = y1 - y0;
    if (!(w > 2 && h > 2)) return null;
    if (w >= h) return [[x0, (y0 + y1) / 2], [x1, (y0 + y1) / 2]];
    return [[(x0 + x1) / 2, y0], [(x0 + x1) / 2, y1]];
  } catch (e) { return null; }
}
// End pixels (full-res) -> hole IDs via the view's registration. For each end,
// the nearest projected hole wins only if within 0.75 pitches; otherwise that
// end stays UNKNOWN. Pitch comes from the homography itself (scale-aware
// across close-ups and wide views). Proposals stay confirm-gated: the quiz
// shows them as tentative for the builder to confirm or correct.
function projectEndsToHoles(entry, ends) {
  const out = [null, null];
  try {
    const H = entry.reg.H, W = entry.w, Hz = entry.h;
    const ax = SM().applyHomography(H, 1, 0), a0 = SM().applyHomography(H, 0, 0);
    const az = SM().applyHomography(H, 0, 1);
    const sx = Math.hypot(ax.x - a0.x, ax.y - a0.y), sz = Math.hypot(az.x - a0.x, az.y - a0.y);
    const pitch = 2.54 * (sx + sz) / 2;
    if (!(pitch > 1)) return out;
    const rows = BB().rowsOf(LS.project);
    let Hinv = null;
    try { Hinv = SM().invertHomography(H); } catch (e) { Hinv = null; }
    for (let e = 0; e < 2; e++) {
      const [ex, ey] = ends[e];
      if (ex <= 2 || ey <= 2 || ex >= W - 2 || ey >= Hz - 2) continue; // frame-edge end: wire exits frame, not evidence
      // Off-board end (wire runs onto the table): mapping outside the board
      // must never latch onto an edge hole. True ends sit on holes, which
      // are millimeters inside the outline, so a tight margin is safe.
      if (Hinv) {
        try {
          const q = SM().applyHomography(Hinv, ex, ey);
          if (!q || !BB().onBoard(LS.project, { x: q.x, z: q.y }, 2)) continue;
        } catch (e) { continue; }
      }
      let best = null, bestD = Infinity;
      for (let rr = 1; rr <= rows; rr++) {
        for (const col of ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']) {
          const mm = BB().holeXY(LS.project, 't' + rr + col);
          if (!mm) continue;
          const p = SM().applyHomography(H, mm.x, mm.z);
          const d = Math.hypot(p.x - ex, p.y - ey);
          if (d < bestD) { bestD = d; best = 't' + rr + col; }
        }
      }
      for (let pos = 1; pos <= rows; pos++) {
        for (const rail of ['rL+', 'rL-', 'rR+', 'rR-']) {
          const h = rail + pos;
          const mm = BB().holeXY(LS.project, h);
          if (!mm) continue;
          const p = SM().applyHomography(H, mm.x, mm.z);
          const d = Math.hypot(p.x - ex, p.y - ey);
          if (d < bestD) { bestD = d; best = h; }
        }
      }
      if (best && bestD <= 0.75 * pitch) out[e] = best;
    }
  } catch (e) { /* projection failures abstain */ }
  return out;
}
// Unregistered-view module anchor voting (automatic placement without a
// human anchor). The lock quad (board outline, NOT lattice-verified) maps the
// module bbox to board-mm through the same center-anchored footprint proposal
// as registration. One view only votes (UNKNOWN preserved); >=2 agreeing
// views (same span within 1 row) place inferred pins + orientation question.
// Registration, user holes, and confirmed status always win. General: any
// even-pinned groove-straddling part, no hardcoded rows or pins.
function voteModuleAnchor(compId, quadPx, imgW, imgH, rot, viewName, box) {
  const out = { voted: false, placed: false, agreed: false };
  try {
    if (!BB() || !SM() || !LS.project) return out;
    const c = BB().getComponent(LS.project, compId);
    if (!c || !Array.isArray(c.pins) || c.pins.length < 8 || c.pins.length % 2) return out;
    if (c.status === 'confirmed' || c.pins.some((p) => p.hole)) return out;
    if (c.placedView && !c.anchorLock) return out; // verified/user placement wins
    if (!Array.isArray(quadPx) || quadPx.length !== 4 || !imgW || !imgH) return out;
    if (!Array.isArray(box) || box.length !== 4) return out;
    const rows = BB().rowsOf(LS.project), BW = BB().BOARD_WIDTH, BL = rows * BB().PITCH_MM + 12;
    const r = ((rot | 0) % 4 + 4) % 4;
    const base = [[-BW / 2, -BL / 2], [BW / 2, -BL / 2], [BW / 2, BL / 2], [-BW / 2, BL / 2]];
    // estimateBoardPose fits dst[k] = corners[(k+rot)%4] to src[k], so image
    // corner j corresponds to src[(j-rot)%4].
    const mm = [0, 1, 2, 3].map((j) => base[(((j - r) % 4) + 4) % 4]);
    const H = SM().homography(mm.map(([x, z]) => ({ x, y: z })), quadPx.map((p) => ({ x: p.x, y: p.y })));
    if (!H) return out;
    const Hinv = SM().invertHomography(H);
    if (!Hinv) return out;
    const px2mm = (x, y) => {
      const p = SM().applyHomography(Hinv, x, y);
      return (p && isFinite(p.x) && isFinite(p.y)) ? { x: p.x, z: p.y } : null;
    };
    const n = c.pins.length / 2;
    const prop = BB().proposeModuleHoles(LS.project, px2mm, imgW, imgH, box, n);
    if (!prop || !prop.pins || prop.pins.length !== c.pins.length) return out;
    const start = parseInt(String(prop.pins[0]).slice(1), 10);
    if (!isFinite(start)) return out;
    c.anchorVotes = Array.isArray(c.anchorVotes) ? c.anchorVotes : [];
    const v = c.anchorVotes.find((x) => x && x.view === viewName);
    if (v) v.start = start;
    else {
      c.anchorVotes.push({ view: viewName, start });
      if (c.anchorVotes.length > 12) c.anchorVotes.splice(0, c.anchorVotes.length - 12);
    }
    out.voted = true;
    const agree = c.anchorVotes.some((x) => x && x.view !== viewName && Math.abs(x.start - start) <= 1);
    if (!agree) return out;
    out.agreed = true;
    if (c.placedView) {
      c.notes += `; ${viewName} agrees anchor ${prop.span}`;
      return out;
    }
    let okAll = true;
    prop.pins.forEach((h, i) => { if (!BB().setPinHole(LS.project, compId, i, h).ok) okAll = false; });
    if (!okAll) { BB().clearPlacement(LS.project, compId); return out; }
    c.placedView = viewName; c.bestConf = 0; c.anchorLock = true;
    c.notes += `; anchor auto-placed from ${c.anchorVotes.length} agreeing outline views (unverified, ${viewName})`;
    if (!LS.project.questions.some((q) => q.key === 'orient:' + compId)) {
      LS.project.questions.push({ id: 'Q' + Math.random().toString(36).slice(2, 8),
        key: 'orient:' + compId, kind: 'confirm',
        text: `${compId} was placed spanning ${prop.span} (pin 1 toward the lowest column; outline lock, unverified). If it is mirrored end-to-end, answer "flip".`,
        target: compId, options: [], status: 'open', answer: null, created: new Date().toISOString() });
    }
    out.placed = true;
    return out;
  } catch (e) { out.error = String((e && e.message) || e); return out; }
}
// Load an entry's image and vote its module boxes through the outline lock.
// Never runs on registered views (verified registration takes precedence)
// and never overwrites verified placements.
async function lockAndVoteModules(entry, boxesById) {
  const out = { voted: 0, placed: 0 };
  try {
    if (!entry || entry.reg || !entry.url || !SM() || !BB() || !LS.project) return out;
    if (!boxesById || !Object.keys(boxesById).length) return out;
    const img = await new Promise((resolve, reject) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => reject(new Error('module vote image load failed'));
      im.src = entry.url;
    });
    if (!img.naturalWidth || !entry.w || !entry.h) return out;
    const cv = document.createElement('canvas');
    cv.width = entry.w; cv.height = entry.h;
    const cx = cv.getContext('2d', { willReadFrequently: true });
    if (!cx) return out;
    cx.drawImage(img, 0, 0, entry.w, entry.h);
    const lock = lockStillLum(cv);
    if (!lock) return out;
    for (const comp of LS.project.components) {
      const box = boxesById[comp.id];
      if (!box) continue;
      const res = voteModuleAnchor(comp.id, lock.corners, cv.width, cv.height, lock.rot, entry.name, box);
      if (res.voted) out.voted++;
      if (res.placed) out.placed++;
    }
  } catch (e) { /* outline votes are advisory; registration and quiz cover the rest */ }
  return out;
}
async function pumpQueue() {
  if (SCAN.interpreting || !SCAN.queue.length) return;
  SCAN.interpreting = true;
  setScanStatus(SCAN.active ? 'Reading…' : 'Reading…', 'live');
  const entry = SCAN.queue.shift();
  entry.reading = true; renderPhotos();
  const manual = entry.retryManual === true;
  entry.retryManual = false;
  try {
    await interpretPhoto(entry, { manual });
  } finally {
    SCAN.interpreting = false;
    if (SCAN.active) setScanStatus(SCAN.paused ? 'Paused' : 'Scanning', SCAN.paused ? '' : 'live');
    else setScanStatus('Idle');
    updateRecProgress();
    if (SCAN.queue.length) pumpQueue();
    maybeFinishReconstruct();
  }
}
async function interpretPhoto(entry, opts) {
  opts = opts || {};
  const weak = SCAN.coverage && SM() ? SM().weakestCell(SCAN.coverage) : null;
  const hint = opts.viewHint || (opts.manual ? 'manual photo'
    : `auto capture #${SCAN.autoN}${weak ? `; weakest region ${weak.zone}` : ''}`);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 150000);
  try {
    const res = await fetch(`/api/livescan/projects/${encodeURIComponent(LS.project.id)}/interpret`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-PCB-Token': app.config.csrf_token },
      body: JSON.stringify({ image: entry.name, view_hint: hint, known: knownSummary(), manual: !!opts.manual }),
      signal: ctrl.signal,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `Interpretation failed (${res.status}).`);
    checkpoint();
    const rep = BB().mergeScan(LS.project, data.observations, entry.name);
    // Re-sightings adopt the latest box while unplaced, so a later
    // registered view can place a part the first view could not.
    const placed = autoPlaceFromReg(entry, [...rep.added, ...rep.matched], rep.boxes);
    // Guided rail plugs for wire sightings (needs the merged wire ids).
    const modBox = (() => {
      const comps = (data.observations && data.observations.components) || [];
      const mod = comps.find((c) => c && /esp32|devkit|module/i.test(String(c.kind || '') + String(c.label || '')))
        || comps.find((c) => c && Array.isArray(c.bbox));
      return mod && Array.isArray(mod.bbox) ? mod.bbox : null;
    })();
    const plugs = await placeWirePlugs(entry, rep.wireSightings, modBox);
    const holeEnds = await placeWireHoleEnds(entry, rep.wireSightings);
    const av = await lockAndVoteModules(entry, (rep.photoBoxes && rep.photoBoxes[entry.name]) || {});
    afterEdit();
    entry.interpreted = true;
    entry.obsCount = rep.added.length + rep.matched.length;
    // maybeFinishReconstruct/updateRecProgress read the persisted project
    // images, which are separate objects from the session entries.
    entry.reading = false;
    const pimg = LS.project.images.find((im) => im.name === entry.name);
    if (pimg) { pimg.interpreted = true; pimg.obsCount = entry.obsCount; }
    if (data.observations && data.observations.next_view) SCAN.metaHint = data.observations.next_view;
    renderPhotos();
    const sizeMsg = rep.sizeHint ? ` Board looks ${rep.sizeHint}-size (project is ${LS.project.breadboard.size}).` : '';
    const qMsg = rep.questions.length ? ` ${rep.questions.length} question(s) added.` : '';
    const pMsg = placed.placed ? ` ${placed.placed} part(s) placed from image registration.` : '';
    const gMsg = plugs.placed ? ` ${plugs.placed} wire end(s) located on the rails.` : '';
    const hMsg = holeEnds.asked ? ` ${holeEnds.asked} wire(s) proposed with both ends — confirm in the quiz.`
      : (holeEnds.verified ? ` ${holeEnds.verified} wire end(s) proposed from close-ups.` : '');
    const aMsg = av.placed ? ` Module anchored from ${av.placed} outline view(s) — confirm orientation in the quiz.` : '';
    scanSay(`Merged ${rep.added.length} new, ${rep.matched.length} re-seen from “${entry.name}”.${qMsg}${sizeMsg}${pMsg}${gMsg}${hMsg}${aMsg}`,
      data.usage && data.usage.total_tokens ? `${data.usage.total_tokens} tokens` : '');
  } catch (e) {
    entry.failed = true; entry.reading = false; entry.failMsg = e.message; renderPhotos();
    const pfail = LS.project.images.find((im) => im.name === entry.name);
    if (pfail) { pfail.failed = true; pfail.failMsg = e.message; }
    scanSay(`Could not interpret “${entry.name}”: ${friendlyReadError(e.message)} The photo is kept.`);
  } finally {
    clearTimeout(timer);
  }
}

/* ----- post-scan guided questions: one popup at a time, 3D-linked ----- */
const QUIZ = { list: [], idx: 0 };
function finishCapture() {
  stopScan();
  stopCamera();
  if (!LS.project) return;
  SCAN.phase = 'reconstruct';
  // Re-queue session captures dropped by the scan stop so every saved
  // image is actually read before the model shows.
  for (const n of (SCAN.sessionIds || [])) {
    const e = LS.project.images.find((im) => im.name === n);
    if (e && !e.interpreted && !e.failed && !SCAN.queue.includes(e)) SCAN.queue.push(e);
  }
  if (SCAN.queue.length && !SCAN.interpreting) pumpQueue();
  maybeFinishReconstruct();
}
// One name can own several records (legacy double uploads): flag them all
// so no stale twin can wedge the settle gate.
function markPhoto(name, patch) {
  for (const r of LS.project.images) if (r && r.name === name) Object.assign(r, patch);
  const e = LS.images.find((im) => im && im.name === name);
  if (e) Object.assign(e, patch);
}
// Mirror each settled session target onto its project record (pure: takes
// both arrays, returns nothing). Repairs a record renamed to the wrong slot
// or missing entirely, so the settle gate can never stick at (M-1)/M.
function reconcilePhotoRecords(targets, records) {
  for (const im of targets || []) {
    if (!im || !im.name) continue;
    const patch = { interpreted: !!im.interpreted, failed: !!im.failed,
      failMsg: im.failMsg || '', obsCount: im.obsCount || 0 };
    const rec = (records || []).find((r) => r && r.name === im.name);
    if (rec) Object.assign(rec, patch);
    else records.push({ name: im.name, w: im.w || 0, h: im.h || 0,
      captured: new Date().toISOString(), ...patch });
  }
}
// Reconstruct only from settled reads: quiz launches when every session
// image is interpreted or has a recorded failure. Never show an empty
// model as successful — failures keep their photos and an explanation.
async function reconstructAll() {
  if (!LS.project) return;
  if (!LS.images.length) { showError(new Error('Add breadboard photos first.')); return; }
  if (!LS.backend) { showError(new Error('Photos are still uploading — wait for the upload, then press Reconstruct.')); return; }
  // Unique names only: a double-uploaded photo must merge once, and the
  // settle gate counts session names — a twin record would wedge it at 6/7.
  const seenNames = new Set();
  const targets = LS.images.filter((im) => {
    if (im.failed || seenNames.has(im.name)) return false;
    seenNames.add(im.name);
    return true;
  });
  if (!targets.length) { showError(new Error('No readable photos.')); return; }
  if (SCAN.batchActive) return;
  if (SCAN.settleWatch) { clearTimeout(SCAN.settleWatch); SCAN.settleWatch = 0; }
  SCAN.phase = 'reconstruct';
  SCAN.batchActive = true;
  SCAN.batchStart = Date.now();
  SCAN.batchTimer = setInterval(() => { updateRecProgress(); updateProgress(); }, 1000);
  SCAN.sessionIds = targets.map((im) => im.name);
  for (const im of targets) { im.reading = true; im.failed = false; im.failMsg = ''; }
  renderPhotos();
  setScanStatus('Generating…', 'live');
  scanSay(`Analyzing ${targets.length} photo(s) together…`);
  updateRecProgress();
  // One multimodal request for the whole set (no per-photo cooldown: this
  // is a single user-initiated batch, not the camera scan loop).
  const hints = {}, dims = {};
  targets.forEach((im, i) => {
    hints[im.name] = `upload ${i + 1} of ${targets.length}`;
    dims[im.name] = [im.w || 0, im.h || 0];
  });
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 300000);
  try {
    const res = await fetch(`/api/livescan/projects/${encodeURIComponent(LS.project.id)}/interpret_batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-PCB-Token': app.config.csrf_token },
      body: JSON.stringify({ images: targets.map((im) => im.name), view_hints: hints, dims, known: knownSummary() }),
      signal: ctrl.signal,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `Batch analysis failed (${res.status}).`);
    // Merge each batch response ONCE as a combined reconstruction — never
    // fabricate per-photo results from it. Source mapping comes from each
    // item's own photo field (see mergeScan).
    const acc = { added: [], matched: [], questions: [], photoBoxes: {}, wireSightings: [] };
    const allComps = [];
    for (const b of data.batches || []) {
      const obs = b.observations || {};
      const rep = BB().mergeScan(LS.project, obs, `batch of ${(b.photos || []).length}`);
      acc.added.push(...rep.added);
      acc.matched.push(...rep.matched);
      acc.questions.push(...rep.questions);
      acc.wireSightings.push(...rep.wireSightings);
      Object.assign(acc.photoBoxes, rep.photoBoxes || {});
      for (const c of obs.components || []) allComps.push(c);
    }
    const errByPhoto = {};
    for (const e of data.errors || []) errByPhoto[e.photo] = e.error;
    let placedN = 0, plugN = 0;
    for (const im of targets) {
      if (errByPhoto[im.name]) {
        im.reading = false; im.failed = true; im.failMsg = errByPhoto[im.name];
        markPhoto(im.name, { failed: true, failMsg: im.failMsg });
        continue;
      }
      try {
      // Partial-board fallback with all views present (upload-time attempt
      // may have lacked a locked calibration view). Existing locks win;
      // this only fills entries the full-board lock abstained on.
      if (!im.reg) {
        try { await ensurePartialReg(im); } catch (e) { /* partial abstains; placement skips */ }
      }
      // Placement uses only this photo's own sightings (its own frame).
      const mine = acc.wireSightings.filter((w) => !w.photo || w.photo === im.name);
      const modBox = (() => {
        const mine2 = allComps.filter((c) => c && c.photo === im.name && Array.isArray(c.bbox));
        const mod = mine2.find((c) => /esp32|devkit|module/i.test(String(c.kind || '') + String(c.label || '')))
          || mine2[0]
          || allComps.find((c) => c && Array.isArray(c.bbox));
        return mod && Array.isArray(mod.bbox) ? mod.bbox : null;
      })();
      const plugs = await placeWirePlugs(im, mine, modBox);
      plugN += plugs.placed || 0;
      // Hole-end proposals are confirm-gated, never auto-placed.
      const he = await placeWireHoleEnds(im, mine);
      if (he.asked) acc.questions.push(`${he.asked} wire(s) proposed with both ends from ${im.name} — confirm in the quiz.`);
      const avm = await lockAndVoteModules(im, acc.photoBoxes[im.name] || {});
      if (avm.placed) acc.questions.push(`Module anchored from outline lock in ${im.name} — confirm orientation in the quiz.`);
      const placed = autoPlaceFromReg(im, [...acc.added, ...acc.matched], acc.photoBoxes[im.name] || {});
      placedN += placed.placed || 0;
      const obsN = allComps.filter((c) => c && c.photo === im.name).length
        + mine.length;
      im.reading = false; im.interpreted = true; im.obsCount = obsN;
      markPhoto(im.name, { interpreted: true, obsCount: obsN });
      } catch (e) {
        // One photo's placement must never discard the other photos'
        // results: record it, keep going, offer Retry on that photo.
        im.reading = false; im.failed = true; im.failMsg = e.message;
        markPhoto(im.name, { failed: true, failMsg: e.message });
      }
    }
    // Settle-gate invariant: every session target must have exactly one
    // project record carrying its settled state. The per-photo loop settles
    // the session entries, but the progress bar, the "Analyzing photo N of
    // M" line, and the quiz gate all count project records by name — a past
    // upload rename that missed its slot left one photo unsettled forever
    // (stuck at 6/7, lingering Generating…, quiz never launching). Repair
    // the mirror here so one divergence can never wedge the run again.
    reconcilePhotoRecords(targets, LS.project.images);
    afterEdit();
    renderPhotos();
    const failedNames = targets.filter((im) => im.failed).map((im) => im.name);
    if (failedNames.length) {
      const why = targets.find((im) => failedNames.includes(im.name)).failMsg || 'unknown error';
      scanSay(`Merged the readable photos — but ${failedNames.join(', ')} could not be read (${why}). Press Retry on ${failedNames.length > 1 ? 'those photos' : 'that photo'} below.`);
    }
    const u = data.usage || {};
    const useMsg = u.total_tokens ? `${u.total_tokens} tokens${u.cost_usd ? ` (~$${Number(u.cost_usd).toFixed(4)})` : ''} in ${u.requests || 1} request(s)` : '';
    scanSay(`Merged ${acc.added.length} new, ${acc.matched.length} re-seen from ${targets.length} photo(s).`
      + (acc.questions.length ? ` ${acc.questions.length} question(s) added.` : '')
      + (placedN ? ` ${placedN} part(s) placed from image registration.` : '')
      + (plugN ? ` ${plugN} wire end(s) located on the rails.` : ''), useMsg);
  } catch (e) {
    for (const im of targets) {
      if (!im.interpreted) { im.reading = false; im.failed = true; im.failMsg = e.message; }
      for (const r of LS.project.images) {
        if (r && r.name === im.name && !r.interpreted) { r.failed = true; r.failMsg = e.message; }
      }
    }
    renderPhotos();
    setScanStatus('Idle', '');
    scanSay(`Could not analyze the photos: ${friendlyReadError(e.message)} Photos are kept — press Retry on a photo or Reconstruct again.`);
  } finally {
    clearTimeout(timer);
    SCAN.batchActive = false;
    if (SCAN.batchTimer) { clearInterval(SCAN.batchTimer); SCAN.batchTimer = null; }
    // The photo path sets Generating… on its status pill at start; always
    // clear it here so it can never linger beside a finished progress bar.
    setScanStatus('Idle', '');
    updateRecProgress();
    updateProgress();
    maybeFinishReconstruct();
    // Settle watchdog: if any session target is STILL unsettled 20 s after
    // the batch ended (an unknown client-side stall outside the paths
    // above), fail it honestly with a Retry pointer and finish — a silent
    // 6/7 with no questions must be impossible. Fires only when the primary
    // path already failed to settle; never marks anything interpreted.
    if (SCAN.phase === 'reconstruct' && LS.project) {
      if (SCAN.settleWatch) clearTimeout(SCAN.settleWatch);
      // Identity at arm time: a project switch or a newer run must never
      // let this timer touch another project's records.
      SCAN.settleWatchFor = { pid: LS.project.id, ids: (SCAN.sessionIds || []).slice() };
      SCAN.settleWatch = setTimeout(() => {
        SCAN.settleWatch = 0;
        forceSettleStragglers();
      }, 20000);
    }
  }
}
// Watchdog body (named for tests): fail honestly any session target that
// never settled, then finish the run. Never marks anything interpreted;
// never touches another project (identity minted at arm time).
function forceSettleStragglers() {
  try {
    const forId = SCAN.settleWatchFor;
    SCAN.settleWatchFor = null;
    if (SCAN.phase !== 'reconstruct' || !LS.project || !forId) return 0;
    if (LS.project.id !== forId.pid) return 0;
    const wedged = [];
    for (const n of forId.ids || []) {
      const e = LS.project.images.find((im) => im && im.name === n);
      if (e && !e.interpreted && !e.failed) {
        e.failed = true;
        e.failMsg = 'Photo analysis did not settle — press Retry on this photo.';
        wedged.push(n);
      }
    }
    for (const im of LS.images || []) {
      if (im && wedged.includes(im.name) && !im.interpreted && !im.failed) {
        im.failed = true; im.reading = false;
        im.failMsg = 'Photo analysis did not settle — press Retry on this photo.';
      }
    }
    if (!wedged.length) return 0;
    markDirty();
    renderPhotos();
    updateRecProgress();
    updateProgress();
    scanSay(`Finished with ${wedged.length} unsettled photo(s) marked for Retry (${wedged.join(', ')}). The rest merged — questions open below.`);
    maybeFinishReconstruct();
    return wedged.length;
  } catch (e) { /* watchdog itself must never wedge */ return 0; }
}
function updateRecProgress() {
  const el = document.getElementById('ls-rec-progress');
  if (!el || !LS.project) return;
  const ids = SCAN.sessionIds || [];
  if (SCAN.phase !== 'reconstruct' || !ids.length) { el.textContent = ''; return; }
  if (SCAN.batchActive) {
    const elapsed = SCAN.batchStart ? Math.round((Date.now() - SCAN.batchStart) / 1000) : 0;
    el.textContent = `Analyzing ${ids.length} photo(s) together… (${elapsed}s elapsed — usually about a minute for ${ids.length} photos)`;
    return;
  }
  const imgs = ids.map((n) => LS.project.images.find((im) => im.name === n));
  const done = imgs.filter((e) => e && e.interpreted).length;
  const failed = imgs.filter((e) => e && e.failed);
  if (done + failed.length >= ids.length && ids.length) {
    // Terminal settled state: never leave "Analyzing photo 7 of 7" on
    // screen when nothing more will happen.
    el.textContent = failed.length
      ? `${done} of ${ids.length} analyzed — ${failed.length} need${failed.length > 1 ? '' : 's'} Retry (${failed.map((e) => e.name).join(', ')})`
      : `Analyzed ${ids.length} of ${ids.length} photos.`;
    return;
  }
  el.textContent = `Analyzing photo ${done + 1} of ${ids.length}…`;
}
function maybeFinishReconstruct() {
  if (SCAN.phase !== 'reconstruct' || !LS.project) return;
  const imgs = LS.project.images;
  const settled = (SCAN.sessionIds || []).filter((n) => {
    const e = imgs.find((im) => im.name === n);
    return e && (e.interpreted || e.failed);
  }).length;
  const left = (SCAN.sessionIds || []).length - settled;
  if (left > 0) {
    setScanStatus('Generating…', 'live');
    scanSay(`Generating 3D breadboard… — reading ${left} view(s).`);
    return;
  }
  const fails = (SCAN.sessionIds || []).filter((n) => {
    const e = imgs.find((im) => im.name === n);
    return e && e.failed;
  }).length;
  if (fails && !LS.project.components.length && !LS.project.wires.length) {
    scanSay(`Reading failed on ${fails} view(s) — photos kept below. Answer what you can, or press Record to retry the capture.`);
  }
  try {
    launchQuiz();
  } catch (e) {
    setScanStatus('Idle', '');
    scanSay('Analysis finished but the questions hit a display problem (' +
      (e && e.message ? e.message : e) + '). Press “Answer questions” below to open them.');
  }
}
function buildQuizList() {
  // Popups ask structural questions first (they change the model), then
  // purpose/identity, then model observations. Never bombard: cap at 10.
  const kindRank = { 'module-anchor': 0, orient: 1, placement: 2, wiring: 3,
    purpose: 4, identify: 5, intent: 6, vision: 7, function: 8, empty: 9 };
  // Both-ends-verified wire proposals starve under a stable kind sort: created
  // last, they sort behind 10+ older wiring asks and the cap hides them.
  // Rank wireconf above plain wiring (below structural placements), so a
  // concrete confirm-or-correct proposal always surfaces while same-wire
  // open asks wait their turn.
  const rankOf = (q) => (q.key && q.key.indexOf('wireconf:') === 0) ? 2.5
    : (kindRank[q.kind] ?? 5);
  return BB().openQuestions(LS.project)
    .sort((a, b) => rankOf(a) - rankOf(b))
    .slice(0, 10);
}
// Manual entry to the popup queue: if the automatic opening ever glitches,
// one tap re-opens it. Never throws silently — failures show as messages.
function openQuizManual() {
  if (!LS.project) return;
  try {
    refreshQuestions();
    QUIZ.list = buildQuizList();
    QUIZ.idx = 0;
    if (!QUIZ.list.length) { scanSay('No open questions — the reconstruction stands as is.'); return; }
    renderQuizPopup();
  } catch (e) {
    scanSay('Could not open the questions: ' + (e && e.message ? e.message : e));
  }
}
function launchQuiz() {
  SCAN.phase = 'done';
  if (!LS.project) return;
  // Fold in fresh suggestions, then snapshot the open queue.
  refreshQuestions();
  if (!LS.project.components.length && !LS.project.wires.length
      && !LS.project.questions.some((q) => q.kind === 'empty')) {
    LS.project.questions.push({ id: 'Q' + Math.random().toString(36).slice(2, 8),
      key: 'empty:board', kind: 'empty', text: 'No components were detected. Is the breadboard empty?',
      target: null, options: ['Yes, it is empty', 'No, it has parts'], status: 'open',
      answer: null, created: new Date().toISOString() });
  }
  QUIZ.list = buildQuizList();
  QUIZ.idx = 0;
  // Announce the outcome on the primary instruction line (both the camera
  // and photo flows end here): without this the guide keeps whatever the
  // last capture said, and nothing confirms the merge finished.
  const nViews = (SCAN.sessionIds && SCAN.sessionIds.length) || LS.images.length;
  scanSay(QUIZ.list.length
    ? `Merged ${nViews} view(s) — ${QUIZ.list.length} question(s) to review.`
    : `Merged ${nViews} view(s) — review the 3D model below.`);
  if (!QUIZ.list.length) { showVerifySummary(); return; }
  renderQuizPopup();
}
function quizTarget(q) {
  if (!q) return null;
  if (q.target) {
    if (BB().getComponent(LS.project, q.target)) return { kind: 'part', id: q.target };
    if (LS.project.wires.some((w) => w.id === q.target)) return { kind: 'wire', id: q.target };
  }
  return null;
}
function quizHighlight(q) {
  const t = quizTarget(q);
  if (!t) return;
  LS.selected = t.id;
  renderParts(); renderWires(); renderInspector();
  if (LS.twin) LS.twin.select(t.id);
  const panel = document.getElementById('ls-twin-panel');
  if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
function quizContext(q) {
  const t = quizTarget(q);
  if (!t) return '';
  const o = t.kind === 'part' ? BB().getComponent(LS.project, t.id)
    : LS.project.wires.find((w) => w.id === t.id);
  if (!o) return '';
  const bits = [];
  if (o.kind) bits.push(BB().PARTS[o.kind] ? BB().PARTS[o.kind].label : o.kind);
  if (o.value) bits.push('value ' + o.value);
  if (o.color) bits.push(o.color + ' wire');
  if (o.region) bits.push('seen ' + o.region);
  if (o.notes) bits.push(o.notes.slice(0, 160));
  return bits.join(' · ');
}
function renderQuizPopup() {
  closeQuizPopup();
  const q = QUIZ.list[QUIZ.idx];
  if (!q) { showVerifySummary(); return; }
  // Skip questions answered elsewhere while the quiz ran.
  if (q.status !== 'open') { QUIZ.idx++; renderQuizPopup(); return; }
  quizHighlight(q);
  const overlay = node('div', 'ls-quiz');
  const card = node('div', 'ls-quiz-card');
  const head = node('div', 'ls-row');
  const count = node('p', 'muted', `Question ${QUIZ.idx + 1} of ${QUIZ.list.length} · scan clarification`);
  const close = node('button', 'text-button', '✕ Close');
  close.type = 'button';
  close.title = 'Close the popup — open questions stay in the list below and the Answer button reopens them.';
  close.setAttribute('aria-label', 'Close questions');
  close.addEventListener('click', () => closeQuizPopup());
  head.append(count, close);
  card.append(head);
  card.append(node('h4', '', q.text));
  const ctx = quizContext(q);
  if (ctx) card.append(node('p', 'muted', ctx));
  const t = quizTarget(q);
  if (t) card.append(node('p', 'ls-quiz-target', `About ${t.id} — highlighted in the 3D view.`));
  const input = node('input', 'ls-quiz-input');
  input.type = 'text'; input.placeholder = 'Type your answer…';
  input.setAttribute('aria-label', q.text);
  card.append(input);
  if (q.options && q.options.length) {
    const opts = node('div', 'ls-quiz-opts');
    for (const o of q.options.slice(0, 6)) {
      const b = node('button', 'secondary', o); b.type = 'button';
      b.addEventListener('click', () => { input.value = o; });
      opts.append(b);
    }
    card.append(opts);
  }
  const row = node('div', 'ls-row');
  const save = node('button', 'primary', 'Save answer'); save.type = 'button';
  const skip = node('button', 'secondary', 'Skip'); skip.type = 'button';
  save.addEventListener('click', () => quizAnswer(q, input.value));
  skip.addEventListener('click', () => { QUIZ.idx++; renderQuizPopup(); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') quizAnswer(q, input.value); });
  const dk = node('button', 'secondary', "I don't know"); dk.type = 'button';
  dk.title = 'Keep the uncertainty recorded without guessing.';
  dk.addEventListener('click', () => quizAnswer(q, "I don't know"));
  row.append(save, skip, dk);
  if (LS.stream || LS.backend) {
    const shot = node('button', 'secondary', 'Take close-up'); shot.type = 'button';
    shot.title = 'Capture a close-up frame, attach it to this question, and read it with AI.';
    shot.addEventListener('click', () => quizCloseUp(q, shot));
    row.append(shot);
  }
  card.append(row);
  const thumbs = node('div', 'ls-quiz-shots');
  for (const name of q.images || []) thumbs.append(node('span', 'muted', '📷 ' + name));
  if (thumbs.children.length) card.append(thumbs);
  overlay.append(card);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeQuizPopup(); });
  document.body.append(overlay);
  setTimeout(() => input.focus(), 50);
}
function closeQuizPopup() {
  document.querySelectorAll('.ls-quiz').forEach((n) => n.remove());
}
function quizAnswer(q, value) {
  const text = String(value || '').trim();
  if (!text) return;
  checkpoint();
  // Application lives in BB.answerQuestion (shared with the panel), so an
  // answer updates pins, wires, identities and confirmations in one place.
  const applied = BB().answerQuestion(LS.project, q.id, text);
  afterEdit();
  saveNow(true);
  if (applied && applied.ok === false) { renderQuizPopup(); return; }
  QUIZ.idx++;
  renderQuizPopup();
}
async function quizCloseUp(q, btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Capturing…'; }
  try {
    const canvas = grabFrameCanvas(1280);
    if (!canvas) throw new Error('Start the camera first.');
    const blob = await new Promise((r) => canvas.toBlob(r, 'image/jpeg', 0.9));
    if (!blob) throw new Error('Capture failed.');
    const entry = await ingestFile(new File([blob], `quiz-${Date.now()}.jpg`, { type: 'image/jpeg' }));
    if (!entry) throw new Error('Could not save the close-up.');
    attachRegistration(entry, canvas);
    q.images = (q.images || []).concat(entry.name);
    markDirty();
    if (LS.backend && !String(entry.url || '').startsWith('data:')) {
      checkpoint();
      await interpretPhoto(entry, { manual: true, viewHint: 'Close-up for question: ' + q.text });
    }
    renderQuizPopup(); // same question, now with the new evidence attached
  } catch (e) {
    showError(e);
    if (btn) { btn.disabled = false; btn.textContent = 'Take close-up'; }
  }
}
function showVerifySummary() {
  closeQuizPopup();
  refreshQuestions();
  refreshFindings();
  updateProgress();
  const open = BB().openQuestions(LS.project).length;
  const findings = BB().runChecks(LS.project);
  const errs = findings.filter((f) => f.severity === 'error').length;
  const warns = findings.filter((f) => f.severity === 'warning').length;
  scanSay(open
    ? `Capture finished with ${open} question(s) still open — see the Questions list.`
    : 'All scan questions answered.',
  `Circuit checks: ${errs} error(s), ${warns} warning(s). Coverage complete is not an electrical sign-off.`);
  const panel = document.getElementById('ls-findings');
  if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function renderPhotos() {
  const box = document.getElementById('ls-photos');
  if (!box) return;
  box.querySelectorAll('.ls-strip, .ls-empty').forEach((n) => n.remove());
  if (!LS.images.length) {
    box.append(node('p', 'muted ls-empty', 'No photos yet.'));
    return;
  }
  const strip = node('div', 'ls-strip');
  for (const im of LS.images) {
    const fig = node('figure', 'ls-thumb');
    const el = document.createElement('img');
    el.src = relPhotoUrl(im.url); el.alt = im.name; el.loading = 'lazy';
    el.addEventListener('click', () => viewPhoto(im));
    el.addEventListener('error', () => {
      if (!im.imgBroken) { im.imgBroken = true; renderPhotos(); }
    });
    const cap = node('figcaption', 'muted ls-filename', shortName(im.name));
    cap.title = `${im.name}${im.w ? ` · ${im.w}×${im.h}` : ''}`;
    fig.append(el, cap);
    // One clear state per photo; technical detail stays in tooltips.
    const state = photoState(im);
    const badge = node('span', state.cls, state.text);
    badge.title = state.detail || '';
    fig.append(badge);
    const row = node('div', 'ls-row ls-thumb-actions');
    if (im.failed && !im.reading) {
      const retry = node('button', 'secondary', 'Retry'); retry.type = 'button';
      retry.title = state.detail || 'Try reading this photo again.';
      retry.addEventListener('click', () => {
        im.failed = false; im.failMsg = ''; im.reading = true;
        im.retryManual = true; // user-initiated: no camera cooldown
        const pimg = LS.project ? LS.project.images.find((x) => x.name === im.name) : null;
        if (pimg) pimg.failed = false;
        if (!SCAN.queue.includes(im)) SCAN.queue.push(im);
        renderPhotos(); pumpQueue();
      });
      row.append(retry);
    }
    const rm = node('button', 'text-button', 'Remove'); rm.type = 'button';
    rm.addEventListener('click', () => {
      LS.images = LS.images.filter((x) => x !== im);
      if (LS.project) LS.project.images = LS.project.images.filter((x) => x.name !== im.name);
      markDirty(); renderPhotos(); updateGuide();
    });
    row.append(rm);
    fig.append(row);
    strip.append(fig);
  }
  box.append(strip);
}
// Photo URLs are always same-origin relative paths: strip any absolute
// origin (e.g. a stale port from an older server) so thumbnails and the
// viewer never point at a dead backend.
function relPhotoUrl(u) {
  const s = String(u || '');
  if (/^https?:\/\//i.test(s)) {
    try { const p = new URL(s); return p.pathname + p.search + p.hash; } catch (_) { return s; }
  }
  return s;
}
function shortName(name) {
  const s = String(name || '');
  if (s.length <= 26) return s;
  const dot = s.lastIndexOf('.');
  const ext = dot > 0 ? s.slice(dot) : '';
  return s.slice(0, 18) + '…' + ext;
}
function photoState(im) {
  if (im.imgBroken) return { cls: 'error ls-badge', text: '⚠ photo not loading',
    detail: 'The image file could not be loaded. Remove and re-add this photo.' };
  if (im.reading) return { cls: 'muted ls-badge', text: '… analyzing' };
  if (im.interpreted) return { cls: 'muted ls-badge', text: `✓ read${im.obsCount ? ` (${im.obsCount})` : ''}` };
  if (im.failed) return { cls: 'error ls-badge', text: '⚠ ' + friendlyReadError(im.failMsg), detail: im.failMsg || '' };
  return { cls: 'muted ls-badge', text: 'queued' };
}
function friendlyReadError(msg) {
  const s = String(msg || '');
  if (/larger than 2 MB/i.test(s)) return 'photo too large for the running server — restart PreFlight, then press Retry';
  if (/API key/i.test(s)) return 'add your Meta API key in Vector settings, then press Retry';
  if (/missing|not found|no such|decode/i.test(s)) return 'photo missing or unreadable on the server — remove and re-add it';
  if (/timed out|timeout/i.test(s)) return 'analysis timed out — press Retry';
  return s ? s.slice(0, 110) : 'could not be read — press Retry';
}

function viewPhoto(im) {
  const overlay = node('div', 'ls-lightbox');
  const img = document.createElement('img');
  img.src = relPhotoUrl(im.url); img.alt = im.name;
  const close = node('button', 'secondary', 'Close');
  close.type = 'button';
  close.addEventListener('click', () => overlay.remove());
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  overlay.append(img, close);
  document.body.append(overlay);
}

/* ----- 3D twin ----- */
function twinPanel() {
  const box = node('section', 'panel');
  box.id = 'ls-twin-panel';
  const head = node('div', 'ls-row');
  head.append(node('h3', '', '3D reconstruction'));
  const trackBtn = node('button', 'text-button ls-track-toggle', 'Track: on');
  trackBtn.type = 'button'; trackBtn.id = 'ls-track-toggle';
  trackBtn.title = 'When on, the board follows the physical orientation during scanning.';
  trackBtn.addEventListener('click', toggleTrackPose);
  head.append(trackBtn);
  const hint = node('span', 'muted', 'Drag to orbit · scroll to zoom · right-drag to pan · click a part');
  const mount = node('div', 'ls-twin');
  mount.id = 'ls-twin';
  mount.setAttribute('aria-label', 'Interactive 3D breadboard');
  const feed = document.createElement('video');
  feed.id = 'ls-track-video'; feed.muted = true; feed.playsInline = true; feed.hidden = true;
  feed.setAttribute('aria-label', 'Live camera feed behind reconstruction');
  // Dedicated WebGL container: twin (re)mounts must never wipe the
  // scanner overlay or the camera feed (mountTwin replaces only this).
  const gl = node('div', 'ls-twin-gl');
  gl.id = 'ls-twin-gl';
  gl.setAttribute('aria-hidden', 'false');
  mount.append(feed, gl);
  // Tracker outline + step positioning guide: the measured quad in feed
  // space (only from a verified lock), plus the large positioning box for
  // the current guided view (passive frame, never a detector by itself).
  const ov = document.createElement('canvas');
  ov.id = 'ls-track-overlay';
  mount.append(ov);
  // Viewport overlay: the main breadboard IS the scanner. Progress and
  // guidance live on the viewport; the circular Record button starts the
  // real camera/scanner workflow (see recordPress).
  const top = node('div', 'ls-vp-top');
  const bar = node('div', 'ls-vp-bar');
  const fill = node('div', 'ls-progress-fill'); fill.id = 'ls-progress-fill';
  bar.append(fill);
  const plabel = node('span', 'ls-vp-pct', '0%');
  plabel.id = 'ls-progress-label';
  const psub = node('span', 'ls-vp-sub', '');
  psub.id = 'ls-progress-sub';
  const stepEl = node('span', 'ls-vp-step', '');
  stepEl.id = 'ls-vp-step';
  stepEl.hidden = true;
  const segs = node('div', 'ls-vp-segs');
  segs.id = 'ls-vp-segs';
  segs.setAttribute('aria-label', 'Completed views');
  for (let i = 0; i < 6; i++) segs.append(node('span', ''));
  const verify = node('span', 'ls-vp-verify', '');
  verify.id = 'ls-vp-verify';
  verify.title = 'Unconfirmed circuit details. Full scan coverage is not an electrical sign-off.';
  top.append(bar, plabel, psub, stepEl, segs, verify);
  // The single camera viewport IS the scanner during capture: the live
  // feed fills the mount (twin hidden), the positioning box frames the
  // current view, and six segments track saved captures. The 3D twin
  // returns after the camera stops.
  const guide = node('div', 'ls-vp-guide', 'Point the camera at the breadboard, then press Record.');
  guide.id = 'ls-vp-guide';
  guide.setAttribute('aria-live', 'polite');
  const controls = node('div', 'ls-vp-controls');
  const rec = node('button', 'ls-record', '');
  rec.type = 'button'; rec.id = 'ls-record';
  rec.setAttribute('aria-label', 'Record: start scanning');
  rec.title = 'Record: start scanning';
  rec.addEventListener('click', recordPress);
  const status = node('span', 'ls-pill', 'Idle');
  status.id = 'ls-scan-status';
  const finish = node('button', 'ls-vp-finish', 'Finish');
  finish.type = 'button'; finish.id = 'ls-finish';
  finish.title = 'End capturing and answer guided questions.';
  finish.hidden = true;
  finish.addEventListener('click', finishCapture);
  controls.append(status, rec, finish);
  // Photo workflow is the primary interface: the live-camera Record button,
  // Finish, step pill and capture segments only appear while scanning.
  // refreshRecord() unhides them whenever a camera scan is active.
  if (!SCAN.active) {
    rec.hidden = true; finish.hidden = true; stepEl.hidden = true;
    segs.hidden = true;
    guide.textContent = 'Add breadboard photos above, then press Reconstruct Breadboard.';
    status.textContent = 'Idle';
  }
  mount.append(top, guide, controls);
  box.append(head, hint, mount);
  return box;
}
// Generation progress on the main viewport: how much of the 3D model is
// built (parts placed of parts inferred), plus a clear done state.
function setGenProgress(done, total, ready, parts, wires, failedNames) {
  const fill = document.getElementById('ls-progress-fill');
  const label = document.getElementById('ls-progress-label');
  const sub = document.getElementById('ls-progress-sub');
  if (!fill || !label) return;
  if (done == null) {
    fill.style.width = '0%'; fill.classList.toggle('done', false);
    label.textContent = '0%';
    if (sub) sub.textContent = '';
    return;
  }
  const pct = total > 0 ? Math.round((100 * done) / total) : 100;
  fill.style.width = pct + '%';
  fill.classList.toggle('done', !!ready);
  if (ready && !failedNames) {
    label.textContent = '✓ Done';
    if (sub) sub.textContent = `3D ready — ${parts} part(s), ${wires} wire(s). Select a part to inspect it.`;
  } else if (ready) {
    // Settled with failures: honest partial-done, and where to fix it.
    label.textContent = `⚠ ${done}/${total}`;
    if (sub) sub.textContent = `Done with ${failedNames} unread — press Retry on its photo below.`;
  } else {
    label.textContent = pct + '%';
    if (sub) sub.textContent = `Building 3D… ${done}/${total} photos merged`;
  }
}
// The Record button is the scanner's single entry point: idle starts the
// camera (when needed) and the real auto-scan loop; while active it
// pauses/resumes. Finish finalizes into the question flow.
async function recordPress() {
  if (SCAN.active) { togglePauseScan(); return; }
  const video = document.getElementById('ls-video');
  if (!video || !video.videoWidth) {
    const sel = document.getElementById('ls-camera-sel');
    await startCamera(sel ? sel.value : '');
    const v2 = document.getElementById('ls-video');
    if (!v2 || !v2.videoWidth) return; // startCamera already explained why
  }
  startScan();
}
// Record glyph + Finish visibility derive from SCAN state, refreshed on
// every status change.
function refreshRecord() {
  const rec = document.getElementById('ls-record');
  if (rec) {
    const label = !SCAN.active ? 'Record: start scanning'
      : SCAN.paused ? 'Resume scanning' : 'Pause scanning';
    rec.setAttribute('aria-label', label);
    rec.title = label;
    rec.classList.toggle('live', SCAN.active && !SCAN.paused);
    rec.classList.toggle('paused', SCAN.active && SCAN.paused);
  }
  const finish = document.getElementById('ls-finish');
  if (finish) finish.hidden = !SCAN.active;
  if (rec) rec.hidden = !SCAN.active;
  const stepEl = document.getElementById('ls-vp-step');
  if (stepEl) {
    if (SCAN.active) {
      const i = Math.min(SCAN.step || 0, 5);
      stepEl.hidden = false;
      stepEl.textContent = `Step ${i + 1} of 6 · ${STEPS[i].short}`;
    } else stepEl.hidden = true;
  }
  const segs = document.getElementById('ls-vp-segs');
  if (segs) segs.hidden = !SCAN.active;
  if (segs) {
    // A segment completes ONLY when its image is captured and saved.
    const done = Math.min(SCAN.step || 0, 6);
    [...segs.querySelectorAll('span')].forEach((sp, i) => sp.classList.toggle('done', i < done));
  }
  const mount = document.getElementById('ls-twin');
  if (mount) mount.classList.toggle('capturing', SCAN.active);
  const steps = document.getElementById('ls-steps');
  if (steps) {
    const cur = SCAN.active ? Math.min(SCAN.step || 0, 5) : -1;
    [...steps.querySelectorAll('li')].forEach((li, i) => {
      li.classList.toggle('done', SCAN.active && i < (SCAN.step || 0));
      li.classList.toggle('current', i === cur);
    });
  }
}

function mountTwin() {
  const mountEl = document.getElementById('ls-twin-gl') || document.getElementById('ls-twin');
  if (!mountEl) return;
  if (LS.twin) { LS.twin.dispose(); LS.twin = null; }
  if (!window.LiveTwin) {
    mountEl.replaceChildren(node('p', 'error', '3D engine failed to load. Parts can still be edited in the list below.'));
    return;
  }
  try {
    LS.twin = window.LiveTwin.mount(mountEl, { breadboard: BB() });
  } catch (e) {
    mountEl.replaceChildren(node('p', 'error', '3D unavailable in this browser (' + e.message + '). Parts can still be edited below.'));
    LS.twin = null;
    return;
  }
  LS.twin.setProject(LS.project);
  LS.twin.onSelect((id) => { LS.selected = id; renderInspector(); });
}

function refreshTwin() {
  if (LS.twin && LS.project) LS.twin.setProject(LS.project);
}

// Highlight the full physical nets of the given holes: populated net members
// plus every hole on the same copper strips.
function highlightHoles(holes) {
  if (!LS.twin) return;
  const all = new Set();
  for (const h of holes || []) {
    if (!h || !BB().parseHole(h)) continue;
    all.add(h);
    const n = BB().netOfHole(LS.project, h);
    if (n) n.holes.forEach((x) => all.add(x));
  }
  LS.twin.highlightNet(BB().expandNetHoles(LS.project, [...all]));
}

/* ----- parts & wires ----- */
function partsPanel() {
  const box = node('section', 'panel');
  box.id = 'ls-parts';
  box.innerHTML = `<h3>Parts</h3>`;
  const add = node('form', 'ls-row ls-add');
  const kind = node('select');
  kind.setAttribute('aria-label', 'Part kind');
  for (const [k, def] of Object.entries(BB().PARTS)) {
    const o = node('option', '', def.label);
    o.value = k;
    kind.append(o);
  }
  const val = node('input');
  val.placeholder = 'Value (e.g. 10k, red, ESP32-WROOM)';
  val.setAttribute('aria-label', 'Part value');
  val.maxLength = 80;
  const btn = node('button', 'secondary', 'Add part');
  btn.type = 'submit';
  add.append(kind, val, btn);
  add.addEventListener('submit', (e) => {
    e.preventDefault();
    checkpoint();
    const res = BB().addComponent(LS.project, { kind: kind.value, value: val.value.trim(), status: 'unknown' });
    if (!res.ok) { showError(new Error(res.error)); return; }
    val.value = '';
    afterEdit();
    LS.selected = res.id;
    renderInspector();
  });
  box.append(add);
  const list = node('div', 'ls-list');
  list.id = 'ls-parts-list';
  box.append(list);
  const insp = node('div', 'ls-inspector');
  insp.id = 'ls-inspector';
  box.append(insp);
  return box;
}

function statusBadge(status) {
  const labels = { observed: 'Observed', inferred: 'Inferred', confirmed: 'Confirmed', unknown: 'Unknown' };
  return labels[status] || 'Unknown';
}

function renderParts() {
  const list = document.getElementById('ls-parts-list');
  if (!list) return;
  list.innerHTML = '';
  if (!LS.project.components.length) {
    list.append(node('p', 'muted', 'No parts yet. Add parts observed in your photos.'));
  }
  for (const c of LS.project.components) {
    const row = node('button', 'ls-item' + (LS.selected === c.id ? ' selected' : ''), '');
    row.type = 'button';
    const placed = c.pins.filter((p) => p.hole).length;
    row.innerHTML = `<strong>${esc(c.id)}</strong><span>${esc(BB().PARTS[c.kind].label)}${c.value ? ' · ' + esc(c.value) : ''}</span><span class="muted">${placed}/${c.pins.length} pins · ${statusBadge(c.status)}</span>`;
    row.addEventListener('click', () => {
      LS.selected = c.id;
      if (LS.twin) LS.twin.select(c.id);
      renderParts(); renderInspector();
    });
    list.append(row);
  }
  renderInspector();
}

function renderInspector() {
  const insp = document.getElementById('ls-inspector');
  if (!insp) return;
  insp.innerHTML = '';
  const c = LS.project.components.find((x) => x.id === LS.selected);
  const w = LS.project.wires.find((x) => x.id === LS.selected);
  if (!c && !w) {
    insp.append(node('p', 'muted', 'Select a part or wire to inspect and edit it.'));
    return;
  }
  if (c) {
    insp.append(node('h4', '', `${c.id} · ${BB().PARTS[c.kind].label}`));
    const grid = node('div', 'ls-form');
    const valRow = node('label', '', 'Value ');
    const val = node('input'); val.value = c.value || ''; val.maxLength = 80;
    valRow.append(val);
    const statusRow = node('label', '', 'Evidence ');
    const status = node('select');
    for (const s of ['observed', 'inferred', 'confirmed', 'unknown']) {
      const o = node('option', '', statusBadge(s));
      o.value = s; if (s === c.status) o.selected = true;
      status.append(o);
    }
    statusRow.append(status);
    grid.append(valRow, statusRow);
    const pins = node('div', 'ls-pins');
    c.pins.forEach((p, i) => {
      const lab = node('label', '', `${p.func} `);
      const inp = node('input');
      inp.value = p.hole || ''; inp.placeholder = 't12E';
      inp.maxLength = 8; inp.setAttribute('aria-label', `${c.id} pin ${p.func} hole`);
      lab.append(inp);
      pins.append(lab);
    });
    grid.append(node('div', 'ls-pins-label', 'Pin holes (e.g. t12E, rL+5):'), pins);
    const noteRow = node('label', '', 'Notes ');
    const note = node('input'); note.value = c.notes || ''; note.maxLength = 500;
    noteRow.append(note);
    grid.append(noteRow);
    const row = node('div', 'ls-row');
    const apply = node('button', 'primary', 'Apply');
    apply.type = 'button';
    apply.addEventListener('click', () => {
      checkpoint();
      BB().setComponent(LS.project, c.id, { value: val.value.trim(), status: status.value, notes: note.value.trim() });
      pins.querySelectorAll('input').forEach((inp, i) => {
        const hole = inp.value.trim() || null;
        const res = BB().setPinHole(LS.project, c.id, i, hole);
        if (!res.ok) showError(new Error(res.error));
      });
      afterEdit();
    });
    const net = node('button', 'secondary', 'Highlight net');
    net.type = 'button';
    net.addEventListener('click', () => {
      highlightHoles(c.pins.map((p) => p.hole).filter(Boolean));
    });
    const del = node('button', 'text-button', 'Remove');
    del.type = 'button';
    del.addEventListener('click', () => {
      checkpoint();
      BB().removeComponent(LS.project, c.id);
      LS.selected = null;
      afterEdit();
    });
    row.append(apply, net, del);
    insp.append(grid, row);
  } else {
    insp.append(node('h4', '', `${w.id} · Jumper`));
    const grid = node('div', 'ls-form');
    for (const end of ['from', 'to']) {
      const lab = node('label', '', end === 'from' ? 'From hole ' : 'To hole ');
      const inp = node('input'); inp.value = w[end] || ''; inp.placeholder = 't12E';
      inp.maxLength = 8;
      lab.append(inp);
      grid.append(lab);
    }
    const colorRow = node('label', '', 'Color ');
    const color = node('select');
    for (const col of ['red', 'orange', 'yellow', 'green', 'blue', 'black', 'white', 'purple']) {
      const o = node('option', '', col);
      o.value = col; if (col === w.color) o.selected = true;
      color.append(o);
    }
    colorRow.append(color);
    const statusRow = node('label', '', 'Evidence ');
    const status = node('select');
    for (const s of ['observed', 'inferred', 'confirmed', 'unknown']) {
      const o = node('option', '', statusBadge(s));
      o.value = s; if (s === w.status) o.selected = true;
      status.append(o);
    }
    statusRow.append(status);
    grid.append(colorRow, statusRow);
    const row = node('div', 'ls-row');
    const apply = node('button', 'primary', 'Apply');
    apply.type = 'button';
    apply.addEventListener('click', () => {
      checkpoint();
      const inputs = grid.querySelectorAll('input');
      const res = BB().setWire(LS.project, w.id, {
        from: inputs[0].value.trim() || null, to: inputs[1].value.trim() || null,
        color: color.value, status: status.value,
      });
      if (!res.ok) showError(new Error(res.error));
      afterEdit();
    });
    const net = node('button', 'secondary', 'Highlight net');
    net.type = 'button';
    net.addEventListener('click', () => {
      highlightHoles([w.from, w.to]);
    });
    const del = node('button', 'text-button', 'Remove');
    del.type = 'button';
    del.addEventListener('click', () => {
      checkpoint();
      BB().removeWire(LS.project, w.id);
      LS.selected = null;
      afterEdit();
    });
    row.append(apply, net, del);
    insp.append(grid, row);
  }
}

function wirePanel() {
  const box = node('section', 'panel');
  box.id = 'ls-wires';
  box.innerHTML = `<h3>Jumpers</h3>`;
  const add = node('form', 'ls-row ls-add');
  const from = node('input'); from.placeholder = 'From (t12E)'; from.maxLength = 8; from.setAttribute('aria-label', 'Wire from hole');
  const to = node('input'); to.placeholder = 'To (rL+5)'; to.maxLength = 8; to.setAttribute('aria-label', 'Wire to hole');
  const btn = node('button', 'secondary', 'Add wire');
  btn.type = 'submit';
  add.append(from, to, btn);
  add.addEventListener('submit', (e) => {
    e.preventDefault();
    checkpoint();
    const res = BB().addWire(LS.project, { from: from.value.trim() || null, to: to.value.trim() || null, status: 'unknown' });
    if (!res.ok) { showError(new Error(res.error)); return; }
    from.value = ''; to.value = '';
    afterEdit();
    LS.selected = res.id;
    renderWires(); renderInspector();
  });
  box.append(add);
  const list = node('div', 'ls-list');
  list.id = 'ls-wires-list';
  box.append(list);
  // Render list contents now and on edits.
  const render = () => {
    list.innerHTML = '';
    if (!LS.project.wires.length) list.append(node('p', 'muted', 'No jumpers yet.'));
    for (const w of LS.project.wires) {
      const row = node('button', 'ls-item' + (LS.selected === w.id ? ' selected' : ''), '');
      row.type = 'button';
      row.innerHTML = `<strong>${esc(w.id)}</strong><span>${esc(w.from || '?')} → ${esc(w.to || '?')}</span><span class="muted">${esc(w.color)} · ${statusBadge(w.status)}</span>`;
      row.addEventListener('click', () => {
        LS.selected = w.id;
        if (LS.twin) LS.twin.select(w.id);
        render(); renderInspector();
      });
      list.append(row);
    }
  };
  box._renderWires = render;
  setTimeout(render, 0);
  return box;
}

function renderWires() {
  const box = document.getElementById('ls-wires');
  if (box && box._renderWires) box._renderWires();
}

/* ----- questions ----- */
function questionPanel() {
  // The popup quiz is the asker; the full list stays collapsed with a count.
  const box = document.createElement('details');
  box.className = 'panel'; box.id = 'ls-questions';
  const sum = document.createElement('summary');
  sum.id = 'ls-questions-summary';
  sum.innerHTML = '<strong>Questions</strong> <span class="muted">asked in popups; answers persist</span>';
  const list = node('div', 'ls-list');
  list.id = 'ls-questions-list';
  box.append(sum, list);
  return box;
}

function refreshQuestions() {
  const list = document.getElementById('ls-questions-list');
  if (!list || !LS.project) return;
  // Retire questions whose condition is already satisfied, then merge fresh ones.
  BB().resolveStaleQuestions(LS.project);
  for (const q of BB().suggestQuestions(LS.project)) {
    if (!LS.project.questions.some((x) => x.key === q.key && x.status === 'open')) LS.project.questions.push(q);
  }
  list.innerHTML = '';
  const open = BB().openQuestions(LS.project);
  const ansBtn = document.getElementById('ls-answer');
  if (ansBtn) {
    ansBtn.hidden = !open.length;
    if (open.length) ansBtn.textContent = `❓ Answer ${open.length} question${open.length > 1 ? 's' : ''}`;
  }
  const sumEl = document.getElementById('ls-questions-summary');
  if (sumEl) sumEl.innerHTML = `<strong>Questions (${open.length} open)</strong> <span class="muted">asked in popups; answers persist</span>`;
  if (!open.length) {
    list.append(node('p', 'muted', 'Nothing needs clarification right now.'));
    return;
  }
  for (const q of open.slice(0, 12)) {
    const item = node('div', 'ls-question');
    item.append(node('p', '', q.text));
    if (q.options && q.options.length) {
      const sel = node('select');
      sel.append(node('option', '', 'Choose…'));
      for (const o of q.options) {
        const op = node('option', '', o);
        op.value = o;
        sel.append(op);
      }
      const btn = node('button', 'secondary', 'Answer');
      btn.type = 'button';
      btn.addEventListener('click', () => {
        if (!sel.value || sel.value === 'Choose…') return;
        checkpoint();
        BB().answerQuestion(LS.project, q.id, sel.value);
        afterEdit(); refreshQuestions();
      });
      item.append(sel, btn);
    } else {
      const inp = node('input');
      inp.placeholder = 'Type your answer…';
      inp.maxLength = 2000;
      inp.setAttribute('aria-label', q.text);
      const btn = node('button', 'secondary', 'Answer');
      btn.type = 'button';
      btn.addEventListener('click', () => {
        if (!inp.value.trim()) return;
        checkpoint();
        BB().answerQuestion(LS.project, q.id, inp.value.trim());
        afterEdit(); refreshQuestions();
      });
      const dk = node('button', 'secondary', "I don't know");
      dk.type = 'button';
      dk.title = 'Keep the uncertainty recorded without guessing.';
      dk.addEventListener('click', () => {
        checkpoint();
        BB().answerQuestion(LS.project, q.id, "I don't know");
        afterEdit(); refreshQuestions();
      });
      item.append(inp, btn, dk);
    }
    list.append(item);
  }
  if (open.length > 12) list.append(node('p', 'muted', `+${open.length - 12} more questions`));
}

/* ----- findings ----- */
function findingPanel() {
  const box = node('section', 'panel');
  box.id = 'ls-findings';
  box.innerHTML = `<h3>Wiring checks</h3><p class="field-help">Recalculated on every edit from confirmed connectivity. Uncertain links stay marked.</p>`;
  const chip = node('span', 'ls-pill', 'Circuit: nothing placed yet');
  chip.id = 'ls-verify';
  chip.title = 'Deterministic check counts on current wiring. Full scan coverage is not an electrical sign-off.';
  box.append(chip);
  const list = node('div', 'ls-list');
  list.id = 'ls-findings-list';
  box.append(list);
  return box;
}

function refreshFindings() {
  const list = document.getElementById('ls-findings-list');
  if (!list || !LS.project) return;
  list.innerHTML = '';
  const findings = BB().runChecks(LS.project);
  const chip = document.getElementById('ls-verify');
  if (chip) {
    const errs = findings.filter((f) => f.severity === 'error').length;
    const warns = findings.filter((f) => f.severity === 'warning').length;
    const infos = findings.filter((f) => f.severity === 'info').length;
    const placed = LS.project.components.some((c) => c.pins.some((p) => p.hole))
      || LS.project.wires.some((w) => w.from && w.to);
    chip.textContent = !placed ? 'Circuit: nothing placed yet'
      : errs ? `Circuit: ${errs} error(s) · ${warns} warning(s)`
      : warns ? `Circuit: ${warns} warning(s), no errors`
      : infos ? `Circuit: ${infos} open item(s), no errors` : 'Circuit: no issues found';
    chip.className = 'ls-pill' + (errs ? ' bad' : (!placed || warns || infos) ? '' : ' live');
    const vp = document.getElementById('ls-vp-verify');
    if (vp) { vp.textContent = chip.textContent; vp.className = 'ls-vp-verify' + (errs ? ' bad' : (!placed || warns || infos) ? '' : ' live'); }
  }
  if (!findings.length) {
    list.append(node('p', 'muted', 'No wiring problems detected in the confirmed connections.'));
    return;
  }
  const order = { error: 0, warning: 1, info: 2 };
  findings.sort((a, b) => order[a.severity] - order[b.severity]);
  for (const f of findings) {
    const item = node('div', `ls-finding sev-${f.severity}`);
    item.append(node('strong', '', f.title));
    item.append(node('p', '', f.detail));
    const row = node('div', 'ls-row');
    if (f.uncertain) row.append(node('span', 'muted', 'Needs confirmation'));
    const show = node('button', 'text-button', 'Show in 3D');
    show.type = 'button';
    show.addEventListener('click', () => {
      const holes = (f.evidence || []).filter((e) => BB().parseHole(e));
      if (holes.length) highlightHoles(holes);
      const first = (f.evidence || []).find((e) => !BB().parseHole(e));
      if (first) { LS.selected = first; renderParts(); renderWires(); renderInspector(); }
      document.getElementById('ls-twin-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
    row.append(show);
    item.append(row);
    list.append(item);
  }
}

/* ----- edit plumbing: checkpoint, refresh, debounced save ----- */
function checkpoint() { if (LS.history) LS.history.checkpoint(); }
function afterEdit() {
  markDirty();
  refreshTwin();
  renderParts();
  renderWires();
  refreshInvestigation();
  refreshReport();
  refreshQuestions();
  refreshFindings();
  updateMeta();
  updateProgress();
}
function markDirty() {
  LS.dirty = true;
  updateMeta();
  clearTimeout(LS.saveTimer);
  LS.saveTimer = setTimeout(() => saveNow(true), 2500);
}
function doUndo(redo) {
  if (!LS.history) return;
  const ok = redo ? LS.history.redo() : LS.history.undo();
  if (!ok) return;
  LS.selected = null;
  afterEdit();
}
// Client-side mirror of the server's save validation (livescan.validate_project).
// Returns a human-readable rule name, or null when the doc is safe to send.
// A rejected doc is NEVER sent: the edits stay local and the reason is loud.
function validateProjectForSave(doc, urlId) {
  if (!doc || typeof doc !== 'object') return 'no project document to save';
  if (doc.version !== 1) return 'unsupported project version ' + JSON.stringify(doc.version) + ' (expected 1)';
  for (const k of ['components', 'wires', 'images', 'questions'])
    if (!Array.isArray(doc[k])) return 'project field "' + k + '" is not a list';
  const size = doc.breadboard && doc.breadboard.size;
  if (size !== 'full' && size !== 'half') return 'unknown breadboard size ' + JSON.stringify(size);
  if (doc.id !== urlId) return 'project id changed during save (' + doc.id + ' vs ' + urlId + ')';
  let bytes;
  try { bytes = JSON.stringify(doc).length; }
  catch (e) { return 'project cannot be serialized (' + ((e && e.message) || e) + ')'; }
  if (bytes > 2 * 1024 * 1024) return 'project document is ' + Math.round(bytes / 1024) + ' KB (limit 2048 KB)';
  if (doc.components.length > 500 || doc.wires.length > 500 || doc.images.length > 100)
    return 'project has too many entries to save';
  return null;
}
async function saveNow(quiet) {
  if (!LS.project || !LS.dirty) return;
  if (!LS.backend) return;
  clearTimeout(LS.saveTimer);
  const doc = LS.project, urlId = LS.project.id;
  const problem = validateProjectForSave(doc, urlId);
  if (problem) {
    showError(new Error('Cannot save project ' + urlId + ': ' + problem + '. Your edits are kept locally.'));
    updateMeta();
    return;
  }
  try {
    await api(`/api/livescan/projects/${encodeURIComponent(urlId)}/save`, { project: doc });
    if (LS.project === doc) LS.dirty = false; // another project may have opened mid-save; keep its edits
    updateMeta();
  } catch (e) {
    // Loud on every failure: a silently unsaved project loses user work.
    showError(new Error('Save failed for project ' + urlId + ': ' + ((e && e.message) || e) + '. Your edits are kept locally.'));
    updateMeta();
  }
}
})();

