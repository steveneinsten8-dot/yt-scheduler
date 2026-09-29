#!/usr/bin/env python3
"""Generate schedules.json: berkala, atau import dari TXT."""
import argparse, json, re
from datetime import datetime, timedelta

# Format TXT: tiap jadwal = satu blok, dipisah baris kosong.
#   2 baris pertama  -> judul (digabung "A — B")
#   baris 📺...      -> komentar live chat
#   baris lain       -> deskripsi
# Baris kosong diikuti baris '#' (hashtag) dianggap lanjutan deskripsi, bukan jadwal baru.


def build_items(count=30, start=None, time="20:00", step=1,
                title="Live Harian #{i}", privacy="public", comment="",
                playlist=""):
    day0 = (datetime.fromisoformat(start) if start
            else datetime.now() + timedelta(days=1))
    day0 = day0.replace(hour=0, minute=0, second=0, microsecond=0)
    items = []
    for i in range(count):
        it = {
            "title": title.format(i=i + 1),
            "start": (day0 + timedelta(days=i * step)).strftime("%Y-%m-%d ") + time,
            "privacy": privacy,
        }
        if comment:
            it["comment"] = comment.format(i=i + 1)
        if playlist:
            it["playlist"] = playlist.format(i=i + 1, title=it["title"])
        items.append(it)
    return items


def parse_txt(text, lead_min=10, gap_min=30, privacy="public"):
    """Teks TXT -> list jadwal. Waktu otomatis: sekarang + lead, tiap entri +gap."""
    blocks = re.split(r"\n[ \t]*\n", text.strip())
    merged = []
    for b in blocks:
        # Blok yang mulai dengan #/📺/🗒️ bukan jadwal baru, melainkan lanjutan
        # jadwal sebelumnya. File scraper menyisipkan baris kosong sebelum 📺
        # saat tak ada baris maskot -> tanpa ini, 📺 jadi "judul" jadwal baru.
        if merged and b.lstrip().startswith(("#", "📺", "🗒️")):
            merged[-1] += "\n" + b
        else:
            merged.append(b)

    now = datetime.now().replace(second=0, microsecond=0)
    items = []
    for i, b in enumerate(merged):
        lines = [l.strip() for l in b.splitlines() if l.strip()]
        if not lines:
            continue
        title = " — ".join(lines[:2]) if len(lines) >= 2 else lines[0]
        comment, desc, playlist = "", [], ""
        for l in lines[2:]:
            if l.startswith("📺"):
                comment = l[len("📺"):].strip()
            elif l.startswith("📁"):
                playlist = l[len("📁"):].strip()
            else:
                desc.append(l)
        t = now + timedelta(minutes=lead_min + gap_min * i)
        it = {"title": title, "start": t.strftime("%Y-%m-%d %H:%M"), "privacy": privacy}
        if desc:
            it["description"] = "\n".join(desc)
        if comment:
            it["comment"] = comment
        if playlist:
            it["playlist"] = playlist
        items.append(it)
    return items


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-n", "--count", type=int, default=30)
    p.add_argument("--start", help="tanggal mulai YYYY-MM-DD (default: besok)")
    p.add_argument("--time", default="20:00", help="jam tiap sesi (waktu lokal)")
    p.add_argument("--step", type=int, default=1, help="jarak hari antar sesi")
    p.add_argument("--title", default="Live Harian #{i}")
    p.add_argument("--privacy", default="public", choices=["public", "private", "unlisted"])
    p.add_argument("--comment", default="", help="teks komentar live chat ({i} = nomor)")
    p.add_argument("--import-txt", help="import jadwal dari file TXT (bukan generate)")
    p.add_argument("--lead", type=int, default=10, help="menit dari sekarang utk entri pertama")
    p.add_argument("--gap", type=int, default=30, help="jarak menit antar entri")
    p.add_argument("-o", "--out", default="schedules.json")
    a = p.parse_args()

    if a.import_txt:
        with open(a.import_txt, encoding="utf-8") as f:
            items = parse_txt(f.read(), a.lead, a.gap, a.privacy)
    else:
        items = build_items(a.count, a.start, a.time, a.step, a.title, a.privacy, a.comment)
    with open(a.out, "w") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    print(f"{len(items)} jadwal -> {a.out}  ({items[0]['start']} .. {items[-1]['start']})")


def _selftest():
    items = build_items(3, "2026-01-01", "20:00", 2, "X #{i}")
    assert [i["start"] for i in items] == [
        "2026-01-01 20:00", "2026-01-03 20:00", "2026-01-05 20:00"]
    assert [i["title"] for i in items] == ["X #1", "X #2", "X #3"]

    # playlist: pola {i}/{title} diisi per item
    pl = build_items(2, "2026-01-01", "20:00", 1, "X #{i}", playlist="Liga {i}")
    assert pl[0]["playlist"] == "Liga 1" and pl[1]["playlist"] == "Liga 2"
    assert "playlist" not in build_items(1, "2026-01-01")[0]

    txt = """Alaska High School Football
SWDP Private School vs Kodiak
Archangels @ Bears
📺watch live: @url:`https://example.com/live?title=SWDP%20Private%20School%20Vs.%20Kodiak`
🗒️The SWDP Private School varsity football team has an away conference game @ Kodiak (AK) on Friday, October 2 @ 4p.

#Alaska #AKHSFootball #Archangels #Bears

Second Game
Team A vs Team B
📺Halo semua
"""
    got = parse_txt(txt, lead_min=10, gap_min=30)
    assert len(got) == 2, got                       # hashtag TIDAK jadi jadwal baru
    a = got[0]
    assert a["title"] == "Alaska High School Football — SWDP Private School vs Kodiak", a["title"]
    assert a["comment"].startswith("watch live:"), a["comment"]
    assert "Archangels @ Bears" in a["description"]
    assert "#Alaska" in a["description"]            # hashtag masuk deskripsi
    assert got[1]["title"] == "Second Game — Team A vs Team B"
    # tanpa baris maskot: scraper menyisipkan baris kosong sebelum 📺.
    # Blok 📺/🗒️ harus menempel ke jadwal sebelumnya, BUKAN jadi judul baru.
    txt2 = """A vs B
State High School Football

📺watch live: https://x/y
🗒️A @ B, starts at 7p

#Tag"""
    g2 = parse_txt(txt2)
    assert len(g2) == 1, g2
    assert g2[0]["title"] == "A vs B — State High School Football", g2[0]["title"]
    assert g2[0]["comment"].startswith("watch live:"), g2[0]["comment"]
    # waktu: naik gap menit, format benar
    t0 = datetime.strptime(a["start"], "%Y-%m-%d %H:%M")
    t1 = datetime.strptime(got[1]["start"], "%Y-%m-%d %H:%M")
    assert (t1 - t0) == timedelta(minutes=30), (t0, t1)
    print("selftest ok")


if __name__ == "__main__":
    import sys
    _selftest() if "--selftest" in sys.argv else main()
