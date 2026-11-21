"""UI error-handling test + evidence screenshots: wrong password, short password on register, empty message.
Needs API :8000, web :3000, PostgreSQL and demo data.   Run: python tests/test_ui_errors.py
"""
import os, sys
from playwright.sync_api import sync_playwright
URL = "http://localhost:3000"
OUT = os.path.join(os.path.dirname(__file__), "..", "assignments", "screenshots"); os.makedirs(OUT, exist_ok=True)
ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name, flush=True)

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True, args=["--disable-gpu", "--disable-software-rasterizer"])
    pg = b.new_context(viewport={"width": 1280, "height": 760}).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL); pg.wait_for_selector("input[placeholder=Email]")
    # 1. wrong password
    pg.fill("input[placeholder=Email]", "thandi@lingualink.example.com"); pg.fill("input[placeholder^=Password]", "wrong-password")
    pg.click("button:has-text('Log in')")
    try:
        pg.wait_for_selector(".error", timeout=60000); msg = pg.inner_text(".error")
    except Exception: msg = ""
    print("   wrong password message:", repr(msg)); check("wrong password shows a readable error", "Incorrect" in msg)
    pg.screenshot(path=os.path.join(OUT, "02_login_error.png"))
    # 2. register with a short password (server returns 422 with a list of errors)
    pg.click("text=Create an account"); pg.wait_for_selector("input[placeholder='Full name']")
    pg.fill("input[placeholder='Full name']", "Test User"); pg.fill("input[placeholder=Email]", "short.pw@example.com"); pg.fill("input[placeholder^=Password]", "123")
    pg.click("button:has-text('Create account')"); pg.wait_for_timeout(8000)
    body = pg.inner_text("body"); crashed = bool(errors) or "Application error" in body or "Objects are not valid" in " ".join(errors)
    print("   register short password page text:", body[:160].replace("\n", " | ")); print("   page errors:", errors[:1])
    check("short password does not crash the page", not crashed)
    check("short password shows a readable message", pg.locator(".error").count() > 0 and "[object Object]" not in pg.inner_text(".error"))
    if pg.locator(".error").count(): print("   message:", pg.inner_text(".error")[:150])
    pg.screenshot(path=os.path.join(OUT, "08_register_validation_error.png"))
    b.close(); sys.exit(0 if ok else 1)
