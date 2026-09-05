#!/usr/bin/env python3
"""Fetch PA + Rad + BMP for all patients in list. Save to pickle.
Chain:
  PA: tindakanmedis?NORM -> filter histopat/sitolog -> KUNJUNGAN 101160601 -> pa/hasil?JENIS=1/2/3
  Rad: hasilrad?NORM=norm (limit=50)
  BMP: orderlab?KUNJUNGAN=<lab_KUNJ> -> orderdetillab?ORDER_ID -> kunjungan?REF -> evaluasisst?KUNJUNGAN
"""
import requests, pickle, os, sys, time
from collections import OrderedDict
sys.path.insert(0,'/home/lenovo')
from patients_29agustus import PATIENTS

BASE="http://localhost:8080/webservice"
# Kredensial via environment (jangan hardcode) — lihat .env.example
import os as _os
LOGIN=_os.environ.get("SIMRS_LOGIN", "")
PASS=_os.environ.get("SIMRS_PASS", "")
OUT_PKL="/tmp/simrs_special_15agustus.pkl"
TIMEOUT=25

def make_session():
    s=requests.Session()
    for _ in range(3):
        try:
            r=s.post(f"{BASE}/authentication/login", json={'LOGIN':LOGIN,'PASSWORD':PASS,'CAPTCHA':'x'}, timeout=15)
            if r.json().get('success'): return s
        except: time.sleep(2)
    return None

def get_tindakan(s,norm):
    """Pagination scan all tindakanmedis (patients can have 500+). Stops at API total."""
    all_t=[]
    total=None
    for off in range(0,2000,50):
        try:
            r=s.get(f"{BASE}/layanan/tindakanmedis", params={'NORM':norm,'offset':off,'limit':100}, timeout=20).json()
        except:
            s=make_session(); continue
        d=r.get('data') or []
        if total is None: total=r.get('total',0) or 0
        all_t.extend([x for x in d if isinstance(x,dict)])
        if not d or (total and len(all_t)>=total): break
        if off>=1950: break
    return all_t

def fetch_pa(s, norm, tindakan_cache=None):
    """Fetch PA (Histopatologi & Sitologi) via targeted search_tindakan keywords.
    Targeted search is 100x faster than full tindakanmedis scan (0.5s vs 70s+ per patient)."""
    pa_hits = []
    seen_kunj = set()
    for kw in ['histopat', 'sitolog', 'anatomi', 'ihc', 'patologi']:
        for kunj, tindakan, desc in search_tindakan(s, norm, kw):
            if kunj and kunj not in seen_kunj:
                seen_kunj.add(kunj)
                pa_hits.append((kunj, desc))
    out = []
    for kunj, desc in pa_hits:
        for jp in [1, 2, 3]:
            try:
                r = s.get(f"{BASE}/layanan/laboratorium/pa/hasil", params={'KUNJUNGAN': kunj, 'JENIS_PEMERIKSAAN': jp}, timeout=15).json()
                if r.get('success') and r.get('data'):
                    for d in r['data']:
                        out.append({
                            'jenis': jp,
                            'jaringan': d.get('JARINGAN') or desc,
                            'kesimpulan': d.get('KESIMPULAN') or d.get('HASIL'),
                            'tanggal': (d.get('TANGGAL') or '')[:10]
                        })
                    break
            except:
                pass
    return out

def fetch_rad(s,norm):
    try:
        r=s.get(f"{BASE}/layanan/hasilrad", params={'NORM':norm,'limit':50}, timeout=20).json()
        if r.get('success') and r.get('data'):
            out=[]
            for d in r['data']:
                out.append({'tanggal':d.get('TANGGAL','')[:10],'klinis':d.get('KLINIS'),'kesan':d.get('KESAN'),'hasil':d.get('HASIL')})
            return out
    except: pass
    return []

def fetch_bmp(s,norm):
    """Fetch BMP via direct search: tindakanmedis?NORM=&JENIS_TINDAKAN=8&NAMA=sumsum → evaluasisst.
    Much faster than scanning all NOPENs (1-2 calls vs 50+)."""
    try:
        r=s.get(f"{BASE}/layanan/tindakanmedis", params={'NORM':norm,'JENIS_TINDAKAN':8,'STATUS':1,'NAMA':'sumsum','limit':50}, timeout=20).json()
    except:
        return []
    out=[]
    for t in (r.get('data') or []):
        hk=t.get('KUNJUNGAN')
        if not hk: continue
        try:
            r2=s.get(f"{BASE}/layanan/hasil/evaluasisst", params={'KUNJUNGAN':hk,'limit':25}, timeout=15).json()
            if r2.get('success') and r2.get('data'):
                b=r2['data'][0]
                out.append({
                    'tanggal':hk[13:15]+'/'+hk[11:13]+'/20'+hk[9:11],
                    'selularitas':b.get('SELULARITAS'),
                    'eritropoietik':b.get('ERITROPOIETIK'),
                    'leukopoietik':b.get('LEUKOPOIETIK'),
                    'trombopoietik':b.get('TROMBOPOIETIK'),
                    'sel_plasma':b.get('SEL_PLASMA'),
                    'mitosis':b.get('MITOSIS'),
                    'me_ratio':b.get('ME_RATIO'),
                    'kesan':b.get('KESAN')
                })
        except: pass
    return out

def search_tindakan(s,norm,term):
    """Generic search: tindakanmedis?NORM=&JENIS_TINDAKAN=8&NAMA=term -> list of (KUNJUNGAN, TINDAKAN, TINDAKAN_DESKRIPSI)."""
    try:
        r=s.get(f"{BASE}/layanan/tindakanmedis", params={'NORM':norm,'JENIS_TINDAKAN':8,'STATUS':1,'NAMA':term,'limit':50}, timeout=20).json()
        return [(t.get('KUNJUNGAN'), t.get('TINDAKAN'), t.get('TINDAKAN_DESKRIPSI','')) for t in (r.get('data') or []) if t.get('KUNJUNGAN')]
    except:
        return []

def fetch_lcs(s,norm):
    """LCS (cairan serebrospinal) - sitologi cairan tubuh. Result in pa/hasil JENIS=2."""
    out=[]
    for kunj,tindakan,desc in search_tindakan(s,norm,'LCS'):
        try:
            r=s.get(f"{BASE}/layanan/laboratorium/pa/hasil", params={'KUNJUNGAN':kunj,'JENIS_PEMERIKSAAN':2}, timeout=15).json()
            if r.get('success') and r.get('data'):
                for d in r['data']:
                    out.append({'tanggal':(d.get('TANGGAL') or '')[:10],'jaringan':desc,'kesimpulan':d.get('KESIMPULAN') or d.get('HASIL')})
        except: pass
    return out

def fetch_immuno(s,norm):
    """Immunofenotyping leukemia - search 'LEUKIMIA' -> /layanan/hasillab + /layanan/catatanhasillab.
    Extracts: Leukosit, Blast %, Penanda Positif/CD Markers, dan Catatan Kesan Dokter Sp.PK."""
    out=[]
    tm_list = s.get(f"{BASE}/layanan/tindakanmedis", params={'NORM': norm, 'NAMA': 'LEUKIMIA', 'limit': 20}, timeout=15).json().get('data') or []
    for tm in tm_list:
        tm_id = tm.get('ID')
        kunj = tm.get('KUNJUNGAN')
        tgl = tm.get('TANGGAL')
        try:
            hl = s.get(f"{BASE}/layanan/hasillab", params={'TINDAKAN_MEDIS': tm_id}, timeout=10).json().get('data') or []
            params = []
            for row in hl:
                p_name = row.get('REFERENSI', {}).get('PARAMETER_TINDAKAN', {}).get('PARAMETER') or row.get('PARAMETER')
                val = row.get('HASIL')
                sat = row.get('SATUAN') or ''
                if p_name and val and str(val).strip():
                    params.append(f"{p_name.strip()}: {val} {sat}".strip())
            
            # Fetch Catatan Ekspertise Dokter Sp.PK
            cat_list = s.get(f"{BASE}/layanan/catatanhasillab", params={'KUNJUNGAN': kunj}, timeout=10).json().get('data') or []
            cat_teks = ''
            dr_nama = ''
            if cat_list:
                c = cat_list[0]
                cat_teks = (c.get('CATATAN') or '').strip()
                dr_nama = c.get('REFERENSI', {}).get('DOKTER', {}).get('NAMA') or ''

            if params or cat_teks:
                kesimpulan_parts = []
                if params:
                    kesimpulan_parts.append(' | '.join(params))
                if cat_teks:
                    kesimpulan_parts.append(f'Catatan: "{cat_teks}"')
                if dr_nama:
                    kesimpulan_parts.append(f'({dr_nama})')
                
                out.append({
                    'tanggal': (tgl or '')[:10],
                    'jaringan': tm.get('TINDAKAN_DESKRIPSI') or 'LEUKIMIA PHENOTYPING/ IMUNOFENOTYPING',
                    'kesimpulan': ' — '.join(kesimpulan_parts)
                })
        except: pass
    return out

def fetch_ihc(s,norm):
    """IHC / Immunohistokimia - search 'Imunohistokimia' -> pa/hasil (deduped by KUNJUNGAN)."""
    out=[]
    seen_kunj = set()
    for kunj,tindakan,desc in search_tindakan(s,norm,'Imunohistokimia'):
        if kunj in seen_kunj:
            continue
        seen_kunj.add(kunj)
        try:
            r=s.get(f"{BASE}/layanan/laboratorium/pa/hasil", params={'KUNJUNGAN':kunj}, timeout=10).json()
            if r.get('success') and r.get('data'):
                for d in r['data']:
                    out.append({
                        'tanggal':(d.get('TANGGAL') or '')[:10],
                        'jaringan':desc,
                        'kesimpulan':d.get('KESIMPULAN') or d.get('HASIL'),
                        'mikroskopik':d.get('MIKROSKOPIK') or ''
                    })
        except: pass
    return out

def main():
    s=make_session()
    if not s: print("LOGIN FAIL"); sys.exit(1)
    # Load existing or create
    if os.path.exists(OUT_PKL):
        data=pickle.load(open(OUT_PKL,'rb'))
    else:
        data={}
    norms=list(OrderedDict((p[3],p) for p in PATIENTS).values())
    print(f"Total patients: {len(norms)}")
    success=0; failed=[]
    for i,p in enumerate(norms):
        lokasi,kamar,name,norm,dx,status=p
        if norm in data:
            print(f"[{i+1}/{len(norms)}] {name}: SKIP (cached)")
            continue
        try:
            pa=fetch_pa(s,norm)
            rad=fetch_rad(s,norm)
            bmp=fetch_bmp(s,norm)
            data[norm]={'name':name,'norm':norm,'lokasi':lokasi,'kamar':kamar,'dx':dx,'status':status,
                        'pa':pa,'rad':rad,'bmp':bmp}
            print(f"[{i+1}/{len(norms)}] {name}: PA={len(pa)} Rad={len(rad)} BMP={len(bmp)}")
            success+=1
            # Save incrementally
            pickle.dump(data, open(OUT_PKL,'wb'))
        except Exception as e:
            print(f"[{i+1}/{len(norms)}] {name}: ERR {e}")
            failed.append(norm)
        time.sleep(0.3)
    print(f"\nDONE: {success} success, {len(failed)} failed")
    if failed: print(f"Failed: {failed}")
    print(f"Saved: {OUT_PKL}")

if __name__=='__main__':
    main()
