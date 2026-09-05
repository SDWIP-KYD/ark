# MIGRATION.md — Pindah VM baru, target: clone + restore = 100% jalan

Skenario: VM lama mati/tidak bisa diakses. Yang ada hanya: repo GitHub ini
(`SDWIP-KYD/ark`, deploy key di mesin baru), backup data (rsync dari VM lama
atau sumber di RS), dan akses Cloudflare.

## 0. Prasyarat (milik owner, BUKAN di repo)
- [ ] Deploy key GitHub sudah nempel di mesin baru (`ssh-keygen` + tambah di repo Settings → Deploy keys, wajib **Access Key**)
- [ ] Kredensial SIMRS (username+password) — dari password manager owner
- [ ] `sirs-web/accounts.json` (login SIMRS per dokter) — salin dari VM lama / backup
- [ ] Akses Cloudflare (DNS/tunnel `hema.ark-kay.my.id`, `sirs.kay.web.id`, `simrs.kay.web.id`, `rad.kay.web.id`)

## 1. Clone + kode
```bash
git clone git@github.com:SDWIP-KYD/ark.git && cd ark
sudo mkdir -p /opt/hema /opt/sirs
sudo cp -r server/. /opt/hema/
sudo cp -r sirs-web/main.py sirs-web/static /opt/sirs/
sudo cp pipeline/. /home/lenovo/          # buat dulu /home/lenovo + user-nya
sudo cp deploy/hema.service deploy/sirs.service /etc/systemd/system/
```

## 2. Data pasien (dari backup VM lama — PHI, bukan dari repo)
```bash
# dari VM lama (atau backup):
rsync -av VM-LAMA:/home/lenovo/Sync/Seno/Hermes-Outputs/ /home/lenovo/Sync/Seno/Hermes-Outputs/
rsync -av VM-LAMA:/home/lenovo/patients_29agustus.py /home/lenovo/
rsync -av VM-LAMA:/opt/sirs/accounts.json /opt/sirs/     # chmod 600
```
Kalau backup hilang: `patients_29agustus.py` bisa di-generate ulang dari paste
WA residen via web (Update Master List), rekap HTML lama hilang permanen.

## 3. Kredensial + venv + service
```bash
python3 -m venv /opt/hema/venv && /opt/hema/venv/bin/pip install requests
python3 -m venv /opt/sirs/venv && /opt/sirs/venv/bin/pip install fastapi uvicorn jinja2 httpx
cp .env.example /opt/hema/hema.env   # isi SIMRS_LOGIN / SIMRS_PASS, chmod 600
# NOTE: sampai live system di-scrub (Fase 2.1), fetch_special & hematology_lookup
# di VM baru butuh 2 baris LOGIN/PASS hardcode sementara ATAU env var di-set:
sudo systemctl daemon-reload
sudo systemctl enable --now hema.service sirs.service
```

## 4. Relay wstunnel (SIMRS RSWS → localhost:8080)
wstunnel **server** jalan di VM (port 8088, service `wstunnel.service`):
```
/usr/local/bin/wstunnel server ws://0.0.0.0:8088 --websocket-ping-frequency 15
```
Sisi RS punya client wstunnel yang mengekspos 192.168.23.104:80 → server ini.
`localhost:8080` di VM adalah listener dari client RS. Setelah pindah VM:
- [ ] Update target di client wstunnel sisi RS → IP publik VM baru:8088
- [ ] Instal wstunnel di VM baru + unit `wstunnel.service` (config sederhana, lihat atas)
- [ ] Test: `curl http://localhost:8080/webservice` → harus respons (301/200)

## 5. Cloudflare
- [ ] Arahkan tunnel/DNS `hema.ark-kay.my.id` → `VM-BARU:8788` (HTTP)
- [ ] `sirs.kay.web.id` → `VM-BARU:8099`
- [ ] Verifikasi semua domain 200 dari luar

## 6. Regenerate data operasional + verifikasi akhir
```bash
cd /home/lenovo
python3 update_lab.py && python3 update_special.py && python3 gen_16agustus_html.py
sudo systemctl restart hema.service
```
- [ ] `curl -s http://127.0.0.1:8788/api/jobs/recent` → OK
- [ ] `curl -s https://hema.ark-kay.my.id/` → 200, judul rekap
- [ ] `/lookup.html` test 1 NORM → keluar data lab
- [ ] sirs.kay.web.id login dokter → jalan
```
