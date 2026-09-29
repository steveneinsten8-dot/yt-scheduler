#!/usr/bin/env python3
"""Auto-kirim komentar ke live chat siaran aktif/belum live, untuk SEMUA pengguna.

Pasang di cron/scheduler tiap 1-2 menit. Aman diulang (dicatat di baris pengguna).
Di Streamlit Cloud tidak ada cron -> jalankan lewat GitHub Actions / cron server.
Catatan: PIN komentar TIDAK bisa via API YouTube — pin manual di YouTube Studio.
"""
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

import auth
import store
import schedule_streams as S


def service(ch, data):
    """Service YouTube dari token channel (tanpa Streamlit — buat cron)."""
    creds = auth.creds_from_dict(ch["token"])
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        ch["token"]["token"] = creds.token
        ch["token"]["expiry"] = creds.expiry.isoformat()
    return build("youtube", "v3", credentials=creds)


def run_channel(user, cid, ch, comments, posted):
    try:
        yt = service(ch, None)
    except Exception as e:
        print(f"[{user}/{cid}] gagal: {e}")
        return 0
    sent = 0
    for bid, title, chat_id in S.chat_broadcasts(yt):
        key = f"{user}:{cid}:{bid}"          # beda user/channel, beda key
        if title in comments and key not in posted and chat_id:
            S.post_chat(yt, chat_id, comments[title])
            posted[key] = title
            sent += 1
            print(f"[{user}/{cid}] KOMEN {title}: {comments[title]}")
    return sent


def run_user(user, data):
    comments = {it["title"]: it["comment"]
                for it in data.get("schedules", []) if it.get("comment")}
    if not comments:
        return 0
    posted = data.setdefault("posted", {})
    return sum(run_channel(user, cid, ch, comments, posted)
               for cid, ch in (data.get("channels") or {}).items()
               if ch.get("token"))


def main():
    total = 0
    for user, data in store.all_users().items():
        total += run_user(user, data)
        store.save(user, data)
    print(f"{total} komentar dikirim. Ingat: pin manual di YouTube Studio.")


if __name__ == "__main__":
    main()
