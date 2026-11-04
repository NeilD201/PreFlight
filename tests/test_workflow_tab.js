// The Workflow tab is gone: a "View live workflow" expander lives at the top
// of the review screen, beside the live investigation status and elapsed time.
const fs = require('node:fs'), assert = require('node:assert/strict');
const html = fs.readFileSync('web/index.html', 'utf8');
assert.ok(!html.includes('data-tab="workflow"'), 'no separate Workflow tab');
assert.ok(!html.includes('id="panel-workflow"'), 'no Workflow tabpanel');
assert.match(html, /id="workflow-strip"/, 'expander strip exists');
assert.match(html, /id="workflow-toggle"[^>]*aria-expanded="false"[^>]*aria-controls="workflow-live"/, 'toggle starts collapsed and targets the live view');
assert.match(html, />View live workflow</, 'toggle labeled');
assert.match(html, /id="workflow-status-text"/, 'live investigation status shown');
assert.match(html, /id="workflow-elapsed"/, 'elapsed time shown');
assert.match(html, /id="workflow-shell"/, 'collapsible shell exists');
for (const id of ['deck', 'rail', 'rail-stats', 'stage', 'graph', 'inspector', 'play', 'scrub', 'tick', 'src-banner', 'zin', 'zout', 'zreset']) {
  assert.ok(html.includes(`id="${id}"`), `embedded view keeps #${id}`);
}
assert.match(html, /<script src="\/command\.js" defer><\/script>/, 'Command Center renderer loads in main app');
assert.match(html, /\/command\?demo/, 'Demo replay stays on the standalone page, not in the live view');
const appjs = fs.readFileSync('web/app.js', 'utf8');
assert.match(appjs, /SHOW_WORKFLOW_VIZ\s*=\s*false/, 'animated viz hidden behind feature flag');
assert.match(appjs, /workflow-toggle/, 'main app wires the expander toggle');
assert.match(appjs, /renderWorkflowStrip/, 'main app renders live investigation status');
assert.match(appjs, /refreshCommandCenter/, 'expanding refreshes the embedded view');
assert.match(appjs, /initializeReviewTabs/, 'tab binding stays delegated');
const commandjs = fs.readFileSync('web/command.js', 'utf8');
assert.match(commandjs, /window\.refreshCommandCenter\s*=\s*load/, 'renderer exposes a refresh hook');
console.log('Workflow expander integration: passed');
