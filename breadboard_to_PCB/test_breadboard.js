// LiveScan breadboard model: geometry, connectivity, checks, undo, persistence.
const assert = require('node:assert/strict');
const BB = require('./breadboard.js');

const full = BB.newProject('t', { size: 'full' });
assert.equal(BB.rowsOf(full), 63);
assert.equal(BB.rowsOf(BB.newProject('h', { size: 'half' })), 30);
assert.ok(BB.validHole(full, 't1A') && BB.validHole(full, 't63J'));
assert.ok(BB.validHole(full, 'rL+1') && BB.validHole(full, 'rR-63'));
assert.ok(!BB.validHole(full, 't64A') && !BB.validHole(full, 'rL+64') && !BB.validHole(full, 'zzz'));
assert.deepEqual(BB.parseHole('t12E'), { kind: 'terminal', row: 12, col: 'E', bank: 0 });
assert.deepEqual(BB.parseHole('t12F').bank, 1);
// Strip groups: same row-bank connects, banks split.
assert.equal(BB.groupOf(full, 't5A'), BB.groupOf(full, 't5E'));
assert.notEqual(BB.groupOf(full, 't5E'), BB.groupOf(full, 't5F'));
assert.notEqual(BB.groupOf(full, 't5A'), BB.groupOf(full, 't6A'));
// Rails split at declared gaps.
assert.equal(BB.groupOf(full, 'rL+1'), BB.groupOf(full, 'rL+63'));
full.breadboard.railGaps['rL+'] = [32];
assert.notEqual(BB.groupOf(full, 'rL+1'), BB.groupOf(full, 'rL+63'));
assert.equal(BB.groupOf(full, 'rL+33'), BB.groupOf(full, 'rL+63'));
// Hole coordinates are symmetric and pitched.
const a = BB.holeXY(full, 't1A'), b = BB.holeXY(full, 't1J');
assert.ok(Math.abs(a.x + b.x) < 1e-9 && a.z === b.z);
assert.ok(Math.abs(BB.holeXY(full, 't2A').z - a.z - BB.PITCH_MM) < 1e-9);
// Real-board layout: 7.62 mm E-F groove, every lane inside the body.
const e5 = BB.holeXY(full, 't5E'), f5 = BB.holeXY(full, 't5F');
assert.ok(Math.abs((f5.x - e5.x) - BB.CENTER_GAP) < 1e-9);
for (const id of ['t1A', 't63J', 'rL+1', 'rL-63', 'rR+30', 'rR-30'])
  assert.ok(Math.abs(BB.holeXY(full, id).x) < BB.BOARD_WIDTH / 2, id + ' inside board');
// Calibrated rail geometry (hole-periodicity + visual comb on real photos):
// inner lane |x| 18.5, outer 20.75. Reference board has MIXED polarity —
// red (+) is the OUTER lane on L, INNER on R (rail clips are flippable, so
// this is a measured board-assembly property, not a universal assumption).
assert.equal(BB.holeXY(full, 'rL+1').x, -20.75);
assert.equal(BB.holeXY(full, 'rL-1').x, -18.5);
assert.equal(BB.holeXY(full, 'rR+1').x, 18.5);
assert.equal(BB.holeXY(full, 'rR-1').x, 20.75);
assert.equal(BB.railLaneX('L', '+'), -20.75);
assert.equal(BB.railLaneX('R', '-'), 20.75);
console.log('geometry: passed');

// Confirmed rail short is an error; uncertain path is a warning.
const p = BB.newProject('short', {});
BB.addWire(p, { id: 'J1', from: 'rL+5', to: 't10A', status: 'confirmed' });
BB.addWire(p, { id: 'J2', from: 't10C', to: 'rL-9', status: 'confirmed' });
let findings = BB.runChecks(p);
let short = findings.find((f) => f.title.includes('shorted'));
assert.equal(short.severity, 'error');
assert.equal(short.uncertain, false);
BB.setWire(p, 'J2', { status: 'inferred' });
short = BB.runChecks(p).find((f) => f.title.includes('shorted'));
assert.equal(short.severity, 'warning');
assert.equal(short.uncertain, true);
// Isolated wires are not shorts.
const q = BB.newProject('clean', {});
BB.addWire(q, { id: 'J1', from: 'rL+5', to: 't10A', status: 'confirmed' });
assert.ok(!BB.runChecks(q).some((f) => f.title.includes('shorted')));
console.log('shorts: passed');

// Supply pins on the wrong rail; LED polarity; unplaced pins.
const e = BB.newProject('esp', {});
BB.addComponent(e, { id: 'U1', kind: 'esp32devkit', status: 'confirmed' });
BB.setPinHole(e, 'U1', 13, 'rL+3'); // GND pin index 13 on + rail
let f = BB.runChecks(e).find((x) => x.title.includes('sits on the + rail'));
assert.equal(f.severity, 'error');
const l = BB.newProject('led', {});
BB.addComponent(l, { id: 'D1', kind: 'led', status: 'confirmed' });
BB.setPinHole(l, 'D1', 0, 'rL-4'); // anode to ground
BB.setPinHole(l, 'D1', 1, 'rL+4'); // cathode to power
f = BB.runChecks(l).find((x) => x.title.includes('looks reversed'));
assert.equal(f.severity, 'error');
const u = BB.newProject('unplaced', {});
BB.addComponent(u, { id: 'R1', kind: 'resistor', status: 'observed' });
assert.ok(BB.runChecks(u).some((x) => x.title.includes('not placed')));
console.log('supply/polarity/placement: passed');

// Questions, answers, intent, confirmations.
const iq = BB.newProject('q', {});
let sug = BB.suggestQuestions(iq);
assert.ok(sug.some((s) => s.kind === 'intent'));
iq.questions.push(...sug);
BB.answerQuestion(iq, iq.questions.find((s) => s.kind === 'intent').id, 'Door sensor with ESP32');
assert.equal(iq.intent.text, 'Door sensor with ESP32');
BB.addComponent(iq, { id: 'U9', kind: 'generic', status: 'unknown' });
sug = BB.suggestQuestions(iq);
assert.ok(sug.some((s) => s.key === 'identify:U9'));
console.log('questions: passed');

// Undo/redo and serialization round-trip.
const h = BB.newProject('hist', {});
const hist = BB.makeHistory(h);
hist.checkpoint();
BB.addComponent(h, { id: 'R1', kind: 'resistor' });
assert.equal(h.components.length, 1);
assert.ok(hist.undo());
assert.equal(h.components.length, 0);
assert.ok(hist.redo());
assert.equal(h.components.length, 1);
assert.equal(BB.deserialize(BB.serialize(h)).components[0].id, 'R1');
assert.throws(() => BB.deserialize('{"nope":true}'), /Not a LiveScan/);
console.log('undo/persistence: passed');

// Net visualization expands populated holes to full copper strips.
const v = BB.newProject('viz', {});
assert.deepEqual(BB.groupHoles(v, BB.groupOf(v, 't5A')),
  ['t5A', 't5B', 't5C', 't5D', 't5E']);
assert.deepEqual(BB.groupHoles(v, BB.groupOf(v, 't5F')),
  ['t5F', 't5G', 't5H', 't5I', 't5J']);
const rail63 = BB.groupHoles(v, BB.groupOf(v, 'rL+4'));
assert.equal(rail63.length, 63);
assert.ok(rail63[0] === 'rL+1' && rail63[62] === 'rL+63');
// Rail gaps split the expansion into segments.
v.breadboard.railGaps['rL+'] = [32];
assert.equal(BB.groupHoles(v, BB.groupOf(v, 'rL+4')).length, 32);
assert.equal(BB.groupHoles(v, BB.groupOf(v, 'rL+63')).length, 31);
assert.deepEqual(BB.groupHoles(v, 'bogus'), []);
const grown = BB.newProject('grow', {});
BB.addWire(grown, { id: 'J1', from: 'rL+5', to: 't10A', status: 'confirmed' });
const exp = BB.expandNetHoles(grown, ['rL+5', 't10A', 'zzz']);
assert.equal(exp.length, 63 + 5); // full rail segment + full terminal bank
assert.ok(exp.includes('rL+63') && exp.includes('t10E') && !exp.includes('zzz'));
console.log('net-expansion: passed');

// Scan merge: inferred adds, kind+zone dedupe, confirmed parts untouched.
const SM = require('./scanmath.js');
for (const bb of [[0, 0, 0.2, 0.2], [0.8, 0.8, 1, 1], [0.4, 0.4, 0.6, 0.6], null, [0, 0.4, 0.2, 0.6]])
  assert.equal(BB.zoneName(bb), SM.zoneName(bb));
const sc = BB.newProject('scan', {});
const OBS = { board: { size_guess: 'half' },
  components: [{ kind: 'resistor', label: 'blue resistor', value_guess: '10k',
    bbox: [0, 0, 0.2, 0.2], confidence: 'high', notes: '' }],
  wires: [{ color: 'red', bbox: [0.8, 0.8, 1, 1], endpoints: 'unclear', confidence: 'low' }],
  questions: ['Is this powered by 5V?'] };
let rep = BB.mergeScan(sc, OBS, 'a.jpg');
assert.deepEqual(rep.added.length, 2);
assert.deepEqual(rep.matched, []);
assert.deepEqual(rep.questions, ['Is this powered by 5V?']);
assert.equal(rep.sizeHint, 'half');
const r1 = sc.components[0];
assert.equal(r1.status, 'inferred');
assert.equal(r1.source, 'scan');
assert.equal(r1.region, 'upper-left');
assert.equal(r1.value, ''); // guesses never become values
assert.ok(r1.notes.includes('a.jpg') && r1.notes.includes('10k'));
// Repeat sighting matches instead of duplicating; new zone adds.
rep = BB.mergeScan(sc, OBS, 'b.jpg');
assert.deepEqual(rep.added, []);
assert.deepEqual(rep.matched.length, 2);
assert.deepEqual(rep.questions, []);
assert.ok(sc.components[0].notes.includes('b.jpg'));
const OBS2 = { components: [{ kind: 'resistor', label: 'r', bbox: [0.8, 0, 1, 0.2] }] };
rep = BB.mergeScan(sc, OBS2, 'c.jpg');
assert.equal(rep.added.length, 1);
assert.equal(sc.components[1].region, 'upper-right');
// Confirmed parts never match; unknown kinds become generic.
sc.components[0].status = 'confirmed';
rep = BB.mergeScan(sc, OBS, 'd.jpg');
assert.equal(rep.added.filter((id) => id !== sc.wires[0].id).length, 1);
const OBS3 = { components: [{ kind: 'teleporter', bbox: null }] };
rep = BB.mergeScan(sc, OBS3, 'e.jpg');
assert.equal(sc.components.find((c) => c.id === rep.added[0]).kind, 'generic');
// IoU matching: distinct neighbours stay apart, repeats collapse.
assert.equal(BB.bboxIoU([0, 0, 1, 1], [0, 0, 1, 1]), 1);
assert.equal(BB.bboxIoU([0, 0, 0.5, 1], [0.5, 0, 1, 1]), 0);
assert.ok(Math.abs(BB.bboxIoU([0, 0, 1, 1], [0.5, 0, 1.5, 1]) - 1 / 3) < 1e-9);
assert.equal(BB.bboxIoU(null, [0, 0, 1, 1]), null);
const dense = BB.newProject('dense', {});
const NEIGH = { components: [
  { kind: 'resistor', label: 'left', bbox: [0.35, 0.35, 0.5, 0.5] },
  { kind: 'resistor', label: 'right', bbox: [0.52, 0.35, 0.67, 0.5] },
] };
rep = BB.mergeScan(dense, NEIGH, 'f.jpg');
assert.equal(rep.added.length, 2); // same zone, disjoint boxes: two parts
assert.equal(rep.matched.length, 0);
rep = BB.mergeScan(dense, NEIGH, 'f.jpg');
assert.deepEqual(rep.added, []);
assert.equal(rep.matched.length, 2); // identical boxes re-seen
const SHIFT = { components: [{ kind: 'resistor', label: 'moved', bbox: [0.36, 0.36, 0.51, 0.51] }] };
rep = BB.mergeScan(dense, SHIFT, 'g.jpg');
assert.deepEqual(rep.added, []); // IoU ~0.9 with left neighbour
assert.equal(rep.matched.length, 1);
// Cross-kind (generic) needs strong overlap; weak overlap stays separate.
const cross = BB.newProject('cross', {});
BB.mergeScan(cross, { components: [{ kind: 'led', label: 'L', bbox: [0.4, 0.4, 0.6, 0.6] }] }, 'h.jpg');
rep = BB.mergeScan(cross, { components: [{ kind: 'generic', label: 'G', bbox: [0.5, 0.5, 0.7, 0.7] }] }, 'i.jpg');
assert.equal(rep.added.length, 1); // IoU ~0.14 < 0.5: distinct parts
rep = BB.mergeScan(cross, { components: [{ kind: 'generic', label: 'G2', bbox: [0.41, 0.41, 0.59, 0.59] }] }, 'j.jpg');
assert.deepEqual(rep.added, []); // IoU ~0.8: same part re-seen
assert.equal(rep.matched.length, 1);
// Batch merge: per-item photo sources map to notes, sightings and photoBoxes.
const batched = BB.newProject('batched', {});
const BOBS = { board: {},
  components: [{ kind: 'resistor', label: 'r-batch', bbox: [0, 0, 0.2, 0.2],
    photo: 'p1.jpg', seen_in: ['p1.jpg', 'p2.jpg'] }],
  wires: [{ color: 'blue', bbox: [0.7, 0.7, 0.9, 0.9], endpoints: 'rail to row',
    photo: 'p2.jpg', seen_in: ['p2.jpg'] }],
  questions: [] };
rep = BB.mergeScan(batched, BOBS, 'batch of 2');
assert.equal(rep.added.length, 2);
assert.ok(batched.components[0].notes.includes('p1.jpg'));
assert.deepEqual(batched.components[0].sightings, ['p1.jpg', 'p2.jpg']);
assert.deepEqual(batched.wires[0].sightings, ['p2.jpg']);
assert.ok(!batched.components[0].notes.includes('batch of 2'));
assert.ok(rep.photoBoxes['p1.jpg'][batched.components[0].id]);
assert.equal(rep.wireSightings[0].photo, 'p2.jpg');
// Single-photo merges without item sources keep the old behavior.
const single = BB.newProject('single', {});
rep = BB.mergeScan(single, OBS, 'solo.jpg');
assert.ok(single.components[0].notes.includes('solo.jpg'));
assert.deepEqual(rep.photoBoxes['solo.jpg'][single.components[0].id], [0, 0, 0.2, 0.2]);
// One anchor hole places a whole devkit instead of 30 pin questions.
const anch = BB.newProject('anch', {});
BB.addComponent(anch, { kind: 'esp32devkit', status: 'inferred', source: 'scan', region: 'center', notes: '' });
let aq = BB.suggestQuestions(anch).filter((q) => /pin|sit in|hole/i.test(q.text));
assert.equal(aq.filter((q) => q.kind === 'placement').length, 0);
assert.equal(aq.filter((q) => q.kind === 'module-anchor').length, 1);
anch.questions = aq;
const anchQ = BB.openQuestions(anch).find((q) => q.kind === 'module-anchor');
assert.deepEqual(BB.answerQuestion(anch, anchQ.id, 't10E'), { ok: true });
assert.equal(anch.components[0].pins.filter((p) => p.hole).length, 30);
assert.equal(anch.components[0].pins[0].hole, 't10E');
assert.equal(BB.suggestQuestions(anch).filter((q) => q.kind === 'placement').length, 0);
assert.equal(BB.suggestQuestions(anch).filter((q) => q.key === 'orient:U1').length, 1);
const bad = BB.newProject('anchbad', {});
BB.addComponent(bad, { kind: 'esp32devkit', status: 'inferred', source: 'scan', region: 'center', notes: '' });
bad.questions = BB.suggestQuestions(bad);
const anchor2 = BB.openQuestions(bad).find((q) => q.kind === 'module-anchor');
assert.equal(BB.answerQuestion(bad, anchor2.id, 'banana').ok, false);
assert.equal(bad.questions.find((q) => q.id === anchor2.id).status, 'open');
// Registration proposals: axis endpoints snap to holes or abstain.
const px2mm = (x) => ({ x: x * 0.5 - 25, z: -55.88 }); // row 10
const prop = BB.proposeHolesFromBox(sc, px2mm, 100, 100, [0.2, 0.3, 0.292, 0.5], 2);
assert.deepEqual(prop.holes, ['t10A', 't10B']);
assert.equal(prop.conf, 'high');
assert.equal(BB.proposeHolesFromBox(sc, px2mm, 100, 100, [0.21, 0.31, 0.23, 0.33], 2), null);
const anchor = BB.proposeHolesFromBox(sc, px2mm, 100, 100, [0.18, 0.3, 0.262, 0.5], 8);
assert.equal(anchor.suggest, 't10A');
assert.equal(BB.proposeHolesFromBox(sc, px2mm, 100, 100, [0.48, 0.3, 0.52, 0.5], 8), null);
assert.equal(BB.proposeHolesFromBox(sc, null, 100, 100, [0, 0, 1, 1], 2), null);
// Unlocated re-sighting: a close-up without bbox matches the lone same-kind part.
const views = BB.newProject('views', {});
BB.mergeScan(views, { components: [{ kind: 'esp32devkit', label: 'ESP', bbox: [0.2, 0.5, 0.5, 0.8] }] }, 'a.jpg');
rep = BB.mergeScan(views, { components: [{ kind: 'esp32devkit', label: 'ESP again', bbox: null }] }, 'b.jpg');
assert.deepEqual(rep.added, []);
assert.equal(rep.matched.length, 1);
// ... but not when two same-kind candidates exist.
BB.mergeScan(views, { components: [{ kind: 'resistor', label: 'R left', bbox: [0.1, 0.1, 0.2, 0.2] }] }, 'a.jpg');
BB.mergeScan(views, { components: [{ kind: 'resistor', label: 'R right', bbox: [0.7, 0.7, 0.8, 0.8] }] }, 'a.jpg');
rep = BB.mergeScan(views, { components: [{ kind: 'resistor', label: 'R ?', bbox: null }] }, 'b.jpg');
assert.equal(rep.added.length, 1); // ambiguous: stays a separate sighting
// Cross-viewpoint: a devkit in a new zone re-sights by kind singleton.
rep = BB.mergeScan(views, { components: [{ kind: 'esp32devkit', label: 'ESP angled', bbox: [0.6, 0.5, 0.9, 0.7] }] }, 'c.jpg');
assert.deepEqual(rep.added, []);
assert.equal(rep.matched.length, 1);
assert.ok(views.questions.some((q) => q.kind === 'confirm'), 'cross-view match flagged for confirmation');
// Cross-viewpoint wires: color singleton on both sides matches.
const wv = BB.newProject('wviews', {});
BB.mergeScan(wv, { wires: [{ color: 'red', bbox: [0.1, 0.1, 0.3, 0.3] }] }, 'a.jpg');
rep = BB.mergeScan(wv, { wires: [{ color: 'red', bbox: [0.6, 0.6, 0.9, 0.9] }] }, 'b.jpg');
assert.deepEqual(rep.added, []);
assert.equal(rep.matched.length, 1);
rep = BB.mergeScan(wv, { wires: [{ color: 'red', bbox: [0.6, 0.6, 0.9, 0.9] }, { color: 'red', bbox: [0.1, 0.5, 0.2, 0.6] }] }, 'c.jpg');
assert.equal(rep.added.length, 2); // two reds in one view: no forced fusion
console.log('scan-merge: passed');

// Wire lanes: cross-view joins by color, fragments collapse, disjoint splits.
{
  const lanes = BB.newProject('lanes', {});
  let r = BB.mergeScan(lanes, { wires: [{ color: 'blue', bbox: [0.1, 0.1, 0.4, 0.4], endpoints: 'off-board to top rail' }] }, 'a.jpg');
  assert.equal(r.added.length, 1);
  r = BB.mergeScan(lanes, { wires: [{ color: 'blue', bbox: [0.6, 0.6, 0.9, 0.9], endpoints: 'off-board to top rail right' }] }, 'b.jpg');
  assert.deepEqual(r.added, [], 'same color rejoins across zones');
  assert.equal(r.matched.length, 1);
  // Same-view overlapping fragments collapse into the lane.
  r = BB.mergeScan(lanes, { wires: [
    { color: 'blue', bbox: [0.55, 0.55, 0.9, 0.9], endpoints: 'rail' },
    { color: 'blue', bbox: [0.6, 0.6, 0.8, 0.8], endpoints: 'rail housing' }] }, 'c.jpg');
  assert.deepEqual(r.added, [], 'contained fragment collapses');
  // Same-view disjoint same-color proves multiplicity — but the geometric
  // repeat rejoins the lane and only the stranger is added.
  r = BB.mergeScan(lanes, { wires: [
    { color: 'blue', bbox: [0.6, 0.6, 0.9, 0.9], endpoints: 'off-board to top rail right' },
    { color: 'blue', bbox: [0.0, 0.7, 0.2, 0.9], endpoints: 'servo bundle left' }] }, 'd.jpg');
  assert.equal(r.added.length, 1, 'only the disjoint stranger splits');
  assert.equal(r.matched.length, 1, 'geometric repeat rejoins');
  assert.equal(lanes.wires.length, 2);
  // Sightings recorded for single-sighting flags.
  assert.deepEqual(lanes.wires[0].sightings, ['a.jpg', 'b.jpg', 'c.jpg', 'd.jpg']);
  console.log('wire-lanes: passed');
}

// Fuzzy vision questions collapse per-view repeats with view markers.
{
  const fq = BB.newProject('fq', {});
  BB.mergeScan(fq, { questions: ['Which ESP32 pins do the green wires use?'] }, 'a.jpg');
  BB.mergeScan(fq, { questions: ['Which ESP32 pins are the green jumpers on?'] }, 'b.jpg');
  BB.mergeScan(fq, { questions: ['Is the servo powered from USB?'] }, 'c.jpg');
  const vq = fq.questions.filter((q) => q.kind === 'vision');
  assert.equal(vq.length, 2, 'near-dup collapses, distinct kept');
  assert.ok(vq[0].text.includes('[also b.jpg]'), 'view marker: ' + vq[0].text);
  console.log('fuzzy-questions: passed');
}

// Module footprint: exact span from registration, honest abstains.
{
  const mp = BB.newProject('mp', { size: 'full' });
  // Overhead mapping: u -> cols 63..1 left-to-right, v -> width.
  const px2mm = (x, y) => ({ x: (y / 1200 - 0.5) * 52, z: (0.5 - x / 1600) * 172 });
  const mod = BB.proposeModuleHoles(mp, px2mm, 1600, 1200, [0.198, 0.5, 0.448, 0.8], 15);
  assert.ok(mod, 'plausible module span places');
  assert.equal(mod.pins.length, 30);
  assert.equal(mod.pins[0], 't37E');
  assert.equal(mod.pins[14], 't51E');
  assert.equal(mod.pins[15], 't37F');
  assert.equal(mod.pins[29], 't51F');
  assert.equal(BB.proposeModuleHoles(mp, px2mm, 1600, 1200, [0.0, 0.0, 0.1, 0.2], 15), null, 'off-board abstains');
  assert.equal(BB.proposeModuleHoles(mp, px2mm, 1600, 1200, [0.4, 0.4, 0.6, 0.6], 15), null, 'square blob abstains');
  console.log('module-footprint: passed');
}

// Flip + orient answers + clearPlacement + hole-name parsing.
{
  const fp = BB.newProject('fp', { size: 'full' });
  const id = BB.addComponent(fp, { kind: 'esp32devkit' }).id;
  fp.components[0].pins.forEach((p, i) => { p.hole = 't' + (37 + (i % 15)) + (i < 15 ? 'E' : 'F'); });
  BB.flipModule(fp, id);
  assert.equal(fp.components[0].pins[0].hole, 't51F', 'flip rotates 180');
  assert.equal(fp.components[0].pins[29].hole, 't37E');
  fp.questions.push({ id: 'Q1', key: 'orient:' + id, kind: 'confirm', text: 't', target: id, options: [], status: 'open', answer: null });
  BB.answerQuestion(fp, 'Q1', 'looks mirrored, please flip it');
  assert.equal(fp.components[0].pins[0].hole, 't37E', 'flip answer restores');
  assert.ok(fp.questions[0].answer.includes('(pins flipped)'));
  BB.clearPlacement(fp, id);
  assert.ok(fp.components[0].pins.every((p) => !p.hole), 'clear wipes pins');
  assert.equal(BB.normalizeHole(' t40a '), 't40A');
  assert.equal(BB.normalizeHole('Rr+25'), 'rR+25');
  assert.equal(BB.normalizeHole('nope'), null);
  assert.deepEqual(BB.extractHoles('from rR+25 to t40A please'), ['rR+25', 't40A']);
  console.log('flip-orient-parse: passed');
}

// Guided rail plugs: hint parsing, selection, lane answers, windows.
{
  // railHintFromText on REAL vision endpoint strings (obs2/6253).
  const purple = BB.railHintFromText('one end at off-board connector at left edge (off-frame bundle); other end into terminal strip near middle-right of breadboard');
  assert.equal(purple.length, 2);
  assert.ok(purple[0].offboard, 'servo end offboard');
  assert.equal(purple[1].u, 0.7);
  assert.equal(purple[1].v, 0.5);
  const brown = BB.railHintFromText('one end into terminal area near center by ESP32; other end into top power rail area near right end');
  assert.ok(brown[0].module, 'module clause flagged');
  assert.equal(brown[1].u, 0.92);
  assert.equal(brown[1].v, 0.2);
  const green = BB.railHintFromText('one end at off-board connector at left edge; other end into bottom power rail near center');
  assert.ok(green[0].offboard);
  assert.equal(green[1].u, 0.5);
  assert.equal(green[1].v, 0.8);
  const orange = BB.railHintFromText('one end off-frame left with servo bundle; other end hidden in left-edge connector bundle, unclear');
  assert.ok(orange.every((h) => h.offboard), 'fully offboard wire');
  assert.deepEqual(BB.railHintFromText(''), []);
  console.log('plug-hints: passed');
}
{
  // selectRailPlug: clusters, hints, gap splits, abstention, width cap.
  const S = BB.selectRailPlug;
  assert.deepEqual(S([{ pos: 40, width: 4 }], null), { pos: 40, spread: 1 });
  assert.deepEqual(S([{ pos: 38, width: 4 }, { pos: 40, width: 4 }], null), { pos: 39, spread: 3 });
  assert.deepEqual(S([{ pos: 16, width: 6 }, { pos: 25, width: 4 }], 17), { pos: 16, spread: 1 });
  assert.deepEqual(S([{ pos: 16, width: 6 }, { pos: 25, width: 4 }], 24), { pos: 25, spread: 1 });
  assert.equal(S([{ pos: 16, width: 6 }, { pos: 25, width: 4 }], null), null, 'hintless multi abstains');
  assert.equal(S([], 10), null);
  // Gap splits (BB830 split rails): never merge across copper breaks.
  assert.deepEqual(S([{ pos: 30, width: 4 }, { pos: 34, width: 4 }], 32, [32]), { pos: 30, spread: 1 });
  // Runaway chains cap at 12 wide (unlocalized = rejected by callers).
  const chain = [10, 14, 18, 22, 26].map((pos) => ({ pos, width: 4 }));
  assert.equal(S(chain, 18).spread <= 13, true);
  console.log('plug-select: passed');
}
{
  // parseLaneAnswer: words count, bare colors never do (wire-color trap).
  const P = BB.parseLaneAnswer;
  assert.equal(P('+'), '+');
  assert.equal(P('plus lane'), '+');
  assert.equal(P('the + rail, thanks'), '+');
  assert.equal(P('minus'), '-');
  assert.equal(P('- lane'), '-');
  assert.equal(P('lane -'), '-');
  assert.equal(P('red stripe side'), '+');
  assert.equal(P('blue stripe'), '-');
  assert.equal(P('the blue wire goes left'), null, 'bare blue is a wire color');
  assert.equal(P('red wire'), null, 'bare red is a wire color');
  assert.equal(P('no idea'), null);
  assert.equal(P(''), null);
  console.log('plug-lanes: passed');
}
{
  // bandWindowFromBox: edge sampling ignores wild off-board corners.
  const px2mm = (x, y) => ({ x: (x - 800) / 40, z: (600 - y) / 8 });
  // Box whose TL corner projects wild but whose right edge crosses the band.
  const win = BB.bandWindowFromBox(px2mm, 1600, 1200, [0.05, 0.1, 0.64, 0.67], -20.75, -18.5, [-78.74, 78.74]);
  assert.ok(win && win[1] > win[0], 'window found');
  assert.ok(win[0] >= -78.74 && win[1] <= 78.74, 'clipped to span');
  // Box nowhere near the band: null, not a garbage span.
  assert.equal(BB.bandWindowFromBox(px2mm, 1600, 1200, [0.4, 0.4, 0.6, 0.6], 18.5, 20.75, [-78.74, 78.74]), null);
  assert.equal(BB.bandWindowFromBox(null, 1600, 1200, [0, 0, 1, 1], 0, 1, [0, 1]), null);
  console.log('plug-windows: passed');
}
{
  // mergeScan reports per-wire sightings; quiz asks per-plug lane questions;
  // a lane answer on a plug sets the loose end to the right rail hole.
  const gp = BB.newProject('gp', { size: 'full' });
  const rep = BB.mergeScan(gp, { wires: [
    { color: 'purple', bbox: [0.1, 0.2, 0.6, 0.7], endpoints: 'servo bundle; other end into rail near middle-right', confidence: 'medium' },
  ] }, 'v1.jpg');
  assert.equal(rep.wireSightings.length, 1);
  assert.equal(rep.wireSightings[0].id, gp.wires[0].id);
  assert.deepEqual(rep.wireSightings[0].bbox, [0.1, 0.2, 0.6, 0.7]);
  const w = gp.wires[0];
  w.plugs = [{ side: 'L', pos: 22, spread: 3, view: 'v1.jpg', end: 'from' }];
  const qs = BB.suggestQuestions(gp).filter((q) => q.kind === 'wiring' && q.target === w.id);
  assert.equal(qs.length, 2, 'lane question for the plug end + generic for the bare end');
  const laneQ = qs.find((q) => q.key === 'wire:' + w.id + ':0');
  assert.ok(laneQ && laneQ.text.includes('column 22') && laneQ.text.includes('red stripe'), JSON.stringify(qs));
  // Lane answer flow (mirrors livescan quizAnswer): no holes + plug + words.
  assert.deepEqual(BB.extractHoles('minus lane please'), []);
  const pol = BB.parseLaneAnswer('minus lane please');
  assert.equal(pol, '-');
  const r = BB.setWire(gp, w.id, { from: 'rL' + pol + w.plugs[0].pos });
  assert.ok(r.ok);
  assert.equal(gp.wires[0].from, 'rL-22');
  assert.ok(BB.groupOf(gp, 'rL-22').startsWith('G:rail:rL-'));
  // Two-plug wire gets two lane questions, one per end.
  w.plugs.push({ side: 'R', pos: 40, spread: 4, view: 'v1.jpg', end: 'to' });
  const qs2 = BB.suggestQuestions(gp).filter((q) => q.kind === 'wiring' && q.target === w.id);
  assert.equal(qs2.length, 1, 'from resolved; only the to-plug asks');
  assert.equal(qs2[0].key, 'wire:' + w.id + ':1');
  console.log('plug-quiz: passed');
}

// Investigation system: priorities, answer application, report.
{
  // Priority order: identity(1) before pins(2) before rail ends(3) before
  // loose ends(4) before purpose(5) before function(6).
  const p = BB.newProject('inv', {});
  BB.addComponent(p, { id: 'U9', kind: 'generic' });
  BB.addWire(p, { id: 'J9', color: 'red' });
  const qs = BB.suggestQuestions(p);
  const prios = qs.map((q) => q.prio);
  assert.deepEqual(prios, prios.slice().sort((a, b) => a - b), 'sorted by priority');
  assert.equal(qs[0].key, 'identify:U9');
  assert.ok(qs[0].text.includes('What is U9'), qs[0].text);
  assert.ok(qs.some((q) => q.key === 'intent' && q.prio === 5));
  console.log('invest-prio: passed');
}
{
  // Identify answers: MPN + datasheet captured, kind adopted on pin match,
  // user-confirmed. "I don't know" defers without fabricating facts.
  const p = BB.newProject('idn', {});
  BB.addComponent(p, { id: 'U1', kind: 'generic', notes: 'black module, markings unreadable' });
  BB.addComponent(p, { id: 'U2', kind: 'generic' });
  for (const q of BB.suggestQuestions(p)) p.questions.push(q);
  const q1 = p.questions.find((q) => q.key === 'identify:U1');
  let r = BB.answerQuestion(p, q1.id, 'MG90S micro servo https://example.com/mg90s.pdf');
  assert.ok(r.ok && !r.deferred);
  const u1 = BB.getComponent(p, 'U1');
  assert.equal(u1.mpn, 'MG90S');
  assert.equal(u1.datasheet, 'https://example.com/mg90s.pdf');
  assert.equal(u1.status, 'confirmed');
  assert.ok(!BB.suggestQuestions(p).some((q) => q.key === 'identify:U1'), 'never re-asked');
  const q2 = p.questions.find((q) => q.key === 'identify:U2');
  r = BB.answerQuestion(p, q2.id, "I don't know");
  assert.ok(r.ok && r.deferred);
  assert.equal(BB.getComponent(p, 'U2').status, 'unknown', 'stays unknown');
  assert.equal(BB.getComponent(p, 'U2').mpn, '', 'no invented MPN');
  assert.ok(!BB.suggestQuestions(p).some((q) => q.key === 'identify:U2'), 'deferred, not nagged');
  assert.ok(BB.isDontKnow('unknown') && BB.isDontKnow('Skip') && !BB.isDontKnow('MG90S'));
  assert.ok(BB.looksLikeMpn('ESP32-WROOM-32') && !BB.looksLikeMpn('blue') && !BB.looksLikeMpn('yes'));
  console.log('invest-identify: passed');
}
{
  // Unified application: placement + wiring answers work via answerQuestion
  // (panel path), including lane words on plugs and double-answer safety.
  const p = BB.newProject('app', {});
  BB.addComponent(p, { id: 'R1', kind: 'resistor' });
  BB.addWire(p, { id: 'J1', color: 'red' });
  p.wires[0].plugs = [{ side: 'R', pos: 40, spread: 2, end: 'from' }];
  for (const q of BB.suggestQuestions(p)) p.questions.push(q);
  const pinQ = p.questions.find((q) => q.key === 'pin:R1:0');
  BB.answerQuestion(p, pinQ.id, 't10A');
  assert.equal(BB.getComponent(p, 'R1').pins[0].hole, 't10A');
  const wq = p.questions.find((q) => q.key === 'wire:J1:0');
  BB.answerQuestion(p, wq.id, 'minus lane');
  assert.equal(p.wires[0].from, 'rR-40');
  const dup = BB.answerQuestion(p, wq.id, 'rR+99');
  assert.ok(!dup.ok, 'answered questions reject second answers');
  assert.equal(p.wires[0].from, 'rR-40', 'no conflicting overwrite');
  console.log('invest-apply: passed');
}
{
  // Servo follow-ups: control question appears for servo+MCU; signal-pin
  // only after ESP32 control confirmed; power always asked once.
  const p = BB.newProject('fol', {});
  BB.addComponent(p, { id: 'U1', kind: 'esp32devkit' });
  BB.addComponent(p, { id: 'S1', kind: 'generic', notes: 'MG90S micro servo off-board' });
  for (const q of BB.suggestQuestions(p)) p.questions.push(q);
  assert.ok(p.questions.some((q) => q.key === 'control:S1'), 'control asked');
  assert.ok(p.questions.some((q) => q.key === 'servopower:S1'), 'power asked');
  assert.ok(!p.questions.some((q) => q.key === 'signalpin:S1'), 'signal pin waits for control answer');
  const cq = p.questions.find((q) => q.key === 'control:S1');
  BB.answerQuestion(p, cq.id, 'U1');
  for (const q of BB.suggestQuestions(p)) p.questions.push(q);
  assert.ok(p.questions.some((q) => q.key === 'signalpin:S1'), 'signal pin follows ESP32 control');
  console.log('invest-followups: passed');
}
{
  // Report: structured, evidence-graded, revisioned; progress counts facts.
  const p = BB.newProject('rep', {});
  BB.addComponent(p, { id: 'U1', kind: 'esp32devkit', status: 'confirmed' });
  BB.addWire(p, { id: 'J1', color: 'red', from: 'rR+40', to: 't40F' });
  for (const q of BB.suggestQuestions(p)) p.questions.push(q);
  const iq = p.questions.find((q) => q.key === 'intent');
  BB.answerQuestion(p, iq.id, 'Door sensor');
  const r = BB.buildReport(p);
  assert.equal(r.version, 1);
  assert.equal(r.identity.name, 'rep');
  assert.equal(r.identity.purpose, 'Door sensor');
  assert.equal(r.components.length, 1);
  assert.ok(r.electrical.nets.length >= 1);
  assert.equal(r.electrical.confirmedConnections, 1);
  assert.equal(r.electrical.unresolvedEnds, 0);
  assert.ok(r.history.some((h) => h.key === 'intent' && h.answer === 'Door sensor'));
  assert.ok(r.revision >= 1);
  const prog = BB.reportProgress(p);
  assert.ok(prog.resolved > 0 && prog.resolved <= prog.total);
  // Roundtrip preserves everything the report needs.
  const q = BB.deserialize(BB.serialize(p));
  const r2 = BB.buildReport(q);
  assert.equal(r2.identity.purpose, 'Door sensor');
  assert.equal(r2.electrical.confirmedConnections, 1);
  assert.deepEqual(r2.history, r.history);
  console.log('invest-report: passed');
}

{
  // No question pile-up across repeated merges: crossview confirms use a
  // stable key, vision re-runs stay bounded, answered keys never return.
  const p = BB.newProject('dup', {});
  const obs = { board: { size_guess: 'unknown', rails_visible: [], coverage_notes: '' },
    components: [{ kind: 'resistor', label: 'R tan', value_guess: '', bbox: [0.2, 0.3, 0.4, 0.5], confidence: 'medium', notes: '' }],
    wires: [], questions: ['Which pin is VCC?'] };
  BB.mergeScan(p, obs, 'batch of 7');
  const n1 = p.questions.length;
  BB.mergeScan(p, obs, 'batch of 7');
  const n2 = p.questions.length;
  BB.mergeScan(p, obs, 'batch of 7');
  const n3 = p.questions.length;
  BB.mergeScan(p, obs, 'batch of 7');
  const n4 = p.questions.length;
  assert.ok(n1 > 0, 'first merge asks questions');
  assert.ok(n4 <= n2 + 1, `re-merges stay bounded (${n1} -> ${n2} -> ${n3} -> ${n4})`);
  const confirms = p.questions.filter((q) => q.kind === 'confirm');
  assert.ok(confirms.length <= 1, `one crossview confirm, not one per merge (got ${confirms.length})`);
  // Answered keys are never re-suggested.
  const key = p.questions[0].key;
  BB.answerQuestion(p, p.questions[0].id, "I don't know");
  const req = BB.suggestQuestions(p).filter((q) => q.key === key);
  assert.equal(req.length, 0, 'answered key is not re-suggested');
  console.log('no-pileup: passed');
}
{
  // Off-board wires get an answerable prompt; plain wires keep the generic one.
  const p = BB.newProject('w', {});
  BB.addWire(p, { color: 'brown', source: 'scan', notes: 'brown wire; one end at the off-board bundle; other end near top rail' });
  BB.addWire(p, { color: 'blue', source: 'scan', notes: 'blue wire near left rail' });
  const qs = BB.suggestQuestions(p).filter((q) => q.kind === 'wiring');
  const off = qs.find((q) => q.target === 'J1');
  const plain = qs.find((q) => q.target === 'J2');
  assert.ok(off && /leaves the board/.test(off.text), 'off-board wire names the off-board end');
  assert.ok(plain && /loose end/.test(plain.text), 'plain wire keeps the hole prompt');
  assert.equal(qs.filter((q) => q.target === 'J1').length, 1, 'one question per wire');
  console.log('offboard-prompt: passed');
}
{
  // Vision-read part numbers promote at merge so the quiz never re-asks
  // the user to type back what the model already extracted.
  const p = BB.newProject('mpn', {});
  BB.mergeScan(p, { components: [{ kind: 'generic', label: 'MG90S micro servo off-board',
    notes: 'pink label MG90S Micro servo readable in top view', bbox: [0.4, 0, 0.6, 0.2], confidence: 'high' }],
    wires: [], questions: [] }, 'a.jpg');
  const u = BB.getComponent(p, 'U1');
  assert.equal(u.mpn, 'MG90S', 'scan MPN recorded');
  assert.equal(u.status, 'inferred', 'machine-read, not user-confirmed');
  assert.equal(BB.suggestQuestions(p).filter((q) => q.kind === 'identify').length, 0, 'no re-ask for known part');
  // Descriptor shapes must never promote.
  for (const label of ['30-pin header strip', '3-wire bundle', '10k resistor']) {
    const px = BB.newProject('mpnx', {});
    BB.mergeScan(px, { components: [{ kind: 'generic', label, notes: label, bbox: [0.1, 0.1, 0.2, 0.2], confidence: 'low' }],
      wires: [], questions: [] }, 'b.jpg');
    assert.ok(!BB.getComponent(px, 'U1').mpn, 'no promotion for ' + label);
  }
  console.log('scan-mpn: passed');
}

// wireconf: AI hole-end proposals merge through sightings, both ends verified
// raise a confirm-style wiring question; YES places, corrections apply,
// unknowns defer, conflicts keep the first claim with a note.
{
  const p = BB.newProject('wc', { size: 'full' });
  BB.addWire(p, { id: 'J1', color: 'red', status: 'inferred', source: 'scan', region: 'top', notes: 'red wire' });
  const rep = BB.mergeScan(p, { wires: [{ color: 'red', bbox: [0.1, 0.1, 0.5, 0.5],
    endpoints: 'rail to terminal', hole_ends: ['rL+5', 't10C'],
    confidence: 'high', photo: 'IMG_1.JPG', seen_in: ['IMG_1.JPG'] }] }, 'IMG_1.JPG');
  assert.ok(rep.matched.includes('J1'), 'red re-sighting joins J1');
  const sight = rep.wireSightings.find((s) => s.id === 'J1');
  assert.deepEqual(sight.hole_ends, ['rL+5', 't10C']);

  const pB = BB.newProject('wcb', { size: 'full' });
  const rep2 = BB.mergeScan(pB, { wires: [{ color: 'blue', bbox: [0.2, 0.2, 0.6, 0.6],
    endpoints: 'x', hole_ends: ['zzz', 't99Z'], confidence: 'low', photo: 'IMG_2.JPG', seen_in: [] }] }, 'IMG_2.JPG');
  assert.equal(rep2.added.length, 1, 'blue wire added, not vacuous');
  assert.deepEqual(rep2.wireSightings[0].hole_ends, [], 'invalid hole IDs dropped');

  // Single-end proposal records without asking; the second end asks.
  let r = BB.proposeWireHoles(p, 'J1', ['rL+5', ''], 'IMG_1.JPG');
  assert.ok(r.ok && !r.asked);
  assert.ok(!p.questions.some((q) => q.key && q.key.indexOf('wireconf:') === 0), 'no question on one end');
  r = BB.proposeWireHoles(p, 'J1', ['', 't10C'], 'IMG_2.JPG');
  assert.ok(r.asked, 'both ends proposed -> confirm question');
  const q = p.questions.find((x) => x.key === 'wireconf:J1:rL+5|t10C');
  assert.ok(q && q.kind === 'wiring');
  assert.equal(BB.answerQuestion(p, q.id, 'YES').ok, true);
  const w = p.wires.find((x) => x.id === 'J1');
  assert.equal(w.from, 'rL+5');
  assert.equal(w.to, 't10C');

  // Typed corrections override the proposal.
  const p2 = BB.newProject('wc2', { size: 'full' });
  BB.addWire(p2, { id: 'J2', color: 'black', status: 'inferred', source: 'scan', region: '', notes: '' });
  BB.proposeWireHoles(p2, 'J2', ['rL+5', 't10C'], 'IMG_1.JPG');
  const q2 = p2.questions.find((x) => x.key.indexOf('wireconf:') === 0);
  assert.equal(BB.answerQuestion(p2, q2.id, 't5A to t5C').ok, true);
  const w2 = p2.wires.find((x) => x.id === 'J2');
  assert.equal(w2.from, 't5A');
  assert.equal(w2.to, 't5C');

  // "I don't know" defers without placing; later conflicts keep first claim.
  const p3 = BB.newProject('wc3', { size: 'full' });
  BB.addWire(p3, { id: 'J3', color: 'red', status: 'inferred', source: 'scan', region: '', notes: '' });
  BB.proposeWireHoles(p3, 'J3', ['rL+5', 't10C'], 'IMG_1.JPG');
  const q3 = p3.questions.find((x) => x.key.indexOf('wireconf:') === 0);
  assert.equal(BB.answerQuestion(p3, q3.id, "I don't know").deferred, true);
  assert.ok(!p3.wires.find((x) => x.id === 'J3').from, 'nothing placed on unknown');
  const r4 = BB.proposeWireHoles(p3, 'J3', ['rL+7', 't10C'], 'IMG_9.JPG');
  assert.ok(r4.ok && !r4.asked, 'no second question for answered key');
  const w3 = p3.wires.find((x) => x.id === 'J3');
  assert.ok(w3.notes.includes('IMG_9.JPG suggests rL+7'), 'conflict noted, first claim kept');
  console.log('wireconf: passed');
}

// segment-fixture: a probe-style observation naming holes in the WRONG repeated
// segment (debugger direct probe said t6E–t15E; verified U1 span is t40E–t54E).
// Merge must carry the IDs but never place ends or ask confirm on its own —
// ends are set only via the registration gate + user confirmation.
{
  const p = BB.newProject('segfix', { size: 'full' });
  BB.addWire(p, { id: 'J1', color: 'purple', status: 'inferred', source: 'scan', region: 'top', notes: 'purple wire' });
  const rep = BB.mergeScan(p, { wires: [{ color: 'purple', bbox: [0.08, 0.23, 0.64, 0.55],
    endpoints: 'off-board to mid-right terminal area', hole_ends: ['t6E', 't15E'],
    confidence: 'medium', photo: 'IMG_6253.jpg', seen_in: ['IMG_6253.jpg'] }] }, 'batch of 7');
  const sight = rep.wireSightings.find((s) => s.id === 'J1');
  assert.deepEqual(sight.hole_ends, ['t6E', 't15E'], 'grammar-valid IDs are carried');
  const w = p.wires.find((x) => x.id === 'J1');
  assert.ok(!w.from && !w.to, 'merge alone never places ends');
  assert.ok(!w.holeProps, 'merge alone records no proposals');
  assert.ok(!p.questions.some((q) => q.key && q.key.indexOf('wireconf:') === 0),
    'merge alone raises no confirm question');
  assert.ok(BB.validHole(p, 't6E') && BB.validHole(p, 't54E'), 'both segments are lattice-valid: gate must disambiguate');
  console.log('segment-fixture: passed');
}
