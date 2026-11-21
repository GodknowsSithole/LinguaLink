"""LinguaLink Chat API: REST endpoints and an authenticated WebSocket for real-time delivery.

Every message is stored in its original form, translated into the recipient's preferred
language by the NLLB-200 model, and pushed to the recipient over WebSocket.
"""

import asyncio
import os
from collections import defaultdict

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, EmailStr, Field

from .auth import current_user_id, decode, hash_password, token, verify_password
from .db import close_pool, connect, init_db
from .detect import detect_language
from .documents import MAX_CHARS, chunk_text, extract_text, save_attachment
from .languages import LANGUAGES
from .translator import get_translator, preload

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB limit for uploaded documents

app = FastAPI(title="LinguaLink Chat API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    preload()  # load the translation model in the background so the first message is not slow


@app.on_event("shutdown")
def shutdown():
    close_pool()


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class Register(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8)
    preferred_language: str


class Login(BaseModel):
    email: EmailStr
    password: str


class Preference(BaseModel):
    preferred_language: str


class SendMessage(BaseModel):
    recipient_id: int
    text: str = Field(min_length=1, max_length=3000)
    source_language: str = ""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def public_user(row):
    """The user fields that are safe to send to the browser (never the password hash)."""
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "preferred_language": row["preferred_language"],
    }


def valid_lang(code):
    if code not in LANGUAGES:
        raise HTTPException(400, "Unsupported language")


def is_blocked(c, user_a, user_b):
    """True if either user has blocked the other."""
    row = c.execute(
        "SELECT 1 FROM blocked_users WHERE (user_id=? AND other_id=?) OR (user_id=? AND other_id=?)",
        (user_a, user_b, user_b, user_a),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Languages, registration and login
# ---------------------------------------------------------------------------


@app.get("/languages")
def languages():
    return [{"code": code, "name": name} for code, name in LANGUAGES.items()]


@app.post("/auth/register")
def register(x: Register):
    valid_lang(x.preferred_language)

    try:
        with connect() as c:
            uid = c.execute(
                "INSERT INTO users(name,email,password_hash,preferred_language) "
                "VALUES(?,?,?,?) RETURNING id",
                (x.name, x.email.lower(), hash_password(x.password), x.preferred_language),
            ).fetchone()["id"]
    except UniqueViolation:
        raise HTTPException(409, "Email already registered")

    return {"access_token": token(uid), "token_type": "bearer"}


@app.post("/auth/login")
def login(x: Login):
    with connect() as c:
        user = c.execute("SELECT * FROM users WHERE email=?", (x.email.lower(),)).fetchone()

    if not user or not verify_password(x.password, user["password_hash"]):
        raise HTTPException(401, "Incorrect email or password")

    return {"access_token": token(user["id"]), "token_type": "bearer"}


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------


@app.get("/me")
def me(uid=Depends(current_user_id)):
    with connect() as c:
        user = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return public_user(user)


@app.patch("/me/language")
def preference(x: Preference, uid=Depends(current_user_id)):
    valid_lang(x.preferred_language)

    with connect() as c:
        c.execute("UPDATE users SET preferred_language=? WHERE id=?", (x.preferred_language, uid))

    return {"preferred_language": x.preferred_language}


# ---------------------------------------------------------------------------
# Finding, hiding and blocking users
# ---------------------------------------------------------------------------


@app.get("/users")
def users(search: str = "", uid=Depends(current_user_id)):
    term = search.strip()

    with connect() as c:
        if term:
            # A search ignores the "hidden" list but never shows users the caller has blocked.
            like = f"%{term}%"
            rows = c.execute(
                """
                SELECT * FROM users
                WHERE id <> ?
                  AND (name ILIKE ? OR email ILIKE ?)
                  AND id NOT IN (SELECT other_id FROM blocked_users WHERE user_id = ?)
                ORDER BY name
                """,
                (uid, like, like, uid),
            ).fetchall()
        else:
            rows = c.execute(
                """
                SELECT * FROM users
                WHERE id <> ?
                  AND id NOT IN (SELECT other_id FROM hidden_users WHERE user_id = ?)
                  AND id NOT IN (SELECT other_id FROM blocked_users WHERE user_id = ?)
                ORDER BY name
                """,
                (uid, uid, uid),
            ).fetchall()

    return [public_user(row) for row in rows]


@app.post("/users/{other_id}/hide")
def hide_user(other_id: int, uid=Depends(current_user_id)):
    with connect() as c:
        c.execute(
            "INSERT INTO hidden_users(user_id,other_id) VALUES(?,?) ON CONFLICT DO NOTHING",
            (uid, other_id),
        )
    return {"hidden": True}


@app.post("/users/{other_id}/unhide")
def unhide_user(other_id: int, uid=Depends(current_user_id)):
    with connect() as c:
        c.execute("DELETE FROM hidden_users WHERE user_id=? AND other_id=?", (uid, other_id))
    return {"hidden": False}


@app.post("/users/{other_id}/block")
def block_user(other_id: int, uid=Depends(current_user_id)):
    with connect() as c:
        c.execute(
            "INSERT INTO blocked_users(user_id,other_id) VALUES(?,?) ON CONFLICT DO NOTHING",
            (uid, other_id),
        )
        # A blocked user is also hidden from the people list.
        c.execute(
            "INSERT INTO hidden_users(user_id,other_id) VALUES(?,?) ON CONFLICT DO NOTHING",
            (uid, other_id),
        )
    return {"blocked": True}


@app.post("/users/{other_id}/unblock")
def unblock_user(other_id: int, uid=Depends(current_user_id)):
    with connect() as c:
        c.execute("DELETE FROM blocked_users WHERE user_id=? AND other_id=?", (uid, other_id))
        c.execute("DELETE FROM hidden_users WHERE user_id=? AND other_id=?", (uid, other_id))
    return {"blocked": False}


@app.get("/users/blocked")
def list_blocked(uid=Depends(current_user_id)):
    with connect() as c:
        rows = c.execute(
            """
            SELECT u.* FROM blocked_users b
            JOIN users u ON u.id = b.other_id
            WHERE b.user_id = ?
            ORDER BY u.name
            """,
            (uid,),
        ).fetchall()
    return [public_user(row) for row in rows]


# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------


@app.get("/messages/{other_id}")
def history(other_id: int, uid=Depends(current_user_id)):
    """All messages between the caller and another user, shown in the caller's language."""
    with connect() as c:
        # "Clear chat" only hides messages up to a point; newer messages still appear.
        clear = c.execute(
            "SELECT cleared_message_id FROM chat_clears WHERE user_id=? AND other_id=?",
            (uid, other_id),
        ).fetchone()
        since = clear["cleared_message_id"] if clear else 0

        # display_text is the caller's own translation when one exists, otherwise the original.
        rows = c.execute(
            """
            SELECT m.*, u.name AS sender_name,
                   COALESCE(t.translated_text, m.original_text) AS display_text
            FROM messages m
            JOIN users u ON u.id = m.sender_id
            LEFT JOIN message_translations t ON t.message_id = m.id AND t.user_id = ?
            WHERE ((sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?))
              AND m.id > ?
              AND m.id NOT IN (SELECT message_id FROM message_hidden_for WHERE user_id = ?)
            ORDER BY m.id
            """,
            (uid, uid, other_id, other_id, uid, since, uid),
        ).fetchall()

    return [dict(row) for row in rows]


@app.delete("/messages/{other_id}")
def delete_chat_for_me(other_id: int, uid=Depends(current_user_id)):
    """Clear the chat for the caller only: remember the newest message id as the cut-off."""
    with connect() as c:
        last = c.execute(
            "SELECT MAX(id) AS mx FROM messages "
            "WHERE (sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?)",
            (uid, other_id, other_id, uid),
        ).fetchone()
        newest_id = last["mx"] or 0

        c.execute(
            "INSERT INTO chat_clears(user_id,other_id,cleared_message_id) VALUES(?,?,?) "
            "ON CONFLICT(user_id,other_id) DO UPDATE SET cleared_message_id=excluded.cleared_message_id",
            (uid, other_id, newest_id),
        )

    return {"cleared_through": newest_id}


# ---------------------------------------------------------------------------
# Real-time delivery
# ---------------------------------------------------------------------------

# Open WebSocket connections, grouped by user id (one user can have several browser tabs).
connections = defaultdict(set)


async def push(uid, payload):
    """Send a JSON payload to every open connection of a user, dropping dead connections."""
    dead = []

    for ws in connections[uid]:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)

    for ws in dead:
        connections[uid].discard(ws)


# ---------------------------------------------------------------------------
# Sending messages and documents
# ---------------------------------------------------------------------------


@app.post("/messages")
async def send(x: SendMessage, uid=Depends(current_user_id)):
    """Detect the language, translate for the recipient, store both texts and push the result."""
    text = x.text.strip()

    # The detector may return "" for very short text; fall back to the language the sender chose.
    source = detect_language(text) or x.source_language
    valid_lang(source)

    with connect() as c:
        recipient = c.execute("SELECT * FROM users WHERE id=?", (x.recipient_id,)).fetchone()
        sender = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

        if not recipient:
            raise HTTPException(404, "Recipient not found")
        if is_blocked(c, uid, x.recipient_id):
            raise HTTPException(403, "You cannot message this user")

    # Translation is slow and CPU-bound, so run it in a worker thread to keep the server responsive.
    target = recipient["preferred_language"]
    translated = await asyncio.to_thread(get_translator().translate, text, source, target)

    with connect() as c:
        message_id = c.execute(
            "INSERT INTO messages(sender_id,recipient_id,original_text,original_language) "
            "VALUES(?,?,?,?) RETURNING id",
            (uid, x.recipient_id, text, source),
        ).fetchone()["id"]
        c.execute(
            "INSERT INTO message_translations(message_id,user_id,language,translated_text) "
            "VALUES(?,?,?,?)",
            (message_id, x.recipient_id, target, translated),
        )

    payload = {
        "type": "message",
        "message": {
            "id": message_id,
            "sender_id": uid,
            "recipient_id": x.recipient_id,
            "sender_name": sender["name"],
            "original_text": text,
            "original_language": source,
            "display_text": translated,
            "translated_language": target,
        },
    }

    # The recipient sees the translation; the sender sees their own original text.
    await push(x.recipient_id, payload)
    await push(uid, {**payload, "message": {**payload["message"], "display_text": text}})

    return payload["message"]


@app.post("/messages/document")
async def send_document(
    recipient_id: int = Form(...),
    file: UploadFile = File(...),
    uid=Depends(current_user_id),
):
    """Share a .txt, .pdf or .docx file. Its text is extracted now and translated on request."""
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "File too large (max 10MB)")

    text = extract_text(file.filename, data).strip()
    if not text:
        raise HTTPException(400, "No readable text found in document")

    # Very long documents are cut short so CPU translation time stays bounded.
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[Document truncated to first %d characters]" % MAX_CHARS

    with connect() as c:
        recipient = c.execute("SELECT * FROM users WHERE id=?", (recipient_id,)).fetchone()
        sender = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

        if not recipient:
            raise HTTPException(404, "Recipient not found")
        if is_blocked(c, uid, recipient_id):
            raise HTTPException(403, "You cannot message this user")

    source = detect_language(text) or "eng_Latn"
    valid_lang(source)

    attachment_path, attachment_mime = save_attachment(file.filename, data)

    with connect() as c:
        message_id = c.execute(
            "INSERT INTO messages(sender_id,recipient_id,original_text,original_language,"
            "attachment_name,attachment_path,attachment_mime) VALUES(?,?,?,?,?,?,?) RETURNING id",
            (uid, recipient_id, text, source, file.filename, attachment_path, attachment_mime),
        ).fetchone()["id"]

    payload = {
        "type": "message",
        "message": {
            "id": message_id,
            "sender_id": uid,
            "recipient_id": recipient_id,
            "sender_name": sender["name"],
            "original_text": text,
            "original_language": source,
            "display_text": text,
            "translated_language": None,
            "attachment_name": file.filename,
        },
    }

    await push(recipient_id, payload)
    await push(uid, payload)

    return payload["message"]


@app.get("/attachments/{message_id}")
def download_attachment(message_id: int, uid=Depends(current_user_id)):
    """Download an attachment. Only the sender and the recipient are allowed."""
    with connect() as c:
        message = c.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()

    if not message or uid not in (message["sender_id"], message["recipient_id"]):
        raise HTTPException(404, "Attachment not found")

    path = message["attachment_path"]
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Attachment not found")

    return FileResponse(
        path,
        media_type=message["attachment_mime"] or "application/octet-stream",
        filename=message["attachment_name"] or "document",
    )


# ---------------------------------------------------------------------------
# Re-translating and deleting messages
# ---------------------------------------------------------------------------


@app.post("/messages/{message_id}/translate")
async def translate_message(message_id: int, uid=Depends(current_user_id)):
    """Translate a message into the caller's current preferred language (cached per language)."""
    with connect() as c:
        message = c.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()

        if not message or uid not in (message["sender_id"], message["recipient_id"]):
            raise HTTPException(404, "Message not found")

        viewer = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        target = viewer["preferred_language"]

        cached = c.execute(
            "SELECT translated_text FROM message_translations "
            "WHERE message_id=? AND user_id=? AND language=?",
            (message_id, uid, target),
        ).fetchone()

    if cached:
        return {"display_text": cached["translated_text"], "translated_language": target}

    source = message["original_language"]

    # Documents are split into chunks because the model has a limit on input length.
    if message["attachment_name"]:
        chunks = chunk_text(message["original_text"])
    else:
        chunks = [message["original_text"]]

    translator = get_translator()
    parts = []
    for chunk in chunks:
        parts.append(await asyncio.to_thread(translator.translate, chunk, source, target))
    translated = "\n".join(parts)

    with connect() as c:
        c.execute(
            "INSERT INTO message_translations(message_id,user_id,language,translated_text) "
            "VALUES(?,?,?,?) ON CONFLICT(message_id,user_id) DO UPDATE "
            "SET language=excluded.language, translated_text=excluded.translated_text",
            (message_id, uid, target, translated),
        )

    return {"display_text": translated, "translated_language": target}


@app.delete("/message/{message_id}/me")
def delete_message_for_me(message_id: int, uid=Depends(current_user_id)):
    """Hide one message from the caller's view only."""
    with connect() as c:
        message = c.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()

        if not message or uid not in (message["sender_id"], message["recipient_id"]):
            raise HTTPException(404, "Message not found")

        c.execute(
            "INSERT INTO message_hidden_for(message_id,user_id) VALUES(?,?) ON CONFLICT DO NOTHING",
            (message_id, uid),
        )

    return {"hidden": True}


@app.delete("/message/{message_id}")
async def delete_message(message_id: int, uid=Depends(current_user_id)):
    """Delete a message for everyone. Only the sender may do this."""
    with connect() as c:
        message = c.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()

        if not message:
            raise HTTPException(404, "Message not found")
        if message["sender_id"] != uid:
            raise HTTPException(403, "You can only delete messages you sent")

        other_id = message["recipient_id"]

        # Remove the uploaded file too, if there is one.
        path = message["attachment_path"]
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

        c.execute("DELETE FROM message_translations WHERE message_id=?", (message_id,))
        c.execute("DELETE FROM messages WHERE id=?", (message_id,))

    # Tell both browsers to remove the message from the screen.
    await push(other_id, {"type": "message_deleted", "message_id": message_id})
    await push(uid, {"type": "message_deleted", "message_id": message_id})

    return {"deleted": True}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket(ws: WebSocket, access_token: str):
    """Authenticated WebSocket: the browser passes its JWT as ?access_token=..."""
    try:
        uid = decode(access_token)
    except HTTPException:
        await ws.close(code=1008)  # 1008 = policy violation (bad or missing token)
        return

    await ws.accept()
    connections[uid].add(ws)

    try:
        while True:
            await ws.receive_text()  # keep the connection open; the server only pushes
    except WebSocketDisconnect:
        connections[uid].discard(ws)
