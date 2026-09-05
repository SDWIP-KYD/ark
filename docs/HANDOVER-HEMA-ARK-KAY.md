# HANDOVER — Hema.ark-kay.my.id

> **Tujuan dokumen:** Satu file yang kalau dibaca AI agent baru, dia langsung paham cara kerja, lokasi file, alur data, cara maintenance, dan troubleshooting website **hema.ark-kay.my.id** tanpa perlu tanya owner lagi.
> **Owner:** dr Senopati / Divisi Hemato-Onkologi Anak RSWS Makassar
> **Host:** Heire (Arch Linux, user `lenovo`, `/home/lenovo`)
> **Last updated:** 2026-09-02 — hasil audit live system
> **Status saat audit:** service `hema-report` RUNNING (PID 969), tunnel hema-ark-kay RUNNING, pickle `/tmp/*.pkl` kosong (habis reboot/clear) — perlu regenerate

---

## 1) Apa itu Hema?

Dashboard rekap lab hemato-onko: list pasien rawat (master sensus) + riwayat lab rutin + hasil PA / Radiologi / BMP (Bone Marrow) / LCS / IHC / Imuno, dengan grouping per lokasi (PICU, Lantai 5/6/7, IRD, PJT, PCC) → kamar → bed, plus catatan per pasien yang autosave.

Publik via `https://hema.ark-kay.my.id` (Cloudflare Tunnel), lokal via `http://127.0.0.1:8788`.

---

## 2) Tidak Ada "Folder Hema" — Peta Lokasi File

Tidak ada folder bernama `hema`. Semua komponen tercecer, ini peta lengkapnya:

| Komponen | Path | Keterangan |
|---|---|---|
| **Web server** | `/home/lenovo/.hermes/scripts/hemato_notes_server.py` (65KB) | HTTPServer Python stdlib, port 8788. Backup: `hemato_notes_server_clean.py`, `hemato_notes_server_broken_backup.py` |
| **Inject UI** | `/home/lenovo/.hermes/scripts/update_master_inject.html` (14KB, 353 baris) | CSS+JS hamburger, sidebar, modal Update Master, notifikasi. Di-load server saat startup lalu di-inject ke HTML |
| **Systemd service** | `/home/lenovo/.config/systemd/user/hema-report.service` | `ExecStart=.../venv/bin/python3 .../hemato_notes_server.py`, `WorkingDirectory=/home/lenovo`, `Restart=always` |
| **Report generator** | `/home/lenovo/gen_16agustus_html.py` (40KB) | Generate HTML final dari pickle + master. Import `patients_29agustus.PATIENTS` |
| **Master sensus** | `/home/lenovo/patients_29agustus.py` | AUTO-GENERATED, 69 pasien saat audit. Struktur lihat §3 |
| **Fetcher lab rutin** | `/home/lenovo/update_lab.py` | Isi `/tmp/simrs_15agustus_full.pkl` (max 200 records/pasien) |
| **Fetcher special** | `/home/lenovo/update_special.py` | Isi `/tmp/simrs_special_15agustus.pkl` (PA/Rad/BMP/LCS/Immuno/IHC) |
| **SIMRS API lib** | `/home/lenovo/fetch_special_15agustus.py` | `BASE=http://localhost:8080/webservice`, kredensial via env `SIMRS_LOGIN`/`SIMRS_PASS` (lihat `.env.example`), fungsi `make_session()`, `fetch_pa/rad/bmp/lcs/immuno/ihc` |
| **SIMRS Web UI** | `/home/lenovo/simrs-web/` (FastAPI, port 8099) | `sirs.kay.web.id` → `127.0.0.1:8099`. Cache di `simrs-web/cache2/` |
| **Tunnel Hema** | `/home/lenovo/.cloudflared/hema-ark-kay.yml` | Tunnel ID `328259b5-66d5-4ce6-87e0-8da8ba04b46b` → `hema.ark-kay.my.id` → `127.0.0.1:8788` |
| **Tunnel Kay** | `/home/lenovo/.cloudflared/config-dashboard.yml` | Tunnel ID `21c5b055-0223-4a6f-88cf-5c9f57387a18` → `hermes/simrs/rad/app/api/andorina/sirs` |
| **Output HTML** | `/home/lenovo/Sync/Seno/Hermes-Outputs/Rekap Lab Hemato *.html` | 30+ file rekap; yang aktif `Rekap Lab Hemato 57 Pasien - 2026-08-29.html` (terakhir) + yang di-serve server |
| **Catatan** | `/home/lenovo/Sync/Seno/Hermes-Outputs/catatan_hemato.json` | Persistent notes per NORM |
| **Pickle (ephemeral)** | `/tmp/simrs_15agustus_full.pkl` & `/tmp/simrs_special_15agustus.pkl` | Hilang tiap reboot — harus regenerate via update_lab/special |
| **Job dir (ephemeral)** | `/tmp/hema_jobs/` | JSON status rebuild/lookup jobs, TTL 1 jam |
| **Feature flag** | `/home/lenovo/.hermes/scripts/feature_flags.json` | `{"lookup_enabled": true}` — matikan lookup jika SIMRS down |

---

## 3) Master Sensus — `patients_29agustus.py`

**Auto-generated**, jangan edit manual kecuali emergency. Di-generate via Update Master List di website.

```python
# header
all_patients = (
    {"norm": 1710191, "nama": "Cahaya Putri Anwar", "ttl": "20-10-2023",
     "dx": "Anemia Defisiensi Besi ...", "status": "KJS", "bed": 1},
    ...
)
PATIENTS = [
    ("PJT", "Kamar 612", "Muhammad Aslan Saputra", 1706040, "Anemia ...", "KJS"),
    # (lokasi, kamar, nama, norm, dx, status)
]
```

- `lokasi`: `PICU`, `Lantai 5/6/7`, `IRD`, `PJT`, `PCC`, `KAMAR ISO` — dipakai grouping
- `status`: `LEADER` / `KJS` / `MCC####` — badge di header
- `ttl`: tanggal lahir bebas format, `"(lihat sensus)"` jika kosong
- Sumber: paste teks WA residen → parser di `hemato_notes_server.py: _parse_census_minimal()` + `update_master_inject.html: parseMaster()`

> Versi harian: `patients_15agustus.py` ... `patients_28agustus.py` (arsip). Yang aktif selalu `patients_29agustus.py` walau tanggal sudah lewat — itu hanya nama file.

---

## 4) Alur Data (End-to-End)

```
Residen kirim list WA (teks sensus)
  → User paste di hema.ark-kay.my.id → Update Master List → Parse → Apply & Rebuild
    → POST /api/master/update → tulis patients_29agustus.py
    → job master_xxx → _run_rebuild(): update_lab.py → update_special.py → gen_16agustus_html.py
      → pickle_lab + pickle_special + HTML baru
    → server restart cache → user lihat report baru

Atau manual:
  python3 update_lab.py [--only NORM]     # fetch lab rutin
  python3 update_special.py [--only NORM] # fetch PA/Rad/BMP/LCS/Immuno/IHC
  python3 gen_16agustus_html.py            # build HTML
  systemctl --user restart hema-report.service
```

**Kenapa 2 pickle terpisah?** Lab rutin fetch cepat (1 req/pasien, limit 200). Special butuh 6 modul × N request, lebih lambat — dipisah biar bisa update parsial.

---

## 5) SIMRS — Sumber Data

- **SIMRS RSWS** di `192.168.23.104:80`, di-tunnel via `simrs.kay.web.id` → `127.0.0.1:8080` (via wstunnel/relay). Lokal Heire akses `http://localhost:8080/webservice`
- **Auth:** `POST /webservice/authentication/login {LOGIN, PASSWORD, CAPTCHA:'x'}` → `PHPSESSID` stable. Kredensial via env `SIMRS_LOGIN` / `SIMRS_PASS` (di server production di-set via systemd, tidak di-commit)
- **Endpoint penting:**
  - `GET /layanan/hasillab?NORM=&limit=200` (lab, tapi `offset` DIABAIKAN server — pakai `start` untuk paging. `total` tidak reliable)
  - `GET /medicalrecord/resume/hasillab/detil?KUNJUNGAN_LAB=<id>` — fast 0.11s/visit
  - `GET /layanan/tindakanmedis?NORM=&JENIS_TINDAKAN=8&NAMA=sumsum` (BMP)
  - `GET /layanan/laboratorium/pa/hasil?KUNJUNGAN=&JENIS_PEMERIKSAAN=` (PA)
  - `GET /layanan/hasilrad?NORM=&limit=50` (Radiologi)
  - Dan 3 modul lagi: LCS, Immuno (fetch_immuno), IHC
- **Gotcha pagination:** jangan pakai `offset`, pakai `start` (ExtJS). Verifikasi di `simrs-data-access` skill ref.

---

## 6) Server — `hemato_notes_server.py` (port 8788)

**Stdlib only** (`http.server.HTTPServer`, no FastAPI). Fitur: in-memory cache + gzip + Cache-Control.

**GET routes** (ringkas):
- `/`, `/index.html` — serve HTML rekap (inject hamburger+modal) + cache mtime
- `/lookup.html` — page lookup RM arbitrary (di luar master) — jangan merge ke list utama
- `/paste-master.html` — legacy paste page
- `/special.html` — special viewer
- `/api/patient/<norm>` — data 1 pasien dari pickle
- `/api/lookup/<norm>?with_special=1` — quick lookup (limit 25 + 3 detil) — cepat <3s
- `/api/lookup-job/start/<norm>` + `/api/lookup-job/status/<id>` — full lookup background (bypass Cloudflare 100s timeout)
- `/api/jobs/recent` — list job rebuild/lookup terbaru (untuk polling notifikasi)
- `/api/notes`, `/api/special/<norm>`, `/api/master/paste`, `/api/build/status/*`, `/api/pasien/lokasi/*`

**POST routes:**
- `/api/notes/<norm>` — autosave catatan (server + localStorage)
- `/api/master/paste`, `/api/master/parse`, `/api/master/execute`, `/api/master/update` — 4 varian update master (parse = regex cepat, update = tulis file + trigger rebuild)
- `/api/refresh` — trigger rebuild tanpa ubah master (Refresh All Lab)
- `/api/lookup-job/start/*` — start background lookup

**Background jobs:**
- `_lookup_jobs` dict + `_JOB_DIR=/tmp/hema_jobs` (file JSON per job)
- `_run_rebuild(job_id)` — background thread: Phase1 lab → Phase2 special → Phase3 build HTML → restart service. Update progress via `_job_write()`
- `_run_full_lookup_job()` — untuk lookup arbitrary RM

**Inject:** saat startup baca `update_master_inject.html` → `inject_update_master_button()` sisipkan sebelum `</style>` dan setelah `<body>`. Ada dedup logic.

**Restart:** `systemctl --user restart hema-report.service` (jangan `sudo`).

---

## 7) Generator — `gen_16agustus_html.py`

Input: `patients_29agustus.PATIENTS` + `PICKLE_LAB=/tmp/simrs_15agustus_full.pkl` + `PICKLE_SPEC=/tmp/simrs_special_15agustus.pkl`
Output: `~/Sync/Seno/Hermes-Outputs/Rekap Lab Hemato 57 Pasien - YYYY-MM-DD.html` (nama tanggal dinamis)

**Fitur:**
1. Grouping: Lokasi → Kamar (PICU, L5, L6, L7 Kemo/Non-Kemo, IRD, PJT, PCC)
2. Lab clustering: kunjungan di hari sama dan selisih ≤2 jam → merge 1 tab; >2 jam → pisah
3. View limit: 5 kunjungan terbaru per pasien, tombol `+xx kunjungan lagi`
4. Urutan param: `STD_ORDER` = HB→PLT→RET→MCV→MCH→MCHC→HCT→WBC→NEUT%→Lymph%→Mono%→Na→K→Cl→Ur→Cr→SGOT→SGPT→Albumin→lainnya
5. Klasifikasi: `classify()` → `N` normal, `A` bermakna, `K` kritis (threshold di `CRIT` dict)
6. Derived ratios (inject di tabel lab):
   - `ANC`, `abs Lymph/Mono`, `NLR`, `PLR`, `LMR`, `MLR`, `SII`, `Mentzer`, `RDW/PLT`, `RPR`, `Sat Transferin` + tambahan `SIRI`, `AISI`, `NMR`, `ELR`, `RLR`, `Shine&Lal`, `England-Frazer`, `HCR`, `RDW/MCV` — semua threshold berbasis **cohort p75/p90** (ward-specific, bukan cutoff dewasa)
   - Formula contoh: `SII = PLT*1000*NEUT/LYMPH`, `RDW/PLT = RDW/PLT*100`, `Mentzer = MCV/RBC`
7. Special modules: card PA/Rad/BMP/LCS/Immuno/IHC per kunjungan, toggle show/hide
8. Notes: textarea autosave `localStorage` + `POST /api/notes` → `catatan_hemato.json`, tombol Export

**Mobile-first**, Telegram WebView compatible (no `position:fixed` di mobile — hamburger slide-in).

---

## 8) Tunnel & Akses Publik

| Domain | Target lokal | Tunnel file |
|---|---|---|
| `hema.ark-kay.my.id` | `127.0.0.1:8788` | `~/.cloudflared/hema-ark-kay.yml` (ID `328259b5-...`) |
| `sirs.kay.web.id` | `127.0.0.1:8099` (SIMRS Web) | `kay-tunnel` (`config-dashboard.yml` ID `21c5b055-...`) |
| `simrs.kay.web.id` | `127.0.0.1:8080` → 192.168.23.104 | idem |
| `rad.kay.web.id` | `127.0.0.1:8081` → 192.168.23.62:8080 | idem |

Jalan via `cloudflared tunnel run` (systemd). Cek: `ps aux | grep cloudflared`, `systemctl --user status hema-report`.

---

## 9) Cara Operasional (SOP)

### A. Update master + lab harian (owner)
1. Buka `https://hema.ark-kay.my.id` → hamburger ☰ → **Update Master List**
2. Paste list WA residen → **Parse** → cek preview (diff: baru/pindah/pulang)
3. **Apply & Rebuild** → job `master_xxx` running → polling tiap 5s → auto-reload HTML saat `done`
4. Fallback manual jika web gagal:
   ```bash
   cd /home/lenovo
   python3 update_lab.py            # atau --only NORM
   python3 update_special.py        # atau --only NORM
   python3 gen_16agustus_html.py
   systemctl --user restart hema-report.service
   ```

### B. Cek pasien di luar sensus
Buka `https://hema.ark-kay.my.id/lookup.html` → masukkan NORM → lookup (quick 3s, full background jika >100s).

### C. Jika pickle hilang (habis reboot)
Pickle di `/tmp` hilang tiap reboot. Regenerate:
```bash
python3 update_lab.py && python3 update_special.py && python3 gen_16agustus_html.py && systemctl --user restart hema-report.service
```
Jangan panik — HTML terakhir di `Sync/Seno/Hermes-Outputs/` tetap ada, hanya job lookup yang butuh pickle.

### D. Restart service
```bash
systemctl --user restart hema-report.service
systemctl --user status hema-report.service --no-pager
journalctl --user -u hema-report.service -n 50 --no-pager
```

---

## 10) Gotcha & Troubleshooting (penting!)

1. **WhatsApp bridge READ-ONLY** — jangan pernah kirim via `send.py/files.py`. Hanya `inbox.py read`.
2. **Jangan `rm -rf` project** — prefer bypass build helper, minta konfirmasi jika >3 file.
3. **Hamburger/UI hilang?** — hard refresh (cache). Cek `update_master_inject.html` ter-load: `curl -s http://127.0.0.1:8788/ | grep hamburger`
4. **Job Apply gak jalan?** — cek `journalctl --user -u hema-report` — sering `NameError` di endpoint `/api/master/update` jika `new_patients` undefined (pernah kejadian 2026-08-31). Fix di `hemato_notes_server.py:1136`.
5. **Timeout Cloudflare 100s** — untuk RM dengan 200+ lab, pakai background job `/api/lookup-job/*`, jangan direct `/api/lookup/`.
6. **Pickle stale overwrite** — sebelum re-fetch, `ps aux | grep fetch_special|prefetch|bmp_` lalu `kill -9` PID lama, karena job lama bisa overwrite pickle dengan data salah (pernah bikin tahun 6010).
7. **Pagination SIMRS** — pakai `start` bukan `offset`, `total` tidak akurat.
8. **Lab order** — selalu HB→PLT→RET→MCV→...→Albumin (fuzzy match).
9. **Hapus catatan?** — `catatan_hemato.json` di Sync, jangan hapus manual — pakai Export di UI.
10. **Port 8788 vs 8099** — Hema 8788, SIMRS Web 8099, jangan tertukar saat debug tunnel.

---

## 11) File Penting untuk AI Agent Baru

- Baca dulu: `~/.hermes/scripts/hemato_notes_server.py` (server), `~/gen_16agustus_html.py` (generator), `~/patients_29agustus.py` (master), `~/fetch_special_15agustus.py` (SIMRS lib)
- Skill relevan: `healthcare/simrs-data-access`, `healthcare/hemato-census-sync`, `healthcare/batch-lab-report-divisi`
- Vault: `/home/lenovo/Sync/Seno/` (vault utama), `/home/lenovo/Sync/Vault-Kedokteran/` (LAN-only)
- Output: `~/Sync/Seno/Hermes-Outputs/` — semua HTML rekap ada di sini

---

## 12) Checklist Serah Terima untuk Agent Baru

- [ ] `systemctl --user status hema-report` → active?
- [ ] `curl -s http://127.0.0.1:8788/api/jobs/recent | jq` → jobs OK?
- [ ] `ls -lh /tmp/simrs*.pkl` → ada? Jika tidak, regenerate
- [ ] `cat ~/.cloudflared/hema-ark-kay.yml` → tunnel hema OK?
- [ ] Buka `https://hema.ark-kay.my.id` → hamburger + Update Master jalan?
- [ ] Test lookup: `https://hema.ark-kay.my.id/lookup.html` → NORM 1129099 (Gibran) keluar?
- [ ] `cat ~/patients_29agustus.py | grep -c norm` → jumlah pasien sesuai sensus?

---

*Dokumen ini digenerate otomatis dari audit live system 2026-09-02. Jika ada perubahan arsitektur, update file ini di `/home/lenovo/Sync/Seno/Hermes-Outputs/HANDOVER-HEMA-ARK-KAY.md` dan beri tahu owner.*

