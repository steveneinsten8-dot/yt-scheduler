#!/usr/bin/env python3
"""Login Google (scope YouTube) multi-pengguna untuk Streamlit Cloud.

Kenapa bukan st.login() bawaan? OIDC bawaan hanya menyimpan id_token +
access_token (starlette_auth_routes.py:617) dan tidak ada st.user.refresh(),
padahal access token YouTube mati ~1 jam. Penjadwal butuh refresh_token, jadi
OAuth dibuat manual (web flow, redirect balik ke URL app ini).

Isolasi antar pengguna: kunci = channel_id YouTube. Cookie berisi daftar
channel_id yang login di browser itu; datanya di store.py (Supabase).
Orang lain login -> channel_id beda -> baris beda -> tidak saling menimpa.
"""
import base64
import hashlib
import hmac
import json
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
COOKIE = "ytsess"        # daftar channel_id (signed)
STATE_COOKIE = "ytstate"  # nonce anti-CSRF


def _secret(*keys, default=None):
    try:
        node = st.secrets
        for k in keys:
            node = node[k]
        return node
    except Exception:
        return default


def configured():
    return bool(_secret("google", "client_id") and _secret("google", "client_secret"))


def redirect_uri():
    """URL app ini = tempat Google memantulkan ?code=... . Otomatis dari host."""
    return (st.context.url or "").split("?")[0].rstrip("/") or "http://localhost:8501"


# ---------- cookie (Streamlit tak punya API set-cookie) ----------
def _sig(v):
    key = str(_secret("cookie_secret", default="dev-insecure")).encode()
    return hmac.new(key, v.encode(), hashlib.sha256).hexdigest()[:20]


def _pack(obj):
    v = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{v}.{_sig(v)}"


def _unpack(tok):
    if not tok or "." not in tok:
        return None
    v, s = tok.rsplit(".", 1)
    if not hmac.compare_digest(_sig(v), s):
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(v + "=" * (-len(v) % 4)))
    except Exception:
        return None


def _set_cookie(name, value, max_age=2592000):
    js = f"document.cookie='{name}={value};path=/;max-age={max_age};SameSite=Lax'"
    st.markdown(f'<img src=x onerror="{js}" style="display:none">',
                unsafe_allow_html=True)


def _cookie_accounts():
    return _unpack(st.context.cookies.get(COOKIE)) or []


# ---------- OAuth ----------
def login_url():
    state = secrets.token_urlsafe(16)
    q = urlencode({
        "client_id": _secret("google", "client_id"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",   # butuh refresh_token
        "prompt": "consent",        # paksa refresh_token terbit lagi
        "state": state,
    })
    _set_cookie(STATE_COOKIE, state, max_age=600)
    return f"{AUTH_URI}?{q}"


def exchange(code):
    r = requests.post(TOKEN_URI, timeout=20, data={
        "code": code,
        "client_id": _secret("google", "client_id"),
        "client_secret": _secret("google", "client_secret"),
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code"})
    r.raise_for_status()
    t = r.json()
    exp = datetime.now(timezone.utc) + timedelta(seconds=t.get("expires_in", 3600))
    return {"token": t["access_token"], "refresh_token": t.get("refresh_token"),
            "token_uri": TOKEN_URI,
            "client_id": _secret("google", "client_id"),
            "client_secret": _secret("google", "client_secret"),
            "scopes": t.get("scope", " ".join(SCOPES)).split(),
            "expiry": exp.isoformat()}


def creds_from_dict(d):
    d = dict(d)
    if d.get("expiry"):
        d["expiry"] = datetime.fromisoformat(d["expiry"])
    return Credentials(**d)


def channel_info(tok):
    """(channel_id, judul) dari token. channel_id = kunci isolasi pengguna."""
    yt = build("youtube", "v3", credentials=creds_from_dict(tok))
    items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        raise RuntimeError("Akun Google ini tidak punya channel YouTube.")
    return items[0]["id"], items[0]["snippet"]["title"]


def handle_callback():
    """Proses ?code=... setelah redirect Google. Set sesi + cookie (tanpa rerun)."""
    code = st.query_params.get("code")
    if not code or st.session_state.get("_done_code") == code:
        return
    state = st.query_params.get("state")
    st.query_params.clear()
    st.session_state["_done_code"] = code      # jangan tukar kode yang sama 2x
    if not state or state != st.context.cookies.get(STATE_COOKIE):
        st.error("Login ditolak: state tidak cocok (CSRF). Coba lagi.")
        st.stop()
    try:
        tok = exchange(code)
        cid, title = channel_info(tok)
    except Exception as e:
        st.error(f"Login gagal: {e}")
        st.stop()
    data = store.load(cid) or {}
    data.update(token=tok, channel_title=title)
    store.save(cid, data)
    accs = [a for a in _cookie_accounts() if a != cid] + [cid]
    st.session_state.accounts = accs
    st.session_state.channel_id = cid
    _set_cookie(COOKIE, _pack(accs))   # render di akhir run, jadi jangan rerun


def accounts():
    """Daftar channel_id yang login di browser ini."""
    if st.session_state.get("accounts") is not None:
        return st.session_state.accounts
    st.session_state.accounts = _cookie_accounts()
    return st.session_state.accounts


def current_user():
    """channel_id aktif, atau None kalau belum login."""
    if st.session_state.get("logged_out"):
        return None
    accs = accounts()
    if not accs:
        return None
    if st.session_state.get("channel_id") not in accs:
        st.session_state.channel_id = accs[0]
    return st.session_state.channel_id


def logout():
    st.session_state["logged_out"] = True
    st.session_state.pop("channel_id", None)
    st.session_state["accounts"] = []
    _set_cookie(COOKIE, "x", max_age=0)


def youtube_for(channel_id):
    """(service YouTube, data) milik channel_id; refresh token bila kedaluwarsa."""
    data = store.load(channel_id)
    if not data.get("token"):
        st.error("Sesi tidak ditemukan. Login ulang.")
        st.stop()
    creds = creds_from_dict(data["token"])
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        data["token"]["token"] = creds.token
        data["token"]["expiry"] = creds.expiry.isoformat()
        store.save(channel_id, data)
    return build("youtube", "v3", credentials=creds), data


def _selftest():
    assert _unpack(_pack(["UCa", "UCb"])) == ["UCa", "UCb"]
    assert _unpack("UCa.deadbeef") is None
    assert _unpack("garbage") is None
    c = {"token": "t", "refresh_token": "r", "token_uri": TOKEN_URI,
         "client_id": "i", "client_secret": "s", "scopes": ["x"],
         "expiry": "2099-01-01T00:00:00+00:00"}
    assert creds_from_dict(c).refresh_token == "r"
    print("auth selftest ok")


if __name__ == "__main__":
    _selftest()
