#!/usr/bin/env python3
"""Penyimpanan key-value per-pengguna (Supabase, fallback file lokal).

Kunci = user_id (username app). Satu baris = satu pengguna, isinya seluruh
state miliknya: password, role, client_secret (oauth), channel YouTube,
jadwal. Isolasi antar pengguna otomatis karena tiap orang menulis barisnya
sendiri — orang lain tidak bisa menimpa akunmu.
"""
import json
import os

import requests
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.path.join(HERE, ".data")
TABLE = "yt_users"
COL = "user_id"
PENDING = "_pending_"   # baris sementara saat OAuth (kunci = state nonce)


def _secret(*keys, default=None):
    try:
        node = st.secrets
        for k in keys:
            node = node[k]
        return node
    except Exception:
        return default


def _cfg():
    """(url, key) Supabase, atau (None, None) kalau tidak dikonfigurasi."""
    url = _secret("supabase", "url")
    key = _secret("supabase", "key")
    if url and key:
        return url.rstrip("/"), key
    return None, None


def _headers(url, key, extra=None):
    h = {"apikey": key, "Authorization": f"Bearer {key}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def load(user_id):
    """Ambil dict state milik user_id ({} kalau belum ada)."""
    url, key = _cfg()
    if not url:
        p = os.path.join(LOCAL, f"{user_id}.json")
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
        return {}
    r = requests.get(f"{url}/rest/v1/{TABLE}",
                     params={COL: f"eq.{user_id}", "select": "data"},
                     headers=_headers(url, key), timeout=20)
    r.raise_for_status()
    rows = r.json()
    return rows[0]["data"] if rows else {}


def save(user_id, data):
    """Simpan/timpa state user_id."""
    url, key = _cfg()
    if not url:
        os.makedirs(LOCAL, exist_ok=True)
        with open(os.path.join(LOCAL, f"{user_id}.json"), "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return
    h = _headers(url, key, {"Prefer": "resolution=merge-duplicates"})
    r = requests.post(f"{url}/rest/v1/{TABLE}", headers=h, timeout=20,
                      json={COL: user_id, "data": data})
    r.raise_for_status()


def delete(user_id):
    url, key = _cfg()
    if not url:
        p = os.path.join(LOCAL, f"{user_id}.json")
        if os.path.exists(p):
            os.remove(p)
        return
    r = requests.delete(f"{url}/rest/v1/{TABLE}",
                        params={COL: f"eq.{user_id}"},
                        headers=_headers(url, key), timeout=20)
    r.raise_for_status()


def all_users():
    """{user_id: data} semua baris — dipakai panel admin & autochat."""
    url, key = _cfg()
    if not url:
        if not os.path.isdir(LOCAL):
            return {}
        return {f[:-5]: json.load(open(os.path.join(LOCAL, f)))
                for f in os.listdir(LOCAL) if f.endswith(".json")}
    r = requests.get(f"{url}/rest/v1/{TABLE}",
                     params={"select": f"{COL},data"},
                     headers=_headers(url, key), timeout=20)
    r.raise_for_status()
    return {x[COL]: x["data"] for x in r.json()}


def _selftest():
    uid = "_selftest_user"
    save(uid, {"role": "admin", "channels": {"UCx": {"title": "T"}}})
    assert load(uid)["role"] == "admin"
    assert uid in all_users()
    delete(uid)
    assert load(uid) == {}
    print("store selftest ok")


if __name__ == "__main__":
    _selftest()
