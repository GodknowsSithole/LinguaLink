"use client";

import { useEffect, useRef, useState } from "react";

// Address of the FastAPI backend (set NEXT_PUBLIC_API_URL when building for production).
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// The shapes of the data the API sends back.
type Lang = { code: string; name: string };
type User = { id: number; name: string; email: string; preferred_language: string };
type Msg = {
  id: number;
  sender_id: number;
  recipient_id: number;
  display_text: string;
  original_text: string;
  original_language: string;
  sender_name?: string;
  attachment_name?: string;
  translated_language?: string | null;
};

// Turn an API error into a message a person can read.
// FastAPI sends either a plain string or a list of validation errors.
function detailMsg(d: any, fallback: string): string {
  const x = d?.detail;
  if (typeof x === "string") return x;
  if (Array.isArray(x)) {
    const m = x
      .map((e: any) => e?.msg || "")
      .filter(Boolean)
      .join("; ");
    return m || fallback;
  }
  return fallback;
}

// The whole chat app: the login screen, the people list and the chat window.
export default function App() {
  // ----- State: what the page remembers -----
  const [tok, setTok] = useState("");
  const [mode, setMode] = useState("login");
  const [langs, setLangs] = useState<Lang[]>([]);
  const [me, setMe] = useState<User | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [search, setSearch] = useState("");
  const [active, setActive] = useState<User | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [form, setForm] = useState({
    name: "",
    email: "",
    password: "",
    preferred_language: "zul_Latn",
  });
  const [err, setErr] = useState("");
  const [uploading, setUploading] = useState(false);
  const [translatingId, setTranslatingId] = useState<number | null>(null);
  const [popup, setPopup] = useState<{ name: string; language: string; text: string } | null>(null);
  const [personMenu, setPersonMenu] = useState<number | null>(null);
  const [chatMenu, setChatMenu] = useState(false);
  const [blockedList, setBlockedList] = useState<User[] | null>(null);
  const [msgMenu, setMsgMenu] = useState<number | null>(null);

  // ----- Refs: values kept between renders without causing a re-render -----
  const ws = useRef<WebSocket | null>(null);
  const activeRef = useRef<User | null>(null);

  // On first load: fetch the language list and restore a saved login.
  useEffect(() => {
    fetch(API + "/languages")
      .then(r => r.json())
      .then(setLangs);
    const t = localStorage.getItem("token");
    if (t) setTok(t);
  }, []);

  // fetch() with the JSON header and the logged-in user's token added.
  const auth = (path: string, init: any = {}) =>
    fetch(API + path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + tok,
        ...init.headers,
      },
    });

  // When logged in: load my profile and open the real-time WebSocket.
  useEffect(() => {
    if (!tok) return;
    let cancelled = false;
    auth("/me").then(async r => {
      if (!r.ok) {
        if (r.status === 401) logout();
        return;
      }
      const m = await r.json();
      if (!cancelled) setMe(m);
    });
    const url = API.replace("http", "ws") + "/ws?access_token=" + encodeURIComponent(tok);
    ws.current = new WebSocket(url);
    ws.current.onmessage = e => {
      const p = JSON.parse(e.data);
      if (p.type === "message")
        setMsgs(old => (old.some(x => x.id === p.message.id) ? old : [...old, p.message]));
      if (p.type === "chat_cleared" && activeRef.current?.id === p.other_id) setMsgs([]);
      if (p.type === "message_deleted") setMsgs(old => old.filter(x => x.id !== p.message_id));
    };
    return () => {
      cancelled = true;
      ws.current?.close();
    };
  }, [tok]);

  // Update the people list as I type (waits 300 ms after the last key press).
  useEffect(() => {
    if (!tok) return;
    let cancelled = false;
    const h = setTimeout(() => {
      auth("/users?search=" + encodeURIComponent(search)).then(async r => {
        if (r.status === 401) return logout();
        const d = await r.json();
        if (!cancelled) setUsers(Array.isArray(d) ? d : []);
      });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(h);
    };
  }, [tok, search]);

  // Load the conversation history when I select a person.
  useEffect(() => {
    if (!active) return;
    auth("/messages/" + active.id).then(async r => {
      if (r.status === 401) return logout();
      const d = await r.json();
      setMsgs(Array.isArray(d) ? d : []);
    });
  }, [active]);

  // Keep a ref in sync so the WebSocket handler can see which chat is open.
  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  // Log in or register, then remember the token in the browser.
  async function submitAuth(e: any) {
    e.preventDefault();
    setErr("");
    const body = mode === "login" ? { email: form.email, password: form.password } : form;
    const r = await fetch(API + "/auth/" + mode, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok) return setErr(detailMsg(d, "Request failed"));
    localStorage.setItem("token", d.access_token);
    setTok(d.access_token);
  }

  // Send the typed message to the selected person (the server translates it).
  async function send(e: any) {
    e.preventDefault();
    if (!active || !text.trim()) return;
    const value = text;
    setText("");
    const r = await auth("/messages", {
      method: "POST",
      body: JSON.stringify({ recipient_id: active.id, text: value }),
    });
    if (!r.ok) setErr(detailMsg(await r.json().catch(() => ({})), "Could not send message"));
  }

  // Share a .txt, .pdf or .docx document with the selected person.
  async function uploadDocument(e: any) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    if (!active) {
      setErr("Select a conversation first");
      return;
    }
    setUploading(true);
    setErr("");
    try {
      const fd = new FormData();
      fd.append("recipient_id", String(active.id));
      fd.append("file", f);
      const r = await fetch(API + "/messages/document", {
        method: "POST",
        headers: { Authorization: "Bearer " + tok },
        body: fd,
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        setErr(detailMsg(d, "Upload failed (" + r.status + ")"));
      } else {
        const d = await r.json();
        setMsgs(old => (old.some(x => x.id === d.id) ? old : [...old, { ...d }]));
      }
    } catch (err: any) {
      setErr("Upload failed: " + (err?.message || "network error"));
    } finally {
      setUploading(false);
    }
  }

  // Save my preferred language on the server.
  async function changeLanguage(v: string) {
    await auth("/me/language", {
      method: "PATCH",
      body: JSON.stringify({ preferred_language: v }),
    });
    setMe(me ? { ...me, preferred_language: v } : me);
  }

  // Translate a shared document into my language and show it in a popup.
  async function translateMessage(id: number, name: string) {
    setTranslatingId(id);
    const r = await auth("/messages/" + id + "/translate", { method: "POST" });
    setTranslatingId(null);
    if (!r.ok) return setErr(detailMsg(await r.json(), "Translate failed"));
    const d = await r.json();
    setPopup({
      name,
      language: langs.find(l => l.code === d.translated_language)?.name || d.translated_language,
      text: d.display_text,
    });
  }

  // Download an attachment (it needs the login token, so a plain link will not work).
  async function downloadAttachment(id: number, name: string) {
    const r = await fetch(API + "/attachments/" + id, {
      headers: { Authorization: "Bearer " + tok },
    });
    if (!r.ok) return setErr("Could not download attachment");
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name || "document";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  // Remove a person from my people list.
  async function hideUser(id: number) {
    setPersonMenu(null);
    await auth("/users/" + id + "/hide", { method: "POST" });
    setUsers(old => old.filter(u => u.id !== id));
    if (active?.id === id) setActive(null);
  }

  // Block a person: they can no longer message me.
  async function blockUser(id: number) {
    setPersonMenu(null);
    await auth("/users/" + id + "/block", { method: "POST" });
    setUsers(old => old.filter(u => u.id !== id));
    if (active?.id === id) setActive(null);
  }

  // Clear this conversation for me only.
  async function deleteChatForMe() {
    if (!active) return;
    setChatMenu(false);
    const r = await auth("/messages/" + active.id, { method: "DELETE" });
    if (!r.ok) return setErr("Could not clear chat");
    setMsgs([]);
  }

  // Translate one text message again into my current language (used after a language change).
  async function retranslateMessage(id: number) {
    setTranslatingId(id);
    const r = await auth("/messages/" + id + "/translate", { method: "POST" });
    setTranslatingId(null);
    setMsgMenu(null);
    if (!r.ok) return setErr(detailMsg(await r.json(), "Re-translate failed"));
    const d = await r.json();
    setMsgs(old =>
      old.map(m =>
        m.id === id
          ? { ...m, display_text: d.display_text, translated_language: d.translated_language }
          : m
      )
    );
  }

  // Hide one message from my view only.
  async function deleteMessageForMe(id: number) {
    setMsgMenu(null);
    const r = await auth("/message/" + id + "/me", { method: "DELETE" });
    if (!r.ok) return setErr(detailMsg(await r.json(), "Could not delete message"));
    setMsgs(old => old.filter(m => m.id !== id));
  }

  // Delete one of my own messages for both people.
  async function deleteMessageForEveryone(id: number) {
    setMsgMenu(null);
    if (!confirm("Delete this message for everyone? This cannot be undone.")) return;
    const r = await auth("/message/" + id, { method: "DELETE" });
    if (!r.ok) return setErr(detailMsg(await r.json(), "Could not delete message"));
    setMsgs(old => old.filter(m => m.id !== id));
  }

  // Load and show the people I have blocked.
  async function openBlockedList() {
    const r = await auth("/users/blocked");
    if (!r.ok) return setErr("Could not load blocked users");
    const d = await r.json();
    setBlockedList(Array.isArray(d) ? d : []);
  }

  // Unblock a person.
  async function unblockUser(id: number) {
    await auth("/users/" + id + "/unblock", { method: "POST" });
    setBlockedList(old => (old ? old.filter(u => u.id !== id) : old));
  }

  // Forget the token and go back to the login screen.
  function logout() {
    localStorage.removeItem("token");
    setTok("");
    setMe(null);
  }

  // ----- Screens -----
  // Not logged in: show the login / register form.
  if (!tok)
    return (
      <main className="auth">
        <form className="box" onSubmit={submitAuth}>
          <h1>LinguaLink</h1>
          <p>Chat naturally across South African languages.</p>
          {mode === "register" && (
            <input
              placeholder="Full name"
              onChange={e => setForm({ ...form, name: e.target.value })}
            />
          )}
          <input
            type="email"
            placeholder="Email"
            onChange={e => setForm({ ...form, email: e.target.value })}
          />
          <input
            type="password"
            placeholder="Password (8+ characters)"
            onChange={e => setForm({ ...form, password: e.target.value })}
          />
          {mode === "register" && (
            <select
              value={form.preferred_language}
              onChange={e => setForm({ ...form, preferred_language: e.target.value })}
            >
              {langs.map(l => (
                <option key={l.code} value={l.code}>
                  {l.name}
                </option>
              ))}
            </select>
          )}
          {err && <b className="error">{err}</b>}
          <button>{mode === "login" ? "Log in" : "Create account"}</button>
          <a onClick={() => setMode(mode === "login" ? "register" : "login")}>
            {mode === "login" ? "Create an account" : "Already registered? Log in"}
          </a>
        </form>
      </main>
    );

  // Logged in: people on the left, the conversation on the right.
  return (
    <main className="shell">
      {/* Left side: my profile, the search box and the list of people */}
      <aside>
        <div className="sidebar-top">
          <div className="brand">LinguaLink</div>
          <div className="profile">
            <b>{me?.name}</b>
            <select value={me?.preferred_language} onChange={e => changeLanguage(e.target.value)}>
              {langs.map(l => (
                <option key={l.code} value={l.code}>
                  {l.name}
                </option>
              ))}
            </select>
          </div>
          <h3>People</h3>
          <input
            className="search"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search by name or email..."
          />
        </div>
        <div className="people-list">
          {users.length === 0 && <small className="empty-search">No users found</small>}
          {users.map(u => (
            <div className="person-row" key={u.id}>
              <button
                className={active?.id === u.id ? "person active" : "person"}
                onClick={() => {
                  setActive(u);
                  setPersonMenu(null);
                }}
              >
                <span>{u.name[0]}</span>
                <div>
                  <b>{u.name}</b>
                  <small>Receives {langs.find(l => l.code === u.preferred_language)?.name}</small>
                </div>
              </button>
              <button
                type="button"
                className="menu-dots"
                onClick={() => setPersonMenu(personMenu === u.id ? null : u.id)}
              >
                ⋮
              </button>
              {personMenu === u.id && (
                <div className="dropdown person-dropdown">
                  <button type="button" onClick={() => hideUser(u.id)}>
                    Hide from list
                  </button>
                  <button type="button" className="danger" onClick={() => blockUser(u.id)}>
                    Block user
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
        <button className="blocked-link" onClick={openBlockedList}>
          Blocked users
        </button>
        <button className="logout" onClick={logout}>
          Log out
        </button>
      </aside>

      {/* Right side: the open conversation */}
      <section className="chat">
        {!active ? (
          <div className="empty">
            <h2>Select a person</h2>
            <p>Incoming messages will automatically appear in your preferred language.</p>
          </div>
        ) : (
          <>
            <header>
              <div>
                <h2>{active.name}</h2>
                <small>Messages translated to each recipient's preference</small>
              </div>
              <div className="header-menu-wrap">
                <button type="button" className="menu-dots" onClick={() => setChatMenu(!chatMenu)}>
                  ⋮
                </button>
                {chatMenu && (
                  <div className="dropdown chat-dropdown">
                    <button type="button" onClick={deleteChatForMe}>
                      Clear chat
                    </button>
                  </div>
                )}
              </div>
            </header>

            {/* Messages: a text bubble, or an attachment with Download / Translate buttons */}
            <div className="messages">
              {msgs.map(m => (
                <div key={m.id} className={m.sender_id === me?.id ? "bubble mine" : "bubble"}>
                  <div className="msg-menu-wrap">
                    <button
                      type="button"
                      className="menu-dots small"
                      onClick={() => setMsgMenu(msgMenu === m.id ? null : m.id)}
                    >
                      ⋮
                    </button>
                    {msgMenu === m.id && (
                      <div className="dropdown msg-dropdown">
                        {m.sender_id !== me?.id && !m.attachment_name && (
                          <button
                            type="button"
                            className="retranslate-btn"
                            disabled={translatingId === m.id}
                            onClick={() => retranslateMessage(m.id)}
                          >
                            {translatingId === m.id ? "Translating..." : "Re-translate"}
                          </button>
                        )}
                        <button type="button" onClick={() => deleteMessageForMe(m.id)}>
                          Delete for me
                        </button>
                        {m.sender_id === me?.id && (
                          <button
                            type="button"
                            className="danger"
                            onClick={() => deleteMessageForEveryone(m.id)}
                          >
                            Delete for everyone
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                  {m.attachment_name ? (
                    <div className="attachment">
                      <small>📎 {m.attachment_name}</small>
                      <button
                        type="button"
                        className="download-btn"
                        onClick={() => downloadAttachment(m.id, m.attachment_name!)}
                      >
                        Download
                      </button>
                      <button
                        type="button"
                        className="translate-btn"
                        disabled={translatingId === m.id}
                        onClick={() => translateMessage(m.id, m.attachment_name!)}
                      >
                        {translatingId === m.id ? "Translating..." : "Translate"}
                      </button>
                    </div>
                  ) : (
                    <>
                      <p>{m.display_text}</p>
                      {m.display_text !== m.original_text && (
                        <small>Original: {m.original_text}</small>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>

            {/* Message box with an attach-document button */}
            <form className="composer" onSubmit={send}>
              <label
                className={uploading ? "attach-btn disabled" : "attach-btn"}
                title="Attach document"
              >
                📎
                <input
                  type="file"
                  accept=".txt,.pdf,.docx"
                  style={{
                    position: "absolute",
                    width: 1,
                    height: 1,
                    opacity: 0,
                    overflow: "hidden",
                    pointerEvents: uploading ? "none" : "auto",
                  }}
                  disabled={uploading}
                  onChange={uploadDocument}
                />
              </label>
              <input
                value={text}
                onChange={e => setText(e.target.value)}
                placeholder={uploading ? "Uploading document..." : "Type a message..."}
              />
              <button disabled={uploading}>Send</button>
            </form>
          </>
        )}
      </section>

      {/* Popup that shows a translated document */}
      {popup && (
        <div className="modal-overlay" onClick={() => setPopup(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <header>
              <h3>📎 {popup.name}</h3>
              <button type="button" className="modal-close" onClick={() => setPopup(null)}>
                ✕
              </button>
            </header>
            <small>Translated to {popup.language}</small>
            <p className="modal-text">{popup.text}</p>
          </div>
        </div>
      )}

      {/* Popup that lists the people I have blocked */}
      {blockedList && (
        <div className="modal-overlay" onClick={() => setBlockedList(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <header>
              <h3>Blocked users</h3>
              <button type="button" className="modal-close" onClick={() => setBlockedList(null)}>
                ✕
              </button>
            </header>
            {blockedList.length === 0 ? (
              <small className="empty-search">You haven't blocked anyone.</small>
            ) : (
              <div className="blocked-rows">
                {blockedList.map(u => (
                  <div className="blocked-row" key={u.id}>
                    <span>{u.name}</span>
                    <button
                      type="button"
                      className="download-btn"
                      onClick={() => unblockUser(u.id)}
                    >
                      Unblock
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </main>
  );
}
