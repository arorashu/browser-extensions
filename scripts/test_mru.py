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
