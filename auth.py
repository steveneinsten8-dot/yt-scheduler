#!/usr/bin/env python3
"""Login app (username+password, role admin/user) + hubungkan channel YouTube.

Identitas pengguna = username app (bukan channel). Jadi:
  - Daftar/masuk pakai username+password (hash pbkdf2, stdlib).
  - Role: 'admin' (lihat/kelola semua pengguna) dan 'user'.
  - Tiap pengguna upload client_secret.json miliknya sendiri di dalam app,
    lalu klik "Hubungkan channel" -> OAuth Google -> token (dengan refresh_token)
    disimpan di baris pengguna itu. Tidak saling menimpa.

Kenapa OAuth Google manual, bukan st.login()? OIDC bawaan Streamlit hanya
menyimpan id_token+access_token (starlette_auth_routes.py:617) dan membuang
refresh_token; access token YouTube mati ~1 jam. Penjadwal butuh refresh_token.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import store

SCOPES = ["https://www.googleapis.com/auth/youtube"]
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
COOKIE = "ytuser"        # user_id yang login (signed)
STATE_COOKIE = "ytstate"  # nonce anti-CSRF
ITER = 200_000
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")


def _secret(*keys, default=None):
    try:
        node = st.secrets
        for k in keys:
            node = node[k]
        return node
    except Exception:
        return default


# ---------- cookie (Streamlit tak punya API set-cookie) ----------
def _cookie_key():
    """Kunci tanda tangan cookie: [auth].cookie_secret, fallback top-level."""
    return str(_secret("auth", "cookie_secret")
               or _secret("cookie_secret")
               or "dev-insecure").encode()


def _sig(v):
    return hmac.new(_cookie_key(), v.encode(), hashlib.sha256).hexdigest()[:20]


def _pack(v):
    return f"{v}.{_sig(v)}"


def _unpack(tok):
    if not tok or "." not in tok:
        return None
    v, s = tok.rsplit(".", 1)
    return v if hmac.compare_digest(_sig(v), s) else None


def _set_cookie(name, value, max_age=2592000):
    js = f"document.cookie='{name}={value};path=/;max-age={max_age};SameSite=Lax'"
    st.markdown(f'<img src=x onerror="{js}" style="display:none">',
                unsafe_allow_html=True)


def redirect_uri():
    """URL app ini = tempat Google memantulkan ?code=... .

    Bisa dipin lewat [google] redirect_uri di secrets (paling aman, nilainya
    tidak berubah). Kalau tidak dipin, dihitung dari URL yang diakses. Harus
    didaftarkan PERSIS sama di Google Console -> Authorized redirect URIs.
    """
    pinned = _secret("google", "redirect_uri") or _secret("redirect_uri")
    if pinned:
        return str(pinned).rstrip("/")
    url = (st.context.url or "").split("?")[0].rstrip("/")
    if not url:
        return "http://localhost:8501"
    if url.startswith("http://") and "localhost" not in url and "127.0.0.1" not in url:
        url = "https://" + url[len("http://"):]      # di belakang proxy selalu https
    return url


def secrets_config():
    """OAuth client dari secrets.toml (dipakai bersama), atau None."""
    cid = _secret("google", "client_id")
    csec = _secret("google", "client_secret")
    if cid and csec:
        return {"client_id": cid, "client_secret": csec, "source": "secrets"}
    return None


# ---------- password (pbkdf2, stdlib) ----------
def hash_pw(pw):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), ITER)
    return f"pbkdf2${ITER}${salt}${h.hex()}"


def verify_pw(pw, stored):
    try:
        _, iters, salt, hexd = stored.split("$")
        h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters))
    except Exception:
        return False
    return hmac.compare_digest(h.hex(), hexd)


# ---------- akun ----------
def _check(username, password):
    if not USERNAME_RE.match(username or ""):
        raise ValueError("Username 3–32 karakter (huruf/angka . _ -).")
    if len(password or "") < 6:
        raise ValueError("Password minimal 6 karakter.")


def register(username, password, role="user"):
    """Buat akun baru. ValueError kalau username tidak valid / sudah dipakai."""
    _check(username, password)
    if store.load(username):
        raise ValueError("Username sudah dipakai.")
    store.save(username, {"role": role, "pw": hash_pw(password),
                          "channels": {}, "schedules": []})
    return username


def authenticate(username, password):
    """True kalau username+password cocok."""
    data = store.load(username or "")
    return bool(data) and verify_pw(password or "", data.get("pw", ""))


def ensure_admin():
    """Buat/promosikan admin dari secrets [auth] admin_user/admin_password."""
    u = _secret("auth", "admin_user")
    p = _secret("auth", "admin_password")
    if not u or not p:
        return
    data = store.load(u)
    if not data:
        store.save(u, {"role": "admin", "pw": hash_pw(p), "channels": {}, "schedules": []})
    elif data.get("role") != "admin":
        data["role"] = "admin"
        store.save(u, data)


def login(username):
    st.session_state["user"] = username
    st.session_state.pop("logged_out", None)
    _set_cookie(COOKIE, _pack(username))


def logout():
    st.session_state["user"] = None
    st.session_state["logged_out"] = True
    for k in [k for k in st.session_state if k.startswith("_login_")]:
        del st.session_state[k]
    _set_cookie(COOKIE, "x", max_age=0)


def current_user():
    """username yang login, atau None."""
    if st.session_state.get("logged_out"):
        return None
    if st.session_state.get("user"):
        return st.session_state.user
    raw = st.context.cookies.get(COOKIE)
    uid = _unpack(raw) if raw else None
    if uid and store.load(uid):
        st.session_state.user = uid
        return uid
    return None


def role(username):
    return (store.load(username) or {}).get("role", "user")


def all_users():
    """{username: data} — untuk panel admin."""
    return store.all_users()


def set_role(username, new_role):
    data = store.load(username)
    if data:
        data["role"] = new_role
        store.save(username, data)


def delete_user(username):
    store.delete(username)


# ---------- client_secret.json ----------
def parse_client_secret(raw):
    """Bytes/str client_secret.json -> {client_id, client_secret}. Harus tipe 'web'."""
    try:
        d = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except Exception as e:
        raise ValueError(f"bukan JSON yang valid: {e}")
    if "web" not in d:
        tipe = "Desktop app" if "installed" in d else "tidak dikenal"
        raise ValueError(f"file ini tipe '{tipe}'. Buat OAuth client tipe "
                         "'Web application' di Google Cloud Console.")
    node = d["web"]
    cid, csec = node.get("client_id"), node.get("client_secret")
    if not cid or not csec:
        raise ValueError("tidak ada client_id/client_secret di file ini")
    return {"client_id": cid, "client_secret": csec, "source": "upload"}


def oauth_config(username):
    """Config OAuth untuk user ini: miliknya sendiri, atau punya admin (secrets)."""
    own = (store.load(username) or {}).get("oauth")
    return own or secrets_config()


def login_url(cfg, username):
    """URL consent Google. Config disimpan di baris user (kita sudah tahu siapa dia)."""
    data = store.load(username) or {}
    data["oauth"] = cfg                      # persist supaya ada saat callback
    store.save(username, data)
    state = secrets.token_urlsafe(16)
    _set_cookie(STATE_COOKIE, state, max_age=600)
    q = urlencode({
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",   # butuh refresh_token
        "prompt": "consent",        # paksa refresh_token terbit lagi
        "state": state,
    })
    return f"{AUTH_URI}?{q}"


def exchange(code, cfg):
    r = requests.post(TOKEN_URI, timeout=20, data={
        "code": code, "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code"})
    r.raise_for_status()
    t = r.json()
    exp = datetime.now(timezone.utc) + timedelta(seconds=t.get("expires_in", 3600))
    return {"token": t["access_token"], "refresh_token": t.get("refresh_token"),
            "token_uri": TOKEN_URI,
            "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
            "scopes": t.get("scope", " ".join(SCOPES)).split(),
            "expiry": exp.isoformat()}


def creds_from_dict(d):
    d = dict(d)
    if d.get("expiry"):
        d["expiry"] = datetime.fromisoformat(d["expiry"])
    return Credentials(**d)


def channel_info(tok):
    """(channel_id, judul) dari token."""
    yt = build("youtube", "v3", credentials=creds_from_dict(tok))
    items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        raise RuntimeError("Akun Google ini tidak punya channel YouTube.")
    return items[0]["id"], items[0]["snippet"]["title"]


def handle_callback():
    """Proses ?code=... setelah balik dari Google. Simpan channel ke baris user."""
    code = st.query_params.get("code")
    if not code or st.session_state.get("_done_code") == code:
        return
    state = st.query_params.get("state")
    st.query_params.clear()
    st.session_state["_done_code"] = code
    user = current_user()
    if not user:
        st.error("Sesi login app hilang. Masuk lagi, lalu hubungkan channel.")
        st.stop()
    if not state or state != st.context.cookies.get(STATE_COOKIE):
        st.error("Ditolak: state tidak cocok (CSRF). Coba lagi.")
        st.stop()
    cfg = oauth_config(user)
    if not cfg:
        st.error("Config OAuth tidak ada. Upload client_secret.json dulu.")
        st.stop()
    try:
        tok = exchange(code, cfg)
        cid, title = channel_info(tok)
    except Exception as e:
        st.error(f"Gagal menghubungkan channel: {e}")
        st.stop()
    data = store.load(user)
    data.setdefault("channels", {})[cid] = {"title": title, "token": tok}
    data["channel_id"] = cid
    store.save(user, data)
    st.session_state["channel_id"] = cid
    for k in [k for k in st.session_state if k.startswith("_login_")]:
        del st.session_state[k]


def youtube_for(user, channel_id):
    """(service YouTube, data user) untuk channel ini; refresh token bila perlu."""
    data = store.load(user)
    ch = (data.get("channels") or {}).get(channel_id)
    if not ch:
        st.error("Channel tidak ditemukan. Hubungkan ulang.")
        st.stop()
    creds = creds_from_dict(ch["token"])
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        ch["token"]["token"] = creds.token
        ch["token"]["expiry"] = creds.expiry.isoformat()
        store.save(user, data)
    return build("youtube", "v3", credentials=creds), data


def _selftest():
    assert _unpack(_pack("alice")) == "alice"
    assert _unpack("alice.deadbeef") is None
    assert _unpack("garbage") is None
    h = hash_pw("rahasia123")
    assert verify_pw("rahasia123", h) and not verify_pw("salah", h)
    for bad in ("ab", "a" * 33, "ada spasi", ""):
        try:
            _check(bad, "x" * 6)
            raise AssertionError(f"username harus ditolak: {bad!r}")
        except ValueError:
            pass
    try:
        _check("budi", "123")
        raise AssertionError("password pendek harus ditolak")
    except ValueError:
        pass
    # client_secret.json
    cfg = parse_client_secret(json.dumps({"web": {"client_id": "C", "client_secret": "S"}}))
    assert cfg["client_id"] == "C" and cfg["source"] == "upload"
    for bad in (b"not json",
                json.dumps({"installed": {"client_id": "C", "client_secret": "S"}}),
                json.dumps({"web": {"client_id": "C"}})):
        try:
            parse_client_secret(bad)
            raise AssertionError(f"harus ditolak: {bad}")
        except ValueError:
            pass
    c = {"token": "t", "refresh_token": "r", "token_uri": TOKEN_URI,
         "client_id": "i", "client_secret": "s", "scopes": ["x"],
         "expiry": "2099-01-01T00:00:00+00:00"}
    assert creds_from_dict(c).refresh_token == "r"
    print("auth selftest ok")


if __name__ == "__main__":
    _selftest()
