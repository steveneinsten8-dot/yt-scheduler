#!/usr/bin/env python3
"""State per-pengguna untuk deploy Streamlit Cloud.

Backend: Supabase (PostgREST via requests) kalau secrets berisi [supabase],
kalau tidak -> file lokal .data/<channel_id>.json (buat ngoding lokal).

Kunci isolasi = channel_id YouTube. Dua orang login ke channel berbeda
tidak akan saling menimpa karena tiap orang baca/tulis barisnya sendiri.
"""
import json
import os

import requests
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.path.join(HERE, ".data")
TABLE = "yt_users"


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


def load(channel_id):
    """Ambil dict state milik channel_id ({} kalau belum ada)."""
    url, key = _cfg()
    if not url:
        p = os.path.join(LOCAL, f"{channel_id}.json")
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
        return {}
    r = requests.get(f"{url}/rest/v1/{TABLE}",
                     params={"channel_id": f"eq.{channel_id}", "select": "data"},
                     headers=_headers(url, key), timeout=20)
    r.raise_for_status()
    rows = r.json()
    return rows[0]["data"] if rows else {}


def save(channel_id, data):
    """Simpan/timpa state channel_id."""
    url, key = _cfg()
    if not url:
        os.makedirs(LOCAL, exist_ok=True)
        with open(os.path.join(LOCAL, f"{channel_id}.json"), "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return
    h = _headers(url, key, {"Prefer": "resolution=merge-duplicates"})
    r = requests.post(f"{url}/rest/v1/{TABLE}", headers=h, timeout=20,
                      json={"channel_id": channel_id, "data": data})
    r.raise_for_status()


def all_users():
    """{channel_id: data} semua pengguna — dipakai autochat (cron)."""
    url, key = _cfg()
    if not url:
        if not os.path.isdir(LOCAL):
            return {}
        return {f[:-5]: json.load(open(os.path.join(LOCAL, f)))
                for f in os.listdir(LOCAL) if f.endswith(".json")}
    r = requests.get(f"{url}/rest/v1/{TABLE}",
                     params={"select": "channel_id,data"},
                     headers=_headers(url, key), timeout=20)
    r.raise_for_status()
    return {x["channel_id"]: x["data"] for x in r.json()}


def _selftest():
    cid = "_selftest_channel"
    save(cid, {"schedules": [{"title": "a"}], "token": {"x": 1}})
    assert load(cid)["schedules"][0]["title"] == "a"
    assert cid in all_users()
    os.remove(os.path.join(LOCAL, f"{cid}.json"))
    print("store selftest ok")


if __name__ == "__main__":
    _selftest()
