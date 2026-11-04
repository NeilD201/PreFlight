// Theme isolation: the dark Command Center stylesheet must never override the
// light app theme. Every :root variable it defines must be namespaced, and it
// must not style bare page elements.
const fs = require('node:fs'), assert = require('node:assert/strict');
function rootVars(css) {
  const out = new Set();
  for (const m of css.matchAll(/:root\s*\{([^}]*)\}/g))
    for (const d of m[1].matchAll(/(--[A-Za-z0-9_-]+)\s*:/g)) out.add(d[1]);
  return out;
}
const app = fs.readFileSync('web/app.css', 'utf8');
const refresh = fs.readFileSync('web/refresh.css', 'utf8');
const command = fs.readFileSync('web/command.css', 'utf8');
const appVars = new Set([...rootVars(app), ...rootVars(refresh)]);
const cmdVars = rootVars(command);
for (const v of cmdVars) {
  assert.ok(!appVars.has(v), `${v} in command.css collides with the app theme`);
  assert.ok(v.startsWith('--cmd-'), `${v} in command.css must be namespaced --cmd-*`);
}
assert.ok(!/^body\s*\{|[^#.\w]body\s*\{/m.test(command), 'command.css must not style bare body');
// Final cascade in <head> order (app, refresh, command): the light theme must win.
const order = [app, refresh, command];
const final = {};
for (const css of order)
  for (const m of css.matchAll(/:root\s*\{([^}]*)\}/g))
    for (const d of m[1].matchAll(/(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);/g)) final[d[1]] = d[2].trim();
assert.equal(final['--ink'], '#232623', 'app text stays dark on light backgrounds');
assert.equal(final['--line'], '#dedfd8', 'app borders stay light');
function lum(hex) {
  const c = hex.replace('#', '');
  const v = [0, 2, 4].map(i => parseInt(c.slice(i, i + 2), 16) / 255)
    .map(x => x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4));
  return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
}
function contrast(a, b) {
  const l1 = lum(a), l2 = lum(b);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}
assert.ok(contrast(final['--ink'], '#ffffff') >= 4.5, `ink/paper contrast ${contrast(final['--ink'], '#ffffff').toFixed(1)} < 4.5`);
assert.ok(contrast(final['--ink'], '#f5f4f0') >= 4.5, `ink/bg contrast ${contrast(final['--ink'], '#f5f4f0').toFixed(1)} < 4.5`);
const html = fs.readFileSync('web/index.html', 'utf8');
const inline = [...html.matchAll(/<style>([\s\S]*?)<\/style>/g)].map(m => m[1]).join('\n');
const bare = ['--line', '--ink', '--amber', '--mono', '--sans', '--base', '--panel', '--panel2', '--dim', '--faint', '--teal', '--rust'];
for (const m of inline.matchAll(/var\(\s*(--[A-Za-z0-9_-]+)/g))
  assert.ok(!bare.includes(m[1]), `index.html inline style leaks dark-theme var ${m[1]}`);
console.log('Theme isolation: passed');
