const STACK_KEY = 'mruStack';
const PICKER_WIN_KEY = 'pickerWindowId';
const ADVANCE_SEQ_KEY = 'advanceSeq';
const MAX_STACK = 50;
const DEBUG = true;
const NON_BROWSER_WINDOW_TYPES = new Set(['popup', 'panel', 'devtools', 'app']);

function log(...args) {
  if (DEBUG) console.log('[mru]', ...args);
}

async function getStack() {
  const { [STACK_KEY]: stack = [] } = await chrome.storage.session.get(STACK_KEY);
  return stack;
}

async function setStack(stack) {
  await chrome.storage.session.set({ [STACK_KEY]: stack });
}

async function pushTab(tabId) {
  const stack = await getStack();
  const next = [tabId, ...stack.filter((id) => id !== tabId)].slice(0, MAX_STACK);
  await setStack(next);
}

async function removeTab(tabId) {
  const stack = await getStack();
  await setStack(stack.filter((id) => id !== tabId));
}

function isBrowserWindow(win) {
  // Treat anything that isn't an explicitly-non-browser type as a regular browser window.
  // Safari sometimes returns windows without `type === 'normal'`, so we exclude only
  // known non-browser values rather than requiring 'normal' explicitly.
  return win && !NON_BROWSER_WINDOW_TYPES.has(win.type);
}

async function getCurrentNormalWindow() {
  const focused = await chrome.windows.getLastFocused();
  log('getLastFocused ->', focused && { id: focused.id, type: focused.type, focused: focused.focused });
  if (isBrowserWindow(focused)) return focused;
  const all = await chrome.windows.getAll();
  log('windows.getAll types ->', all.map((w) => ({ id: w.id, type: w.type })));
  return all.find(isBrowserWindow) || focused;
}

async function switchToMru() {
  const win = await getCurrentNormalWindow();
  if (!win) return null;
  const winId = win.id;
  const stack = await getStack();

  let skippedActive = false;
  for (let i = 0; i < stack.length; i++) {
    const targetId = stack[i];
    try {
      const tab = await chrome.tabs.get(targetId);
      if (tab.windowId !== winId) continue;
      if (!skippedActive) {
        skippedActive = true;
        continue;
      }
      await chrome.tabs.update(targetId, { active: true });
      return targetId;
    } catch {
      await removeTab(targetId);
    }
  }
  return null;
}

async function getPickerWindowId() {
  const { [PICKER_WIN_KEY]: id = null } = await chrome.storage.session.get(PICKER_WIN_KEY);
  return id;
}

async function setPickerWindowId(id) {
  await chrome.storage.session.set({ [PICKER_WIN_KEY]: id });
}

async function openOrAdvancePicker() {
  const stack = await getStack();
  if (stack.length < 2) return;

  const existingId = await getPickerWindowId();
  if (existingId !== null) {
    try {
      await chrome.windows.get(existingId);
      const { [ADVANCE_SEQ_KEY]: seq = 0 } = await chrome.storage.session.get(ADVANCE_SEQ_KEY);
      await chrome.storage.session.set({ [ADVANCE_SEQ_KEY]: seq + 1 });
      try { await chrome.windows.update(existingId, { focused: true }); } catch {}
      return;
    } catch {
      await setPickerWindowId(null);
    }
  }

  const sourceWin = await getCurrentNormalWindow();
  await chrome.storage.session.set({
    [ADVANCE_SEQ_KEY]: 0,
    pickerSourceWindowId: sourceWin ? sourceWin.id : null,
  });
  const win = await chrome.windows.create({
    url: chrome.runtime.getURL('picker.html'),
    type: 'popup',
    width: 460,
    height: 480,
    focused: true,
  });
  await setPickerWindowId(win.id);
}

async function closePicker() {
  const id = await getPickerWindowId();
  if (id !== null) {
    try { await chrome.windows.remove(id); } catch {}
  }
  await setPickerWindowId(null);
}

async function commitTab(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId);
    // Stay within source window if the tab still lives there; otherwise also focus its window.
    const { pickerSourceWindowId = null } = await chrome.storage.session.get('pickerSourceWindowId');
    if (pickerSourceWindowId !== null && tab.windowId !== pickerSourceWindowId) {
      await chrome.windows.update(tab.windowId, { focused: true });
    } else if (pickerSourceWindowId !== null) {
      await chrome.windows.update(pickerSourceWindowId, { focused: true });
    }
    await chrome.tabs.update(tabId, { active: true });
  } catch {
    await removeTab(tabId);
  }
}

globalThis.switchToMru = switchToMru;
globalThis.openOrAdvancePicker = openOrAdvancePicker;

chrome.tabs.onActivated.addListener(async ({ tabId, windowId }) => {
  log('onActivated', { tabId, windowId });
  try {
    const win = await chrome.windows.get(windowId);
    log('  window ->', { id: win.id, type: win.type });
    if (!isBrowserWindow(win)) {
      log('  skipping non-browser window');
      return;
    }
  } catch (e) {
    // If we can't read the window (Safari sometimes denies this for popups),
    // be tolerant and still record the tab. The picker filter will sort it out.
    log('  windows.get failed, recording anyway:', e && e.message);
  }
  await pushTab(tabId);
  if (DEBUG) {
    const stack = await getStack();
    log('  stack now', stack);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  removeTab(tabId);
});

chrome.windows.onRemoved.addListener(async (winId) => {
  const id = await getPickerWindowId();
  if (id === winId) await setPickerWindowId(null);
});

chrome.commands.onCommand.addListener((command) => {
  if (command === 'switch-mru') switchToMru();
  else if (command === 'open-picker') openOrAdvancePicker();
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === 'commit' && typeof msg.tabId === 'number') {
    commitTab(msg.tabId).then(closePicker);
  } else if (msg && msg.type === 'cancel') {
    closePicker();
  }
});

chrome.runtime.onInstalled.addListener(async () => {
  const tabs = await chrome.tabs.query({ active: true });
  for (const tab of tabs) {
    if (typeof tab.id === 'number') await pushTab(tab.id);
  }
});
