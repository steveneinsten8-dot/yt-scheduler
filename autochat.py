#!/usr/bin/env python3
"""Auto-kirim komentar ke live chat siaran aktif/belum live, untuk SEMUA pengguna.

Pasang di cron/scheduler tiap 1-2 menit. Aman diulang (dicatat di state pengguna).
Di Streamlit Cloud tidak ada cron -> jalankan lewat GitHub Actions / cron server.
Catatan: PIN komentar TIDAK bisa via API YouTube — pin manual di YouTube Studio.
"""
import auth
import store
import schedule_streams as S
from googleapiclient.discovery import build


def service(channel_id, data):
    """Service YouTube dari token tersimpan (tanpa Streamlit — buat cron)."""
    from google.auth.transport.requests import Request
    creds = auth.creds_from_dict(data["token"])
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        data["token"]["token"] = creds.token
        data["token"]["expiry"] = creds.expiry.isoformat()
    return build("youtube", "v3", credentials=creds)


def run_user(channel_id, data):
    items = data.get("schedules", [])
    comments = {it["title"]: it["comment"] for it in items if it.get("comment")}
    if not comments or not data.get("token"):
        return 0
    posted = data.setdefault("posted", {})
    try:
        yt = service(channel_id, data)
    except Exception as e:
        print(f"[{channel_id}] gagal: {e}")
        return 0
    sent = 0
    for bid, title, chat_id in S.chat_broadcasts(yt):
        if title in comments and bid not in posted and chat_id:
            S.post_chat(yt, chat_id, comments[title])
            posted[bid] = title
            sent += 1
            print(f"[{channel_id}] KOMEN {title}: {comments[title]}")
    return sent


def main():
    users = store.all_users()
    total = 0
    for cid, data in users.items():
        total += run_user(cid, data)
        store.save(cid, data)
    print(f"{total} komentar dikirim. Ingat: pin manual di YouTube Studio.")


if __name__ == "__main__":
    main()
