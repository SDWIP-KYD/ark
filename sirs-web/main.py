"""
SIMRS Web UI - Step 1: Authenticated Session Server
FastAPI backend. Login via SIMRS API, store PHPSESSID server-side per session.
Credentials never sent to browser; loaded from accounts.json (0600).
"""
import json
import os
import uuid
import time
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

BASE_DIR = Path(__file__).parent
ACCOUNTS_FILE = BASE_DIR / "accounts.json"
SESSIONS = {}  # session_id -> {"simrs_session": requests.Session, "user": label, "login": ...}
SESSION_TTL = 3600 * 8  # 8h

# --- cache statis (nama/TTL/no_hp/kunjungan riwayat): 5 menit ---
CACHE_TTL = 300
_cache = {}
def cache_get(key):
    v = _cache.get(key)
    if v and (time.time() - v["ts"]) < CACHE_TTL: return v["data"]
    _cache.pop(key, None)
    return None
def cache_set(key, data):
    _cache[key] = {"ts": time.time(), "data": data}

app = FastAPI(title="SIMRS Web", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "static")

def load_accounts():
    with open(ACCOUNTS_FILE) as f:
        return json.load(f)

def save_account(login: str, password: str, label: str = None):
    """Append a manual account to accounts.json if not already present."""
    with open(ACCOUNTS_FILE) as f:
        data = json.load(f)
    # skip if login already exists
    for a in data["accounts"]:
        if a["login"].lower() == login.lower():
            return data
    data["accounts"].append({
        "label": label or login,
        "login": login,
        "password": password,
    })
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(ACCOUNTS_FILE, 0o600)
    return data

def simrs_login(login: str, password: str) -> requests.Session:
    """Login to SIMRS, return authenticated session or raise."""
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 SIMRS-Web"})
    r = s.post(
        f"http://127.0.0.1:8080/webservice/authentication/login",
        json={"LOGIN": login, "PASSWORD": password, "CAPTCHA": "x"},
        timeout=30,
    )
    if not r.json().get("success"):
        raise HTTPException(status_code=401, detail="Login SIMRS gagal: " + str(r.json().get("message", "")))
    return s

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    sid = request.cookies.get("sid")
    if sid and sid in SESSIONS:
        return templates.TemplateResponse(request, "dashboard.html", {"user": SESSIONS[sid]["user"]})
    return templates.TemplateResponse(request, "login.html", {"accounts": load_accounts()["accounts"]})

@app.post("/api/login")
def api_login(payload: dict):
    """payload: {mode: 'select'|'manual', login?, password?, account_index?}"""
    accs = load_accounts()
    if payload.get("mode") == "select":
        idx = int(payload["account_index"])
        acc = accs["accounts"][idx]
        login, password, label = acc["login"], acc["password"], acc["label"]
    else:
        login, password = payload["login"], payload["password"]
        label = payload.get("label") or login
    try:
        s = simrs_login(login, password)
    except HTTPException as e:
        return JSONResponse({"ok": False, "error": e.detail}, status_code=401)
    # save manual account for future use (skip if already in list)
    if payload.get("mode") != "select":
        save_account(login, password, label)
    sid = str(uuid.uuid4())
    SESSIONS[sid] = {"simrs_session": s, "user": label, "login": login}
    resp = JSONResponse({"ok": True, "user": label})
    resp.set_cookie("sid", sid, httponly=True, max_age=SESSION_TTL, samesite="Lax")
    return resp

@app.get("/api/logout")
def api_logout(request: Request):
    sid = request.cookies.get("sid")
    if sid in SESSIONS:
        del SESSIONS[sid]
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie("sid")
    return resp

@app.get("/api/me")
def api_me(request: Request):
    sid = request.cookies.get("sid")
    if sid in SESSIONS:
        return {"ok": True, "user": SESSIONS[sid]["user"]}
    return {"ok": False}

# --- helper for downstream steps (not used in step 1 UI yet) ---
def get_simrs(request: Request) -> requests.Session:
    sid = request.cookies.get("sid")
    if not sid or sid not in SESSIONS:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return SESSIONS[sid]["simrs_session"]

def get_user(request: Request) -> str:
    sid = request.cookies.get("sid")
    if not sid or sid not in SESSIONS:
        return ""
    return SESSIONS[sid].get("user", "")

import re, html as _html

def strip_html(h):
    if not h:
        return ""
    h = h.replace("\\u003C", "<").replace("\\u003E", ">").replace("\\u0022", '"').replace("\\/", "/")
    # <br> and block tags -> newline
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"<div[^>]*>", "\n", h, flags=re.I)  # OPENING div also breaks line
    h = re.sub(r"</(p|div|li|tr)>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    # SIMRS uses '*' as line separator in some fields (Objektif etc.)
    h = h.replace("*", "\n")
    t = _html.unescape(h)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    # split merged field labels FIRST: "menitPernapasan" -> "menit\nPernapasan"
    t = re.sub(r"([a-z]+)([A-Z][a-z]+(?:\s*:|(?:\s|$)))", r"\1\n\2", t)
    # add space between value and reference range that got merged (e.g. "mmHgNormal")
    t = re.sub(r"(\d)([A-Z][a-z]+)", r"\1 \2", t)
    # split merged reference labels: "mmHgNormal" -> "mmHg Normal", "mmHgMeningkat" -> "mmHg Meningkat"
    t = re.sub(r"(mmHg|kg|cm|bpm|x|%)([A-Z][a-z]+)", r"\1 \2", t)
    # fix "mm\nHg" back to "mmHg" (over-split by previous rule)
    t = re.sub(r"mm\s*\n\s*Hg", "mmHg", t)
    t = t.replace("mm Hg", "mmHg")
    t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t).strip()
    return t

@app.get("/testing", response_class=HTMLResponse)
def testing_page(request: Request):
    sid = request.cookies.get("sid")
    if not sid or sid not in SESSIONS:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "testing.html", {"user": SESSIONS[sid]["user"]})

@app.get("/api/pasien")
def api_pasien(request: Request, norm: str = ""):
    if not norm:
        return JSONResponse({"ok": False, "error": "NORM kosong"}, status_code=400)
    cached = cache_get(("pasien", norm))
    if cached:
        return JSONResponse(cached)
    sess = get_simrs(request)
    BASE = "http://127.0.0.1:8080"
    r = sess.get(f"{BASE}/webservice/general/pasien/{norm}", timeout=20)
    if "json" in r.headers.get("content-type", "") and r.json().get("success"):
        p = r.json().get("data", {})
        hp = ""
        for kt in (p.get("KONTAK", []) or []):
            if isinstance(kt, dict) and (kt.get("JENIS") in (3, "3") or "081" in str(kt.get("NOMOR", ""))):
                hp = kt.get("NOMOR", ""); break
        out = {
            "ok": True, "norm": norm,
            "nama": p.get("NAMA", ""),
            "tgl_lahir": (p.get("TANGGAL_LAHIR", "") or "")[:10],
            "jk": "L" if p.get("JENIS_KELAMIN") == 1 else "P" if p.get("JENIS_KELAMIN") == 2 else "",
            "alamat": p.get("ALAMAT", ""),
            "no_hp": hp,
        }
        cache_set(("pasien", norm), out)
        return JSONResponse(out)
    return JSONResponse({"ok": False, "error": "Pasien tidak ditemukan"}, status_code=404)

@app.get("/api/kunjungan")
def api_kunjungan(request: Request, norm: str = ""):
    if not norm:
        return JSONResponse({"ok": False, "error": "NORM kosong"}, status_code=400)
    cached = cache_get(("kunjungan", norm))
    if cached:
        return JSONResponse(cached)
    sess = get_simrs(request)
    BASE = "http://127.0.0.1:8080"
    r = sess.get(f"{BASE}/webservice/pendaftaran/kunjungan",
                  params={"NORM": norm, "STATUS": "[1,2]", "limit": 100,
                          "REFERENSI": json.dumps({"Ruangan": {"COLUMNS": ["DESKRIPSI", "JENIS_KUNJUNGAN"]}, "DPJP": True})},
                  timeout=30)
    if not r.json().get("success"):
        return JSONResponse({"ok": False, "error": "Gagal"}, status_code=404)
    out = []
    for k in r.json().get("data", []):
        ref = k.get("REFERENSI", {})
        dpjp = ref.get("DPJP", {})
        out.append({
            "kunjungan": k.get("NOMOR", ""),
            "nopen": k.get("NOPEN", ""),
            "masuk": (k.get("MASUK", "") or "")[:19],
            "keluar": (k.get("KELUAR", "") or "")[:19] if k.get("KELUAR") else "",
            "ruangan": ref.get("RUANGAN", {}).get("DESKRIPSI", ""),
            "dpjp": dpjp.get("NAMA", "") if isinstance(dpjp, dict) else "",
        })
    # sort by masuk desc
    out.sort(key=lambda x: x["masuk"], reverse=True)
    res = {"ok": True, "norm": norm, "total": len(out), "kunjungan": out}
    cache_set(("kunjungan", norm), res)
    return JSONResponse(res)



@app.get("/api/ranap/aktif")
def api_ranap_aktif(request: Request, limit: int = 100, start: int = 0):
    """Daftar pasien rawat inap aktif untuk DPJP yang sedang login (MY_PASIEN=1). Cache 90 detik (refresh lebih sering — bisa ada pasien pindah kamar)."""
    user = get_user(request)
    cached = cache_get(("ranap_aktif", user, start, limit))
    if cached:
        return JSONResponse(cached)
    sess = get_simrs(request)
    BASE = "http://127.0.0.1:8080"
    REFERENSI = json.dumps({
        "Ruangan": {"COLUMNS": ["DESKRIPSI", "JENIS_KUNJUNGAN"], "REFERENSI": {"Referensi": True}},
        "Pendaftaran": True,
        "Referensi": True,
        "RuangKamarTidur": True,
        "DPJP": True,
        "Pasien": True,
        "Mutasi": True,
        "AntrianRuangan": {"COLUMNS": ["ID", "POS", "NOMOR"]}
    })
    try:
        r = sess.get(f"{BASE}/webservice/pendaftaran/kunjungan",
                     params={"STATUS": 1, "REFERENSI": REFERENSI, "MY_PASIEN": 1,
                             "page": 1, "start": start, "limit": limit},
                     timeout=60)
        j = r.json()
        if not j.get("success"):
            return JSONResponse({"ok": False, "error": "SIMRS return non-success"}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)
    out = []
    norms_missing = []
    for k in j.get("data", []):
        ref = k.get("REFERENSI", {})
        ruangan = ref.get("RUANGAN", {}) or {}
        rkt = ref.get("RUANG_KAMAR_TIDUR", {}) or {}
        pdt = ref.get("PENDAFTARAN", {}) or {}
        dpjp = ref.get("DPJP", {}) or {}
        # Bed: RKT.TEMPAT_TIDUR + nested KAMAR
        bed = ""
        rkt_ref = rkt.get("REFERENSI", {}) or {}
        kamar = (rkt_ref.get("RUANG_KAMAR", {}) or {}).get("KAMAR", "")
        bed = rkt.get("TEMPAT_TIDUR") or rkt.get("NOMOR") or ""
        kamar_label = f"{kamar} {bed}".strip()
        # Nama from Pasien nested. NAMA lives in
        # REFERENSI.PENDAFTARAN.REFERENSI.PASIEN
        # — verified live with walk(). Use 3-level deep lookup.
        pdt_ref = pdt.get("REFERENSI", {}) or {}
        # Fallback chain: level-1 PASIEN → level-2 PDT.PASIEN → level-3 PDT.REFERENSI.PASIEN
        pasien = (ref.get("PASIEN", {}) or
                   pdt.get("PASIEN", {}) or
                   pdt_ref.get("PASIEN", {}) or {})
        nama = pasien.get("NAMA", "") or ""
        jk = pasien.get("JENIS_KELAMIN", "")
        ttl = (pasien.get("TANGGAL_LAHIR", "") or "")[:10]
        norm = str(pdt.get("NORM", "") or pasien.get("NORM", "") or
                   pdt_ref.get("PASIEN", {}).get("NORM", ""))
        # No HP dari KONTAK (JENIS=3 = telepon seluler)
        no_hp = ""
        for kt in (pasien.get("KONTAK", []) or []):
            if isinstance(kt, dict) and (kt.get("JENIS") in (3, "3") or "081" in str(kt.get("NOMOR",""))):
                no_hp = kt.get("NOMOR", "")
                break
        if nama:
            pasien_info = {"nama": nama, "jk": ("L" if jk == 1 else "P" if jk == 2 else ""), "tgl_lahir": ttl, "no_hp": no_hp}
        else:
            pasien_info = None
            if norm:
                norms_missing.append(norm)
        dx = ""
        mutasi = ref.get("Mutasi", [])
        if isinstance(mutasi, list) and mutasi:
            dx = mutasi[0].get("DIAGNOSA") or mutasi[0].get("DESKRIPSI") or ""
        if not dx:
            dx = pdt.get("DIAGNOSA") or pdt.get("DIAGNOSIS") or ""
        out.append({
            "kunjungan": k.get("NOMOR", ""),
            "nopen": k.get("NOPEN", ""),
            "norm": norm,
            "nama": nama,
            "jk": pasien_info["jk"] if pasien_info else "",
            "tgl_lahir": pasien_info["tgl_lahir"] if pasien_info else "",
            "no_hp": pasien_info["no_hp"] if pasien_info else "",
            "ruangan": ruangan.get("DESKRIPSI", ""),
            "ruangan_id": k.get("RUANGAN", ""),
            "kamar": kamar_label,
            "kamar_id": k.get("RUANG_KAMAR_TIDUR", ""),
            "masuk": (k.get("MASUK", "") or "")[:19],
            "dpjp": dpjp.get("NAMA", "") if isinstance(dpjp, dict) else "",
            "dx": dx,
        })
    # Fetch missing patient names in parallel (max 60)
    import concurrent.futures as cf
    if norms_missing:
        def get_pasien(norm):
            try:
                rr = sess.get(f"{BASE}/webservice/general/pasien/{norm}", timeout=20)
                if "json" in rr.headers.get("content-type", "") and rr.json().get("success"):
                    d = rr.json().get("data", {})
                    hp = ""
                    for kt in (d.get("KONTAK", []) or []):
                        if isinstance(kt, dict) and (kt.get("JENIS") in (3, "3") or "081" in str(kt.get("NOMOR",""))):
                            hp = kt.get("NOMOR", ""); break
                    return norm, {
                        "nama": d.get("NAMA", ""),
                        "jk": ("L" if d.get("JENIS_KELAMIN") == 1 else "P" if d.get("JENIS_KELAMIN") == 2 else ""),
                        "tgl_lahir": (d.get("TANGGAL_LAHIR", "") or "")[:10],
                        "no_hp": hp,
                    }
            except Exception:
                pass
            return norm, None
        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(get_pasien, norms_missing))
        pmap = dict(results)
        for item in out:
            if not item["nama"] and item["norm"] in pmap and pmap[item["norm"]]:
                item["nama"] = pmap[item["norm"]]["nama"]
                item["jk"] = pmap[item["norm"]]["jk"]
                item["tgl_lahir"] = pmap[item["norm"]]["tgl_lahir"]
                item["no_hp"] = pmap[item["norm"]].get("no_hp", "")
    out.sort(key=lambda x: (x["ruangan"] or "", x["kamar"] or "", x["nama"] or ""))
    # Cache 120s (pasien bisa pindah kamar, tapi refresh tiap 2 menit cukup cepet)
    ranap_res = {"ok": True, "total": j.get("total", len(out)), "data": out}
    _cache[("ranap_aktif", user, start, limit)] = {"ts": time.time() + 120, "data": ranap_res}  # +120 override TTL
    return JSONResponse(ranap_res)

@app.get("/api/cppt")
def api_cppt(request: Request, kunjungan: str = "", norm: str = ""):
    sess = get_simrs(request)
    BASE = "http://127.0.0.1:8080"
    if kunjungan:
        # fetch CPPT by single kunjungan
        r = sess.get(f"{BASE}/webservice/medicalrecord/cppt",
                      params={"KUNJUNGAN": kunjungan, "limit": 100}, timeout=30)
        rows = r.json().get("data", []) if r.json().get("success") else []
    elif norm:
        # fetch all CPPT history by NORM (fallback)
        r = sess.get(f"{BASE}/webservice/medicalrecord/cppt",
                      params={"HISTORY": 1, "NORM": norm, "limit": 3000, "sort": json.dumps({"property": "TANGGAL", "direction": "DESC"})}, timeout=30)
        rows = r.json().get("data", []) if r.json().get("success") else []
    else:
        return JSONResponse({"ok": False, "error": "NORM atau KUNJUNGAN kosong"}, status_code=400)
    out = []
    for c in rows:
        ref = c.get("REFERENSI", {})
        tm = ref.get("TENAGA_MEDIS", {})
        penulis = tm.get("NAMA", "(tanpa nama)")
        jenis = ref.get("JENIS", {}).get("DESKRIPSI", "")
        penulis_full = f"{penulis} ({jenis})" if jenis else penulis
        out.append({
            "tanggal": c.get("TANGGAL", ""),
            "kunjungan": c.get("KUNJUNGAN", ""),
            "penulis": penulis_full,
            "subjektif": strip_html(c.get("SUBYEKTIF", "")),
            "objektif": strip_html(c.get("OBYEKTIF", "")),
            "assesment": strip_html(c.get("ASSESMENT", "")),
            "terapi": strip_html(c.get("INSTRUKSI", "")),
            "planning": strip_html(c.get("PLANNING", "")),
        })
    return JSONResponse({"ok": True, "total": len(out), "cppt": out})

@app.get("/api/detail")
def api_detail(request: Request, kunjungan: str = "", nopen: str = "", norm: str = ""):
    sess = get_simrs(request)
    if not kunjungan:
        return JSONResponse({"ok": False, "error": "KUNJUNGAN kosong"}, status_code=400)
    DEBUG_LOG.append(f"DETAIL req: kunjungan={kunjungan} nopen={nopen}")
    BASE = "http://127.0.0.1:8080"
    def get(url, params):
        try:
            r = sess.get(f"{BASE}{url}", params=params, timeout=25)
            if "json" in r.headers.get("content-type", "") and r.json().get("success"):
                return r.json().get("data", [])
        except: pass
        return []
    def first_text(data, field):
        if isinstance(data, list) and data:
            return strip_html(data[0].get(field, ""))
        if isinstance(data, dict):
            return strip_html(data.get(field, ""))
        return ""
    # anamnesis
    anam = get("/webservice/medicalrecord/anamnesis", {"KUNJUNGAN": kunjungan, "limit": 25})
    kel_utama = get("/webservice/medicalrecord/anamnesis/keluhan/utama", {"KUNJUNGAN": kunjungan, "limit": 25})
    # pemeriksaan fisik
    fisik = get("/webservice/medicalrecord/pemeriksaan/fisik", {"KUNJUNGAN": kunjungan, "limit": 25})
    # rencana terapi
    terapi = get("/webservice/medicalrecord/perencanaan/rencanaterapi", {"KUNJUNGAN": kunjungan, "limit": 25})
    # penilaian diagnosis
    penilaian = get("/webservice/medicalrecord/penilaian/diagnosis", {"KUNJUNGAN": kunjungan, "limit": 25})
    # icd10
    icd = get("/webservice/medicalrecord/icd10", {"NOPEN": nopen, "STATUS": 1, "limit": 100}) if nopen else []
    # konsul -> pakai NORM + HISTORY (HAR-confirmed: pendaftaran/konsul?NORM=..&HISTORY=1)
    # jawaban -> pendaftaran/jawabankonsul?KONSUL_NOMOR=.. per item
    konsul_raw = get("/webservice/pendaftaran/konsul", {"NORM": norm, "HISTORY": 1, "limit": 25}) if norm else []
    if not konsul_raw and nopen:
        asal = ""
        rk = sess.get(f"{BASE}/webservice/pendaftaran/kunjungan", params={"NOMOR": kunjungan, "limit": 1}, timeout=20).json()
        if rk.get("data"):
            asal = rk["data"][0].get("RUANGAN", "")
        konsul_raw = get("/webservice/pendaftaran/konsul", {"NOPEN": nopen, "ASAL": asal, "HISTORY": 1, "limit": 25})
    konsul = []
    for k in konsul_raw:
        knomor = k.get("NOMOR", "")
        jawaban = {}
        if knomor:
            jw = get("/webservice/pendaftaran/jawabankonsul", {"KONSUL_NOMOR": knomor, "limit": 5})
            if isinstance(jw, list) and jw:
                jawaban = jw[0]
        konsul.append({
            "tanggal": k.get("TANGGAL"),
            "permintaan": strip_html(k.get("ALASAN","")),
            "instruksi": strip_html(k.get("PERMINTAAN_TINDAKAN","")),
            "tujuan": (k.get("REFERENSI",{}).get("TUJUAN",{}) or {}).get("DESKRIPSI",""),
            "dokter_tujuan": (k.get("REFERENSI",{}).get("DOKTER_TUJUAN",{}) or {}).get("NAMA",""),
            "status": (k.get("REFERENSI",{}).get("STATUS",{}) or {}).get("DESKRIPSI",""),
            "jawaban": strip_html(jawaban.get("JAWABAN","")),
            "anjuran": strip_html(jawaban.get("ANJURAN","")),
            "jawaban_tanggal": (jawaban.get("TANGGAL","") or "")[:16],
            "jawaban_dokter": ((jawaban.get("REFERENSI",{}) or {}).get("DOKTER",{}) or {}).get("NAMA",""),
            "layak_operasi": jawaban.get("STATUS_LAYAK_OPERASI"),
            "rawat_bersama": jawaban.get("STATUS_RAWAT_BERSAMA"),
        })
    # urut konsul terbaru -> terlama
    konsul.sort(key=lambda k: (k.get("tanggal") or ""), reverse=True)
    return JSONResponse({
        "ok": True,
        "keluhan_utama": first_text(kel_utama, "DESKRIPSI"),
        "riwayat_penyakit_sekarang": first_text(anam, "RPS") or first_text(anam, "DESKRIPSI"),
        "riwayat": first_text(anam, "RPT") or first_text(anam, "RPK"),
        "pemeriksaan_fisik": first_text(fisik, "DESKRIPSI"),
        "rencana_terapi": "\n".join([strip_html(t.get("DESKRIPSI","")) for t in terapi if t.get("DESKRIPSI")]) if isinstance(terapi, list) else "",
        "penilaian_diagnosis": "\n".join([strip_html(d.get("DIAGNOSIS","")) for d in penilaian if d.get("DIAGNOSIS")]) if isinstance(penilaian, list) else "",
        "icd10": [{"kode": i.get("KODE"), "diagnosa": i.get("DIAGNOSA"), "utama": i.get("UTAMA")} for i in icd] if isinstance(icd, list) else [],
        "konsul": konsul,
    })

@app.get("/api/lab/resume")
@app.get("/api/lab/index")
def api_lab_index(request: Request, norm: str = "", kunjungan: str = "", page: int = 1):
    """List panel lab (tanggal + jenis) via tindakanmedis search (comprehensive keywords).
    NO detail fetch - frontend loads detil on-click. Page=1 all (list <10s backend)."""
    sess = get_simrs(request)
    if not norm and not kunjungan:
        return JSONResponse({"ok": False, "error": "NORM atau KUNJUNGAN kosong"}, status_code=400)
    BASE = "http://127.0.0.1:8080"
    n = norm or kunjungan
    LAB_KEYWORDS = [
        "hematologi","kimia","pemeriksaan laboratorium","marker","serologi","darah tepi","sumsum",
        "gambaran darah","gambaran sumsum","faal hemostasis","hemostasis","gula","lemak","elektrolit",
        "ureum","kreatinin","sgot","sgpt","albumin","protein","bilirubin","asam urat","led","d-dimer",
        "crp","feritin","ldh","trigliserida","kolesterol","natrium","kalium","klor","kalsium","tiroid",
        "hbsag","hiv","widal","malaria","laboratorium","pemeriksaan lab","darah lengkap","fungsi hati",
        "fungsi ginjal","gds","gdpp","hba1c","pt","aptt","inr","tt","fibrinogen","retikulosit","leukosit",
        "trombosit","eritrosit","hemoglobin","analisa gas","agd","urin","urine","feces","faeces","tinja",
        "sputum","dahak","swab","cairan","sedimen","makroskopik","mikroskopik","urinalisis","analisa faeces",
        "urin rutin","bta","pulasan","kultur","biakan","gram","eksudat","transudat","complement","ana",
        "autoantibodi","imunologi","antibodi","c3","c4","lupus","dsdna","reumatoid","aslo","asio","vdrl",
        "hcv","tokso","rubella","cmv","imunofenotip","leukimia phenotyp","imunofluoresensi","antinuklear",
        "panel","screening","sitologi","histopatologi","imunohistokimia","flowsitometri","bmp","pbf","aptt",
    ]
    panels = []
    seen = set()
    try:
        import concurrent.futures as cf
        def fetch_kw(kw):
            try:
                r = sess.get(f"{BASE}/webservice/layanan/tindakanmedis", params={"NORM": n, "NAMA": kw, "limit": 50}, timeout=15)
                if "json" in r.headers.get("content-type", "") and r.json().get("success"):
                    out = []
                    for t in r.json().get("data", []):
                        kj = t.get("KUNJUNGAN")
                        if kj and kj not in seen:
                            seen.add(kj)
                            out.append({"kunjungan_lab": kj, "tanggal": (t.get("TANGGAL") or "")[:19], "jenis": t.get("TINDAKAN_DESKRIPSI") or "Lab", "n_items": 0})
                    return out
            except: pass
            return []
        with cf.ThreadPoolExecutor(max_workers=10) as ex:
            for res in ex.map(fetch_kw, LAB_KEYWORDS):
                panels.extend(res)
    except Exception as e:
        DEBUG_LOG.append(f"lab_index err: {e}")
    panels.sort(key=lambda p: p["tanggal"], reverse=True)
    return JSONResponse({"ok": True, "total": len(panels), "panels": panels})


@app.get("/api/lab/detil")
def api_lab_detil(request: Request, kunjungan_lab: str = ""):
    """ON-CLICK: fetch detail 1 panel lab via resume/hasillab/detil."""
    sess = get_simrs(request)
    if not kunjungan_lab:
        return JSONResponse({"ok": False, "error": "KUNJUNGAN_LAB kosong"}, status_code=400)
    BASE = "http://127.0.0.1:8080"
    try:
        r = sess.get(f"{BASE}/webservice/medicalrecord/resume/hasillab/detil", params={"KUNJUNGAN_LAB": kunjungan_lab}, timeout=20)
        if "json" not in r.headers.get("content-type", "") or not r.json().get("success"):
            # SIMRS return non-success (timeout/rate-limit/old data) - return error tanpa label "arsip"
            return JSONResponse({"ok": False, "error": "Gagal fetch detail lab (SIMRS timeout atau data tidak tersedia)"}, status_code=200)
        det = r.json().get("data", [])
        items = []
        for d in det:
            ref = d.get("REFERENSI", {})
            param = ref.get("PARAMETER_TINDAKAN", {}) or ref.get("LABORATORIUM", {}) or {}
            name = d.get("PARAMETER") or param.get("PARAMETER") or param.get("NAMA") or d.get("NAMA")
            val = d.get("HASIL")
            sat = d.get("SATUAN") or ""
            normv = d.get("NORMAL") or d.get("NILAI_NORMAL") or param.get("NILAI_RUJUKAN") or ""
            if name and val is not None and str(val).strip():
                items.append({
                    "nama": str(name).strip(),
                    "hasil": str(val).strip(),
                    "satuan": str(sat).strip(),
                    "normal": str(normv).strip(),
                    "jenis": classify_lab(str(name).strip()),
                })
        return JSONResponse({"ok": True, "kunjungan_lab": kunjungan_lab, "items": items})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)



@app.get("/api/lab/special")
def api_lab_special(request: Request, norm: str = "", kunjungan: str = ""):
    """Lab special: PA, IHC, LCS, BMP, Immuno, Radiologi — pakai cara hematology_lookup.py / fetch_special_15agustus.py."""
    sess = get_simrs(request)
    if not norm and not kunjungan:
        return JSONResponse({"ok": False, "error": "NORM atau KUNJUNGAN kosong"}, status_code=400)
    BASE = "http://127.0.0.1:8080"
    n = norm or kunjungan
    def get(url, params):
        try:
            r = sess.get(f"{BASE}{url}", params=params, timeout=25)
            if "json" in r.headers.get("content-type", "") and r.json().get("success"):
                return r.json().get("data", [])
        except: pass
        return []
    def search_tindakan(nama, jt=8):
        out = []
        try:
            r = sess.get(f"{BASE}/webservice/layanan/tindakanmedis", params={"NORM": n, "JENIS_TINDAKAN": jt, "STATUS": 1, "NAMA": nama, "limit": 50}, timeout=15)
            if "json" in r.headers.get("content-type", "") and r.json().get("success"):
                for t in r.json().get("data", []):
                    out.append((t.get("KUNJUNGAN"), t.get("ID"), t.get("TANGGAL"), t.get("TINDAKAN_DESKRIPSI") or nama))
        except: pass
        return out
    results = []
    # PA / IHC -> pa/hasil JENIS 1,2,3
    seen_kunj = set()
    for kunj, tm_id, tgl, desc in search_tindakan("histopat") + search_tindakan("sitolog") + search_tindakan("anatomi") + search_tindakan("ihc") + search_tindakan("patologi") + search_tindakan("Imunohistokimia"):
        if kunj in seen_kunj: continue
        seen_kunj.add(kunj)
        for jp in [1, 2, 3]:
            pa = get("/webservice/layanan/laboratorium/pa/hasil", {"KUNJUNGAN": kunj, "JENIS_PEMERIKSAAN": jp})
            if pa:
                for d in pa:
                    hasil = strip_html(d.get("KESIMPULAN") or d.get("HASIL") or d.get("KESAN") or "")
                    detail = {}
                    for k in ["MAKROSKOPIK","MIKROSKOPIK","KESIMPULAN","HASIL","KESAN","JARINGAN"]:
                        if d.get(k): detail[k] = strip_html(d[k])
                    if hasil or detail:
                        jn = "PA" if jp==1 else "LCS" if jp==2 else "IHC"
                        results.append({"tanggal": (d.get("TANGGAL") or tgl or "")[:10], "jenis": jn, "jaringan": desc or d.get("JARINGAN") or jn, "kesimpulan": hasil, "detail": detail})
                break
    # Radiologi -> hasilrad (NORM) — FULL: KLINIS, HASIL, KESAN, USUL, BTK
    # Build exam_name map dari tindakanmedis (ID(str) -> TINDAKAN_DESKRIPSI)
    # NOTE: JENIS_TINDAKAN=8 cuma lab; radiologi jenis beda -> pakai search tanpa filter jenis
    rad_map = {}
    try:
        for kw in ["Radiografi","Foto Thorak","CT Scan","USG","MRI","Scan"]:
            rr = sess.get(f"{BASE}/webservice/layanan/tindakanmedis",
                          params={"NORM": n, "NAMA": kw, "limit": 50}, timeout=15)
            if "json" in rr.headers.get("content-type", "") and rr.json().get("success"):
                for t in rr.json().get("data", []):
                    rad_map[str(t.get("ID", ""))] = t.get("TINDAKAN_DESKRIPSI") or kw
    except Exception:
        pass
    rad = get("/webservice/layanan/hasilrad", {"NORM": n, "limit": 50})
    for d in rad:
        detail = {}
        klinis = strip_html(d.get("KLINIS") or "")
        hasil = strip_html(d.get("HASIL") or "")
        kesan = strip_html(d.get("KESAN") or "")
        usul = strip_html(d.get("USUL") or "")
        btk = strip_html(d.get("BTK") or "")
        # Exam name dari tindakanmedis map
        exam_name = rad_map.get(str(d.get("TINDAKAN_MEDIS","")), "")
        if klinis: detail["Indikasi"] = klinis
        if hasil: detail["Hasil"] = hasil
        if kesan: detail["Kesan"] = kesan
        if usul: detail["Usulan"] = usul
        if btk: detail["Dibaca oleh"] = btk
        if detail:
            results.append({"tanggal": (d.get("TANGGAL") or "")[:10], "jenis": "Radiologi", "jaringan": exam_name or klinis or "Radiologi", "kesimpulan": kesan, "detail": detail})
    # BMP -> evaluasisst (search sumsum)
    for kunj, tm_id, tgl, desc in search_tindakan("sumsum") + search_tindakan("GAMBARAN SUMSUM"):
        ev = get("/webservice/layanan/hasil/evaluasisst", {"KUNJUNGAN": kunj, "limit": 25})
        if ev:
            b = ev[0]
            detail = {}
            for k in ["SELULARITAS","ERITROPOIETIK","LEUKOPOIETIK","TROMBOPOIETIK","SEL_PLASMA","MITOSIS","ME_RATIO"]:
                if b.get(k): detail[k] = strip_html(b[k])
            kesan = strip_html(b.get("KESAN") or b.get("KESIMPULAN") or "")
            if kesan: detail["Kesan"] = kesan
            if detail:
                results.append({"tanggal": (tgl or "")[:10], "jenis": "BMP", "jaringan": desc or "Sumsum Tulang", "kesimpulan": kesan, "detail": detail})
    # LCS -> pa/hasil JENIS=2 (search LCS)
    for kunj, tm_id, tgl, desc in search_tindakan("LCS") + search_tindakan("cairan tubuh") + search_tindakan("Efusi") + search_tindakan("Ascites"):
        lcs = get("/webservice/layanan/laboratorium/pa/hasil", {"KUNJUNGAN": kunj, "JENIS_PEMERIKSAAN": 2})
        for d in lcs:
            hasil = strip_html(d.get("KESIMPULAN") or d.get("HASIL") or d.get("KESAN") or "")
            detail = {}
            for k in ["MAKROSKOPIK","MIKROSKOPIK","KESIMPULAN","HASIL","KESAN"]:
                if d.get(k): detail[k] = strip_html(d[k])
            if hasil or detail:
                results.append({"tanggal": (d.get("TANGGAL") or tgl or "")[:10], "jenis": "LCS", "jaringan": desc or "LCS", "kesimpulan": hasil, "detail": detail})
    # Immuno -> hasillab + catatanhasillab
    for kunj, tm_id, tgl, desc in search_tindakan("LEUKIMIA") + search_tindakan("IMUNOFENOTIP") + search_tindakan("FENOTIP"):
        hl = get("/webservice/layanan/hasillab", {"TINDAKAN_MEDIS": tm_id})
        params = [f"{row.get('REFERENSI',{}).get('PARAMETER_TINDAKAN',{}).get('PARAMETER') or row.get('PARAMETER')}: {row.get('HASIL')} {row.get('SATUAN','')}".strip() for row in hl if row.get("HASIL")]
        cat = get("/layanan/catatanhasillab", {"KUNJUNGAN": kunj})
        cat_teks = cat[0].get("CATATAN", "") if cat else ""
        detail = {}
        if params: detail["Hasil"] = " | ".join(params)
        if cat_teks: detail["Catatan"] = cat_teks
        if detail:
            results.append({"tanggal": (tgl or "")[:10], "jenis": "Immuno", "jaringan": desc or "Immunofenotyping", "kesimpulan": detail.get("Hasil","")[:100], "detail": detail})
    # Kultur / Biakan
    for kunj, tm_id, tgl, desc in search_tindakan("KULTUR") + search_tindakan("BIAKAN") + search_tindakan("SENSITIVITAS"):
        hl = get("/webservice/layanan/hasillab", {"TINDAKAN_MEDIS": tm_id})
        items = []
        for row in hl:
            nm = row.get("REFERENSI",{}).get("PARAMETER_TINDAKAN",{}).get("PARAMETER") or row.get("PARAMETER")
            vl = row.get("HASIL")
            if nm and vl: items.append(f"• {nm}: {vl}")
        if items:
            results.append({"tanggal": (tgl or "")[:10], "jenis": "Kultur", "jaringan": desc, "kesimpulan": items[0] if items else "", "detail": {"Hasil": "\n".join(items)}})
    return JSONResponse({"ok": True, "total": len(results), "special": results})


def nopen_from_kunjungan(sess, kunjungan):
    BASE = "http://127.0.0.1:8080"
    try:
        r = sess.get(f"{BASE}/webservice/pendaftaran/kunjungan", params={"NOMOR": kunjungan, "limit": 1}, timeout=15)
        if "json" in r.headers.get("content-type", "") and r.json().get("success") and r.json().get("data"):
            return r.json()["data"][0].get("NOPEN", "")
    except: pass
    return ""


def classify_lab(name):
    n = name.lower().strip()
    # Hematologi (full names + abbreviations)
    if any(k in n for k in ["wbc", "leukosit", "rbc", "eritrosit", "hgb", "hb", "hemoglobin", "hct", "hematokrit",
                              "mcv", "mch", "mchc", "plt", "trombosit", "rdw", "pdw", "mpv", "pct", "neut", "lymph",
                              "mono", "eo", "baso", "ret", "retikulo", "nrbc", "ipf", "lfr", "mfr", "hfr", "ret-he",
                              "led", "gds", "gdpp", "hba1c", "d-dimer", "crp", "prokalsitonin", "feritin", "ldh",
                              "asam urat", "eritrosit", "sel darah", "hitung jenis", "darah tepi", "pbf", "index",
                              "blast", "mayor", "minor", "auto control", "jumlah blast", "rasio", "it ", "p-lcr", "p-lcc"]):
        return "Hematologi"
    if any(k in n for k in ["glukosa", "ureum", "kreatinin", "sgot", "sgpt", "alanin", "asam", "natrium", "kalium",
                              "klor", "kalsium", "albumin", "protein", "kolesterol", "trigliser", "bilirubin",
                              "elektrolit", "egfr", "fungsi hati", "fungsi ginjal", "na", "k ", "cl", "ca", "ph",
                              "panel hati", "panel ginjal", "lipoprotein", "amilase", "lipase", "ck", "ckmb", "troponin",
                              "po2", "pco2", "so2", "hco3", "be", "cto2", "ctco2", "gas darah", "agd", "o2", "co2"]):
        return "Kimia Klinik"
    if any(k in n for k in ["crp", "prokalsitonin", "feritin", "ldh", "asam urat", "d-dimer", "il-6", "il6",
                              "procalcitonin", "crp-ku", "hs-crp", "marker", "tumor marker", "procalc", "ferrit"]):
        return "Inflamasi/Marker"
    if any(k in n for k in ["urin", "urine", "feces", "faeces", "tinja", "sputum", "dahak", "swab", "bta",
                              "pulasan", "kultur", "biakan", "gram", "eksudat", "transudat", "sedimen", "urinalisis",
                              "analisa faeces", "makroskopik", "mikroskopik", "cairan tubuh", "sitologi cairan",
                              "lekosit", "bj", "berat jenis", "glukose", "urobilinogen", "keton", "nitrit", "blood",
                              "vit", "konsistensi", "lendir", "amoeba", "cacing", "telur", "kristal", "epitel",
                              "silinder", "bakteri", "warna", "kejernihan", "ph urin", "protein urin", "reduksi",
                              "metode pemeriksaan", "jenis spesimen"]):
        return "Urin/Feces/Lainnya"
    return "Lainnya"


DEBUG_LOG = []
@app.get("/api/debug")
def api_debug(request: Request):
    # return last debug entries (manual test hook)
    return JSONResponse({"ok": True, "log": DEBUG_LOG[-10:]})

# ============================================================================
# API v2 — Optimized endpoints for /testing2
# Improvements over v1:
#   1. AUTH-FIRST (no cache-before-auth bypass) — cache key includes user.
#   2. Request timeout guard so a hung SIMRS upstream can't hang uvicorn.
#   3. Lab index: reduced keyword set (113 -> 30) for ~3-4x fewer requests.
#   4. Lab special: all sub-chains (PA/Rad/BMP/LCS/Immuno/Kultur) run in
#      PARALLEL instead of sequential (27.6s -> ~6-8s).
#   5. Detail: konsul jawaban fetch in parallel.
# ============================================================================
from functools import partial

def require_auth2(request: Request):
    """Auth check FIRST. Returns (simrs_session, user). Raises 401 if no session."""
    sid = request.cookies.get("sid")
    if not sid or sid not in SESSIONS:
        raise HTTPException(status_code=401, detail="Not authenticated")
    rec = SESSIONS[sid]
    if (time.time() - rec.get("ts", time.time())) > SESSION_TTL:
        SESSIONS.pop(sid, None)
        raise HTTPException(status_code=401, detail="Session expired")
    return rec["simrs_session"], rec["user"]

def cache_get2(scope: tuple):
    v = _cache.get(scope)
    if v and (time.time() - v["ts"]) < CACHE_TTL:
        return v["data"]
    _cache.pop(scope, None)
    return None
def cache_set2(scope: tuple, data):
    _cache[scope] = {"ts": time.time(), "data": data}

# Outbound concurrency caps: user-facing traffic vs background prewarm.
# (Measured: 2-3 concurrent = solo speed; ~84 concurrent = 3-4x slowdown.)
import threading as _threading
SEM_U = _threading.BoundedSemaphore(12)    # user-facing /api2 calls
SEM_PW = _threading.BoundedSemaphore(3)    # background prewarm (low priority)

def _sem_get(sess, url, params=None, timeout=20):
    """_simrs_get + user semaphore: blocks until a slot is free."""
    with SEM_U:
        return _simrs_get(sess, url, params, timeout)

def _pw_get(sess, url, params=None, timeout=30):
    """_simrs_get + prewarm semaphore: gentle background pacing."""
    with SEM_PW:
        return _simrs_get(sess, url, params, timeout)

# High-value keywords. Measured greedy set-cover on 2 patients (RM 1508615: 84
# panels, RM 1151276 heavy: 119 panels): these 15 cover 100% of panels found by
# the old 38-keyword list; the dropped ones returned 0 rows or exact duplicates
# while being the slowest searches.
LAB_KW_V2 = [
    "hematologi","kimia","darah","elektrolit","ureum","kreatinin","albumin","crp",
    "urin","faeces","kultur","biakan","ana","leukimia","pemeriksaan",
]

# L2 disk cache for lab index/special (90 min, per-NORM, shared across users):
# repeat RM opens are instant; Reload button passes fresh=1 to bypass.
DISK_CACHE_DIR = BASE_DIR / "cache2"
DISK_CACHE_TTL = 5400

def disk_cache_get(key):
    p = DISK_CACHE_DIR / f"{key}.json"
    try:
        if p.exists() and (time.time() - p.stat().st_mtime) < DISK_CACHE_TTL:
            return json.loads(p.read_text())
    except Exception:
        pass
    return None

def disk_cache_set(key, data):
    try:
        DISK_CACHE_DIR.mkdir(exist_ok=True)
        p = DISK_CACHE_DIR / f"{key}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, p)
    except Exception:
        pass

# --- core lab fetchers (shared by endpoint + prewarm) ---

def _lab_index_core(sess, n, gget):
    """15-keyword parallel tindakanmedis search -> panel list. gget(sess,url,params,timeout)->(data,err)."""
    import concurrent.futures as cf
    lock = _threading.Lock()
    seen = set()
    def fetch_kw(kw):
        try:
            data, _ = gget(sess, "/webservice/layanan/tindakanmedis",
                           {"NORM": n, "NAMA": kw, "limit": 50}, timeout=25)
            out = []
            for t in data:
                kj = t.get("KUNJUNGAN")
                if kj:
                    with lock:
                        if kj in seen: continue
                        seen.add(kj)
                    out.append({"kunjungan_lab": kj, "tanggal": (t.get("TANGGAL","") or "")[:19],
                                "jenis": t.get("TINDAKAN_DESKRIPSI") or "Lab", "n_items": 0})
            return out
        except Exception:
            return []
    panels = []
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for res in ex.map(fetch_kw, LAB_KW_V2):
            panels.extend(res)
    panels.sort(key=lambda p: p["tanggal"], reverse=True)
    return {"ok": True, "total": len(panels), "panels": panels}

def _lab_special_core(sess, n, gget):
    """PA/Rad/BMP/LCS/Immuno/Kultur chains run in PARALLEL."""
    import concurrent.futures as cf
    results = []
    def search_tindakan(nama, jt=8):
        try:
            data, _ = gget(sess, "/webservice/layanan/tindakanmedis",
                           {"NORM": n, "JENIS_TINDAKAN": jt, "STATUS": 1, "NAMA": nama, "limit": 50}, timeout=25)
            return [(t.get("KUNJUNGAN"), t.get("ID"), t.get("TANGGAL"), t.get("TINDAKAN_DESKRIPSI") or nama)
                    for t in data]
        except Exception:
            return []
    def get(url, params, timeout=25):
        data, _ = gget(sess, f"/webservice{url}", params, timeout)
        return data

    def fetch_pa():
        rk = search_tindakan("histopat")+search_tindakan("sitolog")+search_tindakan("anatomi")+search_tindakan("ihc")+search_tindakan("Imunohistokimia")
        seen=set(); uniq=[]
        for kunj,tid,tgl,desc in rk:
            if kunj and kunj not in seen:
                seen.add(kunj); uniq.append((kunj,tid,tgl,desc))
        out=[]
        def _one(kunj,tid,tgl,desc):
            for jp in (1,2,3):
                pa = get("/layanan/laboratorium/pa/hasil", {"KUNJUNGAN": kunj, "JENIS_PEMERIKSAAN": jp})
                if pa:
                    res=[]
                    for d in pa:
                        hasil = strip_html(d.get("KESIMPULAN") or d.get("HASIL") or d.get("KESAN") or "")
                        detail={}
                        for kk in ["MAKROSKOPIK","MIKROSKOPIK","KESIMPULAN","HASIL","KESAN","JARINGAN"]:
                            if d.get(kk): detail[kk]=strip_html(d[kk])
                        if hasil or detail:
                            jn="PA" if jp==1 else "LCS" if jp==2 else "IHC"
                            res.append({"tanggal":(d.get("TANGGAL") or tgl or "")[:10],"jenis":jn,
                                "jaringan":desc or d.get("JARINGAN") or jn,"kesimpulan":hasil,"detail":detail})
                    return res
            return None
        with cf.ThreadPoolExecutor(max_workers=4) as ex:
            for r in ex.map(lambda a: _one(*a), uniq):
                if r: out.extend(r)
        return out

    def fetch_rad():
        rad_map={}
        try:
            for kw in ["Radiografi","CT Scan","USG","MRI"]:
                data, _ = gget(sess, "/webservice/layanan/tindakanmedis",
                               {"NORM": n, "NAMA": kw, "limit": 50}, timeout=25)
                for t in data:
                    rad_map[str(t.get("ID",""))]=t.get("TINDAKAN_DESKRIPSI") or kw
        except Exception:
            pass
        rad = get("/layanan/hasilrad", {"NORM": n, "limit": 50})
        out=[]
        for d in rad:
            det={}
            kl=strip_html(d.get("KLINIS") or ""); hs=strip_html(d.get("HASIL") or "")
            ks=strip_html(d.get("KESAN") or ""); us=strip_html(d.get("USUL") or ""); bt=strip_html(d.get("BTK") or "")
            tm = d.get("TINDAKAN_MEDIS","")
            exam=rad_map.get(str(tm).strip() if isinstance(tm,str) else str(tm), "")
            if kl: det["Indikasi"]=kl
            if hs: det["Hasil"]=hs
            if ks: det["Kesan"]=ks
            if us: det["Usulan"]=us
            if bt: det["Dibaca oleh"]=bt
            if det:
                out.append({"tanggal":(d.get("TANGGAL","") or "")[:10],"jenis":"Radiologi",
                    "jaringan":exam or kl or "Radiologi","kesimpulan":ks,"detail":det})
        return out

    def fetch_bmp():
        rk = search_tindakan("sumsum")
        out=[]
        for kunj,tid,tgl,desc in rk:
            ev = get("/layanan/hasil/evaluasisst", {"KUNJUNGAN": kunj, "limit": 25})
            if ev:
                b=ev[0]; det={}
                for k in ["SELULARITAS","ERITROPOIETIK","LEUKOPOIETIK","TROMBOPOIETIK","SEL_PLASMA","MITOSIS","ME_RATIO"]:
                    if b.get(k): det[k]=strip_html(b[k])
                ks=strip_html(b.get("KESAN") or b.get("KESIMPULAN") or "")
                if ks: det["Kesan"]=ks
                if det:
                    out.append({"tanggal":(tgl or "")[:10],"jenis":"BMP","jaringan":desc or "Sumsum Tulang","kesimpulan":ks,"detail":det})
        return out

    def fetch_lcs():
        rk = search_tindakan("LCS")+search_tindakan("cairan tubuh")+search_tindakan("Efusi")+search_tindakan("Ascites")
        out=[]
        for kunj,tid,tgl,desc in rk:
            lcs = get("/layanan/laboratorium/pa/hasil", {"KUNJUNGAN": kunj, "JENIS_PEMERIKSAAN": 2})
            for d in lcs:
                hasil=strip_html(d.get("KESIMPULAN") or d.get("HASIL") or d.get("KESAN") or "")
                det={}
                for k in ["MAKROSKOPIK","MIKROSKOPIK","KESIMPULAN","HASIL","KESAN"]:
                    if d.get(k): det[k]=strip_html(d[k])
                if hasil or det:
                    out.append({"tanggal":(d.get("TANGGAL") or tgl or "")[:10],"jenis":"LCS","jaringan":desc or "LCS","kesimpulan":hasil,"detail":det})
        return out

    def fetch_immuno():
        rk = search_tindakan("LEUKIMIA")+search_tindakan("IMUNOFENOTIP")
        out=[]
        for kunj,tid,tgl,desc in rk:
            if not tid: continue
            hl = get("/layanan/hasillab", {"TINDAKAN_MEDIS": tid})
            params=[f"{row.get('REFERENSI',{}).get('PARAMETER_TINDAKAN',{}).get('PARAMETER') or row.get('PARAMETER')}: {row.get('HASIL')} {row.get('SATUAN','')}".strip() for row in hl if row.get("HASIL")]
            cat = get("/layanan/catatanhasillab", {"KUNJUNGAN": kunj})
            cat_t = cat[0].get("CATATAN","") if cat else ""
            det={}
            if params: det["Hasil"]=" | ".join(params)
            if cat_t: det["Catatan"]=cat_t
            if det:
                out.append({"tanggal":(tgl or "")[:10],"jenis":"Immuno","jaringan":desc or "Immunofenotyping",
                    "kesimpulan":det.get("Hasil","")[:100],"detail":det})
        return out

    def fetch_kultur():
        rk = search_tindakan("KULTUR")+search_tindakan("BIAKAN")+search_tindakan("SENSITIVITAS")
        out=[]
        for kunj,tid,tgl,desc in rk:
            if not tid: continue
            hl = get("/layanan/hasillab", {"TINDAKAN_MEDIS": tid})
            items=[]
            for row in hl:
                nm = row.get('REFERENSI',{}).get('PARAMETER_TINDAKAN',{}).get('PARAMETER') or row.get('PARAMETER')
                vl = row.get('HASIL')
                if nm and vl: items.append(f"• {nm}: {vl}")
            if items:
                out.append({"tanggal":(tgl or "")[:10],"jenis":"Kultur","jaringan":desc,"kesimpulan":items[0] if items else "",
                    "detail":{"Hasil":"\n".join(items)}})
        return out

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(fn) for fn in [fetch_pa, fetch_rad, fetch_bmp, fetch_lcs, fetch_immuno, fetch_kultur]]
        for f in futs:
            try: results.extend(f.result())
            except Exception: pass
    results.sort(key=lambda r: r.get("tanggal") or "", reverse=True)
    return {"ok": True, "total": len(results), "special": results}

# --- background prewarm: fills disk cache for ranap patients (low priority) ---
_PREWARM_INFLIGHT = set()
_PREWARM_LOCK = _threading.Lock()

def _prewarm_norms(sess, norms):
    for n in norms:
        with _PREWARM_LOCK:
            if n in _PREWARM_INFLIGHT:
                continue
            _PREWARM_INFLIGHT.add(n)
        try:
            if disk_cache_get(f"labidx_{n}") is None:
                res = _lab_index_core(sess, n, _pw_get)
                disk_cache_set(f"labidx_{n}", res)
            if disk_cache_get(f"labsp_{n}") is None:
                res = _lab_special_core(sess, n, _pw_get)
                disk_cache_set(f"labsp_{n}", res)
        except Exception:
            pass
        finally:
            with _PREWARM_LOCK:
                _PREWARM_INFLIGHT.discard(n)

def kick_prewarm(sess, norms):
    norms = [str(x) for x in dict.fromkeys(norms) if x]
    if not norms:
        return
    _threading.Thread(target=_prewarm_norms, args=(sess, norms), daemon=True).start()

def _simrs_get(sess, url, params=None, timeout=20):
    BASE = "http://127.0.0.1:8080"
    try:
        r = sess.get(f"{BASE}{url}", params=params, timeout=timeout)
    except Exception as e:
        return [], f"request failed: {str(e)[:120]}"
    ct = r.headers.get("content-type", "")
    if "json" in ct:
        try:
            j = r.json()
        except Exception:
            return [], "invalid JSON"
        if j.get("success"):
            return j.get("data", []), None
        return [], j.get("message", "non-success")
    return [], "non-JSON response"

@app.get("/testing2", response_class=HTMLResponse)
def testing2_page(request: Request):
    sid = request.cookies.get("sid")
    if not sid or sid not in SESSIONS:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "testing2.html", {"user": SESSIONS[sid]["user"]})

@app.get("/cppt", response_class=HTMLResponse)
def cppt_page(request: Request):
    sid = request.cookies.get("sid")
    if not sid or sid not in SESSIONS:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "testing2.html", {"user": SESSIONS[sid]["user"]})

@app.get("/ruangan", response_class=HTMLResponse)
def ruangan_page(request: Request):
    sid = request.cookies.get("sid")
    if not sid or sid not in SESSIONS:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "ruangan.html", {"user": SESSIONS[sid]["user"]})

@app.get("/laporan-lab", response_class=HTMLResponse)
def laporan_lab_page(request: Request):
    sid = request.cookies.get("sid")
    if not sid or sid not in SESSIONS:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "laporan.html", {"user": SESSIONS[sid]["user"]})

@app.get("/laporan-lab/{job_id}", response_class=HTMLResponse)
def laporan_lab_view(request: Request, job_id: str):
    import re as _r
    if not _r.fullmatch(r"[0-9a-f-]{4,16}", job_id):
        return RedirectResponse("/laporan-lab", status_code=302)
    sid = request.cookies.get("sid")
    if not sid or sid not in SESSIONS:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "laporan.html", {"user": SESSIONS[sid]["user"], "job_id": job_id})

@app.get("/api2/pasien")
def api2_pasien(request: Request, norm: str = ""):
    if not norm:
        return JSONResponse({"ok": False, "error": "NORM kosong"}, status_code=400)
    sess, user = require_auth2(request)          # auth FIRST
    cached = cache_get2(("pasien2", norm, user))
    if cached:
        return JSONResponse(cached)
    data, err = _sem_get(sess, f"/webservice/general/pasien/{norm}")
    if not data:
        return JSONResponse({"ok": False, "error": err or "Pasien tidak ditemukan"}, status_code=404)
    p = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    hp = ""
    for kt in (p.get("KONTAK", []) or []):
        if isinstance(kt, dict) and (kt.get("JENIS") in (3, "3") or "081" in str(kt.get("NOMOR", ""))):
            hp = kt.get("NOMOR", ""); break
    out = {"ok": True, "norm": norm, "nama": p.get("NAMA", ""),
           "tgl_lahir": (p.get("TANGGAL_LAHIR", "") or "")[:10],
           "jk": "L" if p.get("JENIS_KELAMIN") == 1 else "P" if p.get("JENIS_KELAMIN") == 2 else "",
           "alamat": p.get("ALAMAT", ""), "no_hp": hp}
    cache_set2(("pasien2", norm, user), out)
    # Background prewarm lab index+special for ad-hoc RM search: when the user
    # types a NORM directly into the search box, this fires before they click a
    # kunjungan, so the lab panels are already cached by then.
    try:
        kick_prewarm(sess, [norm])
    except Exception:
        pass
    return JSONResponse(out)

@app.get("/api2/kunjungan")
def api2_kunjungan(request: Request, norm: str = ""):
    if not norm:
        return JSONResponse({"ok": False, "error": "NORM kosong"}, status_code=400)
    sess, user = require_auth2(request)
    cached = cache_get2(("kunj2", norm, user))
    if cached:
        return JSONResponse(cached)
    import json as _json
    data, _ = _sem_get(sess, "/webservice/pendaftaran/kunjungan", params={
        "NORM": norm, "STATUS": "[1,2]", "limit": 100,
        "REFERENSI": _json.dumps({"Ruangan": {"COLUMNS": ["DESKRIPSI", "JENIS_KUNJUNGAN"]},
                                  "DPJP": True,
                                  "RuangKamarTidur": True})}, timeout=30)
    out = []
    for k in data:
        ref = k.get("REFERENSI", {})
        dpjp = ref.get("DPJP", {})
        ruangan = (ref.get("RUANGAN", {}) or {}).get("DESKRIPSI", "")
        rkt = ref.get("RUANG_KAMAR_TIDUR", {}) or {}
        bed = rkt.get("TEMPAT_TIDUR") or rkt.get("NOMOR") or ""
        rkt_ref = rkt.get("REFERENSI", {}) or {}
        km = (rkt_ref.get("RUANG_KAMAR", {}) or {}).get("KAMAR", "")
        kamar = f"{km} {bed}".strip()
        dpjp_name = dpjp.get("NAMA", "") if isinstance(dpjp, dict) else ""
        out.append({"kunjungan": k.get("NOMOR", ""), "nopen": k.get("NOPEN", ""),
                    "masuk": (k.get("MASUK", "") or "")[:19],
                    "keluar": (k.get("KELUAR", "") or "")[:19] if k.get("KELUAR") else "",
                    "ruangan": ruangan, "kamar": kamar,
                    "dpjp": dpjp_name, "dpjp_aktif": dpjp_name,
                    "masuk_aktif": (k.get("MASUK", "") or "")[:19]})
    out.sort(key=lambda x: x["masuk"], reverse=True)
    res = {"ok": True, "norm": norm, "total": len(out), "kunjungan": out}
    cache_set2(("kunj2", norm, user), res)
    return JSONResponse(res)

@app.get("/api2/ranap/aktif")
def api2_ranap_aktif(request: Request, limit: int = 100, start: int = 0):
    sess, user = require_auth2(request)
    cached = cache_get2(("ranap2", user, start, limit))
    if cached:
        return JSONResponse(cached)
    BASE = "http://127.0.0.1:8080"
    import json as _json, concurrent.futures as cf
    REFERENSI = _json.dumps({"Ruangan": {"COLUMNS":["DESKRIPSI","JENIS_KUNJUNGAN"],"REFERENSI":{"Referensi":True}},
        "Pendaftaran": True, "Referensi": True, "RuangKamarTidur": True, "DPJP": True, "Pasien": True, "Mutasi": True,
        "AntrianRuangan": {"COLUMNS":["ID","POS","NOMOR"]}})
    try:
        jdata, jerr = _sem_get(sess, "/webservice/pendaftaran/kunjungan",
                     {"STATUS": 1, "REFERENSI": REFERENSI, "MY_PASIEN": 1, "page":1,"start":start,"limit":limit}, timeout=60)
        if jerr and not jdata:
            return JSONResponse({"ok": False, "error": "SIMRS non-success: " + str(jerr)[:120]}, status_code=500)
        j = {"data": jdata, "total": len(jdata)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)
    out = []
    norms_missing = []
    for k in j.get("data", []):
        ref = k.get("REFERENSI", {})
        ruangan = ref.get("RUANGAN", {}) or {}
        rkt = ref.get("RUANG_KAMAR_TIDUR", {}) or {}
        pd = ref.get("PENDAFTARAN", {}) or {}
        dpjp = ref.get("DPJP", {}) or {}
        rkt_ref = rkt.get("REFERENSI", {}) or {}
        kamar = (rkt_ref.get("RUANG_KAMAR",{}) or {}).get("KAMAR","")
        bed = rkt.get("TEMPAT_TIDUR") or rkt.get("NOMOR") or ""
        # NAMA di level-3: REFERENSI.PENDAFTARAN.REFERENSI.PASIEN (verified via exp_ranap_path)
        pd_ref = pd.get("REFERENSI", {}) or {}
        pasien = (ref.get("PASIEN", {}) or pd.get("PASIEN", {}) or
                  pd_ref.get("PASIEN", {}) or {})
        nm = str(pd.get("NORM", "") or pasien.get("NORM", "") or
                 pd_ref.get("PASIEN", {}).get("NORM", ""))
        hp = ""
        for kt in (pasien.get("KONTAK",[]) or []):
            if isinstance(kt,dict) and (kt.get("JENIS") in (3,"3") or "081" in str(kt.get("NOMOR",""))):
                hp=kt.get("NOMOR",""); break
        out.append({"kunjungan": k.get("NOMOR",""), "nopen": k.get("NOPEN",""), "norm": nm,
            "nama": pasien.get("NAMA","") or "",
            "jk": ("L" if pasien.get("JENIS_KELAMIN")==1 else "P" if pasien.get("JENIS_KELAMIN")==2 else ""),
            "tgl_lahir": (pasien.get("TANGGAL_LAHIR","") or "")[:10], "no_hp": hp,
            "ruangan": ruangan.get("DESKRIPSI",""), "ruangan_id": k.get("RUANGAN",""),
            "kamar": f"{kamar} {bed}".strip(), "kamar_id": k.get("RUANG_KAMAR_TIDUR",""),
            "masuk": (k.get("MASUK","") or "")[:19],
            "dpjp": dpjp.get("NAMA","") if isinstance(dpjp,dict) else "",
            "dx": (ref.get("Mutasi",[{}])[0] if isinstance(ref.get("Mutasi"),list) and ref.get("Mutasi") else {}).get("DIAGNOSA") or pd.get("DIAGNOSA","")})
    if norms_missing:
        def _gp(n):
            try:
                d, gerr = _sem_get(sess, f"/webservice/general/pasien/{n}", None, timeout=15)
                if d:
                    d = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else {})
                    h=""
                    for kt in (d.get("KONTAK",[]) or []):
                        if isinstance(kt,dict) and (kt.get("JENIS") in (3,"3") or "081" in str(kt.get("NOMOR",""))):
                            h=kt.get("NOMOR","");break
                    return n,{"nama":d.get("NAMA",""),"jk":("L" if d.get("JENIS_KELAMIN")==1 else "P" if d.get("JENIS_KELAMIN")==2 else ""),"tgl_lahir":(d.get("TANGGAL_LAHIR","") or "")[:10],"no_hp":h}
            except: pass
            return n,None
        # rebuild norms_missing from out (those with no nama)
        norms_missing=[i["norm"] for i in out if not i["nama"] and i["norm"]]
        if norms_missing:
            with cf.ThreadPoolExecutor(max_workers=6) as ex:
                pm=dict(ex.map(_gp, set(norms_missing)))
            for i in out:
                if not i["nama"] and i["norm"] in pm and pm[i["norm"]]:
                    i.update(pm[i["norm"]])
    out.sort(key=lambda x:(x.get("ruangan") or "", x.get("kamar") or "", x.get("nama") or ""))
    res={"ok":True,"total":j.get("total",len(out)),"data":out}
    _cache[("ranap2",user,start,limit)]={"ts":time.time()+120,"data":res}
    # Background-prewarm lab index+special disk cache for all listed patients so
    # that clicking an RM later hits a warm cache (~instant open).
    try:
        kick_prewarm(sess, [i["norm"] for i in out])
    except Exception:
        pass
    return JSONResponse(res)

@app.get("/api2/cppt")
def api2_cppt(request: Request, kunjungan: str = "", norm: str = ""):
    if not kunjungan and not norm:
        return JSONResponse({"ok": False, "error": "NORM atau KUNJUNGAN kosong"}, status_code=400)
    sess, user = require_auth2(request)
    cached = cache_get2(("cppt2", kunjungan, norm, user))
    if cached:
        return JSONResponse(cached)
    BASE = "http://127.0.0.1:8080"
    if kunjungan:
        params = {"KUNJUNGAN": kunjungan, "limit": 100}
    else:
        params = {"HISTORY": 1, "NORM": norm, "limit": 3000,
                  "sort": json.dumps({"property":"TANGGAL","direction":"DESC"})}
    rows, cppt_err = _sem_get(sess, "/webservice/medicalrecord/cppt", params, timeout=30)
    out = []
    for c in rows:
        ref = c.get("REFERENSI", {})
        tm = ref.get("TENAGA_MEDIS", {})
        penulis = tm.get("NAMA", "(tanpa nama)")
        jenis = ref.get("JENIS", {}).get("DESKRIPSI", "")
        out.append({"tanggal": c.get("TANGGAL",""), "kunjungan": c.get("KUNJUNGAN",""),
            "penulis": f"{penulis} ({jenis})" if jenis else penulis,
            "subjektif": strip_html(c.get("SUBYEKTIF","")), "objektif": strip_html(c.get("OBYEKTIF","")),
            "assesment": strip_html(c.get("ASSESMENT","")), "terapi": strip_html(c.get("INSTRUKSI","")),
            "planning": strip_html(c.get("PLANNING",""))})
    res = {"ok": True, "total": len(out), "cppt": out}
    cache_set2(("cppt2", kunjungan, norm, user), res)
    return JSONResponse(res)

@app.get("/api2/detail")
def api2_detail(request: Request, kunjungan: str = "", nopen: str = "", norm: str = "", fresh: int = 0):
    if not kunjungan:
        return JSONResponse({"ok": False, "error": "KUNJUNGAN kosong"}, status_code=400)
    sess, user = require_auth2(request)
    # 10-min disk cache per kunjungan: repeat opens instant; fresh=1 bypasses
    if not fresh:
        cached = disk_cache_get(f"detail_{kunjungan}")
        if cached:
            return JSONResponse(cached)
    BASE = "http://127.0.0.1:8080"
    import concurrent.futures as cf
    def get(url, params, timeout=25):
        data, _ = _sem_get(sess, f"/webservice{url}", params=params, timeout=timeout)
        return data
    # parallel fetch anamnesis, fisik, terapi, diagnosis, icd, keluhan_utama
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        f_ku = ex.submit(get, "/medicalrecord/anamnesis/keluhan/utama", {"KUNJUNGAN": kunjungan, "limit": 25})
        f_anam = ex.submit(get, "/medicalrecord/anamnesis", {"KUNJUNGAN": kunjungan, "limit": 25})
        f_fisik = ex.submit(get, "/medicalrecord/pemeriksaan/fisik", {"KUNJUNGAN": kunjungan, "limit": 25})
        f_terapi = ex.submit(get, "/medicalrecord/perencanaan/rencanaterapi", {"KUNJUNGAN": kunjungan, "limit": 25})
        f_diag = ex.submit(get, "/medicalrecord/penilaian/diagnosis", {"KUNJUNGAN": kunjungan, "limit": 25})
        f_icd = ex.submit(get, "/medicalrecord/icd10", {"NOPEN": nopen, "STATUS": 1, "limit": 100}) if nopen else None
        keluhan = f_ku.result()
        anam = f_anam.result()
        fisik = f_fisik.result()
        terapi = f_terapi.result()
        pen = f_diag.result()
        icd = f_icd.result() if f_icd else []
    def ft(d, f):
        if isinstance(d,list) and d: return strip_html(d[0].get(f,""))
        if isinstance(d,dict): return strip_html(d.get(f,""))
        return ""
    konsul_raw = []
    if norm:
        konsul_raw, _ = _sem_get(sess, "/webservice/pendaftaran/konsul", {"NORM": norm, "HISTORY": 1, "limit": 25})
    konsul = []
    if konsul_raw:
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            def _jawab(k):
                kn = k.get("NOMOR","")
                jw, _ = _sem_get(sess, "/webservice/pendaftaran/jawabankonsul", {"KONSUL_NOMOR": kn, "limit": 5})
                jaw = jw[0] if (isinstance(jw,list) and jw) else {}
                return {"tanggal": k.get("TANGGAL"),
                    "permintaan": strip_html(k.get("ALASAN","")),
                    "instruksi": strip_html(k.get("PERMINTAAN_TINDAKAN","")),
                    "tujuan": ((k.get("REFERENSI",{}) or {}).get("TUJUAN",{}) or {}).get("DESKRIPSI",""),
                    "dokter_tujuan": ((k.get("REFERENSI",{}) or {}).get("DOKTER_TUJUAN",{}) or {}).get("NAMA",""),
                    "status": ((k.get("REFERENSI",{}) or {}).get("STATUS",{}) or {}).get("DESKRIPSI",""),
                    "jawaban": strip_html(jaw.get("JAWABAN","")),
                    "anjuran": strip_html(jaw.get("ANJURAN","")),
                    "jawaban_tanggal": (jaw.get("TANGGAL","") or "")[:16],
                    "jawaban_dokter": (((jaw.get("REFERENSI",{}) or {}).get("DOKTER",{}) or {}).get("NAMA","")),
                    "layak_operasi": jaw.get("STATUS_LAYAK_OPERASI"),
                    "rawat_bersama": jaw.get("STATUS_RAWAT_BERSAMA")}
            for r in ex.map(_jawab, konsul_raw):
                konsul.append(r)
    konsul.sort(key=lambda k:k.get("tanggal") or "",reverse=True)
    res = {"ok": True,
        "keluhan_utama": ft(keluhan,"DESKRIPSI"),
        "riwayat_penyakit_sekarang": ft(anam,"RPS") or ft(anam,"DESKRIPSI"),
        "riwayat": ft(anam,"RPT") or ft(anam,"RPK"),
        "pemeriksaan_fisik": ft(fisik,"DESKRIPSI"),
        "rencana_terapi": "\n".join(strip_html(t.get("DESKRIPSI","")) for t in terapi if t.get("DESKRIPSI")),
        "penilaian_diagnosis": "\n".join(strip_html(d.get("DIAGNOSIS","")) for d in pen if d.get("DIAGNOSIS")),
        "icd10": [{"kode":i.get("KODE"),"diagnosa":i.get("DIAGNOSA"),"utama":i.get("UTAMA")} for i in icd] if isinstance(icd,list) else [],
        "konsul": konsul}
    disk_cache_set(f"detail_{kunjungan}", res)
    return JSONResponse(res)

@app.get("/api2/lab/index")
def api2_lab_index(request: Request, norm: str = "", kunjungan: str = "", fresh: int = 0):
    sess, user = require_auth2(request)
    if not norm and not kunjungan:
        return JSONResponse({"ok": False, "error": "NORM atau KUNJUNGAN kosong"}, status_code=400)
    n = norm or kunjungan
    # 10-min memory cache per user+norm + 90-min disk cache per norm:
    # reopening the same RM is instant; fresh=1 bypasses
    if not fresh:
        cached = cache_get2(("labidx2", n, user))
        if cached:
            return JSONResponse(cached)
        cached = disk_cache_get(f"labidx_{n}")
        if cached:
            _cache[("labidx2", n, user)] = {"ts": time.time(), "data": cached}
            return JSONResponse(cached)
    res = _lab_index_core(sess, n, _sem_get)
    _cache[("labidx2", n, user)] = {"ts": time.time(), "data": res}
    disk_cache_set(f"labidx_{n}", res)
    return JSONResponse(res)

@app.get("/api2/lab/detil")
def api2_lab_detil(request: Request, kunjungan_lab: str = ""):
    if not kunjungan_lab:
        return JSONResponse({"ok": False, "error": "KUNJUNGAN_LAB kosong"}, status_code=400)
    sess, user = require_auth2(request)
    data, err = _sem_get(sess, "/webservice/medicalrecord/resume/hasillab/detil",
                           {"KUNJUNGAN_LAB": kunjungan_lab}, timeout=20)
    if not data:
        return JSONResponse({"ok": False, "error": err or "Gagal fetch detail lab"}, status_code=200)
    items = []
    for d in data:
        ref = d.get("REFERENSI", {})
        param = ref.get("PARAMETER_TINDAKAN", {}) or ref.get("LABORATORIUM", {}) or {}
        name = d.get("PARAMETER") or param.get("PARAMETER") or param.get("NAMA") or d.get("NAMA")
        val = d.get("HASIL"); sat = d.get("SATUAN") or ""
        normv = d.get("NORMAL") or d.get("NILAI_NORMAL") or param.get("NILAI_RUJUKAN") or ""
        if name and val is not None and str(val).strip():
            items.append({"nama": str(name).strip(), "hasil": str(val).strip(),
                          "satuan": str(sat).strip(), "normal": str(normv).strip(),
                          "jenis": classify_lab(str(name.strip()) if name else "")})
    return JSONResponse({"ok": True, "kunjungan_lab": kunjungan_lab, "items": items})

@app.get("/api2/lab/special")
def api2_lab_special(request: Request, norm: str = "", kunjungan: str = "", fresh: int = 0):
    sess, user = require_auth2(request)
    if not norm and not kunjungan:
        return JSONResponse({"ok": False, "error": "NORM atau KUNJUNGAN kosong"}, status_code=400)
    n_pre = norm or kunjungan
    if not fresh:
        cached = cache_get2(("labsp2", n_pre, user))
        if cached:
            return JSONResponse(cached)
        cached = disk_cache_get(f"labsp_{n_pre}")
        if cached:
            _cache[("labsp2", n_pre, user)] = {"ts": time.time(), "data": cached}
            return JSONResponse(cached)
    res = _lab_special_core(sess, n_pre, _sem_get)
    _cache[("labsp2", n_pre, user)] = {"ts": time.time(), "data": res}
    disk_cache_set(f"labsp_{n_pre}", res)
    return JSONResponse(res)

# ---------- Ruangan (cek pasien per ruangan) ----------
def _parse_ranap_row(k: dict) -> dict:
    """Parse one kunjungan row (REFERENSI layout, HAR-verified) -> patient dict.
    Field-for-field identical to api2_ranap_aktif output so the frontend can render both."""
    ref = k.get("REFERENSI", {}) or {}
    ruangan = ref.get("RUANGAN", {}) or {}
    rkt = ref.get("RUANG_KAMAR_TIDUR", {}) or {}
    pd = ref.get("PENDAFTARAN", {}) or {}
    dpjp = ref.get("DPJP", {}) or {}
    rkt_ref = rkt.get("REFERENSI", {}) or {}
    kamar = (rkt_ref.get("RUANG_KAMAR", {}) or {}).get("KAMAR", "")
    bed = rkt.get("TEMPAT_TIDUR") or rkt.get("NOMOR") or ""
    pd_ref = pd.get("REFERENSI", {}) or {}
    pasien = (ref.get("PASIEN", {}) or pd.get("PASIEN", {}) or
              pd_ref.get("PASIEN", {}) or {})
    nm = str(pd.get("NORM", "") or pasien.get("NORM", "") or
             pd_ref.get("PASIEN", {}).get("NORM", ""))
    hp = ""
    for kt in (pasien.get("KONTAK", []) or []):
        if isinstance(kt, dict) and (kt.get("JENIS") in (3, "3") or "081" in str(kt.get("NOMOR", ""))):
            hp = kt.get("NOMOR", ""); break
    dx = (ref.get("Mutasi", [{}])[0] if isinstance(ref.get("Mutasi"), list) and ref.get("Mutasi")
          else {}).get("DIAGNOSA") or pd.get("DIAGNOSA", "")
    return {"kunjungan": k.get("NOMOR", ""), "nopen": k.get("NOPEN", ""), "norm": nm,
            "nama": pasien.get("NAMA", "") or "",
            "jk": ("L" if pasien.get("JENIS_KELAMIN") == 1 else "P" if pasien.get("JENIS_KELAMIN") == 2 else ""),
            "tgl_lahir": (pasien.get("TANGGAL_LAHIR", "") or "")[:10], "no_hp": hp,
            "ruangan": ruangan.get("DESKRIPSI", ""), "ruangan_id": k.get("RUANGAN", ""),
            "kamar": f"{kamar} {bed}".strip(), "kamar_id": k.get("RUANG_KAMAR_TIDUR", ""),
            "masuk": (k.get("MASUK", "") or "")[:19],
            "dpjp": dpjp.get("NAMA", "") if isinstance(dpjp, dict) else "",
            "dx": dx}

def _fetch_ruangan_master(sess) -> list:
    """Enumerate all active ruangan via one cheap kunjungan call (REFERENSI=Ruangan only,
    limit=1000). HAR/probe-verified: ~0.7s, ~376KB, covers all occupied rooms."""
    import json as _json
    REF_MIN = _json.dumps({"Ruangan": {"COLUMNS": ["DESKRIPSI", "JENIS_KUNJUNGAN"]}})
    jdata, jerr = _sem_get(sess, "/webservice/pendaftaran/kunjungan",
                           {"STATUS": 1, "REFERENSI": REF_MIN, "page": 1, "start": 0, "limit": 1000},
                           timeout=60)
    if jerr and not jdata:
        raise HTTPException(status_code=500, detail="SIMRS non-success: " + str(jerr)[:120])
    rooms = {}
    for k in jdata:
        rid = k.get("RUANGAN")
        if not rid:
            continue
        rr = (k.get("REFERENSI", {}) or {}).get("RUANGAN", {}) or {}
        rooms[rid] = {"id": rid, "deskripsi": rr.get("DESKRIPSI", ""),
                      "jenis": rr.get("JENIS_KUNJUNGAN")}
    return sorted(rooms.values(), key=lambda r: (r.get("jenis") or 0, r.get("deskripsi") or ""))

@app.get("/api2/ruangan")
def api2_ruangan(request: Request):
    """Daftar ruangan aktif (ada pasien) — id + deskripsi + jenis kunjungan."""
    sess, user = require_auth2(request)
    cached = cache_get2(("ruangan_list", user))
    if cached:
        return JSONResponse(cached)
    try:
        rooms = _fetch_ruangan_master(sess)
    except HTTPException as e:
        raise e
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)
    res = {"ok": True, "total": len(rooms), "data": rooms}
    _cache[("ruangan_list", user)] = {"ts": time.time(), "data": res}
    return JSONResponse(res)

@app.get("/api2/ranap/ruangan")
def api2_ranap_ruangan(request: Request, ruangan: str = "", limit: int = 100, start: int = 0):
    """Semua pasien rawat inap aktif di satu ruangan (tanpa filter MY_PASIEN/DPJP).
    HAR-verified: endpoint kunjungan yang sama + param RUANGAN=<id>."""
    sess, user = require_auth2(request)
    if not ruangan:
        return JSONResponse({"ok": False, "error": "Param ruangan kosong"}, status_code=400)
    cached = cache_get2(("ranap_ruang", user, ruangan, start, limit))
    if cached:
        return JSONResponse(cached)
    import json as _json, concurrent.futures as cf
    REFERENSI = _json.dumps({"Ruangan": {"COLUMNS": ["DESKRIPSI", "JENIS_KUNJUNGAN"], "REFERENSI": {"Referensi": True}},
        "Pendaftaran": True, "Referensi": True, "RuangKamarTidur": True, "DPJP": True, "Pasien": True, "Mutasi": True,
        "AntrianRuangan": {"COLUMNS": ["ID", "POS", "NOMOR"]}})
    try:
        jdata, jerr = _sem_get(sess, "/webservice/pendaftaran/kunjungan",
                     {"STATUS": 1, "REFERENSI": REFERENSI, "RUANGAN": ruangan, "page": 1, "start": start, "limit": limit}, timeout=60)
        if jerr and not jdata:
            return JSONResponse({"ok": False, "error": "SIMRS non-success: " + str(jerr)[:120]}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)
    out = [_parse_ranap_row(k) for k in (jdata or [])]
    # enrich patients whose identity is missing (rare nested-layout variants)
    norms_missing = [i["norm"] for i in out if not i["nama"] and i["norm"]]
    if norms_missing:
        def _gp(n):
            try:
                d, gerr = _sem_get(sess, f"/webservice/general/pasien/{n}", None, timeout=15)
                if d:
                    d = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else {})
                    h = ""
                    for kt in (d.get("KONTAK", []) or []):
                        if isinstance(kt, dict) and (kt.get("JENIS") in (3, "3") or "081" in str(kt.get("NOMOR", ""))):
                            h = kt.get("NOMOR", ""); break
                    return n, {"nama": d.get("NAMA", ""),
                               "jk": ("L" if d.get("JENIS_KELAMIN") == 1 else "P" if d.get("JENIS_KELAMIN") == 2 else ""),
                               "tgl_lahir": (d.get("TANGGAL_LAHIR", "") or "")[:10], "no_hp": h}
            except Exception:
                pass
            return n, None
        if norms_missing:
            with cf.ThreadPoolExecutor(max_workers=6) as ex:
                pm = dict(ex.map(_gp, set(norms_missing)))
            for i in out:
                if not i["nama"] and i["norm"] in pm and pm[i["norm"]]:
                    i.update(pm[i["norm"]])
    out.sort(key=lambda x: (x.get("kamar") or "", x.get("nama") or ""))
    res = {"ok": True, "ruangan": ruangan, "total": len(out), "data": out}
    _cache[("ranap_ruang", user, ruangan, start, limit)] = {"ts": time.time(), "data": res}
    # Prewarm lab index+special disk cache for all listed patients (same as ranap/aktif)
    try:
        kick_prewarm(sess, [i["norm"] for i in out])
    except Exception:
        pass
    return JSONResponse(res)

# ---------- Laporan Lab massal (generate dari list NRM) ----------
import re as _re

def extract_nrm(text: str):
    """Ambil nomor RM unik dari teks bebas (list pasien yang di-paste user).
    Heuristik: token digit 5-12; 'RM <n>' / 'No.RM: <n>' diprioritaskan, tapi
    token digit berdiri sendiri juga dianggap kandidat (di-verify via SIMRS nanti)."""
    if not text:
        return []
    explicit = _re.findall(r"(?i)\brm[\s.:/#-]*(\d{5,12})\b", text)
    generic = _re.findall(r"(?<!\d)(\d{5,12})(?!\d)", text)
    seen, out = set(), []
    for n in explicit + [g for g in generic if g not in explicit]:
        if n not in seen:
            seen.add(n); out.append(n)
    return out

def _laporan_one(sess, norm: str) -> dict:
    """Kumpulkan identitas + kunjungan aktif + lab regular (dengan item) + special utk 1 pasien."""
    import concurrent.futures as cf
    card = {"norm": norm, "nama": "", "ruangan": "", "dpjp": "", "jk": "", "tgl_lahir": "",
            "kunjungan_aktif": "", "masuk_aktif": "", "lab_regular": [], "lab_special": [],
            "status": "error", "error": ""}
    # paralel: identitas, riwayat kunjungan (buat ruangan/DPJP aktif), lab index, lab special
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        f_pas = ex.submit(_sem_get, sess, f"/webservice/general/pasien/{norm}", None, 20)
        f_kun = ex.submit(_sem_get, sess, "/webservice/pendaftaran/kunjungan",
                          {"NORM": norm, "STATUS": "[1,2]", "limit": 100,
                           "REFERENSI": json.dumps({"Ruangan": {"COLUMNS": ["DESKRIPSI"]}, "DPJP": True,
                                                    "RuangKamarTidur": True})}, 30)
        f_lab = ex.submit(_lab_index_core, sess, norm, _sem_get)
        f_sp = ex.submit(_lab_special_core, sess, norm, _sem_get)
        pas, pas_err = f_pas.result()
        kuns, kun_err = f_kun.result()
        try: labj = f_lab.result()
        except Exception: labj = {"ok": False, "panels": []}
        try: spj = f_sp.result()
        except Exception: spj = {"ok": False, "special": []}
    # --- identitas ---
    p = pas[0] if isinstance(pas, list) and pas else (pas if isinstance(pas, dict) else {})
    if p.get("NAMA"):
        card["nama"] = p.get("NAMA", "")
        card["jk"] = "L" if p.get("JENIS_KELAMIN") == 1 else "P" if p.get("JENIS_KELAMIN") == 2 else ""
        card["tgl_lahir"] = (p.get("TANGGAL_LAHIR", "") or "")[:10]
    # --- kunjungan aktif → ruangan + DPJP ---
    aktif = None
    for k in (kuns or []):
        if not k.get("KELUAR"):
            aktif = k; break
    if aktif is None and kuns:
        aktif = sorted(kuns, key=lambda x: x.get("MASUK") or "", reverse=True)[0]
    if aktif:
        ref = aktif.get("REFERENSI", {}) or {}
        card["ruangan"] = (ref.get("RUANGAN") or {}).get("DESKRIPSI", "")
        card["dpjp"] = (ref.get("DPJP") or {}).get("NAMA", "") if isinstance(ref.get("DPJP"), dict) else ""
        card["kunjungan_aktif"] = aktif.get("NOMOR", "")
        card["masuk_aktif"] = (aktif.get("MASUK", "") or "")[:19]
        card["nopen_aktif"] = aktif.get("NOPEN", "")
        # kamar + bed dari ruang_kamar_tidur (HAR-verified layout)
        rkt = (ref.get("RUANG_KAMAR_TIDUR") or {})
        if isinstance(rkt, dict):
            rkt_ref = rkt.get("REFERENSI", {}) or {}
            kamar = (rkt_ref.get("RUANG_KAMAR", {}) or {}).get("KAMAR", "")
            bed = rkt.get("TEMPAT_TIDUR") or rkt.get("NOMOR") or ""
            card["kamar"] = f"{kamar} {bed}".strip()
    # fallback: beberapa layout nested — panggil /general/kunjungan per-nomor utk RKT kosong
    if aktif and not card.get("kamar"):
        try:
            d2, _e2 = _sem_get(sess, "/webservice/pendaftaran/kunjungan",
                               {"NOMOR": aktif.get("NOMOR"), "limit": 1,
                                "REFERENSI": json.dumps({"RuangKamarTidur": True})}, timeout=20)
            if d2:
                rkt = ((d2[0].get("REFERENSI") or {}).get("RUANG_KAMAR_TIDUR") or {})
                if isinstance(rkt, dict):
                    kr = (rkt.get("REFERENSI") or {}).get("RUANG_KAMAR") or {}
                    card["kamar"] = f"{kr.get('KAMAR','')} {rkt.get('TEMPAT_TIDUR') or rkt.get('NOMOR') or ''}".strip()
        except Exception:
            pass
    # --- lab regular: panel + items per panel (detil digabung, dedup by kunjungan_lab) ---
    panels = labj.get("panels", []) if isinstance(labj, dict) else []
    def _detil(kl):
        data, err = _sem_get(sess, "/webservice/medicalrecord/resume/hasillab/detil",
                             {"KUNJUNGAN_LAB": kl}, timeout=20)
        items = []
        for d in (data or []):
            r2 = d.get("REFERENSI", {}) or {}
            param = r2.get("PARAMETER_TINDAKAN", {}) or r2.get("LABORATORIUM", {}) or {}
            name = d.get("PARAMETER") or param.get("PARAMETER") or param.get("NAMA") or d.get("NAMA")
            val = d.get("HASIL"); sat = d.get("SATUAN") or ""
            normv = d.get("NORMAL") or d.get("NILAI_NORMAL") or param.get("NILAI_RUJUKAN") or ""
            if name and val is not None and str(val).strip():
                items.append({"nama": str(name).strip(), "hasil": str(val).strip(),
                              "satuan": str(sat).strip(), "normal": str(normv).strip()})
        return items
    MAX_PANELS = 8
    # panel non-lab yang nyangkut via keyword (bukan hasil lab riil) — buang
    _NOT_LAB = ("Konsultasi Dokter", "Pemeriksaan Fisik", "Pengambilan Sampel")
    panels = [pn for pn in (labj.get("panels", []) if isinstance(labj, dict) else [])
              if not any(nl.lower() in (pn.get("jenis") or "").lower() for nl in _NOT_LAB)]
    if panels:
        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            detils = list(ex.map(lambda pn: _detil(pn["kunjungan_lab"]), panels[:MAX_PANELS]))
        seen_kl = set()
        for pn, its in zip(panels[:MAX_PANELS], detils):
            kl = pn.get("kunjungan_lab", "")
            if kl in seen_kl:
                continue
            seen_kl.add(kl)
            card["lab_regular"].append({"tanggal": pn.get("tanggal", ""),
                                        "jenis": pn.get("jenis", "Lab"),
                                        "kunjungan_lab": kl,
                                        "items": its})
        if len(panels) > MAX_PANELS:
            card["lab_truncated"] = len(panels) - MAX_PANELS
    card["lab_special"] = spj.get("special", []) if isinstance(spj, dict) else []
    # dedup special by (tanggal, jenis, kesimpulan) — same result reachable via multiple keywords
    uniq_sp, seen_sp = [], set()
    for sp in card["lab_special"]:
        key = (sp.get("tanggal"), sp.get("jenis"), (sp.get("kesimpulan") or "")[:80])
        if key in seen_sp:
            continue
        seen_sp.add(key)
        uniq_sp.append(sp)
    card["lab_special"] = uniq_sp
    # panel regular dengan 0 item = bukan hasil lab nyata (tindakan/admin) — buang dari laporan
    card["lab_regular"] = [pn for pn in card["lab_regular"] if pn["items"]]
    ok_any = bool(card["nama"]) and (card["lab_regular"] or card["lab_special"])
    if card["nama"]:
        card["status"] = "done"
        card["error"] = "" if ok_any else "Tidak ada hasil laboratorium"
    else:
        card["status"] = "error"
        card["error"] = "NRM tidak ditemukan"
    return card

_LAPORAN_JOBS = {}          # job_id -> {total, done, cards, errors, started, user}
_LAPORAN_LOCK = _threading.Lock()
_LAPORAN_SEM = _threading.BoundedSemaphore(2)   # maks 2 job laporan bersamaan

# --- persist laporan ke disk: bisa dibuka ulang via /laporan-lab/{id} selama 48 jam ---
LAPORAN_DIR = BASE_DIR / "laporan"
LAPORAN_TTL = 3600 * 48

def _laporan_path(job_id):
    return LAPORAN_DIR / f"{job_id}.json"

def laporan_save(job_id):
    """Snapshot state job ke disk (dipanggil tiap card selesai & saat finished)."""
    try:
        with _LAPORAN_LOCK:
            j = _LAPORAN_JOBS.get(job_id)
            if not j:
                return
            snap = {"job_id": job_id, "total": j["total"], "done": j["done"],
                    "order": j["order"], "cards": j["cards"], "finished": j.get("finished", False),
                    "created": j["started"], "user": j.get("user", "")}
        LAPORAN_DIR.mkdir(exist_ok=True)
        p = _laporan_path(job_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False))
        os.replace(tmp, p)
    except Exception:
        pass

def laporan_load(job_id):
    """Load snapshot dari disk; None kalau gak ada / expired 48 jam."""
    p = _laporan_path(job_id)
    try:
        if p.exists() and (time.time() - p.stat().st_mtime) < LAPORAN_TTL:
            return json.loads(p.read_text())
    except Exception:
        pass
    return None

def laporan_cleanup():
    """Buang snapshot >48 jam (dipanggil saat start job baru)."""
    try:
        if LAPORAN_DIR.exists():
            cutoff = time.time() - LAPORAN_TTL
            for f in LAPORAN_DIR.glob("*.json"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
    except Exception:
        pass

@app.post("/api2/laporan-lab/start")
def api2_laporan_start(request: Request, payload: dict):
    sess, user = require_auth2(request)
    norms = extract_nrm(payload.get("text", ""))
    for x in (payload.get("extra_norms") or []):
        x = str(x).strip()
        if _re.fullmatch(r"\d{5,12}", x) and x not in norms:
            norms.append(x)
    if not norms:
        return JSONResponse({"ok": False, "error": "Tidak ada nomor RM valid (5-12 digit) di teks"}, status_code=400)
    if len(norms) > 60:
        return JSONResponse({"ok": False, "error": f"Maks 60 pasien per laporan (dikirim {len(norms)})"}, status_code=400)
    laporan_cleanup()
    job_id = str(uuid.uuid4())[:8]
    with _LAPORAN_LOCK:
        _LAPORAN_JOBS[job_id] = {"total": len(norms), "done": 0, "cards": {}, "order": norms,
                                 "started": time.time(), "user": user, "finished": False}
    laporan_save(job_id)
    def _run():
        acquired = _LAPORAN_SEM.acquire(timeout=300)
        if not acquired:
            with _LAPORAN_LOCK:
                j = _LAPORAN_JOBS.get(job_id)
                if j: j["abort"] = "Antrian penuh, coba lagi"
            laporan_save(job_id)
            return
        try:
            for n in norms:
                try:
                    card = _laporan_one(sess, n)
                except Exception as e:
                    card = {"norm": n, "nama": "", "status": "error", "error": str(e)[:150]}
                with _LAPORAN_LOCK:
                    j = _LAPORAN_JOBS.get(job_id)
                    if j:
                        j["cards"][n] = card
                        j["done"] += 1
                laporan_save(job_id)
            with _LAPORAN_LOCK:
                j = _LAPORAN_JOBS.get(job_id)
                if j: j["finished"] = True
            laporan_save(job_id)
        finally:
            _LAPORAN_SEM.release()
    t = _threading.Thread(target=_run, daemon=True)
    t.start()
    return JSONResponse({"ok": True, "job_id": job_id, "total": len(norms), "norms": norms})

def _sse(obj):
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

@app.get("/api2/laporan-lab/snapshot/{job_id}")
def api2_laporan_snapshot(request: Request, job_id: str):
    """Snapshot penuh untuk view ulang /laporan-lab/{id} — dari memori kalau masih hidup, dari disk kalau sudah lewat."""
    sess, user = require_auth2(request)
    with _LAPORAN_LOCK:
        j = _LAPORAN_JOBS.get(job_id)
        if j:
            snap = {"job_id": job_id, "total": j["total"], "done": j["done"], "order": j["order"],
                    "cards": j["cards"], "finished": j.get("finished", False), "created": j["started"]}
            return JSONResponse({"ok": True, "snapshot": snap})
    snap = laporan_load(job_id)
    if snap:
        return JSONResponse({"ok": True, "snapshot": snap})
    return JSONResponse({"ok": False, "error": "Laporan tidak ditemukan atau sudah kedaluwarsa (>48 jam)"}, status_code=404)

@app.post("/api2/laporan-lab/refresh-one")
def api2_laporan_refresh_one(request: Request, payload: dict):
    """Ambil ulang data 1 pasien (fresh, bypass cache lab) dan update snapshot di disk."""
    sess, user = require_auth2(request)
    job_id = str(payload.get("job_id") or "")
    norm = str(payload.get("norm") or "").strip()
    if not _re.fullmatch(r"[0-9a-f-]{4,16}", job_id) or not _re.fullmatch(r"\d{5,12}", norm):
        return JSONResponse({"ok": False, "error": "job_id/norm tidak valid"}, status_code=400)
    snap = laporan_load(job_id)
    if not snap:
        return JSONResponse({"ok": False, "error": "Laporan tidak ditemukan / kedaluwarsa"}, status_code=404)
    try:
        card = _laporan_one(sess, norm)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)
    snap["cards"][norm] = card
    p = _laporan_path(job_id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False))
    os.replace(tmp, p)
    with _LAPORAN_LOCK:
        j = _LAPORAN_JOBS.get(job_id)
        if j:
            j["cards"][norm] = card
    return JSONResponse({"ok": True, "card": card})

@app.get("/api2/laporan-lab/stream/{job_id}")
def api2_laporan_stream(request: Request, job_id: str):
    sess, user = require_auth2(request)
    def gen():
        sent = 0
        t0 = time.time()
        while time.time() - t0 < 1800:   # hard cap 30 menit
            with _LAPORAN_LOCK:
                j = _LAPORAN_JOBS.get(job_id)
                if not j:
                    yield _sse({"event": "error", "error": "Job tidak ditemukan"}); return
                done = j["done"]; total = j["total"]
                finished = j.get("finished"); abort = j.get("abort")
                new_cards = [j["cards"][n] for n in j["order"][sent:done]]
                sent = done
            for c in new_cards:
                yield _sse({"event": "card", "card": c, "done": done, "total": total})
            if finished:
                yield _sse({"event": "done", "done": done, "total": total}); return
            if abort:
                yield _sse({"event": "error", "error": abort}); return
            yield _sse({"event": "ping"})
            time.sleep(1.5)
        yield _sse({"event": "error", "error": "Timeout >30 menit"})
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8099, timeout_keep_alive=600, timeout_graceful_shutdown=30)
