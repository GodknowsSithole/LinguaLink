"""UI test: change preferred language, then re-translate an older received text message.

Needs the stack running (API :8000, web :3000, PostgreSQL) and the demo data (tests/seed_demo.py).
Run:  python tests/test_retranslate_ui.py
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = "http://localhost:3000"
PASSWORD = "Demo@12345"
SCREENSHOTS = os.path.join(os.path.dirname(__file__), "..", "assignments", "screenshots")
os.makedirs(SCREENSHOTS, exist_ok=True)

# The message under test: Thandi's English "Thank you..." message, shown to Sipho in his language.
# We find it by the "Original: ..." line, so the test does not depend on the exact translation.
ORIGINAL = "Original: Thank you. Please bring your identity document."


def wait_for_text_change(bubble, old_text, timeout=240):
    """Wait until the message text differs from `old_text`; return the new text."""
    start = time.time()
    while time.time() - start < timeout:
        current = bubble.locator("p").inner_text()
        if current != old_text:
            return current
        time.sleep(1)
    raise TimeoutError("The message text did not change after Re-translate")


def re_translate(page, bubble):
    bubble.locator("button.menu-dots").click()
    option = bubble.locator("button.retranslate-btn")
    assert option.count() == 1, "Re-translate option is missing"
    option.click()


with sync_playwright() as p:
    browser = p.chromium.launch(
        channel="msedge",
        headless=True,
        args=["--disable-gpu", "--disable-software-rasterizer"],
    )
    page = browser.new_context(viewport={"width": 1280, "height": 760}).new_page()

    # Log in as Sipho (isiZulu) and open the conversation with Thandi.
    page.goto(URL)
    page.wait_for_selector("input[placeholder=Email]")
    page.fill("input[placeholder=Email]", "sipho@lingualink.example.com")
    page.fill("input[placeholder^=Password]", PASSWORD)
    page.click("button:has-text('Log in')")
    page.wait_for_selector("button.person")
    page.click("button.person:has-text('Thandi')")

    bubble = page.locator(".bubble", has_text=ORIGINAL)
    bubble.wait_for(timeout=60000)
    before = bubble.locator("p").inner_text()
    print("1. Message shown in Sipho's language:", before.encode("ascii", "replace").decode())

    # Change the preferred language to Sesotho. Old messages must NOT change by themselves.
    page.select_option(".profile select", label="Sesotho")
    page.wait_for_timeout(1500)
    unchanged = bubble.locator("p").inner_text() == before
    print("2. Old message unchanged after the language change (expected):", unchanged)
    page.screenshot(path=os.path.join(SCREENSHOTS, "06_before_retranslate.png"))

    # Re-translate from the message menu.
    started = time.time()
    re_translate(page, bubble)
    after = wait_for_text_change(bubble, before)
    print(f"3. Re-translated in {time.time() - started:.1f}s:", after.encode("ascii", "replace").decode())
    page.wait_for_timeout(1000)
    page.screenshot(path=os.path.join(SCREENSHOTS, "07_after_retranslate.png"))

    # The option must not appear on the user's own messages.
    page.locator(".bubble.mine").first.locator("button.menu-dots").click()
    hidden_on_own = page.locator(".bubble.mine button.retranslate-btn").count() == 0
    print("4. Option hidden on own messages:", hidden_on_own)

    # Put the demo data back: language isiZulu and the message translated into isiZulu again.
    page.select_option(".profile select", label="isiZulu")
    page.wait_for_timeout(1500)
    re_translate(page, bubble)
    restored = wait_for_text_change(bubble, after)
    print("5. Demo data restored:", restored == before)

    passed = unchanged and hidden_on_own and after != before
    print("RESULT:", "PASS" if passed else "FAIL")
    browser.close()
    sys.exit(0 if passed else 1)
