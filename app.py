#!/usr/bin/env python3
"""UI Streamlit jadwal live YouTube. Login app (admin/user), tiap pengguna
punya client_secret.json + channel sendiri. Jalankan: ./ui.sh"""
from datetime import datetime
import io
import re
import time

import streamlit as st
from PIL import Image

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


tabs = ["1️⃣ Generate", "2️⃣ Edit & Cek", "3️⃣ Buat di YouTube", "4️⃣ Live Chat", "5️⃣ Channel"]
if role == "admin":
    tabs.append("🛠️ Admin")
tab_list = st.tabs(tabs)
tab_gen, tab_edit, tab_run, tab_chat, tab_channels = tab_list[:5]

with tab_channels:
    st.subheader("📺 Channel terhubung")
    st.caption(f"Total: {len(channels)} channel")
    rows = [{
        "Channel": info.get("title", ch_id),
        "ID channel": ch_id,
        "Aktif": "✅" if ch_id == pick else "",
    } for ch_id, info in channels.items()]
    st.dataframe(rows, width="stretch", hide_index=True)
    st.info("Pilih channel aktif dari sidebar untuk membuat broadcast atau memasang banner.")

with tab_gen:
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
    
    st.divider()
    st.subheader("Banner channel")
    st.caption("Pasang banner untuk channel aktif (2560×1152 px min., maks 6 MB).")
    banner_file = st.file_uploader("Gambar banner (JPG/PNG)", type=["jpg", "jpeg", "png"], key="banner_tab1")
    banner_all = st.checkbox("Terapkan ke semua channel", key="banner_all")
    banner_bytes = None
    if banner_file:
        try:
            img = Image.open(banner_file).convert("RGB")
            tw, th = 2560, 1440                       # rekomendasi YouTube (16:9)
            r = max(tw / img.width, th / img.height)  # skala "cover" biar tak gepeng
            img = img.resize((round(img.width * r), round(img.height * r)),
                             Image.Resampling.LANCZOS)
            left, top = (img.width - tw) // 2, (img.height - th) // 2
            img = img.crop((left, top, left + tw, top + th))   # crop tengah
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=92, optimize=True)
            banner_bytes = out.getvalue()
            st.caption("Gambar otomatis diubah ke 2560×1440 px (16:9, crop tengah).")
        except Exception as e:
            st.error(f"Gambar banner tidak valid: {e}")
    if banner_bytes and st.button("🖼️ Pasang banner", key="btn_banner"):
        targets = list(channels) if banner_all else [pick]
        ok_banner = fail_banner = 0
        for ch_id in targets:
            label = channels[ch_id].get("title", ch_id)
            try:
                yt_banner, _ = auth.youtube_for(user, ch_id)
                S.set_banner(yt_banner, banner_bytes)
                ok_banner += 1
                st.write(f"OK — {label}")
            except Exception as e:
                fail_banner += 1
                st.error(f"FAIL — {label}: {e}")
        if fail_banner:
            st.warning(f"Banner: {ok_banner} berhasil · {fail_banner} gagal")
        else:
            st.success(f"Banner terpasang ke {ok_banner} channel.")
    with st.expander("Contoh format TXT"):
        st.code("""Alaska High School Football
SWDP Private School vs Kodiak
Archangels @ Bears
📺watch live: https://example.com/live
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
    auto_multi = st.checkbox(
        "🔀 Auto per channel (blok 30 jadwal → channel berikutnya)", value=False)
    chunk_size = 30
    if auto_multi:
        st.caption(f"{len(channels)} channel terhubung: " +
                   " → ".join(channels[c].get("title", c) for c in channels))
    if st.button("🚀 Buat di YouTube", type="primary"):
        items = st.session_state.schedules
        if not items:
            st.error("Tidak ada jadwal.")
            st.stop()
        log = st.empty()
        lines = []
        def say(s):
            lines.append(s)
            log.code("\n".join(lines))

        def run_channel(ch_id, yt_ch, batch, label):
            """Proses satu batch di satu channel. Return (ok, skip, fail)."""
            try:
                stream_id = S.ensure_stream(yt_ch)
                say(f"liveStream [{label}]: {stream_id}")
                have = S.existing_keys(yt_ch)
            except Exception as e:
                for it in batch:
                    say(f"FAIL [{label}] {it.get('start')} {it.get('title')} — {e}")
                st.warning(f"Channel `{label}` gagal setup: {e}")
                return 0, 0, len(batch)
            o = s = f = 0
            prog = st.progress(0.0, text=f"[{label}] 0/{len(batch)}")
            for n, it in enumerate(batch, 1):
                try:
                    key = (it["title"], S._norm(S.to_rfc3339(it["start"])))
                except Exception as e:
                    f += 1
                    say(f"FAIL [{label}] {it.get('start')} {it.get('title')} — {e}")
                    prog.progress(n / len(batch), text=f"[{label}] {n}/{len(batch)}")
                    continue
                if key in have:
                    s += 1
                    say(f"SKIP [{label}] {it['start']} {it['title']} (sudah ada)")
                    prog.progress(n / len(batch), text=f"[{label}] {n}/{len(batch)}")
                    continue
                try:
                    bid = S.create(yt_ch, it, stream_id, thumb_bytes)
                except Exception as e:
                    f += 1
                    say(f"FAIL [{label}] {it['start']} {it['title']} — {e}")
                else:
                    o += 1
                    have.add(key)
                    say(f"OK   [{label}] {it['start']} {it['title']} → youtube.com/watch?v={bid}")
                time.sleep(1)
                prog.progress(n / len(batch), text=f"[{label}] {n}/{len(batch)}")
            say(f"—— [{label}] {o} OK · {s} SKIP · {f} FAIL ——")
            return o, s, f

        ok = skip = fail = 0
        if auto_multi:
            ch_ids = list(channels)
            for i in range(0, len(items), chunk_size):
                chunk = items[i:i + chunk_size]
                ch_id = ch_ids[(i // chunk_size) % len(ch_ids)]
                label = channels[ch_id].get("title", ch_id)
                say(f"═══ Batch {i//chunk_size + 1}: {len(chunk)} jadwal → {label} ═══")
                o = s = f = 0
                try:
                    yt_ch, _ = auth.youtube_for(user, ch_id)
                    o, s, f = run_channel(ch_id, yt_ch, chunk, label)
                except Exception as e:
                    for it in chunk:
                        say(f"FAIL [{label}] {it.get('start')} {it.get('title')} — {e}")
                    f = len(chunk)
                ok += o; skip += s; fail += f
        else:
            o = s = f = 0
            try:
                o, s, f = run_channel(pick, yt, items, channels[pick].get("title", pick))
            except Exception as e:
                for it in items:
                    say(f"FAIL [{channels[pick].get('title', pick)}] {it.get('start')} {it.get('title')} — {e}")
                f = len(items)
            ok += o; skip += s; fail += f
        total = ok + skip + fail
        st.success(
            f"Selesai: {total}/{len(items)} jadwal diproses · "
            f"{ok} berhasil · {skip} di-skip (sudah ada) · {fail} gagal")
        if fail:
            st.error(f"Periksa log di atas: {fail} jadwal gagal dari {len(items)} jadwal import.")

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

if role == "admin":
    with tab_list[-1]:
        _admin_panel(user)
