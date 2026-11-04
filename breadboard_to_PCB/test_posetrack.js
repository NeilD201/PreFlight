// Pose tracking: exact homography recovery, quad detection on rasterized
// quads, and a known-viewpoint-change tracking sequence through the full
// raster -> detect -> homography -> decompose -> filter chain.
const assert = require('node:assert/strict');
const SM = require('./scanmath.js');

const DEG = 180 / Math.PI;

// --- exact homography recovery from a known transform ---
(function () {
  const src = [{ x: -26, y: -60 }, { x: 26, y: -60 }, { x: 26, y: 60 }, { x: -26, y: 60 }];
  // Known H: rotate 15 deg, scale 1.7, translate, mild perspective.
  const t = 15 / DEG, s = 1.7, c = Math.cos(t), si = Math.sin(t);
  const H = [s * c, -s * si, 80, s * si, s * c, 60, 0.0011, -0.0007, 1];
  const dst = src.map((p) => SM.applyHomography(H, p.x, p.y));
  const rec = SM.homography(src, dst);
  assert.ok(rec, 'homography solves');
  for (let i = 0; i < 4; i++) {
    const q = SM.applyHomography(rec, src[i].x, src[i].y);
    assert.ok(Math.hypot(q.x - dst[i].x, q.y - dst[i].y) < 1e-6, 'exact remap ' + i);
  }
  assert.equal(SM.solveLinear([[2, 1], [1, 3]], [5, 6]).map((v) => Math.round(v * 1e9) / 1e9).join(','), '1.8,1.4');
  console.log('homography: passed');
})();

// --- quad detection on a rasterized parallelogram ---
function rasterize(w, h, poly, fg, bg) {
  const lum = new Float32Array(w * h).fill(bg);
  const xs = poly.map((p) => p.x), ys = poly.map((p) => p.y);
  const x0 = Math.max(0, Math.floor(Math.min(...xs))), x1 = Math.min(w - 1, Math.ceil(Math.max(...xs)));
  const y0 = Math.max(0, Math.floor(Math.min(...ys))), y1 = Math.min(h - 1, Math.ceil(Math.max(...ys)));
  const inside = (x, y) => {
    let c = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const a = poly[i], b = poly[j];
      if ((a.y > y) !== (b.y > y) && x < ((b.x - a.x) * (y - a.y)) / (b.y - a.y) + a.x) c = !c;
    }
    return c;
  };
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++)
    if (inside(x + 0.5, y + 0.5)) lum[y * w + x] = fg;
  return lum;
}

(function () {
  const W = 160, H = 120;
  const truth = [{ x: 30, y: 25 }, { x: 130, y: 35 }, { x: 120, y: 95 }, { x: 20, y: 85 }];
  const lum = rasterize(W, H, truth, 230, 40);
  const found = SM.findBoardQuad(lum, W, H);
  assert.ok(found, 'quad detected');
  const ordered = SM.orderCorners(truth);
  for (let i = 0; i < 4; i++)
    assert.ok(Math.hypot(found.corners[i].x - ordered[i].x, found.corners[i].y - ordered[i].y) < 3,
      'corner ' + i + ' near truth');
  // Ordering starts top-left-most, clockwise.
  assert.deepEqual(SM.orderCorners([{ x: 1, y: 1 }, { x: 9, y: 1 }, { x: 9, y: 5 }, { x: 1, y: 5 }]),
    [{ x: 1, y: 1 }, { x: 9, y: 1 }, { x: 9, y: 5 }, { x: 1, y: 5 }]);
  // Honest rejection: blank, tiny, non-convex.
  assert.equal(SM.estimateBoardPose(new Float32Array(W * H).fill(40), W, H, 120, 60).length, 0);
  assert.equal(SM.estimateBoardPose(rasterize(W, H,
    [{ x: 70, y: 50 }, { x: 90, y: 50 }, { x: 90, y: 70 }, { x: 70, y: 70 }], 230, 40), W, H, 120, 60).length, 0);
  assert.equal(SM.quadQuality([{ x: 0, y: 0 }, { x: 9, y: 0 }, { x: 9, y: 9 }], W, H).ok, false);
  assert.equal(SM.detectBoardFraming(lum, W, H).state, 'full');
  assert.equal(SM.detectBoardFraming(new Float32Array(W * H).fill(40), W, H).state, 'none');
  const cut = rasterize(W, H, [{ x: -20, y: 25 }, { x: 130, y: 35 }, { x: 120, y: 95 }, { x: -20, y: 85 }], 230, 40);
  assert.equal(SM.detectBoardFraming(cut, W, H).state, 'partial');
  console.log('quad-detect: passed');
})();

// --- known-viewpoint tracking sequence ---
(function () {
  const W = 160, H = 120, F = W * 1.1, CX = W / 2, CY = H / 2;
  const BW = 120, BL = 60, TZ = 200; // board section, distance (px-consistent units)
  const Ry = (t) => [[Math.cos(t), 0, Math.sin(t)], [0, 1, 0], [-Math.sin(t), 0, Math.cos(t)]];
  const Rx = (t) => [[1, 0, 0], [0, Math.cos(t), -Math.sin(t)], [0, Math.sin(t), Math.cos(t)]];
  const mmult = (A, B) => A.map((row) => B[0].map((_, j) => row[0] * B[0][j] + row[1] * B[1][j] + row[2] * B[2][j]));
  const project = (R, P) => {
    const X = R[0][0] * P[0] + R[0][1] * P[1] + R[0][2] * P[2];
    const Y = R[1][0] * P[0] + R[1][1] * P[1] + R[1][2] * P[2];
    const Z = R[2][0] * P[0] + R[2][1] * P[1] + R[2][2] * P[2] + TZ;
    return { x: (F * X) / Z + CX, y: (F * Y) / Z + CY };
  };
  const boardCorners = [[-BW / 2, -BL / 2, 0], [BW / 2, -BL / 2, 0], [BW / 2, BL / 2, 0], [-BW / 2, BL / 2, 0]];
  const frame = (R) => {
    const poly = boardCorners.map((P) => project(R, P));
    return { lum: rasterize(W, H, poly, 230, 40), poly };
  };

  // Yaw sweep 0 -> 20 deg, no smoothing: output must equal relative truth.
  const filt = SM.makeTrackFilter({ alpha: 1, maxJump: 10 });
  let prevRy = -1e9;
  for (let k = 0; k <= 4; k++) {
    const deg = k * 5;
    const est = SM.estimateBoardPose(frame(Ry(deg / DEG)).lum, W, H, BW, BL);
    assert.ok(est.length, 'pose at yaw ' + deg + ' got ' + est.length);
    const st = filt.update(est);
    assert.ok(st.state === 'lock' || st.state === 'track', st.state);
    const ry = st.euler.ry * DEG;
    assert.ok(ry >= prevRy - 1e-9, `yaw monotonic at ${deg}: ${ry}`);
    prevRy = ry;
    if (k === 4) {
      assert.ok(Math.abs(ry - 20) < 4, `final yaw ~20, got ${ry}`);
      assert.ok(Math.abs(st.euler.rx * DEG) < 4 && Math.abs(st.euler.rz * DEG) < 4, 'off-axis small');
    }
  }
  // Pitch 12 deg about X: three.js mapping negates camera-X rotation.
  const filt2 = SM.makeTrackFilter({ alpha: 1, maxJump: 10 });
  filt2.update(SM.estimateBoardPose(frame(Ry(0)).lum, W, H, BW, BL));
  const st2 = filt2.update(SM.estimateBoardPose(frame(Rx(12 / DEG)).lum, W, H, BW, BL));
  assert.equal(st2.state, 'track');
  assert.ok(Math.abs(st2.euler.rx * DEG - -12) < 4, `pitch maps to ${st2.euler.rx * DEG}`);
  // Combined rotation recovers both axes.
  const filt3 = SM.makeTrackFilter({ alpha: 1, maxJump: 10 });
  filt3.update(SM.estimateBoardPose(frame(Ry(0)).lum, W, H, BW, BL));
  const st3 = filt3.update(SM.estimateBoardPose(frame(mmult(Ry(15 / DEG), Rx(10 / DEG))).lum, W, H, BW, BL));
  assert.ok(Math.abs(st3.euler.ry * DEG - 15) < 5, `yaw ${st3.euler.ry * DEG}`);
  assert.ok(Math.abs(st3.euler.rx * DEG - -10) < 5, `pitch ${st3.euler.rx * DEG}`);
  // Translation direction: board shifted right in frame -> t.x > 0.
  const shift = (dx) => boardCorners.map((P) => {
    const q = project(Ry(0), P);
    return { x: q.x + dx, y: q.y };
  });
  const estT = SM.estimateBoardPose(rasterize(W, H, shift(25), 230, 40), W, H, BW, BL);
  assert.ok(estT.length && estT[0].t[0] > 0, 'rightward shift gives +t.x');
  console.log('tracking-sequence: passed');
})();

// --- filter honesty: loss freezes, jumps rejected ---
(function () {
  const filt = SM.makeTrackFilter({ alpha: 1, maxJump: 0.6 });
  const I = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  assert.equal(filt.update([{ R: I, skew: 0 }]).state, 'lock');
  assert.equal(filt.update([]).state, 'lost');
  assert.deepEqual(filt.update(null).euler, { rx: 0, ry: 0, rz: 0 });
  const flip = [[-1, 0, 0], [0, 1, 0], [0, 0, -1]]; // 180 deg yaw jump
  assert.equal(filt.update([{ R: flip, skew: 0 }]).state, 'jump');
  assert.deepEqual(filt.update(null).euler, { rx: 0, ry: 0, rz: 0 });
  console.log('filter-honesty: passed');
})();

// --- structural board score: groove + silhouette discriminate ---
(function () {
  const W = 320, H = 200;
  const truth = [{ x: 40, y: 50 }, { x: 280, y: 60 }, { x: 270, y: 150 }, { x: 30, y: 140 }];
  const inside = (x, y) => {
    let c = false;
    for (let i = 0, j = 3; i < 4; j = i++) {
      const a = truth[i], b = truth[j];
      if ((a.y > y) !== (b.y > y) && x < ((b.x - a.x) * (y - a.y)) / (b.y - a.y) + a.x) c = !c;
    }
    return c;
  };
  let seed = 7;
  const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  const lum = new Float32Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const inn = inside(x + 0.5, y + 0.5);
    lum[y * W + x] = inn ? 225 + rnd() * 15 : 35 + rnd() * 10;
  }
  for (let t = 0; t <= 1.001; t += 1 / 240) { // center groove
    const cx = Math.round(40 + t * 235), cy = Math.round(95 + t * 5);
    for (let d = -1; d <= 1; d++)
      if (inside(cx, cy + d)) lum[(cy + d) * W + cx] = 90;
  }
  for (let gy = 58; gy < 145; gy += 7) for (let gx = 45; gx < 275; gx += 7) {
    if (inside(gx, gy) && Math.abs(gy - (95 + (gx - 40) / 235 * 5)) > 4) lum[gy * W + gx] = 150;
  }
  const paper = new Float32Array(W * H); // same outline, no groove/holes
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const inn = inside(x + 0.5, y + 0.5);
    paper[y * W + x] = inn ? 225 + rnd() * 15 : 35 + rnd() * 10;
  }
  const blank = new Float32Array(W * H).fill(40);
  const sBoard = SM.boardScore(lum, W, H, truth).score;
  const sPaper = SM.boardScore(paper, W, H, truth).score;
  const sBlank = SM.boardScore(blank, W, H, truth).score;
  assert.ok(sBoard > 40 && sBoard > sPaper + 20, `board ${sBoard} beats paper ${sPaper}`);
  assert.ok(sBlank < 5, `blank ~0 (got ${sBlank})`);
  // Lock mechanics: line completion + refine + structural proof find the board.
  const L = SM.estimateBoardPose(lum, W, H, 52, 165, { verify: { lumHi: lum, wHi: W, hHi: H } });
  assert.equal(L.length, 1, 'exactly one lock hypothesis');
  assert.ok(L[0].contrast >= 60, `lock score above threshold (${L[0].contrast})`);
  const o = SM.orderCorners(truth);
  let e2 = 0;
  for (let i = 0; i < 4; i++) e2 += Math.hypot(L[0].corners[i].x - o[i].x, L[0].corners[i].y - o[i].y);
  assert.ok(e2 / 4 < 12, `lock corners near truth (${(e2 / 4).toFixed(1)}px)`);
  // Honest rejection: paper and blank never lock.
  assert.equal(SM.estimateBoardPose(paper, W, H, 52, 165,
    { verify: { lumHi: paper, wHi: W, hHi: H } }).length, 0, 'paper rejected');
  assert.equal(SM.estimateBoardPose(blank, W, H, 52, 165,
    { verify: { lumHi: blank, wHi: W, hHi: H } }).length, 0, 'blank rejected');
  // refineQuad is a bounded hill-climb: improves (or holds) score, stays in bounds.
  const off = truth.map((p) => ({ x: p.x + 12, y: p.y - 8 }));
  const start = SM.boardScore(lum, W, H, off).score;
  const rq = SM.refineQuad(lum, W, H, off);
  assert.ok(rq.score >= start, `refine improves ${start} -> ${rq.score}`);
  for (let i = 0; i < 4; i++)
    assert.ok(Math.hypot(rq.corners[i].x - off[i].x, rq.corners[i].y - off[i].y) <= 24 + 1e-9, 'stays bounded');
  console.log('structural-lock: passed');
})();

// --- populated board: a dark module splits the bright mass, lock survives ---
(function () {
  const W = 320, H = 200;
  const truth = [{ x: 40, y: 50 }, { x: 280, y: 60 }, { x: 270, y: 150 }, { x: 30, y: 140 }];
  const inside = (x, y) => {
    let c = false;
    for (let i = 0, j = 3; i < 4; j = i++) {
      const a = truth[i], b = truth[j];
      if ((a.y > y) !== (b.y > y) && x < ((b.x - a.x) * (y - a.y)) / (b.y - a.y) + a.x) c = !c;
    }
    return c;
  };
  let seed = 21;
  const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  const lum = new Float32Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const inn = inside(x + 0.5, y + 0.5);
    lum[y * W + x] = inn ? 225 + rnd() * 15 : 35 + rnd() * 10;
  }
  for (let t = 0; t <= 1.001; t += 1 / 240) {
    const cx = Math.round(40 + t * 235), cy = Math.round(95 + t * 5);
    for (let d = -1; d <= 1; d++)
      if (inside(cx, cy + d)) lum[(cy + d) * W + cx] = 90;
  }
  for (let y = 48; y < 152; y++) for (let x = 140; x < 200; x++) // dark module, full height
    if (inside(x, y)) lum[y * W + x] = 30 + rnd() * 10;
  // The module fragments the bright mass: largest blob is a fraction now.
  const blob = SM.largestBlob(lum, W, H, SM.otsuThreshold(lum));
  assert.ok(blob.area < W * H * 0.2, `fragmented (${blob.area})`);
  const sPop = SM.boardScore(lum, W, H, truth).score;
  assert.ok(sPop >= 60, `populated board still scores (${sPop.toFixed(1)})`);
  const L = SM.estimateBoardPose(lum, W, H, 52, 165, { verify: { lumHi: lum, wHi: W, hHi: H } });
  assert.ok(L.length >= 1, 'populated board locks');
  const o = SM.orderCorners(truth);
  let e2 = 0;
  for (let i = 0; i < 4; i++) e2 += Math.hypot(L[0].corners[i].x - o[i].x, L[0].corners[i].y - o[i].y);
  assert.ok(e2 / 4 < 14, `lock corners near truth (${(e2 / 4).toFixed(1)}px)`);
  console.log('populated-lock: passed');
})();
