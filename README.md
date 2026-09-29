# YouTube Live Scheduler (multi-pengguna, Streamlit Cloud)

Bikin broadcast live YouTube terjadwal dari daftar judul. Tiap pengguna login
dengan akun Google-nya sendiri → **jadwal & token terpisah, tidak saling menimpa**.

## Kenapa OAuth manual, bukan `st.login()` bawaan?
Streamlit 1.64 hanya menyimpan `id_token` + `access_token`
(`starlette_auth_routes.py:617`) dan **membuang `refresh_token`** — juga tidak ada
`st.user.refresh()`. Access token YouTube mati ~1 jam, jadi penjadwal butuh
refresh token. `auth.py` menjalankan web OAuth flow sendiri yang menyimpannya.

## Isolasi antar pengguna
Kunci = **channel_id YouTube**. Cookie berisi daftar channel_id yang login di
browser itu; data tiap channel disimpan di `store.py` → Supabase (atau file
`.data/<channel_id>.json` saat lokal). Orang lain login → channel beda → baris beda.

## 1. Supabase (kalau mau multi-pengguna online)
SQL Editor → jalankan:
```sql
create table if not exists yt_users (
  channel_id text primary key,
  data       jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
alter table yt_users enable row level security;
-- Server pakai service_role key, jadi tidak perlu policy untuk anon.
```
Isi `[supabase] url` dan `key` (pakai **service_role** atau anon + policy yang cocok).

## 2. Secrets
Salin `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` (lokal), atau
tempel isinya di Streamlit Cloud → **Settings → Secrets**.

Di Google Cloud Console, buat OAuth client **Web application** dan tambahkan
**Authorized redirect URI** = URL app persis (tanpa path), mis.
`https://<app>.streamlit.app` dan `http://localhost:8501`.

> Catatan: `redirect_uri` di sini = URL app itu sendiri (Google memantulkan
> `?code=` ke root app). Karena itu jangan tambahkan path apa pun di daftar
> Authorized redirect URI.

## 3. Deploy
1. Push repo ke GitHub (`.gitignore` sudah menutup `secrets.toml`, `token.pickle`,
   `client_secret.json`, `accounts/`, `.data/`, `posted.json`).
2. share.streamlit.io → New app → pilih repo → main file `app.py`.
3. Advanced settings → **Secrets** = isi secrets di atas; Python **3.12** (default).
4. Deploy. Buka URL app → "Masuk dengan Google".

## Lokal
```bash
./ui.sh          # UI  (butuh .streamlit/secrets.toml)
./run.sh         # CLI buat broadcast dari schedules.json (mode lama)
```

## Auto-komentar tanpa cron di cloud
Streamlit Cloud tidak punya cron. Jadwalkan `autochat.py` lewat GitHub Actions
atau cron server tiap 1–2 menit (butuh secrets yang sama sebagai env/Supabase).
Komentar juga sudah dikirim otomatis saat broadcast dibuat, jadi ini hanya jaring
untuk siaran yang dibuat manual di Studio.

Pin komentar **tidak bisa** via API YouTube — pin manual di YouTube Studio.
