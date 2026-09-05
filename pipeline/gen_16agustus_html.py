#!/usr/bin/env python3
"""Hemato Lab Report Generator — List 16 Agustus 2026.
Features:
1. Grouping by Location -> Bed (Lantai 4 PICU, L5, L6, L7 Kemo/Non Kemo, IRD, PJT, PCC)
2. Accurate Patient Master integration (Lantai, Kamar, Diagnosis, Status KJS/Leader)
3. Lab Clustering: merges results on same date if within 2 hours; separates shift changes
4. Lab View Limit: displays 5 latest clustered visits by default, with +xx load button
5. Full Support for +xx more visits API
6. Special Modules: PA, Rad, BMP, LCS, Immuno, IHC
7. Note autosave to server + LocalStorage
"""
import pickle, re, os, json, sys
from collections import OrderedDict
from datetime import datetime

sys.path.insert(0, '/home/lenovo')
from patients_29agustus import PATIENTS

CRIT = {
    'hgb': (6, 18), 'hemoglobin': (6, 18), 'hb': (6, 18),
    'plt': (20, 1000), 'trombosit': (20, 1000),
    'wbc': (2, 50), 'leukosit': (2, 50),
    'natrium': (120, 160), 'kalium': (2.5, 6.5),
    'hct': (15, 60), 'hematokrit': (15, 60),
    'kreatinin': (None, 5), 'ureum': (None, 200),
    'crp': (None, 100), 'procalcitonin': (None, 50),
    'ferritin': (None, 2000), 'ferritine': (None, 2000)
}

def tv(v):
    if not v or v == '-': return None
    try: return float(v.strip().replace(',', '.').lstrip('<').lstrip('>'))
    except: return None

def pr(s):
    m = re.match(r'([\d.]+)\s*-\s*([\d.]+)', str(s).strip().replace(',', '.'))
    if m:
        try: return float(m.group(1)), float(m.group(2))
        except: pass
    return None

def classify(pv):
    hv = tv(pv.get('hasil'))
    rg = pr(pv.get('normal'))
    if hv is None or rg is None: return 'N'
    lo, hi = rg
    if hv < lo or hv > hi:
        nk = pv.get('name', '').lower().strip()
        for k, (cl, ch) in CRIT.items():
            if k in nk:
                if (cl is not None and hv < cl) or (ch is not None and hv > ch): return 'K'
        return 'A'
    return 'N'

STD_ORDER = [
    'hb', 'hgb', 'hemoglobin', 'plt', 'trombosit', 'ret', 'retikulosit', 'retikulosit%',
    'mcv', 'mch', 'mchc', 'hct', 'hematokrit', 'wbc', 'leukosit',
    'neut%', 'neutrofil%', 'neutrofil', 'lymph%', 'limfosit%', 'limfosit',
    'mono%', 'monosit%', 'monosit', 'natrium', 'na', 'kalium', 'k',
    'chlorida', 'cl', 'klorida', 'ureum', 'ur', 'kreatinin', 'cr',
    'sgot', 'ast', 'sgpt', 'alt', 'albumin'
]

def sort_params(params):
    def sk(p):
        n = p.get('name', '').lower().strip()
        for i, s in enumerate(STD_ORDER):
            if n == s or s in n or n in s: return i
        return len(STD_ORDER)
    return sorted(params, key=sk)

def cluster_labs(raw_groups):
    """Cluster lab results:
    - Same calendar day & time difference <= 2 hours -> MERGE
    - Same day & time difference > 2 hours -> KEEP SEPARATE
    - Different days -> KEEP SEPARATE
    """
    valid_groups = []
    for g in raw_groups:
        tgl_str = g.get('tgl', '')
        try:
            dt = datetime.strptime(tgl_str[:19], '%Y-%m-%d %H:%M:%S')
        except:
            try:
                dt = datetime.strptime(tgl_str[:10], '%Y-%m-%d')
            except:
                dt = datetime.min
        valid_groups.append((dt, tgl_str, g.get('params', [])))
    valid_groups.sort(key=lambda x: x[0], reverse=True)

    clustered = []
    for dt, tgl_str, params in valid_groups:
        if not clustered:
            clustered.append({'dt': dt, 'tgl': tgl_str, 'params': list(params)})
            continue
        last = clustered[-1]
        same_day = (dt.date() == last['dt'].date()) if (dt != datetime.min and last['dt'] != datetime.min) else (tgl_str[:10] == last['tgl'][:10])
        time_diff = abs((last['dt'] - dt).total_seconds()) if (dt != datetime.min and last['dt'] != datetime.min) else 999999

        if same_day and time_diff <= 7200: # 2 hours
            existing_names = {p['name'].lower().strip() for p in last['params']}
            for p in params:
                pn = p.get('name', '').lower().strip()
                if pn not in existing_names:
                    last['params'].append(p)
                    existing_names.add(pn)
        else:
            clustered.append({'dt': dt, 'tgl': tgl_str, 'params': list(params)})
            
    # Sort params in each clustered visit
    for c in clustered:
        c['params'] = sort_params(c['params'])
    return clustered

# 1. Load Lab Data & Special Data
PKL_LAB = '/tmp/simrs_15agustus_full.pkl'
PKL_SPEC = '/tmp/simrs_special_15agustus.pkl'

lab_data = pickle.load(open(PKL_LAB, 'rb')) if os.path.exists(PKL_LAB) else {}
spec_data = pickle.load(open(PKL_SPEC, 'rb')) if os.path.exists(PKL_SPEC) else {}

# 2. Build Master Patient Records from PATIENTS (16 Agustus)
patients = []
for idx, (lokasi, kamar, name, norm_raw, dx, status) in enumerate(PATIENTS):
    norm = str(int(norm_raw))
    
    # Retrieve raw labs
    l_entry = lab_data.get(norm) or {}
    raw_labs = l_entry.get('labs') or l_entry.get('visits') or []
    
    # Cluster labs
    clustered_visits = cluster_labs(raw_labs)
    
    # Retrieve special
    s_entry = spec_data.get(norm) or {}
    
    patients.append({
        'idx': idx + 1,
        'norm': norm,
        'name': name,
        'lokasi': lokasi,
        'kamar': kamar,
        'diagnosis': dx,
        'status': status,
        'visits': clustered_visits,
        'raw_total': l_entry.get('total', len(raw_labs)),
        'raw_shown': l_entry.get('shown', len(raw_labs)),
        'special': {
            'pa': s_entry.get('pa', []),
            'rad': s_entry.get('rad', []),
            'bmp': s_entry.get('bmp', []),
            'lcs': s_entry.get('lcs', []),
            'immuno': s_entry.get('immuno', []),
            'ihc': s_entry.get('ihc', [])
        }
    })

def kamar_label(kamar_str):
    if not kamar_str:
        return 'Tanpa Kamar'
    ks = str(kamar_str)
    if ks.startswith('Kamar '):
        return ks.split('·')[0].strip()
    if ks.startswith('Bed '):
        return 'Bed (Tanpa No. Kamar)'
    return ks

# 3. Group by Location -> Kamar -> patients (nested, preserves order)
def normalize_lokasi(lok):
    lok = (lok or '').strip()
    if lok in ('RUANG KEMO','RUANG NON KEMO','Lantai 7 KEMO','Lantai 7 NON KEMO','RUANG KEMO 7','RUANG NON KEMO 7'):
        return 'Lantai 7'
    if lok in ('KAMAR ISO','Kamar ISO','ISO','Ruang ISO'):
        return 'LANTAI 5'
    if lok in ('IRD Anak',):
        return 'IRD Anak Mother And Child'
    if lok in ('PICU RSUH',):
        return 'LANTAI 4 PICU'
    return lok

LOC_ORDER = [
    'LANTAI 4 PICU', 'LANTAI 5', 'LANTAI 6',
    'Lantai 7',
    'IRD Anak Mother And Child', 'PJT', 'PCC Lt 4', 'LANTAI 3 ICU IBU'
]

groups = OrderedDict()
for loc in LOC_ORDER:
    groups[loc] = OrderedDict()
for p in patients:
    lok = normalize_lokasi(p['lokasi'])
    p['lokasi'] = lok
    if lok not in groups:
        groups[lok] = OrderedDict()
    kl = kamar_label(p['kamar'])
    if kl not in groups[lok]:
        groups[lok][kl] = []
    groups[lok][kl].append(p)

today = os.environ.get('REPORT_DATE', datetime.now().strftime('%Y-%m-%d'))
tm = datetime.now().strftime('%H:%M')

hparts = []
uid = [0]

for lok, kamar_map in groups.items():
    if not kamar_map: continue
    lbg = {
        'LANTAI 4 PICU': '#f3e5f5',
        'LANTAI 5': '#e8f5e9',
        'LANTAI 6': '#e3f2fd',
        'Lantai 7': '#fff3e0',
        'IRD Anak Mother And Child': '#fce4ec',
        'PJT': '#fff8e1',
        'PCC Lt 4': '#e0f2f1',
        'LANTAI 3 ICU IBU': '#ede7f6',
        'PICU RSUH': '#f3e5f5',
    }.get(lok, '#f5f5f5')
    total_pats = sum(len(v) for v in kamar_map.values())
    hparts.append(f'<div class="loc-section"><div class="loc-title" style="background:{lbg}" onclick="toggleLoc(this)">{lok} <span class="count">({total_pats})</span> <span class="loc-toggle">▼</span></div>\n')
    hparts.append('<div class="loc-body">\n')

    for kl, pats in kamar_map.items():
        hparts.append(f'<div class="kamar-section"><div class="kamar-title" onclick="toggleKamar(this)">{kl} <span class="count">({len(pats)})</span> <span class="kamar-toggle">▶</span></div>\n')
        hparts.append('<div class="kamar-body collapsed">\n')
        for p in pats:
            vs = p['visits']
            uid[0] += 1
            pid = uid[0]

            hparts.append(f'<div class="patient-card" data-name="{p["name"].lower()}" data-norm="{p["norm"]}">\n')
            hparts.append(f'<div class="patient-header" onclick="togglePatient(this)">\n')
            hparts.append(f'<div><div class="patient-name">{p["name"]} <span class="room">/ {p["kamar"]}</span></div>\n')
            hparts.append(f'<div class="patient-meta">NORM: <b>{p["norm"]}</b> | <span class="status-badge status-{p["status"].lower()}">{p["status"]}</span> | {p["diagnosis"]}</div></div>\n')
            hparts.append(f'<span class="toggle-icon">▶</span></div>\n')

            hparts.append(f'<div class="patient-body">\n')
            hparts.append(f'<div class="note-box"><span class="note-label">📝 Catatan:</span><textarea id="note-{pid}" class="note-input" data-norm="{p["norm"]}" oninput="noteDirty(this)" placeholder="Tulis catatan untuk pasien ini..."></textarea></div>\n')

            # Toggle button for Special Data
            hparts.append(f'<div class="special-toggle"><button class="special-btn" onclick="toggleSpecial(this, \'{p["norm"]}\')">📂 Tampilkan PA / Rad / BMP / LCS / Immuno / IHC</button><div class="special-result" id="special-{pid}" style="display:none;"></div></div>\n')

            if not vs:
                hparts.append(f'<div class="no-lab-alert">⚠️ Belum ada riwayat hasil laboratorium di SIMRS</div>\n')
                hparts.append(f'</div></div>\n')
                continue
            hparts.append(f'<div class="lab-placeholder" id="lab-{pid}" data-norm="{p["norm"]}" data-loaded="0" style="padding:8px;text-align:center"><button class="lab-load-btn" style="background:#e8eaf6;color:#283593;border:1px solid #c5cae9;padding:6px 12px;border-radius:6px;font-size:11px;cursor:pointer" onclick="loadLab(this, \'{p["norm"]}\', {pid})">📥 Muat hasil lab ({len(vs)} kunjungan)</button><div class="lab-content" style="display:none;text-align:left"></div></div>\n')
            hparts.append(f'</div></div>\n')
            continue

            # Cap initial view to 5 clustered visits
            RENDER_N = 5
            shown = vs[:RENDER_N]

            tabs_html = [f'<div class="visit-tabs" id="tabs-{pid}">']
            for vi, v in enumerate(shown):
                a = "active" if vi == 0 else ""
                tgl_clean = (v.get("tgl") or "?")[:16]
                tabs_html.append(f'<div class="visit-tab {a}" onclick="showVisit(this,\'h{pid}-{vi}\')">Kunj {vi+1}<br><small>{tgl_clean}</small></div>')

            if len(vs) > RENDER_N:
                tabs_html.append(f'<button class="load-all-btn" id="btn-{pid}" onclick="loadAll(\'{p["norm"]}\',{pid})">+{len(vs)-RENDER_N} kunjungan lagi ▾</button>')
            tabs_html.append('</div>\n')
            hparts.append('\n'.join(tabs_html))

            # Visit panels
            for vi, v in enumerate(shown):
                a = "active" if vi == 0 else ""
                hparts.append(f'<div class="visit-panel {a}" id="h{pid}-{vi}">\n<table>\n<tr><th>Parameter</th><th>Hasil</th><th>N Normal</th><th>Satuan</th><th>Status</th></tr>\n')
                # ---- compute all derived ratios ----
                _wbc=_neut=_lymph=_mono=_bands=_eos=_baso=_rbc=_mcv=_mch=_hct=_rdw=_rdw_cv=_rdw_sd=_hgb=_mchc=_plt=_retic=_fe=_tibc=None
                for _pv in v.get('params', []):
                    _nm=(_pv.get('name') or '').upper()
                    _rv=str(_pv.get('hasil') or '').replace(',','.').strip()
                    try:
                        _x=float(_rv)
                    except:
                        _x=None
                    if _x is None: continue
                    if 'WBC' in _nm and _wbc is None: _wbc=_x
                    elif 'NEUT' in _nm and _neut is None: _neut=_x
                    elif 'LYMPH' in _nm and _lymph is None: _lymph=_x
                    elif 'MONO' in _nm and _mono is None: _mono=_x
                    elif 'BAND' in _nm and _bands is None: _bands=_x
                    elif 'EO' in _nm and _eos is None: _eos=_x
                    elif 'BASO' in _nm and _baso is None: _baso=_x
                    elif (_nm=='RBC' or _nm=='ERITROSIT') and _rbc is None: _rbc=_x
                    elif 'MCV' in _nm and _mcv is None: _mcv=_x
                    elif 'MCH' in _nm and _mch is None: _mch=_x
                    elif 'RDW-CV' in _nm and _rdw_cv is None: _rdw_cv=_x
                    elif 'RDW-SD' in _nm and _rdw_sd is None: _rdw_sd=_x
                    elif 'RDW' in _nm and _rdw is None: _rdw=_x
                    elif ('HGB' in _nm or 'HB' in _nm) and _hgb is None: _hgb=_x
                    elif ('HCT' in _nm or 'HEMATOKRIT' in _nm) and _hct is None: _hct=_x
                    elif 'MCHC' in _nm and _mchc is None: _mchc=_x
                    elif ('PLT' in _nm or 'TROMB' in _nm) and _plt is None: _plt=_x
                    elif 'RETIC' in _nm and _retic is None: _retic=_x
                    elif _nm=='FE (BESI)' and _fe is None: _fe=_x
                    elif 'TIBC' in _nm and _tibc is None: _tibc=_x
                _b=_bands if _bands is not None else 0
                # absolute counts (/uL)
                _anc = _wbc*(_neut+_b)/100.0*1000.0 if (_wbc is not None and _neut is not None) else None
                _absL = _wbc*_lymph/100.0*1000.0 if (_wbc is not None and _lymph is not None) else None
                _absM = _wbc*_mono/100.0*1000.0 if (_wbc is not None and _mono is not None) else None
                # ratios
                _nlr = (_neut/_lymph) if (_neut is not None and _lymph not in (None,0)) else None
                _plr = (_plt/(_lymph/100.0*_wbc)) if (_plt is not None and _lymph is not None and _wbc not in (None,0)) else None
                _lmr = (_lymph/_mono) if (_lymph is not None and _mono not in (None,0)) else None
                _mlr = (_mono/_lymph) if (_mono is not None and _lymph not in (None,0)) else None
                _rdw = _rdw_cv if _rdw_cv is not None else (_rdw_sd if _rdw_sd is not None else _rdw)
                # SII = PLT(10^3/µL)*1000 × NEUT% / LYMPH% — PLT must be in /µL (×1000); NEUT/LYMPH are % so ratio cancels
                _sii = (_plt*1000.0*_neut/_lymph) if (_plt is not None and _neut is not None and _lymph not in (None,0)) else None
                _mentzer = (_mcv/_rbc) if (_mcv is not None and _rbc not in (None,0)) else None
                _rdwp = (_rdw/_plt*100.0) if (_rdw is not None and _plt not in (None,0)) else None
                # RPR & sat transferin need RETIC / Fe+TIBC (often missing)
                _rpr = (_retic*_hgb/45.0) if (_retic is not None and _hgb is not None) else None
                _tsat = (_fe/_tibc*100.0) if (_fe is not None and _tibc not in (None,0)) else None
                # ---- NEW CBC-derived ratios (Tier 1-3) ----
                # absolute lymphocyte (for RLR)
                _absL2 = _wbc*_lymph/100.0*1000.0 if (_wbc is not None and _lymph not in (None,0)) else None
                # Tier 1: differential / platelet composites
                _siri = (_neut*_mono/_lymph) if (_neut is not None and _mono is not None and _lymph not in (None,0)) else None
                _aisi = (_plt*_neut*_mono/_lymph) if (_plt is not None and _neut is not None and _mono is not None and _lymph not in (None,0)) else None
                _nmr = (_neut/_mono) if (_neut is not None and _mono not in (None,0)) else None
                _elr = (_eos/_lymph) if (_eos is not None and _lymph not in (None,0)) else None
                # Tier 2: RBC indices
                _rlr = (_rdw_cv/_absL2) if (_rdw_cv is not None and _absL2 not in (None,0)) else None  # RDW-CV / absLymph
                _shine = (_mcv*_mcv*_rdw_cv/(_hgb*100.0)) if (_mcv is not None and _rdw_cv is not None and _hgb not in (None,0)) else None
                _engf = (_mcv - 10.0*_mch) if (_mcv is not None and _mch is not None) else None
                _hcr = (_hct/_hgb) if (_hct is not None and _hgb not in (None,0)) else None
                _rdwmcv = (_rdw_cv/_mcv) if (_rdw_cv is not None and _mcv not in (None,0)) else None
                # Tier 3: NRBC
                _nrbc_h = _nrbc_p = None
                for _pv2 in v.get('params', []):
                    _n2=(_pv2.get('name') or '').upper()
                    try: _x2=float(str(_pv2.get('hasil')).replace(',','.'))
                    except: _x2=None
                    if _x2 is None: continue
                    if _n2=='NRBC#' and _nrbc_p is None: _nrbc_p=_x2
                    elif _n2=='NRBC%' and _nrbc_h is None: _nrbc_h=_x2
                _absnrbc = (_nrbc_p/100.0*(_wbc*1000.0)) if (_nrbc_p is not None and _wbc is not None) else None
                _anc_done=False; _absL_done=False; _absM_done=False
                _nlr_done=False; _plr_done=False; _lmr_done=False; _mlr_done=False; _sii_done=False; _mentzer_done=False; _rdwp_done=False; _rpr_done=False; _tsat_done=False
                for pv in v.get('params', []):
                    sp = classify(pv)
                    sl = {'A': 'Bermakna', 'K': 'KRITIS', 'N': 'Normal'}[sp]
                    val = str(pv.get('hasil', ''))
                    if 'WBC' in (pv.get('name') or '').upper():
                        try:
                            _wv=float(str(pv.get('hasil')).replace(',','.'))
                            val=f"{_wv*1000:.0f}"
                        except: pass
                    if sp == 'K': val += " [K]"
                    elif sp == 'A': val += " [A]"
                    cls = "critical" if sp == "K" else ("abnormal" if sp == "A" else "normal")
                    icon = "🔴" if sp == "K" else ("🟠" if sp == "A" else "")
                    _unit = pv.get("satuan","")
                    if 'WBC' in (pv.get('name') or '').upper():
                        _unit = '/µL'
                    hparts.append(f'<tr><td>{pv.get("name","")}</td><td class="result-{cls}">{val}</td><td>{pv.get("normal","")}</td><td>{_unit}</td><td class="status-{cls}">{icon} {sl}</td></tr>\n')
                    # ---- inject derived rows after relevant anchors ----
                    _nm=(pv.get('name') or '').upper()
                    if (not _anc_done) and _anc is not None and 'WBC' in _nm:
                        _anc_done=True
                        if _anc<200: _acls='critical'; _asl='AGRANULOSITOSIS'; _aicon='🔴'
                        elif _anc<500: _acls='critical'; _asl='SEVERE'; _aicon='🔴'
                        elif _anc<1000: _acls='abnormal'; _asl='MODERATE'; _aicon='🟠'
                        elif _anc<1500: _acls='abnormal'; _asl='MILD'; _aicon='🟠'
                        else: _acls='normal'; _asl='Normal'; _aicon=''
                        hparts.append(f'<tr class="anc-row"><td>💡 ANC (Neutrofil Absolut)</td><td class="result-{_acls}">{_anc:.0f}</td><td>1500 - 8000</td><td>/µL</td><td class="status-{_acls}">{_aicon} {_asl}</td></tr>\n')
                        _hint=f'Interpretasi: &lt;200 agranulositosis (very high risk infeksi); &lt;500 severe; 500-999 moderate; 1000-1499 mild neutropenia'
                        hparts.append(f'<tr class="calc-note"><td colspan="5">{_hint}</td></tr>\n')
                    if (not _absL_done) and _absL is not None and 'LYMPH' in _nm:
                        _absL_done=True
                        _alcls='normal'; _alicon=''
                        if _absL<1000: _alcls='abnormal'; _alicon='🟠'
                        elif _absL<500: _alcls='critical'; _alicon='🔴'
                        hparts.append(f'<tr class="calc-row"><td>💡 Limfosit Absolut</td><td class="result-{_alcls}">{_absL:.0f}</td><td>1000 - 4000</td><td>/µL</td><td class="status-{_alcls}">{_alicon}</td></tr>\n')
                    if (not _absM_done) and _absM is not None and 'MONO' in _nm:
                        _absM_done=True
                        hparts.append(f'<tr class="calc-row"><td>💡 Monosit Absolut</td><td class="result-normal">{_absM:.0f}</td><td>200 - 1000</td><td>/µL</td><td class="status-normal"></td></tr>\n')
                    if (not _nlr_done) and _nlr is not None and 'NEUT' in _nm:
                        _nlr_done=True
                    if (not _plr_done) and _plr is not None and 'NEUT' in _nm:
                        _plr_done=True
                    if (not _lmr_done) and _lmr is not None and 'MONO' in _nm:
                        _lmr_done=True
                    if (not _mlr_done) and _mlr is not None and 'MONO' in _nm:
                        _mlr_done=True
                    if (not _sii_done) and 'LYMPH' in _nm:
                        _sii_done=True
                    if (not _mentzer_done) and _mentzer is not None and 'MCV' in _nm:
                        _mentzer_done=True
                    if (not _rdwp_done) and _rdwp is not None and 'RDW' in _nm:
                        _rdwp_done=True
                    if (not _rpr_done) and 'HGB' in _nm or ('HB' in _nm and not _rpr_done):
                        if _rpr is not None:
                            _rpr_done=True
                            hparts.append(f'<tr class="calc-row"><td>💡 RPR (Retik×HGB/45)</td><td class="result-normal">{_rpr:.1f}</td><td>1 - 3</td><td>%</td><td class="status-normal"></td></tr>\n')
                        elif not _rpr_done:
                            _rpr_done=True
                            hparts.append(f'<tr class="calc-row"><td>💡 RPR (Retik×HGB/45)</td><td class="result-normal">—</td><td>1 - 3</td><td>%</td><td class="status-normal">data RETIC ?</td></tr>\n')
                    if (not _tsat_done) and 'TIBC' in _nm:
                        _tsat_done=True
                        if _tsat is not None:
                            _tcls='abnormal' if _tsat<20 else 'normal'; _ticon='🟠' if _tsat<20 else ''
                            hparts.append(f'<tr class="calc-row"><td>💡 Sat Transferin (Fe/TIBC)</td><td class="result-{_tcls}">{_tsat:.0f}</td><td>20 - 50</td><td>%</td><td class="status-{_tcls}">{_ticon}</td></tr>\n')
                        else:
                            hparts.append(f'<tr class="calc-row"><td>💡 Sat Transferin (Fe/TIBC)</td><td class="result-normal">—</td><td>20 - 50</td><td>%</td><td class="status-normal">data Fe/TIBC ?</td></tr>\n')
                # ============ UNIFIED "💡 RASIO" SECTION (bottom of visit panel) ============
                # Thresholds derived from THIS cohort (284 visits, pediatrik-onko RSWS):
                #   abn = cohort p75 (🟠 elevated vs ward baseline), crit = cohort p90 (🔴 markedly high)
                #   Ratios with LOW-direction abnormal (Mentzer/Shine/England) use low_abn/low_crit.
                hparts.append(f'<tr class="ratio-header"><td colspan="5">💡 RASIO HEMATOLITIK (cutoff pediatrik-onko)</td></tr>\n')
                def _ratio_row(label, val, fmt, ref, note, crit=None, abn=None, low_crit=None, low_abn=None):
                    if val is None:
                        return f'<tr class="ratio-row"><td>💡 {label}</td><td class="result-normal">—</td><td>{ref}</td><td></td><td class="status-normal"></td></tr>\n'
                    try:
                        vs = float(val)
                    except Exception:
                        return f'<tr class="ratio-row"><td>💡 {label}</td><td class="result-normal">—</td><td>{ref}</td><td></td><td class="status-normal"></td></tr>\n'
                    cls='normal'; icon=''
                    # high-direction
                    if crit is not None and vs>=crit: cls='critical'; icon='🔴'
                    elif abn is not None and vs>=abn: cls='abnormal'; icon='🟠'
                    # low-direction (wins if no high flag)
                    if cls=='normal' and low_crit is not None and vs<=low_crit: cls='critical'; icon='🔴'
                    elif cls=='normal' and low_abn is not None and vs<=low_abn: cls='abnormal'; icon='🟠'
                    return (f'<tr class="ratio-row"><td>💡 {label}</td><td class="result-{cls}">{vs:{fmt}}</td>'
                            f'<td>{ref}</td><td></td><td class="status-{cls}">{icon}</td></tr>\n'
                            + (f'<tr class="ratio-note"><td colspan="5">{note}</td></tr>\n' if note else ''))
                # Tier 1 — differential / platelet (crit=cohort p90, abn=cohort p75)
                hparts.append(_ratio_row('SIRI (NEUT×MONO/LYMPH)', _siri, '.1f', '<p90:84', 'Sistemik inflamasi/beban tumor (onko). Naik = prognosis buruk.', abn=34, crit=84))
                hparts.append(_ratio_row('AISI (PLT×NEUT×MONO/LYMPH)', _aisi, '.0f', '<p90:17239', 'Aggregate immune-inflammation. Research composite.', abn=7909, crit=17239))
                hparts.append(_ratio_row('NMR (NEUT/MONO)', _nmr, '.2f', '<p90:47', 'Neutrophil-monocyte ratio; naik = severity infeksi/sistemik.', abn=18, crit=47))
                hparts.append(_ratio_row('ELR (EO/LYMPH)', _elr, '.2f', '—', 'Eosinophil-lymphocyte; naik = alergi/parasit/eosinofilia.'))
                # Tier 2 — RBC indices
                hparts.append(_ratio_row('RLR (RDW-CV/absLYMPH)', _rlr, '.3f', '<p90:0.03', 'RDW-CV ÷ limfosit absolut; prognostic sepsis/onko.', abn=0.02, crit=0.03))
                hparts.append(_ratio_row('Shine & Lal (MCV²×RDW/100HGB)', _shine, '.1f', 'AI<87 / Thal>166', 'Bedakan anemia defisiensi besi (AI, rendah) vs thalasemia (tinggi).', abn=126, crit=166, low_abn=87, low_crit=54))
                hparts.append(_ratio_row('England-Frazer (MCV−10×MCH)', _engf, '.1f', 'AI<-182 (neg)', 'Screen defisiensi besi: semakin negatif = makin AI. (ref p25:-204)', abn=-182, low_abn=-182, low_crit=-251))
                hparts.append(_ratio_row('HCR (HCT/HGB)', _hcr, '.2f', '~3.0 (p90:3.16)', 'Sferositosis >3.6; thalassemia trait <3.0.', abn=3.16, crit=3.3))
                hparts.append(_ratio_row('RDW/MCV', _rdwmcv, '.2f', '<p90:0.25', 'Anisositosis relatif vs ukuran eritrosit.', abn=0.21, crit=0.25))
                # Tier 3 — NRBC
                if _absnrbc is not None:
                    hparts.append(_ratio_row('absNRBC (NRBC#/100×WBC)', _absnrbc, '.0f', 'ada = abnormal', 'Nukleated eritrosit beredar = marrow stress / infiltrasi. Ada = 🟠.', abn=1))
                elif _nrbc_h is not None:
                    hparts.append(f'<tr class="ratio-row"><td>💡 NRBC% (present)</td><td class="result-abnormal">{_nrbc_h:.2f}</td><td>0</td><td>%</td><td class="status-abnormal">🟠</td></tr>\n')
                    hparts.append(f'<tr class="ratio-note"><td colspan="5">NRBC% &gt;0 = marrow stress / pelepasan prekursor (onko-metastatik, hipoksia). absNRBC perlu WBC.</td></tr>\n')
                # existing derived ratios consolidated here too (cohort thresholds)
                hparts.append(_ratio_row('NLR (NEUT/LYMPH)', _nlr, '.2f', '<p90:13.4', 'NLR naik = stres/infeksi/peradangan sistemik.', abn=6, crit=13))
                hparts.append(_ratio_row('PLR (PLT/absLYMPH)', _plr, '.0f', '<p90:330', 'Platelet-lymphocyte; naik = inflamasi/prognosis.', abn=214, crit=330))
                hparts.append(_ratio_row('LMR (LYMPH/MONO)', _lmr, '.2f', '—', 'Lymphocyte-monocyte ratio.'))
                hparts.append(_ratio_row('MLR (MONO/LYMPH)', _mlr, '.2f', '<p90:1.22', 'Monocyte-lymphocyte; naik = inflamasi.', abn=0.57, crit=1.22))
                hparts.append(_ratio_row('SII (PLT×NEUT/LYMPH ×1000)', _sii, '.0f', '<p90:3.29M', 'Sistemik inflamasi (onko/prognosis). PLT absolut ×1000.', abn=1213642, crit=3294912))
                hparts.append(_ratio_row('Mentzer (MCV/RBC)', _mentzer, '.1f', 'AI>19 / Thal<19', 'Thalasemia <13; AI 13-15; anemi kronik >15. Ward median 22.', abn=25, crit=31, low_abn=19, low_crit=13))
                hparts.append(_ratio_row('RDW/PLT Ratio (×100)', _rdwp, '.1f', '<p90:67', 'Anisositosis ÷ trombosit; composite stres marrow+inflamasi.', abn=27, crit=67))
                hparts.append(f'</table>\n</div>\n')

            hparts.append(f'</div></div>\n')
        hparts.append('</div></div>\n')
    hparts.append('</div></div>\n')

html = f'''<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Rekap Lab Hemato {len(patients)} Pasien - {today}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f5f6fa;color:#333;}}
.header{{background:#1a237e;color:#fff;padding:16px 20px;position:sticky;top:0;z-index:100;}}
.header h1{{font-size:18px;}}.header small{{font-size:12px;opacity:0.8;}}
.nav-bar{{background:#283593;padding:8px 12px;position:sticky;top:56px;z-index:99;display:flex;gap:4px;overflow-x:auto;white-space:nowrap;scrollbar-width:thin;align-items:center;}}
.nav-bar a{{color:#e8eaf6;text-decoration:none;font-size:11px;padding:4px 8px;border-radius:4px;flex-shrink:0;}}
.nav-bar a:hover{{background:#5c6bc0;color:#fff;}}
.nav-btn{{background:#3949ab;color:#fff;border:none;padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer;flex-shrink:0;}}
.nav-btn:hover{{background:#5c6bc0;}}
.nav-status{{color:#ffeb3b;font-size:10px;margin-left:8px;flex-shrink:0;}}
.container{{max-width:1000px;margin:0 auto;padding:12px;}}
.search-box{{margin-bottom:12px;}}
.search-box input{{width:100%;padding:8px 12px;border:1px solid #ccc;border-radius:6px;font-size:14px;outline:none;}}
.search-box input:focus{{border-color:#3f51b5;}}
.loc-section{{margin:16px 0;}}
.loc-title{{font-size:15px;font-weight:700;color:#1a237e;padding:8px 12px;border-left:4px solid #3f51b5;border-radius:6px 6px 0 0;cursor:pointer;display:flex;align-items:center;gap:8px;}}
.loc-toggle{{font-size:11px;color:#666;margin-left:auto;transition:transform 0.2s;}}
.loc-title.collapsed .loc-toggle{{transform:rotate(-90deg);}}
.loc-body{{display:block;}}
.loc-body.collapsed{{display:none;}}
.kamar-section{{margin:8px 0 8px 12px;border-left:2px dashed #c5cae9;}}
.kamar-title{{font-size:13px;font-weight:600;color:#37474f;padding:6px 10px;background:#eceff1;border-radius:4px;cursor:pointer;display:flex;align-items:center;gap:8px;}}
.kamar-toggle{{font-size:10px;color:#607d8b;margin-left:auto;transition:transform 0.2s;}}
.kamar-title.collapsed .kamar-toggle{{transform:rotate(0deg);}}
.kamar-title:not(.collapsed) .kamar-toggle{{transform:rotate(90deg);}}
.kamar-body{{display:block;padding-left:4px;}}
.kamar-body.collapsed{{display:none;}}
.count{{font-size:11px;color:#666;font-weight:400;}}
.patient-card{{background:#fff;margin:6px 0;box-shadow:0 1px 3px rgba(0,0,0,0.08);border-radius:6px;border:1px solid #e0e0e0;}}
.patient-header{{padding:10px 12px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;background:#fafafa;border-radius:6px;}}
.patient-header:hover{{background:#f0f0f0;}}
.patient-name{{font-weight:700;font-size:13px;color:#2c3e50;}}
.patient-name .room{{color:#e65100;font-weight:600;font-size:12px;}}
.patient-meta{{font-size:11px;color:#616161;margin-top:3px;line-height:1.4;}}
.status-badge{{padding:1px 6px;border-radius:3px;font-size:9px;font-weight:700;}}
.status-kjs{{background:#e8eaf6;color:#283593;}}
.status-leader{{background:#fbe9e7;color:#d84315;}}
.toggle-icon{{font-size:12px;color:#999;transition:transform 0.2s;}}
.open .toggle-icon{{transform:rotate(90deg);}}
.patient-body{{display:none;padding:10px;border-top:1px solid #eee;}}.open .patient-body{{display:block;}}
.note-box{{background:#fffde7;border:1px solid #f9e79f;border-radius:6px;padding:8px;margin:8px 0;}}
.note-label{{font-size:11px;font-weight:600;color:#8a6d3b;display:block;margin-bottom:4px;}}
.note-input{{width:100%;min-height:48px;border:1px solid #d4ac0d;border-radius:4px;padding:6px;font-size:12px;resize:vertical;font-family:inherit;background:#fff;}}
.note-input:focus{{outline:none;border-color:#b7950b;}}
.special-toggle{{margin:10px 0;}}
.special-btn{{background:#e65100;color:#fff;border:none;padding:8px 14px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;width:100%;text-align:left;}}
.special-btn:hover{{background:#bf4600;}}
.special-result{{margin-top:8px;padding:10px;background:#f9f9f9;border-radius:6px;border:1px solid #e0e0e0;}}
.sp-section{{margin:8px 0;padding:8px;border-left:3px solid #e91e63;background:#fff;border-radius:4px;}}
.sp-section h4{{font-size:12px;margin-bottom:4px;color:#c2185b;}}
.sp-item{{font-size:11px;padding:3px 0;border-bottom:1px dashed #eee;line-height:1.4;}}
.badge{{background:#e91e63;color:#fff;padding:1px 5px;border-radius:8px;font-size:9px;}}
.empty{{color:#999;font-style:italic;font-size:11px;}}
.no-lab-alert{{background:#fff3e0;border:1px solid #ffe0b2;color:#e65100;padding:8px 12px;border-radius:4px;font-size:11px;margin:6px 0;}}
.visit-tabs{{display:flex;gap:4px;margin:8px 0;flex-wrap:wrap;align-items:center;}}
.visit-tab{{padding:4px 8px;border-radius:6px;font-size:10px;cursor:pointer;background:#e0e0e0;text-align:center;line-height:1.2;}}
.visit-tab.active{{background:#3f51b5;color:#fff;font-weight:600;}}
.visit-tab:hover{{background:#c5cae9;}}
.load-all-btn{{background:#eceff1;color:#455a64;border:1px solid #cfd8dc;padding:4px 8px;border-radius:6px;font-size:10px;font-weight:600;cursor:pointer;}}
.load-all-btn:hover{{background:#cfd8dc;}}
.visit-panel{{display:none;margin-top:6px;}}.visit-panel.active{{display:block;}}
table{{width:100%;border-collapse:collapse;font-size:11px;background:#fff;border-radius:4px;overflow:hidden;}}
th{{background:#37474f;color:#fff;padding:6px 8px;text-align:left;font-weight:600;}}
td{{padding:5px 8px;border-bottom:1px solid #f0f0f0;}}
.anc-row{{background:#e8f5e9;border-left:3px solid #2e7d32;}}
.anc-row td{{font-weight:600;}}
.calc-row{{background:#e3f2fd;border-left:3px solid #1565c0;}}
.calc-row td{{font-weight:600;font-style:italic;}}
.calc-note{{background:#f5f5f5;}}
.calc-note td{{font-size:10px;color:#555;font-style:italic;padding:3px 8px;border-bottom:1px dashed #ddd;}}
.ratio-row{{background:#ede7f6;border-left:3px solid #5e35b1;}}
.ratio-row td{{font-weight:600;font-style:italic;}}
.ratio-note{{background:#f3e5f5;}}
.ratio-note td{{font-size:10px;color:#555;font-style:italic;padding:3px 8px;border-bottom:1px dashed #ddd;}}
.ratio-header{{background:#4527a0;color:#fff;font-weight:700;font-size:11px;padding:5px 8px;letter-spacing:0.5px;}}
.ratio-header td{{color:#fff;}}
.result-critical{{color:#c62828;font-weight:700;}}
.result-abnormal{{color:#e65100;font-weight:600;}}
.result-normal{{color:#2e7d32;}}
.status-critical{{color:#c62828;font-weight:700;}}
.status-abnormal{{color:#e65100;font-weight:600;}}
.status-normal{{color:#2e7d32;}}
</style>
</head>
<body>
<div class="header">
  <h1>🏥 Rekap Lab Hematoonkologi {len(patients)} Pasien — Agustus 2026</h1>
  <small>{today} {tm} WITA | 📝 Catatan tersimpan permanen (server) | 🟠=Bermakna 🔴=Kritis</small>
</div>
<div class="nav-bar">
  <a href="#" onclick="filterLoc('all')">📋 Semua</a>
  <a href="#" onclick="filterLoc('PICU')">🟣 PICU</a>
  <a href="#" onclick="filterLoc('Lantai 5')">🟢 Lantai 5</a>
  <a href="#" onclick="filterLoc('Lantai 6')">🔵 Lantai 6</a>
  <a href="#" onclick="filterLoc('Lantai 7')">🟠 Lantai 7</a>
  <a href="#" onclick="filterLoc('IRD')">🌸 IRD</a>
  <a href="#" onclick="filterLoc('PJT')">🟡 PJT</a>
  <a href="#" onclick="filterLoc('PCC')">🔷 PCC</a>
  <button class="nav-btn" onclick="saveAll()">💾 Simpan Semua</button>
  <span class="nav-status" id="saveStatus"></span>
</div>
<div class="container">
  <div class="search-box" style="position:relative">
    <input type="text" id="search" placeholder="🔍 Cari nama/NORM/kamar/diagnosis..." autocomplete="off" oninput="onSearchInput(this.value)" onfocus="onSearchInput(this.value)" onblur="setTimeout(()=>{{document.getElementById('searchDropdown').style.display='none'}},200)">
    <div id="searchDropdown" style="display:none;position:absolute;top:100%;left:0;right:0;background:#fff;border:1px solid #ccc;border-radius:0 0 8px 8px;box-shadow:0 4px 12px rgba(0,0,0,0.12);max-height:320px;overflow-y:auto;z-index:200"></div>
  </div>
  <div id="soloBanner" style="display:none;align-items:center;justify-content:space-between;background:#e8eaf6;border:1px solid #c5cae9;border-radius:8px;padding:8px 12px;margin:10px 0;font-size:13px;color:#283593">
    <span id="soloText" style="font-weight:600"></span>
    <button onclick="exitSolo()" style="background:#3949ab;color:#fff;border:none;padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer;white-space:nowrap">✕ Tampilkan semua</button>
  </div>
  {''.join(hparts)}
</div>
<script>
var NOTES={{}};var SAVE_TIMER=null;
function normOf(t){{var c=t.closest('.patient-card');return c?c.dataset.norm:'';}}
function loadNotes(){{fetch('/api/notes').then(r=>r.json()).then(function(data){{NOTES=data||{{}};document.querySelectorAll('.note-input').forEach(function(t){{var n=t.dataset.norm;if(n&&NOTES[n])t.value=NOTES[n];}});setStatus('✅ Catatan dimuat ('+Object.keys(NOTES).length+')');}}).catch(function(){{setStatus('⚠️ Server catatan tidak bisa diakses — catatan tdk tersimpan permanen');}});}}
function noteDirty(t){{clearTimeout(SAVE_TIMER);SAVE_TIMER=setTimeout(function(){{saveOne(t);}},800);}}
function saveOne(t){{var n=t.dataset.norm;if(!n)return;NOTES[n]=t.value;setStatus('Menyimpan...');fetch('/api/notes',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(NOTES)}}).then(r=>r.json()).then(function(){{setStatus('✅ Tersimpan '+Object.keys(NOTES).length);}}).catch(function(){{setStatus('⚠️ Gagal simpan');}});}}
function saveAll(){{document.querySelectorAll('.note-input').forEach(function(t){{NOTES[t.dataset.norm]=t.value;}});setStatus('Menyimpan semua...');fetch('/api/notes',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(NOTES)}}).then(r=>r.json()).then(function(){{setStatus('✅ Semua tersimpan ('+Object.keys(NOTES).length+')');}}).catch(function(){{setStatus('⚠️ Gagal simpan');}});}}
function setStatus(s){{var el=document.getElementById('saveStatus');if(el)el.textContent=s;}}
function toggleLoc(t){{t.classList.toggle('collapsed');var b=t.parentElement.querySelector('.loc-body');if(b)b.classList.toggle('collapsed');}}
function toggleKamar(t){{t.classList.toggle('collapsed');var b=t.parentElement.querySelector('.kamar-body');if(b)b.classList.toggle('collapsed');}}
function togglePatient(h){{var card=h.parentElement;var wasOpen=card.classList.contains('open');card.classList.toggle('open');if(!wasOpen){{var btn=card.querySelector('.lab-load-btn');if(btn&&card.querySelector('.lab-placeholder')&&card.querySelector('.lab-placeholder').dataset.loaded!=='1') btn.click();}}}}
function showVisit(t,p){{var c=t.closest('.patient-card');c.querySelectorAll('.visit-tab').forEach(x=>x.classList.remove('active'));c.querySelectorAll('.visit-panel').forEach(x=>x.classList.remove('active'));t.classList.add('active');var target=document.getElementById(p);if(target)target.classList.add('active');}}
var MASTER_INDEX = [];
var SOLO_NORM=null;
(function buildMasterIndex(){{
  document.querySelectorAll('.patient-card').forEach(function(c){{
    MASTER_INDEX.push({{norm:c.dataset.norm||'',text:(c.textContent||'').toLowerCase(),el:c}});
  }});
}})();
function exitSolo(){{
  SOLO_NORM=null;
  document.querySelectorAll('.patient-card').forEach(function(c){{c.style.display='block';}});
  document.querySelectorAll('.loc-section').forEach(function(s){{s.style.display='block';}});
  document.querySelectorAll('.kamar-section').forEach(function(s){{s.style.display='block';}});
  var b=document.getElementById('soloBanner'); if(b) b.style.display='none';
  var inp=document.getElementById('search'); if(inp) inp.value='';
  var dd=document.getElementById('searchDropdown'); if(dd) dd.style.display='none';
}}
function exitSoloKeepQuery(){{
  SOLO_NORM=null;
  document.querySelectorAll('.patient-card').forEach(function(c){{c.style.display='block';}});
  document.querySelectorAll('.loc-section').forEach(function(s){{s.style.display='block';}});
  document.querySelectorAll('.kamar-section').forEach(function(s){{s.style.display='block';}});
  var b=document.getElementById('soloBanner'); if(b) b.style.display='none';
}}
function onSearchInput(v){{
  if(SOLO_NORM) exitSoloKeepQuery();
  var dd=document.getElementById('searchDropdown');
  var q=(v||'').trim().toLowerCase();
  if(!q){{dd.style.display='none';document.querySelectorAll('.patient-card').forEach(function(c){{c.style.display='block';}});document.querySelectorAll('.loc-section').forEach(function(s){{s.style.display='block';}});document.querySelectorAll('.kamar-section').forEach(function(s){{s.style.display='block';}});return;}}
  var hits=MASTER_INDEX.filter(function(m){{return m.text.includes(q);}}).slice(0,8);
  if(!hits.length){{dd.innerHTML='<div style="padding:10px;color:#999;font-size:12px">Tidak ada hasil</div>';dd.style.display='block';return;}}
  dd.innerHTML=hits.map(function(m){{
    var card=m.el;var titleEl=card.querySelector('.patient-name');var title=titleEl?titleEl.textContent.trim():'';
    var norm=m.norm;var kamar=card.querySelector('.room')?card.querySelector('.room').textContent:'';
    return '<div data-norm="'+norm+'" onclick="scrollToPatient(this.dataset.norm)" style="padding:8px 12px;border-bottom:1px solid #eee;cursor:pointer;display:flex;justify-content:space-between;align-items:center"><div><div style="font-weight:600;font-size:13px">'+esc(title)+'</div><div style="font-size:11px;color:#666">'+esc(norm)+' '+esc(kamar)+'</div></div><span style="font-size:11px;color:#3f51b5">buka →</span></div>';
  }}).join('');
  dd.style.display='block';
  document.querySelectorAll('.patient-card').forEach(function(c){{c.style.display=c.textContent.toLowerCase().includes(q)?'block':'none';}});
  // hide empty loc/kamar sections while filtering
  document.querySelectorAll('.kamar-section').forEach(function(ks){{var vis=false; ks.querySelectorAll('.patient-card').forEach(function(c){{if(c.style.display!=='none') vis=true;}}); ks.style.display=vis?'block':'none';}});
  document.querySelectorAll('.loc-section').forEach(function(ls){{var vis=false; ls.querySelectorAll('.patient-card').forEach(function(c){{if(c.style.display!=='none') vis=true;}}); ls.style.display=vis?'block':'none';}});
}}
function scrollToPatient(norm){{
  var card=document.querySelector('.patient-card[data-norm="'+norm+'"]');
  if(!card) return;
  document.getElementById('searchDropdown').style.display='none';
  SOLO_NORM=norm;
  document.querySelectorAll('.patient-card').forEach(function(c){{c.style.display=(c.dataset.norm===norm?'block':'none');}});
  document.querySelectorAll('.kamar-section').forEach(function(ks){{var has=!!ks.querySelector('.patient-card[data-norm="'+norm+'"]'); ks.style.display=has?'block':'none';}});
  document.querySelectorAll('.loc-section').forEach(function(ls){{var has=!!ls.querySelector('.patient-card[data-norm="'+norm+'"]'); ls.style.display=has?'block':'none';}});
  var banner=document.getElementById('soloBanner');
  var nameEl=card.querySelector('.patient-name');
  if(banner){{banner.style.display='flex'; var txt=document.getElementById('soloText'); if(txt) txt.textContent='👁️ Fokus: '+(nameEl?nameEl.textContent.trim():'')+' (RM '+norm+')';}}
  var locBody=card.closest('.loc-body'); if(locBody&&locBody.classList.contains('collapsed')){{locBody.classList.remove('collapsed');locBody.previousElementSibling&&locBody.previousElementSibling.classList.remove('collapsed');}}
  var kamarBody=card.closest('.kamar-body'); if(kamarBody&&kamarBody.classList.contains('collapsed')){{kamarBody.classList.remove('collapsed');kamarBody.previousElementSibling&&kamarBody.previousElementSibling.classList.remove('collapsed');}}
  if(!card.classList.contains('open')) card.classList.add('open');
  var btn=card.querySelector('.lab-load-btn');
  if(btn && card.querySelector('.lab-placeholder') && card.querySelector('.lab-placeholder').dataset.loaded!=='1') btn.click();
  setTimeout(function(){{card.scrollIntoView({{behavior:'smooth',block:'center'}});}},80);
  card.style.outline='2px solid #3f51b5'; setTimeout(function(){{card.style.outline='';}},1800);
}}
function filterSearch(v){{onSearchInput(v);}}
function filterLoc(l){{if(SOLO_NORM) exitSoloKeepQuery(); var q=(l||'').toLowerCase();document.querySelectorAll('.loc-section').forEach(function(s){{var t=(s.querySelector('.loc-title').textContent||'').toLowerCase();if(l==='all'){{s.style.display='block'}}else{{s.style.display=t.includes(q)?'block':'none'}}}});var dd=document.getElementById('searchDropdown');if(dd)dd.style.display='none';}}

function loadLab(btn,norm,pid){{
  var wrap=btn.parentElement; var content=wrap.querySelector('.lab-content');
  if(wrap.dataset.loaded==='1'){{content.style.display=content.style.display==='none'?'block':'none';return;}}
  btn.textContent='⏳ Memuat...'; btn.disabled=true;
  fetch('/api/patient/'+norm).then(function(r){{return r.json();}}).then(function(d){{
    if(d&&d.visits&&d.visits.length){{renderLabVisits(content,pid,d.visits);wrap.dataset.loaded='1'; content.style.display='block'; btn.style.display='none';return;}}
    throw new Error('empty');
  }}).catch(function(e){{
    fetch('/api/lookup/'+norm).then(function(r){{return r.json();}}).then(function(d2){{
      if(!d2||!d2.visits||!d2.visits.length){{btn.textContent='⚠️ Gagal muat';btn.disabled=false;return;}}
      renderLabVisits(content,pid,d2.visits);
      wrap.dataset.loaded='1'; content.style.display='block'; btn.style.display='none';
    }}).catch(function(){{btn.textContent='⚠️ Gagal muat';btn.disabled=false;}});
  }});
}}
function renderLabVisits(container,pid,visits){{
  var RENDER_N=5;
  container.innerHTML='';
  var tabs=document.createElement('div');
  tabs.className='visit-tabs'; tabs.id='tabs-'+pid;
  visits.slice(0,RENDER_N).forEach(function(v,vi){{
    var tab=document.createElement('div');
    tab.className='visit-tab'+(vi===0?' active':'');
    var tgl=(v.tgl||'?').slice(0,16);
    tab.innerHTML='Kunj '+(vi+1)+'<br><small>'+tgl+'</small>';
    tab.onclick=function(){{showVisit(this,'h'+pid+'-'+vi);}};
    tabs.appendChild(tab);
  }});
  if(visits.length>RENDER_N){{
    var btn=document.createElement('button');
    btn.className='load-all-btn'; btn.id='btn-'+pid;
    btn.textContent='+'+(visits.length-RENDER_N)+' kunjungan lagi \u25BE';
    (function(n,pp){{btn.onclick=function(){{loadAll(n,pp);}};}})(container.parentElement.dataset.norm,pid);
    tabs.appendChild(btn);
  }}
  container.appendChild(tabs);
  visits.slice(0,RENDER_N).forEach(function(v,vi){{
    var panel=document.createElement('div');
    panel.className='visit-panel'+(vi===0?' active':''); panel.id='h'+pid+'-'+vi;
    var html='<table><tr><th>Parameter</th><th>Hasil</th><th>N Normal</th><th>Satuan</th><th>Status</th></tr>';
    (v.params||[]).forEach(function(pv){{html+='<tr><td>'+(pv.name||'')+'</td><td>'+(pv.hasil||'')+'</td><td>'+(pv.normal||'')+'</td><td>'+(pv.satuan||'')+'</td><td></td></tr>';}});
    html+='</table>';
    panel.innerHTML=html;
    container.appendChild(panel);
  }});
}}
function loadAll(norm,pid){{
  var btn=document.getElementById('btn-'+pid);
  if(btn)btn.textContent='Memuat...';
  fetch('/api/patient/'+norm).then(function(r){{return r.json();}}).then(function(d){{
    if(!d||!d.visits){{if(btn)btn.textContent='Gagal muat';return;}}
    var tabs=document.getElementById('tabs-'+pid);
    var card=tabs.closest('.patient-card');
    var body=card.querySelector('.patient-body');
    d.visits.forEach(function(v,vi){{
      if(vi<5)return;
      var tab=document.createElement('div');
      tab.className='visit-tab';
      tab.setAttribute('onclick',"showVisit(this,'h"+pid+"-"+vi+"')");
      var tgl=v.tgl?(v.tgl.length>=16?v.tgl.slice(0,16):v.tgl.slice(0,10)):'?';
      tab.innerHTML='Kunj '+(vi+1)+'<br><small>'+tgl+'</small>';
      tabs.insertBefore(tab,btn);
      
      var panel=document.createElement('div');
      panel.className='visit-panel';
      panel.id='h'+pid+'-'+vi;
      var html='<table><tr><th>Parameter</th><th>Hasil</th><th>N Normal</th><th>Satuan</th><th>Status</th></tr>';
      (v.params||[]).forEach(function(pv){{
        html+='<tr><td>'+(pv.name||'')+'</td><td>'+(pv.hasil||'')+'</td><td>'+(pv.normal||'')+'</td><td>'+(pv.satuan||'')+'</td><td></td></tr>';
      }});
      html+='</table>';
      panel.innerHTML=html;
      body.appendChild(panel);
    }});
    if(btn)btn.style.display='none';
  }}).catch(function(e){{if(btn)btn.textContent='Gagal muat';}});
}}

function toggleSpecial(btn,norm){{
  var box=btn.parentElement.querySelector('.special-result');
  if(box.style.display==='none'){{
    if(!box.dataset.loaded){{
      btn.textContent='Memuat...';
      fetch('/api/special/'+norm).then(r=>r.json()).then(function(d){{
        if(!d.success){{box.innerHTML='<div class="empty">'+d.error+'</div>';box.style.display='block';btn.textContent='📂 Sembunyikan';return;}}
        var h='';
        var secs={{'pa':'🔬 PA','rad':'📡 Radiologi','bmp':'🩸 BMP','lcs':'🧠 LCS','immuno':'🧬 Immunofenotyping','ihc':'🎯 IHC'}};
        for(var k in secs){{
          var arr=d[k]||[];
          if(!arr.length){{continue;}}
          h+='<div class="sp-section"><h4>'+secs[k]+' <span class="badge">'+arr.length+'</span></h4>';
          for(var i=0;i<arr.length;i++){{var x=arr[i];h+='<div class="sp-item">'+((x.tanggal||'?'))+' | '+((x.kesimpulan||x.kesan||x.jaringan||'(hasil)')+'')+'</div>';}}
          h+='</div>';
        }}
        if(!h)h='<div class="empty">Tidak ada data penunjang special di SIMRS</div>';
        box.innerHTML=h;box.dataset.loaded='1';
      }}).catch(function(e){{box.innerHTML='<div class="empty">Error: '+e+'</div>';}});
    }}
    box.style.display='block';btn.textContent='📂 Sembunyikan PA / Rad / BMP / LCS / Immuno / IHC';
  }}else{{box.style.display='none';btn.textContent='📂 Tampilkan PA / Rad / BMP / LCS / Immuno / IHC';}}
}}
document.addEventListener('keydown',function(e){{if(e.key==='/'&&!e.ctrlKey&&!e.metaKey){{e.preventDefault();document.getElementById('search').focus();}}}});
loadNotes();
</script>
</body></html>'''

out_path = f"/home/lenovo/Sync/Seno/Hermes-Outputs/Rekap Lab Hemato {len(patients)} Pasien - {today}.html"
with open(out_path, 'w') as f:
    f.write(html)
print(f"HTML generated: {out_path} ({len(patients)} patients)", flush=True)
