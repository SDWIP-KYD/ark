# hema.ark-kay.my.id — Dashboard Rekap Lab Hemato-Onkologi Anak RSWS Makassar

> **REPO PRIVATE — berisi kode sistem medis. Jangan pernah commit data pasien
> (`patients_*.py`, `*.pkl`, rekap HTML, `catatan_hemato.json`) atau kredensial.**

## Peta File — mana yang aktif, mana arsip

| File | Status | Fungsi |
|---|---|---|
| `server/hemato_notes_server.py` | **AKTIF** | Web server stdlib port 8788, API, background jobs. Unit: `hema.service` |
| `server/hematology_lookup.py` | **AKTIF** | Lookup real-time by NORM via SIMRS (dipakai `/api/lookup/*`) |
| `server/lookup.html`, `paste_master.html`, `update_master_inject.html` | **AKTIF** | UI (inject dibaca server saat startup) |
| `server/feature_flags.example.json` | template | Salin ke `feature_flags.json` (`{"lookup_enabled": true}`) |
| `pipeline/update_lab.py` | **AKTIF** | Fetch lab rutin → `/tmp/simrs_15agustus_full.pkl` |
| `pipeline/update_special.py` | **AKTIF** | Fetch PA/Rad/BMP/LCS/Immuno/IHC → `/tmp/simrs_special_15agustus.pkl` |
| `pipeline/fetch_special_15agustus.py` | **AKTIF** | Lib SIMRS (auth, endpoint, `make_session()`, `fetch_*`). Tanggal di nama = label, bukan umur |
| `pipeline/gen_16agustus_html.py` | **AKTIF** | Builder HTML rekap → `~/Sync/Seno/Hermes-Outputs/` |
| `docs/HANDOVER-HEMA-ARK-KAY.md` | referensi | Handover lengkap arsitektur + SOP + gotcha |

**Tidak direpo (data, ada di mesin):** `patients_29agustus.py` (master sensus —
auto-generated, PHI), pickle `/tmp/*.pkl` (ephemeral), output HTML rekap,
`catatan_hemato.json` (catatan klinis).

## Setup mesin baru (dari nol)

1. Salin data (BUKAN dari repo): `patients_29agustus.py` ke `/home/lenovo/`,
   pickle regenerate via `update_lab.py && update_special.py && gen_16agustus_html.py`
2. Buat venv: `python3 -m venv /opt/hema/venv && /opt/hema/venv/bin/pip install requests`
3. Salin `server/*` ke `/opt/hema/`, salin `pipeline/*` ke `/home/lenovo/`
4. Kredensial: `cp .env.example /opt/hema/hema.env` → isi → `chmod 600` →
   tambahkan `EnvironmentFile=/opt/hema/hema.env` di unit systemd
5. Deploy: `sudo cp deploy/hema.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now hema.service`
6. Verifikasi: `curl -s http://127.0.0.1:8788/api/jobs/recent` + buka domain publik

## Gotcha terpenting (selengkapnya di docs/HANDOVER)

- Pagination SIMRS: pakai `start`, BUKAN `offset` (`total` tidak reliable)
- Lookup RM dengan 200+ lab: WAJIB background job `/api/lookup-job/*` (timeout Cloudflare 100s)
- Pickle di `/tmp` hilang tiap reboot → regenerate (HTML lama di Hermes-Outputs tetap aman)
- WhatsApp bridge READ-ONLY — hanya `inbox.py` baca, jangan pernah kirim
- Restart service: `sudo systemctl restart hema.service` (di mesin VM ini; bukan `--user` seperti di Heire)
