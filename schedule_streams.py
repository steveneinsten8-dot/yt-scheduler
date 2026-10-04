#!/usr/bin/env python3
"""Buat jadwal live streaming YouTube dari schedules.json (YouTube Data API v3)."""
import argparse, io, json, os, pickle, sys, time
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/youtube"]
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.path.join(HERE, "token.pickle")          # token akun 'default'
ACCOUNTS = os.path.join(HERE, "accounts")           # accounts/<nama>/{client_secret.json,token.pickle}


def account_dir(account=None):
    """Folder profil akun. None/'default' = folder utama (kompatibel lama)."""
    if not account or account == "default":
        return HERE
    return os.path.join(ACCOUNTS, account)


def secret_path(account=None):
    p = os.path.join(account_dir(account), "client_secret.json")
    return p if os.path.exists(p) else os.path.join(HERE, "client_secret.json")


def token_path(account=None):
    return os.path.join(account_dir(account), "token.pickle")


def accounts():
    """Nama profil akun yang ada (default dulu, lalu urut abjad)."""
    names = ["default"] if os.path.exists(os.path.join(HERE, "client_secret.json")) else []
    if os.path.isdir(ACCOUNTS):
        names += sorted(n for n in os.listdir(ACCOUNTS)
                        if os.path.isdir(os.path.join(ACCOUNTS, n)))
    return names


def save_secret(account, data):
    """Simpan client_secret.json (bytes) ke profil akun. Return path."""
    d = account_dir(account)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "client_secret.json")
    with open(p, "wb") as f:
        f.write(data)
    return p


def has_token(account=None):
    return os.path.exists(token_path(account))


def login(account=None):
    """OAuth di browser (paksa pilih akun), simpan token ke profil akun."""
    flow = InstalledAppFlow.from_client_secrets_file(secret_path(account), SCOPES)
    creds = flow.run_local_server(port=0, prompt="select_account")
    with open(token_path(account), "wb") as f:
        pickle.dump(creds, f)
    return creds


def youtube(account=None):
    creds = None
    tp = token_path(account)
    if os.path.exists(tp):
        with open(tp, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = login(account)
        with open(tp, "wb") as f:
            pickle.dump(creds, f)
    return build("youtube", "v3", credentials=creds)


def whoami(yt):
    """Nama channel dari akun yang sedang dipakai (konfirmasi akun benar)."""
    items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
    return items[0]["snippet"]["title"] if items else "(tidak ada channel)"


def to_rfc3339(s):
    """'2025-06-01 20:00' / ISO -> RFC3339 UTC. Tanpa offset dianggap waktu lokal."""
    dt = datetime.fromisoformat(s.replace(" ", "T"))
    if dt.tzinfo is None:
        dt = dt.astimezone()
    dt = dt.astimezone(timezone.utc)
    if dt <= datetime.now(timezone.utc):
        raise ValueError(f"jadwal '{s}' sudah lewat (harus di masa depan)")
    return dt.isoformat().replace("+00:00", "Z")


def ensure_playlist(yt, title, privacy="public"):
    """Cari playlist milik channel ini dengan judul sama; buat kalau belum ada.
    Mengembalikan playlistId."""
    req = yt.playlists().list(part="snippet", mine=True, maxResults=50)
    while req:
        res = req.execute()
        for p in res.get("items", []):
            if p["snippet"]["title"] == title:
                return p["id"]
        req = yt.playlists().list_next(req, res)
    body = {"snippet": {"title": title},
            "status": {"privacyStatus": privacy}}
    return with_retry(lambda: yt.playlists().insert(
        part="snippet,status", body=body).execute())["id"]


def add_to_playlist(yt, playlist_id, video_id):
    """Tambahkan video ke playlist (idempotent: abaikan kalau sudah ada)."""
    try:
        with_retry(lambda: yt.playlistItems().insert(part="snippet", body={
            "snippet": {"playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id}}
        }).execute())
    except HttpError as e:
        if e.resp.status != 409:      # 409 = sudah ada di playlist
            raise


def set_thumbnail(yt, video_id, data):
    """Set thumbnail video dari bytes gambar (JPEG/PNG, <2 MB)."""
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="image/jpeg", resumable=False)
    return with_retry(lambda: yt.thumbnails().set(
        videoId=video_id, media_body=media).execute())


def set_banner(yt, data):
    """Upload + pasang banner channel. `data` = bytes JPEG/PNG.
    Dua langkah: channelBanners.insert -> URL, lalu channels.update."""
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
    res = with_retry(lambda: yt.channelBanners().insert(
        media_body=media).execute()) or {}
    url = res["url"]
    mine = with_retry(lambda: yt.channels().list(part="brandingSettings", mine=True).execute())
    ch = mine["items"][0]
    bs = dict(ch.get("brandingSettings") or {})
    bs.setdefault("image", {})["bannerExternalUrl"] = url
    return with_retry(lambda: yt.channels().update(
        part="brandingSettings",
        body={"id": ch["id"], "brandingSettings": bs}).execute())


def ensure_stream(yt, title="Auto Stream"):
    """Pakai liveStream milik channel; buat otomatis jika belum ada."""
    result = with_retry(lambda: yt.liveStreams().list(
        part="id,snippet,cdn", mine=True, maxResults=50).execute()) or {}
    streams = result.get("items", [])
    if streams:
        return streams[0]["id"]
    body = {
        "snippet": {"title": title},
        "cdn": {"ingestionType": "rtmp", "resolution": "variable", "frameRate": "variable"},
    }
    return (with_retry(lambda: yt.liveStreams().insert(
        part="snippet,cdn", body=body).execute()) or {})["id"]


def create(yt, item, stream_id, thumbnail=None):
    body = {
        "snippet": {
            "title": item["title"],
            "scheduledStartTime": to_rfc3339(item["start"]),
        },
        "status": {
            "privacyStatus": item.get("privacy", "private"),
            "selfDeclaredMadeForKids": False,
        },
        "contentDetails": {
            "enableAutoStart": True,
            "enableAutoStop": True,
            "latencyPreference": "normal",
        },
    }
    if item.get("description"):
        body["snippet"]["description"] = item["description"]

    bc = with_retry(lambda: yt.liveBroadcasts().insert(
        part="snippet,status,contentDetails", body=body).execute())
    if stream_id:
        with_retry(lambda: yt.liveBroadcasts().bind(
            part="id,contentDetails", id=bc["id"], streamId=stream_id).execute())
    # Thumbnail sama untuk semua (kalau ada). Thumbnail hanya bisa setelah video
    # punya ID; kalau gagal (mis. channel belum terverifikasi), jangan batalkan.
    if thumbnail:
        try:
            set_thumbnail(yt, bc["id"], thumbnail)
        except HttpError as e:
            print(f"    (thumbnail dilewati untuk {bc['id']}: {e})")
    # Tambahkan ke playlist (kalau item punya "playlist"). Cari/buat sekali.
    if item.get("playlist"):
        pid = ensure_playlist(yt, item["playlist"],
                              "private" if item.get("privacy") == "private" else "public")
        add_to_playlist(yt, pid, bc["id"])
    # Kirim komentar SEKARANG (bisa sebelum live selama broadcast sudah dibuat).
    if item.get("comment"):
        chat_id = bc["snippet"].get("liveChatId")
        if not chat_id:                    # kadang belum ada di response insert
            got = with_retry(lambda: yt.liveBroadcasts().list(
                part="snippet", id=bc["id"]).execute())
            items = got.get("items", [])
            chat_id = items[0]["snippet"].get("liveChatId") if items else None
        if chat_id:
            post_chat(yt, chat_id, item["comment"])
        else:
            print(f"    (komentar dilewati: liveChatId belum tersedia untuk {bc['id']})")
    return bc["id"]


def is_rate_limit(e):
    """True kalau error = YouTube membatasi kecepatan request.
    Pesan aslinya 'User requests exceed the rate limit.' (spasi), bukan camelCase."""
    m = str(e).lower()
    return getattr(getattr(e, "resp", None), "status", None) == 403 and \
        ("rate limit" in m or "ratelimit" in m)


def with_retry(fn, attempts=3, base=8):
    """Ulangi saat kena rate limit YouTube (403 userRequestsExceedRateLimit).
    attempts=3, base=8s biar cepat jatuh ke FAIL (stuck <30s) dan log tetap muncul."""
    for i in range(attempts):
        try:
            return fn()
        except HttpError as e:
            if not is_rate_limit(e) or i == attempts - 1:
                raise
            wait = base * 2 ** i
            print(f"    ⏱️ rate limit, tunggu {wait}s ({i+1}/{attempts})")
            time.sleep(wait)


def _norm(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def post_chat(yt, live_chat_id, text):
    """Kirim pesan ke live chat. Bisa dipakai sebelum live (broadcast 'ready')."""
    body = {"snippet": {
        "liveChatId": live_chat_id,
        "type": "textMessageEvent",
        "textMessageDetails": {"messageText": text},
    }}
    return with_retry(lambda: yt.liveChatMessages().insert(
        part="snippet", body=body).execute())


def chat_broadcasts(yt):
    """Broadcast yang punya live chat (ready + active): [(id, title, liveChatId)]."""
    out = []
    for status in ("active", "upcoming"):
        res = with_retry(lambda s=status: yt.liveBroadcasts().list(
            part="snippet", broadcastStatus=s, maxResults=50).execute())
        for b in res.get("items", []):
            s = b["snippet"]
            if s.get("liveChatId"):
                out.append((b["id"], s["title"], s["liveChatId"]))
    return out


def active_broadcasts(yt):
    """Broadcast yang sedang live: [(id, title, liveChatId), ...]."""
    res = with_retry(lambda: yt.liveBroadcasts().list(
        part="snippet", broadcastStatus="active", maxResults=50).execute())
    return [(b["id"], b["snippet"]["title"], b["snippet"].get("liveChatId"))
            for b in res.get("items", [])]


def existing_keys(yt):
    """Set (title, waktu UTC) broadcast yang sudah terjadwal, untuk hindari duplikat."""
    keys = set()
    req = yt.liveBroadcasts().list(
        part="snippet", broadcastStatus="upcoming", maxResults=50)
    while req:
        res = req.execute()
        for b in res.get("items", []):
            s = b["snippet"]
            if s.get("scheduledStartTime"):
                keys.add((s["title"], _norm(s["scheduledStartTime"])))
        req = yt.liveBroadcasts().list_next(req, res)
    return keys


def list_broadcasts(yt, statuses=("upcoming", "active", "completed")):
    """Daftar broadcast channel: [(id, judul, mulai, status)]."""
    out = []
    for status in statuses:
        req = yt.liveBroadcasts().list(
            part="snippet,status", broadcastStatus=status, maxResults=50)
        while req:
            res = req.execute()
            for b in res.get("items", []):
                s = b["snippet"]
                out.append((b["id"], s.get("title", ""),
                            s.get("scheduledStartTime", ""), status))
            req = yt.liveBroadcasts().list_next(req, res)
    return out


def delete_broadcast(yt, broadcast_id):
    """Hapus broadcast. Hanya yang belum live/selesai bisa dihapus via API."""
    return with_retry(lambda: yt.liveBroadcasts().delete(
        id=broadcast_id).execute())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("schedules", nargs="?", default="schedules.json")
    ap.add_argument("--account", help="nama profil akun (default: 'default')")
    ap.add_argument("--list-accounts", action="store_true", help="tampilkan profil akun")
    ap.add_argument("--login", action="store_true", help="login/OAuth akun ini lalu keluar")
    ap.add_argument("--stream-id", help="ID liveStream yang dipakai (default: ambil yang ada)")
    ap.add_argument("--thumbnail", help="file gambar thumbnail untuk SEMUA jadwal (opsional)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.list_accounts:
        for n in accounts():
            print(f"{n:20} {'✓ token' if has_token(n) else '· belum login'}")
        return

    if args.login:
        yt = youtube(args.account)          # memicu OAuth bila belum ada token
        print(f"Login OK sebagai: {whoami(yt)}  (profil: {args.account or 'default'})")
        return

    with open(os.path.join(HERE, args.schedules)) as f:
        items = json.load(f)

    if args.dry_run:
        for it in items:
            print("DRY", to_rfc3339(it["start"]), it["title"])
        return

    yt = youtube(args.account)
    print(f"Akun: {whoami(yt)}  (profil: {args.account or 'default'})")
    stream_id = args.stream_id
    if not stream_id:
        stream_id = ensure_stream(yt)
        print("Pakai liveStream:", stream_id)

    thumb = None
    if args.thumbnail:
        with open(args.thumbnail, "rb") as f:
            thumb = f.read()

    have = existing_keys(yt)
    ok = fail = skip = 0
    for it in items:
        if (it["title"], _norm(to_rfc3339(it["start"]))) in have:
            skip += 1
            print(f"SKIP  {it['start']}  {it['title']}  (sudah ada)")
            continue
        try:
            bid = create(yt, it, stream_id, thumb)
        except Exception as e:
            fail += 1
            print(f"FAIL  {it['start']}  {it['title']}  -> {e}")
            continue
        ok += 1
        have.add((it["title"], _norm(to_rfc3339(it["start"]))))
        print(f"OK  {it['start']}  {it['title']}  -> https://www.youtube.com/watch?v={bid}")
        time.sleep(1)
    print(f"\n{ok} berhasil, {skip} dilewati (sudah ada), {fail} gagal")


def _selftest():
    assert to_rfc3339("2099-06-01 20:00+07:00") == "2099-06-01T13:00:00Z"
    assert to_rfc3339("2099-06-01T20:00:00Z") == "2099-06-01T20:00:00Z"
    try:
        to_rfc3339("2020-01-01 00:00+07:00")
        raise AssertionError("jadwal lewat harus ditolak")
    except ValueError:
        pass
    # profil akun terpisah
    assert account_dir("alice") == os.path.join(ACCOUNTS, "alice")
    assert account_dir(None) == HERE and account_dir("default") == HERE
    assert token_path("alice").endswith(os.path.join("accounts", "alice", "token.pickle"))
    # set_banner: insert TANPA kwarg part, update pakai id channel (mock service)
    bcalls = []
    class _Banners:
        def insert(self, **kw):
            bcalls.append(("insert", kw))
            class _E:
                def execute(self): return {"url": "https://yt/be.jpg"}
            return _E()
    class _Channels:
        def list(self, **kw):
            class _E:
                def execute(self): return {"items": [{"id": "CH1", "brandingSettings": {"image": {}}}]}
            return _E()
        def update(self, **kw):
            bcalls.append(("update", kw))
            class _E:
                def execute(self): return kw["body"]
            return _E()
    class _BannerYt:
        def channelBanners(self): return _Banners()
        def channels(self): return _Channels()
    set_banner(_BannerYt(), b"\x89PNG fake")
    assert bcalls[0][0] == "insert" and "part" not in bcalls[0][1]
    assert bcalls[1][1]["body"]["id"] == "CH1"
    assert bcalls[1][1]["body"]["brandingSettings"]["image"]["bannerExternalUrl"] == "https://yt/be.jpg"
    # ensure_stream membuat liveStream saat daftar kosong (mock service)
    class _Streams:
        def list(self, **kw):
            class _E:
                def execute(self): return {"items": []}
            return _E()
        def insert(self, **kw):
            class _E:
                def execute(self): return {"id": "STREAM123"}
            return _E()
    class _StreamYt:
        def liveStreams(self): return _Streams()
    assert ensure_stream(_StreamYt()) == "STREAM123"
    # set_thumbnail: kirim bytes gambar via thumbnails().set (mock service)
    calls = {}
    class _Th:
        def set(self, **kw):
            calls.update(kw)
            class _E:
                def execute(self): return {"kind": "youtube#thumbnailSetResponse"}
            return _E()
    class _Yt:
        def thumbnails(self): return _Th()
    set_thumbnail(_Yt(), "VID123", b"\xff\xd8\xff\xe0fakejpg")
    assert calls["videoId"] == "VID123"
    assert calls["media_body"] is not None
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
