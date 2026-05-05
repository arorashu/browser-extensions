const STACK_KEY = 'mruStack';
const MAX_STACK = 50;

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

async function switchToMru() {
  let stack = await getStack();
  while (stack.length >= 2) {
    const targetId = stack[1];
    try {
      const tab = await chrome.tabs.get(targetId);
      await chrome.windows.update(tab.windowId, { focused: true });
      await chrome.tabs.update(targetId, { active: true });
      return targetId;
    } catch {
      await removeTab(targetId);
      stack = await getStack();
    }
  }
  return null;
}

globalThis.switchToMru = switchToMru;

chrome.tabs.onActivated.addListener(({ tabId }) => {
  pushTab(tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  removeTab(tabId);
});

chrome.commands.onCommand.addListener((command) => {
  if (command === 'switch-mru') switchToMru();
});

chrome.runtime.onInstalled.addListener(async () => {
  const tabs = await chrome.tabs.query({ active: true });
  for (const tab of tabs) {
    if (typeof tab.id === 'number') await pushTab(tab.id);
  }
});
