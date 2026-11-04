// Grid registration: holeAt inversion, pitch/origin recovery on synthetic
// lattices, registration contrast, warp correctness.
const assert = require('node:assert/strict');
const SM = require('./scanmath.js');
const BB = require('./breadboard.js');

// holeAt inverts holeXY; off-lattice points report big misses.
(function () {
  const p = BB.newProject('g', {});
  for (const id of ['t1A', 't63J', 't30E', 't30F', 't12C', 'rL+1', 'rL-63', 'rR+30', 'rR-15']) {
    const pt = BB.holeXY(p, id);
    const back = BB.holeAt(p, pt.x, pt.z);
    assert.equal(back.hole, id);
    assert.ok(back.dx < 1e-9 && back.dz < 1e-9);
  }
  const groove = BB.holeAt(p, 0, BB.holeXY(p, 't10A').z);
  assert.ok(!groove || Math.hypot(groove.dx, groove.dz) > 5, 'groove far from any hole');
  assert.equal(BB.holeAt(p, 0, 500), null);
  assert.equal(BB.holeAt(p, NaN, 0), null);
  console.log('holeAt: passed');
})();

// Synthetic lattice: pitch + origin recovery.
function lattice(rw, rh, ox, oy, px, py, cols, rows) {
  const rect = new Float32Array(rw * rh).fill(220);
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const cx = ox + c * px, cy = oy + r * py;
    for (let y = Math.floor(cy - 1); y <= Math.ceil(cy + 1); y++)
      for (let x = Math.floor(cx - 1); x <= Math.ceil(cx + 1); x++)
        if (x >= 0 && y >= 0 && x < rw && y < rh && Math.hypot(x - cx, y - cy) < 1.2)
          rect[y * rw + x] = 60;
  }
  return rect;
}

(function () {
  const RW = 100, RH = 170;
  const rect = lattice(RW, RH, 3, 5, 8, 8, 10, 20);
  const px = SM.profileDarkness(rect, RW, RH, 'x');
  const py = SM.profileDarkness(rect, RW, RH, 'y');
  assert.ok(Math.abs(SM.autocorrPitch(px, 4, 16).pitch - 8) <= 1, 'x pitch');
  assert.ok(Math.abs(SM.autocorrPitch(py, 4, 16).pitch - 8) <= 1, 'y pitch');
  assert.ok(Math.abs(SM.fitCombOrigin(px, 8, 10).origin - 3) <= 1.5, 'x origin');
  assert.ok(Math.abs(SM.fitCombOrigin(py, 8, 20).origin - 5) <= 1.5, 'y origin');
  console.log('pitch-origin: passed');
})();

// Registration contrast: aligned lattice beats half-pitch-shifted.
(function () {
  const RW = 100, RH = 170;
  const rect = lattice(RW, RH, 3, 5, 8, 8, 10, 20);
  const holes = [], shifted = [];
  for (let r = 0; r < 20; r++) for (let c = 0; c < 10; c++) {
    holes.push([3 + c * 8, 5 + r * 8, 8, 8]);
    shifted.push([3 + c * 8 + 4, 5 + r * 8 + 4, 8, 8]);
  }
  const good = SM.registrationContrast(rect, RW, RH, holes);
  const bad = SM.registrationContrast(rect, RW, RH, shifted);
  assert.ok(good > 0.4, 'aligned contrast ' + good);
  assert.ok(good > bad + 0.3, `aligned ${good} beats shifted ${bad}`);
  console.log('contrast: passed');
})();

// Warp: identity-region rectify reproduces the crop; inverse is exact.
(function () {
  const W = 60, H = 40;
  const lum = new Float32Array(W * H).fill(200);
  lum[20 * W + 30] = 20; // single dark dot inside the quad
  const quad = [{ x: 10, y: 8 }, { x: 50, y: 8 }, { x: 50, y: 32 }, { x: 10, y: 32 }];
  const out = SM.rectifyQuad(lum, W, H, quad, 40, 24);
  assert.ok(out && out.length === 40 * 24);
  let mi = 0;
  for (let i = 1; i < out.length; i++) if (out[i] < out[mi]) mi = i;
  const mx = mi % 40, my = (mi / 40) | 0;
  assert.ok(Math.hypot(mx - 20, my - 12) < 1.5, `dot lands at ${mx},${my}`);
  assert.ok(Math.abs(out[mi] - 155) < 1, 'bilinear quarter-weight, value ' + out[mi]);
  assert.ok(out[0] > 190 && out[out.length - 1] > 190, 'flat field stays flat');
  const Hm = [1.7 * 0.866, -1.7 * 0.5, 80, 1.7 * 0.5, 1.7 * 0.866, 60, 0.0011, -0.0007, 1];
  const inv = SM.invertHomography(Hm);
  assert.ok(inv, 'invertible');
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) {
    let s = 0;
    for (let k = 0; k < 3; k++) s += Hm[r * 3 + k] * inv[k * 3 + c];
    assert.ok(Math.abs(s - (r === c ? 1 : 0)) < 1e-6, `I[${r}][${c}]=${s}`);
  }
  console.log('warp: passed');
})();
