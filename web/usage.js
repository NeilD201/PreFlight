'use strict';
/* Meta spend tracker. Text updates only: never navigates, never moves scroll. */
let usageSummary = null;
function usageDollars(value) {
  const amount = Number(value);
  return '$' + (Number.isFinite(amount) ? amount : 0).toFixed(4);
}
function usageTokens(count) {
  const total = Math.max(0, Math.round(Number(count) || 0));
  if (total < 1000) return total + ' tok';
  if (total < 1000000) return (total / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
  return (total / 1000000).toFixed(2).replace(/\.?0+$/, '') + 'M';
}
function usageRow(label, value, kind = '') {
  const row = node('div', 'usage-row ' + kind);
  row.append(node('span', 'usage-label', label), node('strong', 'usage-value', value));
  return row;
}
function usageVersion(model) {
  const short = String(model || '').replace(/^meta\//, '').replace(/^muse-/, '').replace(/-contributor$/, '');
  return short || '—';
}
function renderUsage(summary) {
  usageSummary = summary || null;
  const pill = $('usage-pill');
  if (!pill) return;
  if (!summary) {
    $('usage-cost').textContent = '—';
    $('usage-tokens').textContent = '';
    if ($('usage-model')) $('usage-model').textContent = '';
    pill.classList.remove('warn', 'high', 'over');
    pill.title = 'Usage unavailable';
    return;
  }
  $('usage-cost').textContent = usageDollars(summary.estimated_cost_usd);
  if ($('usage-model')) $('usage-model').textContent = usageVersion(summary.model);
  $('usage-tokens').textContent = usageTokens(summary.total_tokens);
  pill.classList.remove('warn', 'high', 'over');
  if (summary.level && summary.level !== 'ok') pill.classList.add(summary.level);
  pill.setAttribute('aria-label', `Estimated API cost ${usageDollars(summary.estimated_cost_usd)}, ${summary.total_tokens} tokens, ${summary.requests} requests`);
  pill.title = `${summary.requests} requests · ${summary.total_tokens} tokens · today ${usageDollars(summary.today_cost_usd)}`;
  if ($('usage-dialog').open) fillUsageDialog();
}
function fillUsageDialog() {
  const box = $('usage-detail');
  box.replaceChildren();
  if (!usageSummary) {
    box.append(note('No usage reported yet.'));
    return;
  }
  const s = usageSummary;
  box.append(usageRow('Model', String(s.model || '').replace(/^meta\//, '')));
  box.append(usageRow('API requests', String(s.requests)));
  box.append(usageRow('Input tokens', Number(s.input_tokens).toLocaleString('en-US')));
  box.append(usageRow('Output tokens', Number(s.output_tokens).toLocaleString('en-US')));
  if (s.cached_input_tokens > 0)
    box.append(usageRow('Cached input tokens', Number(s.cached_input_tokens).toLocaleString('en-US')));
  box.append(usageRow('Spending today', usageDollars(s.today_cost_usd)));
  box.append(usageRow('Estimated API cost', usageDollars(s.estimated_cost_usd), 'usage-total'));
  box.append(note('Scanning photos and AI answers consume tokens — each LiveScan capture and question counts here.'));
  const remaining = Number(s.remaining_usd);
  box.append(usageRow('Budget remaining',
    (remaining < 0 ? 'over by ' : '') + usageDollars(Math.abs(remaining)) + ` of ${usageDollars(s.budget_usd)}`,
    remaining < 0 ? 'usage-over' : ''));
  $('usage-budget').value = s.budget_usd;
  $('usage-warn').value = s.warn_usd;
  $('usage-warn-high').value = s.warn_high_usd;
}
function initializeUsage() {
  $('usage-pill').addEventListener('click', () => { fillUsageDialog(); $('usage-dialog').showModal(); });
  $('close-usage').addEventListener('click', () => $('usage-dialog').close());
  $('usage-budget-form').addEventListener('submit', async event => {
    event.preventDefault();
    const message = $('usage-budget-message');
    message.textContent = '';
    const values = {};
    for (const [id, field] of [['usage-budget', 'budget_usd'], ['usage-warn', 'warn_usd'], ['usage-warn-high', 'warn_high_usd']]) {
      const parsed = Number($(id).value);
      if (!Number.isFinite(parsed) || parsed < 0) { message.textContent = 'Enter budget and warning amounts as numbers of 0 or more.'; return; }
      values[field] = parsed;
    }
    try {
      const summary = await api('/api/usage-budget', values);
      app.config.usage = summary;
      renderUsage(summary);
      message.textContent = 'Budget saved.';
    } catch (error) { message.textContent = error.message; }
  });
}
