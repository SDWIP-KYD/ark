#!/usr/bin/env python3
"""hematology_lookup.py — Accurate real-time lookup by NORM via SIMRS.
Features:
1. Fast Direct Lab Rutin via /layanan/hasillab + Time Clustering & Standard Sorting
2. Accurate Special Modules (PA, Rad, BMP, LCS, Immuno, IHC) with full KESIMPULAN / KESAN / HASIL.
"""
import sys, os, json, requests, time
from collections import OrderedDict
from datetime import datetime

BASE = "http://localhost:8080/webservice"
# Kredensial via environment (jangan hardcode) — lihat .env.example
LOGIN = os.environ.get("SIMRS_LOGIN", "")
PASS = os.environ.get("SIMRS_PASS", "")

PARAM_ORDER = [
    'HB', 'HGB', 'HEMOGLOBIN',
    'PLT', 'TROMBOSIT', 'PLATELET',
    'RET', 'RETICULOCYTE', 'RET-HE',
    'MCV', 'MCH', 'MCHC', 'HCT', 'HEMATOKRIT',
    'WBC', 'LEUKOSIT',
    'NEUT', 'NEUTROFIL', 'NEUT%',
    'LYMPH', 'LIMFOSIT', 'LYMPH%',
    'MONO', 'MONOSIT', 'MONO%',
    'EO', 'EOSINOFIL', 'BASO', 'BASOFIL',
    'LED', 'LED I', 'LED II', 'NRBC', 'NRBC%', 'IPF',
    'NATRIUM', 'NA', 'KALIUM', 'K', 'KLORIDA', 'CL',
    'UREUM', 'KREATININ', 'EGFR',
    'SGOT', 'SGPT', 'ALBUMIN', 'PROTEIN TOTAL', 'GLOBULIN',
    'BILIRUBIN TOTAL', 'BILIRUBIN DIREK', 'BILIRUBIN INDIREK',
    'GDS', 'GDP', 'GLUKOSA', 'GLUKOSA POCT',
    'PT', 'APTT', 'INR', 'FIBRINOGEN', 'D-DIMER',
    'CRP', 'PROKALSITONIN', 'FERITIN',
    'PH', 'PCO2', 'PO2', 'HCO3', 'BE', 'SO2', 'CTO2', 'CTCO2'
]

def make_session():
    s = requests.Session()
    try:
        r = s.post(f"{BASE}/authentication/login", json={'LOGIN': LOGIN, 'PASSWORD': PASS, 'CAPTCHA': 'x'}, timeout=15)
        if r.json().get('success'):
            return s
    except Exception:
        pass
    return None

def get_patient_name(s, norm):
    try:
        r = s.get(f"{BASE}/pasien/{norm}", timeout=15)
        if r.status_code == 200:
            d = r.json()
            for k in ('NAMA', 'name', 'nama'):
                if d.get(k): return d.get(k)
            if isinstance(d.get('data'), dict):
                return d['data'].get('NAMA') or d['data'].get('nama')
    except Exception:
        pass
    return None

def sort_params(params):
    def get_rank(name):
        n_up = str(name).upper().strip()
        for idx, target in enumerate(PARAM_ORDER):
            if n_up == target or n_up.startswith(target + ' ') or n_up.startswith(target + '(') or target in n_up:
                return idx
        return 999
    return sorted(params, key=lambda x: (get_rank(x.get('name', '')), x.get('name', '')))

def cluster_labs(raw_groups):
    """Cluster lab groups: <= 2 hours apart on same date -> merged, > 2 hours -> split."""
    if not raw_groups: return []
    parsed = []
    for g in raw_groups:
        tgl_str = g.get('tgl', '')
        dt = None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(tgl_str[:19], fmt)
                break
            except Exception: pass
        parsed.append({'dt': dt, 'tgl': tgl_str, 'params': g.get('params', []), 'vid': g.get('vid')})

    parsed.sort(key=lambda x: x['dt'] or datetime.min, reverse=True)
    clustered = []
    for item in parsed:
        dt = item['dt']
        tgl_str = item['tgl']
        params = item['params']
        if not clustered:
            clustered.append({'dt': dt, 'tgl': tgl_str, 'params': list(params)})
            continue
        last = clustered[-1]
        last_dt = last['dt']
        if dt and last_dt and dt.date() == last_dt.date():
            diff_hours = abs((last_dt - dt).total_seconds()) / 3600.0
            if diff_hours <= 2.0:
                existing_names = {p.get('name') for p in last['params']}
                for p in params:
                    pn = p.get('name')
                    if pn not in existing_names:
                        last['params'].append(p)
                        existing_names.add(pn)
            else:
                clustered.append({'dt': dt, 'tgl': tgl_str, 'params': list(params)})
        else:
            clustered.append({'dt': dt, 'tgl': tgl_str, 'params': list(params)})

    for c in clustered:
        if 'dt' in c: del c['dt']
        c['params'] = sort_params(c['params'])
    return clustered

def fetch_lab_quick_fast(s, norm, max_clusters=3):
    """<3s quick path: small hasillab page to discover recent KUNJUNGAN_LAB ids,
    then one cheap detil call per visit instead of pulling hundreds of raw rows.
    Returns same visit shape as fetch_lab_data (tgl, params)."""
    try:
        # 1) Discover recent visit ids with a small page (SIMRS newest-first).
        r = s.get(f"{BASE}/layanan/hasillab", params={"NORM": norm, "limit": 25}, timeout=30).json()
        items = r.get("data") or []
        seen = []
        for it in items:
            rf = it.get("REFERENSI", {})
            if isinstance(rf, str):
                try: rf = json.loads(rf)
                except: rf = {}
            tm = rf.get("TINDAKAN_MEDIS", {}) if isinstance(rf, dict) else {}
            if isinstance(tm, str):
                try: tm = json.loads(tm)
                except: tm = {}
            kid = tm.get("KUNJUNGAN")
            tgl = (it.get("TANGGAL") or "")[:19]
            if kid and kid not in [x[0] for x in seen]:
                seen.append((kid, tgl))
        visits = []
        for kid, tgl in seen[:max_clusters]:
            try:
                d = s.get(f"{BASE}/medicalrecord/resume/hasillab/detil",
                          params={"KUNJUNGAN_LAB": kid}, timeout=20).json()
            except Exception:
                continue
            params = []
            for x in (d.get("data") or []):
                prf = x.get("REFERENSI", {}) or {}
                pt = prf.get("PARAMETER_TINDAKAN", {}) if isinstance(prf, dict) else {}
                if isinstance(pt, str):
                    try: pt = json.loads(pt)
                    except: pt = {}
                if not isinstance(pt, dict): pt = {}
                pn = pt.get("PARAMETER") or x.get("PARAMETER") or x.get("NAMA") or "?"
                if not pn or pn == "-": continue
                sat = ""
                nested = pt.get("REFERENSI") if isinstance(pt, dict) else None
                if isinstance(nested, dict):
                    sat_obj = nested.get("SATUAN")
                    if isinstance(sat_obj, dict):
                        sat = sat_obj.get("DESKRIPSI", "") or ""
                if not sat:
                    direct = pt.get("SATUAN")
                    if isinstance(direct, dict):
                        sat = direct.get("DESKRIPSI", "") or ""
                    elif isinstance(direct, str) and direct not in ("0", "0.0"):
                        sat = direct
                params.append({
                    "name": pn,
                    "hasil": str(x.get("HASIL", "") or ""),
                    "normal": str(pt.get("NILAI_RUJUKAN", "") or x.get("NILAI_NORMAL", "") or ""),
                    "satuan": sat,
                })
            if params:
                visits.append({"tgl": tgl, "params": params})
        return visits
    except Exception as e:
        print(f"ERR fetch_lab_quick_fast {norm}: {e}")
        return []

def fetch_lab_data(s, norm, quick=False, max_clusters=3):
    try:
        all_items = []
        offset = 0
        page_limit = 200
        # In quick mode, stop once we have enough recent records to form max_clusters
        # SIMRS returns newest-first, so first pages already contain the latest visits.
        while True:
            r = None
            for attempt in range(3):
                try:
                    # ExtJS pagination uses `start`, not `offset`.
                    # `offset` is silently ignored by SIMRS and repeats page 1.
                    r = s.get(f"{BASE}/layanan/hasillab", params={"NORM": norm, "limit": page_limit, "start": offset}, timeout=60).json()
                    break
                except Exception as e:
                    print(f"  retry offset {offset} attempt {attempt + 1}/3: {e}")
                    time.sleep(2)
            if not isinstance(r, dict) or not r.get("success"):
                break
            batch = r.get("data") or []
            all_items.extend(batch)
            total = r.get("total", 0) or 0
            # Build preliminary clusters to decide if we have enough in quick mode
            if quick:
                prelim = _cluster_from_items(all_items, max_clusters)
                if len(prelim) >= max_clusters:
                    break
            if len(batch) < page_limit or (total and offset + len(batch) >= total):
                break
            offset += page_limit
        if quick:
            clustered = _cluster_from_items(all_items, max_clusters)
        else:
            clustered = _cluster_from_items(all_items, None)
        return clustered
    except Exception as e:
        print(f"ERR fetch_lab_data {norm}: {e}")
        return []

def _cluster_from_items(all_items, max_clusters=None):
    """Cluster raw items into visits; optionally cap at max_clusters (newest first)."""
    vd = OrderedDict()
    for it in all_items:
        # Group by KUNJUNGAN (visit), not by TINDAKAN_MEDIS (individual test ID).
        # One lab visit has many TINDAKAN_MEDIS entries; KUNJUNGAN is the real visit key.
        rf = it.get("REFERENSI", {})
        if isinstance(rf, str):
            try: rf = json.loads(rf)
            except: rf = {}
        tm = rf.get("TINDAKAN_MEDIS", {}) if isinstance(rf, dict) else {}
        if isinstance(tm, str):
            try: tm = json.loads(tm)
            except: tm = {}
        vid = str(tm.get("KUNJUNGAN") or it.get("KUNJUNGAN_LAB") or it.get("TINDAKAN_MEDIS") or "?")
        # Use the visit date (TANGGAL) as cluster time; fall back to item TANGGAL
        tgl = (it.get("TANGGAL") or rf.get("TANGGAL") or "?")[:19]
        if vid not in vd: vd[vid] = {"tgl": tgl, "vid": vid, "params": [], "_seen": set()}
        prf = rf.get("PARAMETER_TINDAKAN", {}) if isinstance(rf, dict) else {}
        if isinstance(prf, str):
            try: prf = json.loads(prf)
            except: prf = {}
        if not isinstance(prf, dict): prf = {}
        pn = str(prf.get("PARAMETER", "?") or "")
        if not pn or pn == "-": continue
        if pn in vd[vid]["_seen"]:
            continue
        vd[vid]["_seen"].add(pn)
        # Resolve SATUAN: nested REFERENSI.SATUAN.DESKRIPSI (SIMRS stores SATUAN as ID, desc in nested REFERENSI)
        sat = ""
        nested = prf.get("REFERENSI") if isinstance(prf, dict) else None
        if isinstance(nested, str):
            try: nested = json.loads(nested)
            except: nested = {}
        if isinstance(nested, dict):
            sat_obj = nested.get("SATUAN")
            if isinstance(sat_obj, dict):
                sat = str(sat_obj.get("DESKRIPSI", "") or "")
        # Fallback: direct SATUAN as string/dict
        if not sat:
            direct = prf.get("SATUAN")
            if isinstance(direct, dict):
                sat = str(direct.get("DESKRIPSI", "") or "")
            elif isinstance(direct, str) and direct not in ("0", "0.0"):
                sat = direct
        vd[vid]["params"].append({
            "name": pn,
            "hasil": str(it.get("HASIL", "") or ""),
            "normal": str(prf.get("NILAI_RUJUKAN", "") or prf.get("NILAI_NORMAL", "") or ""),
            "satuan": sat,
        })
    out = []
    for v in vd.values():
        v.pop("_seen", None)
        out.append(v)
    return cluster_labs(out)[:max_clusters] if max_clusters else cluster_labs(out)

def fetch_special_full(s, norm):
    """Fetch 6 special modules with true KESIMPULAN / KESAN / HASIL."""
    sys.path.insert(0, '/home/lenovo')
    import fetch_special_15agustus as FS
    special = {'pa': [], 'rad': [], 'bmp': [], 'lcs': [], 'immuno': [], 'ihc': []}
    
    try:
        special['pa'] = FS.fetch_pa(s, norm) or []
    except Exception: pass

    try:
        special['rad'] = FS.fetch_rad(s, norm) or []
    except Exception: pass

    try:
        special['bmp'] = FS.fetch_bmp(s, norm) or []
    except Exception: pass

    try:
        special['lcs'] = FS.fetch_lcs(s, norm) or []
    except Exception: pass

    try:
        special['immuno'] = FS.fetch_immuno(s, norm) or []
    except Exception: pass

    try:
        special['ihc'] = FS.fetch_ihc(s, norm) or []
    except Exception: pass

    # Dedup & Clean up PA & IHC if duplicate KUNJUNGAN
    for k in special:
        seen = set()
        clean = []
        for it in special[k]:
            tgl = it.get('tanggal', '')
            kes = it.get('kesimpulan') or it.get('kesan') or it.get('hasil') or ''
            key = (tgl, kes[:50])
            if key not in seen:
                seen.add(key)
                clean.append(it)
        special[k] = clean

    return special

def fetch_adt_via_tindakan(s, norm):
    """GAMBARAN DARAH TEPI (ADT) sometimes doesn't appear in /layanan/hasillab?NORM=
    but IS found via /layanan/tindakanmedis?NORM=X&NAMA=kw. Fetch those visit ids,
    pull detil per KUNJUNGAN_LAB, return as visit dicts (tgl + params).
    Dedup against visit ids already collected by the lab fetch."""
    try:
        kids = []
        for kw in ('darah tepi', 'gambaran darah'):
            try:
                r = s.get(f"{BASE}/layanan/tindakanmedis",
                          params={"NORM": norm, "NAMA": kw, "limit": 50}, timeout=20).json()
            except Exception:
                continue
            for t in (r.get("data") or []):
                kid = t.get("KUNJUNGAN")
                tgl = (t.get("TANGGAL") or "")[:19]
                if kid and kid not in [x[0] for x in kids]:
                    kids.append((kid, tgl))
        out = []
        for kid, tgl in kids:
            try:
                d = s.get(f"{BASE}/medicalrecord/resume/hasillab/detil",
                          params={"KUNJUNGAN_LAB": kid}, timeout=20).json()
            except Exception:
                continue
            params = []
            for x in (d.get("data") or []):
                rf = x.get("REFERENSI", {}) or {}
                pt = rf.get("PARAMETER_TINDAKAN", {}) if isinstance(rf, dict) else {}
                if isinstance(pt, str):
                    try: pt = json.loads(pt)
                    except: pt = {}
                if not isinstance(pt, dict): pt = {}
                pn = pt.get("PARAMETER") or x.get("PARAMETER") or x.get("NAMA") or "?"
                if not pn or pn == "-": continue
                sat = ""
                nested = pt.get("REFERENSI") if isinstance(pt, dict) else None
                if isinstance(nested, dict):
                    so = nested.get("SATUAN")
                    if isinstance(so, dict): sat = so.get("DESKRIPSI", "") or ""
                params.append({
                    "name": pn,
                    "hasil": str(x.get("HASIL", "") or ""),
                    "normal": str(pt.get("NILAI_RUJUKAN", "") or x.get("NILAI_NORMAL", "") or ""),
                    "satuan": sat,
                })
            if params:
                out.append({"tgl": tgl, "params": params})
        return out
    except Exception as e:
        print(f"ERR fetch_adt_via_tindakan {norm}: {e}")
        return []

def lookup(norm, include_special=True, quick=False, max_clusters=3, use_cache=True):
    import os, time
    cache_dir = '/tmp/hema_lookup_cache'
    try: os.makedirs(cache_dir, exist_ok=True)
    except: pass
    cache_file = os.path.join(cache_dir, f"{norm}_{include_special}_{('q'+str(max_clusters) if quick else 'full')}.json")
    # Serve from cache if fresh (< 6 hours) to avoid re-fetching huge histories.
    # Pre-warm cron keeps these fresh; this prevents Cloudflare 100s timeouts on big patients.
    if use_cache:
        try:
            # Quick mode reads the FULL cache (if present) and slices to max_clusters,
            # so previously-fetched RMs return <1s without touching SIMRS.
            probe = cache_file if not quick else os.path.join(cache_dir, f"{norm}_{include_special}_full.json")
            if os.path.exists(probe) and time.time() - os.path.getmtime(probe) < 21600:
                cached = json.load(open(probe))
                if quick:
                    cached = dict(cached)
                    cached['visits'] = cached.get('visits', [])[:max_clusters]
                    cached['is_partial'] = True
                    cached['total_records'] = len(cached['visits'])
                return cached
        except Exception:
            pass

    try:
        n = str(int(norm))
    except Exception:
        return {'success': False, 'error': 'NORM harus angka'}

    s = make_session()
    if not s:
        return {'success': False, 'error': 'SIMRS tidak reachable / login gagal'}

    name = get_patient_name(s, n) or f"Pasien RM {n}"
    # Quick mode: fast <3s path via small page + detil calls. Fallback to full-page fetch.
    if quick:
        clustered_labs = fetch_lab_quick_fast(s, n, max_clusters=max_clusters)
        if not clustered_labs:
            clustered_labs = fetch_lab_data(s, n, quick=True, max_clusters=max_clusters)
    else:
        clustered_labs = fetch_lab_data(s, n, quick=False, max_clusters=max_clusters)
    # ADT (GAMBARAN DARAH TEPI) sometimes missing from /layanan/hasillab?NORM= but
    # present in /layanan/tindakanmedis — merge those visits in (dedup by visit id).
    try:
        adt = fetch_adt_via_tindakan(s, n)
        if adt:
            existing = {(v.get('tgl') or '')[:16] for v in clustered_labs}
            for av in adt:
                key = (av.get('tgl') or '')[:16]
                if key and key not in existing:
                    clustered_labs.append(av)
                    existing.add(key)
            clustered_labs.sort(key=lambda v: v.get('tgl') or '', reverse=True)
    except Exception as e:
        print(f"ERR adt merge {n}: {e}")
    special = fetch_special_full(s, n) if include_special else {'pa': [], 'rad': [], 'bmp': [], 'lcs': [], 'immuno': [], 'ihc': []}

    if not clustered_labs and not any(special.values()):
        return {'success': False, 'error': f'Tidak ditemukan riwayat data lab maupun penunjang di SIMRS untuk NORM {n}'}

    # In quick mode, also fetch the true full count of visits for the "load all" button
    is_partial = False
    full_count = len(clustered_labs)
    if quick:
        try:
            cnt_r = s.get(f"{BASE}/layanan/hasillab", params={"NORM": n, "limit": 1}, timeout=15).json()
            # total_records from API is raw item count, not clusters; estimate clusters via full fetch only if needed
            full_count = cnt_r.get("total", len(clustered_labs)) or len(clustered_labs)
        except Exception:
            pass
        is_partial = True

    result = {
        'success': True,
        'norm': n,
        'name': name,
        'visits': clustered_labs,
        'total_records': len(clustered_labs),
        'is_partial': is_partial,
        'full_estimate': full_count,
        'special': special
    }
    # Cache result for fast repeat lookups (only full mode caches to disk)
    if not quick:
        try:
            with open(cache_file, 'w') as cf:
                json.dump(result, cf, ensure_ascii=False)
        except Exception:
            pass
    return result

if __name__ == '__main__':
    n = sys.argv[1] if len(sys.argv) > 1 else '1469712'
    print(json.dumps(lookup(n, include_special=True), ensure_ascii=False, indent=2)[:600])
