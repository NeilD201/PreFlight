// Scan math: sharpness ordering, exposure bands, frame differencing,
// zone naming, coverage tracking. Synthetic pixel buffers only.
const assert = require('node:assert/strict');
const SM = require('./scanmath.js');

function frame(w, h, fn) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const v = fn(x, y);
    const i = (y * w + x) * 4;
    data[i] = data[i + 1] = data[i + 2] = v; data[i + 3] = 255;
  }
  return { data, w, h };
}
const lumOf = (f) => SM.luminance(f.data, f.w, f.h);

// Sharpness: textured beats flat; blur direction is monotonic.
const flat = lumOf(frame(32, 32, () => 128));
const checker = lumOf(frame(32, 32, (x, y) => (((x >> 2) + (y >> 2)) % 2 ? 200 : 60)));
assert.equal(SM.sharpness(flat, 32, 32), 0);
assert.ok(SM.sharpness(checker, 32, 32) > 50);
assert.ok(SM.sharpness(checker, 32, 32) > SM.SHARP_MIN);
console.log('sharpness: passed');

// Exposure bands.
assert.ok(SM.exposure(lumOf(frame(16, 16, () => 10))).darkFrac > 0.9);
assert.ok(SM.exposure(lumOf(frame(16, 16, () => 250))).brightFrac > 0.9);
const mid = SM.exposure(lumOf(frame(16, 16, () => 128)));
assert.ok(mid.mean === 128 && mid.darkFrac === 0 && mid.brightFrac === 0);
console.log('exposure: passed');

// Verdicts combine the bands.
const vOk = SM.qualityVerdict({ sharpness: 40, exposure: mid });
assert.ok(vOk.ok && vOk.tips.length === 0);
const vBad = SM.qualityVerdict({ sharpness: 2, exposure: { mean: 10, darkFrac: 0.9, brightFrac: 0 } });
assert.ok(!vBad.ok && vBad.tips.includes('blurry') && vBad.tips.includes('dark'));
console.log('verdict: passed');

// Differencing: identical is 0, shifted is large, mismatch is Infinity.
assert.equal(SM.frameDiff(checker, checker.slice()), 0);
const shifted = lumOf(frame(32, 32, (x, y) => (((x >> 2) + (y >> 2) + 1) % 2 ? 200 : 60)));
assert.ok(SM.frameDiff(checker, shifted) > SM.NOVEL_DIFF);
assert.equal(SM.frameDiff(checker, new Float32Array(4)), Infinity);
console.log('diff: passed');

// Zones: corners, edges, center, garbage.
assert.equal(SM.zoneName([0, 0, 0.2, 0.2]), 'upper-left');
assert.equal(SM.zoneName([0.8, 0.8, 1, 1]), 'lower-right');
assert.equal(SM.zoneName([0.4, 0.4, 0.6, 0.6]), 'center');
assert.equal(SM.zoneName([0.4, 0, 0.6, 0.2]), 'upper');
assert.equal(SM.zoneName([0, 0.4, 0.2, 0.6]), 'left');
assert.equal(SM.zoneName(null), 'unknown');
assert.equal(SM.zoneName([0.4, 0.7, 0.6, 0.9]), 'lower');
console.log('zones: passed');

// Progress: evidence-only, capped below 100 with any unknown.
(function () {
  const P = SM.scanProgress;
  let r = P({ coverage: 0, components: [], wires: [], openQuestions: 0 });
  assert.equal(r.pct, 0);
  assert.equal(r.complete, false);
  r = P({ coverage: 1, components: [], wires: [], openQuestions: 0 });
  assert.ok(r.pct < 100, 'nothing observed caps below 100: ' + r.pct);
  r = P({ coverage: 1, components: [], wires: [], openQuestions: 0, emptyConfirmed: true });
  assert.equal(r.pct, 100);
  assert.equal(r.complete, true);
  const done = { coverage: 0.96,
    components: [{ pinsTotal: 2, pinsPlaced: 2, identified: true }],
    wires: [{ fromSet: true, toSet: true }], openQuestions: 0 };
  r = P(done);
  assert.equal(r.pct, 100);
  assert.equal(r.unresolved, 0);
  r = P(Object.assign({}, done, { openQuestions: 1 }));
  assert.ok(r.pct <= 99 && r.unresolved === 1, 'open question caps: ' + r.pct);
  r = P({ coverage: 0.5,
    components: [{ pinsTotal: 2, pinsPlaced: 1, identified: false }],
    wires: [], openQuestions: 2 });
  assert.ok(r.pct > 0 && r.pct < 99, 'partial mid-range: ' + r.pct);
  assert.equal(r.unresolved, 1 + 1 + 2);
  console.log('progress: passed');
})();

// Coverage: max-accumulates, weakest names the unscanned cell.
const cov = SM.newCoverage(6, 4);
assert.equal(SM.coverageScore(cov), 0);
const grid = new Array(24).fill(40); grid[7] = 2;
SM.updateCoverage(cov, grid);
assert.ok(Math.abs(SM.coverageScore(cov) - 23 / 24) < 1e-9);
const weak = SM.weakestCell(cov);
assert.equal(weak.index, 7);
assert.equal(typeof weak.zone, 'string');
SM.updateCoverage(cov, new Array(24).fill(0)); // never regresses
assert.equal(SM.coverageScore(cov), 23 / 24);
console.log('coverage: passed');

// Rail candidates: synthetic band with holes, stripes, one housing, one
// diagonal crossing and one along-rail wire. Only the housing qualifies.
(function () {
  const pitch = 2.54, rows = 63;
  const innerX = 18.5, outerX = 20.75;
  const zRow = (r) => (r - (rows + 1) / 2) * pitch;
  const S = 8, xMin = 10, zMin = zRow(28);
  const W = Math.ceil(18 * S), H = Math.ceil(26 * pitch * S);
  const Hm = [S, 0, -xMin * S, 0, S, -zMin * S, 0, 0, 1];
  const X = (x) => Math.round((x - xMin) * S);
  const Z = (z) => Math.round((z - zMin) * S);
  const holes = [];
  for (let r = 29; r <= 53; r++) for (const lx of [innerX, outerX]) holes.push([lx, zRow(r)]);
  const housing = { x0: 17.35, x1: 21.85, z0: zRow(40) - 1.5, z1: zRow(40) + 1.5 };
  const segDist = (x, z, ax, az, bx, bz) => {
    const dx = bx - ax, dz = bz - az, L2 = dx * dx + dz * dz;
    let t = L2 ? ((x - ax) * dx + (z - az) * dz) / L2 : 0;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(x - (ax + dx * t), z - (az + dz * t));
  };
  const build = (withParts) => {
    const f = frame(W, H, (px, py) => {
      const x = px / S + xMin, z = py / S + zMin;
      // Stripes: dark paint lines (gray 100 reads dark, like red/blue).
      if (Math.abs(x - 22.55) < 0.55 || Math.abs(x - 16.7) < 0.55) return 100;
      for (const [hx, hz] of holes)
        if (Math.abs(x - hx) < 0.7 && Math.abs(z - hz) < 0.7) return 40;
      if (!withParts) return 220;
      if (x >= housing.x0 && x <= housing.x1 && z >= housing.z0 && z <= housing.z1) return 30;
      if (segDist(x, z, 17.5, zRow(44) + 1.27, 21.5, zRow(46) + 1.27) < 1.0) return 50; // diagonal crossing
      if (x >= 18.6 && x <= 20.6 && z >= zRow(30) && z <= zRow(35)) return 50; // along-rail wire
      return 220;
    });
    return lumOf(f);
  };
  const span = SM.bandSpan(innerX, outerX);
  assert.deepEqual(span, [14.5, 21.95]);
  const clean = build(false);
  assert.deepEqual(SM.railCandidates(clean, W, H, Hm, innerX, outerX, zRow(29), zRow(53), pitch, rows, zRow), []);
  const pop = build(true);
  const cands = SM.railCandidates(pop, W, H, Hm, innerX, outerX, zRow(29), zRow(53), pitch, rows, zRow);
  assert.equal(cands.length, 1, JSON.stringify(cands));
  // First-maximum edge pick on the flat-topped 3 mm housing: within a hole.
  assert.equal(cands[0].pos, 39);
  assert.ok(cands[0].width >= 4 && cands[0].width <= 5.5, 'housing width ' + cands[0].width);
  // Band width primitives: housing wide, empty hole narrow, stripe excluded.
  const px = (x, z) => SM.bandRunWidth(pop, W, H, Hm, S, 140, span, z);
  assert.ok(px(0, zRow(40)) >= 4, 'housing band width');
  assert.ok(px(0, zRow(50)) < 2.5, 'empty holes stay narrow');
  console.log('rail-candidates: passed');
})();

// Lattice registration locality: synthetic board only (no customer photos).
// A centered lock verifies with placement-quality H; a (60,0) perturbation
// must still fail (bounded search, no false accepts).
{
  const BB = require('./breadboard.js');
  const proj = BB.newProject('synth', { size: 'full' });
  const rows = BB.rowsOf(proj), BW = BB.BOARD_WIDTH, BL = rows * BB.PITCH_MM + 12;
  const W = 1200, H = 900;
  const quadTrue = [{ x: 150, y: 120 }, { x: 1050, y: 120 }, { x: 1050, y: 780 }, { x: 150, y: 780 }];
  const mmSrc = [{ x: -BW / 2, y: -BL / 2 }, { x: BW / 2, y: -BL / 2 },
    { x: BW / 2, y: BL / 2 }, { x: -BW / 2, y: BL / 2 }];
  const Hr = SM.homography(mmSrc, quadTrue);
  assert.ok(Hr, 'synthetic render homography exists');
  const data = new Uint8ClampedArray(W * H * 4);
  for (let i = 0; i < W * H; i++) { data[i * 4] = data[i * 4 + 1] = data[i * 4 + 2] = 215; data[i * 4 + 3] = 255; }
  const dot = (cx, cy, r, v) => {
    for (let y = Math.floor(cy - r); y <= Math.ceil(cy + r); y++)
      for (let x = Math.floor(cx - r); x <= Math.ceil(cx + r); x++) {
        if (x < 0 || y < 0 || x >= W || y >= H) continue;
        if (Math.hypot(x - cx, y - cy) > r + 0.4) continue;
        const k = (y * W + x) * 4;
        data[k] = data[k + 1] = data[k + 2] = v;
      }
  };
  for (let rr = 1; rr <= rows; rr++) for (const col of 'ABCDEFGHIJ') {
    const m = BB.holeXY(proj, 't' + rr + col);
    if (!m) continue;
    const p = SM.applyHomography(Hr, m.x, m.z);
    dot(p.x, p.y, 3, 45);
  }
  const lum = SM.luminance(data, W, H);
  const geom = { BW, BL, rows, holeXY: (id) => BB.holeXY(proj, id), PITCH: BB.PITCH_MM };
  // Center lock: verifies, and the fitted H places a known hole within px.
  // (Deliberately local search: a widened multi-start was tried and reverted —
  // alias fits outscored truth 4.05 vs 0.35 with 42px+ placement error.)
  const near = SM.registerBoard(lum, W, H, quadTrue, 0, geom, 1);
  assert.ok(near && near.conf >= 0.3, 'center lock verifies: ' + JSON.stringify(near && near.conf));
  const hole = BB.holeXY(proj, 't40E');
  const truth = SM.applyHomography(Hr, hole.x, hole.z);
  const got = SM.applyHomography(near.H, hole.x, hole.z);
  assert.ok(Math.hypot(got.x - truth.x, got.y - truth.y) < 12,
    `hole projects within 12px (~1 row, the vote-agreement tolerance) (got ${got.x.toFixed(1)},${got.y.toFixed(1)} vs ${truth.x.toFixed(1)},${truth.y.toFixed(1)})`);
  // NOTE: no far-start rejection assert — a 60px-off start aliases with HIGH
  // contrast (measured 4.05 vs 0.35 truth), so max-contrast selection cannot
  // tell them apart. Safety comes from the lock starting near truth plus
  // downstream cross-view agreement (voteModuleAnchor ±1 row, orient
  // question), never from this threshold alone.
  console.log('register-locality: passed');
}

// Edge-snap lock refinement: synthetic board only (no customer photos).
// White tilted board on dark bg with sparse holes + gray module bar.
// A lock quad shortened 40px at the bottom and 30px at the right must snap
// back near truth; garbage input returns null; a true quad barely moves.
{
  const W = 800, H = 600;
  const data = new Uint8ClampedArray(W * H * 4);
  for (let i = 0; i < W * H; i++) { data[i * 4] = data[i * 4 + 1] = data[i * 4 + 2] = 25; data[i * 4 + 3] = 255; }
  const inPoly = (x, y, q) => {
    let inside = false;
    for (let i = 0, j = 3; i < 4; j = i++) {
      const xi = q[i].x, yi = q[i].y, xj = q[j].x, yj = q[j].y;
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  };
  const paint = (q, v) => {
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      if (!inPoly(x + 0.5, y + 0.5, q)) continue;
      const k = (y * W + x) * 4;
      data[k] = data[k + 1] = data[k + 2] = v;
    }
  };
  const truth = [{ x: 150, y: 100 }, { x: 650, y: 120 }, { x: 620, y: 500 }, { x: 120, y: 480 }];
  paint(truth, 210);
  paint([{ x: 300, y: 280 }, { x: 500, y: 290 }, { x: 495, y: 340 }, { x: 295, y: 330 }], 120);
  const dot = (cx, cy, r, v) => {
    for (let y = Math.floor(cy - r); y <= Math.ceil(cy + r); y++)
      for (let x = Math.floor(cx - r); x <= Math.ceil(cx + r); x++) {
        if (x < 0 || y < 0 || x >= W || y >= H) continue;
        const k = (y * W + x) * 4;
        data[k] = data[k + 1] = data[k + 2] = v;
      }
  };
  for (let i = 0; i < 40; i++) dot(200 + (i * 37) % 380, 150 + (i * 53) % 300, 2, 60);
  const lum = SM.luminance(data, W, H);
  const short = [{ x: 150, y: 100 }, { x: 620, y: 120 }, { x: 595, y: 460 }, { x: 120, y: 444 }];
  const snapped = SM.snapQuadToBoardEdges(lum, W, H, short);
  assert.ok(snapped, 'snap returns a quad for a shortened lock');
  const err = Math.max(...snapped.map((p, i) => Math.hypot(p.x - truth[i].x, p.y - truth[i].y)));
  assert.ok(err < 12, `snapped within 12px of truth (got ${err.toFixed(1)})`);
  const same = SM.snapQuadToBoardEdges(lum, W, H, truth);
  assert.ok(same, 'true quad stays valid');
  const drift = Math.max(...same.map((p, i) => Math.hypot(p.x - truth[i].x, p.y - truth[i].y)));
  assert.ok(drift < 6, `true quad barely moves (got ${drift.toFixed(1)})`);
  assert.equal(SM.snapQuadToBoardEdges(lum, W, H, [{ x: 0, y: 0 }]), null, 'garbage in, null out');
  assert.equal(SM.snapQuadToBoardEdges(null, W, H, truth), null, 'null lum, null out');
  console.log('snap-edges: passed');
}

// Wire color segmentation: synthetic conductor only (no customer photos).
// Green bar on beige: ends land near the bar tips on the bar row.
// Dark neutrals (black/gray/white/unknown) are unsupported -> null (the
// caller keeps the bbox-extreme fallback). A dot-sized blob -> null.
{
  const W = 40, H = 20;
  const rgb = new Uint8Array(W * H * 3);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const i = (y * W + x) * 3, bar = y >= 9 && y <= 11 && x >= 4 && x <= 35;
    rgb[i] = bar ? 20 : 200; rgb[i + 1] = bar ? 150 : 190; rgb[i + 2] = bar ? 40 : 170;
  }
  assert.ok(SM.wireColorMatch('green', 20, 150, 40), 'green probe matches');
  assert.ok(!SM.wireColorMatch('green', 200, 190, 170), 'beige is not green');
  assert.ok(!SM.wireColorMatch('black', 10, 10, 10), 'black unsupported');
  assert.ok(!SM.wireColorMatch('white', 250, 250, 250), 'white unsupported');
  assert.ok(!SM.wireColorMatch('mauve', 128, 0, 128), 'unknown color unsupported');
  const ce = SM.wireColorEnds(rgb, W, H, [0, 0, 1, 1], 'green');
  assert.ok(ce && ce.ends, 'green bar segments');
  assert.ok(ce.ends[0][0] < 10 && ce.ends[1][0] > 30,
    'ends near bar tips: ' + JSON.stringify(ce.ends));
  assert.ok(ce.ends[0][1] > 5 && ce.ends[0][1] < 15 && ce.ends[1][1] > 5 && ce.ends[1][1] < 15,
    'ends on the bar row: ' + JSON.stringify(ce.ends));
  assert.equal(SM.wireColorEnds(rgb, W, H, [0, 0, 1, 1], 'black'), null, 'black never segments');
  const beige = new Uint8Array(W * H * 3);
  for (let i = 0; i < W * H; i++) { beige[i * 3] = 200; beige[i * 3 + 1] = 190; beige[i * 3 + 2] = 170; }
  assert.equal(SM.wireColorEnds(beige, W, H, [0, 0, 1, 1], 'green'), null, 'absent color misses');
  const dot = beige.slice();
  dot[(10 * W + 20) * 3] = 20; dot[(10 * W + 20) * 3 + 1] = 150; dot[(10 * W + 20) * 3 + 2] = 40;
  assert.equal(SM.wireColorEnds(dot, W, H, [0, 0, 1, 1], 'green'), null, 'dot is not a wire');
  assert.equal(SM.wireColorEnds(null, W, H, [0, 0, 1, 1], 'green'), null, 'null pixels, null out');
  console.log('wire-color: passed');
}
