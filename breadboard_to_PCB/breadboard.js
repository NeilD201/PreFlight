/* PreFlight LiveScan: structured breadboard model, connectivity and checks.
   Pure logic (no DOM, no THREE) so it runs in Node tests and the browser.
   The 3D twin renders FROM this model; it is never the source of truth. */
(function (root) {
'use strict';

const VERSION = 1;
const PITCH_MM = 2.54;

// Small honest parts library. Only power-pin functions needed for safety
// checks are asserted; everything else stays descriptive, never invented.
const PARTS = {
  resistor: { label: 'Resistor', pins: [{ func: 'A' }, { func: 'B' }], prefix: 'R' },
  capacitor: { label: 'Capacitor', pins: [{ func: 'A' }, { func: 'B' }], prefix: 'C' },
  led: { label: 'LED', pins: [{ func: 'anode' }, { func: 'cathode' }], prefix: 'D', polarized: true },
  diode: { label: 'Diode', pins: [{ func: 'anode' }, { func: 'cathode' }], prefix: 'D', polarized: true },
  dip8: { label: 'DIP-8 IC', pins: 8, prefix: 'U' },
  dip14: { label: 'DIP-14 IC', pins: 14, prefix: 'U' },
  dip16: { label: 'DIP-16 IC', pins: 16, prefix: 'U' },
  // Common 30-pin ESP32 devkit (2x15). Power + strapping pins asserted;
  // GPIOs labeled by silkscreen number only.
  esp32devkit: { label: 'ESP32 devkit (30-pin)', prefix: 'U', pins: [
    { func: '3V3' }, { func: 'EN' }, { func: 'GPIO36' }, { func: 'GPIO39' }, { func: 'GPIO34' },
    { func: 'GPIO35' }, { func: 'GPIO32' }, { func: 'GPIO33' }, { func: 'GPIO25' }, { func: 'GPIO26' },
    { func: 'GPIO27' }, { func: 'GPIO14' }, { func: 'GPIO12' }, { func: 'GND' }, { func: 'GPIO13' },
    { func: 'GND' }, { func: 'GPIO15' }, { func: 'GPIO2' }, { func: 'GPIO4' }, { func: 'GPIO16' },
    { func: 'GPIO17' }, { func: 'GPIO5' }, { func: 'GPIO18' }, { func: 'GPIO19' }, { func: 'GPIO21' },
    { func: 'GPIO3' }, { func: 'GPIO1' }, { func: 'GPIO22' }, { func: 'GPIO23' }, { func: '5V' },
  ] },
  sensor3: { label: '3-pin module', pins: [{ func: 'VCC' }, { func: 'GND' }, { func: 'SIG' }], prefix: 'U' },
  button: { label: 'Push button', pins: [{ func: 'A' }, { func: 'B' }], prefix: 'S' },
  generic: { label: 'Component', pins: [{ func: 'P1' }, { func: 'P2' }], prefix: 'U' },
};

const SIZES = {
  full: { rows: 63, label: 'Full-size (830 tie-points)' },
  half: { rows: 30, label: 'Half-size (400 tie-points)' },
};

function newProject(name, opts) {
  opts = opts || {};
  const size = SIZES[opts.size] ? opts.size : 'full';
  const now = new Date().toISOString();
  return {
    version: VERSION, id: 'ls-' + Math.random().toString(36).slice(2, 10),
    name: String(name || 'Breadboard build').slice(0, 120),
    created: now, updated: now,
    breadboard: { size, railGaps: {} },
    intent: { text: '', fields: {}, updated: null },
    components: [], wires: [], images: [], questions: [],
    confirmations: {}, counters: {},
  };
}

function rowsOf(project) { return SIZES[project.breadboard.size].rows; }

// ---- hole ids -----------------------------------------------------------
// Terminal: t{row}{A-J}  (A-E left bank, F-J right bank). Rails: r{L|R}{+|-}{pos}
function parseHole(id) {
  let m = /^t(\d{1,2})([A-J])$/.exec(id || '');
  if (m) return { kind: 'terminal', row: +m[1], col: m[2], bank: m[2] < 'F' ? 0 : 1 };
  m = /^r([LR])([+-])(\d{1,2})$/.exec(id || '');
  if (m) return { kind: 'rail', side: m[1], pol: m[2], pos: +m[3] };
  return null;
}

function validHole(project, id) {
  const h = parseHole(id);
  if (!h) return false;
  const rows = rowsOf(project);
  if (h.kind === 'terminal') return h.row >= 1 && h.row <= rows;
  return h.pos >= 1 && h.pos <= rows;
}

// Forgiving hole-name normalization for typed answers ("t40a", "Rr+25",
// " T12C ") into canonical ids. Returns the canonical id or null.
function normalizeHole(text) {
  const s = String(text || '').trim();
  let m = /^t(\d{1,2})([A-Ja-j])$/.exec(s);
  if (m) return 't' + (+m[1]) + m[2].toUpperCase();
  m = /^[rR]([LRlr])([+-])(\d{1,2})$/.exec(s);
  if (m) return 'r' + m[1].toUpperCase() + m[2] + (+m[3]);
  return null;
}

// Pull canonical hole names out of free text ("rR+25 to t40A"). Order kept,
// duplicates dropped. Returns ids that still need validHole range checks.
function extractHoles(text) {
  const out = [];
  const re = /[tT]\d{1,2}[A-Ja-j]|[rR][LlRr][+-]\d{1,2}/g;
  let m;
  while ((m = re.exec(String(text || '')))) {
    const h = normalizeHole(m[0]);
    if (h && !out.includes(h)) out.push(h);
  }
  return out;
}

// Image-region hints from a vision endpoints description ("one end at the
// off-board bundle; other end into the top rail near the right end").
// Split per clause on ';': each clause yields {u, v} image fractions,
// {module: true} (near the main module: caller resolves its bbox center), or
// {offboard: true}. u/v stay null when the clause gives no information on
// that axis. Callers map (u, v) through the view's own registration, so the
// hints survive 180° flips — never convert to columns here.
function railHintFromText(text) {
  const clauses = String(text || '').split(';');
  const out = [];
  for (const raw of clauses) {
    const c = raw.toLowerCase();
    if (!c.trim()) continue;
    if (/(off[\s-]?board|off[\s-]?frame|white block|servo|cable bundle|connector bundle|not visible|unclear|hidden|left edge)/.test(c)
      && !/(rail|strip|hole|col|row|terminal|esp32|module|mc[cu]|devkit)/.test(c)) {
      out.push({ offboard: true });
      continue;
    }
    const hint = { u: null, v: null };
    if (/(esp32|module|mc[cu]|devkit)/.test(c)) hint.module = true;
    if (/(right|right-hand)\s*(end|edge|side)/.test(c)) hint.u = 0.92;
    else if (/(left|left-hand)\s*(end|edge|side)/.test(c)) hint.u = 0.08;
    else if (/middle[\s-]?right|right[\s-]?center|right[\s-]?middle/.test(c)) hint.u = 0.7;
    else if (/middle[\s-]?left|left[\s-]?center|left[\s-]?middle/.test(c)) hint.u = 0.3;
    else if (/(center|centre|middle)/.test(c)) hint.u = 0.5;
    if (/(top|upper)\s*(power\s*)?rail/.test(c)) hint.v = 0.2;
    else if (/(bottom|lower)\s*(power\s*)?rail/.test(c)) hint.v = 0.8;
    else if (/(top|upper)\s*(area|end|side|half)/.test(c)) hint.v = 0.25;
    else if (/(bottom|lower)\s*(area|end|side|half)/.test(c)) hint.v = 0.75;
    else if (/(terminal|strip|row)/.test(c) && hint.u !== null) hint.v = 0.5;
    out.push(hint);
  }
  return out;
}

// Pick one plug hypothesis from guided rail candidates. Candidates within
// 6 holes (touching housings) form one cluster reported by middle and
// spread — exact columns inside a cluster are electrically identical on a
// rail segment, so precision beyond the cluster is fake. Clusters never span
// a rail gap (different copper). A column hint picks the nearest cluster;
// without a hint, several far-apart clusters abstain (null) rather than
// guess. Selections wider than 12 holes are rejected as unlocalized.
// Returns {pos, spread} or null.
function selectRailPlug(candidates, hintCol, gaps) {
  const cs = (candidates || []).filter((c) => c && Number.isFinite(c.pos)).slice()
    .sort((a, b) => a.pos - b.pos);
  if (!cs.length) return null;
  const gs = Array.isArray(gaps) ? gaps.filter((g) => Number.isFinite(g)).sort((a, b) => a - b) : [];
  const spanned = (a, b) => gs.some((g) => g >= Math.min(a, b) && g < Math.max(a, b));
  const clusters = [];
  for (const c of cs) {
    const last = clusters[clusters.length - 1];
    if (last && c.pos - last.hi < 6 && !spanned(last.hi, c.pos)
      && c.pos - last.lo <= 12) {
      last.hi = Math.max(last.hi, c.pos);
      last.w = Math.max(last.w, c.width || 0);
    } else clusters.push({ lo: c.pos, hi: c.pos, w: c.width || 0 });
  }
  const mid = (k) => Math.round((k.lo + k.hi) / 2);
  let pick = null;
  // NOTE: global isFinite(null) is true by coercion; hints use null for
  // "no information", so only Number.isFinite is correct here.
  if (Number.isFinite(hintCol)) {
    let bd = Infinity;
    for (const k of clusters) {
      const d = Math.abs(mid(k) - hintCol);
      if (d < bd) { bd = d; pick = k; }
    }
  } else if (clusters.length === 1) pick = clusters[0];
  else return null;
  return { pos: mid(pick), spread: Math.max(1, pick.hi - pick.lo + 1) };
}

// Lane words from a quiz answer about a rail plug ("+", "minus lane", "red
// stripe side"). Red/blue alone are wire colors, never lane evidence — they
// only count adjacent to stripe/lane/rail/side. Returns '+', '-', or null.
function parseLaneAnswer(text) {
  const t = String(text || '').toLowerCase();
  if (/red\s*(stripe|lane|rail|side)/.test(t)) return '+';
  if (/blue\s*(stripe|lane|rail|side)/.test(t)) return '-';
  if (/\+|plus/.test(t)) return '+';
  if (/\bminus\b|-\s*(lane|rail|stripe|side)|(lane|rail|stripe|side)\s*-/.test(t)) return '-';
  return null;
}

// Canonical electrical group for a hole (breadboard metal strips).
function groupOf(project, id) {
  const h = parseHole(id);
  if (!h) return null;
  if (h.kind === 'terminal') return 'G:term:' + h.row + ':' + h.bank;
  const key = 'r' + h.side + h.pol;
  const gaps = (project.breadboard.railGaps[key] || []).slice().sort((a, b) => a - b);
  let seg = 0;
  for (const g of gaps) if (h.pos > g) seg++;
  return 'G:rail:' + key + ':' + seg;
}

// Real-board X layout in mm (BB830-class, origin at board center):
// terminal banks span +/-3.81 (E/F) .. +/-13.97 (A/J) with a 7.62 mm DIP
// groove between E and F; row numerals at +/-15.9.
// Rail lanes calibrated from registered real-photo evidence (hole-periodicity
// scan + visual comb overlay, dual-confirmed, symmetric to +/-0.25 mm):
// inner lane |x| = 18.5, outer lane |x| = 20.75. Polarity is MIXED on the
// reference board (red stripe outer on L, inner on R): L+ = outer,
// R+ = inner. Rail clips are flippable, so treat per-side polarity as a
// board-assembly property, not a universal constant.
// Only the 3D twin consumes these coordinates; checks use groups, not mm.
const CENTER_GAP = 3 * PITCH_MM; // 7.62 mm E-F spacing
const RAIL_INNER_X = 18.5;
const RAIL_OUTER_X = 20.75;
// True when the + (red-adjacent) lane is the outer lane on that side.
const RAIL_PLUS_IS_OUTER = { L: true, R: false };
// Signed board-mm x of a rail lane. Single source of truth for rail geometry.
function railLaneX(side, pol) {
  const outer = RAIL_PLUS_IS_OUTER[side] ? pol === '+' : pol === '-';
  return (side === 'L' ? -1 : 1) * (outer ? RAIL_OUTER_X : RAIL_INNER_X);
}
const BOARD_WIDTH = 52;

// Physical position in mm, origin at breadboard center, y up ignored here.
function holeXY(project, id) {
  const h = parseHole(id);
  if (!h) return null;
  const rows = rowsOf(project);
  const zRow = (r) => (r - (rows + 1) / 2) * PITCH_MM;
  if (h.kind === 'terminal') {
    const colIndex = h.col.charCodeAt(0) - 65; // A=0..J=9
    const x = colIndex < 5
      ? (colIndex - 4) * PITCH_MM - CENTER_GAP / 2
      : (colIndex - 5) * PITCH_MM + CENTER_GAP / 2;
    return { x, z: zRow(h.row) };
  }
  // Rails run along z; x offsets outside the terminal field.
  return { x: railLaneX(h.side, h.pol), z: zRow(h.pos) };
}

// Inverse of holeXY: nearest hole to a board-mm point with miss distance.
// Returns {hole, dx, dz} or null when outside every lattice.
function holeAt(project, x, z) {
  if (!isFinite(x) || !isFinite(z)) return null;
  const rows = rowsOf(project);
  const zRow = (r) => (r - (rows + 1) / 2) * PITCH_MM;
  const cands = [];
  // Terminal strips.
  const row = Math.round(z / PITCH_MM + (rows + 1) / 2);
  if (row >= 1 && row <= rows) {
    const dz = Math.abs(z - zRow(row));
    let col = -1;
    if (x < 0) {
      col = Math.round((x + CENTER_GAP / 2) / PITCH_MM + 4);
      if (col < 0 || col > 4) col = -1;
    } else {
      col = Math.round((x - CENTER_GAP / 2) / PITCH_MM + 5);
      if (col < 5 || col > 9) col = -1;
    }
    if (col >= 0) {
      const hx = col < 5 ? (col - 4) * PITCH_MM - CENTER_GAP / 2 : (col - 5) * PITCH_MM + CENTER_GAP / 2;
      cands.push({ hole: 't' + row + 'ABCDEFGHIJ'[col], dx: Math.abs(x - hx), dz });
    }
  }
  // Rails.
  const pos = Math.round(z / PITCH_MM + (rows + 1) / 2);
  if (pos >= 1 && pos <= rows) {
    const dz = Math.abs(z - zRow(pos));
    const side = x < 0 ? 'L' : 'R';
    const ax = Math.abs(x);
    const outer = Math.abs(ax - RAIL_OUTER_X) < Math.abs(ax - RAIL_INNER_X);
    const lx = outer ? RAIL_OUTER_X : RAIL_INNER_X;
    const pol = (outer === !!RAIL_PLUS_IS_OUTER[side]) ? '+' : '-';
    if (Math.abs(ax - lx) < 3 * PITCH_MM)
      cands.push({ hole: 'r' + side + pol + pos, dx: Math.abs(ax - lx), dz });
  }
  if (!cands.length) return null;
  cands.sort((a, b) => (Math.hypot(a.dx, a.dz) - Math.hypot(b.dx, b.dz)));
  return cands[0];
}

// ---- controlled vocabulary -----------------------------------------------
const STATUS = ['observed', 'inferred', 'confirmed', 'unknown'];
function normStatus(s) { return STATUS.includes(s) ? s : 'unknown'; }
// Higher = less certain.
function certaintyRank(s) {
  return s === 'confirmed' ? 0 : s === 'observed' ? 1 : s === 'inferred' ? 2 : 3;
}

// ---- project mutations (all return {ok, error?}) --------------------------
function touch(project) { project.updated = new Date().toISOString(); }

function nextRef(project, kind) {
  const def = PARTS[kind] || PARTS.generic;
  const prefix = kind === 'wire' ? 'J' : def.prefix;
  project.counters[prefix] = (project.counters[prefix] || 0) + 1;
  return prefix + project.counters[prefix];
}

function partPins(kind) {
  const def = PARTS[kind] || PARTS.generic;
  if (Array.isArray(def.pins)) return def.pins.map((p) => ({ func: p.func }));
  const n = def.pins | 0;
  const out = [];
  for (let i = 1; i <= n; i++) out.push({ func: 'P' + i });
  return out;
}

function addComponent(project, spec) {
  spec = spec || {};
  const kind = PARTS[spec.kind] ? spec.kind : 'generic';
  const comp = {
    id: spec.id || nextRef(project, kind), kind,
    value: String(spec.value || ''), pins: partPins(kind).map((p) => ({ func: p.func, hole: null })),
    orientation: spec.orientation || 0, status: normStatus(spec.status || 'unknown'),
    source: spec.source || 'user', notes: String(spec.notes || ''),
    region: String(spec.region || ''), bbox: Array.isArray(spec.bbox) ? spec.bbox.slice(0, 4) : null,
    manufacturer: String(spec.manufacturer || ''), mpn: String(spec.mpn || ''),
    package: String(spec.package || ''), purpose: String(spec.purpose || ''),
    datasheet: String(spec.datasheet || ''),
  };
  if (project.components.some((c) => c.id === comp.id)) return { ok: false, error: 'Duplicate id ' + comp.id };
  project.components.push(comp); touch(project);
  return { ok: true, id: comp.id };
}

function getComponent(project, id) { return project.components.find((c) => c.id === id) || null; }

function setPinHole(project, compId, pinIndex, hole) {
  const comp = getComponent(project, compId);
  if (!comp) return { ok: false, error: 'Unknown component' };
  if (pinIndex < 0 || pinIndex >= comp.pins.length) return { ok: false, error: 'Bad pin index' };
  if (hole !== null && !validHole(project, hole)) return { ok: false, error: 'Bad hole ' + hole };
  comp.pins[pinIndex].hole = hole; touch(project);
  return { ok: true };
}

function setComponent(project, compId, patch) {
  const comp = getComponent(project, compId);
  if (!comp) return { ok: false, error: 'Unknown component' };
  if (patch.value !== undefined) comp.value = String(patch.value).slice(0, 80);
  if (patch.orientation !== undefined) comp.orientation = patch.orientation % 360;
  if (patch.status !== undefined) comp.status = normStatus(patch.status);
  if (patch.notes !== undefined) comp.notes = String(patch.notes).slice(0, 500);
  touch(project);
  return { ok: true };
}

function removeComponent(project, compId) {
  const i = project.components.findIndex((c) => c.id === compId);
  if (i < 0) return { ok: false, error: 'Unknown component' };
  project.components.splice(i, 1); touch(project);
  return { ok: true };
}

function addWire(project, spec) {
  spec = spec || {};
  if (spec.from && !validHole(project, spec.from)) return { ok: false, error: 'Bad hole ' + spec.from };
  if (spec.to && !validHole(project, spec.to)) return { ok: false, error: 'Bad hole ' + spec.to };
  const wire = {
    id: spec.id || nextRef(project, 'wire'),
    from: spec.from || null, to: spec.to || null,
    color: String(spec.color || 'red').slice(0, 24),
    status: normStatus(spec.status || 'unknown'), source: spec.source || 'user',
    notes: String(spec.notes || ''), region: String(spec.region || ''),
    bbox: Array.isArray(spec.bbox) ? spec.bbox.slice(0, 4) : null,
  };
  if (project.wires.some((w) => w.id === wire.id)) return { ok: false, error: 'Duplicate id ' + wire.id };
  project.wires.push(wire); touch(project);
  return { ok: true, id: wire.id };
}

function setWire(project, wireId, patch) {
  const wire = project.wires.find((w) => w.id === wireId);
  if (!wire) return { ok: false, error: 'Unknown wire' };
  if (patch.from !== undefined) {
    if (patch.from && !validHole(project, patch.from)) return { ok: false, error: 'Bad hole ' + patch.from };
    wire.from = patch.from || null;
  }
  if (patch.to !== undefined) {
    if (patch.to && !validHole(project, patch.to)) return { ok: false, error: 'Bad hole ' + patch.to };
    wire.to = patch.to || null;
  }
  if (patch.color !== undefined) wire.color = String(patch.color).slice(0, 24);
  if (patch.status !== undefined) wire.status = normStatus(patch.status);
  touch(project);
  return { ok: true };
}

function removeWire(project, wireId) {
  const i = project.wires.findIndex((w) => w.id === wireId);
  if (i < 0) return { ok: false, error: 'Unknown wire' };
  project.wires.splice(i, 1); touch(project);
  return { ok: true };
}

function confirm(project, key, value) {
  project.confirmations[key] = { value: !!value, at: new Date().toISOString() };
  touch(project);
}

// ---- connectivity (union-find over holes + strip groups) ------------------
function computeNets(project) {
  const parent = {}, rank = {}, worst = {}, members = {};
  const find = (x) => {
    if (!(x in parent)) { parent[x] = x; rank[x] = 0; worst[x] = 0; members[x] = []; }
    while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; }
    return x;
  };
  const union = (a, b, certainty) => {
    let ra = find(a), rb = find(b);
    if (ra === rb) { worst[ra] = Math.max(worst[ra], certainty); return; }
    if (rank[ra] < rank[rb]) { const t = ra; ra = rb; rb = t; }
    parent[rb] = ra;
    if (rank[ra] === rank[rb]) rank[ra]++;
    worst[ra] = Math.max(worst[ra], worst[rb], certainty);
  };
  const touch2 = (x) => find(x);
  // Strip metal is certain physics; endpoints carry the observation status.
  const link = (hole, certainty) => {
    if (!hole) return;
    const g = groupOf(project, hole);
    if (!g) return;
    touch2(hole); touch2(g);
    union(hole, g, 0);
    members[find(hole)].push({ hole, certainty });
  };
  for (const c of project.components)
    for (const p of c.pins) link(p.hole, certaintyRank(c.status));
  for (const w of project.wires) {
    if (w.from && w.to) {
      link(w.from, certaintyRank(w.status)); link(w.to, certaintyRank(w.status));
      union(w.from, w.to, certaintyRank(w.status));
    } else {
      link(w.from, certaintyRank(w.status)); link(w.to, certaintyRank(w.status));
    }
  }
  const nets = {};
  for (const x of Object.keys(parent)) {
    const r = find(x);
    (nets[r] = nets[r] || { root: r, holes: [], certainty: 0, roles: {} });
    nets[r].certainty = Math.max(nets[r].certainty, worst[r]);
    for (const m of members[x] || []) {
      if (!nets[r].holes.includes(m.hole)) nets[r].holes.push(m.hole);
      const h = parseHole(m.hole);
      if (h && h.kind === 'rail') nets[r].roles[h.pol] = true;
    }
  }
  return Object.values(nets);
}

function netOfHole(project, hole) {
  for (const net of computeNets(project)) if (net.holes.includes(hole)) return net;
  return null;
}

// 3x3 zone name for a normalized bbox. Mirrors ScanMath.zoneName so scan
// observations and model regions share one vocabulary.
function zoneName(bbox) {
  if (!Array.isArray(bbox) || bbox.length !== 4) return 'unknown';
  const cx = (bbox[0] + bbox[2]) / 2, cy = (bbox[1] + bbox[3]) / 2;
  const vert = cy < 0.34 ? 'upper' : cy < 0.67 ? '' : 'lower';
  const horiz = cx < 0.34 ? 'left' : cx < 0.67 ? 'middle' : 'right';
  if (!vert) return horiz === 'middle' ? 'center' : horiz;
  return horiz === 'middle' ? vert : vert + '-' + horiz;
}

// Intersection-over-union for normalized bboxes; null when unmeasurable.
function bboxIoU(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== 4 || b.length !== 4) return null;
  const ix0 = Math.max(a[0], b[0]), iy0 = Math.max(a[1], b[1]);
  const ix1 = Math.min(a[2], b[2]), iy1 = Math.min(a[3], b[3]);
  const inter = Math.max(0, ix1 - ix0) * Math.max(0, iy1 - iy0);
  const ua = Math.max(0, a[2] - a[0]) * Math.max(0, a[3] - a[1]);
  const ub = Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]);
  const union = ua + ub - inter;
  return union > 0 ? inter / union : null;
}

// A repeat sighting matches when zone agrees, the kind test passes, and the
// boxes overlap enough. Same-kind neighbours need IoU >= 0.2; cross-kind
// matches (only via 'generic') need IoU >= 0.5 so a vague "black component"
// box cannot absorb a neighbouring LED. Unlocalized sightings fall back to
// zone; matches adopt bboxes. minIoUFor(e) returns the threshold or null.
function sightingMatch(list, zone, bbox, minIoUFor) {
  const cands = [];
  for (const e of list) {
    if (e.status === 'confirmed' || (e.region || '') !== zone) continue;
    const m = minIoUFor ? minIoUFor(e) : 0.2;
    if (m !== null) cands.push([e, m]);
  }
  if (!bbox) {
    if (cands.length) return cands[0][0];
    // Unlocated re-sighting (close-ups lose the zone frame): match only
    // when a single candidate exists anywhere, preferring exact-kind
    // matches (the lowest threshold group) over generic cross-kind ones.
    const scored = [];
    for (const e of list) {
      if (e.status === 'confirmed') continue;
      const m = minIoUFor ? minIoUFor(e) : 0.2;
      if (m !== null) scored.push([e, m]);
    }
    if (!scored.length) return null;
    const best = Math.min(...scored.map(([, m]) => m));
    const exact = scored.filter(([, m]) => m === best);
    return exact.length === 1 ? exact[0][0] : null;
  }
  let unloc = null, best = null, bestIoU = -1, bestMin = 0.2;
  for (const [e, m] of cands) {
    if (!e.bbox) { unloc = unloc || e; continue; }
    const ioU = bboxIoU(e.bbox, bbox);
    if (ioU !== null && ioU > bestIoU) { bestIoU = ioU; best = e; bestMin = m; }
  }
  if (best && bestIoU >= bestMin) return best;
  return unloc;
}

// Endpoint-text overlap for multi-lane assignment: shared meaningful words
// between a lane's accumulated history and a new sighting's description.
const END_STOP = new Set(('the,a,an,to,of,at,in,on,near,one,end,other,unclear,both,ends,with,from,into,area,via,and,or,each,is,are,it,as,by,out,up,over,under,' +
  'off,frame,wire,wires,board,breadboard,seen,jpg,jpeg,png,' +
  'black,white,red,orange,yellow,green,blue,purple,brown,gray,grey').split(','));
function endTokens(s) {
  return String(s || '').toLowerCase().split(/[^a-z0-9]+/)
    .filter((w) => w.length > 2 && !END_STOP.has(w));
}
function endOverlap(history, endpoints) {
  const have = new Set(endTokens(history));
  let n = 0;
  for (const w of new Set(endTokens(endpoints))) if (have.has(w)) n++;
  return n;
}

// Merge one sanitized vision observation set into the project as INFERRED
// evidence. Matches repeat sightings instead of duplicating them, but keeps
// distinct neighbours apart via bbox overlap. Returns a merge report.
function mergeScan(project, obs, imageName) {
  const report = { added: [], matched: [], questions: [], boxes: {}, photoBoxes: {}, wireSightings: [], sizeHint: '' };
  const crossView = [];
  obs = obs && typeof obs === 'object' ? obs : {};
  const wasEmpty = !project.components.length && !project.wires.length;
  const tag = imageName ? `seen in ${imageName}` : 'seen in scan';
  const sameKind = (a, b) => a === b || a === 'generic' || b === 'generic';
  for (const o of Array.isArray(obs.components) ? obs.components : []) {
    if (!o || typeof o !== 'object') continue;
    // Batch responses carry their own per-item source photo; single-photo
    // responses fall back to the merge call's image name.
    const osrc = o.photo || imageName;
    const otag = osrc ? `seen in ${osrc}` : 'seen in scan';
    const kind = PARTS[o.kind] ? o.kind : 'generic';
    const zone = zoneName(o.bbox);
    const label = String(o.label || kind);
    const bits = [label];
    if (o.value_guess) bits.push(`reads "${String(o.value_guess).slice(0, 40)}" (unverified)`);
    if (o.confidence === 'low') bits.push('low confidence');
    if (o.notes) bits.push(String(o.notes).slice(0, 120));
    bits.push(otag);
    const note = bits.join('; ');
    let hit = sightingMatch(project.components, zone, o.bbox || null,
      (c) => (sameKind(c.kind, kind) ? (c.kind === kind ? 0.2 : 0.5) : null));
    if (!hit && kind === 'esp32devkit') {
      // Cross-viewpoint: a devkit moves across the frame between angles, so
      // zones and bboxes cannot match. There is normally one devkit per
      // board; re-sight it by kind singleton and flag for confirmation.
      const solo = project.components.filter((c) => c.kind === kind && c.status !== 'confirmed');
      if (solo.length === 1) {
        hit = solo[0];
        crossView.push(`${hit.id} re-sighted in ${imageName || 'scan'}`);
      }
    }
    if (hit) {
      if (!hit.notes.includes(otag)) hit.notes = (hit.notes ? hit.notes + '; ' : '') + note;
      // Adopt the latest view's box while unplaced so a later registered
      // view can place the part; never move an already-placed part.
      if (!hit.placedView && o.bbox) hit.bbox = o.bbox.slice(0, 4);
      hit.sightings = Array.isArray(hit.sightings) ? hit.sightings : [];
      if (osrc && !hit.sightings.includes(osrc)) hit.sightings.push(osrc);
      for (const s of (Array.isArray(o.seen_in) ? o.seen_in : [])) {
        if (s && !hit.sightings.includes(s)) hit.sightings.push(s);
      }
      if (o.bbox) {
        report.boxes[hit.id] = o.bbox.slice(0, 4);
        if (osrc) {
          report.photoBoxes[osrc] = report.photoBoxes[osrc] || {};
          report.photoBoxes[osrc][hit.id] = o.bbox.slice(0, 4);
        }
      }
      report.matched.push(hit.id);
      continue;
    }
    const res = addComponent(project, { kind, status: 'inferred', source: 'scan', region: zone, bbox: o.bbox || null, notes: note });
    if (res.ok) {
      const c = getComponent(project, res.id);
      if (c) {
        // Vision already read the markings: record the part number now
        // instead of asking the user to type it back. Status stays
        // 'inferred' (machine-read, not user-confirmed); the identify
        // question is skipped while c.mpn is set.
        const sm = scanMpn((o.label || '') + ' ' + (o.notes || ''));
        if (sm && !c.mpn) {
          c.mpn = sm.toUpperCase().slice(0, 40);
          if (!c.value) c.value = c.mpn;
          c.notes = (c.notes ? c.notes + '; ' : '') + 'markings read from scan: ' + sm;
        }
        c.sightings = osrc ? [osrc] : [];
        for (const s of (Array.isArray(o.seen_in) ? o.seen_in : [])) {
          if (s && !c.sightings.includes(s)) c.sightings.push(s);
        }
      }
      if (o.bbox) {
        report.boxes[res.id] = o.bbox.slice(0, 4);
        if (osrc) {
          report.photoBoxes[osrc] = report.photoBoxes[osrc] || {};
          report.photoBoxes[osrc][res.id] = o.bbox.slice(0, 4);
        }
      }
      report.added.push(res.id);
    }
  }
  // Wires merge by COLOR LANES, not zones: bboxes and zones are meaningless
  // across viewpoints, so same-color sightings join one lane unless a single
  // view proves multiplicity (two located, disjoint, non-containing boxes).
  // Fragments of one wire in a view (close-ups, housings) collapse; genuinely
  // distinct same-color wires split into lanes and get confirmation questions.
  const seenWires = Array.isArray(obs.wires) ? obs.wires.filter((x) => x && typeof x === 'object') : [];
  const wireColor = (x) => String(x.color || 'unknown').slice(0, 24);
  const wireDistinct = (a, b) => {
    const ba = Array.isArray(a.bbox) ? a.bbox : null;
    const bb = Array.isArray(b.bbox) ? b.bbox : null;
    if (!ba || !bb) return false; // unlocated fragments cannot prove distinctness
    if ((bboxIoU(ba, bb) || 0) >= 0.1) return false;
    const ix = Math.max(0, Math.min(ba[2], bb[2]) - Math.max(ba[0], bb[0]))
      * Math.max(0, Math.min(ba[3], bb[3]) - Math.max(ba[1], bb[1]));
    const small = Math.min((ba[2] - ba[0]) * (ba[3] - ba[1]), (bb[2] - bb[0]) * (bb[3] - bb[1]));
    if (small > 0 && ix / small >= 0.7) return false; // housing fragment inside the wire's span
    return true;
  };
  const wireClusters = [];
  for (const o of seenWires) {
    const color = wireColor(o);
    let placed = false;
    for (const cl of wireClusters) {
      if (cl.color !== color) continue;
      if (cl.items.some((m) => wireDistinct(m, o))) continue;
      cl.items.push(o); placed = true; break;
    }
    if (!placed) wireClusters.push({ color, items: [o] });
  }
  const confRank = (c) => (c === 'high' ? 0 : c === 'medium' ? 1 : 2);
  const laneTaken = new Set(); // wire ids already joined by this view
  const multiCluster = (color) => wireClusters.filter((c) => c.color === color).length > 1;
  const ordered = wireClusters.slice().sort((a, b) =>
    Math.min(...a.items.map((x) => confRank(x.confidence)))
    - Math.min(...b.items.map((x) => confRank(x.confidence))));
  for (const cl of ordered) {
    const sorted = cl.items.slice().sort((a, b) => confRank(a.confidence) - confRank(b.confidence));
    const o = sorted[0];
    const osrc = o.photo || imageName;
    const otag = osrc ? `seen in ${osrc}` : 'seen in scan';
    const zone = zoneName(o.bbox);
    const frag = cl.items.length > 1 ? ` (${cl.items.length} fragments merged)` : '';
    const note = `${cl.color} wire (${zone}); ${o.endpoints ? String(o.endpoints).slice(0, 120) + '; ' : ''}${otag}${frag}`;
    const lanes = project.wires.filter((w) => w.color === cl.color
      && w.status !== 'confirmed' && !laneTaken.has(w.id));
    const quietGeom = (w) => w.region === zone
      && (!w.bbox || !o.bbox || (bboxIoU(w.bbox, o.bbox) || 0) >= 0.2);
    let hit = null;
    if (!multiCluster(cl.color) && lanes.length === 1) {
      hit = lanes[0];
      // Same-view repeats (zone agrees, boxes overlap) match quietly; only
      // true cross-viewpoint re-sightings ask for a confirming glance.
      if (!quietGeom(hit)) crossView.push(`${hit.id} (${cl.color}) re-sighted in ${imageName || 'scan'}`);
    } else if (lanes.length) {
      // Ambiguous multiplicity (several clusters this view, or several
      // lanes): join only on strong evidence — a geometric repeat that also
      // agrees on endpoints, or a clearly best endpoint-text match — else
      // start a new lane rather than fuse distinct wires.
      const scored = lanes.map((w) => {
        const ov = endOverlap(w.notes, o.endpoints);
        return [quietGeom(w) && ov >= 2 ? 99 : ov, w];
      }).sort((a, b) => b[0] - a[0]);
      const best = scored[0][0];
      const runnerUp = scored.length > 1 ? scored[1][0] : -1;
      if (best >= 2 && best > runnerUp) {
        hit = scored[0][1];
        crossView.push(`${hit.id} (${cl.color}) re-sighted in ${imageName || 'scan'}`);
      } else if (best >= 2 && best <= runnerUp) {
        hit = scored[0][1];
        crossView.push(`${hit.id} (${cl.color}) re-sighted in ${imageName || 'scan'}`
          + ' (ambiguous: resembles another ' + cl.color + ' wire)');
      }
    }
    if (hit) {
      laneTaken.add(hit.id);
      if (!hit.notes.includes(otag)) hit.notes = (hit.notes ? hit.notes + '; ' : '') + note;
      if (!hit.bbox && o.bbox) hit.bbox = o.bbox.slice(0, 4);
      hit.sightings = Array.isArray(hit.sightings) ? hit.sightings : [];
      if (osrc && !hit.sightings.includes(osrc)) hit.sightings.push(osrc);
      for (const s of (Array.isArray(o.seen_in) ? o.seen_in : [])) {
        if (s && !hit.sightings.includes(s)) hit.sightings.push(s);
      }
      report.matched.push(hit.id);
      if (o.bbox) report.wireSightings.push({ id: hit.id, bbox: o.bbox.slice(0, 4), endpoints: o.endpoints || '', photo: osrc || '',
        hole_ends: Array.isArray(o.hole_ends) ? o.hole_ends.filter((h) => validHole(project, h)).slice(0, 2) : [] });
      continue;
    }
    const res = addWire(project, { color: cl.color, status: 'inferred', source: 'scan', region: zone, bbox: o.bbox || null, notes: note });
    if (res.ok) {
      laneTaken.add(res.id);
      const w = project.wires.find((x) => x.id === res.id);
      if (w) {
        w.sightings = osrc ? [osrc] : [];
        for (const s of (Array.isArray(o.seen_in) ? o.seen_in : [])) {
          if (s && !w.sightings.includes(s)) w.sightings.push(s);
        }
      }
      report.added.push(res.id);
      if (o.bbox) report.wireSightings.push({ id: res.id, bbox: o.bbox.slice(0, 4), endpoints: o.endpoints || '', photo: osrc || '',
        hole_ends: Array.isArray(o.hole_ends) ? o.hole_ends.filter((h) => validHole(project, h)).slice(0, 2) : [] });
    }
  }
  if (crossView.length) {
    const text = `Cross-view matches in ${imageName || 'scan'} need a glance: ${crossView.join('; ')}. `
      + 'Correct the parts/wires list if any match is wrong.';
    // Stable key from the sorted match set: re-merging the same views must
    // not pile up another confirm question every time.
    const stable = 'crossview:' + [...crossView].sort().join('|').toLowerCase()
      .replace(/[^a-z0-9|]+/g, ' ').trim().slice(0, 120);
    if (!project.questions.some((q) => q && q.key === stable)) {
      project.questions.push({ id: 'Q' + Math.random().toString(36).slice(2, 8),
        key: stable,
        kind: 'confirm', text, target: null, options: [], status: 'open',
        answer: null, created: new Date().toISOString() });
      report.questions.push(text);
    }
  }
  const have = new Set(project.questions.map((q) => String(q.text || '').trim().toLowerCase()));
  // Near-duplicate vision questions ("Which ESP32 pins..." asked per view)
  // collapse onto the first wording with a view counter instead of piling up.
  const fuzzyKey = (t) => String(t || '').toLowerCase().replace(/[^a-z0-9 ]/g, '')
    .split(/\s+/).filter(Boolean).slice(0, 3).join(' ');
  const fuzzySeen = new Map();
  for (const q of project.questions) {
    if (q && q.kind === 'vision') {
      const k = fuzzyKey(q.text);
      if (k && !fuzzySeen.has(k)) fuzzySeen.set(k, q);
    }
  }
  for (const raw of Array.isArray(obs.questions) ? obs.questions : []) {
    const text = String(raw || '').trim();
    if (!text || have.has(text.toLowerCase())) continue;
    const k = fuzzyKey(text);
    if (k && fuzzySeen.has(k)) {
      const prev = fuzzySeen.get(k);
      if (imageName && !prev.text.includes(imageName)) prev.text += ` [also ${imageName}]`;
      continue;
    }
    have.add(text.toLowerCase());
    if (k) fuzzySeen.set(k, { text });
    const q = { id: 'Q' + Math.random().toString(36).slice(2, 8),
      key: 'vision:' + Date.now().toString(36) + project.questions.length,
      kind: 'vision', text, target: null, options: [], status: 'open',
      answer: null, created: new Date().toISOString() };
    project.questions.push(q);
    if (k) fuzzySeen.set(k, q);
    report.questions.push(text);
  }
  const size = obs.board && obs.board.size_guess;
  if ((size === 'full' || size === 'half') && project.breadboard.size !== size && wasEmpty)
    report.sizeHint = size;
  touch(project);
  return report;
}

// All physical holes sharing one strip-metal group (a terminal bank of 5 or
// one rail segment honoring railGaps). Used to visualize full nets: the
// union-find only tracks populated holes, but the copper joins the whole strip.
function groupHoles(project, groupId) {
  const parts = String(groupId || '').split(':');
  if (parts.length !== 4 || parts[0] !== 'G') return [];
  const rows = rowsOf(project);
  if (parts[1] === 'term') {
    const row = +parts[2], bank = +parts[3];
    if (!(row >= 1 && row <= rows) || (bank !== 0 && bank !== 1)) return [];
    const cols = bank === 0 ? 'ABCDE' : 'FGHIJ';
    return [...cols].map((c) => 't' + row + c);
  }
  if (parts[1] === 'rail') {
    const key = parts[2], seg = +parts[3];
    const m = /^r([LR])([+-])$/.exec(key);
    if (!m || !(seg >= 0)) return [];
    const gaps = (project.breadboard.railGaps[key] || []).slice().sort((a, b) => a - b);
    if (seg > gaps.length) return [];
    const lo = seg === 0 ? 1 : gaps[seg - 1] + 1;
    const hi = seg < gaps.length ? gaps[seg] : rows;
    const out = [];
    for (let i = lo; i <= hi; i++) out.push('r' + m[1] + m[2] + i);
    return out;
  }
  return [];
}

// Expand populated net holes to every physical hole on the same strips.
function expandNetHoles(project, holes) {
  const out = new Set();
  for (const h of holes || []) {
    if (!parseHole(h)) continue;
    out.add(h);
    const g = groupOf(project, h);
    if (g) for (const x of groupHoles(project, g)) out.add(x);
  }
  return [...out];
}

// ---- checks (only confirmed physics; uncertainty stays visible) ------------
function runChecks(project) {
  const findings = [];
  let seq = 0;
  const add = (severity, title, evidence, detail, uncertain) => {
    findings.push({ id: 'F' + (++seq), severity, title, evidence, detail, uncertain: !!uncertain });
  };
  // 1. Confirmed power-to-ground shorts.
  for (const net of computeNets(project)) {
    if (net.roles['+'] && net.roles['-']) {
      const plus = net.holes.find((h) => { const p = parseHole(h); return p && p.kind === 'rail' && p.pol === '+'; });
      const minus = net.holes.find((h) => { const p = parseHole(h); return p && p.kind === 'rail' && p.pol === '-'; });
      add(net.certainty <= 1 ? 'error' : 'warning',
        'Power rail shorted to ground rail',
        [plus, minus].filter(Boolean),
        net.certainty <= 1
          ? 'A confirmed conductive path joins the + and − rails. Remove a jumper before powering the board.'
          : 'A possible path joins the + and − rails, but at least one link is inferred or unknown. Confirm the wiring before powering the board.',
        net.certainty > 1);
    }
  }
  // 2. Supply pins on the wrong rail; 3. unplaced pins.
  for (const c of project.components) {
    c.pins.forEach((p, i) => {
      if (!p.hole) {
        add('info', `${c.id} pin ${p.func || i + 1} is not placed`,
          [c.id], 'Place every pin in a hole before trusting connectivity.', c.status !== 'confirmed');
        return;
      }
      const net = netOfHole(project, p.hole);
      if (!net) return;
      const fn = String(p.func || '').toUpperCase();
      const certain = net.certainty <= 1 && c.status !== 'inferred' && c.status !== 'unknown';
      if ((fn === 'GND' || fn === 'VSS') && net.roles['+'] && !net.roles['-'])
        add(certain ? 'error' : 'warning', `${c.id} ${p.func} sits on the + rail`, [p.hole, c.id],
          'A ground pin appears wired to the positive rail. Verify the rail and the pin placement.', !certain);
      if ((fn === 'VCC' || fn === 'VDD' || fn === '3V3' || fn === '5V' || fn === 'VIN') && net.roles['-'] && !net.roles['+'])
        add(certain ? 'error' : 'warning', `${c.id} ${p.func} sits on the ground rail`, [p.hole, c.id],
          'A supply pin appears wired to the ground rail. Verify the rail and the pin placement.', !certain);
    });
    // 4. LED/diode polarity against known rails.
    if ((c.kind === 'led' || c.kind === 'diode') && c.pins.length >= 2) {
      const [a, k] = c.pins;
      if (a.hole && k.hole) {
        const na = netOfHole(project, a.hole), nk = netOfHole(project, k.hole);
        if (na && nk && na.roles['-'] && !na.roles['+'] && nk.roles['+'] && !nk.roles['-']) {
          const certain = na.certainty <= 1 && nk.certainty <= 1;
          add(certain ? 'error' : 'warning', `${c.id} looks reversed`, [a.hole, k.hole, c.id],
            'The anode sits on the ground rail and the cathode on the positive rail. Flip the part or fix the wiring.', !certain);
        }
      }
    }
  }
  // 5. Wires with unknown endpoints are questions, surfaced as info here.
  for (const w of project.wires) {
    if (!w.from || !w.to)
      add('info', `${w.id} has an unplaced end`, [w.id],
        'Set both endpoints before trusting this connection.', true);
  }
  return findings;
}

// ---- questions --------------------------------------------------------------
function openQuestions(project) { return project.questions.filter((q) => q.status === 'open'); }

function suggestQuestions(project) {
  const out = [];
  // Never re-suggest a key that was already asked, answered or open.
  const seen = new Set(project.questions.map((q) => q.key));
  // Priority: identities (1) > pin mappings (2) > rail/power ends (3) >
  // other wire ends (4) > project purpose (5) > circuit function (6) >
  // confirmations and details (7). Sorted before return.
  const push = (key, kind, text, target, options, prio) => {
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ id: 'Q' + Math.random().toString(36).slice(2, 8), key, kind, text, target,
      options: options || [], status: 'open', answer: null, prio: prio || 7,
      created: new Date().toISOString() });
  };
  const isOff = (c) => /off-board/i.test(c.notes || '');
  for (const c of project.components) {
    // Scanned parts arrive 'inferred'; anything still generic and
    // unconfirmed needs an identity question regardless of status word —
    // unless vision already read a part number (c.mpn), in which case
    // re-asking the user to type it back is noise, not diligence.
    if ((c.status === 'unknown' || c.kind === 'generic') && c.status !== 'confirmed' && !c.mpn) {
      const pins = c.pins.length;
      const where = c.region ? ` near ${c.region}` : '';
      const marks = /marking|unreadable|unclear/i.test(c.notes || '') ? ', but its markings are not readable' : '';
      push('identify:' + c.id, 'identify',
        `What is ${c.id} (${PARTS[c.kind].label}${where} with ${pins} pins${marks})? Give its name or part number (e.g. MG90S, ESP32-WROOM-32), or answer "I don't know".`,
        c.id, [], 1);
    }
    // Off-board parts (vision's own words) have no holes; asking wastes a
    // quiz round per pin.
    if (isOff(c)) continue;
    // Standard-footprint modules place from ONE anchor hole instead of one
    // question per pin (30 rounds for a devkit is not a usable quiz).
    const anchorOpen = project.questions.some((x) => x.key === 'anchor:' + c.id && x.status === 'open');
    let justAnchored = false;
    if (c.kind === 'esp32devkit' && c.pins.every((p) => !p.hole) && !anchorOpen
      && !project.questions.some((x) => x.key === 'anchor:' + c.id && x.status === 'answered')) {
      push('anchor:' + c.id, 'module-anchor',
        `Where does ${c.id} sit? Name the hole for its ${c.pins[0].func} corner pin on the E row (e.g. t10E) — I'll place all 30 pins from the devkit footprint.`,
        c.id, [], 2);
      justAnchored = true;
    }
    const anchorPending = c.kind === 'esp32devkit' && (anchorOpen || justAnchored
      || project.questions.some((x) => x.key === 'anchor:' + c.id && x.status === 'open'));
    // Orientation check after an anchor placement: one flip word fixes a
    // mirrored module instead of 30 re-answers.
    if (c.kind === 'esp32devkit' && c.placedView === 'user-anchor'
      && !project.confirmations['orient-ok:' + c.id]
      && !project.questions.some((x) => x.key === 'orient:' + c.id && x.status === 'open')) {
      push('orient:' + c.id, 'orient',
        `I placed all 30 ${c.id} pins from your anchor. If the USB end points the wrong way, say "flip" — otherwise say OK.`,
        c.id, [], 2);
    }
    if (!anchorPending) c.pins.forEach((p, i) => {
      if (!p.hole) push(`pin:${c.id}:${i}`, 'placement', `Which hole does ${c.id} pin ${p.func} sit in?`, c.id, [], 2);
    });
  }
  for (const w of project.wires) {
    if (w.from && w.to) continue;
    const plugs = Array.isArray(w.plugs) ? w.plugs.filter((p) => p && p.side && Number.isFinite(p.pos)) : [];
    plugs.forEach((plug, i) => {
      if (plug.end && w[plug.end]) return;
      const bank = plug.side === 'L' ? 'A–E' : 'F–J';
      const pm = plug.spread > 1 ? ` (±${Math.ceil(plug.spread / 2)})` : '';
      const which = plugs.length > 1 ? (i === 0 ? 'One board end' : 'Its other board end') : `The ${w.color} wire's board end`;
      push(`wire:${w.id}:${i}`, 'wiring',
        `${which} is near the ${bank}-side power rail around column ${plug.pos}${pm}. Which lane — + (red stripe) or − (blue stripe)? Name a hole (e.g. r${plug.side}+${plug.pos}), answer + / −, or "I don't know".`,
        w.id, [], 3);
    });
    const covered = new Set(plugs.map((p) => p.end));
    if ((!w.from && !covered.has('from')) || (!w.to && !covered.has('to'))) {
      // A wire seen leaving the board gets an off-board-aware prompt: asking
      // for "the hole" of an end that is not on the board is unanswerable.
      // Endpoints stay UNKNOWN unless a real hole (or I don't know) is given.
      const off = railHintFromText(w.notes || '').some((h) => h && h.offboard);
      // A single photo-verified proposal is quoted so the user can confirm
      // it with YES instead of guessing blind; corrections still win.
      const singles = (w.holeProps && Array.isArray(w.holeProps.ends) ? w.holeProps.ends : [])
        .filter((h, i) => h && validHole(project, h)
          && ((i === 0 && !w.from && !covered.has('from')) || (i === 1 && !w.to && !covered.has('to'))));
      const singleBit = singles.length
        ? ` Photo evidence suggests ${singles.join(' / ')} — reply YES to accept, or name the correct hole(s).` : '';
      push('wire:' + w.id, 'wiring', off
        ? `The ${w.color || ''} wire ${w.id} leaves the board on one end. Which hole does the board end use (e.g. t10A)?${singleBit} If neither end is on the board, answer "I don't know".`
        : `Where does the loose end of ${w.id} connect?${singleBit} Name a hole or answer "I don't know".`, w.id, [], 4);
    }
  }
  if (!project.intent.text)
    push('intent', 'intent', 'What is this circuit intended to do — what are you building? Describe its purpose in a sentence or two.', null, [], 5);
  for (const c of project.components) {
    if (c.kind === 'esp32devkit' && !project.confirmations['purpose:' + c.id])
      push('purpose:' + c.id, 'purpose', `What is ${c.id} supposed to do in this circuit?`, c.id, [], 5);
  }
  // Circuit-function follow-ups: only where the answer changes the
  // interpretation. Actuator + controller pairs ask about control mapping.
  const mcus = project.components.filter((c) => c.kind === 'esp32devkit');
  const servos = project.components.filter((c) => /servo/i.test(String(c.value || '') + ' ' + String(c.notes || '') + ' ' + String(c.mpn || '')));
  for (const s of servos) {
    const mcu = mcus[0];
    if (mcu && !project.confirmations['control:' + s.id])
      push('control:' + s.id, 'function',
        `Is the servo ${s.id} controlled by ${mcu.id} or by an external controller?`, s.id,
        [mcu.id, 'External controller', "I don't know"], 6);
    const ctrlQ = project.questions.find((x) => x.key === 'control:' + s.id && x.status === 'answered' && !x.deferred);
    const ctrl = ctrlQ ? String(ctrlQ.answer || '') : '';
    if (mcu && ctrl && new RegExp(mcu.id.replace(/[^A-Za-z0-9]/g, ''), 'i').test(ctrl.replace(/[^A-Za-z0-9]/g, ''))
      && !project.confirmations['signalpin:' + s.id])
      push('signalpin:' + s.id, 'function',
        `Which ${mcu.id} pin drives the ${s.id} servo signal?`, s.id, [], 6);
    if (!project.confirmations['servopower:' + s.id])
      push('servopower:' + s.id, 'function',
        `What voltage powers the servo ${s.id}?`, s.id, ['5 V', '3.3 V', "I don't know"], 6);
  }
  // Single-sighting parts across a multi-photo scan are the most likely
  // misreads: one summary confirmation instead of silent trust or deletion.
  if (project.images.length >= 3) {
    const singles = [...project.components, ...project.wires]
      .filter((e) => Array.isArray(e.sightings) && e.sightings.length <= 1 && e.status !== 'confirmed')
      .map((e) => e.id).sort();
    if (singles.length)
      push('singles:' + singles.join(','), 'confirm',
        `Seen only once across ${project.images.length} photos — confirm these are real, not misreads: ${singles.join(', ')}.`, null, [], 7);
  }
  out.sort((a, b) => (a.prio - b.prio) || (a.created < b.created ? -1 : 1));
  return out;
}

// Resolved-fact progress: identified components, placed pins, set wire
// ends, answered questions. Honest counts, never a fabricated percentage.
function reportProgress(project) {
  let total = 0, resolved = 0;
  for (const c of project.components) {
    total++;
    if (c.status === 'confirmed' || c.kind !== 'generic') resolved++;
    for (const p of c.pins) {
      total++;
      if (p.hole) resolved++;
    }
  }
  for (const w of project.wires) {
    total += 2;
    if (w.from) resolved++;
    if (w.to) resolved++;
  }
  for (const q of project.questions) {
    total++;
    if (q.status === 'answered') resolved++;
  }
  return { resolved, total };
}

// Internal engineering project report: everything PreFlight knows, split
// by evidence grade. Derived purely from project state (no separate store
// to drift); history comes from the persisted question log. Inferred items
// are labeled as such — never presented as confirmed facts.
function buildReport(project) {
  const rows = rowsOf(project);
  const nets = computeNets(project);
  const open = project.questions.filter((q) => q.status === 'open');
  const answered = project.questions.filter((q) => q.status === 'answered');
  const comps = project.components.map((c) => ({
    id: c.id, kind: c.kind, label: PARTS[c.kind] ? PARTS[c.kind].label : c.kind,
    status: c.status, manufacturer: c.manufacturer || null, mpn: c.mpn || null,
    package: c.package || null, value: c.value || null, purpose: c.purpose || null,
    datasheet: c.datasheet || null, offBoard: /off-board/i.test(c.notes || ''),
    region: c.region || null, sightings: (c.sightings || []).slice(),
    pinsPlaced: c.pins.filter((p) => p.hole).length, pinsTotal: c.pins.length,
    pins: c.pins.map((p) => ({ func: p.func, hole: p.hole || null })),
    evidence: (c.notes || '').slice(0, 300),
  }));
  const wires = project.wires.map((w) => ({
    id: w.id, color: w.color, status: w.status, from: w.from || null, to: w.to || null,
    plugs: (w.plugs || []).map((p) => ({ side: p.side, pos: p.pos, spread: p.spread, end: p.end || null })),
    sightings: (w.sightings || []).slice(),
  }));
  const railNets = nets.filter((n) => n.holes.some((h) => { const p = parseHole(h); return p && p.kind === 'rail'; }));
  return {
    version: 1,
    generated: new Date().toISOString(),
    revision: answered.length + Object.keys(project.confirmations || {}).length,
    identity: {
      name: project.name, purpose: project.intent.text || null,
      status: !open.length && (comps.length || wires.length) ? 'resolved'
        : (comps.length || wires.length) ? 'investigating' : 'empty',
      openQuestions: open.length,
    },
    components: comps,
    unknownComponents: comps.filter((c) => c.status === 'unknown').map((c) => c.id),
    electrical: {
      nets: nets.map((n) => ({ holes: n.holes.slice().sort(), certainty: n.certainty, roles: Object.keys(n.roles).sort() })),
      railNets: railNets.length,
      confirmedConnections: wires.filter((w) => w.from && w.to).length,
      unresolvedEnds: wires.reduce((a, w) => a + (w.from ? 0 : 1) + (w.to ? 0 : 1), 0),
    },
    physical: {
      size: project.breadboard.size, rows, pitchMm: PITCH_MM,
      railGaps: JSON.parse(JSON.stringify(project.breadboard.railGaps || {})),
      wires,
      offBoard: comps.filter((c) => c.offBoard).map((c) => c.id),
      images: (project.images || []).map((im) => im.name || im),
    },
    intent: {
      text: project.intent.text || null, updated: project.intent.updated || null,
      purposes: comps.filter((c) => c.purpose).map((c) => ({ id: c.id, purpose: c.purpose })),
    },
    evidence: {
      userConfirmed: Object.keys(project.confirmations || {}).sort(),
      deferred: answered.filter((q) => q.deferred).map((q) => q.key),
      missing: open.map((q) => ({ key: q.key, kind: q.kind, text: q.text })),
    },
    history: answered.map((q) => ({ key: q.key, answer: (q.answer || '').slice(0, 200),
      at: q.answeredAt || null, deferred: !!q.deferred })),
  };
}

// Auto-resolve open questions whose condition is already satisfied, so the
// quiz never asks about placed pins, connected wires, or confirmed parts.
function resolveStaleQuestions(project) {
  let n = 0;
  for (const q of project.questions) {
    if (!q || q.status !== 'open') continue;
    if (q.kind === 'placement' && q.target) {
      const c = getComponent(project, q.target);
      const idx = q.key && q.key.includes(':') ? Number(q.key.split(':').pop()) : -1;
      const hole = c && c.pins[idx] ? c.pins[idx].hole : null;
      if (hole) {
        q.status = 'answered'; q.answer = hole; q.autoResolved = true;
        q.answeredAt = new Date().toISOString(); n++;
      }
    } else if (q.kind === 'wiring' && q.target) {
      const w = project.wires.find((x) => x.id === q.target);
      if (w && w.from && w.to) {
        q.status = 'answered'; q.answer = w.from + ' to ' + w.to; q.autoResolved = true;
        q.answeredAt = new Date().toISOString(); n++;
      }
    } else if (q.kind === 'identify' && q.target) {
      const c = getComponent(project, q.target);
      if (c && c.status === 'confirmed') {
        q.status = 'answered'; q.answer = (c.value || PARTS[c.kind].label) + ' (confirmed in editor)';
        q.autoResolved = true; q.answeredAt = new Date().toISOString(); n++;
      }
    }
  }
  if (n) touch(project);
  return n;
}

// "I don't know"-style answers: recorded, never treated as facts, and the
// question key stays asked so the quiz does not nag again.
function isDontKnow(text) {
  return /^\s*(i\s+(dont|don't|do\s+not)\s+know|don'?t\s+know|unknown|not\s+sure|skip(\s+for\s+now)?)\s*[.!?]?\s*$/i.test(String(text || ''));
}

// Looks like a manufacturer part number (digits mixed with letters/dashes,
// service words excluded). Heuristic triage only — never a verified MPN.
function looksLikeMpn(text) {
  const t = String(text || '').trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9_.+-]{2,39}$/.test(t)) return false;
  if (!/\d/.test(t) || !/[A-Za-z]/.test(t)) return false;
  if (/^(yes|no|none|left|right|gelb|gruen|blau|rot|schwarz|braun|wire|cable|plus|minus)$/i.test(t)) return false;
  return true;
}
// Merge-time twin of the identify answer path: promote what vision already
// read so the quiz never re-asks the user for known info. Conservative on
// purpose — letter-led tokens only, so descriptor shapes like 30-pin,
// 3-wire or 10k keep their questions exactly as today.
function scanMpn(text) {
  const toks = String(text || '').split(/[\s,;]+/)
    .map((s) => s.replace(/^["'(\[]+|["'().,\]]+$/g, ''));
  return toks.find((s) => /^[A-Za-z]/.test(s) && looksLikeMpn(s)) || null;
}

// AI-proposed wire ends from close-up/registered views. Holes are validated
// (never guessed), recorded as INFERRED proposals only — from/to stay empty.
// Only a both-ends proposal raises a confirm-style question: YES places them,
// anything else falls through to the normal wiring-answer path.
function proposeWireHoles(project, wireId, holes, viewName) {
  const w = (project.wires || []).find((x) => x && x.id === wireId);
  if (!w || (w.from && w.to)) return { ok: false };
  w.holeProps = w.holeProps || {};
  if (!Array.isArray(w.holeProps.ends)) w.holeProps.ends = [null, null];
  if (!Array.isArray(w.holeProps.views)) w.holeProps.views = [];
  (Array.isArray(holes) ? holes : []).slice(0, 2).forEach((raw, i) => {
    const h = String(raw || '').trim();
    if (!validHole(project, h)) return;
    if (!w.holeProps.ends[i]) w.holeProps.ends[i] = h;
    else if (w.holeProps.ends[i] !== h)
      w.notes = (w.notes ? w.notes + '; ' : '') + `${viewName || 'scan'} suggests ${h} (kept ${w.holeProps.ends[i]})`;
  });
  if (viewName && !w.holeProps.views.includes(viewName)) w.holeProps.views.push(viewName);
  touch(project);
  const e0 = w.holeProps.ends[0], e1 = w.holeProps.ends[1];
  if (e0 && e1 && !w.from && !w.to) {
    const key = `wireconf:${wireId}:${e0}|${e1}`;
    if (!project.questions.some((q) => q && q.key === key)) {
      project.questions.push({ id: 'Q' + Math.random().toString(36).slice(2, 8),
        key, kind: 'wiring', text: `${wireId} (${w.color}) looks like it runs from ${e0} to ${e1}`
          + (w.holeProps.views.length ? ` (seen in ${w.holeProps.views.slice(-2).join(', ')})` : '')
          + '. Reply YES to place both ends, or give the correct holes.',
        target: wireId, holes: [e0, e1], options: [], status: 'open',
        answer: null, created: new Date().toISOString() });
      return { ok: true, asked: true };
    }
  }
  return { ok: true, asked: false };
}

function answerQuestion(project, qid, answer) {
  const q = project.questions.find((x) => x.id === qid);
  if (!q || q.status !== 'open') return { ok: false, error: 'Unknown question' };
  q.status = 'answered'; q.answer = String(answer).slice(0, 2000); q.answeredAt = new Date().toISOString();
  // Single application point for quiz popup AND questions panel: answers
  // update the structured model (pins, wires, identities), never just a
  // transcript. Unknowns stay unknown; duplicates are impossible because
  // answered keys are never re-suggested.
  if (isDontKnow(q.answer)) {
    q.deferred = true;
    touch(project);
    return { ok: true, deferred: true };
  }
  if (q.key && q.key.indexOf('orient:') === 0 && q.target) {
    // Module orientation confirmation: "flip" mirrors an end-to-end
    // reversed placement; anything else confirms the assumed direction.
    if (/flip|mirror|revers|swap|wrong\s*(way|direction|end|side)|other\s*way/i.test(q.answer)) {
      flipModule(project, q.target);
      q.answer += ' (pins flipped)';
    } else {
      confirm(project, 'orient-ok:' + q.target, q.answer);
    }
  }
  if (q.kind === 'intent') {
    project.intent.text = q.answer; project.intent.updated = q.answeredAt;
  }
  if (q.kind === 'purpose' && q.target) {
    const c = getComponent(project, q.target);
    if (c) { c.purpose = q.answer; c.status = 'confirmed'; }
    confirm(project, q.kind + ':' + q.target, q.answer);
  }
  if (q.kind === 'identify' && q.target) {
    const c = getComponent(project, q.target);
    if (c) {
      // Trials in order: part number, datasheet URL, kind word, free text.
      const url = q.answer.match(/https?:\/\/\S+/i);
      if (url) c.datasheet = url[0].slice(0, 300);
      const mpn = q.answer.split(/[\s,;]+/).map((s) => s.replace(/^["'(\[]+|["'().,\]]+$/g, ''))
        .find((s) => looksLikeMpn(s));
      if (mpn) c.mpn = mpn.toUpperCase().slice(0, 40);
      const kindHit = Object.keys(PARTS).find((k) =>
        new RegExp('\\b' + k.replace(/[^a-z0-9]/gi, '') + '\\b', 'i').test(q.answer.replace(/[^a-z0-9 ]/gi, '')));
      if (kindHit && c.kind === 'generic' && c.pins.length === PARTS[kindHit].pins.length) {
        c.kind = kindHit;
        c.pins.forEach((p, i) => { p.func = PARTS[kindHit].pins[i].func; });
      }
      if (!c.value) c.value = q.answer.slice(0, 60);
      c.status = 'confirmed';
      c.notes = (c.notes ? c.notes + '; ' : '') + 'identified by user: ' + q.answer.slice(0, 120);
    }
    confirm(project, q.kind + ':' + q.target, q.answer);
  }
  if (q.kind === 'function' && q.target) confirm(project, q.key, q.answer);
  if (q.kind === 'empty') confirm(project, 'boardEmpty', q.answer);
  if (q.kind === 'placement' && q.target) {
    const hole = normalizeHole(q.answer);
    if (hole && validHole(project, hole)) {
      const c = getComponent(project, q.target);
      const tail = (q.key || '').split(':').pop();
      let idx = /^\d+$/.test(tail) ? Number(tail) : -1;
      if (idx < 0 && c) idx = c.pins.findIndex((p) => p.func === tail);
      if (c && c.pins[idx] && !c.pins[idx].hole) setPinHole(project, q.target, idx, hole);
    }
  }
  if (q.kind === 'module-anchor' && q.target) {
    // One anchor hole places a whole standard footprint (devkit 2x15 on
    // rows E/F). Invalid answers leave the question open for retry.
    const c = getComponent(project, q.target);
    const hole = normalizeHole(q.answer);
    const m = hole && /^t(\d+)([EF])$/i.exec(hole);
    const rows = rowsOf(project);
    const start = m ? +m[1] : 0;
    if (c && c.kind === 'esp32devkit' && m && start >= 1 && start + 14 <= rows
      && c.pins.length === 30) {
      const side = m[2].toUpperCase(), other = side === 'E' ? 'F' : 'E';
      c.pins.forEach((p, i) => { p.hole = i < 15 ? `t${start + i}${side}` : `t${start + i - 15}${other}`; });
      c.placedView = 'user-anchor';
      confirm(project, 'anchor:' + c.id, q.answer);
      touch(project);
      return { ok: true };
    }
    q.status = 'open';
    q.deferred = false;
    touch(project);
    return { ok: false, error: 'Name a terminal hole like t10E.' };
  }
  if (q.kind === 'wiring' && q.target) {
    const w = project.wires.find((x) => x.id === q.target);
    if (w) {
      // Confirm-style fast path: YES (with no holes of its own) accepts the
      // AI-proposed ends stored on the question; corrections fall through.
      if (Array.isArray(q.holes) && q.holes.length >= 2 && !w.from && !w.to
        && !extractHoles(q.answer).length
        && /^(y|yes|yeah|yep|confirm|confirmed|correct|right|ok|okay|sure)\b/i.test(q.answer.trim())) {
        setWire(project, w.id, { from: q.holes[0], to: q.holes[1] });
        confirm(project, q.key, q.answer);
        touch(project);
        return { ok: true };
      }
      // Single-proposal fast path: YES (with no holes of its own) accepts
      // the photo-verified single end(s) quoted in a loose-end question.
      // Only fills empty slots; named holes in the answer still win.
      if (!extractHoles(q.answer).length
        && /^(y|yes|yeah|yep|confirm|confirmed|correct|right|ok|okay|sure)\b/i.test(String(q.answer).trim())) {
        const pe = (w.holeProps && Array.isArray(w.holeProps.ends) ? w.holeProps.ends : []).slice(0, 2);
        if ((pe[0] && validHole(project, pe[0]) && !w.from) || (pe[1] && validHole(project, pe[1]) && !w.to)) {
          if (pe[0] && validHole(project, pe[0]) && !w.from) setWire(project, w.id, { from: pe[0] });
          if (pe[1] && validHole(project, pe[1]) && !w.to) setWire(project, w.id, { to: pe[1] });
          confirm(project, q.key, q.answer);
          touch(project);
          return { ok: true };
        }
      }
      const holes = extractHoles(q.answer).filter((h) => validHole(project, h));
      if (holes.length >= 2) {
        setWire(project, w.id, { from: holes[0], to: holes[1] });
      } else if (holes.length === 1) {
        if (!w.from) setWire(project, w.id, { from: holes[0] });
        else if (!w.to) setWire(project, w.id, { to: holes[0] });
      } else {
        // Lane-word answers resolve a guided plug hypothesis ("+ lane"
        // with plug rL??22 sets rL+22 on the plug's loose end).
        const plugs = Array.isArray(w.plugs) ? w.plugs : [];
        const tail = (q.key || '').split(':').pop();
        const idx = /^\d+$/.test(tail) ? Number(tail) : 0;
        const plug = plugs[idx] || plugs[0];
        const pol = plug && plug.side && Number.isFinite(plug.pos) ? parseLaneAnswer(q.answer) : null;
        if (plug && pol) {
          const hole = 'r' + plug.side + pol + plug.pos;
          if (validHole(project, hole)) {
            const end = (plug.end === 'from' || plug.end === 'to') && !w[plug.end]
              ? plug.end : (!w.from ? 'from' : (!w.to ? 'to' : null));
            if (end) setWire(project, w.id, { [end]: hole });
          }
        }
      }
    }
  }
  touch(project);
  return { ok: true };
}

// Propose holes for a detection bbox using image registration. px2mm maps
// image pixels to {x, z} board mm (from the capture's homography). Two-pin
// parts use the bbox long axis (resistor-style span); anything else gets a
// single suggested anchor. Returns {holes:[..], conf} or {suggest} or null.
// Points mapping off the board (off-board parts, wire ends leaving the
// frame) abstain instead of snapping to a wrong hole.
function onBoard(project, p, margin) {
  if (!p || !isFinite(p.x) || !isFinite(p.z)) return false;
  const m = margin === undefined ? 6 : margin;
  const BL = rowsOf(project) * PITCH_MM + 12;
  return Math.abs(p.x) <= BOARD_WIDTH / 2 + m && Math.abs(p.z) <= BL / 2 + m;
}
// Board-mm z-window where a vision bbox crosses a rail band. Samples the
// bbox EDGES (off-board corners project wildly, so corners alone would span
// the whole rail) and keeps samples near the band in x, padded by zMargin.
// Returns [z0, z1] clipped to zSpan, or null when the box never nears the
// band. Pure: px2mm maps pixels to {x, z}.
function bandWindowFromBox(px2mm, imgW, imgH, bbox, bandLo, bandHi, zSpan, opts) {
  if (typeof px2mm !== 'function' || !Array.isArray(bbox) || bbox.length !== 4) return null;
  const o = opts || {};
  const xTol = o.xTol || 4, zMargin = o.zMargin || 12;
  const [u0, v0, u1, v1] = bbox;
  const pts = [];
  for (let k = 0; k <= 12; k++) {
    const t = k / 12;
    pts.push([u0 + (u1 - u0) * t, v0], [u0 + (u1 - u0) * t, v1],
      [u0, v0 + (v1 - v0) * t], [u1, v0 + (v1 - v0) * t]);
  }
  const zs = [];
  for (const [u, v] of pts) {
    const p = px2mm(u * imgW, v * imgH);
    if (!p || !Number.isFinite(p.x) || !Number.isFinite(p.z)) continue;
    if (p.x < bandLo - xTol || p.x > bandHi + xTol) continue;
    zs.push(p.z);
  }
  if (zs.length < 2) return null;
  const z0 = Math.max(zSpan[0], Math.min(...zs) - zMargin);
  const z1 = Math.min(zSpan[1], Math.max(...zs) + zMargin);
  return z1 > z0 + 1 ? [z0, z1] : null;
}

function proposeHolesFromBox(project, px2mm, imgW, imgH, bbox, pinCount) {
  if (typeof px2mm !== 'function' || !Array.isArray(bbox) || bbox.length !== 4) return null;
  const pts = [[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]]]
    .map(([u, v]) => px2mm(u * imgW, v * imgH));
  if (pts.some((p) => !p || !isFinite(p.x) || !isFinite(p.z))) return null;
  if (pinCount === 2) {
    let a = pts[0], b = pts[1], bd = -1;
    for (let i = 0; i < 4; i++) for (let j = i + 1; j < 4; j++) {
      const d = (pts[i].x - pts[j].x) ** 2 + (pts[i].z - pts[j].z) ** 2;
      if (d > bd) { bd = d; a = pts[i]; b = pts[j]; }
    }
    const at = (t) => ({ x: a.x + (b.x - a.x) * t, z: a.z + (b.z - a.z) * t });
    const p1 = at(0.3), p2 = at(0.7);
    if (!onBoard(project, p1) || !onBoard(project, p2)) return null;
    const h1 = holeAt(project, p1.x, p1.z);
    const h2 = holeAt(project, p2.x, p2.z);
    if (!h1 || !h2 || h1.hole === h2.hole) return null;
    const miss = Math.max(Math.hypot(h1.dx, h1.dz), Math.hypot(h2.dx, h2.dz));
    if (miss > 0.35 * PITCH_MM) return null;
    return { holes: [h1.hole, h2.hole], conf: miss < 0.2 * PITCH_MM ? 'high' : 'medium' };
  }
  const cx = (pts[0].x + pts[2].x) / 2, cz = (pts[0].z + pts[2].z) / 2;
  if (!onBoard(project, { x: cx, z: cz })) return null;
  const h = holeAt(project, cx, cz);
  if (!h || Math.hypot(h.dx, h.dz) > 0.6 * PITCH_MM) return null;
  return { suggest: h.hole };
}

// Footprint proposal for a groove-straddling module (ESP32 devkit: 2x15
// pins at 0.1" pitch in rows E and F). The bbox must map to a plausible
// module span along the board; anything else abstains. Pin 1 is assumed at
// the LOW-column end — a 180° registration flip mirrors this, so callers
// must ask the user to confirm the orientation. Returns {pins:[30], span}
// or null.
function proposeModuleHoles(project, px2mm, imgW, imgH, bbox, pinsPerSide) {
  if (typeof px2mm !== 'function' || !Array.isArray(bbox) || bbox.length !== 4) return null;
  const n = pinsPerSide || 15;
  if (!(n >= 2)) return null;
  const pts = [[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]]]
    .map(([u, v]) => px2mm(u * imgW, v * imgH));
  if (pts.some((p) => !p || !isFinite(p.x) || !isFinite(p.z))) return null;
  const xs = pts.map((p) => p.x), zs = pts.map((p) => p.z);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const z0 = Math.min(...zs), z1 = Math.max(...zs);
  const rows = rowsOf(project);
  // Module length runs along the board: the mapped span must fit n pins.
  // Vision boxes are loose (margins, adjacent housings), so the span gate
  // is generous — the width and groove gates below carry the precision.
  const spanPitches = (z1 - z0) / PITCH_MM;
  if (!(spanPitches >= n - 3 && spanPitches <= n + 14)) return null;
  if (!((x1 - x0) >= 12 && (x1 - x0) <= 45)) return null; // devkit-module width
  const cx = (x0 + x1) / 2;
  if (Math.abs(cx) > 9) return null; // must straddle the center groove
  // Anchor from the span CENTER: bbox edges are plastic, but plastic and
  // pins share a center, so the middle pin lands right either way.
  const c0 = Math.round((z0 + z1) / 2 / PITCH_MM + (rows + 1) / 2) - Math.floor(n / 2);
  if (!(c0 >= 1 && c0 + n - 1 <= rows)) return null;
  const pins = [];
  for (let i = 0; i < n; i++) pins.push('t' + (c0 + i) + 'E');
  for (let i = 0; i < n; i++) pins.push('t' + (c0 + i) + 'F');
  if (pins.some((h) => !validHole(project, h))) return null;
  return { pins, span: `t${c0}E–t${c0 + n - 1}E / t${c0}F–t${c0 + n - 1}F` };
}

// Clear an inferred placement (pins, view, best-view score) so a better
// registered view can place the part again. Never clears user-confirmed
// pins: callers must check confirmation first.
function clearPlacement(project, compId) {
  const comp = getComponent(project, compId);
  if (!comp) return { ok: false, error: 'Unknown component' };
  comp.pins.forEach((p) => { p.hole = null; });
  comp.placedView = null;
  comp.bestConf = 0;
  touch(project);
  return { ok: true };
}

// Reverse a component's pin-hole assignment (180° footprint rotation).
// Used when the user reports a mirrored module placement.
function flipModule(project, compId) {
  const comp = getComponent(project, compId);
  if (!comp) return { ok: false, error: 'Unknown component' };
  const holes = comp.pins.map((p) => p.hole).reverse();
  comp.pins.forEach((p, i) => { p.hole = holes[i]; });
  comp.notes = (comp.notes ? comp.notes + '; ' : '') + 'pin order flipped by user';
  touch(project);
  return { ok: true };
}

// ---- undo/redo (snapshot stack) ------------------------------------------------
function snapshot(project) { return JSON.stringify(project); }
function restore(project, snap) {
  const data = JSON.parse(snap);
  for (const k of Object.keys(project)) delete project[k];
  Object.assign(project, data);
}
function makeHistory(project, limit) {
  const stack = { past: [], future: [], limit: limit || 50 };
  return {
    checkpoint() {
      stack.past.push(snapshot(project));
      if (stack.past.length > stack.limit) stack.past.shift();
      stack.future.length = 0;
    },
    undo() {
      if (!stack.past.length) return false;
      stack.future.push(snapshot(project));
      restore(project, stack.past.pop());
      return true;
    },
    redo() {
      if (!stack.future.length) return false;
      stack.past.push(snapshot(project));
      restore(project, stack.future.pop());
      return true;
    },
  };
}

// ---- serialization ------------------------------------------------------------
function serialize(project) { return JSON.stringify(project); }
function deserialize(text) {
  const data = JSON.parse(text);
  if (!data || data.version !== VERSION || !Array.isArray(data.components) || !Array.isArray(data.wires))
    throw new Error('Not a LiveScan project file.');
  return data;
}

const api = {
  VERSION, PITCH_MM, CENTER_GAP, RAIL_INNER_X, RAIL_OUTER_X, RAIL_PLUS_IS_OUTER, railLaneX, BOARD_WIDTH,
  PARTS, SIZES, newProject, rowsOf, parseHole, validHole, normalizeHole, extractHoles,
  railHintFromText, selectRailPlug, parseLaneAnswer, isDontKnow, looksLikeMpn,
  buildReport, reportProgress,
  groupOf, holeXY, holeAt, onBoard, addComponent, getComponent, setPinHole, clearPlacement, setComponent,
  removeComponent, addWire, setWire, removeWire, confirm, computeNets,
  netOfHole, groupHoles, expandNetHoles, zoneName, bboxIoU, mergeScan, proposeHolesFromBox, proposeModuleHoles, flipModule, endOverlap, runChecks, openQuestions, suggestQuestions, answerQuestion, resolveStaleQuestions, bandWindowFromBox, proposeWireHoles,
  makeHistory, snapshot, restore, serialize, deserialize,
};
if (typeof module !== 'undefined' && module.exports) module.exports = api;
else root.Breadboard = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
