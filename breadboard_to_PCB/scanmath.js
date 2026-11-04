/* PreFlight LiveScan scan math: pure frame-quality functions shared by the
   browser scanner and node tests. Operates on raw pixel data, no DOM. */
(function (root) {
'use strict';

// Starting thresholds, calibrated against the LiveScan QC scale where a
// Laplacian mean below ~12 reads as visibly blurry on 160px frames.
const SHARP_MIN = 12;
const EXPOSE_LO = 55, EXPOSE_HI = 205;
const DARK_FRAC_MAX = 0.35, BRIGHT_FRAC_MAX = 0.25;
const STABLE_DIFF = 4;   // mean abs luma change between samples: held still
const NOVEL_DIFF = 10;   // mean abs change vs last capture: a new view

function luminance(data, w, h) {
  const lum = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++)
    lum[i] = 0.299 * data[i * 4] + 0.587 * data[i * 4 + 1] + 0.114 * data[i * 4 + 2];
  return lum;
}

function sharpness(lum, w, h) {
  if (w < 3 || h < 3) return 0;
  let lap = 0;
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
    const i = y * w + x;
    lap += Math.abs(4 * lum[i] - lum[i - 1] - lum[i + 1] - lum[i - w] - lum[i + w]);
  }
  return lap / ((w - 2) * (h - 2));
}

function sharpnessGrid(lum, w, h, cols, rows) {
  const cells = new Array(cols * rows).fill(0);
  const counts = new Array(cols * rows).fill(0);
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
    const i = y * w + x;
    const v = Math.abs(4 * lum[i] - lum[i - 1] - lum[i + 1] - lum[i - w] - lum[i + w]);
    const c = Math.min(cols - 1, Math.floor((x / w) * cols));
    const r = Math.min(rows - 1, Math.floor((y / h) * rows));
    cells[r * cols + c] += v; counts[r * cols + c]++;
  }
  return cells.map((s, k) => (counts[k] ? s / counts[k] : 0));
}

function exposure(lum) {
  const n = lum.length || 1;
  let sum = 0, dark = 0, bright = 0;
  for (let i = 0; i < lum.length; i++) {
    sum += lum[i];
    if (lum[i] < 18) dark++;
    if (lum[i] > 238) bright++;
  }
  return { mean: sum / n, darkFrac: dark / n, brightFrac: bright / n };
}

function frameDiff(a, b) {
  if (!a || !b || a.length !== b.length || !a.length) return Infinity;
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += Math.abs(a[i] - b[i]);
  return sum / a.length;
}

// Short verdict for guidance. Returns {ok, tips[]} — ok means "good enough
// to auto-capture if the frame is also stable and novel".
function qualityVerdict(m) {
  const tips = [];
  if (m.sharpness < SHARP_MIN) tips.push('blurry');
  if (m.exposure.mean < EXPOSE_LO || m.exposure.darkFrac > DARK_FRAC_MAX) tips.push('dark');
  if (m.exposure.mean > EXPOSE_HI || m.exposure.brightFrac > BRIGHT_FRAC_MAX) tips.push('glare');
  return { ok: tips.length === 0, tips };
}

// 3x3 zone name for a normalized bbox [x0,y0,x1,y1]; used for dedupe keys
// and user-facing region references ("upper-left").
function zoneName(bbox) {
  if (!Array.isArray(bbox) || bbox.length !== 4) return 'unknown';
  const cx = (bbox[0] + bbox[2]) / 2, cy = (bbox[1] + bbox[3]) / 2;
  const vert = cy < 0.34 ? 'upper' : cy < 0.67 ? '' : 'lower';
  const horiz = cx < 0.34 ? 'left' : cx < 0.67 ? 'middle' : 'right';
  if (!vert) return horiz === 'middle' ? 'center' : horiz;
  return horiz === 'middle' ? vert : vert + '-' + horiz;
}

// Coverage: per-cell best sharpness seen. Update with elementwise max;
// weakest() names the least-scanned cell for guidance.
function newCoverage(cols, rows) { return { cols, rows, cells: new Array(cols * rows).fill(0) }; }
function updateCoverage(cov, grid) {
  for (let i = 0; i < cov.cells.length && i < grid.length; i++)
    if (grid[i] > cov.cells[i]) cov.cells[i] = grid[i];
  return cov;
}
function weakestCell(cov) {
  let k = 0;
  for (let i = 1; i < cov.cells.length; i++) if (cov.cells[i] < cov.cells[k]) k = i;
  const cx = (k % cov.cols + 0.5) / cov.cols, cy = (Math.floor(k / cov.cols) + 0.5) / cov.rows;
  return { index: k, value: cov.cells[k], zone: zoneName([cx, cy, cx, cy]) };
}
function coverageScore(cov) {
  if (!cov.cells.length) return 0;
  const good = cov.cells.filter((v) => v >= SHARP_MIN).length;
  return good / cov.cells.length;
}

/* ----- board pose tracking -------------------------------------------
   Detects the breadboard quad in a frame, solves the board->image
   homography, and decomposes it into a 3D orientation. All geometry is
   measured per frame; the track filter freezes on detection loss and
   rejects jumps, so the twin never moves without evidence. Camera model:
   principal point at frame center, focal ~1.1x frame width (typical phone
   wide lens; error biases tilt magnitude, not direction). */
function otsuThreshold(lum) {
  const hist = new Array(256).fill(0);
  for (let i = 0; i < lum.length; i++) hist[Math.max(0, Math.min(255, lum[i] | 0))]++;
  const n = lum.length;
  let sum = 0;
  for (let t = 0; t < 256; t++) sum += t * hist[t];
  let sumB = 0, wB = 0, best = 0, thresh = 128;
  for (let t = 0; t < 256; t++) {
    wB += hist[t];
    if (!wB) continue;
    const wF = n - wB;
    if (!wF) break;
    sumB += t * hist[t];
    const mB = sumB / wB, mF = (sum - sumB) / wF;
    const between = wB * wF * (mB - mF) * (mB - mF);
    if (between > best) { best = between; thresh = t; }
  }
  return thresh;
}

// Largest bright connected component (4-connectivity, iterative BFS).
function largestBlob(lum, w, h, thresh) {
  const seen = new Uint8Array(w * h);
  let best = null;
  const stack = [];
  for (let s = 0; s < w * h; s++) {
    if (seen[s] || lum[s] <= thresh) continue;
    let area = 0, x0 = w, y0 = h, x1 = 0, y1 = 0;
    stack.length = 0;
    stack.push(s); seen[s] = 1;
    while (stack.length) {
      const i = stack.pop();
      const x = i % w, y = (i / w) | 0;
      area++;
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
      if (x > 0 && !seen[i - 1] && lum[i - 1] > thresh) { seen[i - 1] = 1; stack.push(i - 1); }
      if (x < w - 1 && !seen[i + 1] && lum[i + 1] > thresh) { seen[i + 1] = 1; stack.push(i + 1); }
      if (y > 0 && !seen[i - w] && lum[i - w] > thresh) { seen[i - w] = 1; stack.push(i - w); }
      if (y < h - 1 && !seen[i + w] && lum[i + w] > thresh) { seen[i + w] = 1; stack.push(i + w); }
    }
    if (!best || area > best.area) best = { area, x0, y0, x1, y1 };
  }
  return best;
}

function allBlobs(field, w, h, thresh, minArea) {
  const seen = new Uint8Array(w * h);
  const blobs = [];
  const stack = [];
  for (let s = 0; s < w * h; s++) {
    if (seen[s] || field[s] <= thresh) continue;
    let area = 0, x0 = w, y0 = h, x1 = 0, y1 = 0;
    stack.length = 0;
    stack.push(s); seen[s] = 1;
    while (stack.length) {
      const i = stack.pop();
      const x = i % w, y = (i / w) | 0;
      area++;
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
      if (x > 0 && !seen[i - 1] && field[i - 1] > thresh) { seen[i - 1] = 1; stack.push(i - 1); }
      if (x < w - 1 && !seen[i + 1] && field[i + 1] > thresh) { seen[i + 1] = 1; stack.push(i + 1); }
      if (y > 0 && !seen[i - w] && field[i - w] > thresh) { seen[i - w] = 1; stack.push(i - w); }
      if (y < h - 1 && !seen[i + w] && field[i + w] > thresh) { seen[i + w] = 1; stack.push(i + w); }
    }
    if (area >= minArea) blobs.push({ area, x0, y0, x1, y1 });
  }
  return blobs.sort((a, b) => b.area - a.area);
}

// Local texture variance (box window, integral image): breadboard plastic is
// bright AND perforated (holes, rails, parts), while walls/tables/paper are
// bright but smooth. Variance-gated segmentation separates boards from
// bright backgrounds that merge with them under plain thresholding.
function localVariance(lum, w, h, r) {
  const W1 = w + 1;
  const i1 = new Float64Array(W1 * (h + 1)), i2 = new Float64Array(W1 * (h + 1));
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const v = lum[y * w + x], i = (y + 1) * W1 + x + 1;
    i1[i] = v + i1[i - 1] + i1[i - W1] - i1[i - W1 - 1];
    i2[i] = v * v + i2[i - 1] + i2[i - W1] - i2[i - W1 - 1];
  }
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    const y0 = Math.max(0, y - r), y1 = Math.min(h - 1, y + r);
    for (let x = 0; x < w; x++) {
      const x0 = Math.max(0, x - r), x1 = Math.min(w - 1, x + r);
      const n = (x1 - x0 + 1) * (y1 - y0 + 1);
      const s1 = i1[(y1 + 1) * W1 + x1 + 1] - i1[(y1 + 1) * W1 + x0] - i1[y0 * W1 + x1 + 1] + i1[y0 * W1 + x0];
      const s2 = i2[(y1 + 1) * W1 + x1 + 1] - i2[(y1 + 1) * W1 + x0] - i2[y0 * W1 + x1 + 1] + i2[y0 * W1 + x0];
      out[y * w + x] = Math.max(0, s2 / n - (s1 / n) * (s1 / n));
    }
  }
  return out;
}

// Separable box dilate/erode on a binary mask (Uint8 0/1).
function boxDilate(mask, w, h, r) {
  const tmp = new Uint8Array(w * h), out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    let run = 0;
    for (let x = 0; x < w; x++) {
      run = mask[y * w + x] ? r + 1 : Math.max(0, run - 1);
      tmp[y * w + x] = run > 0 ? 1 : 0;
    }
    run = 0;
    for (let x = w - 1; x >= 0; x--) {
      run = tmp[y * w + x] ? r + 1 : Math.max(0, run - 1);
      if (run > 0) tmp[y * w + x] = 1;
    }
  }
  for (let x = 0; x < w; x++) {
    let run = 0;
    for (let y = 0; y < h; y++) {
      run = tmp[y * w + x] ? r + 1 : Math.max(0, run - 1);
      out[y * w + x] = run > 0 ? 1 : 0;
    }
    run = 0;
    for (let y = h - 1; y >= 0; y--) {
      run = out[y * w + x] ? r + 1 : Math.max(0, run - 1);
      if (run > 0) out[y * w + x] = 1;
    }
  }
  return out;
}
function boxErode(mask, w, h, r) {
  const inv = new Uint8Array(w * h);
  for (let i = 0; i < mask.length; i++) inv[i] = mask[i] ? 0 : 1;
  const d = boxDilate(inv, w, h, r);
  for (let i = 0; i < d.length; i++) d[i] = d[i] ? 0 : 1;
  return d;
}

// Board mask: bright pixels minus background. Background is smooth-bright
// connected to the frame border (walls, tables). Smoothness is judged over
// a wide window so perforated plastic, rails with holes, and thin wires all
// read as non-smooth and block the flood — only truly blank areas drain
// away. Rails and grooves stay (they are board interior). A small opening
// cuts thin wires attached to the board. Callers keep largest component.
function boardMask(lum, w, h, thresh) {
  const v = localVariance(lum, w, h, 4);
  const smooth = new Uint8Array(w * h);
  for (let i = 0; i < lum.length; i++) smooth[i] = (lum[i] > thresh && v[i] <= 100) ? 1 : 0;
  const bg = floodFromBorder(smooth, w, h);
  const core = new Uint8Array(w * h);
  for (let i = 0; i < lum.length; i++) core[i] = (lum[i] > thresh && !bg[i]) ? 1 : 0;
  return boxDilate(boxErode(core, w, h, 2), w, h, 2);
}

// Pixels of a binary field reachable from the frame border (4-connected).
function floodFromBorder(field, w, h) {
  const out = new Uint8Array(w * h);
  const stack = [];
  const seed = (x, y) => {
    const i = y * w + x;
    if (field[i] && !out[i]) { out[i] = 1; stack.push(i); }
  };
  for (let x = 0; x < w; x++) { seed(x, 0); seed(x, h - 1); }
  for (let y = 1; y < h - 1; y++) { seed(0, y); seed(w - 1, y); }
  while (stack.length) {
    const i = stack.pop();
    const x = i % w, y = (i / w) | 0;
    if (x > 0) seed(x - 1, y);
    if (x < w - 1) seed(x + 1, y);
    if (y > 0) seed(x, y - 1);
    if (y < h - 1) seed(x, y + 1);
  }
  return out;
}

// Pixel mask of the largest 4-connected component of a binary field.
function largestComponent(field, w, h) {
  const seen = new Uint8Array(w * h);
  let best = null, bestN = 0;
  const stack = [];
  for (let s = 0; s < w * h; s++) {
    if (!field[s] || seen[s]) continue;
    stack.length = 0;
    stack.push(s); seen[s] = 1;
    const pts = [];
    while (stack.length) {
      const i = stack.pop();
      pts.push(i);
      const x = i % w, y = (i / w) | 0;
      if (x > 0 && field[i - 1] && !seen[i - 1]) { seen[i - 1] = 1; stack.push(i - 1); }
      if (x < w - 1 && field[i + 1] && !seen[i + 1]) { seen[i + 1] = 1; stack.push(i + 1); }
      if (y > 0 && field[i - w] && !seen[i - w]) { seen[i - w] = 1; stack.push(i - w); }
      if (y < h - 1 && field[i + w] && !seen[i + w]) { seen[i + w] = 1; stack.push(i + w); }
    }
    if (pts.length > bestN) { bestN = pts.length; best = pts; }
  }
  const out = new Uint8Array(w * h);
  if (best) for (const i of best) out[i] = 1;
  return out;
}

// Moore-neighbour contour trace from the topmost-leftmost blob pixel.
function traceContour(lum, w, h, thresh, blob) {
  // Boundary scan, not Moore following: collect blob pixels with a dark
  // 4-neighbor. Moore walks fall into premature stop loops on thin or
  // edge-touching regions; the unordered boundary feeds the
  // order-independent hull identically and handles every topology uniformly.
  // Points are unordered — callers must not assume ring order before hulling.
  const pts = [];
  for (let y = blob.y0; y <= blob.y1; y++) {
    for (let x = blob.x0; x <= blob.x1; x++) {
      if (lum[y * w + x] <= thresh) continue;
      if (x > blob.x0 && lum[y * w + x - 1] > thresh
        && x < blob.x1 && lum[y * w + x + 1] > thresh
        && y > blob.y0 && lum[(y - 1) * w + x] > thresh
        && y < blob.y1 && lum[(y + 1) * w + x] > thresh) continue;
      pts.push({ x, y });
    }
  }
  return pts;
}

function convexHull(points) {
  const pts = points.slice().sort((a, b) => (a.x - b.x) || (a.y - b.y));
  if (pts.length < 3) return pts;
  const cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  lower.pop(); upper.pop();
  return lower.concat(upper);
}

function rdpKeep(chain, eps) {
  const keep = new Array(chain.length).fill(false);
  keep[0] = keep[chain.length - 1] = true;
  const dist = (p, s, e) => {
    const dx = e.x - s.x, dy = e.y - s.y;
    const len2 = dx * dx + dy * dy;
    if (len2 < 1e-9) return Math.hypot(p.x - s.x, p.y - s.y);
    return Math.abs(dy * p.x - dx * p.y + e.x * s.y - e.y * s.x) / Math.sqrt(len2);
  };
  const stack = [[0, chain.length - 1]];
  while (stack.length) {
    const [s, e] = stack.pop();
    let bi = -1, bv = eps;
    for (let i = s + 1; i < e; i++) {
      const v = dist(chain[i], chain[s], chain[e]);
      if (v > bv) { bv = v; bi = i; }
    }
    if (bi > 0) { keep[bi] = true; stack.push([s, bi], [bi, e]); }
  }
  return keep;
}

function rdpClosed(points, eps) {
  if (points.length < 4) return points.slice();
  // Split the ring at its widest pair into two open chains.
  const n = points.length;
  let a = 0, b = 0, bd = -1;
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    const d = (points[i].x - points[j].x) ** 2 + (points[i].y - points[j].y) ** 2;
    if (d > bd) { bd = d; a = i; b = j; }
  }
  const c1 = [], c2 = [];
  for (let i = a; ; i = (i + 1) % n) { c1.push(points[i]); if (i === b) break; }
  for (let i = b; ; i = (i + 1) % n) { c2.push(points[i]); if (i === a) break; }
  const k1 = rdpKeep(c1, eps), k2 = rdpKeep(c2, eps);
  const out = [];
  c1.forEach((p, i) => { if (k1[i]) out.push(p); });
  c2.forEach((p, i) => { if (k2[i] && i > 0 && i < c2.length - 1) out.push(p); });
  return out;
}

function polyArea(poly) {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const p = poly[i], q = poly[(i + 1) % poly.length];
    a += p.x * q.y - q.x * p.y;
  }
  return Math.abs(a) / 2;
}

// Order quad corners starting at top-left-most, clockwise in image coords.
function orderCorners(quad) {
  const cx = quad.reduce((s, p) => s + p.x, 0) / 4;
  const cy = quad.reduce((s, p) => s + p.y, 0) / 4;
  const sorted = quad.slice().sort((a, b) =>
    Math.atan2(a.y - cy, a.x - cx) - Math.atan2(b.y - cy, b.x - cx));
  let start = 0;
  for (let i = 1; i < 4; i++)
    if (sorted[i].y < sorted[start].y - 1e-9
      || (Math.abs(sorted[i].y - sorted[start].y) < 1e-9 && sorted[i].x < sorted[start].x)) start = i;
  return [0, 1, 2, 3].map((k) => sorted[(start + k) % 4]);
}

// Snap a lock quad's edges to the board's physical boundary (bright plastic
// vs darker surroundings), lattice-independent. Each edge probes offsets
// along its outward normal; the score rewards a strong luminance step whose
// bright side faces the board interior. Returns the adjusted quad, or null
// when edges are inconsistent (caller keeps the original). Never invents
// board area: total corner movement is bounded and convexity/area rechecked.
function snapQuadToBoardEdges(lum, w, h, quad) {
  try {
    if (!lum || !w || !h || !Array.isArray(quad) || quad.length !== 4) return null;
    const at = (x, y) => {
      const xi = Math.round(x), yi = Math.round(y);
      if (xi < 0 || yi < 0 || xi >= w || yi >= h) return -1;
      return lum[yi * w + xi];
    };
    let cx = 0, cy = 0;
    quad.forEach((p) => { cx += p.x; cy += p.y; });
    cx /= 4; cy /= 4;
    let cur = quad.map((p) => ({ x: p.x, y: p.y }));
    const area0 = polyArea(cur) || 1;
    for (let round = 0; round < 2; round++) {
      let moved = false;
      for (let e = 0; e < 4; e++) {
        const a = cur[e], b = cur[(e + 1) % 4];
        const dx = b.x - a.x, dy = b.y - a.y;
        const len = Math.hypot(dx, dy);
        if (!(len > 1)) continue;
        let nx = -dy / len, ny = dx / len;
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        if ((mx + nx - cx) * (mx - cx) + (my + ny - cy) * (my - cy) < 0) { nx = -nx; ny = -ny; }
        const N = 24;
        let bestOff = 0, bestScore = -Infinity, found = false;
        for (const off of [-24, -16, -8, -4, 0, 4, 8, 16, 24]) {
          let str = 0, agree = 0, n = 0;
          for (let i = 0; i <= N; i++) {
            const t = i / N;
            const px = a.x + dx * t + nx * off, py = a.y + dy * t + ny * off;
            const sOut = at(px + nx * 4, py + ny * 4);
            const sIn = at(px - nx * 4, py - ny * 4);
            if (sOut < 0 || sIn < 0) continue;
            str += Math.abs(sOut - sIn); n++;
            if (sIn > sOut + 25) agree++;
          }
          if (!n) continue;
          found = true;
          const score = str / n + (agree >= n * 0.6 ? 60 : -60);
          if (score > bestScore) { bestScore = score; bestOff = off; }
        }
        if (found && bestOff !== 0) {
          cur[e] = { x: cur[e].x + nx * bestOff, y: cur[e].y + ny * bestOff };
          cur[(e + 1) % 4] = { x: cur[(e + 1) % 4].x + nx * bestOff, y: cur[(e + 1) % 4].y + ny * bestOff };
          moved = true;
        }
      }
      if (!moved) break;
    }
    if (!quadQuality(cur, w, h).ok) return null;
    const area = polyArea(cur);
    if (area < area0 * 0.5 || area > area0 * 2) return null;
    let maxMove = 0;
    for (let i = 0; i < 4; i++) maxMove = Math.max(maxMove, Math.hypot(cur[i].x - quad[i].x, cur[i].y - quad[i].y));
    if (maxMove > 120) return null;
    return cur;
  } catch (e) { return null; }
}
function quadQuality(quad, w, h) {
  if (!quad || quad.length !== 4) return { ok: false, reason: 'not-quad' };
  const area = polyArea(quad), frame = w * h;
  if (area < frame * 0.04) return { ok: false, reason: 'too-small' };
  if (area > frame * 0.96) return { ok: false, reason: 'too-large' };
  let sign = 0;
  const edges = [];
  for (let i = 0; i < 4; i++) {
    const p = quad[i], q = quad[(i + 1) % 4], r = quad[(i + 2) % 4];
    const c = (q.x - p.x) * (r.y - q.y) - (q.y - p.y) * (r.x - q.x);
    if (c !== 0) {
      if (!sign) sign = Math.sign(c);
      else if (Math.sign(c) !== sign) return { ok: false, reason: 'not-convex' };
    }
    edges.push(Math.hypot(q.x - p.x, q.y - p.y));
  }
  const minE = Math.min(...edges), maxE = Math.max(...edges);
  if (minE < 8) return { ok: false, reason: 'degenerate' };
  if (maxE / minE > 5) return { ok: false, reason: 'bad-aspect' };
  return { ok: true, score: Math.min(1, area / (frame * 0.4)) };
}

function findBoardQuad(lum, w, h) {
  const thresh = otsuThreshold(lum);
  const blob = largestBlob(lum, w, h, thresh);
  if (!blob || blob.area < w * h * 0.04) return null;
  const contour = traceContour(lum, w, h, thresh, blob);
  if (contour.length < 16) return null;
  const hull = convexHull(contour);
  if (hull.length < 4) return null;
  let per = 0;
  for (let i = 0; i < hull.length; i++)
    per += Math.hypot(hull[(i + 1) % hull.length].x - hull[i].x, hull[(i + 1) % hull.length].y - hull[i].y);
  const simp = rdpClosed(hull, Math.max(1.5, per * 0.02));
  if (simp.length !== 4) return null;
  const quad = orderCorners(simp);
  const q = quadQuality(quad, w, h);
  return q.ok ? { corners: quad, score: q.score } : null;
}

// Framing check: is a board-like bright mass fully in frame, cut off by an
// edge, or absent? Drives "pull back / aim at the board" guidance.
function detectBoardFraming(lum, w, h) {
  const thresh = otsuThreshold(lum);
  const blob = largestBlob(lum, w, h, thresh);
  if (!blob || blob.area < w * h * 0.04) return { state: 'none', areaFrac: 0 };
  const areaFrac = blob.area / (w * h);
  const touches = blob.x0 <= 0 || blob.y0 <= 0 || blob.x1 >= w - 1 || blob.y1 >= h - 1;
  return { state: touches ? 'partial' : 'full', areaFrac };
}

// Gaussian elimination with partial pivot for n x n systems.
function solveLinear(A, b) {
  const n = b.length;
  const M = A.map((row, i) => row.concat(b[i]));
  for (let c = 0; c < n; c++) {
    let piv = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r;
    if (Math.abs(M[piv][c]) < 1e-12) return null;
    const tmp = M[c]; M[c] = M[piv]; M[piv] = tmp;
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = M[r][c] / M[c][c];
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
    }
  }
  return M.map((row, i) => row[n] / M[i][i]);
}

// Homography mapping src->dst (4 correspondences, row-major 3x3). Fixes h[8]=1.
function homography(src, dst) {
  const A = [], b = [];
  for (let i = 0; i < 4; i++) {
    const [X, Y] = [src[i].x, src[i].y], [x, y] = [dst[i].x, dst[i].y];
    A.push([-X, -Y, -1, 0, 0, 0, x * X, x * Y]); b.push(-x);
    A.push([0, 0, 0, -X, -Y, -1, y * X, y * Y]); b.push(-y);
  }
  // Normal equations: (A'A)h = A'b.
  const AtA = [], Atb = new Array(8).fill(0);
  for (let r = 0; r < 8; r++) {
    AtA.push(new Array(8).fill(0));
    for (let c = 0; c < 8; c++) {
      let s = 0;
      for (let k = 0; k < 8; k++) s += A[k][r] * A[k][c];
      AtA[r][c] = s;
    }
    let s = 0;
    for (let k = 0; k < 8; k++) s += A[k][r] * b[k];
    Atb[r] = s;
  }
  const h = solveLinear(AtA, Atb);
  return h ? h.concat(1) : null;
}

function applyHomography(H, x, y) {
  const w = H[6] * x + H[7] * y + H[8];
  return { x: (H[0] * x + H[1] * y + H[2]) / w, y: (H[3] * x + H[4] * y + H[5]) / w };
}

// Decompose board->image homography into camera-space rotation columns.
function decomposeHomography(H, w, h, focal) {
  const f = focal || w * 1.1, cx = w / 2, cy = h / 2;
  const col = (j) => [H[j], H[3 + j], H[6 + j]];
  const norm = (v) => [(v[0] - cx * v[2]) / f, (v[1] - cy * v[2]) / f, v[2]];
  const b1 = norm(col(0)), b2 = norm(col(1));
  const n1 = Math.hypot(...b1), n2 = Math.hypot(...b2);
  if (!(n1 > 1e-9) || !(n2 > 1e-9)) return null;
  const lam = 2 / (n1 + n2);
  const r1 = b1.map((v) => v * lam), r2 = b2.map((v) => v * lam);
  const cross = (a, b2v) => [a[1] * b2v[2] - a[2] * b2v[1], a[2] * b2v[0] - a[0] * b2v[2], a[0] * b2v[1] - a[1] * b2v[0]];
  // Orthonormalize columns (Gram-Schmidt).
  const dot = (a, b2v) => a[0] * b2v[0] + a[1] * b2v[1] + a[2] * b2v[2];
  const nrm = (a) => { const l = Math.hypot(...a) || 1; return a.map((v) => v / l); };
  const e1 = nrm(r1);
  const e2 = nrm(r2.map((v, i) => v - dot(r2, e1) * e1[i]));
  const e3 = cross(e1, e2);
  const R = [[e1[0], e2[0], e3[0]], [e1[1], e2[1], e3[1]], [e1[2], e2[2], e3[2]]];
  const t = norm(col(2)).map((v) => v * lam);
  const skew = Math.abs(n1 - n2) / (n1 + n2);
  return { R, t, skew };
}

// Rotation matrix (camera coords, y-down) to three.js-style YXZ euler after
// mapping to y-up: R3 = S*R*S with S = diag(1,-1,1).
function rotationToEulerYXZ(R) {
  const S = (i, j) => R[i][j] * (i === 1 ? -1 : 1) * (j === 1 ? -1 : 1);
  const r02 = S(0, 2), r12 = S(1, 2), r22 = S(2, 2), r10 = S(1, 0), r11 = S(1, 1);
  const rx = Math.asin(Math.max(-1, Math.min(1, -r12)));
  if (Math.abs(r12) < 0.9999) return { rx, ry: Math.atan2(r02, r22), rz: Math.atan2(r10, r11) };
  return { rx, ry: Math.atan2(-S(2, 0), S(0, 0)), rz: 0 };
}

function matMul3(A, B) {
  return A.map((row) => B[0].map((_, j) => row[0] * B[0][j] + row[1] * B[1][j] + row[2] * B[2][j]));
}
function matTranspose3(A) {
  return A[0].map((_, j) => A.map((row) => row[j]));
}

// Full per-frame estimate. boardW/boardL in the same units (mm). Corner
// correspondence is ambiguous under rotation, so every cyclic rotation is
// decomposed and only geometrically consistent hypotheses (low skew) are
// returned — usually one, sometimes two (the 180-degree twin, resolved by
// track continuity). Empty array when the board is not measurable.
//
// opts.verify enables the structurally-verified lock used in production:
//   { lumHi, wHi, hHi, minScore, track }
// Outline detection alone cannot tell a breadboard from any white rectangle,
// and bright backgrounds merge with the board into non-quad blobs. The
// verified path fits generous outline/line candidates at high resolution,
// then keeps ONLY quads whose rectified interior shows the breadboard's
// large-scale structure (center groove + left/right silhouette steps) —
// paper, walls, and merged blobs are rejected, never tracked.
function estimateBoardPose(lum, w, h, boardW, boardL, opts) {
  const verify = opts && opts.verify;
  if (verify && verify.lumHi) return estimateVerified(verify, boardW, boardL);
  const found = findBoardQuad(lum, w, h);
  if (!found) return [];
  const src = boardSrc(boardW, boardL);
  const out = [];
  for (let rot = 0; rot < 4; rot++) {
    const dst = [0, 1, 2, 3].map((k) => found.corners[(k + rot) % 4]);
    const H = homography(src, dst);
    if (!H) continue;
    const dec = decomposeHomography(H, w, h);
    if (!dec || dec.skew > 0.35) continue;
    out.push({ R: dec.R, t: dec.t, skew: dec.skew, corners: found.corners, rot,
      conf: Math.max(0, found.score * (1 - dec.skew)) });
  }
  return out;
}

function boardSrc(boardW, boardL) {
  return [{ x: -boardW / 2, y: -boardL / 2 }, { x: boardW / 2, y: -boardL / 2 },
    { x: boardW / 2, y: boardL / 2 }, { x: -boardW / 2, y: boardL / 2 }];
}

// Generous outline candidates: RDP epsilon sweep plus 4-subsets of
// over-segmented hulls. Candidates come from TWO segmentations: the
// variance-gated board mask (separates perforated plastic from smooth bright
// backgrounds that merge with it) and the plain bright field (fallback for
// heavily blurred frames). Sorted best-first, deduplicated.
function findBoardQuads(lum, w, h) {
  const out = [];
  const thresh = otsuThreshold(lum);
  // Discriminative segmentation first; plain bright field only as fallback.
  const mask = boardMask(lum, w, h, thresh);
  quadsFromField(mask, w, h, 0, w * h * 0.015, out);
  if (out.length < 8) quadsFromField(lum, w, h, thresh, w * h * 0.02, out);
  return out.sort((a, b) => b.score - a.score);
}

// Longest run of blob pixels along one frame edge (0=top,1=bottom,2=left,3=right).
function edgeRun(field, w, h, thresh, blob, edge) {
  let run = 0, mx = 0;
  const test = (x, y) => (x < blob.x0 || x > blob.x1 || y < blob.y0 || y > blob.y1)
    ? false : field[y * w + x] > thresh;
  if (edge === 0) for (let x = 0; x < w; x++) { run = test(x, 0) ? run + 1 : 0; mx = Math.max(mx, run); }
  if (edge === 1) for (let x = 0; x < w; x++) { run = test(x, h - 1) ? run + 1 : 0; mx = Math.max(mx, run); }
  if (edge === 2) for (let y = 0; y < h; y++) { run = test(0, y) ? run + 1 : 0; mx = Math.max(mx, run); }
  if (edge === 3) for (let y = 0; y < h; y++) { run = test(w - 1, y) ? run + 1 : 0; mx = Math.max(mx, run); }
  return mx;
}

function quadsFromField(field, w, h, thresh, minArea, out) {
  const blob = largestBlob(field, w, h, thresh);
  if (!blob || blob.area < minArea) return;
  // Honest partial-view veto: a LONG run of blob pixels along a frame edge
  // means the board itself is cut off (short runs are just wires/dots), so
  // no fitted quad can be trusted as the full outline. The scan UI already
  // steers the user to pull back in this case.
  const maxRun = Math.max(
    edgeRun(field, w, h, thresh, blob, 0), edgeRun(field, w, h, thresh, blob, 1),
    edgeRun(field, w, h, thresh, blob, 2), edgeRun(field, w, h, thresh, blob, 3));
  if (maxRun > Math.min(w, h) * 0.12) return;
  // Hull only the connected component: nearby specks inside the bbox must
  // not poke the outline out.
  const bin = new Uint8Array(w * h);
  for (let y = blob.y0; y <= blob.y1; y++)
    for (let x = blob.x0; x <= blob.x1; x++) bin[y * w + x] = field[y * w + x] > thresh ? 1 : 0;
  const comp = largestComponent(bin, w, h);
  const cblob = largestBlob(comp, w, h, 0);
  if (cblob) {
    const contour = traceContour(comp, w, h, 0, cblob);
    if (contour && contour.length >= 16) quadsFromHull(convexHull(contour), w, h, out);
  }
  if (out.length >= 8) return;
  // Merge path: dark modules and wire bundles split the board's bright mass
  // into aligned fragments, so the largest blob alone is a piece of the
  // board. Group aligned blobs and hull the union; boardScore verifies.
  quadsFromMerged(field, w, h, thresh, minArea, out);
}

function quadsFromMerged(field, w, h, thresh, minArea, out) {
  const blobs = allBlobs(field, w, h, thresh, Math.max(200, minArea * 0.15)).slice(0, 12);
  if (blobs.length < 2) return;
  // Merge only along the anchor fragment's long axis: board pieces line up
  // end to end, while reflections and clutter sit off-axis. The elongation
  // gate additionally rejects chains that wander into square unions.
  const anchor = blobs[0];
  const horizontal = (anchor.x1 - anchor.x0) >= (anchor.y1 - anchor.y0);
  const parent = blobs.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const aligned = (a, b) => {
    const uw = Math.max(a.x1, b.x1) - Math.min(a.x0, b.x0);
    const uh = Math.max(a.y1, b.y1) - Math.min(a.y0, b.y0);
    if (Math.max(uw, uh) / Math.max(1, Math.min(uw, uh)) < 1.4) return false;
    if (horizontal) {
      const y0 = Math.max(a.y0, b.y0), y1 = Math.min(a.y1, b.y1);
      const xgap = Math.max(0, Math.max(a.x0, b.x0) - Math.min(a.x1, b.x1));
      const ah = a.y1 - a.y0, bh = b.y1 - b.y0;
      const mh = Math.min(ah, bh);
      const yc = Math.abs((a.y0 + a.y1) / 2 - (b.y0 + b.y1) / 2);
      // Fragments of one board edge share a thickness; a tall reflection
      // next to a thin strip is clutter, not a board piece.
      if (mh / Math.max(1, Math.max(ah, bh)) < 0.35) return false;
      // Gap scales with the pair union (the board estimate): a module can
      // cover a third of the board, dwarfing either fragment beside it.
      return y1 - y0 > 0.4 * mh && xgap < 0.35 * uw
        && yc < 0.4 * Math.max(ah, bh);
    }
    const x0 = Math.max(a.x0, b.x0), x1 = Math.min(a.x1, b.x1);
    const ygap = Math.max(0, Math.max(a.y0, b.y0) - Math.min(a.y1, b.y1));
    const aw = a.x1 - a.x0, bw = b.x1 - b.x0;
    const mw = Math.min(aw, bw);
    const xc = Math.abs((a.x0 + a.x1) / 2 - (b.x0 + b.x1) / 2);
    if (mw / Math.max(1, Math.max(aw, bw)) < 0.35) return false;
    return x1 - x0 > 0.4 * mw && ygap < 0.35 * uh
      && xc < 0.4 * Math.max(aw, bw);
  };
  for (let i = 0; i < blobs.length; i++)
    for (let j = i + 1; j < blobs.length; j++)
      if (aligned(blobs[i], blobs[j])) parent[find(i)] = find(j);
  const groups = new Map();
  blobs.forEach((b, i) => {
    const r = find(i);
    if (!groups.has(r)) groups.set(r, []);
    groups.get(r).push(b);
  });
  const merged = [...groups.values()].filter((g) => g.length > 1).map((g) => ({
    box: { x0: Math.min(...g.map((b) => b.x0)), y0: Math.min(...g.map((b) => b.y0)),
      x1: Math.max(...g.map((b) => b.x1)), y1: Math.max(...g.map((b) => b.y1)) },
    area: g.reduce((s, b) => s + b.area, 0),
  })).filter((g) => g.area >= minArea)
    .sort((a, b) => b.area - a.area).slice(0, 2);
  for (const g of merged) {
    if (out.length >= 8) return;
    const box = { ...g.box, area: g.area };
    const maxRun = Math.max(
      edgeRun(field, w, h, thresh, box, 0), edgeRun(field, w, h, thresh, box, 1),
      edgeRun(field, w, h, thresh, box, 2), edgeRun(field, w, h, thresh, box, 3));
    if (maxRun > Math.min(w, h) * 0.12) continue;
    const pts = [];
    for (let y = box.y0; y <= box.y1; y++)
      for (let x = box.x0; x <= box.x1; x++)
        if (field[y * w + x] > thresh) pts.push({ x, y });
    if (pts.length < 16) continue;
    quadsFromHull(convexHull(pts), w, h, out);
  }
}

function quadsFromHull(hull, w, h, out) {
  if (!hull || hull.length < 4) return;
  let per = 0;
  for (let i = 0; i < hull.length; i++)
    per += Math.hypot(hull[(i + 1) % hull.length].x - hull[i].x, hull[(i + 1) % hull.length].y - hull[i].y);
  const push = (pts) => {
    if (!pts || pts.length !== 4 || out.length >= 8) return;
    const quad = orderCorners(pts);
    const q = quadQuality(quad, w, h);
    if (!q.ok) return;
    const edges = [];
    for (let i = 0; i < 4; i++)
      edges.push(Math.hypot(quad[(i + 1) % 4].x - quad[i].x, quad[(i + 1) % 4].y - quad[i].y));
    const ratio = Math.min(...edges) / Math.max(...edges);
    if (!(ratio >= 0.08 && ratio <= 0.85)) return; // breadboard proportions, tilt-tolerant
    const tol = Math.max(2, w * 0.01);
    for (const e of out) {
      let d = 0;
      for (let i = 0; i < 4; i++) d += Math.hypot(e.corners[i].x - quad[i].x, e.corners[i].y - quad[i].y);
      if (d / 4 < tol) return;
    }
    out.push({ corners: quad, score: q.score });
  };
  for (const f of [0.008, 0.02, 0.035, 0.055]) {
    if (out.length >= 8) return;
    const simp = rdpClosed(hull, Math.max(1.5, per * f));
    if (simp.length === 4) { push(simp); continue; }
    if (simp.length < 5 || simp.length > 14) continue;
    const n = simp.length;
    let count = 0;
    for (let a = 0; a < n && count < 250 && out.length < 8; a++)
      for (let b = a + 1; b < n && count < 250 && out.length < 8; b++)
        for (let c = b + 1; c < n && count < 250 && out.length < 8; c++)
          for (let d = c + 1; d < n && count < 250 && out.length < 8; d++) {
            count++;
            push([simp[a], simp[b], simp[c], simp[d]]);
          }
  }
}

// Lattice verification: rectify the candidate quad and check that dark holes
// sit on the expected breadboard grid. holeMM(row, colIdx) maps 1-based row +
// 0-based A-J column to {x, z} mm; geometry stays owned by breadboard.js.
// Returns 0..1: mean relative darkness of hole centers vs neighboring
// plastic, sampled 3x3 for blur tolerance. Normalizes by plastic brightness
// (not global sd) so dark modules on the board cannot suppress the score.
function latticeContrast(lum, w, h, corners, rot, boardW, boardL, holeMM, rows) {
  return latticeScores(lum, w, h, corners, boardW, boardL, holeMM, rows)[((rot % 4) + 4) % 4];
}

// All four rotation scores from a single rectification: rotating the
// hole-coordinate frame is equivalent to rotating the corner order.
function latticeScores(lum, w, h, corners, boardW, boardL, holeMM, rows, outW) {
  const RW = outW || 156, RH = Math.max(60, Math.round((RW * boardL) / boardW));
  const rect = rectifyQuad(lum, w, h, corners, RW, RH);
  if (!rect) return [0, 0, 0, 0];
  const holes = [];
  for (let r = 1; r <= rows; r += 1) {
    for (let ci = 0; ci < 10; ci++) {
      const m = holeMM(r, ci);
      if (!m || !isFinite(m.x) || !isFinite(m.z)) continue;
      holes.push([(m.x + boardW / 2) / boardW, (m.z + boardL / 2) / boardL]);
    }
  }
  const rotUV = (u, v, r) => (r === 0 ? [u, v] : r === 1 ? [1 - v, u] : r === 2 ? [1 - u, 1 - v] : [v, 1 - u]);
  // Per-hole z-score vs its own plastic annulus: a hole must be darker
  // than the local texture explains. Global margins are gameable (dark
  // modules pass as holes; textured desks pass by chance); local
  // normalization makes true quads score near the visible-hole fraction
  // while clutter and aliasing collapse toward zero.
  const out = [];
  for (let r = 0; r < 4; r++) {
    let pass = 0, n = 0;
    for (const [u, v] of holes) {
      const [ur, vr] = rotUV(u, v, r);
      const x = ur * RW, y = vr * RH;
      const xi = Math.round(x), yi = Math.round(y);
      if (xi < 4 || yi < 4 || xi >= RW - 4 || yi >= RH - 4) continue;
      let c = 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) c += rect[(yi + dy) * RW + xi + dx];
      c /= 9;
      let s1 = 0, s2 = 0, m = 0;
      for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
        if (Math.abs(dx) <= 1 && Math.abs(dy) <= 1) continue;
        const vv = rect[(yi + dy) * RW + xi + dx];
        s1 += vv; s2 += vv * vv; m++;
      }
      const mu = s1 / m, sd = Math.sqrt(Math.max(0, s2 / m - mu * mu));
      if ((mu - c) / Math.max(4, sd) > 2.5) pass++;
      n++;
    }
    out.push(n < 100 ? 0 : pass / n);
  }
  return out;
}

// Structural board score: rectify the quad to 160x48 and measure two
// large-scale features that survive blur, occlusion, and layout mismatch:
// (1) the center-groove darkness dip across the middle rows, (2) the
// left/right silhouette steps where the board meets the background.
// Returns { score, rot }: best over 4 corner rotations (groove/silhouette
// assume the long axis maps to the rect u-axis). Calibrated on a real
// full-board photo: true quad ~76, near-miss ~42, off-board ~-5, so the
// default lock threshold is 60 (PRELIMINARY: three samples only; the
// relative track path below needs no absolute threshold).
function boardScore(lum, w, h, corners, fixedRot) {
  const base = orderCorners(corners.map((p) => ({ x: p.x, y: p.y })));
  const rots = (fixedRot === undefined) ? [0, 1, 2, 3] : [fixedRot];
  let best = { score: -Infinity, rot: 0 };
  for (const rot of rots) {
    const q = [0, 1, 2, 3].map((k) => base[(k + rot) % 4]);
    const data = rectifyQuad(lum, w, h, q, 160, 48);
    if (!data) continue;
    const rw = 160, rh = 48;
    const rowMean = new Float32Array(rh);
    for (let y = 0; y < rh; y++) {
      let s = 0;
      for (let x = 0; x < rw; x++) s += data[y * rw + x];
      rowMean[y] = s / rw;
    }
    let mid = Infinity, midRow = 24;
    for (let y = 18; y < 30; y++) {
      if (rowMean[y] < mid) { mid = rowMean[y]; midRow = y; }
    }
    // Plastic reference: mean of the brighter half of rectified pixels. A
    // plain row mean is dragged down by dark modules riding the board,
    // which would shrink genuine groove depth on populated boards.
    const px = Array.from(data);
    px.sort((a, b) => a - b);
    const half = px.length >> 1;
    let out = 0;
    for (let i = half; i < px.length; i++) out += px[i];
    out /= Math.max(1, px.length - half);
    // The groove must span the board's length: a dark wire crossing a few
    // columns makes a deep but local dip. Weight depth by the fraction of
    // columns actually dark on the groove row.
    let darkCols = 0;
    if (out > mid) {
      const thr = out - (out - mid) * 0.5;
      for (let x = 0; x < rw; x++) if (data[midRow * rw + x] < thr) darkCols++;
    }
    const groove = (out - mid) * (darkCols / rw);
    const step = (xIn, xOut) => {
      const ds = [];
      for (let y = 2; y < rh - 2; y++) {
        let si = 0, so = 0;
        for (let x = 0; x < 6; x++) { si += data[y * rw + xIn + x]; so += data[y * rw + xOut + x]; }
        ds.push(Math.abs(si - so) / 6);
      }
      ds.sort((a, b) => a - b);
      return ds[Math.floor(ds.length * 0.6)];
    };
    const score = groove + Math.min(step(10, 1), step(rw - 16, rw - 7));
    if (score > best.score) best = { score, rot };
  }
  return best;
}

// Resolution contract: the geometry constants below (variance window,
// RANSAC band, refine steps/bound) are calibrated for scan resolution
// (~480px wide). Do NOT run this on megapixel stills directly — downsample
// for the lock, then scale the quad up (see registerCapture).
function estimateVerified(verify, boardW, boardL) {
  const lum = verify.lumHi, w = verify.wHi, h = verify.hHi;
  const src = boardSrc(boardW, boardL);
  const minS = (verify.minScore !== undefined) ? verify.minScore : 60;
  const track = verify.track || null; // locked corners: continuity gate
  const check = (corners, needProof) => {
    const out = [];
    // boardScore rotates orderCorners(corners); reuse that base order so
    // the winning rotation maps the same way here.
    const base = orderCorners(corners.map((p) => ({ x: p.x, y: p.y })));
    const verdict = needProof ? boardScore(lum, w, h, corners)
      : { score: 1, rot: 0 };
    if (needProof && !(verdict.score >= minS)) return out;
    const rots = needProof ? [verdict.rot] : [0, 1, 2, 3];
    for (const rot of rots) {
      const dst = [0, 1, 2, 3].map((k) => base[(k + rot) % 4]);
      const H = homography(src, dst);
      if (!H) continue;
      const dec = decomposeHomography(H, w, h);
      if (!dec || dec.skew > 0.35) continue;
      out.push({ R: dec.R, t: dec.t, skew: dec.skew, corners: base, rot,
        contrast: verdict.score, conf: dec.skew < 0.35 ? Math.min(1, verdict.score / 100) : 0 });
    }
    return out;
  };
  const near = (corners) => {
    if (!track) return true;
    let d = 0;
    for (let i = 0; i < 4; i++) d += Math.hypot(corners[i].x - track[i].x, corners[i].y - track[i].y);
    return d / 4 < Math.max(12, w * 0.05);
  };
  // Precise outline fits first (cheap); RANSAC line completion only when
  // fragmentation or clutter defeats the outline path.
  const cands = findBoardQuads(lum, w, h);
  let out = [];
  for (const q of cands) {
    if (!near(q.corners)) continue;
    // Proof always required: continuity alone would let a passing white
    // rectangle hijack the pose.
    out.push(...check(q.corners, true));
  }
  if (!out.length && !track) {
    // Lock path: refine the top-ranked line quads (their corners land
    // ±15px off from span gaps) before the structural verdict.
    for (const q of fitQuadFromLines(lum, w, h).slice(0, 3)) {
      const rq = refineQuad(lum, w, h, q.corners);
      out.push(...check(rq.corners, true));
      if (out.length) break;
    }
  }
  if (!out.length && track) {
    // Track path: hill-climb the structural score from the locked quad.
    // Relative (accept on improvement + continuity), so no absolute
    // threshold is needed; the caller re-verifies on a slow cadence.
    const base = boardScore(lum, w, h, track).score;
    const rq = refineQuad(lum, w, h, track);
    if (rq.score >= base * 0.5 && rq.score > 15 && near(rq.corners))
      out.push(...check(rq.corners, false));
  }
  out.sort((a, b) => b.contrast - a.contrast);
  return out;
}

// Coordinate-descent corner refinement against the structural board score.
// Translation moves come first: span gaps shift line-fit corners
// coherently, and single-corner moves cannot follow. Optional fixedRot
// pins the winning rotation (per-frame tracking); otherwise each eval
// searches all 4 rotations. Returns { corners, score, rot }.
function refineQuad(lum, w, h, corners, fixedRot) {
  const score = (q) => boardScore(lum, w, h, q, fixedRot).score;
  const start = corners.map((p) => ({ x: p.x, y: p.y }));
  const bounded = (q) => {
    for (let i = 0; i < 4; i++)
      if (Math.hypot(q[i].x - start[i].x, q[i].y - start[i].y) > 24) return false;
    return true;
  };
  let q = start.map((p) => ({ x: p.x, y: p.y }));
  let best = score(q);
  // Translation sweeps iterate to convergence: a single pass cannot reach
  // diagonal moves (each axis tried separately), stranding the descent.
  for (let sweep = 0; sweep < 3; sweep++) {
    let moved = false;
    for (const step of [10, 4]) {
      for (const [dx, dy] of [[step, 0], [-step, 0], [0, step], [0, -step],
          [step, step], [step, -step], [-step, step], [-step, -step]]) {
        const t = q.map((p) => ({ x: p.x + dx, y: p.y + dy }));
        if (!bounded(t)) continue;
        const s = score(t);
        if (s > best) { best = s; q = t; moved = true; }
      }
    }
    if (!moved) break;
  }
  for (const step of [6, 2]) {
    for (let i = 0; i < 4; i++) {
      for (const [dx, dy] of [[step, 0], [-step, 0], [0, step], [0, -step]]) {
        const t = q.map((p, j) => (j === i ? { x: p.x + dx, y: p.y + dy } : p));
        if (!bounded(t)) continue;
        const s = score(t);
        if (s > best) { best = s; q = t; }
      }
    }
  }
  const rot = boardScore(lum, w, h, q, fixedRot).rot;
  return { corners: q, score: best, rot };
}

// Quad completion from texture-run edges: the board is the tallest textured
// structure along most scanlines, so per-column/per-row tallest runs yield
// edge points directly — no brightness segmentation to merge with the
// background. Top/bottom fit first (long, reliable); sides fit in x-windows
// around the top/bottom span ends (attached wires corrupt the global side
// point sets beyond usability). Combinations intersect into quads ranked by
// physical aspect match; the structural boardScore check owns truth.
function fitQuadFromLines(lum, w, h) {
  const v = localVariance(lum, w, h, 4);
  const tex = new Uint8Array(w * h);
  for (let i = 0; i < lum.length; i++) tex[i] = v[i] > 100 ? 1 : 0;
  const minRun = Math.max(15, Math.round(Math.min(w, h) * 0.08));
  const top = [], bot = [], lft = [], rgt = [];
  for (let x = 0; x < w; x += 2) {
    let y0 = -1, best = null;
    for (let y = 0; y <= h; y++) {
      const on = y < h && tex[y * w + x];
      if (on && y0 < 0) y0 = y;
      if (!on && y0 >= 0) {
        if (y - y0 >= minRun && (!best || y - y0 > best[1] - best[0])) best = [y0, y - 1];
        y0 = -1;
      }
    }
    if (best) { top.push({ x, y: best[0] }); bot.push({ x, y: best[1] }); }
  }
  for (let y = 0; y < h; y += 2) {
    let x0 = -1, best = null;
    for (let x = 0; x <= w; x++) {
      const on = x < w && tex[y * w + x];
      if (on && x0 < 0) x0 = x;
      if (!on && x0 >= 0) {
        if (x - x0 >= minRun && (!best || x - x0 > best[1] - best[0])) best = [x0, x - 1];
        x0 = -1;
      }
    }
    if (best) { lft.push({ x: best[0], y }); rgt.push({ x: best[1], y }); }
  }
  if (top.length < 30 || bot.length < 30) return [];
  const T = ransacLines(top, 3, 25), B = ransacLines(bot, 3, 25);
  if (!T.length || !B.length) return [];
  // Sides: lines roughly perpendicular to top/bottom with tall inlier
  // spans. Attached wires curve (short straight pieces) and module edges
  // are shorter than board sides, so direction + span + inlier count
  // isolate the true sides without any x-window around the
  // (wire-corruptible) top/bottom span ends.
  // Circular mean of doubled normal angles (theta wraps modulo PI: 88deg
  // and -92deg are the same direction, but naive averaging gives -2deg).
  const t2 = (T[0].theta * 2 + B[0].theta * 2) / 2;
  const baseTheta = Math.atan2(Math.sin(t2), Math.cos(t2)) / 2
    + (Math.abs(T[0].theta - B[0].theta) > Math.PI / 2 ? Math.PI / 2 : 0);
  const sideLines = (pts) => {
    if (pts.length < 15) return [];
    return ransacLines(pts, 6, 12).filter((l) => {
      let d = Math.abs(l.theta - baseTheta) % Math.PI;
      if (d > Math.PI / 2) d = Math.PI - d;
      const perp = Math.abs(d - Math.PI / 2) < 0.45;
      const span = Math.hypot(l.ends[1].x - l.ends[0].x, l.ends[1].y - l.ends[0].y);
      return perp && span > Math.min(w, h) * 0.18;
    }).slice(0, 3);
  };
  const L = sideLines(lft), R = sideLines(rgt);
  if (!L.length || !R.length) return [];
  const quads = [];
  for (const t of T) for (const b of B) for (const l of L) for (const r of R) {
    const p1 = lineIntersect(t, l), p2 = lineIntersect(t, r);
    const p3 = lineIntersect(b, r), p4 = lineIntersect(b, l);
    if (!p1 || !p2 || !p3 || !p4) continue;
    const quad = orderCorners([p1, p2, p3, p4]);
    const q = quadQuality(quad, w, h);
    if (!q.ok) continue;
    // Frame containment: wire-line intersections land far outside the
    // frame yet pass area/aspect/convexity gates on their own.
    if (quad.some((p) => p.x < -w * 0.02 || p.x > w * 1.02 || p.y < -h * 0.02 || p.y > h * 1.02)) continue;
    const edges = [];
    for (let e = 0; e < 4; e++)
      edges.push(Math.hypot(quad[(e + 1) % 4].x - quad[e].x, quad[(e + 1) % 4].y - quad[e].y));
    const ratio = Math.min(...edges) / Math.max(...edges);
    if (!(ratio >= 0.12 && ratio <= 0.75)) continue;
    quads.push({ corners: quad, rank: (t.n + b.n + l.n + r.n) / (1 + 8 * Math.abs(ratio - 0.3)) });
  }
  quads.sort((a, b) => b.rank - a.rank);
  return quads.slice(0, 6);
}

// Normal-form lines through boundary points: sample distant pairs, keep
// lines with long straight support, remove inliers, repeat. Curves and
// specks never gather enough collinear inliers to survive.
function ransacLines(pts, maxLines, minInliers) {
  const lines = [];
  let rest = pts;
  let seed = 1234567;
  const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const p of pts) {
    if (p.x < x0) x0 = p.x; if (p.x > x1) x1 = p.x;
    if (p.y < y0) y0 = p.y; if (p.y > y1) y1 = p.y;
  }
  const minPair = Math.max(30, Math.min(x1 - x0, y1 - y0) * 0.08);
  const minN = minInliers || 50;
  for (let L = 0; L < maxLines && rest.length >= minN; L++) {
    let best = null;
    for (let it = 0; it < 250; it++) {
      const a = rest[(rnd() * rest.length) | 0], b = rest[(rnd() * rest.length) | 0];
      if (Math.hypot(a.x - b.x, a.y - b.y) < minPair) continue;
      const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len, rho = nx * a.x + ny * a.y;
      let n = 0;
      for (const p of rest) if (Math.abs(nx * p.x + ny * p.y - rho) < 2) n++;
      if (!best || n > best.n) best = { nx, ny, rho, n, theta: Math.atan2(ny, nx) };
    }
    if (!best || best.n < minN) break;
    // Inlier span endpoints along the line direction (5th/95th percentiles:
    // stray wire inliers stretch min/max spans far past the true edge).
    const dx = -best.ny, dy = best.nx;
    const ts = [];
    for (const p of rest) {
      if (Math.abs(best.nx * p.x + best.ny * p.y - best.rho) >= 2) continue;
      ts.push(p.x * dx + p.y * dy);
    }
    ts.sort((a, b) => a - b);
    const t0 = ts[Math.floor(ts.length * 0.05)], t1 = ts[Math.floor(ts.length * 0.95)];
    best.ends = [{ x: best.nx * best.rho + dx * t0, y: best.ny * best.rho + dy * t0 },
      { x: best.nx * best.rho + dx * t1, y: best.ny * best.rho + dy * t1 }];
    lines.push(best);
    rest = rest.filter((p) => Math.abs(best.nx * p.x + best.ny * p.y - best.rho) >= 2);
  }
  // Refit direction on inliers is skipped: 2px tolerance already absorbs the
  // sampling jitter, and the lattice check owns final accuracy.
  return lines;
}

function lineIntersect(l1, l2) {
  const d = l1.nx * l2.ny - l2.nx * l1.ny;
  if (Math.abs(d) < 1e-6) return null;
  return { x: (l1.rho * l2.ny - l2.rho * l1.ny) / d, y: (l1.nx * l2.rho - l2.nx * l1.rho) / d };
}

// Relative-motion track filter: first lock sets the reference; output is the
// smoothed rotation SINCE lock, so the twin idles flat and follows motion.
// Each frame the hypothesis closest to the running pose wins (continuity
// resolves the 180-degree twin). Jumps and dropouts freeze the output
// instead of inventing movement.
function makeTrackFilter(opts) {
  opts = opts || {};
  const alpha = opts.alpha || 0.35, maxJump = opts.maxJump || 0.6;
  let ref = null, smooth = null;
  const wrap = (a, b) => {
    let d = a - b;
    while (d > Math.PI) d -= 2 * Math.PI;
    while (d < -Math.PI) d += 2 * Math.PI;
    return b + d;
  };
  return {
    update(hyps) {
      if (!hyps || !hyps.length) return { state: 'lost', euler: smooth };
      if (!ref) {
        // First lock: strongest lattice proof wins; tie-break by low skew,
        // then toward frontal. (Legacy hyps without contrast keep the old
        // skew+frontal ordering.)
        const scored = hyps.map((hh) => {
          const e = rotationToEulerYXZ(hh.R);
          const proof = (typeof hh.contrast === 'number' ? hh.contrast : 0) * 20;
          return [hh.skew * 10 + Math.abs(e.rx) + Math.abs(e.ry) + Math.abs(e.rz) - proof, hh];
        }).sort((a, b) => a[0] - b[0]);
        ref = scored[0][1].R;
        smooth = { rx: 0, ry: 0, rz: 0 };
        return { state: 'lock', euler: smooth, hyp: scored[0][1] };
      }
      let best = null, bestD = Infinity, bestHyp = null;
      for (const hh of hyps) {
        const e = rotationToEulerYXZ(matMul3(hh.R, matTranspose3(ref)));
        const unwrapped = { rx: wrap(e.rx, smooth.rx), ry: wrap(e.ry, smooth.ry), rz: wrap(e.rz, smooth.rz) };
        const d = Math.abs(unwrapped.rx - smooth.rx)
          + Math.abs(unwrapped.ry - smooth.ry) + Math.abs(unwrapped.rz - smooth.rz);
        if (d < bestD) { bestD = d; best = unwrapped; bestHyp = hh; }
      }
      const jump = Math.max(Math.abs(best.rx - smooth.rx),
        Math.abs(best.ry - smooth.ry), Math.abs(best.rz - smooth.rz));
      if (jump > maxJump) return { state: 'jump', euler: smooth, hyp: bestHyp };
      smooth = { rx: smooth.rx + (best.rx - smooth.rx) * alpha,
        ry: smooth.ry + (best.ry - smooth.ry) * alpha,
        rz: smooth.rz + (best.rz - smooth.rz) * alpha };
      return { state: 'track', euler: smooth, hyp: bestHyp };
    },
    reset() { ref = null; smooth = null; },
  };
}

/* ----- hole-grid registration ----------------------------------------
   Rectifies the detected board quad to a top-down view, verifies the hole
   lattice against the known board dimensions, and refines the origin by
   contrast search. Model-driven (analytic hole positions), so a verified
   registration maps image points to holes with measured confidence. */
function invertHomography(H) {
  const [a, b, c, d, e, f, g, h, i] = H;
  const A = e * i - f * h, B = f * g - d * i, C = d * h - e * g;
  const det = a * A + b * B + c * C;
  if (Math.abs(det) < 1e-12) return null;
  return [A / det, (c * h - b * i) / det, (b * f - c * e) / det,
    B / det, (a * i - c * g) / det, (c * d - a * f) / det,
    C / det, (b * g - a * h) / det, (a * e - b * d) / det];
}

// Warp the quad region of lum to an outW x outH rectangle (bilinear).
// quad order: TL,TR,BR,BL in image coords.
function rectifyQuad(lum, w, h, quad, outW, outH) {
  const dst = [{ x: 0, y: 0 }, { x: outW, y: 0 }, { x: outW, y: outH }, { x: 0, y: outH }];
  const H = homography(dst, quad); // rect -> image
  if (!H) return null;
  const out = new Float32Array(outW * outH);
  for (let y = 0; y < outH; y++) for (let x = 0; x < outW; x++) {
    const p = applyHomography(H, x + 0.5, y + 0.5);
    const x0 = Math.floor(p.x), y0 = Math.floor(p.y);
    const fx = p.x - x0, fy = p.y - y0;
    const at = (xx, yy) => lum[Math.max(0, Math.min(h - 1, yy)) * w + Math.max(0, Math.min(w - 1, xx))];
    out[y * outW + x] = at(x0, y0) * (1 - fx) * (1 - fy) + at(x0 + 1, y0) * fx * (1 - fy)
      + at(x0, y0 + 1) * (1 - fx) * fy + at(x0 + 1, y0 + 1) * fx * fy;
  }
  return out;
}

// Mean darkness profile along one axis (holes read as peaks).
function profileDarkness(rect, rw, rh, axis) {
  let base = 0;
  for (let i = 0; i < rect.length; i++) base += rect[i];
  base /= rect.length;
  if (axis === 'x') {
    const prof = new Array(rw).fill(0);
    for (let y = 0; y < rh; y++) for (let x = 0; x < rw; x++) prof[x] += base - rect[y * rw + x];
    return prof.map((v) => v / rh);
  }
  const prof = new Array(rh).fill(0);
  for (let y = 0; y < rh; y++) for (let x = 0; x < rw; x++) prof[y] += base - rect[y * rw + x];
  return prof.map((v) => v / rw);
}

// Dominant period of a profile via autocorrelation, searched in [minP, maxP].
function autocorrPitch(prof, minP, maxP) {
  let mean = 0;
  for (const v of prof) mean += v;
  mean /= prof.length;
  let best = minP, bestV = -Infinity;
  for (let lag = minP; lag <= Math.min(maxP, (prof.length / 2) | 0); lag++) {
    let s = 0;
    for (let i = 0; i + lag < prof.length; i++) s += (prof[i] - mean) * (prof[i + lag] - mean);
    if (s > bestV) { bestV = s; best = lag; }
  }
  return { pitch: best, strength: bestV };
}

// Fit comb origin: given pitch and expected count, find phase 0<=o<pitch
// maximizing profile energy at o + k*pitch.
function fitCombOrigin(prof, pitch, count) {
  let bestO = 0, bestE = -Infinity;
  for (let o = 0; o < pitch; o++) {
    let e = 0;
    for (let k = 0; k < count; k++) {
      const i = Math.round(o + k * pitch);
      if (i >= 0 && i < prof.length) e += prof[i];
    }
    if (e > bestE) { bestE = e; bestO = o; }
  }
  return { origin: bestO, energy: bestE };
}

// Contrast of expected hole centers vs midpoints: mean(dark(at holes) -
// dark(between holes)), normalized by image std. Positive = lattice present.
function registrationContrast(rect, rw, rh, holeXYs) {
  let mean = 0;
  for (let i = 0; i < rect.length; i++) mean += rect[i];
  mean /= rect.length;
  let sd = 0;
  for (let i = 0; i < rect.length; i++) sd += (rect[i] - mean) * (rect[i] - mean);
  sd = Math.sqrt(sd / rect.length) || 1;
  let on = 0, off = 0, n = 0;
  for (const [x, y, px, py] of holeXYs) {
    const xi = Math.round(x), yi = Math.round(y);
    if (xi < 0 || yi < 0 || xi >= rw || yi >= rh) continue;
    on += mean - rect[yi * rw + xi];
    const mx = Math.round(x + px / 2), my = Math.round(y + py / 2);
    if (mx >= 0 && my >= 0 && mx < rw && my < rh) off += mean - rect[my * rw + mx];
    n++;
  }
  if (!n) return 0;
  return (on - off) / n / sd;
}

// Best rect-space similarity (shift ±6px, scale 0.94–1.06) aligning the
// expected holes with the lattice. step=1 is the fine grid; step=2 the
// coarse grid used inside edge descent. Returns {contrast, du, dv, ds}.
function fitLatticeSimilarity(rect, RW, RH, holes, step) {
  const base = holes.map(([x, y]) => [x, y]);
  const px = holes.length ? holes[0][2] : 5, py = holes.length ? holes[0][3] : 5;
  const cx = RW / 2, cy = RH / 2;
  const st = step || 1;
  const dsStep = st === 1 ? 0.02 : 0.04;
  let best = registrationContrast(rect, RW, RH, holes);
  let du = 0, dv = 0, ds = 1;
  for (let s = 0.94; s <= 1.0601; s += dsStep) {
    for (let yy = -6; yy <= 6; yy += st) {
      for (let xx = -6; xx <= 6; xx += st) {
        const moved = base.map(([x, y]) => [cx + (x - cx) * s + xx, cy + (y - cy) * s + yy, px, py]);
        const c = registrationContrast(rect, RW, RH, moved);
        if (c > best) { best = c; du = xx; dv = yy; ds = s; }
      }
    }
  }
  return { contrast: best, du, dv, ds };
}

// Per-edge coordinate descent on the quad (lum pixels), maximizing a coarse
// lattice fit. Fixes perspective edge errors a similarity cannot. Bounded:
// 2 rounds x 4 edges x 9 offsets. Returns {quad, contrast}.
function refineQuadEdges(lum, w, h, quad, holes, RW, RH) {
  const coarse = (rect) => fitLatticeSimilarity(rect, RW, RH, holes, 2).contrast;
  let cur = quad.map((p) => ({ x: p.x, y: p.y }));
  const startRect = rectifyQuad(lum, w, h, cur, RW, RH);
  if (!startRect) return null;
  let score = coarse(startRect);
  for (let round = 0; round < 2; round++) {
    let moved = false;
    for (let e = 0; e < 4; e++) {
      const a = cur[e], b = cur[(e + 1) % 4];
      const dx = b.x - a.x, dy = b.y - a.y;
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      let best = score, bestOff = 0;
      for (let off = -10; off <= 10; off += 2) {
        if (!off) continue;
        const q2 = cur.map((p, i) => (i === e || i === (e + 1) % 4)
          ? { x: p.x + nx * off, y: p.y + ny * off } : { x: p.x, y: p.y });
        const rect = rectifyQuad(lum, w, h, q2, RW, RH);
        if (!rect) continue;
        const c = coarse(rect);
        if (c > best) { best = c; bestOff = off; }
      }
      if (bestOff) {
        cur = cur.map((p, i) => (i === e || i === (e + 1) % 4)
          ? { x: p.x + nx * bestOff, y: p.y + ny * bestOff } : p);
        score = best; moved = true;
      }
    }
    if (!moved) break;
  }
  return { quad: cur, contrast: score };
}

// Image-to-board registration core (pure: no DOM). lum/w/h is the working
// grayscale frame, quad its detected board corners in lum pixels, rot the
// detector's corner rotation. geom = {BW, BL, rows, holeXY, PITCH} with
// holeXY(holeId) -> {x, z} board mm. k scales lum pixels to the caller's
// target frame (bboxes map in target pixels). Rectifies the quad, verifies
// the hole lattice, and returns {H, conf} with H mapping board mm {x, y=z}
// to target pixels — or null when unverifiable (never faked).
function registerBoard(lum, w, h, quad, rot, geom, k) {
  try {
    if (!lum || !w || !h || !Array.isArray(quad) || quad.length !== 4 || !geom) return null;
    const BW = geom.BW, BL = geom.BL, rows = geom.rows;
    if (!(BW > 0) || !(BL > 0) || !(rows > 0) || typeof geom.holeXY !== 'function') return null;
    // Strong perspective (end-on views) foreshortens the lattice into
    // aliases that pass a naive contrast check with a garbage homography.
    // Opposite-edge ratios near 1 mean a flat view; below 0.55, abstain.
    const edgeLen = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    const er1 = Math.min(edgeLen(quad[0], quad[1]), edgeLen(quad[2], quad[3]))
      / (Math.max(edgeLen(quad[0], quad[1]), edgeLen(quad[2], quad[3])) || 1);
    const er2 = Math.min(edgeLen(quad[1], quad[2]), edgeLen(quad[3], quad[0]))
      / (Math.max(edgeLen(quad[1], quad[2]), edgeLen(quad[3], quad[0])) || 1);
    if (Math.min(er1, er2) < 0.55) return null;
    const PITCH = geom.PITCH || 2.54;
    const r = ((rot | 0) % 4 + 4) % 4;
    const RW = 104, RH = Math.round((RW * BL) / BW);
    const rect = rectifyQuad(lum, w, h, quad, RW, RH);
    if (!rect) return null;
    const rc = [[0, 0], [RW, 0], [RW, RH], [0, RH]];
    const C = [0, 1, 2, 3].map((i) => rc[(i + r) % 4]);
    const at = (u, v) => [
      C[0][0] * (1 - u) * (1 - v) + C[1][0] * u * (1 - v) + C[2][0] * u * v + C[3][0] * (1 - u) * v,
      C[0][1] * (1 - u) * (1 - v) + C[1][1] * u * (1 - v) + C[2][1] * u * v + C[3][1] * (1 - u) * v];
    const px = (RW / BW) * PITCH, py = (RH / BL) * PITCH;
    const holes = [];
    for (let rr = 1; rr <= rows; rr += 3) for (const col of 'ABCDEFGHIJ') {
      const m = geom.holeXY('t' + rr + col);
      if (!m) continue;
      const [qx, qy] = at((m.x + BW / 2) / BW, (m.z + BL / 2) / BL);
      holes.push([qx, qy, px, py]);
    }
    let curQuad = quad;
    // Base similarity fit first, then per-edge descent, keeping the better
    // of the two. The search is deliberately LOCAL to the lock quad: widening
    // it (multi-start) was tried and disproven — alias fits outscore truth
    // (synthetic alias 4.05 vs truth 0.35; real-photo winners disagreed with
    // verified placements by 94-171px). (Cross-view agreement, not a stricter
    // single threshold, guards against aliased fits: see best-view placement.)
    let fit = fitLatticeSimilarity(rect, RW, RH, holes);
    const refined = refineQuadEdges(lum, w, h, curQuad, holes, RW, RH);
    if (refined) {
      const rect2 = rectifyQuad(lum, w, h, refined.quad, RW, RH);
      if (rect2) {
        const fit2 = fitLatticeSimilarity(rect2, RW, RH, holes);
        if (fit2.contrast > fit.contrast) {
          fit = fit2; curQuad = refined.quad;
        }
      }
    }
    const { contrast, du, dv, ds } = fit;
    if (!(contrast >= 0.3)) return null;
    const kk = k || 1;
    // Homography, corrected by the refinement: board corner -> rect (with
    // the fitted similarity) -> image, scaled to the target frame.
    const dst = [{ x: 0, y: 0 }, { x: RW, y: 0 }, { x: RW, y: RH }, { x: 0, y: RH }];
    const R = homography(dst, curQuad); // rect -> lum px
    if (!R) return null;
    const src = [{ x: -BW / 2, y: -BL / 2 }, { x: BW / 2, y: -BL / 2 },
      { x: BW / 2, y: BL / 2 }, { x: -BW / 2, y: BL / 2 }];
    const cc = [[0, 0], [1, 0], [1, 1], [0, 1]].map(([u, v]) => {
      const [qx, qy] = at(u, v);
      const p = applyHomography(R, RW / 2 + (qx - RW / 2) * ds + du, RH / 2 + (qy - RH / 2) * ds + dv);
      return { x: p.x * kk, y: p.y * kk };
    });
    const H = homography(src, cc);
    if (!H) return null;
    return { H, conf: Math.round(contrast * 100) / 100 };
  } catch (e) { return null; }
}

/* ----- scan progress (evidence-only, never time-based) ----------------
   pct blends frame coverage (seen sharply) with model completeness
   (identified + placed + wired + answered). 100 requires full coverage,
   zero unknowns, and at least one observation (or an explicit empty-board
   confirmation). Anything less caps at 99. */
function scanProgress(input) {
  const cov = Math.max(0, Math.min(1, input.coverage || 0));
  const comps = input.components || [], wires = input.wires || [];
  const openQ = input.openQuestions || 0;
  let pins = 0, placed = 0, unknownId = 0;
  for (const c of comps) {
    const total = c.pinsTotal || 0;
    pins += total; placed += Math.min(total, c.pinsPlaced || 0);
    if (!c.identified) unknownId++;
  }
  let wireEnds = 0, wireSet = 0;
  for (const w of wires) { wireEnds += 2; wireSet += (w.fromSet ? 1 : 0) + (w.toSet ? 1 : 0); }
  const fracs = [];
  if (pins) fracs.push(placed / pins);
  if (comps.length) fracs.push((comps.length - unknownId) / comps.length);
  if (wireEnds) fracs.push(wireSet / wireEnds);
  const items = comps.length + wires.length;
  const model = fracs.length ? fracs.reduce((a, b) => a + b, 0) / fracs.length
    : (input.emptyConfirmed ? 1 : 0);
  const unresolved = (pins - placed) + unknownId + (wireEnds - wireSet) + openQ;
  let pct = Math.round(100 * (0.35 * cov + 0.65 * model));
  const complete = unresolved === 0 && cov >= 0.95 && (items > 0 || !!input.emptyConfirmed);
  if (!complete) pct = Math.min(pct, 99);
  if (complete) pct = 100;
  return { pct, visual: cov, model, unresolved, complete };
}

// Board-mm x-span of a rail lane band: 4 mm inward of the inner lane
// (housings overhang toward the strips and cover the inner stripe) and
// 1.2 mm outside the outer lane (the outer stripe and board edge stay out).
function bandSpan(innerX, outerX) {
  const lo = Math.min(innerX, outerX), hi = Math.max(innerX, outerX);
  const s = (innerX + outerX) >= 0 ? 1 : -1; // side sign: R +1, L -1
  return s > 0 ? [lo - 4, hi + 1.2] : [lo - 1.2, hi + 4];
}

// Widest contiguous run darker than darkAt across [x0, x1] at board-mm z.
function bandRunWidth(lum, w, h, H, pxmm, darkAt, span, z) {
  const step = Math.max(0.2, 1 / Math.max(1, pxmm));
  let best = 0, cur = 0;
  for (let x = span[0]; x <= span[1] + 1e-6; x += step) {
    const p = applyHomography(H, x, z);
    const ix = Math.max(0, Math.min(w - 1, Math.round(p.x)));
    const iy = Math.max(0, Math.min(h - 1, Math.round(p.y)));
    if (lum[iy * w + ix] < darkAt) { cur += step; if (cur > best) best = cur; }
    else cur = 0;
  }
  return best;
}

// Guided plug candidates: local dark-width maxima along one rail band
// inside a board-mm z-window (typically a vision wire bbox intersected with
// the band). Returns [{pos, width}] in 1-based hole numbers, strongest
// evidence first. Blind full-rail use also works but wire piles and rail
// gaps mimic housings there — callers should guide the window and treat
// candidates as hypotheses, never as confirmed holes.
function railCandidates(lum, w, h, H, innerX, outerX, z0, z1, pitch, rows, zRow, opts) {
  const o = opts || {};
  const darkAt = o.darkAt || 140;
  const widthMin = o.widthMin || 3.5;
  const widthOff = o.widthOff || 2.8;
  // A wire lying over rail holes reads housing-wide AT hole rows (the hole
  // span is 3.65 mm) but only for the holes' ~1.4 mm dark length, so regions
  // must outlast that; real housings span 2.5+ mm along z.
  const regionMin = o.regionMin || 2.0;
  const p0 = applyHomography(H, 0, 0), p1 = applyHomography(H, pitch, 0);
  const pxmm = Math.max(1, Math.hypot(p1.x - p0.x, p1.y - p0.y) / pitch);
  const span = bandSpan(innerX, outerX);
  const step = 0.5;
  const out = [];
  let region = null;
  const close = () => {
    if (!region) return;
    if (region.peak >= widthMin && region.z1 - region.z0 >= regionMin) {
      const pos = Math.max(1, Math.min(rows, Math.round(region.peakZ / pitch + (rows + 1) / 2)));
      out.push({ pos, width: region.peak });
    }
    region = null;
  };
  // Peak is the FIRST maximum: plateau middles drift into wire piles on
  // real rails, while edge-picking stays within a hole of housing centers.
  for (let z = z0; z <= z1 + 1e-6; z += step) {
    const wd = bandRunWidth(lum, w, h, H, pxmm, darkAt, span, z);
    const on = region ? wd >= widthOff : wd >= widthMin;
    if (!on) { close(); continue; }
    if (!region) region = { z0: z, z1: z, peak: wd, peakZ: z };
    else {
      region.z1 = z;
      if (wd > region.peak) { region.peak = wd; region.peakZ = z; }
    }
  }
  close();
  out.sort((a, b) => b.width - a.width);
  return out;
}

/* ----- partial-board end registration ----------------------------------
   Full-board registration needs the whole quad; close-ups and end-on views
   legitimately never show one. This fitter registers those views from the
   opposite direction: comb the two power-rail hole lines, find the visible
   board end (or a component landmark when no end shows), anchor absolute
   rows from the printed numerals + a calibrated end margin, solve the
   homography by least-squares DLT, and verify with the same lattice,
   anisotropy, rail-width and stripe gates as the full path. Every anchor
   input is measured per project at runtime (margins from a locked full
   view, digit phase, stripe colors); nothing about a specific board is
   assumed. Ambiguous views abstain with a reason instead of guessing. */

// Nearest-pixel sample along a segment; returns n luminance values.
function sampleLine(lum, w, h, x0, y0, x1, y1, n) {
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    const t = n === 1 ? 0 : i / (n - 1);
    const x = Math.max(0, Math.min(w - 1, Math.round(x0 + (x1 - x0) * t)));
    const y = Math.max(0, Math.min(h - 1, Math.round(y0 + (y1 - y0) * t)));
    out[i] = lum[y * w + x];
  }
  return out;
}

// Working coordinates with U along the board's long axis (PCA of the bright
// mask), V across it. at(u,v) samples luminance; lineU(v,n)/lineV(u,n)
// sample full cross-sections.
function boardAxis(lum, w, h) {
  try {
    // Cleaned board mask: smooth-bright pixels, morphologically closed, then
    // the largest connected blob. Wires/keys/texture fall out as fragments
    // while the board body dominates. (No border-flood: partial boards
    // legitimately touch the frame edge, which flood would erase.)
    const t = otsuThreshold(lum);
    const vari = localVariance(lum, w, h, 4);
    // 0.8x the Otsu level: shadowed-but-visible board (vignetting, wire
    // shadows) still masks in; the variance gate keeps texture out and the
    // largest blob keeps fragments out. Local scans use local contrast, so
    // the looser mask only widens the span, never the lattice verdict.
    const smooth = new Uint8Array(w * h);
    for (let i = 0; i < w * h; i++) smooth[i] = (lum[i] > t * 0.8 && vari[i] <= 100) ? 1 : 0;
    const mask = largestComponent(boxDilate(boxErode(smooth, w, h, 1), w, h, 2), w, h);
    // Bright-mask bounding box per axis: restricts every later scan to the
    // board's own span so background (table weave, keys) cannot contribute
    // periodicity, false combs, or fake edges.
    let x0 = w, x1 = -1, y0 = h, y1 = -1;
    for (let y = 0; y < h; y += 2) for (let x = 0; x < w; x += 2) {
      if (mask[y * w + x]) {
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
    }
    if (x1 < x0 || y1 < y0) return null;
    let n = 0, sx = 0, sy = 0;
    for (let y = 0; y < h; y += 3) for (let x = 0; x < w; x += 3) {
      if (mask[y * w + x]) { n++; sx += x; sy += y; }
    }
    if (n < 200) return null;
    const cx = sx / n, cy = sy / n;
    let sxx = 0, sxy = 0, syy = 0;
    for (let y = 0; y < h; y += 3) for (let x = 0; x < w; x += 3) {
      if (mask[y * w + x]) { const dx = x - cx, dy = y - cy; sxx += dx * dx; sxy += dx * dy; syy += dy * dy; }
    }
    const tr = sxx + syy, det = sxx * syy - sxy * sxy;
    const disc = Math.sqrt(Math.max(0, (tr * tr) / 4 - det));
    const l1 = tr / 2 + disc, l2 = tr / 2 - disc;
    if (!(l1 > 1e-9) || l2 / l1 > 0.45) return null; // not elongated: no axis
    let vx = sxy, vy = l1 - sxx;
    if (vx * vx + vy * vy < 1e-9) { vx = l1 - syy; vy = sxy; }
    if (vx * vx + vy * vy < 1e-9) return null;
    const alongX = Math.abs(vx) >= Math.abs(vy);
    const Lu = alongX ? w : h, Lv = alongX ? h : w;
    const uLo = alongX ? Math.max(0, x0 - 2) : Math.max(0, y0 - 2);
    const uHi = alongX ? Math.min(w - 1, x1 + 2) : Math.min(h - 1, y1 + 2);
    const at = (u, v) => {
      const x = alongX ? u : v, y = alongX ? v : u;
      const xi = Math.max(0, Math.min(w - 1, Math.round(x)));
      const yi = Math.max(0, Math.min(h - 1, Math.round(y)));
      return lum[yi * w + xi];
    };
    const toXY = (u, v) => (alongX ? { x: u, y: v } : { x: v, y: u });
    return { alongX, Lu, Lv, cx, cy, thresh: t, at, toXY, uLo, uHi };
  } catch (e) { return null; }
}

// Mean darkness profile along U (middle 80% of V) plus the global mean.
function axisDarkProfile(lum, ax) {
  const { Lu, Lv, at } = ax;
  let base = 0, bn = 0;
  for (let i = 0; i < lum.length; i += 7) { base += lum[i]; bn++; }
  base /= Math.max(1, bn);
  const v0 = Math.max(0, Math.round(Lv * 0.1)), v1 = Math.min(Lv - 1, Math.round(Lv * 0.9));
  const prof = new Array(Lu);
  for (let u = 0; u < Lu; u++) {
    let s = 0, c = 0;
    for (let v = v0; v <= v1; v += 2) { s += base - at(u, v); c++; }
    prof[u] = s / Math.max(1, c);
  }
  return { prof, base };
}

// Normalized periodicity score of a 1D line in [minP, maxP] (autocorrelation
// peak over variance). Strong regular holes score high; wires, modules and
// texture score low.
function normPeriod(line, minP, maxP) {
  try {
    const { pitch, strength } = autocorrPitch(line, minP, maxP);
    let mean = 0;
    for (const v of line) mean += v;
    mean /= Math.max(1, line.length);
    let vari = 0;
    for (const v of line) vari += (v - mean) * (v - mean);
    if (!(vari > 1e-9)) return { pitch, score: 0 };
    return { pitch, score: strength / vari };
  } catch (e) { return { pitch: minP, score: 0 }; }
}

// Least-squares DLT homography from N>=4 board-mm -> image correspondences
// (fixes h[8]=1). Null when underdetermined or degenerate.
function dltHomography(pairs) {
  try {
    if (!Array.isArray(pairs) || pairs.length < 4) return null;
    const A = [], b = [];
    for (const p of pairs) {
      if (!p || !isFinite(p.mx) || !isFinite(p.my) || !isFinite(p.px) || !isFinite(p.py)) continue;
      const { mx, my, px, py } = p;
      A.push([-mx, -my, -1, 0, 0, 0, px * mx, px * my]); b.push(-px);
      A.push([0, 0, 0, -mx, -my, -1, py * mx, py * my]); b.push(-py);
    }
    if (A.length < 8) return null;
    const AtA = [], Atb = new Array(8).fill(0);
    for (let r = 0; r < 8; r++) {
      AtA.push(new Array(8).fill(0));
      for (let c = 0; c < 8; c++) {
        let s = 0;
        for (let k = 0; k < A.length; k++) s += A[k][r] * A[k][c];
        AtA[r][c] = s;
      }
      let s = 0;
      for (let k = 0; k < A.length; k++) s += A[k][r] * b[k];
      Atb[r] = s;
    }
    const h = solveLinear(AtA, Atb);
    if (!h || h.some((v) => !isFinite(v))) return null;
    return h.concat(1);
  } catch (e) { return null; }
}

// 1D projective fit u = (a*r + b)/(c*r + 1) for labeled (row, u) pairs.
// Used to score anchor hypotheses: the true anchor predicts every comb hole.
function fitProjective1D(labeled) {
  try {
    if (!Array.isArray(labeled) || labeled.length < 3) return null;
    const A = [], b = [];
    for (const [r, u] of labeled) {
      A.push([r, 1, -u * r]); b.push(u);
    }
    const AtA = [[0, 0, 0], [0, 0, 0], [0, 0, 0]], Atb = [0, 0, 0];
    for (let k = 0; k < A.length; k++) {
      for (let i = 0; i < 3; i++) {
        Atb[i] += A[k][i] * b[k];
        for (let j = 0; j < 3; j++) AtA[i][j] += A[k][i] * A[k][j];
      }
    }
    const x = solveLinear(AtA, Atb);
    if (!x || x.some((v) => !isFinite(v))) return null;
    return { a: x[0], b: x[1], c: x[2] };
  } catch (e) { return null; }
}
function project1D(f, r) {
  const d = f.c * r + 1;
  return Math.abs(d) > 1e-9 ? (f.a * r + f.b) / d : NaN;
}

// Axis-scale ratio of a homography at board center (same math as the
// browser's lockAnisotropyOk gate, pure so tests and the fitter share it).
function axisScaleRatio(H) {
  try {
    if (!Array.isArray(H) || H.length < 9) return Infinity;
    const w = H[8] || 1;
    const ax = (H[0] - H[6] * H[2] / w) / w, ay = (H[1] - H[7] * H[2] / w) / w;
    const zx = (H[3] - H[6] * H[5] / w) / w, zy = (H[4] - H[7] * H[5] / w) / w;
    const sx = Math.hypot(ax, ay), sz = Math.hypot(zx, zy);
    if (!isFinite(sx) || !isFinite(sz) || sx <= 0 || sz <= 0) return Infinity;
    return Math.max(sx, sz) / Math.min(sx, sz);
  } catch (e) { return Infinity; }
}

// Field-lattice verification in source pixels: every visible field hole must
// be darker than its own plastic annulus (same z-test as latticeScores, but
// projected directly through H so partial views verify at full resolution).
// Returns {score, n}; n counts in-frame holes.
function fieldLatticeScore(lum, w, h, H, holeXY, r0, r1) {
  try {
    let pass = 0, n = 0;
    const cols = 'ABCDEFGHIJ';
    for (let r = r0; r <= r1; r++) {
      for (let ci = 0; ci < 10; ci++) {
        const m = holeXY('t' + r + cols[ci]);
        if (!m || !isFinite(m.x) || !isFinite(m.z)) continue;
        const p = applyHomography(H, m.x, m.z);
        const xi = Math.round(p.x), yi = Math.round(p.y);
        if (xi < 5 || yi < 5 || xi >= w - 5 || yi >= h - 5) continue;
        let c = 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) c += lum[(yi + dy) * w + xi + dx];
        c /= 9;
        let s1 = 0, s2 = 0, m2 = 0;
        for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
          if (Math.abs(dx) <= 1 && Math.abs(dy) <= 1) continue;
          const vv = lum[(yi + dy) * w + xi + dx];
          s1 += vv; s2 += vv * vv; m2++;
        }
        const mu = s1 / m2, sd = Math.sqrt(Math.max(0, s2 / m2 - mu * mu));
        if ((mu - c) / Math.max(4, sd) > 2.5) pass++;
        n++;
      }
    }
    if (!n) return { score: 0, n: 0 };
    return { score: pass / n, n };
  } catch (e) { return { score: 0, n: 0 }; }
}

// Rail-band across-positions: per-half pitch first (cheap global autocorrs,
// so foreshortened far halves keep their own pitch), then each V line is
// scored by comb energy at the fixed per-half pitch. Keeps the outermost
// lines above 0.4x the peak. Returns [{v, pitch}].
function railBandLines(lum, ax) {
  try {
    const { Lu, Lv, at } = ax;
    const rLo = Math.max(0, Math.floor(ax.uLo || 0));
    const rHi = Math.min(Lu - 1, Math.ceil(ax.uHi == null ? Lu - 1 : ax.uHi));
    if (rHi - rLo < 40) return null;
    const mid = Math.floor((rLo + rHi) / 2);
    const segDark = (v, u0, u1) => {
      const n = Math.max(8, Math.floor((u1 - u0) / 2));
      const out = new Array(n);
      for (let i = 0; i < n; i++) {
        const u = u0 + Math.round((i / Math.max(1, n - 1)) * (u1 - u0));
        out[i] = at(u, v);
      }
      let base = 0;
      for (const x of out) base += x;
      base /= Math.max(1, out.length);
      return out.map((x) => base - x);
    };
    const pitchOf = (u0, u1) => {
      // Rows can sit in the groove (flat) or under a wire (one huge dark
      // plateau drowns the holes): clip plateaus, then take the MEDIAN
      // pitch across rows so minority-corrupted rows cannot outvote.
      const ps = [];
      for (const fv of [0.22, 0.3, 0.38, 0.46, 0.54, 0.62, 0.7, 0.78]) {
        const v = Math.round(Lv * fv);
        if (v < 1 || v >= Lv - 1) continue;
        const mid = segDark(v, u0, u1);
        ps.push(detrendPeriod(mid));
      }
      if (!ps.length) return 8;
      ps.sort((a, b) => a - b);
      return Math.max(3, Math.min(120, ps[Math.floor(ps.length / 2)]));
    };
    // Detrended dominant period: smooth(3) + first-difference kills steps
    // and vignetting gradients that otherwise make lag 2 always win; the
    // hole peak-train survives differencing and still peaks at its period.
    const detrendPeriod = (line) => {
      const sm = line.map((x, i) => {
        const a = line[Math.max(0, i - 1)], b = line[Math.min(line.length - 1, i + 1)];
        return (a + x + b) / 3;
      });
      const d = [];
      for (let i = 0; i + 1 < sm.length; i++) d.push(sm[i + 1] - sm[i]);
      const ap = autocorrPitch(d, 2, 60);
      return ap.pitch * 2; // u was 2x-downsampled
    };
    const pA = pitchOf(rLo, mid), pB = pitchOf(mid, rHi);
    const scored = [];
    for (let v = 2; v < Lv - 2; v += 3) {
      const la = segDark(v, rLo, mid), lb = segDark(v, mid, rHi);
      const ca = Math.max(2, Math.round(la.length / Math.max(2, pA / 2)));
      const cb = Math.max(2, Math.round(lb.length / Math.max(2, pB / 2)));
      const ea = fitCombOrigin(la, pA / 2, ca).energy / ca;
      const eb = fitCombOrigin(lb, pB / 2, cb).energy / cb;
      scored.push({ v, score: (ea + eb) / 2, pitch: (pA + pB) / 2 });
    }
    if (!scored.length) return null;
    let peak = 0;
    for (const s of scored) peak = Math.max(peak, s.score);
    if (!(peak > 0)) return null;
    const good = scored.filter((s) => s.score >= 0.4 * peak);
    if (good.length < 2) return null;
    good.sort((a, b) => a.v - b.v);
    // All periodic lines (rail lanes + field rows); cluster into lines.
    const lines = [];
    for (const g of good) {
      const last = lines[lines.length - 1];
      if (last && g.v - last.v <= 5) {
        if (g.score > last.score) lines[lines.length - 1] = g;
      } else lines.push(g);
    }
    if (lines.length < 4) return { bands: null, lines };
    const P = (lines[0].pitch + lines[lines.length - 1].pitch) / 2;
    // Rail pairs by comb verification: candidate (outer, inner) line pairs
    // near each board end are combed, and the first pair whose lanes both
    // yield >=5 holes agreeing on rows wins. Stripes, edges and texture
    // fail the comb; true lane pairs pass. Tilt only shrinks V gaps, so a
    // candidate wider than 1.6 pitches cannot be a lane pair.
    const prLo = Math.max(0, Math.floor(ax.uLo || 0));
    const prHi = Math.min(ax.Lu - 1, Math.ceil(ax.uHi == null ? ax.Lu - 1 : ax.uHi));
    const tryPair = (outer, inner) => {
      if (!outer || !inner || Math.abs(inner.v - outer.v) > 1.6 * P) return null;
      const ho = combULine(ax, Math.round(outer.v), prLo, prHi, P);
      if (ho.length < 5) return null;
      const hi = combULine(ax, Math.round(inner.v), prLo, prHi, P);
      if (hi.length < 5) return null;
      let agree = 0;
      for (const u of ho) {
        let bd = Infinity;
        for (const v of hi) { const d = Math.abs(v - u); if (d < bd) bd = d; }
        if (bd < P * 0.35) agree++;
      }
      if (agree / ho.length < 0.6) return null;
      return { v: (outer.v + inner.v) / 2, lanes: [outer.v, inner.v], pitch: (outer.pitch + inner.pitch) / 2 };
    };
    const findPair = (cands) => {
      for (const pr of cands) {
        const hit = tryPair(pr[0], pr[1]);
        if (hit) return hit;
      }
      return null;
    };
    const n = lines.length;
    const topC = [[lines[0], lines[1]]];
    if (lines[2]) topC.push([lines[1], lines[2]]);
    const botC = [[lines[n - 1], lines[n - 2]]];
    if (lines[n - 3]) botC.push([lines[n - 2], lines[n - 3]]);
    const top = findPair(topC), bot = findPair(botC);
    if (!top || !bot || bot.v - top.v < 8) return { bands: null, lines, top, bot };
    return { bands: [top, bot], lines };
  } catch (e) { return { bands: null, lines: [] }; }
}

// Windowed 1D comb along U at a fixed V: overlapping 6-pitch windows with
// local stats (robust to wire shadows and vignetting), merged with
// min-separation dedupe keeping the darker. Returns U positions.
function combULine(ax, laneV, rLo, rHi, pitchPx) {
  const dark = [];
  for (let u = rLo; u <= rHi; u++) {
    let s = 0, c = 0;
    for (let dv = -1; dv <= 1; dv++) s += ax.at(u, laneV + dv), c++;
    dark.push(s / c);
  }
  let base = 0;
  for (const x of dark) base += x;
  base /= Math.max(1, dark.length);
  const line = dark.map((x) => base - x);
  const winLen = Math.max(16, Math.round(pitchPx * 6));
  const winStep = Math.max(8, Math.round(winLen / 2));
  const raw = [];
  for (let w0 = 0; w0 < line.length; w0 += winStep) {
    const seg = line.slice(w0, w0 + winLen);
    if (seg.length < pitchPx * 2) continue;
    const found = combMinima(seg, pitchPx);
    for (const f of found) raw.push(f + w0);
  }
  raw.sort((a, b) => a - b);
  const out = [];
  for (const c of raw) {
    const last = out[out.length - 1];
    if (last !== undefined && c - last < pitchPx * 0.55) {
      if (line[c] > line[last]) out[out.length - 1] = c;
    } else out.push(c);
  }
  return out.map((ui) => ui + rLo);
}

// Board-end edge along U past the end-most comb hole: scans outward for the
// bright-plastic -> dark-background transition. Returns {u} in U coords, or
// {offFrame:true} when plastic runs to the frame border (no end visible).
function findEndEdge(ax, uFrom, outward, pitchPx) {
  try {
    const { Lu, Lv, at, thresh } = ax;
    const rLo = Math.max(0, Math.floor(ax.uLo == null ? 0 : ax.uLo));
    const rHi = Math.min(Lu - 1, Math.ceil(ax.uHi == null ? Lu - 1 : ax.uHi));
    const span = Math.max(8, Math.round(pitchPx * 3));
    // The board leaves the frame on this side: no end edge to find.
    if ((outward < 0 && rLo <= 2) || (outward > 0 && rHi >= Lu - 3)) return { offFrame: true };
    let lastBright = uFrom;
    for (let k = 1; k <= span; k++) {
      const u = Math.round(uFrom + outward * k);
      if (u < rLo || u > rHi) break;
      let bright = 0, c = 0;
      for (let v = Math.round(Lv * 0.25); v < Math.round(Lv * 0.75); v += 3) {
        if (at(u, v) > thresh) bright++;
        c++;
      }
      if (bright / Math.max(1, c) > 0.45) lastBright = u;
    }
    if (Math.abs(lastBright - uFrom) >= span - 1) return { offFrame: true };
    return { u: lastBright + outward * 0.5 };
  } catch (e) { return null; }
}

// Numeral-strip search: the printed row numbers sit between each rail pair
// and the field, but the exact offset varies with perspective, so the strip
// is FOUND, not assumed — scan candidate rows from the inner lane toward
// the board center and keep the row whose dark clusters repeat every five
// pitches. Returns {digits:[U-centers], stripV:[v0, v1]}.
function findNumeralStrip(ax, bands, allLines, pitchPx, endEdges, endExclPx) {
  try {
    const { at } = ax;
    void allLines;
    const rLo = Math.max(0, Math.floor(ax.uLo || 0));
    const rHi = Math.min(ax.Lu - 1, Math.ceil(ax.uHi == null ? ax.Lu - 1 : ax.uHi));
    const all = [];
    const stripV = [];
    for (const [bi, b] of bands.entries()) {
      const inward = bi === 0 ? 1 : -1; // toward board center
      const inner = b.lanes[1];
      let bestV = null, bestScore = -1, bestC = [];
      for (let off = pitchPx * 0.3; off <= pitchPx * 2.6; off += Math.max(1, pitchPx * 0.25)) {
        const v = Math.round(inner + inward * off);
        const line = [];
        for (let u = rLo; u <= rHi; u++) line.push(at(u, v));
        let base = 0;
        for (const x of line) base += x;
        base /= Math.max(1, line.length);
        let sd = 0;
        for (const x of line) sd += (x - base) * (x - base);
        sd = Math.sqrt(sd / Math.max(1, line.length));
        const centers = [];
        let run = -1;
        for (let u = 0; u < line.length; u++) {
          if (line[u] < base - 1.6 * Math.max(1, sd)) { if (run < 0) run = u; }
          else {
            if (run >= 0 && u - run >= 1 && u - run <= pitchPx * 0.3) centers.push(run + (u - run) / 2 + rLo);
            run = -1;
          }
        }
        if (centers.length < 2) continue;
        // Score: fraction of clusters with a partner ~5 pitches away, gated
        // by sparsity. Hole-rim rows also repeat every 5 pitches, but they
        // fire at (nearly) every pitch; numerals fire ~once per 5 pitches.
        let paired = 0;
        for (const c of centers) {
          for (const d of centers) {
            const gap = Math.abs(d - c) / pitchPx;
            if (gap >= 4 && gap <= 6) { paired++; break; }
          }
        }
        // Sparsity over the CLUSTER span (not the comb span): hole-rim rows
        // fire at nearly every pitch; numerals fire ~once per 5 pitches.
        const cLo = centers[0], cHi = centers[centers.length - 1];
        const spanP = Math.max(2, (cHi - cLo) / pitchPx);
        const density = centers.length / spanP;
        if (density > 0.5) continue; // hole-rim row, not print
        // Local crowding: a numeral run never puts >3 clusters inside any
        // 6-pitch window (doubles included); hole rims fire every pitch.
        let crowded = false;
        for (let i = 0; i < centers.length && !crowded; i++) {
          let n = 0;
          for (let j = i; j < centers.length; j++) {
            if (centers[j] - centers[i] > pitchPx * 6) break;
            if (++n > 3) { crowded = true; break; }
          }
        }
        if (crowded) continue;
        const score = paired / centers.length + Math.min(0.5, centers.length / 12);
        if (score > bestScore) { bestScore = score; bestV = v; bestC = centers; }
      }
      if (bestV !== null && bestScore >= 0.5) {
        stripV.push(bestV);
        for (const c of bestC) {
          let excluded = false;
          for (const e of (endEdges || [])) {
            if (!e || e.offFrame) continue;
            if (Math.abs(c - e.u) < (endExclPx || 0)) { excluded = true; break; }
          }
          if (!excluded) all.push(c);
        }
      }
    }
    all.sort((a, b) => a - b);
    const merged = [];
    for (const c of all) {
      const last = merged[merged.length - 1];
      if (last !== undefined && c - last < Math.max(3, pitchPx * 0.5)) merged[merged.length - 1] = (last + c) / 2;
      else merged.push(c);
    }
    return { digits: merged, stripV };
  } catch (e) { return { digits: [], stripV: [] }; }
}

// Dark print clusters (numerals/letters) in the numeral strips: v centered
// stripOffPx inside each rail band, both sides. Returns U-centers sorted,
// excluding anything within endExclPx of a visible end edge (letters).
function digitBlobs(ax, bands, stripOffPx, endEdges, endExclPx) {
  try {
    const { Lu, at } = ax;
    const rLo = Math.max(0, Math.floor(ax.uLo || 0));
    const rHi = Math.min(Lu - 1, Math.ceil(ax.uHi == null ? Lu - 1 : ax.uHi));
    const blobs = [];
    for (const b of bands) {
      for (const side of [-1, 1]) {
        const v = Math.round(b.v + side * stripOffPx);
        const line = [];
        for (let u = rLo; u <= rHi; u += 1) line.push(at(u, v));
        let base = 0;
        for (const x of line) base += x;
        base /= Math.max(1, line.length);
        let sd = 0;
        for (const x of line) sd += (x - base) * (x - base);
        sd = Math.sqrt(sd / Math.max(1, line.length));
        let run = -1;
        const flush = (end) => {
          if (run >= 0 && end - run >= 2 && end - run <= Math.max(6, stripOffPx * 1.2)) {
            const c = (run + end) / 2 + rLo; // rebase to U coords
            let excluded = false;
            for (const e of (endEdges || [])) {
              if (!e || e.offFrame) continue;
              if (Math.abs(c - e.u) < (endExclPx || 0)) { excluded = true; break; }
            }
            if (!excluded) blobs.push(c);
          }
          run = -1;
        };
        for (let u = 0; u < line.length; u++) {
          if (line[u] < base - 1.6 * Math.max(1, sd)) { if (run < 0) run = u; }
          else flush(u);
        }
        flush(line.length);
      }
    }
    blobs.sort((a, b) => a - b);
    const merged = [];
    for (const c of blobs) {
      const last = merged[merged.length - 1];
      if (last !== undefined && c - last < Math.max(3, stripOffPx * 0.5)) merged[merged.length - 1] = (last + c) / 2;
      else merged.push(c);
    }
    return merged;
  } catch (e) { return []; }
}
// Outer-stripe color: median R-B along the outer lane of each rail band.
// Red (+) reads strongly positive; blue/gray lanes read near zero or
// negative. Null rgb skips the check (orientation then rests on geometry).
function stripeOuterColor(rgb, w, h, toXY, u0, u1, vOuter) {
  // Red/blue print signal along one rail's outer lane: median and 10th
  // percentile of (R-B). Vivid red reads strongly positive, blue strongly
  // negative, gray/beige near zero. The p10 uniformity leg rejects beige
  // gradients under warm light (a faded-but-real stripe stays warm along
  // the whole rail). Returns {med, p10} or null.
  try {
    if (!rgb) return null;
    const vals = [];
    const n = Math.max(4, Math.round(Math.abs(u1 - u0) / 4));
    for (let i = 0; i <= n; i++) {
      const u = u0 + ((u1 - u0) * i) / n;
      const p = toXY(Math.round(u), Math.round(vOuter));
      if (p.x < 0 || p.y < 0 || p.x >= w || p.y >= h) continue;
      const k = (p.y * w + p.x) * 3; // rgb is flat [r,g,b] triplets
      vals.push(rgb[k] - rgb[k + 2]);
    }
    if (vals.length < 3) return null;
    vals.sort((a, b) => a - b);
    return { med: vals[Math.floor(vals.length / 2)], p10: vals[Math.floor(vals.length * 0.1)] };
  } catch (e) { return null; }
}

// Calibrate partial-end anchors from a locked full-board view: end margins
// (hole-center to board-edge, mm) for the row-1 and row-63 ends, measured by
// scanning outward along both rail lanes through the lock H. All geometry
// comes from this board's own lock; nothing is assumed about its dimensions.
function calibratePartialEnd(lum, w, h, H, geom) {
  try {
    if (!lum || !w || !h || !H || !geom || !geom.holeXY) return null;
    const PITCH = geom.PITCH || 2.54, rows = geom.rows || 63;
    const railX = geom.railInner && geom.railOuter
      ? [geom.railInner, geom.railOuter] : [18.5, 20.75];
    let base = 0, bn = 0;
    for (let i = 0; i < lum.length; i += 11) { base += lum[i]; bn++; }
    base /= Math.max(1, bn);
    // One board edge (row end): scan outward from the end-most hole along the
    // ROW AXIS (H-projected, never radial) across many lanes. Windowed
    // bright-fraction ignores narrow interruptions (holes, letters, wires);
    // the edge is the last bright window before a sustained dark run.
    // Median over lanes; IQR > 6px means disagreement (occluded: unknown).
    const edgeMargin = (row, dir) => {
      const lanes = [-24, -railX[1], -railX[0], -14, -8, 0, 8, 14, railX[0], railX[1], 24];
      const dists = [], pxmms = [];
      for (const laneX of lanes) {
        const p0 = applyHomography(H, laneX, (row - (rows + 1) / 2) * PITCH);
        const pA = applyHomography(H, laneX, ((row - (rows + 1) / 2) + dir * 2) * PITCH);
        let dx = pA.x - p0.x, dy = pA.y - p0.y;
        const L = Math.hypot(dx, dy) || 1; dx /= L; dy /= L;
        const pxmm = Math.max(0.2, L / (2 * PITCH)); // px per mm
        const win = Math.max(6, Math.round(2 * PITCH * pxmm));
        const need = Math.max(12, Math.round(3 * PITCH * pxmm));
        const prof = [];
        for (let k = 0; k < 200; k += 1) {
          const x = Math.round(p0.x + dx * k), y = Math.round(p0.y + dy * k);
          if (x < 1 || y < 1 || x >= w - 1 || y >= h - 1) break;
          prof.push(lum[y * w + x] > base * 0.85 ? 1 : 0);
        }
        let edge = -1;
        for (let k = 0; k + win + need <= prof.length; k++) {
          let wb = 0;
          for (let j = 0; j < win; j++) wb += prof[k + j];
          if (wb / win < 0.5) continue;
          let dark = 0;
          for (let j = 0; j < need && k + win + j < prof.length; j++) {
            if (!prof[k + win + j]) dark++;
            else break;
          }
          if (dark >= need) { edge = k + win - 1; break; }
        }
        if (edge < 0) continue;
        dists.push(edge); pxmms.push(pxmm);
      }
      if (dists.length < 6) return null;
      const s = dists.slice().sort((a, b) => a - b);
      const q1 = s[Math.floor(s.length / 4)], q3 = s[Math.floor((3 * s.length) / 4)];
      if (q3 - q1 > 6) return null;
      const medD = s[Math.floor(s.length / 2)];
      const medPx = pxmms.slice().sort((a, b) => a - b)[Math.floor(pxmms.length / 2)];
      return medD / medPx; // px / (px/mm) = mm
    };
    const marginLo = edgeMargin(1, -1), marginHi = edgeMargin(rows, 1);
    if (!(marginLo > 0) || !(marginHi > 0)) return null;
    return { marginLo, marginHi, pxmmHint: 0, digitTol: 0.8 };
  } catch (e) { return null; }
}

// Largest dark blob in the field region (component bodies: modules read as
// one big dark mass). Returns {u0, u1} U-extent or null.
function fieldDarkBlob(lum, ax, vLo, vHi) {
  try {
    const { Lu, at } = ax;
    const bLo = Math.max(0, Math.floor(ax.uLo == null ? 0 : ax.uLo));
    const bHi = Math.min(Lu - 1, Math.ceil(ax.uHi == null ? Lu - 1 : ax.uHi));
    if (bHi - bLo < 10) return null;
    let base = 0, bn = 0;
    for (let u = bLo; u <= bHi; u += 3) for (let v = vLo; v <= vHi; v += 3) { base += at(u, v); bn++; }
    base /= Math.max(1, bn);
    const W = Math.ceil((bHi - bLo + 1) / 2), Hh = Math.ceil((vHi - vLo + 1) / 2);
    const mask = new Uint8Array(W * Hh);
    for (let y = 0; y < Hh; y++) for (let x = 0; x < W; x++) {
      if (at(bLo + x * 2, vLo + y * 2) < base * 0.72) mask[y * W + x] = 1;
    }
    const seen = new Uint8Array(W * Hh);
    let best = null;
    for (let i = 0; i < W * Hh; i++) {
      if (!mask[i] || seen[i]) continue;
      let q = [i], u0 = 1e9, u1 = -1e9, n = 0;
      seen[i] = 1;
      while (q.length) {
        const c = q.pop(); n++;
        const cx = c % W, cy = (c / W) | 0;
        if (bLo + cx * 2 < u0) u0 = bLo + cx * 2;
        if (bLo + cx * 2 > u1) u1 = bLo + cx * 2;
        const nb = [c - 1, c + 1, c - W, c + W];
        for (const d of nb) {
          if (d < 0 || d >= W * Hh || seen[d] || !mask[d]) continue;
          const dx = d % W;
          if (Math.abs(dx - cx) > 1) continue;
          seen[d] = 1; q.push(d);
        }
      }
      if (n >= 12 && (!best || n > best.n)) best = { u0, u1, n };
    }
    return best;
  } catch (e) { return null; }
}

// Hypothesis scoring: label comb holes under one anchor hypothesis, fit the
// 1D projective row->u map, and score digit/margin/landmark agreement.
// hyp = {endU:'lo'|'hi'|null, endIsRow1, loIsL, rEnd}.
// holes = [{u, lane:'inner'|'outer'|null, band:0|1}].
function scoreAnchorHyp(hyp, holes, digits, pitchPx, cal, landmarks, geom) {
  const PITCH = geom.PITCH || 2.54, rows = geom.rows || 63;
  const endU = hyp.endU === 'lo'
    ? Math.min(...holes.map((h) => h.u)) : Math.max(...holes.map((h) => h.u));
  // Primary anchor: the printed digits form a 5-pitch grid of unknown offset
  // C; the dense letter band at the board end fixes C absolutely. End-hole
  // labels are fragile under housings, so they are only a fallback.
  let labeled = null, consensus = 0;
  if (hyp.preLabeled && hyp.preLabeled.length >= 4) {
    // No end visible: rows interpolated from the module landmark span.
    const f = fitProjective1D(hyp.preLabeled);
    if (f) {
      labeled = {
        fit: f, miss: 0,
        span: [Math.min(...hyp.preLabeled.map((p) => p[1])), Math.max(...hyp.preLabeled.map((p) => p[1]))],
      };
    }
  }
  if (!labeled && hyp.letters && digits && digits.length >= 3) {
    const ds = digits.slice().sort((a, b) => a - b);
    // Lattice RANSAC: digits sit on a 5-pitch lattice of unknown phase, but
    // two-character numerals ("15") and occluded numerals break a naive
    // index→u fit. Hypothesize the lattice step from digit pairs, count
    // inliers, average duplicates per lattice node, then fit node→u. The
    // downstream C search absorbs the arbitrary lattice origin.
    let g = null;
    {
      const P5 = 5 * pitchPx;
      let bestInl = [];
      for (let i = 0; i < ds.length && !g; i++) {
        for (let j = i + 1; j < ds.length; j++) {
          const dist = Math.abs(ds[j] - ds[i]);
          const k = Math.round(dist / P5);
          if (k < 1 || k > 12) continue;
          const p5 = dist / k;
          if (Math.abs(p5 - P5) > P5 * 0.12) continue;
          const mOf = (d) => Math.round((d - ds[i]) / p5);
          const inl = ds.filter((d) => Math.abs(d - (ds[i] + mOf(d) * p5)) < pitchPx * 0.6);
          if (inl.length >= 3 && inl.length > bestInl.length) bestInl = inl;
        }
      }
      if (bestInl.length >= 3) {
        const byM = new Map();
        const p5best = (() => {
          // Re-derive step from the inlier pair with the widest separation.
          let bd = 0, bk = 1;
          for (const a of bestInl) for (const b of bestInl) {
            const dd = Math.abs(b - a), kk = Math.round(dd / P5);
            if (kk >= 1 && dd > bd) { bd = dd; bk = kk; }
          }
          return bd / Math.max(1, bk);
        })();
        const mOfB = (d) => Math.round((d - bestInl[0]) / p5best);
        for (const d of bestInl) {
          const m = mOfB(d);
          if (!byM.has(m)) byM.set(m, []);
          byM.get(m).push(d);
        }
        const pts = [...byM.entries()].map(([m, arr]) => [m, arr.reduce((a, b) => a + b, 0) / arr.length]);
        if (pts.length >= 3) g = fitProjective1D(pts);
      }
    }
    if (g) {
      // Row map for grid offset C: u = g(r/5 - C). The letter anchor
      // admits exactly one C (5-pitch spacing vs 1.5-pitch window).
      for (let C = -2; C <= 13 && !labeled; C++) {
        const d0 = 1 - g.c * C;
        if (Math.abs(d0) < 0.2) continue;
        const f = { a: (g.a / 5) / d0, b: (g.b - g.a * C) / d0, c: (g.c / 5) / d0 };
        const pred = project1D(f, hyp.letters.row);
        if (!isFinite(pred) || Math.abs(pred - hyp.letters.u) > pitchPx * 1.5) continue;
        labeled = { fit: f, miss: 0, span: [ds[0], ds[ds.length - 1]] };
      }
    }
  }
  if (!labeled && hyp.endU !== null && hyp.rEnd) {
    // Fallback: label holes near the end (fragile under housings).
    const dir = hyp.endU === 'lo' ? 1 : -1;
    for (let miss = 0; miss <= 8 && !labeled; miss++) {
      const cand = [];
      const sorted = holes.slice().sort((a, b) => hyp.endU === 'lo' ? a.u - b.u : b.u - a.u);
      for (let i = 0; i < Math.min(8, sorted.length); i++) {
        const row = hyp.rEnd + dir * (miss + i);
        if (row < 1 || row > rows) continue;
        cand.push([row, sorted[i].u]);
      }
      if (cand.length >= 3) {
        const f = fitProjective1D(cand);
        if (f) {
          let err = 0;
          for (const [r, u] of cand) err += Math.abs(project1D(f, r) - u);
          if (err / cand.length < pitchPx * 0.5) {
            labeled = {
              fit: f, miss,
              span: [Math.min(...cand.map((p) => p[1])), Math.max(...cand.map((p) => p[1]))],
            };
          }
        }
      }
    }
  }
  if (!labeled) return null;
  const rowOfU = (u) => {
    // invert the projective fit by search (monotonic in u over the board)
    let best = hyp.rEnd, be = Infinity;
    for (let r = 1; r <= rows; r++) {
      const e = Math.abs(project1D(labeled.fit, r) - u);
      if (e < be) { be = e; best = r; }
    }
    return { row: best, err: be };
  };
  // Digit phase: digits must sit on 5-multiple rows (within cal tolerance).
  // Trimmed mean (drop the worst tercile): a mislocated strip can corrupt
  // individual digits, but the majority still anchor the phase.
  let digitScore = 0.5, nDigits = 0;
  if (digits && digits.length >= 2) {
    const per = [];
    const span = labeled.span || [0, 1e9];
    for (const d of digits) {
      // Digits far outside the labeled span would test extrapolation, not
      // the hypothesis: only digits near the fitted region count.
      if (d < span[0] - pitchPx * 10 || d > span[1] + pitchPx * 10) continue;
      const { row, err } = rowOfU(d);
      if (err > pitchPx * 1.2) continue; // digit outside mapped span
      const dist5 = Math.abs(row / 5 - Math.round(row / 5)) * 5;
      per.push(1 - Math.min(1, dist5 / (cal && cal.digitTol ? cal.digitTol : 0.8)));
    }
    nDigits = per.length;
    if (nDigits >= 2) {
      per.sort((a, b) => a - b);
      const drop = Math.floor(per.length / 3);
      const kept = per.slice(drop);
      digitScore = kept.reduce((a, b) => a + b, 0) / Math.max(1, kept.length);
    } else return null; // digits present but unmapped: not this hypothesis
  }
  // Consensus: every comb hole must sit near its predicted row. Wrong-end
  // and wrong-geometry fits extrapolate wildly and collapse here, while a
  // row-shifted-but-geometrically-true fit still scores (digits separate
  // the shift absolutely).
  if (labeled) {
    let hit = 0;
    for (const hh of holes) {
      let be = Infinity;
      for (let r = 1; r <= rows; r++) {
        const e = Math.abs(project1D(labeled.fit, r) - hh.u);
        if (e < be) be = e;
      }
      if (be < pitchPx * 0.4) hit++;
    }
    consensus = hit / Math.max(1, holes.length);
  }
  // Margin: edge-to-first-hole distance vs calibration (weak: both sides
  // carry ~2mm of method bias, so this only vetoes gross mismatches).
  let marginScore = 0.5, marginErr = null;
  if (hyp.edgeU !== undefined && cal && isFinite(cal.marginLo)) {
    const edgeDistPx = Math.abs(hyp.edgeU - endU);
    const edgeDistMm = (edgeDistPx / pitchPx) * PITCH;
    const want = hyp.endIsRow1 ? cal.marginLo : cal.marginHi;
    marginErr = Math.abs(edgeDistMm - want);
    marginScore = 1 - Math.min(1, marginErr / 4);
  }
  // Module landmark: predicted span vs detected blob U-extent.
  let moduleScore = 0.5;
  if (landmarks && landmarks.moduleSpan && landmarks.moduleU) {
    const a = project1D(labeled.fit, landmarks.moduleSpan.start);
    const b = project1D(labeled.fit, landmarks.moduleSpan.end);
    if (isFinite(a) && isFinite(b)) {
      const lo = Math.min(a, b), hi = Math.max(a, b);
      const mlo = landmarks.moduleU.u0, mhi = landmarks.moduleU.u1;
      const inter = Math.max(0, Math.min(hi, mhi) - Math.max(lo, mlo));
      const union = Math.max(hi, mhi) - Math.min(lo, mlo);
      moduleScore = union > 0 ? inter / union : 0;
    } else moduleScore = 0;
  }
  const total = 0.45 * digitScore + 0.3 * consensus + 0.1 * marginScore
    + 0.1 * moduleScore + (hyp.stripeOk ? 0.05 : 0);
  return {
    total, digitScore, marginScore, moduleScore, marginErr, consensus, fit: labeled.fit,
    stripeVeto: hyp.stripeVeto === true,
  };
}

// Rescore a REFINED row fit (trimmed digit phase + full-span consensus).
// Used for final acceptance after the end-seeded fit is refit on inliers.
function rescoreFit(fit, holes, digits, pitchPx, cal, rows) {
  const rowOfU = (u) => {
    let best = 1, be = Infinity;
    for (let r = 1; r <= rows; r++) {
      const e = Math.abs(project1D(fit, r) - u);
      if (e < be) { be = e; best = r; }
    }
    return { row: best, err: be };
  };
  let hit = 0;
  for (const hh of holes) {
    const { err } = rowOfU(hh.u);
    if (err < pitchPx * 0.4) hit++;
  }
  const consensus = hit / Math.max(1, holes.length);
  let digitScore = 0.5, nDigits = 0;
  if (digits && digits.length >= 2) {
    const per = [];
    for (const d of digits) {
      const { row, err } = rowOfU(d);
      if (err > pitchPx * 1.2) continue;
      const dist5 = Math.abs(row / 5 - Math.round(row / 5)) * 5;
      per.push(1 - Math.min(1, dist5 / (cal && cal.digitTol ? cal.digitTol : 0.8)));
    }
    nDigits = per.length;
    if (nDigits >= 2) {
      per.sort((a, b) => a - b);
      const kept = per.slice(Math.floor(per.length / 3));
      digitScore = kept.reduce((a, b) => a + b, 0) / Math.max(1, kept.length);
    }
  }
  return { digitScore, consensus, nDigits };
}

// Partial-board end registration. lum/w/h working grayscale, rgb optional
// color (same size, RGBA) for the stripe check, geom {PITCH, rows, holeXY,
// railInner, railOuter}, cal from calibratePartialEnd (or null), opts
// {moduleSpan:{start,end}, k} with k scaling lum px to target px.
// Returns {H, rows, conf, detail} or null (abstain with reason in detail).
function fitPartialEndLock(lum, w, h, rgb, geom, cal, opts) {
  const detail = { method: 'partial-end' };
  const fail = () => (opts && opts.debug ? { detail } : null);
  try {
    if (!lum || !w || !h || !geom || typeof geom.holeXY !== 'function') {
      detail.reason = 'no-input'; return fail();
    }
    const PITCH = geom.PITCH || 2.54, rows = geom.rows || 63;
    const railInner = geom.railInner || 18.5, railOuter = geom.railOuter || 20.75;
    const o = opts || {};
    const ax = boardAxis(lum, w, h);
    if (!ax) { detail.reason = 'no-board-shape'; return fail(); }
    const rb = railBandLines(lum, ax);
    const bands = rb && rb.bands;
    detail.bands = bands;
    detail.railLines = rb && rb.lines ? rb.lines.map((l) => ({ v: Math.round(l.v), pitch: Math.round(l.pitch) })) : null;
    if (!bands || bands.length !== 2) { detail.reason = 'no-rails'; return fail(); }
    const pitchPx = (bands[0].pitch + bands[1].pitch) / 2;
    if (!(pitchPx >= 4) || pitchPx > 130) { detail.reason = 'no-pitch'; return fail(); }
    // Comb each rail LANE separately (lanes resolved by railBandLines, so
    // every hole has a known lane), restricted to the board's own U span.
    const rLo = Math.max(0, Math.floor(ax.uLo || 0));
    const rHi = Math.min(ax.Lu - 1, Math.ceil(ax.uHi == null ? ax.Lu - 1 : ax.uHi));
    const holes = [];
    bands.forEach((b, bi) => {
      for (const [laneV, lane] of [[b.lanes[0], 'outer'], [b.lanes[1], 'inner']]) {
        const lv = Math.round(laneV);
        for (const u of combULine(ax, lv, rLo, rHi, pitchPx)) holes.push({ u, v: lv, lane, band: bi });
      }
    });
    if (holes.length < 6) { detail.reason = 'few-holes'; detail.holes = holes.length; return fail(); }
    detail.holesN = holes.length;
    detail.laneKnown = holes.filter((hh) => hh.lane).length;
    // Visible board ends (plastic runs out within 3 pitches past the combs).
    const us = holes.map((hh) => hh.u).sort((a, b) => a - b);
    const eLo = findEndEdge(ax, us[0], -1, pitchPx);
    const eHi = findEndEdge(ax, us[us.length - 1], 1, pitchPx);
    const ends = [];
    if (eLo && !eLo.offFrame) ends.push({ side: 'lo', u: eLo.u });
    if (eHi && !eHi.offFrame) ends.push({ side: 'hi', u: eHi.u });
    detail.ends = ends.map((e) => ({ side: e.side, u: Math.round(e.u) }));
    detail.pitchPx = Math.round(pitchPx * 10) / 10;
    if (!ends.length && !(o.moduleSpan && o.moduleSpan.start)) {
      detail.reason = 'no-end-no-landmark'; return fail();
    }
    // Numeral print (strip found empirically) + stripes + module blob.
    const strip = findNumeralStrip(ax, bands, (rb && rb.lines) || [], pitchPx,
      ends.map((e) => ({ u: e.u })), pitchPx * 2.5);
    const digits = strip.digits;
    detail.digitsN = digits.length;
    // Letter bands near each visible end: absolute row-1 / row-63 anchors
    // (letters sit ~rows 0-2 / 61-64, just past the end-most holes).
    const letterBySide = {};
    if (strip.stripV && strip.stripV.length) {
      for (const e of ends) {
        const cands = [];
        strip.stripV.forEach((sv, svi) => {
          const inward = svi === 0 ? 1 : -1;
          const lo = e.side === 'lo' ? e.u + pitchPx * 0.3 : e.u - pitchPx * 6;
          const hi = e.side === 'lo' ? e.u + pitchPx * 6 : e.u - pitchPx * 0.3;
          const lb = letterBandU(ax, sv, pitchPx, inward,
            Math.max(rLo, Math.round(lo)), Math.min(rHi, Math.round(hi)));
          if (lb) cands.push(lb.u);
        });
        if (cands.length) {
          cands.sort((a, b) => a - b);
          letterBySide[e.side] = cands[Math.floor(cands.length / 2)];
        }
      }
    }
    detail.letterBySide = letterBySide;
    let stripe = null;
    if (rgb) {
      const u0 = us[0] + (us[us.length - 1] - us[0]) * 0.25;
      const u1 = us[0] + (us[us.length - 1] - us[0]) * 0.75;
      const sLo = stripeOuterColor(rgb, w, h, ax.toXY, u0, u1, bands[0].lanes[0] - pitchPx * 0.5);
      const sHi = stripeOuterColor(rgb, w, h, ax.toXY, u0, u1, bands[1].lanes[0] + pitchPx * 0.5);
      if (sLo && sHi) stripe = { lo: sLo, hi: sHi };
    }
    let moduleU = null;
    if (o.moduleSpan) {
      const vLo = Math.min(bands[0].lanes[1], bands[1].lanes[1]) + pitchPx * 0.4;
      const vHi = Math.max(bands[0].lanes[1], bands[1].lanes[1]) - pitchPx * 0.4;
      if (vHi > vLo) moduleU = fieldDarkBlob(lum, ax, Math.round(vLo), Math.round(vHi));
    }
    const landmarks = { moduleSpan: o.moduleSpan || null, moduleU };
    // Enumerate anchor hypotheses. Module-anchored (endU null) hypotheses
    // are ALWAYS included when a module span is known: a detected "end"
    // can be a shadow or cutoff, and must not suppress the landmark path.
    const hyps = [];
    const endSides = ends.length ? ends.map((e) => e.side) : [null];
    if (ends.length && o.moduleSpan) endSides.push(null);
    for (const endU of endSides) {
      // Module-anchored hypotheses (endU null) carry no end, letter, or
      // margin evidence: endIsRow1 cannot change their score, but the
      // u-vs-row DIRECTION is a genuine binary unknown, so rEnd 1/2 encode
      // straight vs mirrored interpolation (verification picks).
      const row1s = endU === null ? [true] : [true, false];
      for (const endIsRow1 of row1s) {
        for (const loIsL of [true, false]) {
          const rEnds = endU === null ? [1, 2]
            : endIsRow1 ? [1, 2, 3] : [rows - 2, rows - 1, rows];
          for (const rEnd of rEnds) {
            const edgeU = endU === null ? undefined
              : ends.find((e) => e.side === endU).u;
            const letters = endU === null || letterBySide[endU] === undefined ? null
              : { u: letterBySide[endU], row: endIsRow1 ? 1 : rows };
            let preLabeled = null;
            if (endU === null && landmarks.moduleSpan && landmarks.moduleU) {
              // Rows interpolated across the detected module blob from the
              // known module span (project evidence, not a guess). The
              // frame's u direction vs row order is a genuine binary
              // unknown (no digits/letters here), so the mirror variant
              // rides along and verification picks the survivor.
              const { start, end } = landmarks.moduleSpan;
              const { u0, u1 } = landmarks.moduleU;
              const spanU = Math.max(1, u1 - u0);
              // mirror flag is encoded by the caller via rEnd parity: rEnd
              // 1 = u increases with row, rEnd 2 = mirrored. (Only these
              // two variants exist for endU null; see enumeration above.)
              const mirror = rEnd === 2;
              preLabeled = [];
              for (const hh of holes) {
                if (hh.u < u0 - pitchPx * 2 || hh.u > u1 + pitchPx * 2) continue;
                const t = (hh.u - u0) / spanU;
                const row = Math.round(mirror ? end - t * (end - start) : start + t * (end - start));
                if (row >= start - 3 && row <= end + 3) preLabeled.push([row, hh.u]);
              }
              if (preLabeled.length < 4) preLabeled = null;
            }
            let stripeVeto = false, stripeOk = false;
            if (stripe && stripe.lo && stripe.hi) {
              // Red (+) outer lane against a cool opposite (gray, beige, or
              // blue): decide only on a UNIFORMLY warm side clearly separated
              // from a cool side. Vivid red (>40) or faded-but-uniform warm
              // (median >12 with p10 >8 and separation >8). A wrong call
              // would mirror the board, so weak or symmetric signals decide
              // nothing; the ambiguity margin converts residual error into
              // abstain rather than misplacement.
              const warm = (s, o) => s.med > 40 || (s.med > 12 && s.p10 > 8 && s.med - o.med > 8);
              const cool = (s) => s.med < 8;
              const redLo = warm(stripe.lo, stripe.hi), redHi = warm(stripe.hi, stripe.lo);
              if ((redLo && cool(stripe.hi)) || (redHi && cool(stripe.lo))) {
                const loIsRed = stripe.lo.med > stripe.hi.med;
                // Red outer lane belongs to the L (plus-outer) rail pair.
                if ((loIsRed && !loIsL) || (!loIsRed && loIsL)) stripeVeto = true;
                else stripeOk = true;
              }
            }
            hyps.push({ endU, endIsRow1, loIsL, rEnd, edgeU, stripeVeto, stripeOk, preLabeled, letters });
          }
        }
      }
    }
    let best = null, second = null;
    const scored = [];
    for (const hyp of hyps) {
      if (hyp.stripeVeto) continue;
      const s = scoreAnchorHyp(hyp, holes, digits, pitchPx, cal, landmarks, geom);
      if (!s || s.stripeVeto) continue;
      // Without calibration margins there is no way to separate rEnd=1..3
      // (digits repeat every 5); demand a landmark or calibration then.
      const needsAnchor = !cal || !isFinite(cal.marginLo);
      if (needsAnchor && !(landmarks.moduleSpan && landmarks.moduleU)
        && !(digits && digits.length >= 3)) continue;
      const rec = { hyp, s };
      if (o.debug) {
        scored.push({
          total: Math.round(s.total * 1000) / 1000, digit: Math.round(s.digitScore * 1000) / 1000,
          cons: Math.round(s.consensus * 1000) / 1000, mod: Math.round(s.moduleScore * 1000) / 1000,
          marg: s.marginErr === null ? null : Math.round(s.marginErr * 100) / 100,
          rEnd: hyp.rEnd, e1: hyp.endIsRow1, loL: hyp.loIsL, endU: hyp.endU,
        });
      }
      if (!best || s.total > best.s.total) { second = best; best = rec; }
      else if (!second || s.total > second.s.total) second = rec;
    }
    if (o.debug && scored.length) {
      scored.sort((a, b) => b.total - a.total);
      detail.topHyps = scored.slice(0, 8);
    }
    if (!best) { detail.reason = 'ambiguous'; return fail(); }
    detail.best = {
      total: best.s.total, digit: best.s.digitScore, consensus: best.s.consensus,
      margin: best.s.marginScore, module: best.s.moduleScore, marginErr: best.s.marginErr,
      rEnd: best.hyp.rEnd, endIsRow1: best.hyp.endIsRow1, loIsL: best.hyp.loIsL,
      endU: best.hyp.endU, stripeOk: !!best.hyp.stripeOk,
    };
    if (second) detail.runnerUp = { total: second.s.total, rEnd: second.hyp.rEnd, endIsRow1: second.hyp.endIsRow1, loIsL: second.hyp.loIsL, endU: second.hyp.endU };
    // Refine the row map on all inlier holes (the end seed is fragile under
    // housings; the full span decides), then re-accept on refined scores.
    // A drift guard reverts to the seed fit if refinement walks away.
    let fit = best.s.fit;
    {
      const seedLab = [];
      for (const hh of holes) {
        let br = 1, be = Infinity;
        for (let r = 1; r <= rows; r++) { const e = Math.abs(project1D(fit, r) - hh.u); if (e < be) { be = e; br = r; } }
        if (be < pitchPx * 0.4) seedLab.push([hh.u, br]);
      }
      for (let it = 0; it < 3; it++) {
        const lab = [];
        for (const hh of holes) {
          let br = 1, be = Infinity;
          for (let r = 1; r <= rows; r++) { const e = Math.abs(project1D(fit, r) - hh.u); if (e < be) { be = e; br = r; } }
          if (be < pitchPx * 0.4) lab.push([br, hh.u]);
        }
        if (lab.length < 8) break;
        const f2 = fitProjective1D(lab);
        if (!f2) break;
        fit = f2;
      }
      let drift = 0;
      for (const [u, br] of seedLab) {
        let b2 = 1, be = Infinity;
        for (let r = 1; r <= rows; r++) { const e = Math.abs(project1D(fit, r) - u); if (e < be) { be = e; b2 = r; } }
        drift = Math.max(drift, Math.abs(b2 - br));
      }
      if (drift <= 2) {
        best.s.fit = fit;
        const rs = rescoreFit(fit, holes, digits, pitchPx, cal, rows);
        if (rs.nDigits >= 2) best.s.digitScore = rs.digitScore;
        best.s.consensus = rs.consensus;
        detail.refined = { drift, consensus: Math.round(rs.consensus * 1000) / 1000, digit: Math.round(best.s.digitScore * 1000) / 1000 };
      } else detail.refined = { drift, reverted: true };
    }
    best.s.total = 0.45 * best.s.digitScore + 0.3 * best.s.consensus
      + 0.1 * best.s.marginScore + 0.1 * best.s.moduleScore
      + (best.hyp.stripeOk ? 0.05 : 0);
    detail.best = {
      total: best.s.total, digit: best.s.digitScore, consensus: best.s.consensus,
      margin: best.s.marginScore, module: best.s.moduleScore, marginErr: best.s.marginErr,
      rEnd: best.hyp.rEnd, endIsRow1: best.hyp.endIsRow1, loIsL: best.hyp.loIsL,
      endU: best.hyp.endU, stripeOk: !!best.hyp.stripeOk,
    };
    if (best.s.total < 0.55) { detail.reason = 'weak-anchor'; detail.score = best.s.total; return fail(); }
    // Mirror tie: straight vs mirrored module interpolation score nearly
    // identically by construction (every component is direction-blind), so
    // the score gap cannot decide. The geometry can: verify both and take
    // a unique lattice survivor. Any other tie stays ambiguous.
    let mirrorAlt = null;
    if (second && best.s.total - second.s.total < 0.12) {
      const a = best.hyp, b = second.hyp;
      const twins = a.endU === null && b.endU === null && a.loIsL === b.loIsL
        && ((a.rEnd === 1 && b.rEnd === 2) || (a.rEnd === 2 && b.rEnd === 1));
      if (twins) mirrorAlt = second;
      else { detail.reason = 'ambiguous'; detail.scores = [best.s.total, second.s.total]; return fail(); }
    }
    if (digits && digits.length >= 2 && best.s.digitScore < 0.4) {
      detail.reason = 'digit-mismatch'; return fail();
    }
    if (best.s.consensus < 0.5) { detail.reason = 'no-consensus'; return fail(); }
    if (best.s.marginErr !== null && best.s.marginErr > 3.5) {
      detail.reason = 'margin-mismatch'; detail.marginErr = best.s.marginErr; return fail();
    }
    // Solve the homography from lane-confident holes under a fit, then
    // verify: anisotropy + visible-field lattice. Pure geometry per record.
    const verifyFit = (rec) => {
      const f = rec.s.fit, bh = rec.hyp;
      const laneX = (band, lane) => {
        const inner = lane === 'inner';
        const L = bh.loIsL ? (band === 0) : (band === 1);
        if (L) return inner ? -railInner : -railOuter;
        return inner ? railInner : railOuter;
      };
      const pairs = [];
      for (const hh of holes) {
        if (!hh.lane) continue;
        // row via winner fit: nearest integer row to the projective prediction
        let bestR = 0, be = Infinity;
        for (let r = 1; r <= rows; r++) {
          const e = Math.abs(project1D(f, r) - hh.u);
          if (e < be) { be = e; bestR = r; }
        }
        if (be > pitchPx * 0.4) continue;
        const z = (bestR - (rows + 1) / 2) * PITCH;
        const p = ax.toXY(hh.u, hh.v);
        pairs.push({ mx: laneX(hh.band, hh.lane), my: z, px: p.x, py: p.y });
      }
      if (pairs.length < 6) return { fail: 'few-labeled' };
      const spanRows = Math.max(...pairs.map((p) => p.my)) - Math.min(...pairs.map((p) => p.my));
      if (spanRows < 8 * PITCH) return { fail: 'narrow-span' };
      const H = dltHomography(pairs);
      if (!H) return { fail: 'dlt-fail' };
      // Residual: median pair reprojection error in px. A consistent set
      // lands sub-pitch; a mislabeled set (wrong rows/lanes) blows up and
      // the lattice below confirms the failure mode.
      let resid = 0;
      {
        const errs = [];
        for (const q of pairs) {
          const p = applyHomography(H, q.mx, q.my);
          errs.push(Math.hypot(p.x - q.px, p.y - q.py));
        }
        errs.sort((a, b) => a - b);
        resid = errs.length ? errs[Math.floor(errs.length / 2)] : Infinity;
      }
      if (axisScaleRatio(H) > 4) return { fail: 'anisotropy', resid, pairsN: pairs.length };
      const rAll = pairs.map((p) => Math.round(p.my / PITCH + (rows + 1) / 2));
      const r0 = Math.max(1, Math.min(...rAll) - 1), r1 = Math.min(rows, Math.max(...rAll) + 1);
      const lat = fieldLatticeScore(lum, w, h, H, geom.holeXY, r0, r1);
      if (!(lat.n >= 40) || !(lat.score >= 0.3)) return { fail: 'lattice', lat, r0, r1, resid, pairsN: pairs.length };
      return { H, rows: [r0, r1], lat, resid, pairsN: pairs.length };
    };
    let v = verifyFit(best);
    if (v.fail && mirrorAlt) {
      const v2 = verifyFit(mirrorAlt);
      if (!v2.fail) {
        v = v2; best = mirrorAlt;
        detail.mirrorResolved = true;
        detail.best = {
          total: best.s.total, digit: best.s.digitScore, consensus: best.s.consensus,
          margin: best.s.marginScore, module: best.s.moduleScore, marginErr: best.s.marginErr,
          rEnd: best.hyp.rEnd, endIsRow1: best.hyp.endIsRow1, loIsL: best.hyp.loIsL,
          endU: best.hyp.endU, stripeOk: !!best.hyp.stripeOk,
        };
      }
    }
    if (v.fail) {
      detail.reason = v.fail;
      if (v.lat) detail.lat = v.lat;
      if (v.resid !== undefined) detail.dltResid = Math.round(v.resid * 10) / 10;
      if (v.pairsN !== undefined) detail.pairsN = v.pairsN;
      if (v.r0 !== undefined) detail.verifyRows = [v.r0, v.r1];
      return fail();
    }
    let H = v.H;
    const k = (o && o.k) || 1;
    if (k !== 1) H = [H[0], H[1], H[2] * k, H[3], H[4], H[5] * k, H[6], H[7], H[8]];
    const conf = Math.round(100 * (0.5 * best.s.total + 0.5 * v.lat.score)) / 100;
    detail.score = best.s.total; detail.lat = v.lat; detail.rows = v.rows;
    detail.digits = digits ? digits.length : 0;
    detail.endIsRow1 = best.hyp.endIsRow1; detail.loIsL = best.hyp.loIsL;
    return { H, rows: v.rows, conf, detail };
  } catch (e) { detail.reason = 'exception'; return (opts && opts.debug ? { detail } : null); }
}

// Named wire-color test on one sRGB pixel. Only saturated harness colors
// are supported (red orange yellow green blue purple brown); dark neutrals
// (black/gray) and board-white are inseparable from shadows, table, and
// rails, so they return false and callers keep the bbox-extreme fallback.
// Hue in degrees, s/v 0..1. Pure: no DOM, no canvas.
function wireColorMatch(color, r, g, b) {
  try {
    const R = r / 255, G = g / 255, B = b / 255;
    const mx = Math.max(R, G, B), mn = Math.min(R, G, B);
    const v = mx, d = mx - mn;
    const s = mx > 0 ? d / mx : 0;
    let h = 0;
    if (d > 0) {
      if (mx === R) h = 60 * (((G - B) / d) % 6);
      else if (mx === G) h = 60 * ((B - R) / d + 2);
      else h = 60 * ((R - G) / d + 4);
      if (h < 0) h += 360;
    }
    switch (String(color || '').toLowerCase()) {
      case 'red': return (h < 14 || h >= 344) && s >= 0.42 && v >= 0.22;
      case 'orange': return h >= 14 && h < 45 && s >= 0.42 && v >= 0.3;
      case 'yellow': return h >= 45 && h < 75 && s >= 0.35 && v >= 0.35;
      case 'green': return h >= 75 && h < 168 && s >= 0.28 && v >= 0.18;
      case 'blue': return h >= 168 && h < 262 && s >= 0.3 && v >= 0.18;
      case 'purple': return h >= 230 && h < 315 && s >= 0.25 && v >= 0.15;
      case 'brown': return h >= 10 && h < 45 && s >= 0.15 && v >= 0.05 && v <= 0.65;
      default: return false;
    }
  } catch (e) { return false; }
}

// Wire endpoints from real pixels: segment the named harness color inside
// the sighting bbox, keep the largest connected blob, and return its two
// long-axis extremes in full-res image pixels. Bbox extremes fail on curved
// wires (the midline of the extreme edge can sit several pitches off the
// copper); color pixels mark the actual conductor. Returns
// {ends:[[x,y],[x,y]], cover, n} or null when the color is unsupported,
// absent, or dot-sized. Pure: rgb is flat [r,g,b]*w*h, bbox fractions.
function wireColorEnds(rgb, w, h, bbox, color) {
  try {
    if (!rgb || !w || !h || !Array.isArray(bbox) || bbox.length !== 4) return null;
    if (!wireColorMatch(color, 255, 0, 0) && !wireColorMatch(color, 0, 255, 0)
      && !wireColorMatch(color, 0, 0, 255) && !wireColorMatch(color, 255, 255, 255)
      && !wireColorMatch(color, 255, 165, 0) && !wireColorMatch(color, 255, 255, 0)
      && !wireColorMatch(color, 128, 0, 128) && !wireColorMatch(color, 139, 69, 19)) {
      // Probe failed on every saturated primary: unsupported color name
      // (black/gray/white/unknown) — caller keeps the bbox fallback.
      return null;
    }
    const x0 = Math.max(0, Math.floor(bbox[0] * w)), y0 = Math.max(0, Math.floor(bbox[1] * h));
    const x1 = Math.min(w - 1, Math.ceil(bbox[2] * w)), y1 = Math.min(h - 1, Math.ceil(bbox[3] * h));
    if (x1 <= x0 + 1 || y1 <= y0 + 1) return null;
    const stride = Math.max(1, Math.ceil(Math.max(x1 - x0, y1 - y0) / 420));
    const gw = Math.ceil((x1 - x0 + 1) / stride), gh = Math.ceil((y1 - y0 + 1) / stride);
    if (gw < 2 || gh < 2) return null;
    const mask = new Uint8Array(gw * gh);
    for (let gy = 0; gy < gh; gy++) {
      for (let gx = 0; gx < gw; gx++) {
        const px = Math.min(w - 1, x0 + gx * stride), py = Math.min(h - 1, y0 + gy * stride);
        const i = (py * w + px) * 3;
        if (wireColorMatch(color, rgb[i], rgb[i + 1], rgb[i + 2])) mask[gy * gw + gx] = 1;
      }
    }
    // Largest 4-connected blob: the conductor. Stray specks never win.
    const seen = new Uint8Array(gw * gh);
    let best = null;
    const stack = [];
    for (let s = 0; s < gw * gh; s++) {
      if (!mask[s] || seen[s]) continue;
      stack.length = 0; stack.push(s); seen[s] = 1;
      const pts = [];
      while (stack.length) {
        const c = stack.pop(); pts.push(c);
        const cx = c % gw, cy = (c / gw) | 0;
        if (cx > 0 && mask[c - 1] && !seen[c - 1]) { seen[c - 1] = 1; stack.push(c - 1); }
        if (cx < gw - 1 && mask[c + 1] && !seen[c + 1]) { seen[c + 1] = 1; stack.push(c + 1); }
        if (cy > 0 && mask[c - gw] && !seen[c - gw]) { seen[c - gw] = 1; stack.push(c - gw); }
        if (cy < gh - 1 && mask[c + gw] && !seen[c + gw]) { seen[c + gw] = 1; stack.push(c + gw); }
      }
      if (!best || pts.length > best.length) best = pts;
    }
    const minSize = Math.max(24, Math.floor(gw * gh * 0.0015));
    if (!best || best.length < minSize) return null;
    let mnx = gw, mxx = -1, mny = gh, mxy = -1;
    for (const c of best) {
      const cx = c % gw, cy = (c / gw) | 0;
      if (cx < mnx) mnx = cx; if (cx > mxx) mxx = cx;
      if (cy < mny) mny = cy; if (cy > mxy) mxy = cy;
    }
    const xr = mxx - mnx, yr = mxy - mny;
    if (xr < 3 && yr < 3) return null; // dot, not a wire run
    const horiz = xr >= yr;
    const along = best.map((c) => (horiz ? c % gw : (c / gw) | 0)).sort((a, b) => a - b);
    // p2/p98 extremes resist single stray pixels; map back to pixel centers.
    const q = (p) => along[Math.min(along.length - 1, Math.max(0, Math.floor(p * along.length)))];
    const eA = q(0.02), eB = q(0.98);
    const perp = (e) => {
      let sum = 0, n = 0;
      for (const c of best) {
        const a = horiz ? c % gw : (c / gw) | 0;
        if (Math.abs(a - e) <= 1) { sum += horiz ? (c / gw) | 0 : c % gw; n++; }
      }
      return n ? sum / n : (horiz ? (mny + mxy) / 2 : (mnx + mxx) / 2);
    };
    const toPx = (a, p) => (horiz ? [x0 + (a + 0.5) * stride, y0 + (p + 0.5) * stride]
      : [x0 + (p + 0.5) * stride, y0 + (a + 0.5) * stride]);
    const E0 = toPx(eA, perp(eA)), E1 = toPx(eB, perp(eB));
    return { ends: [E0, E1], cover: best.length / (gw * gh), n: best.length };
  } catch (e) { return null; }
}

// Letter-band detection: the printed column letters (a-e / f-j) form a
// dense cluster of >=4 narrow strokes within 3.5 pitches, unlike the sparse
// numerals. stripV is the numeral-strip row; letters sit up to ~1.2 pitches
// field-side of it. Searches u in [uLo, uHi]. Returns {u} or null.
function letterBandU(ax, stripV, pitchPx, inward, uLo, uHi) {
  try {
    const { at } = ax;
    // Windowed local stats: the nearby board edge is a giant step that
    // would drown global thresholds; letter strokes are local dips.
    for (let voff = 0; voff <= pitchPx * 1.2; voff += Math.max(1, pitchPx * 0.3)) {
      const v = Math.round(stripV + inward * voff);
      const line = [];
      for (let u = uLo; u <= uHi; u++) line.push(at(u, v));
      const centers = [];
      const winLen = Math.max(12, Math.round(pitchPx * 4));
      for (let w0 = 0; w0 < line.length; w0 += Math.max(4, Math.round(winLen / 3))) {
        const seg = line.slice(w0, w0 + winLen);
        if (seg.length < 8) continue;
        let base = 0;
        for (const x of seg) base += x;
        base /= seg.length;
        let sd = 0;
        for (const x of seg) sd += (x - base) * (x - base);
        sd = Math.sqrt(sd / seg.length);
        let run = -1;
        const flush = (end) => {
          if (run >= 0 && end - run >= 1 && end - run <= pitchPx * 0.3) {
            centers.push(uLo + w0 + run + (end - run) / 2);
          }
          run = -1;
        };
        for (let u = 0; u < seg.length; u++) {
          if (seg[u] < base - 1.4 * Math.max(1, sd)) { if (run < 0) run = u; }
          else flush(u);
        }
        flush(seg.length);
      }
      centers.sort((a, b) => a - b);
      const merged = [];
      for (const c of centers) {
        const last = merged[merged.length - 1];
        if (last !== undefined && c - last < 2) merged[merged.length - 1] = (last + c) / 2;
        else merged.push(c);
      }
      const W = pitchPx * 3.5;
      for (let i = 0; i < merged.length; i++) {
        const win = merged.filter((c) => c >= merged[i] - W / 2 && c <= merged[i] + W / 2);
        if (win.length >= 4 && win[win.length - 1] - win[0] >= pitchPx * 1.2) {
          return { u: win[Math.floor(win.length / 2)] };
        }
      }
    }
    return null;
  } catch (e) { return null; }
}

// Local minima of a darkness line: lowest in +-35% pitch window, darker than
// median - 0.4*sd, separated by >= 0.55 pitch (keeps the darker on conflict).
// Missing holes (covered by components) simply leave gaps; rows are assigned
// by division later, never by counting.
function combMinima(line, pitchEst) {
  const out = [];
  if (!line || !line.length || !(pitchEst > 2)) return out;
  const sorted = line.slice().sort((a, b) => a - b);
  const med = sorted[Math.floor(sorted.length / 2)] || 0;
  let sd = 0;
  for (const v of line) sd += (v - med) * (v - med);
  sd = Math.sqrt(sd / Math.max(1, line.length));
  const r = Math.max(1, Math.round(pitchEst * 0.35));
  const minSep = pitchEst * 0.55;
  const prom = Math.max(3, 0.5 * sd); // prominence kills plateau noise dips
  // Tolerance: JPEG noise makes neighbouring pixels differ by ~1-2, so a
  // strict maximum test rejects real hole bottoms; accept anything within
  // tol of the window max and let the min-separation dedupe keep the darkest.
  const tol = Math.max(1.5, 0.2 * sd);
  for (let i = 0; i < line.length; i++) {
    if (!(line[i] > med + 0.4 * Math.max(1, sd))) continue; // dark enough
    let isMin = true, wmin = Infinity;
    for (let k = 1; k <= r; k++) {
      const a = i - k >= 0 ? line[i - k] : Infinity;
      const b = i + k < line.length ? line[i + k] : Infinity;
      if (a < wmin) wmin = a;
      if (b < wmin) wmin = b;
      if (a > line[i] + tol || b > line[i] + tol) { isMin = false; break; }
    }
    if (!isMin) continue;
    // Depth above the surrounding plastic (window minimum), not the window
    // maximum: neighbouring holes are equally dark and must not count.
    if (!(line[i] - wmin > prom)) continue;
    const last = out[out.length - 1];
    if (last !== undefined && i - last < minSep) {
      if (line[i] > line[last]) out[out.length - 1] = i;
    } else out.push(i);
  }
  return out;
}

const api = {
  SHARP_MIN, EXPOSE_LO, EXPOSE_HI, STABLE_DIFF, NOVEL_DIFF,
  luminance, sharpness, sharpnessGrid, exposure, frameDiff,
  qualityVerdict, zoneName, newCoverage, updateCoverage,
  weakestCell, coverageScore,
  otsuThreshold, largestBlob, allBlobs, traceContour, convexHull, rdpClosed,
  orderCorners, quadQuality, findBoardQuad, detectBoardFraming, solveLinear, homography,
  applyHomography, decomposeHomography, rotationToEulerYXZ,
  estimateBoardPose, makeTrackFilter, invertHomography, rectifyQuad, findBoardQuads, quadsFromField, latticeContrast,
  profileDarkness, autocorrPitch, fitCombOrigin, registrationContrast, registerBoard,
  bandSpan, bandRunWidth, railCandidates,
  scanProgress, localVariance, boxDilate, boxErode, boardMask,
  floodFromBorder, largestComponent, fitQuadFromLines, ransacLines, latticeScores, refineQuad, boardScore, snapQuadToBoardEdges,
  sampleLine, boardAxis, axisDarkProfile, normPeriod, combMinima,
  dltHomography, fitProjective1D, project1D, axisScaleRatio, fieldLatticeScore,
  railBandLines, findEndEdge, digitBlobs, findNumeralStrip, letterBandU, fieldDarkBlob, stripeOuterColor,
  calibratePartialEnd, scoreAnchorHyp, fitPartialEndLock,
  wireColorMatch, wireColorEnds,
};
if (typeof module !== 'undefined' && module.exports) module.exports = api;
else root.ScanMath = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
