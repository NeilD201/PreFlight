const search = document.querySelector('#search');
const severity = document.querySelector('#severity');
const focus = document.querySelector('#focus');
let current = 'review';
function filterReport() {
  const query = search.value.trim().toLowerCase();
  let count = 0;
  if (current === 'findings') {
    document.querySelectorAll('.issue-group').forEach(group => {
      let matches = 0;
      const label = group.querySelector('summary').textContent.toLowerCase();
      group.querySelectorAll('.finding').forEach(item => {
        const visible = (severity.value === 'all' || item.dataset.severity === severity.value)
          && (label + ' ' + item.textContent.toLowerCase()).includes(query);
        item.hidden = !visible;
        if (visible) matches++;
      });
      group.hidden = matches === 0;
      group.querySelector('.group-count').textContent = matches;
      if (query && matches) group.open = true;
      count += matches;
    });
  } else {
    document.querySelectorAll(`#${current} .searchable`).forEach(item => {
      const categoryMatch = current !== 'review' || focus.value === 'all'
        || (focus.value === 'attention' ? item.dataset.priority !== 'note' : item.dataset.category === focus.value);
      item.hidden = !categoryMatch || !item.textContent.toLowerCase().includes(query);
      if (!item.hidden) count++;
    });
  }
  document.querySelector('#visible-count').textContent = `${count} matches`;
  document.querySelector('#empty').hidden = count !== 0 || current === 'ai' || current === 'circuit';
}
function selectTab(button) {
  current = button.dataset.tab;
  document.querySelectorAll('[data-tab]').forEach(b => {
    b.classList.toggle('active', b === button);
    b.setAttribute('aria-pressed', String(b === button));
  });
  document.querySelectorAll('.panel').forEach(panel => panel.hidden = panel.id !== current);
  document.querySelector('#severity-label').hidden = current !== 'findings';
  document.querySelector('#focus-label').hidden = current !== 'review';
  filterReport();
}
document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => selectTab(button)));
document.querySelectorAll('[data-jump]').forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  search.value = '';
  severity.value = 'all';
  focus.value = 'all';
  selectTab(document.querySelector(`[data-tab="${link.dataset.jump}"]`));
  let id = link.getAttribute('href').slice(1);
  try { id = decodeURIComponent(id); } catch (_) { /* Literal percent in an identifier. */ }
  const target = document.getElementById(id);
  if (target) {
    for (let node = target; node; node = node.parentElement) {
      if (node.tagName === 'DETAILS') node.open = true;
    }
    target.scrollIntoView({behavior: 'smooth', block: 'center'});
    target.classList.add('highlight');
    setTimeout(() => target.classList.remove('highlight'), 1800);
  }
}));
search.addEventListener('input', filterReport);
severity.addEventListener('change', filterReport);
focus.addEventListener('change', filterReport);
filterReport();
