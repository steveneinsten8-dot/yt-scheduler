# YouTube Live Scheduler (login + role, Streamlit Cloud)

Bikin broadcast live YouTube terjadwal. Ada sistem **login app** (username +
password) dengan role **admin** dan **user**. Tiap pengguna punya jadwal,
channel, dan `client_secret.json` sendiri → **tidak saling menimpa**.

## Login & role
- **Daftar/masuk** dengan username + password (hash pbkdf2, stdlib).
- **admin**: tab 🛠️ Admin → lihat semua pengguna, ubah role, hapus akun, buat akun.
- **user**: hanya kelola jadwal/channel sendiri.
- Admin pertama dibuat otomatis dari `[auth] admin_user` / `admin_password` di
  secrets (lihat contoh). **Ganti password-nya setelah login pertama.**

## Kenapa OAuth Google manual, bukan `st.login()`?
Streamlit 1.64 hanya menyimpan `id_token`+`access_token`
(`starlette_auth_routes.py:617`) dan **membuang `refresh_token`**; tak ada
`st.user.refresh()`. Access token YouTube mati ~1 jam, penjadwal butuh refresh
token. Jadi OAuth dijalankan sendiri (`auth.py`).

## Hubungkan channel YouTube
Identitas pengguna = username app. Google dipakai hanya untuk **menghubungkan
channel**. Di sidebar → "➕ Hubungkan channel YouTube":
- Upload `client_secret.json` **tipe Web application** (redirect URI = URL app,
  tanpa path), atau
- langsung pakai OAuth milik admin kalau `[google]` di secrets terisi.

File tipe "Desktop app" **ditolak** — flow ini butuh redirect web. Buat OAuth
client **Web application** di Google Cloud Console, tambahkan **Authorized
redirect URI** = URL app persis (mis. `https://<app>.streamlit.app`,
`http://localhost:8501`).

## 1. Supabase (untuk multi-pengguna online)
SQL Editor → jalankan:
```sql
create table if not exists yt_users (
  user_id    text primary key,
  data       jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
alter table yt_users enable row level security;
-- Server pakai service_role key, jadi tidak perlu policy untuk anon.
```
Tanpa Supabase, app pakai file lokal `.data/<username>.json` (buat ngoding lokal
— **tidak** cocok untuk Streamlit Cloud karena filesystem-nya sementara).

## 2. Secrets
Salin `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` (lokal), atau
tempel di Streamlit Cloud → **Settings → Secrets**. Isi:
- `[auth] cookie_secret` (wajib — kunci tanda tangan cookie login) + `admin_user`/`admin_password`
- `[supabase] url`/`key` (wajib untuk cloud)
- `[google]` (opsional; kalau kosong, tiap user upload client_secret.json sendiri)

## 3. Deploy
1. Push repo ke GitHub (`.gitignore` sudah menutup `secrets.toml`, `token.pickle`,
   `client_secret.json`, `accounts/`, `.data/`, `posted.json`).
2. share.streamlit.io → New app → pilih repo → main file `app.py`.
3. Advanced settings → **Secrets** = isi di atas; Python **3.12** (default).
4. Deploy → buka URL app → **Daftar** akun, atau masuk sebagai admin.

## Lokal
```bash
./ui.sh          # UI
./run.sh         # CLI buat broadcast dari schedules.json (mode lama)
```

## Auto-komentar tanpa cron di cloud
Streamlit Cloud tidak punya cron. Jadwalkan `autochat.py` lewat GitHub Actions
atau cron server tiap 1–2 menit (butuh secrets yang sama). Komentar juga sudah
dikirim otomatis saat broadcast dibuat, jadi ini hanya jaring untuk siaran yang
dibuat manual di Studio.

Pin komentar **tidak bisa** via API YouTube — pin manual di YouTube Studio.
