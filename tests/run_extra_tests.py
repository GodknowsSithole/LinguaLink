"""Extra API test cases (TC-12 .. TC-23): search, hide, delete, clear chat, language change + re-translate,
DOCX/PDF upload, size limit, same-language message, validation.  Writes tests/results_extra.json.
Usage: python tests/run_extra_tests.py [base_url]
Test accounts are named Thandi/Sipho so tests/cleanup_test_users.sql removes them afterwards.
"""
import io, json, sys, time, uuid
import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
R = []

def rec(tc, name, expected, actual, ok):
    R.append({"id": tc, "name": name, "expected": expected, "actual": actual, "status": "Pass" if ok else "Fail"})
    print(f"{tc} {'PASS' if ok else 'FAIL'} - {name}: {actual}", flush=True)

def reg(name, lang):
    email = f"{name.lower()}.{uuid.uuid4().hex[:6]}@example.com"
    r = requests.post(f"{BASE}/auth/register", json={"name": name, "email": email, "password": "Passw0rd!23", "preferred_language": lang})
    tok = r.json()["access_token"]; H = {"Authorization": f"Bearer {tok}"}
    return email, H, requests.get(f"{BASE}/me", headers=H).json()["id"]

ea, HA, A = reg("Thandi", "eng_Latn"); eb, HB, B = reg("Sipho", "zul_Latn"); ec, HC, C = reg("Thandi", "eng_Latn")
def send(H, rid, text): return requests.post(f"{BASE}/messages", headers=H, json={"recipient_id": rid, "text": text})
def hist(H, other): return requests.get(f"{BASE}/messages/{other}", headers=H).json()

# TC-12 search users (case-insensitive, by e-mail fragment, no match)
s1 = [u["id"] for u in requests.get(f"{BASE}/users", headers=HA, params={"search": "SIPHO"}).json()]
s2 = [u["id"] for u in requests.get(f"{BASE}/users", headers=HA, params={"search": eb.split("@")[0]}).json()]
s3 = requests.get(f"{BASE}/users", headers=HA, params={"search": "zzzzqqxx"}).json()
rec("TC-12", "Search users (case-insensitive name, e-mail fragment, no match)", "uppercase name finds Sipho; e-mail fragment finds exactly Sipho; nonsense returns empty list",
    f"name hit={B in s1}, e-mail hit={s2 == [B]}, no match={s3 == []}", B in s1 and s2 == [B] and s3 == [])

# TC-13 hide / unhide
requests.post(f"{BASE}/users/{B}/hide", headers=HA)
hid = B not in [u["id"] for u in requests.get(f"{BASE}/users", headers=HA).json()]
requests.post(f"{BASE}/users/{B}/unhide", headers=HA)
back = B in [u["id"] for u in requests.get(f"{BASE}/users", headers=HA).json()]
rec("TC-13", "Hide and unhide a user", "hidden user disappears from the people list and returns after unhide", f"hidden={hid}, restored={back}", hid and back)

# TC-14 delete for me
m = send(HA, B, "Please bring your identity document to the clinic.").json(); mid = m["id"]
requests.delete(f"{BASE}/message/{mid}/me", headers=HB)
b_has = any(x["id"] == mid for x in hist(HB, A)); a_has = any(x["id"] == mid for x in hist(HA, B))
rec("TC-14", "Delete a message for me only", "recipient no longer sees it; sender still does", f"recipient sees={b_has}, sender sees={a_has}", (not b_has) and a_has)

# TC-15 delete for everyone (sender) and unknown message
m2 = send(HA, B, "This message will be deleted for everyone.").json(); mid2 = m2["id"]
d1 = requests.delete(f"{BASE}/message/{mid2}", headers=HA).status_code
gone = not any(x["id"] == mid2 for x in hist(HA, B)) and not any(x["id"] == mid2 for x in hist(HB, A))
d2 = requests.delete(f"{BASE}/message/99999999", headers=HA).status_code
rec("TC-15", "Delete for everyone (sender) and delete a non-existent message", "200 and message gone for both; 404 for unknown id", f"delete={d1}, gone for both={gone}, unknown={d2}", d1 == 200 and gone and d2 == 404)

# TC-16 clear chat for me
send(HA, B, "Another message for the clear chat test.")
requests.delete(f"{BASE}/messages/{A}", headers=HB)
rec("TC-16", "Clear chat for me", "caller's history is empty afterwards; the other user's history is unchanged",
    f"caller={len(hist(HB, A))} messages, other={len(hist(HA, B))} messages", len(hist(HB, A)) == 0 and len(hist(HA, B)) > 0)

# TC-17 change preferred language, then re-translate (API)
m3 = send(HA, B, "Where is the nearest clinic?").json(); mid3 = m3["id"]; before = m3["display_text"]
ch = requests.patch(f"{BASE}/me/language", headers=HB, json={"preferred_language": "sot_Latn"}).status_code
t0 = time.perf_counter(); rt = requests.post(f"{BASE}/messages/{mid3}/translate", headers=HB); secs = time.perf_counter() - t0
j = rt.json(); after = hist(HB, A); stored = [x for x in after if x["id"] == mid3][0]["display_text"]
rec("TC-17", "Change preferred language then re-translate a text message", "language saved (200); re-translation returns Sesotho text that differs from the isiZulu text and replaces the stored translation",
    f"patch={ch}, translate={rt.status_code} in {secs:.2f}s, lang={j.get('translated_language')}, changed={j.get('display_text') != before}, stored matches={stored == j.get('display_text')}",
    ch == 200 and rt.status_code == 200 and j.get("translated_language") == "sot_Latn" and j.get("display_text") != before and stored == j.get("display_text"))
requests.patch(f"{BASE}/me/language", headers=HB, json={"preferred_language": "zul_Latn"})

# TC-18 DOCX upload
from docx import Document
buf = io.BytesIO(); doc = Document(); doc.add_paragraph("Please bring your identity document to the clinic on Monday."); doc.save(buf)
up = requests.post(f"{BASE}/messages/document", headers=HA, data={"recipient_id": B}, files={"file": ("note.docx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
tr = requests.post(f"{BASE}/messages/{up.json().get('id')}/translate", headers=HB) if up.status_code == 200 else None
rec("TC-18", "Upload a .docx document and translate it", "upload 200 with extracted text; translate 200", f"upload={up.status_code}, translate={tr.status_code if tr is not None else '-'}, text='{(tr.json().get('display_text') if tr is not None else '')[:60]}'",
    up.status_code == 200 and tr is not None and tr.status_code == 200)

# TC-19 PDF upload (minimal hand-built PDF containing text)
def mini_pdf(text):
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    stream = f"BT /F1 14 Tf 72 700 Td ({text}) Tj ET"; objs[3] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    out = b"%PDF-1.4\n"; offs = []
    for i, o in enumerate(objs, 1): offs.append(len(out)); out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    x = len(out); out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode() + "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    return out + f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF".encode()
up2 = requests.post(f"{BASE}/messages/document", headers=HA, data={"recipient_id": B}, files={"file": ("note.pdf", mini_pdf("Please bring your identity document."), "application/pdf")})
rec("TC-19", "Upload a .pdf document (text extracted)", "upload 200 and the PDF text is extracted into the message", f"upload={up2.status_code}, text='{str(up2.json().get('original_text', up2.json().get('detail')))[:50]}'",
    up2.status_code == 200 and "identity" in up2.json().get("original_text", ""))

# TC-20 file too large
big = requests.post(f"{BASE}/messages/document", headers=HA, data={"recipient_id": B}, files={"file": ("big.txt", b"a" * (10 * 1024 * 1024 + 10), "text/plain")})
rec("TC-20", "Upload a file larger than 10 MB", "400 'File too large (max 10MB)'", f"{big.status_code} {big.json().get('detail')}", big.status_code == 400)

# TC-21 same language: no translation needed
t0 = time.perf_counter(); same = send(HA, C, "Good morning, how are you today?"); secs = time.perf_counter() - t0
rec("TC-21", "Message between two users with the same language", "200; text unchanged; no model call needed (fast)", f"{same.status_code}, unchanged={same.json().get('display_text') == 'Good morning, how are you today?'}, {secs:.2f}s",
    same.status_code == 200 and same.json().get("display_text") == "Good morning, how are you today?")

# TC-22 invalid language / unknown recipient
bad = requests.patch(f"{BASE}/me/language", headers=HA, json={"preferred_language": "xxx_Xxxx"}).status_code
ghost = send(HA, 99999999, "Hello there, is anyone listening?").status_code
rec("TC-22", "Invalid preferred language and unknown recipient", "400 for an unsupported language; 404 for a recipient that does not exist", f"language={bad}, recipient={ghost}", bad == 400 and ghost == 404)

# TC-23 unauthenticated access to protected endpoints
codes = {p: requests.get(f"{BASE}{p}").status_code for p in ("/users", f"/messages/{B}", "/users/blocked")}
rec("TC-23", "Protected endpoints require a token", "403 (no bearer token) on every protected route", str(codes), all(c in (401, 403) for c in codes.values()))

json.dump({"tests": R}, open("tests/results_extra.json", "w"), indent=1, ensure_ascii=False)
print(f"{sum(r['status']=='Pass' for r in R)}/{len(R)} passed")
