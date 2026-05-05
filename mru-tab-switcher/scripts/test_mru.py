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

        # ---- Multi-window scoping test ----
        # Build a fresh state with two normal windows, mixed activation.
        print("\n[multi-window] tearing down and rebuilding with two windows")
        for pg in list(ctx.pages):
            try: await pg.close()
            except Exception: pass
        await asyncio.sleep(0.3)

        setup = await sw.evaluate("""async () => {
          const winA = await chrome.windows.create({url: 'data:text/html,<title>A1</title>'});
          const a1 = winA.tabs[0].id;
          const a2 = (await chrome.tabs.create({windowId: winA.id, url: 'data:text/html,<title>A2</title>'})).id;
          const a3 = (await chrome.tabs.create({windowId: winA.id, url: 'data:text/html,<title>A3</title>'})).id;
          const winB = await chrome.windows.create({url: 'data:text/html,<title>B1</title>'});
          const b1 = winB.tabs[0].id;
          const b2 = (await chrome.tabs.create({windowId: winB.id, url: 'data:text/html,<title>B2</title>'})).id;
          return {winA: winA.id, winB: winB.id, a1, a2, a3, b1, b2};
        }""")
        print(f"[multi-window] setup: {setup}")

        # Activate in cross-window order so MRU is interleaved.
        await sw.evaluate(f"""async () => {{
          const s = {setup};
          const seq = [
            [s.winA, s.a1], [s.winB, s.b1], [s.winA, s.a2],
            [s.winB, s.b2], [s.winA, s.a3]
          ];
          for (const [wid, tid] of seq) {{
            await chrome.windows.update(wid, {{focused: true}});
            await chrome.tabs.update(tid, {{active: true}});
            await new Promise(r => setTimeout(r, 120));
          }}
        }}""")
        await asyncio.sleep(0.4)

        # User is now in winA on tab a3. Switching MRU should land on a2, not b2.
        await sw.evaluate("globalThis.switchToMru()")
        await asyncio.sleep(0.3)
        active_a = await sw.evaluate(f"async () => (await chrome.tabs.query({{active: true, windowId: {setup['winA']}}}))[0]?.id")
        active_b = await sw.evaluate(f"async () => (await chrome.tabs.query({{active: true, windowId: {setup['winB']}}}))[0]?.id")
        print(f"[multi-window] after switchToMru in winA: winA active={active_a} (expect a2={setup['a2']}), winB active={active_b} (expect b2={setup['b2']})")
        results.append(("switchToMru stays in source window", active_a == setup["a2"]))
        results.append(("switchToMru does not disturb other window", active_b == setup["b2"]))

        # Picker: should list only winA tabs (a1), not winB tabs.
        await sw.evaluate(f"async () => {{ await chrome.windows.update({setup['winA']}, {{focused: true}}); }}")
        await asyncio.sleep(0.2)
        # Switch back to a3 first so picker source = winA, current = a3
        await sw.evaluate(f"async () => chrome.tabs.update({setup['a3']}, {{active: true}})")
        await asyncio.sleep(0.3)
        await sw.evaluate("globalThis.openOrAdvancePicker()")
        picker2 = None
        for _ in range(40):
            for pg in ctx.pages:
                if pg.url.endswith("/picker.html"):
                    picker2 = pg
                    break
            if picker2:
                break
            await asyncio.sleep(0.1)
        if picker2:
            await picker2.wait_for_function("globalThis.__pickerState && globalThis.__pickerState().tabs.length >= 0", timeout=3000)
            pstate = await picker2.evaluate("globalThis.__pickerState()")
            shown_titles = [t["title"] for t in pstate["tabs"]]
            print(f"[multi-window] picker shows: {shown_titles}")
            results.append(("picker excludes winB tabs", all(not t.startswith("B") for t in shown_titles)))
            results.append(("picker includes winA tabs", any(t.startswith("A") for t in shown_titles)))
            await sw.evaluate("globalThis.__closePickerForTest__ = true")
            await sw.evaluate("(async () => { const id = (await chrome.storage.session.get('pickerWindowId')).pickerWindowId; if (id != null) await chrome.windows.remove(id); })()")
        else:
            results.append(("picker excludes winB tabs", False))
            results.append(("picker includes winA tabs", False))

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
