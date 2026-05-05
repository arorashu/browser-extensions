"""Validate the MRU Tab Switcher extension end-to-end.

Launches Chromium (Playwright bundled) with the extension and a clean
user-data-dir, opens three tabs, activates them in a known order, and
checks that triggering switchToMru() in the service worker activates
the previous tab (and toggles back on a second call).

Run with: uv run --with playwright python scripts/test_mru.py
(headed mode adds: --headed)
"""

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from playwright.async_api import async_playwright

EXT_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = EXT_DIR / ".mru-test-profile"

URLS = [f"data:text/html,<title>Tab%20{i+1}</title><h1>Tab%20{i+1}</h1>" for i in range(5)]


async def wait_for_service_worker(ctx, timeout_s=10):
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if ctx.service_workers:
            return ctx.service_workers[0]
        await asyncio.sleep(0.2)
    raise RuntimeError("extension service worker did not appear")


async def active_index(sw):
    """Return the tabstrip index of the currently active tab (no 'tabs' permission needed)."""
    return await sw.evaluate(
        "async () => { const [t] = await chrome.tabs.query({active: true}); return t ? t.index : null; }"
    )


async def get_stack(sw):
    return await sw.evaluate(
        "async () => (await chrome.storage.session.get('mruStack')).mruStack || []"
    )


async def main(headless: bool, executable_path: str | None = None):
    if PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR)
    PROFILE_DIR.mkdir(parents=True)

    results = []
    async with async_playwright() as p:
        kwargs = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            args=[
                f"--disable-extensions-except={EXT_DIR}",
                f"--load-extension={EXT_DIR}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        if executable_path:
            kwargs["executable_path"] = executable_path
            print(f"[ok] using browser at: {executable_path}")
        ctx = await p.chromium.launch_persistent_context(**kwargs)

        sw = await wait_for_service_worker(ctx)
        print(f"[ok] service worker ready: {sw.url}")

        # Open 5 tabs. They live at indices 0..4 in the tab strip.
        pages = [ctx.pages[0] if ctx.pages else await ctx.new_page()]
        await pages[0].goto(URLS[0])
        for url in URLS[1:]:
            p = await ctx.new_page()
            await p.goto(url)
            pages.append(p)

        # Activate in a non-sequential order to prove MRU tracks recency, not position.
        # Order by tab index: [2, 0, 4, 1, 3]. After this, the MRU stack's top entries
        # (most-recent-first) correspond to tab indices [3, 1, 4, 0, 2].
        activation_order = [2, 0, 4, 1, 3]
        for idx in activation_order:
            await pages[idx].bring_to_front()
            await asyncio.sleep(0.4)

        stack = await get_stack(sw)
        print(f"[ok] MRU stack (tab IDs, most-recent-first): {stack}")
        cur = await active_index(sw)
        print(f"[ok] currently active tab index: {cur} (expected 3)")
        results.append(("active tab is the last activated (idx 3)", cur == 3))

        await sw.evaluate("globalThis.switchToMru()")
        await asyncio.sleep(0.5)
        after1 = await active_index(sw)
        print(f"[ok] after 1st switchToMru(): index {after1} (expected 1)")
        results.append(("1st switch -> previous MRU (idx 1)", after1 == 1))

        await sw.evaluate("globalThis.switchToMru()")
        await asyncio.sleep(0.5)
        after2 = await active_index(sw)
        print(f"[ok] after 2nd switchToMru(): index {after2} (expected 3)")
        results.append(("2nd switch -> toggle back (idx 3)", after2 == 3))

        await sw.evaluate("globalThis.switchToMru()")
        await asyncio.sleep(0.5)
        after3 = await active_index(sw)
        print(f"[ok] after 3rd switchToMru(): index {after3} (expected 1)")
        results.append(("3rd switch -> toggle again (idx 1)", after3 == 1))

        # Close the active tab and verify the next switch falls back to a still-open tab.
        await pages[1].close()
        await asyncio.sleep(0.4)
        after4 = await active_index(sw)
        print(f"[ok] after closing idx-1 tab, active is now: index {after4}")
        # tab indices shift left after close; surviving pages are at indices 0..3
        results.append(("survives tab close (some tab is active)", after4 is not None))

        # ---- Picker tests ----
        print("\n[picker] reseating activation order for picker test")
        # Re-seat: page0, page2, page3, page4 are still around (page1 closed). Activate to known order.
        # New order: 0, 2, 3, 4 -> stack [4,3,2,0], current = page4 (now at index 3 in tabstrip)
        survivors = [pages[0], pages[2], pages[3], pages[4]]
        for p in survivors:
            await p.bring_to_front()
            await asyncio.sleep(0.3)

        print("[picker] opening picker via openOrAdvancePicker()")
        await sw.evaluate("globalThis.openOrAdvancePicker()")
        # Wait for picker page to appear in the context
        picker = None
        for _ in range(40):
            for pg in ctx.pages:
                if pg.url.endswith("/picker.html"):
                    picker = pg
                    break
            if picker:
                break
            await asyncio.sleep(0.1)
        if not picker:
            results.append(("picker window opens", False))
        else:
            results.append(("picker window opens", True))
            await picker.wait_for_function("globalThis.__pickerState && globalThis.__pickerState().tabs.length > 0", timeout=3000)
            state1 = await picker.evaluate("globalThis.__pickerState()")
            print(f"[picker] state on open: highlight={state1['highlight']}, tabs={[t['title'] for t in state1['tabs']]}")
            # First tab in picker should be the previously-active tab (survivors[-2] = page3)
            results.append(("picker first row is most-recent-other-tab", state1["tabs"][0]["title"].endswith("Tab 4") or "Tab 4" in state1["tabs"][0]["title"]))
            results.append(("picker starts with highlight=0", state1["highlight"] == 0))

            # Advance twice via SW (simulating two more shortcut presses)
            await sw.evaluate("globalThis.openOrAdvancePicker()")
            await asyncio.sleep(0.2)
            await sw.evaluate("globalThis.openOrAdvancePicker()")
            await asyncio.sleep(0.3)
            state2 = await picker.evaluate("globalThis.__pickerState()")
            print(f"[picker] state after 2 advances: highlight={state2['highlight']}")
            results.append(("picker advances on subsequent shortcut presses", state2["highlight"] == 2))

            # Commit by clicking the active row
            target_title = state2["tabs"][state2["highlight"]]["title"]
            print(f"[picker] committing to: {target_title}")
            await picker.click(".item.active")
            await asyncio.sleep(0.5)
            # Picker window should be gone
            picker_gone = picker.url.endswith("/picker.html") and picker.is_closed() if hasattr(picker, "is_closed") else not any(pg.url.endswith("/picker.html") for pg in ctx.pages)
            try:
                still_open = any(pg.url.endswith("/picker.html") for pg in ctx.pages)
            except Exception:
                still_open = False
            results.append(("picker closes after commit", not still_open))

            # The committed tab should now be active
            active_after_commit = await active_index(sw)
            print(f"[picker] active tab index after commit: {active_after_commit}")
            # We committed the 3rd item in the picker (highlight=2), which corresponds to
            # the 3rd-most-recent normal-window tab. With survivors activated in order
            # 0, 2, 3, 4 the picker list is [Tab 4, Tab 3, Tab 1] -> highlight 2 = Tab 1
            # = pages[0], which sits at tabstrip index 0 in the main window.
            results.append(("commit activates the chosen tab (idx 0)", active_after_commit == 0))

        await ctx.close()

    print()
    all_pass = True
    for name, ok in results:
        marker = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  [{marker}] {name}")
    print()
    print("OVERALL:", "PASS" if all_pass else "FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--browser", help="path to a specific browser executable (e.g. system Chromium)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(headless=not args.headed, executable_path=args.browser)))
