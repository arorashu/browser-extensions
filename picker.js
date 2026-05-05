let tabs = [];
let highlight = 0;
let lastAdvanceSeq = 0;

async function load() {
  const { mruStack = [], advanceSeq = 0 } = await chrome.storage.session.get(['mruStack', 'advanceSeq']);
  lastAdvanceSeq = advanceSeq;
  const rest = mruStack.slice(1);
  const resolved = await Promise.all(rest.map(async (id) => {
    try { return await chrome.tabs.get(id); } catch { return null; }
  }));
  tabs = resolved.filter((t) => t !== null);
  highlight = 0;
  render();
}

function makeFallback() {
  const div = document.createElement('div');
  div.className = 'fallback';
  return div;
}

function render() {
  const list = document.getElementById('list');
  list.innerHTML = '';
  if (tabs.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'No other tabs';
    list.appendChild(empty);
    return;
  }
  tabs.forEach((tab, i) => {
    const div = document.createElement('div');
    div.className = 'item' + (i === highlight ? ' active' : '');
    div.dataset.index = String(i);

    if (tab.favIconUrl) {
      const img = document.createElement('img');
      img.className = 'favicon';
      img.src = tab.favIconUrl;
      img.onerror = () => img.replaceWith(makeFallback());
      div.appendChild(img);
    } else {
      div.appendChild(makeFallback());
    }

    const title = document.createElement('div');
    title.className = 'title';
    title.textContent = tab.title || tab.url || '(untitled)';
    div.appendChild(title);

    let host = '';
    try { host = new URL(tab.url || '').host; } catch {}
    if (host) {
      const hostEl = document.createElement('div');
      hostEl.className = 'host';
      hostEl.textContent = host;
      div.appendChild(hostEl);
    }

    div.addEventListener('click', () => commit(i));
    div.addEventListener('mouseenter', () => {
      if (i !== highlight) {
        highlight = i;
        render();
      }
    });
    list.appendChild(div);
  });
  list.querySelector('.item.active')?.scrollIntoView({ block: 'nearest' });
}

function advance(direction) {
  if (tabs.length === 0) return;
  highlight = (highlight + direction + tabs.length) % tabs.length;
  render();
}

function commit(idx = highlight) {
  if (idx < 0 || idx >= tabs.length) return cancel();
  chrome.runtime.sendMessage({ type: 'commit', tabId: tabs[idx].id });
}

function cancel() {
  chrome.runtime.sendMessage({ type: 'cancel' });
}

document.addEventListener('keydown', (e) => {
  switch (e.key) {
    case 'ArrowDown':
    case 'Tab':
      e.preventDefault();
      advance(e.shiftKey ? -1 : 1);
      break;
    case 'ArrowUp':
      e.preventDefault();
      advance(-1);
      break;
    case 'Enter':
      e.preventDefault();
      commit();
      break;
    case 'Escape':
      e.preventDefault();
      cancel();
      break;
  }
});

window.addEventListener('blur', () => cancel());

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'session') return;
  if (changes.advanceSeq) {
    const newSeq = changes.advanceSeq.newValue ?? 0;
    const delta = newSeq - lastAdvanceSeq;
    lastAdvanceSeq = newSeq;
    if (delta > 0) {
      for (let i = 0; i < delta; i++) advance(1);
    }
  }
});

globalThis.__pickerState = () => ({ tabs: tabs.map((t) => ({ id: t.id, title: t.title })), highlight });

load();
