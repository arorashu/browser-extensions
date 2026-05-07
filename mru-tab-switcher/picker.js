let tabs = [];
let highlight = 0;
let lastAdvanceSeq = 0;

let thumbs = {};

async function load() {
  const { mruStack = [], advanceSeq = 0, pickerSourceWindowId = null, thumbs: storedThumbs = {} } =
    await chrome.storage.session.get(['mruStack', 'advanceSeq', 'pickerSourceWindowId', 'thumbs']);
  lastAdvanceSeq = advanceSeq;
  thumbs = storedThumbs;
  console.log('[picker] mruStack', mruStack, 'sourceWin', pickerSourceWindowId, 'thumbs', Object.keys(thumbs).length);
  const resolved = await Promise.all(mruStack.map(async (id) => {
    try { return await chrome.tabs.get(id); } catch { return null; }
  }));
  console.log('[picker] resolved tabs', resolved.map((t) => t && {
    id: t.id, windowId: t.windowId, title: t.title, url: t.url, favIconUrl: t.favIconUrl,
  }));
  const inWindow = resolved.filter(
    (t) => t !== null && (pickerSourceWindowId === null || t.windowId === pickerSourceWindowId)
  );
  tabs = inWindow.slice(1);
  console.log('[picker] showing', tabs.length, 'tabs');
  highlight = 0;
  render();
}

function makeFaviconFallback() {
  const div = document.createElement('div');
  div.className = 'favicon-fallback';
  return div;
}

function makeThumbFallback() {
  const div = document.createElement('div');
  div.className = 'thumb-fallback';
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
    const card = document.createElement('div');
    card.className = 'item' + (i === highlight ? ' active' : '');
    card.dataset.index = String(i);

    const thumbWrap = document.createElement('div');
    thumbWrap.className = 'thumb-wrap';
    const thumbUrl = thumbs[String(tab.id)];
    if (thumbUrl) {
      const img = document.createElement('img');
      img.className = 'thumb';
      img.src = thumbUrl;
      img.onerror = () => img.replaceWith(makeThumbFallback());
      thumbWrap.appendChild(img);
    } else {
      thumbWrap.appendChild(makeThumbFallback());
    }
    card.appendChild(thumbWrap);

    const meta = document.createElement('div');
    meta.className = 'meta';
    if (tab.favIconUrl) {
      const img = document.createElement('img');
      img.className = 'favicon';
      img.src = tab.favIconUrl;
      img.onerror = () => img.replaceWith(makeFaviconFallback());
      meta.appendChild(img);
    } else {
      meta.appendChild(makeFaviconFallback());
    }
    const title = document.createElement('div');
    title.className = 'title';
    title.textContent = tab.title || tab.url || '(untitled)';
    meta.appendChild(title);
    card.appendChild(meta);

    card.addEventListener('click', () => commit(i));
    card.addEventListener('mouseenter', () => {
      if (i !== highlight) {
        highlight = i;
        render();
      }
    });
    list.appendChild(card);
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
