#!/usr/bin/env python3
"""UI Streamlit jadwal live YouTube. Login app (admin/user), tiap pengguna
punya client_secret.json + channel sendiri. Jalankan: ./ui.sh"""
from datetime import datetime
import re
import time

import streamlit as st

import auth
import store
import schedule_streams as S
from gen_schedules import build_items, parse_txt

st.set_page_config(page_title="YouTube Live Scheduler", page_icon="📅", layout="wide")
try:
    auth.ensure_admin()             # bikin akun admin dari secrets (kalau diisi)
    auth.handle_callback()          # tukar ?code=... kalau baru balik dari Google
    user = auth.current_user()
except RuntimeError as e:           # masalah Supabase (tabel/key/skema) -> tampilkan jelas
    st.error(f"Masalah penyimpanan (Supabase): {e}")
    st.stop()

# ---------- Gerbang login / daftar ----------
if not user:
    st.title("📅 YouTube Live Scheduler")
    tab_in, tab_up = st.tabs(["Masuk", "Daftar"])
    with tab_in:
        u = st.text_input("Username", key="in_u")
        p = st.text_input("Password", type="password", key="in_p")
        if st.button("Masuk", type="primary"):
            if auth.authenticate(u, p):
                auth.login(u)
                st.rerun()
            else:
                st.error("Username atau password salah.")
    with tab_up:
        st.caption("Buat akun baru. Setelah masuk, upload `client_secret.json` "
                   "milikmu untuk menghubungkan channel YouTube.")
        nu = st.text_input("Username baru", key="up_u")
        np1 = st.text_input("Password", type="password", key="up_p1")
        np2 = st.text_input("Ulangi password", type="password", key="up_p2")
        if st.button("Daftar", type="primary"):
            if np1 != np2:
                st.error("Password tidak sama.")
            else:
                try:
                    auth.register(nu, np1)
                    auth.login(nu)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
    with st.expander("ℹ️ Setup OAuth (buat admin)"):
        st.caption("Daftarkan **Authorized redirect URI** ini PERSIS (tanpa `/` "
                   "di akhir) di Google Cloud Console → Credentials → OAuth client "
                   "Web application. Kalau salah sedikit → `redirect_uri_mismatch`.")
        st.code(auth.redirect_uri(), language="text")
    st.stop()

role = auth.role(user)
data = store.load(user)
channels = data.get("channels") or {}

# ---------- Sidebar ----------
st.sidebar.header(f"👤 {user}")
st.sidebar.caption(f"role: **{role}**")
_bk = store.backend()
if _bk == "supabase":
    _ok, _msg = store.ping()
    st.sidebar.caption(f"storage: **supabase** {'🟢' if _ok else '🔴'} {_msg}")
else:
    st.sidebar.caption("storage: **json** (file lokal)")
    st.sidebar.caption("⚠️ Data hilang saat app restart. Isi `[supabase]` "
                       "di Secrets untuk simpan permanen (lihat README).")

if channels:
    labels = {c: v.get("title", c) for c, v in channels.items()}
    default = data.get("channel_id") if data.get("channel_id") in channels else list(channels)[0]
    pick = st.sidebar.selectbox("Channel aktif", list(channels),
                                index=list(channels).index(default),
                                format_func=lambda c: labels[c])
    if pick != data.get("channel_id"):
        data["channel_id"] = pick
        store.save(user, data)
        st.rerun()
    if st.sidebar.button("🚪 Logout channel aktif"):
        auth.logout_channel(user, pick)
        st.rerun()
else:
    pick = None
    st.sidebar.warning("Belum ada channel. Hubungkan di bawah.")

with st.sidebar.expander("➕ Hubungkan channel YouTube", expanded=not channels):
    st.caption("Upload `client_secret.json` tipe **Web application** "
               "(redirect URI: " + auth.redirect_uri() + ").")
    if auth.secrets_config():
        st.caption("Admin sudah menyediakan OAuth — kamu bisa langsung hubungkan.")
    up = st.file_uploader("client_secret.json (milikmu)", type=["json"])
    cfg = None
    if up:
        try:
            cfg = auth.parse_client_secret(up.getvalue())
        except ValueError as e:
            st.error(str(e))
    if cfg is None:
        cfg = auth.oauth_config(user)          # milik sendiri (tersimpan) atau admin
    if cfg:
        st.link_button("🔗 Lanjut ke Google", auth.login_url(cfg, user), type="primary")
    else:
        st.info("Upload client_secret.json dulu, atau minta admin mengisi `[google]`.")

if st.sidebar.button("🚪 Keluar"):
    auth.logout()
    st.rerun()

def _admin_panel(me):
    st.subheader("🛠️ Kelola pengguna")
    users = auth.all_users()
    rows = [{"username": u, "role": d.get("role", "user"),
             "channel": ", ".join(v.get("title", c) for c, v in (d.get("channels") or {}).items()),
             "jadwal": len(d.get("schedules") or [])}
            for u, d in sorted(users.items())]
    st.dataframe(rows, width="stretch")

    st.divider()
    st.write("**Ubah role / hapus**")
    c = st.columns([2, 2, 1])
    target = c[0].selectbox("Pengguna", sorted(users))
    new_role = c[1].selectbox("Role baru", ["user", "admin"])
    if c[2].button("Simpan"):
        try:
            auth.set_role(target, new_role)
            st.success(f"{target} → {new_role}")
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    if st.button(f"🗑️ Hapus akun `{target}`"):
        if target == me:
            st.error("Tidak bisa menghapus akun sendiri.")
        else:
            try:
                auth.delete_user(target)
                st.success(f"{target} dihapus.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    st.divider()
    st.write("**Reset password**")
    c = st.columns([2, 2, 1])
    rp_user = c[0].selectbox("Pengguna", sorted(users), key="rp_user")
    rp_pw = c[1].text_input("Password baru", type="password", key="rp_pw")
    if c[2].button("Reset", key="rp_btn"):
        try:
            auth.set_password(rp_user, rp_pw)
            st.success(f"Password {rp_user} direset.")
        except ValueError as e:
            st.error(str(e))

    st.divider()
    st.write("**Buat akun baru**")
    c = st.columns(3)
    nu = c[0].text_input("Username baru (admin)")
    npw = c[1].text_input("Password (admin)", type="password")
    nr = c[2].selectbox("Role baru (admin)", ["user", "admin"])
    if st.button("Buat"):
        try:
            auth.register(nu, npw, nr)
            st.success(f"Akun {nu} ({nr}) dibuat.")
        except ValueError as e:
            st.error(str(e))


if not channels:
    st.info("👈 Hubungkan channel YouTube dulu di sidebar untuk mulai membuat jadwal.")
    if role == "admin":
        _admin_panel(user)
    st.stop()

yt, data = auth.youtube_for(user, pick)

# ---------- Jadwal (per pengguna) ----------
if st.session_state.get("_owner") != user:
    st.session_state.schedules = data.get("schedules", [])
    st.session_state._owner = user


def save():
    data["schedules"] = st.session_state.schedules
    store.save(user, data)


tabs = ["1️⃣ Generate", "2️⃣ Edit & Cek", "3️⃣ Buat di YouTube", "4️⃣ Live Chat", "5️⃣ Banner Channel"]
if role == "admin":
    tabs.append("🛠️ Admin")
tab_list = st.tabs(tabs)
tab_gen, tab_edit, tab_run, tab_chat, tab_banner = tab_list[:5]

with tab_gen:
    mode = st.radio("Sumber jadwal", ["Berkala (otomatis)", "Import TXT"], horizontal=True)
    if mode == "Berkala (otomatis)":
        c = st.columns(4)
        count = c[0].number_input("Jumlah", 1, 200, 30)
        start = c[1].date_input("Mulai", value=datetime.now().date())
        time_ = c[2].time_input("Jam", value=datetime.strptime("20:00", "%H:%M").time())
        step = c[3].number_input("Jarak (hari)", 1, 30, 1)
        title = st.text_input("Pola judul ({i} = nomor)", "Live Harian #{i}")
        comment = st.text_input("Komentar live chat otomatis ({i} = nomor, kosongkan = tidak ada)",
                                "Halo! Selamat datang di Live Harian #{i} 🙌")
        playlist = st.text_input("Playlist ({i} = nomor, {title} = judul; kosongkan = tanpa playlist)",
                                 "")
        privacy = st.selectbox("Privasi", ["public", "unlisted", "private"])
        if st.button("Generate", type="primary"):
            st.session_state.schedules = build_items(
                int(count), start.isoformat(), time_.strftime("%H:%M"),
                int(step), title, privacy, comment, playlist)
            save()
            st.success(f"{len(st.session_state.schedules)} jadwal dibuat.")
    else:
        st.caption("Tiap jadwal = 1 blok, dipisah **baris kosong**. "
                   "Baris 1+2 → judul (digabung). Baris 📺 → komentar live chat. "
                   "Baris lain → deskripsi. Baris `#hashtag` menempel ke jadwal sebelumnya. "
                   "Waktu otomatis: sekarang + lead, tiap entri +gap.")
        up = st.file_uploader("File TXT", type=["txt"])
        c = st.columns(2)
        lead = c[0].number_input("Lead (menit dari sekarang)", 1, 1440, 10)
        gap = c[1].number_input("Jarak antar entri (menit)", 1, 1440, 30)
        privacy2 = st.selectbox("Privasi (TXT)", ["public", "unlisted", "private"], key="p2")
        if up and st.button("Import", type="primary"):
            st.session_state.schedules = parse_txt(
                up.read().decode("utf-8"), int(lead), int(gap), privacy2)
            save()
            st.success(f"{len(st.session_state.schedules)} jadwal diimpor dari TXT.")
        with st.expander("Contoh format TXT"):
            st.code("""Alaska High School Football
SWDP Private School vs Kodiak
Archangels @ Bears
📺watch live: @url:`https://example.com/live`
🗒️The SWDP varsity football team has an away game @ Kodiak.

#Alaska #AKHSFootball""", language="text")

def _template_description(item):
    title = item.get("title", "")
    parts = [x.strip() for x in title.split(" — ", 1)]
    teams = parts[0]
    desc = item.get("description", "")
    date = next((x.strip() for x in desc.splitlines()
                 if re.search(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+\w+\s+\d+", x, re.I)), "")
    tags = " ".join(x for x in desc.split() if x.startswith("#"))
    return f"""🏆 High School Football Live Streaming – Watch the Game Live! 🏈🎥
Welcome to our High School Football Live Streaming! Get ready for an action-packed game as talented young athletes take the field to showcase their skills, teamwork, and passion for football. Watch the excitement of High School Football live from the field! Catch every touchdown, tackle, and unforgettable play as local teams battle it out under the Friday night lights. Don’t miss the energy, passion, and community spirit that make high school football special. Stream the game live and cheer for your favorite team from anywhere!

📅 Match Details:
🆚 Teams : {teams}
📅 Date : {date}

🔥 Why You Should Watch:
✅ Live Play-by-Play Coverage – Follow every play in real time.
✅ Expert Commentary & Analysis – Stay informed with insights from our commentators.
✅ High-Quality Streaming – Enjoy a clear and smooth viewing experience.
✅ Exciting Highlights & Key Moments – Catch the best plays and game-changing moments.
✅ Interactive Fan Experience – Join the conversation in live chat and cheer for your team!

📢 Support Your Team!
Show your school spirit by leaving a comment, hitting the like button, and sharing this live stream with friends and family.

🔔 Subscribe & Stay Updated!
Don’t miss future games. Subscribe and turn on notifications for upcoming live streams and highlights.

Thank you for watching, and enjoy the game!🏈🔥

{tags}""".strip()


with tab_edit:
    if st.session_state.schedules:
        template = st.selectbox("Template deskripsi", ["Tidak ada", "High School Football Live Streaming"])
        if st.button("Tambah template ke semua jadwal") and template != "Tidak ada":
            for _it in st.session_state.schedules:
                _it["description"] = _template_description(_it)
            save()
            st.success("Template deskripsi ditambahkan ke semua jadwal.")
        for _it in st.session_state.schedules:      # kolom Playlist selalu tampil
            _it.setdefault("playlist", "")
        edited = st.data_editor(
            st.session_state.schedules, num_rows="dynamic", width="stretch",
            column_config={
                "title": st.column_config.TextColumn("Judul", required=True),
                "start": st.column_config.TextColumn("Mulai (YYYY-MM-DD HH:MM)"),
                "privacy": st.column_config.SelectboxColumn(
                    "Privasi", options=["public", "unlisted", "private"]),
                "description": st.column_config.TextColumn("Deskripsi"),
                "comment": st.column_config.TextColumn("Komentar live chat"),
                "playlist": st.column_config.TextColumn("Playlist"),
            })
        if st.button("Simpan perubahan"):
            st.session_state.schedules = edited
            save()
            st.success("Disimpan.")
    else:
        st.info("Belum ada jadwal. Generate dulu di tab 1.")

with tab_run:
    st.caption(f"Total {len(st.session_state.schedules)} jadwal · channel `{pick}`")
    thumb_file = st.file_uploader(
        "Thumbnail (opsional, satu gambar untuk SEMUA jadwal di batch ini) "
        "— JPEG/PNG, maks 2 MB", type=["jpg", "jpeg", "png"], key="thumb")
    thumb_bytes = thumb_file.getvalue() if thumb_file else None
    if thumb_bytes:
        st.image(thumb_bytes, width=320, caption="Thumbnail akan dipakai untuk semua jadwal")
    if st.button("🔍 Dry-run (validasi tanpa kirim)"):
        bad = 0
        for it in st.session_state.schedules:
            try:
                st.write("✅", S.to_rfc3339(it["start"]), it["title"])
            except Exception as e:
                bad += 1
                st.write("❌", it.get("start"), it.get("title"), "—", e)
        if bad:
            st.warning(f"{bad} jadwal bermasalah")
    if st.button("🚀 Buat di YouTube", type="primary"):
        items = st.session_state.schedules
        if not items:
            st.error("Tidak ada jadwal.")
            st.stop()
        log = st.empty()
        lines = []
        def say(s):
            lines.append(s)
            log.code("\n".join(lines[-20:]))
        stream_id = S.ensure_stream(yt)
        say(f"liveStream: {stream_id}")
        have = S.existing_keys(yt)
        ok = fail = skip = 0
        prog = st.progress(0.0)
        for n, it in enumerate(items, 1):
            try:
                key = (it["title"], S._norm(S.to_rfc3339(it["start"])))
            except Exception as e:
                fail += 1
                say(f"FAIL {it.get('start')} {it.get('title')} — {e}")
                prog.progress(n / len(items))
                continue
            if key in have:
                skip += 1
                say(f"SKIP {it['start']} {it['title']} (sudah ada)")
                prog.progress(n / len(items))
                continue
            try:
                bid = S.create(yt, it, stream_id, thumb_bytes)
            except Exception as e:
                fail += 1
                say(f"FAIL {it['start']} {it['title']} — {e}")
                if S.is_rate_limit(e):
                    say("⏹️ Rate limit YouTube — hentikan dulu, jalankan ulang beberapa "
                        "menit lagi (broadcast yang sudah OK akan otomatis di-SKIP).")
                    prog.progress(n / len(items))
                    break
            else:
                ok += 1
                say(f"OK   {it['start']} {it['title']} → youtube.com/watch?v={bid}")
            time.sleep(1)
            prog.progress(n / len(items))
        st.success(f"{ok} berhasil · {skip} dilewati · {fail} gagal")

with tab_chat:
    st.info("Komentar dikirim otomatis **saat broadcast dibuat** (bisa sebelum live). "
            "Di sini untuk kirim ulang/manual ke broadcast yang sudah ada. "
            "⚠️ Pin komentar TIDAK bisa via API YouTube — pin manual di YouTube Studio.")
    try:
        live = S.chat_broadcasts(yt)
    except Exception as e:
        st.error(f"Gagal ambil broadcast: {e}")
        live = []
    if not live:
        st.warning("Tidak ada broadcast dengan live chat.")
    else:
        st.caption(f"Komentar akan dikirim ke semua broadcast upcoming/active di channel `{pick}`.")
        manual = st.text_area("Komentar untuk semua broadcast", placeholder="Tulis komentar live chat...")
        if st.button("💬 Kirim ke semua broadcast", type="primary"):
            if not manual.strip():
                st.error("Komentar masih kosong.")
            else:
                sent = miss = 0
                for bid, title, chat_id in live:
                    if chat_id:
                        S.post_chat(yt, chat_id, manual.strip())
                        sent += 1
                    else:
                        miss += 1
                st.success(f"{sent} broadcast menerima komentar · {miss} belum punya live chat")
        st.divider()
        st.caption("Komentar dari jadwal (otomatis ke broadcast yang judulnya cocok)")
        if st.button("Kirim komentar jadwal ke semua"):
            comments = {it["title"]: it["comment"] for it in st.session_state.schedules if it.get("comment")}
            sent = miss = 0
            for bid, title, chat_id in live:
                if title in comments and chat_id:
                    S.post_chat(yt, chat_id, comments[title])
                    sent += 1
                else:
                    miss += 1
            st.success(f"{sent} terkirim · {miss} tanpa komentar")

with tab_banner:
    st.caption(f"Pasang banner untuk channel `{pick}`. Rekomendasi 2560×1152 px (min 2048×1152, 16:9), maks 6 MB.")
    st.warning("⚠️ Foto profil channel TIDAK bisa lewat API — hanya banner yang bisa.")
    banner_file = st.file_uploader("Gambar banner (JPG/PNG)", type=["jpg", "jpeg", "png"], key="banner")
    if st.button("🖼️ Pasang banner ke channel", type="primary"):
        if not banner_file:
            st.error("Pilih gambar banner dulu.")
        else:
            try:
                S.set_banner(yt, banner_file.getvalue())
                st.success(f"Banner terpasang di channel `{pick}`. Cek di YouTube Studio (bisa perlu refresh).")
            except Exception as e:
                st.error(f"Gagal pasang banner: {e}")

if role == "admin":
    with tab_list[-1]:
        _admin_panel(user)
