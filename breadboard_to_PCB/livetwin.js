/* PreFlight LiveScan 3D twin. Renders the structured breadboard model with
   three.js (r149, vendored). The model in breadboard.js owns all circuit
   truth; this module only draws it and reports selection. */
import * as THREE from '/vendor/three.module.js';

// 1 unit = 1 mm throughout the scene.
const COLORS = {
  body: 0xf1ede4, bodyEdge: 0xd8d2c4, hole: 0x17191c, railRed: 0xc23b2e,
  railBlue: 0x2e5fc2, groove: 0xcfc8b8, lead: 0xb9bec6, resistor: 0xd9a45f,
  capacitor: 0x3f5a8a, ledRed: 0xd9362b, ledGreen: 0x2fae5f, ic: 0x22252a,
  pcbBlue: 0x1d4f9c, shield: 0xb9bec6, generic: 0x8a8f98, select: 0xffb020,
};
const WIRE_COLORS = {
  red: 0xc23b2e, orange: 0xd97a1f, yellow: 0xd9b81f, green: 0x2fae5f,
  blue: 0x2e5fc2, black: 0x26282c, white: 0xe8e6e0, purple: 0x7a4fc2,
  brown: 0x6b3f12, grey: 0x8a8f98, gray: 0x8a8f98,
};

function lam(color, extra) {
  return new THREE.MeshStandardMaterial(Object.assign({ color, roughness: 0.85, metalness: 0.0 }, extra || {}));
}

export function mount(container, opts) {
  opts = opts || {};
  const BB = opts.breadboard;
  const W = container.clientWidth || 800, H = container.clientHeight || 520;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(W, H);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputEncoding = THREE.sRGBEncoding;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, W / H, 1, 4000);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f98, 0.95));
  const key = new THREE.DirectionalLight(0xffffff, 1.15);
  key.position.set(90, 160, 70);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.left = -140; key.shadow.camera.right = 140;
  key.shadow.camera.top = 140; key.shadow.camera.bottom = -140;
  key.shadow.camera.far = 500;
  key.shadow.bias = -0.0004;
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xdfe8ff, 0.35);
  fill.position.set(-120, 60, -80);
  scene.add(fill);

  // Custom orbit controls (no addons vendored): drag orbits, wheel zooms,
  // right-drag pans, touch orbit/pinch. Damped.
  const orbit = { theta: 0.7, phi: 1.02, radius: 215, target: new THREE.Vector3(0, 0, 0),
    goal: null, dragging: false, moved: 0 };
  orbit.goal = { theta: orbit.theta, phi: orbit.phi, radius: orbit.radius, target: orbit.target.clone() };
  function applyCamera() {
    orbit.theta += (orbit.goal.theta - orbit.theta) * 0.18;
    orbit.phi += (orbit.goal.phi - orbit.phi) * 0.18;
    orbit.radius += (orbit.goal.radius - orbit.radius) * 0.18;
    orbit.target.lerp(orbit.goal.target, 0.18);
    const sp = Math.sin(orbit.phi), x = orbit.radius * sp * Math.sin(orbit.theta);
    const y = orbit.radius * Math.cos(orbit.phi), z = orbit.radius * sp * Math.cos(orbit.theta);
    camera.position.set(orbit.target.x + x, orbit.target.y + y, orbit.target.z + z);
    camera.lookAt(orbit.target);
  }
  const el = renderer.domElement;
  el.style.touchAction = 'none';
  let pinch = null;
  el.addEventListener('contextmenu', (e) => e.preventDefault());
  el.addEventListener('pointerdown', (e) => {
    try { el.setPointerCapture(e.pointerId); } catch (_) { /* synthetic or released pointer */ }
    orbit.dragging = true; orbit.moved = 0;
    orbit.pan = (e.button === 2); orbit.sx = e.clientX; orbit.sy = e.clientY;
  });
  el.addEventListener('pointermove', (e) => {
    if (!orbit.dragging) return;
    const dx = e.clientX - orbit.sx, dy = e.clientY - orbit.sy;
    orbit.moved += Math.abs(dx) + Math.abs(dy);
    orbit.sx = e.clientX; orbit.sy = e.clientY;
    if (orbit.pan) {
      const k = orbit.radius / 600;
      const right = new THREE.Vector3(Math.cos(orbit.theta), 0, -Math.sin(orbit.theta));
      const fwd = new THREE.Vector3(-Math.sin(orbit.theta), 0, -Math.cos(orbit.theta));
      orbit.goal.target.addScaledVector(right, -dx * k).addScaledVector(fwd, -dy * k);
      orbit.goal.target.x = Math.max(-160, Math.min(160, orbit.goal.target.x));
      orbit.goal.target.z = Math.max(-160, Math.min(160, orbit.goal.target.z));
    } else {
      orbit.goal.theta -= dx * 0.005;
      orbit.goal.phi = Math.max(0.15, Math.min(1.45, orbit.goal.phi - dy * 0.005));
    }
  });
  const endDrag = (e) => {
    if (!orbit.dragging) return;
    orbit.dragging = false;
    if (orbit.moved < 5) pick(e.clientX, e.clientY);
  };
  el.addEventListener('pointerup', endDrag);
  el.addEventListener('pointercancel', () => { orbit.dragging = false; });
  el.addEventListener('wheel', (e) => {
    e.preventDefault();
    orbit.goal.radius = Math.max(60, Math.min(700, orbit.goal.radius * (e.deltaY > 0 ? 1.12 : 1 / 1.12)));
  }, { passive: false });
  el.addEventListener('touchstart', (e) => {
    if (e.touches.length === 2) {
      pinch = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
    }
  }, { passive: true });
  el.addEventListener('touchmove', (e) => {
    if (e.touches.length === 2 && pinch) {
      const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
      orbit.goal.radius = Math.max(60, Math.min(700, orbit.goal.radius * (pinch / Math.max(d, 1))));
      pinch = d;
    }
  }, { passive: true });

  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const pickables = [];
  let selected = null, selectRing = null, onSelect = null;
  function pick(cx, cy) {
    const r = el.getBoundingClientRect();
    pointer.x = ((cx - r.left) / r.width) * 2 - 1;
    pointer.y = -((cy - r.top) / r.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects(pickables, true);
    let id = null;
    if (hits.length) {
      let o = hits[0].object;
      while (o && !o.userData.pickId) o = o.parent;
      id = o ? o.userData.pickId : null;
    }
    select(id, true);
  }
  function select(id, notify) {
    selected = id;
    scene.traverse((o) => {
      if (o.userData.pickId && o.material && o.material.emissive) {
        const on = o.userData.pickId === id;
        o.material.emissive.setHex(on ? COLORS.select : (o.userData.baseEmissive || 0x000000));
        o.material.emissiveIntensity = on ? 0.55 : (o.userData.baseEmissiveIntensity || 0);
      }
    });
    if (selectRing) { scene.remove(selectRing); selectRing = null; }
    if (id && pickRoots[id]) {
      const box = new THREE.Box3().setFromObject(pickRoots[id]);
      const size = box.getSize(new THREE.Vector3()).length();
      selectRing = new THREE.Mesh(
        new THREE.TorusGeometry(Math.max(size / 2 + 3, 5), 0.7, 8, 40),
        new THREE.MeshBasicMaterial({ color: COLORS.select }));
      selectRing.rotation.x = Math.PI / 2;
      poseGroup.updateMatrixWorld(true);
      const c = box.getCenter(new THREE.Vector3());
      selectRing.position.set(c.x, c.y + 0.6, c.z);
      scene.add(selectRing);
    }
    if (notify && onSelect) onSelect(id);
  }

  const pickRoots = {};
  // poseGroup carries the scan-tracked physical orientation; orbit camera
  // stays independent so tracking never fights user navigation.
  const poseGroup = new THREE.Group();
  poseGroup.rotation.order = 'YXZ';
  scene.add(poseGroup);
  const boardGroup = new THREE.Group();
  poseGroup.add(boardGroup);
  const modelGroup = new THREE.Group();
  poseGroup.add(modelGroup);

  function setBoardPose(euler) {
    if (euler && isFinite(euler.rx) && isFinite(euler.ry) && isFinite(euler.rz)) {
      poseGroup.rotation.set(euler.rx, euler.ry, euler.rz);
    } else {
      poseGroup.rotation.set(0, 0, 0);
    }
    // Keep the selection ring glued to its part under rotation.
    if (selectRing && selected && pickRoots[selected]) {
      poseGroup.updateMatrixWorld(true);
      const c = new THREE.Box3().setFromObject(pickRoots[selected]).getCenter(new THREE.Vector3());
      selectRing.position.set(c.x, c.y + 0.6, c.z);
    }
  }
  let holeMesh = null, holeIndex = {}, holeBaseColor = null;

  function boardDims(rows) {
    const width = BB.BOARD_WIDTH, length = rows * BB.PITCH_MM + 12;
    return { width, length };
  }

  // Silkscreen-style text sprite; scale is in mm so markings stay proportional.
  function makeLabel(text, opts) {
    opts = opts || {};
    const px = 64, pad = 14;
    const cv = document.createElement('canvas');
    let cx = cv.getContext('2d');
    const font = (opts.weight || '600') + ' ' + px + 'px system-ui, -apple-system, sans-serif';
    cx.font = font;
    cv.width = Math.ceil(cx.measureText(text).width) + pad * 2;
    cv.height = px + pad * 2;
    cx = cv.getContext('2d');
    cx.font = font; cx.fillStyle = opts.color || '#8a8578';
    cx.textBaseline = 'middle'; cx.textAlign = 'center';
    cx.fillText(text, cv.width / 2, cv.height / 2 + 2);
    const tex = new THREE.CanvasTexture(cv);
    tex.anisotropy = 4;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
    const h = opts.h || 2.6;
    sp.scale.set((h * cv.width) / cv.height, h, 1);
    sp.position.set(opts.x || 0, opts.y || 0.35, opts.z || 0);
    return sp;
  }

  function buildBoard(project) {
    while (boardGroup.children.length) {
      const c = boardGroup.children.pop();
      if (c.geometry) c.geometry.dispose();
      if (c.material) {
        if (c.material.map) c.material.map.dispose();
        c.material.dispose(); // repeat dispose is harmless in three.js
      }
    }
    holeIndex = {};
    const rows = BB.rowsOf(project);
    const { width, length } = boardDims(rows);
    const body = new THREE.Mesh(new THREE.BoxGeometry(width, 8, length), lam(COLORS.body));
    body.position.y = -4.2;
    body.receiveShadow = true;
    boardGroup.add(body);
    const rim = new THREE.Mesh(new THREE.BoxGeometry(width + 3, 2.5, length + 3), lam(COLORS.bodyEdge));
    rim.position.y = -7.5;
    boardGroup.add(rim);
    const groove = new THREE.Mesh(new THREE.BoxGeometry(5.6, 0.5, length - 10), lam(COLORS.groove));
    groove.position.y = -0.42; // hair above the surface: reads as a channel
    boardGroup.add(groove);
    // Rail stripes: red outboard of the + lane, blue outboard of the - lane
    // (outboard = away from the sibling lane; polarity is per-side, see
    // Breadboard.railLaneX). Visual markings only; connectivity comes from
    // the model, never the paint.
    const stripeLen = length - 10;
    for (const side of ['L', 'R']) {
      const plusX = BB.railLaneX(side, '+');
      const minusX = BB.railLaneX(side, '-');
      const red = new THREE.Mesh(new THREE.BoxGeometry(1.1, 0.4, stripeLen),
        lam(COLORS.railRed, { roughness: 0.6 }));
      red.position.set(plusX + Math.sign(plusX - minusX) * 1.8, -0.05, 0);
      const blue = new THREE.Mesh(new THREE.BoxGeometry(1.1, 0.4, stripeLen),
        lam(COLORS.railBlue, { roughness: 0.6 }));
      blue.position.set(minusX + Math.sign(minusX - plusX) * 1.8, -0.05, 0);
      boardGroup.add(red, blue);
    }
    // Split-rail breaks: body-colored notches across lanes and stripes.
    const zRow = (r) => (r - (rows + 1) / 2) * BB.PITCH_MM;
    const notchMat = lam(COLORS.body, { roughness: 0.9 });
    for (const side of ['L', 'R']) for (const pol of ['+', '-']) {
      const key = 'r' + side + pol;
      const laneX = BB.railLaneX(side, pol);
      for (const g of project.breadboard.railGaps[key] || []) {
        const nz = zRow(g) + BB.PITCH_MM / 2;
        const notch = new THREE.Mesh(new THREE.BoxGeometry(3.2, 0.55, 1.4), notchMat);
        notch.position.set(laneX, -0.05, nz);
        // Stripe cover sits outboard of the lane, mirroring the stripe rule.
        const sibX = BB.railLaneX(side, pol === '+' ? '-' : '+');
        const cover = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.55, 1.4), notchMat);
        cover.position.set(laneX + Math.sign(laneX - sibX) * 1.8, -0.05, nz);
        boardGroup.add(notch, cover);
      }
    }
    // Printed markings: row numbers, column letters, rail polarity.
    const numX = 15.9, ink = '#8a8578';
    for (let r = 5; r <= rows; r += 5) {
      for (const s of [-1, 1])
        boardGroup.add(makeLabel(String(r), { x: s * numX, z: zRow(r), color: ink, h: 2.4 }));
    }
    if (rows % 5) for (const s of [-1, 1])
      boardGroup.add(makeLabel(String(rows), { x: s * numX, z: zRow(rows), color: ink, h: 2.4 }));
    const colX = (c) => BB.holeXY(project, 't1' + c).x;
    for (const end of [0, rows + 1]) {
      const ez = zRow(end);
      for (const c of 'ABCDEFGHIJ')
        boardGroup.add(makeLabel(c, { x: colX(c), z: ez, color: ink, h: 2.2 }));
    }
    for (const side of ['L', 'R']) {
      for (const end of [0, rows + 1]) {
        boardGroup.add(makeLabel('+', { x: BB.railLaneX(side, '+'), z: zRow(end), color: '#c23b2e', h: 2.8, weight: '700' }));
        boardGroup.add(makeLabel('−', { x: BB.railLaneX(side, '-'), z: zRow(end), color: '#2e5fc2', h: 2.8, weight: '700' }));
      }
    }
    // Holes as one InstancedMesh.
    const holeIds = [];
    for (let r = 1; r <= rows; r++) for (let c = 0; c < 10; c++)
      holeIds.push('t' + r + 'ABCDEFGHIJ'[c]);
    for (const s of ['L', 'R']) for (const p of ['+', '-']) for (let i = 1; i <= rows; i++)
      holeIds.push('r' + s + p + i);
    const geo = new THREE.CylinderGeometry(0.85, 0.85, 0.5, 10);
    const mat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1 });
    holeMesh = new THREE.InstancedMesh(geo, mat, holeIds.length);
    const m = new THREE.Matrix4(), col = new THREE.Color(COLORS.hole);
    holeIds.forEach((id, i) => {
      const p = BB.holeXY(project, id);
      m.makeTranslation(p.x, -0.2, p.z);
      holeMesh.setMatrixAt(i, m);
      holeMesh.setColorAt(i, col);
      holeIndex[id] = i;
    });
    holeMesh.instanceMatrix.needsUpdate = true;
    if (holeMesh.instanceColor) holeMesh.instanceColor.needsUpdate = true;
    holeBaseColor = col.clone();
    boardGroup.add(holeMesh);
  }

  function leadMesh(x1, z1, x2, z2, topY) {
    const g = new THREE.Group();
    const mat = lam(COLORS.lead, { metalness: 0.65, roughness: 0.35 });
    const mk = (x, z) => {
      const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.32, topY + 2, 8), mat);
      leg.position.set(x, topY / 2 - 1, z);
      leg.castShadow = true;
      return leg;
    };
    g.add(mk(x1, z1), mk(x2, z2));
    return g;
  }

  function bodyBetween(kind, value, p1, p2, comp) {
    const g = new THREE.Group();
    const mx = (p1.x + p2.x) / 2, mz = (p1.z + p2.z) / 2;
    const dist = Math.hypot(p2.x - p1.x, p2.z - p1.z);
    const ang = Math.atan2(p2.x - p1.x, p2.z - p1.z);
    if (kind === 'resistor') {
      const bodyM = new THREE.Mesh(new THREE.CylinderGeometry(1.5, 1.5, Math.min(dist * 0.55, 7), 12),
        lam(COLORS.resistor, { roughness: 0.55 }));
      bodyM.rotation.z = Math.PI / 2;
      const wrap = new THREE.Group();
      wrap.add(bodyM);
      // Color bands: honest decoration only when the value parses as digits.
      const digits = String(value || '').replace(/[^0-9]/g, '').slice(0, 3);
      const bandCols = [0x1a1a1a, 0x6b3f12, 0xc23b2e, 0xd97a1f, 0xd9b81f, 0x2fae5f, 0x2e5fc2, 0x7a4fc2, 0x8a8f98, 0xe8e6e0];
      [...digits].forEach((d, i) => {
        const band = new THREE.Mesh(new THREE.TorusGeometry(1.52, 0.28, 8, 16), lam(bandCols[+d]));
        band.position.x = -2 + i * 1.3;
        band.rotation.y = Math.PI / 2;
        wrap.add(band);
      });
      wrap.position.set(mx, 4.2, mz);
      wrap.rotation.y = ang + Math.PI / 2;
      wrap.traverse((o) => { o.castShadow = true; });
      g.add(wrap, leadMesh(p1.x, p1.z, p2.x, p2.z, 4.2));
    } else if (kind === 'led' || kind === 'diode') {
      const domeC = /green/i.test(value) ? COLORS.ledGreen : COLORS.ledRed;
      const dome = new THREE.Mesh(new THREE.SphereGeometry(2.2, 16, 12, 0, Math.PI * 2, 0, Math.PI / 2),
        new THREE.MeshStandardMaterial({ color: domeC, roughness: 0.25, transparent: true, opacity: 0.92 }));
      const skirt = new THREE.Mesh(new THREE.CylinderGeometry(2.2, 2.2, 1.6, 16),
        new THREE.MeshStandardMaterial({ color: domeC, roughness: 0.4 }));
      // Anode lead (p1) drawn longer, matching the real part.
      const anode = p1, cathode = p2;
      dome.position.set(mx, 5.4, mz); skirt.position.set(mx, 3.6, mz);
      dome.castShadow = true;
      g.add(dome, skirt);
      const mat = lam(COLORS.lead, { metalness: 0.65, roughness: 0.35 });
      const la = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.32, 6.5, 8), mat);
      la.position.set(anode.x, 2.2, anode.z);
      const lc = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.32, 5.2, 8), mat);
      lc.position.set(cathode.x, 1.6, cathode.z);
      g.add(la, lc);
      // Flat side marker on the cathode side.
      const flat = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.4, 0.4), lam(0xffffff));
      flat.position.set(mx + (cathode.x - mx) * 0.4, 4.4, mz + (cathode.z - mz) * 0.4);
      g.add(flat);
    } else if (kind === 'capacitor') {
      const bodyM = new THREE.Mesh(new THREE.CylinderGeometry(1.8, 1.8, 4, 14), lam(COLORS.capacitor, { roughness: 0.5 }));
      bodyM.position.set(mx, 3.4, mz);
      bodyM.castShadow = true;
      g.add(bodyM, leadMesh(p1.x, p1.z, p2.x, p2.z, 3.4));
    } else if (kind === 'button') {
      const bodyM = new THREE.Mesh(new THREE.BoxGeometry(5, 3, 5), lam(0x303338, { roughness: 0.6 }));
      bodyM.position.set(mx, 2.6, mz);
      bodyM.castShadow = true;
      const cap = new THREE.Mesh(new THREE.CylinderGeometry(1.6, 1.6, 1.2, 12), lam(0x1a1c1e));
      cap.position.set(mx, 4.6, mz);
      g.add(bodyM, cap, leadMesh(p1.x, p1.z, p2.x, p2.z, 2.6));
    } else {
      const bodyM = new THREE.Mesh(new THREE.BoxGeometry(Math.min(Math.max(dist * 0.6, 3), 9), 2.4, 3.4),
        lam(COLORS.generic, { roughness: 0.6 }));
      bodyM.position.set(mx, 3, mz);
      bodyM.rotation.y = ang;
      bodyM.castShadow = true;
      g.add(bodyM, leadMesh(p1.x, p1.z, p2.x, p2.z, 3));
    }
    void comp;
    return g;
  }

  function chipBody(pins /* [{x,z}] */, w, color, h) {
    const g = new THREE.Group();
    const xs = pins.map((p) => p.x), zs = pins.map((p) => p.z);
    const mx = (Math.min(...xs) + Math.max(...xs)) / 2, mz = (Math.min(...zs) + Math.max(...zs)) / 2;
    const sx = Math.max(...xs) - Math.min(...xs) + w, sz = Math.max(...zs) - Math.min(...zs) + 4;
    const bodyM = new THREE.Mesh(new THREE.BoxGeometry(sx, h, sz), lam(color, { roughness: 0.55 }));
    bodyM.position.set(mx, h / 2 + 1.2, mz);
    bodyM.castShadow = true;
    g.add(bodyM);
    const mat = lam(COLORS.lead, { metalness: 0.65, roughness: 0.35 });
    for (const p of pins) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.8, h + 1.2, 0.8), mat);
      leg.position.set(p.x, (h + 1.2) / 2 - 1, p.z);
      g.add(leg);
    }
    // Pin-1 dot.
    const dot = new THREE.Mesh(new THREE.CylinderGeometry(0.7, 0.7, 0.3, 10), lam(0xd8d8d8));
    dot.position.set(pins[0].x * 0.9 + mx * 0.1, h + 1.35, pins[0].z * 0.9 + mz * 0.1);
    g.add(dot);
    return g;
  }

  // ESP32-DevKitC as it looks on a real breadboard: black 28 x 52 mm PCB on
  // two header strips, silver RF shield can, metal micro-USB shell with a
  // black tongue, EN/BOOT buttons, red power + blue activity LEDs, white
  // silkscreen edge stripes and a copper antenna keepout. Dimensions follow
  // the DevKit footprint; details are visual, never electrical truth.
  function buildEsp32Devkit(pts) {
    const g = new THREE.Group();
    const xs = pts.map((p) => p.x), zs = pts.map((p) => p.z);
    const mx = (Math.min(...xs) + Math.max(...xs)) / 2;
    const mz = (Math.min(...zs) + Math.max(...zs)) / 2;
    const gold = lam(0xc8a038, { metalness: 0.75, roughness: 0.3 });
    for (const p of pts) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.7, 3.6, 0.7), gold);
      leg.position.set(p.x, 0.6, p.z);
      leg.castShadow = true;
      g.add(leg);
    }
    for (const hx of [Math.min(...xs), Math.max(...xs)]) {
      const strip = new THREE.Mesh(new THREE.BoxGeometry(2.6, 2.4, 50),
        lam(0x1a1c1e, { roughness: 0.6 }));
      strip.position.set(hx, 1.6, mz);
      strip.castShadow = true;
      g.add(strip);
    }
    const pcb = new THREE.Mesh(new THREE.BoxGeometry(28, 1.6, 52),
      lam(0x141518, { roughness: 0.5 }));
    pcb.position.set(mx, 3.6, mz);
    pcb.castShadow = true;
    g.add(pcb);
    for (const sx of [-12.5, 12.5]) {
      const silk = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.12, 46),
        lam(0xd8d5cc, { roughness: 0.7 }));
      silk.position.set(mx + sx, 4.45, mz);
      g.add(silk);
    }
    const shield = new THREE.Mesh(new THREE.BoxGeometry(18, 3.2, 19.5),
      lam(COLORS.shield, { metalness: 0.75, roughness: 0.35 }));
    shield.position.set(mx, 6.0, mz - 8);
    shield.castShadow = true;
    g.add(shield);
    const canTop = new THREE.Mesh(new THREE.BoxGeometry(16.4, 0.25, 17.9),
      lam(0x8a8f98, { metalness: 0.7, roughness: 0.45 }));
    canTop.position.set(mx, 7.7, mz - 8);
    g.add(canTop);
    const usb = new THREE.Mesh(new THREE.BoxGeometry(7.5, 3.4, 6),
      lam(COLORS.lead, { metalness: 0.8, roughness: 0.3 }));
    usb.position.set(mx, 6.1, mz + 22);
    usb.castShadow = true;
    g.add(usb);
    const tongue = new THREE.Mesh(new THREE.BoxGeometry(5.4, 1.1, 1.6),
      lam(0x0c0d0e, { roughness: 0.6 }));
    tongue.position.set(mx, 5.8, mz + 25.2);
    g.add(tongue);
    const ant = new THREE.Mesh(new THREE.BoxGeometry(10, 0.15, 6),
      lam(0xb87333, { metalness: 0.6, roughness: 0.4 }));
    ant.position.set(mx, 4.45, mz - 21.5);
    g.add(ant);
    for (const bx of [-7, 7]) {
      const btn = new THREE.Mesh(new THREE.BoxGeometry(3, 1.8, 4),
        lam(0x1a1c1e, { roughness: 0.6 }));
      btn.position.set(mx + bx, 5.3, mz + 13);
      const cap = new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.7, 2.2),
        lam(0xb9bec6, { metalness: 0.6, roughness: 0.4 }));
      cap.position.set(mx + bx, 6.5, mz + 13);
      btn.castShadow = true;
      g.add(btn, cap);
    }
    const ledR = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.9, 0.9),
      new THREE.MeshStandardMaterial({ color: 0x550f0a, emissive: 0xff2a1a, emissiveIntensity: 1.6 }));
    ledR.position.set(mx - 4, 4.9, mz + 17.5);
    const ledB = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.9, 0.9),
      new THREE.MeshStandardMaterial({ color: 0x0a1a55, emissive: 0x2a6aff, emissiveIntensity: 1.2 }));
    ledB.position.set(mx + 4, 4.9, mz + 17.5);
    g.add(ledR, ledB);
    return g;
  }

  // Tower Pro MG90S micro servo for the off-board tray: black case, mounting
  // flange with ears, white output spline + cross horn, blue label sticker
  // and the brown/red/orange 3-wire cable. Datasheet size 22.8 x 12.2 mm.
  function buildServoMG90S(x, z) {
    const g = new THREE.Group();
    const ink = lam(0x17191c, { roughness: 0.55 });
    const lower = new THREE.Mesh(new THREE.BoxGeometry(22.8, 14, 12.2), ink);
    lower.position.set(x, 7, z);
    lower.castShadow = true;
    const upper = new THREE.Mesh(new THREE.BoxGeometry(22.8, 7, 12.2), ink);
    upper.position.set(x, 17.5, z);
    upper.castShadow = true;
    const flange = new THREE.Mesh(new THREE.BoxGeometry(32.6, 2.6, 12.2), ink);
    flange.position.set(x, 22.3, z);
    flange.castShadow = true;
    g.add(lower, upper, flange);
    const holeMat = lam(0x0a0b0c, { roughness: 1 });
    for (const ex of [-13.5, 13.5]) for (const ez of [-3.4, 3.4]) {
      const ear = new THREE.Mesh(new THREE.CylinderGeometry(2.1, 2.1, 2.7, 12), holeMat);
      ear.position.set(x + ex, 22.3, z + ez);
      g.add(ear);
    }
    const sticker = new THREE.Mesh(new THREE.BoxGeometry(0.4, 7, 10),
      lam(0x2456a8, { roughness: 0.5 }));
    sticker.position.set(x + 11.6, 8, z);
    g.add(sticker);
    const spline = new THREE.Mesh(new THREE.CylinderGeometry(2.3, 2.3, 3.4, 16),
      lam(0xe8e6e0, { roughness: 0.45 }));
    spline.position.set(x - 5.7, 25.4, z);
    spline.castShadow = true;
    const horn = new THREE.Group();
    const arm = new THREE.Mesh(new THREE.BoxGeometry(21, 1.4, 4),
      lam(0xe8e6e0, { roughness: 0.45 }));
    arm.castShadow = true;
    const hub = new THREE.Mesh(new THREE.CylinderGeometry(3.4, 3.4, 2.2, 16),
      lam(0xe8e6e0, { roughness: 0.45 }));
    horn.add(arm, hub);
    horn.position.set(x - 5.7, 27.4, z);
    horn.rotation.y = 0.35;
    g.add(spline, horn);
    const cableCols = [0x6b3f12, 0xc23b2e, 0xd97a1f];
    cableCols.forEach((cc, i) => {
      const curve = new THREE.QuadraticBezierCurve3(
        new THREE.Vector3(x + 11.4, 4 + i * 1.6, z + 3),
        new THREE.Vector3(x + 20, 2, z + 8 + i * 1.2),
        new THREE.Vector3(x + 26, 0.6, z + 14 + i * 1.6));
      const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 12, 0.55, 6),
        lam(cc, { roughness: 0.55 }));
      g.add(tube);
    });
    return g;
  }

  function buildModel(project) {
    while (modelGroup.children.length) {
      const c = modelGroup.children.pop();
      c.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
    }
    for (const k of Object.keys(pickRoots)) delete pickRoots[k];
    pickables.length = 0;
    clearHighlight();
    const reg = (id, obj) => {
      obj.userData.pickId = id;
      pickRoots[id] = obj;
      pickables.push(obj);
      modelGroup.add(obj);
    };
    for (const c of project.components) {
      const pts = c.pins.map((p) => (p.hole ? BB.holeXY(project, p.hole) : null));
      if (pts.some((p) => !p)) continue; // unplaced parts live in the list, not the scene
      let obj = null;
      if (c.pins.length === 2 && c.kind !== 'sensor3') {
        obj = bodyBetween(c.kind, c.value, pts[0], pts[1], c);
      } else if (c.kind === 'esp32devkit') {
        obj = buildEsp32Devkit(pts);
      } else {
        obj = chipBody(pts, 4, c.kind.startsWith('dip') ? COLORS.ic : COLORS.generic, 2.6);
      }
      if (c.orientation) obj.rotation.y = (c.orientation * Math.PI) / 180;
      obj.traverse((o) => {
        if (o.material && o.material.emissive) {
          o.userData.baseEmissive = o.material.emissive.getHex();
          o.userData.baseEmissiveIntensity = o.material.emissiveIntensity;
        }
      });
      reg(c.id, obj);
    }
    for (const w of project.wires) {
      if (!w.from || !w.to) continue;
      const a = BB.holeXY(project, w.from), b = BB.holeXY(project, w.to);
      if (!a || !b) continue;
      const dist = Math.hypot(b.x - a.x, b.z - a.z);
      const mid = new THREE.Vector3((a.x + b.x) / 2, 4 + dist * 0.18, (a.z + b.z) / 2);
      const curve = new THREE.QuadraticBezierCurve3(
        new THREE.Vector3(a.x, 8, a.z), mid, new THREE.Vector3(b.x, 8, b.z));
      const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 24, 0.75, 8),
        lam(WIRE_COLORS[w.color] || 0xc23b2e, { roughness: 0.5 }));
      tube.castShadow = true;
      const g = new THREE.Group();
      g.add(tube);
      for (const p of [a, b]) {
        // Dupont connector housing at each endpoint, as on the real build:
        // a black 1-pin shell standing in the hole, wire entering its top.
        const shell = new THREE.Mesh(new THREE.BoxGeometry(2.6, 8, 2.6),
          lam(0x17191c, { roughness: 0.55 }));
        shell.position.set(p.x, 4, p.z);
        shell.castShadow = true;
        g.add(shell);
      }
      g.traverse((o) => {
        if (o.material && o.material.emissive) {
          o.userData.baseEmissive = o.material.emissive.getHex();
          o.userData.baseEmissiveIntensity = o.material.emissiveIntensity;
        }
      });
      reg(w.id, g);
    }
    // Off-board modules (identified from photos but not on the breadboard,
    // e.g. the servo): render at datasheet size on a tray beside the board
    // so the twin shows the actual part, not a random box. Pin-less and
    // explicitly off-board only — unplaced on-board parts stay in the list.
    {
      const trayX = BB.BOARD_WIDTH / 2 + 34;
      let trayZ = -30;
      for (const c of project.components) {
        if ((c.pins || []).some((p) => p.hole)) continue;
        if (!/off-?board/i.test(String((c.notes || '') + ' ' + (c.label || '')))) continue;
        const isServo = /servo|mg90|mg996/i.test(String(c.label || '') + String(c.notes || ''));
        // Tower Pro MG90S datasheet: 23.2 x 12.2 x 29.0 mm.
        const dims = isServo ? [32.6, 28.5, 14] : [22, 8, 17];
        const g = isServo ? buildServoMG90S(trayX, trayZ) : new THREE.Group();
        if (!isServo) {
          const bodyM = new THREE.Mesh(new THREE.BoxGeometry(dims[0], dims[1], dims[2]),
            lam(COLORS.generic, { roughness: 0.55 }));
          bodyM.position.set(trayX, dims[1] / 2, trayZ);
          bodyM.castShadow = true;
          g.add(bodyM);
        }
        g.traverse((o) => {
          if (o.material && o.material.emissive) {
            o.userData.baseEmissive = o.material.emissive.getHex();
            o.userData.baseEmissiveIntensity = o.material.emissiveIntensity;
          }
        });
        reg(c.id, g);
        trayZ += dims[2] + 12;
      }
    }
    select(selected && pickRoots[selected] ? selected : null, false);
  }

  function clearHighlight() {
    if (!holeMesh) return;
    const col = holeBaseColor;
    for (const k of Object.keys(holeIndex)) holeMesh.setColorAt(holeIndex[k], col);
    holeMesh.instanceColor.needsUpdate = true;
  }
  function highlightNet(holes) {
    clearHighlight();
    if (!holeMesh || !holes || !holes.length) return;
    const col = new THREE.Color(0xffb020);
    for (const h of holes) if (h in holeIndex) holeMesh.setColorAt(holeIndex[h], col);
    holeMesh.instanceColor.needsUpdate = true;
  }

  let project = null, raf = 0, disposed = false;
  function frame() {
    if (disposed) return;
    applyCamera();
    renderer.render(scene, camera);
    raf = requestAnimationFrame(frame);
  }
  function resize() {
    const w = container.clientWidth || 800, h = container.clientHeight || 520;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }
  window.addEventListener('resize', resize);

  const api = {
    setProject(p) {
      const sizeChanged = !project || project.breadboard.size !== p.breadboard.size ||
        JSON.stringify(project.breadboard.railGaps) !== JSON.stringify(p.breadboard.railGaps);
      project = p;
      if (sizeChanged || !holeMesh) buildBoard(p);
      buildModel(p);
    },
    refresh() { if (project) { buildBoard(project); buildModel(project); } },
    select(id) { select(id, false); },
    selected: () => selected,
    highlightNet,
    clearHighlight,
    onSelect(cb) { onSelect = cb; },
    setBoardPose(e) { setBoardPose(e); },
    resetView() {
      orbit.goal.theta = 0.7; orbit.goal.phi = 1.02; orbit.goal.radius = 215;
      orbit.goal.target.set(0, 0, 0);
    },
    dispose() {
      disposed = true;
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', resize);
      renderer.dispose();
      if (renderer.domElement.parentNode === container) container.removeChild(renderer.domElement);
    },
  };
  applyCamera();
  frame();
  return api;
}

window.LiveTwin = { mount };
