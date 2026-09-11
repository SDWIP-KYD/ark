#!/usr/bin/env python3
"""Hemato notes server — serves report HTML + persistent notes API.
OPTIMIZED: in-memory cache + gzip + Cache-Control. Full lab data preserved.
"""
import json, os, gzip, threading, sys, time, uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import pickle

# Arbitrary-RM full lookups run as background jobs so Cloudflare never holds
# one HTTP request open for >100s. Jobs are process-local and expire after 1h.
_lookup_jobs = {}
_lookup_jobs_lock = threading.Lock()

def _run_full_lookup_job(job_id, norm, with_special, use_cache=True):
    try:
        import hematology_lookup as HL
        result = HL.lookup(norm, include_special=with_special, quick=False, use_cache=use_cache)
        with _lookup_jobs_lock:
            _lookup_jobs[job_id].update(status='done', result=result, finished_at=time.time())
    except Exception as e:
        with _lookup_jobs_lock:
            _lookup_jobs[job_id].update(status='error', error=str(e), finished_at=time.time())

OUT = '/home/lenovo/Sync/Seno/Hermes-Outputs'
NOTES_FILE = os.path.join(OUT, 'catatan_hemato.json')
PICKLE = '/tmp/simrs_15agustus_full.pkl'
FLAG_FILE = '/home/lenovo/.hermes/scripts/feature_flags.json'
PORT = 8788
sys.path.insert(0, '/home/lenovo/.hermes/scripts')

def lookup_enabled():
    try:
        return json.load(open(FLAG_FILE)).get('lookup_enabled', False)
    except Exception:
        return False

_html_cache = {'path': None, 'mtime': 0, 'data': None, 'lock': threading.Lock()}

# Load injection snippet (button + modal CSS + JS) once at startup
_INJECT_CSS = ''
_INJECT_HTML = ''
try:
    inj_path = os.path.join(os.path.dirname(__file__), 'update_master_inject.html')
    with open(inj_path, 'r') as f:
        _INJECT_CSS = f.read()
except: pass

def inject_update_master_button(html):
    """Inject hamburger menu + sidebar into the served HTML. Accepts bytes or str."""
    if isinstance(html, bytes):
        html = html.decode('utf-8', errors='replace')
    if not _INJECT_CSS: return html.encode('utf-8')
    
    # Hamburger button + sidebar structure
    body_inject = """<button class="hamburger-btn" onclick="toggleSidebar()">☰</button>
<div id="sidebarOverlay" class="sidebar-overlay" onclick="closeSidebar()"></div>
<div id="mainSidebar" class="sidebar">
  <div class="sidebar-header">
    Menu
    <button class="sidebar-close" onclick="closeSidebar()">×</button>
  </div>
  <div class="sidebar-menu">
    <div class="sidebar-item" onclick="openMasterModal()">
      <span class="sidebar-item-icon">📋</span>
      Update Master List
    </div>
    <div class="sidebar-item" onclick="refreshAllLab()">
      <span class="sidebar-item-icon">🔄</span>
      Refresh All Lab
    </div>
    <div class="sidebar-item" onclick="showNotifications()" style="position:relative;">
      <span class="sidebar-item-icon">🔔</span>
      Notifikasi
      <span class="notif-badge" style="display:none;">0</span>
    </div>
  </div>
</div>

<!-- Notification Modal -->
<div id="notifModal" class="notif-panel-modal">
  <div class="notif-header">
    🔔 Job Status
    <button class="notif-close" onclick="closeNotifications()">×</button>
  </div>
  <div id="notifBody" class="notif-body"></div>
</div>"""
    # Add modal styles BEFORE the first closing </style> so they render as CSS
    if '</style>' in html:
        html = html.replace('</style>', _INJECT_CSS + '\n</style>', 1)
    elif '</head>' in html:
        html = html.replace('</head>', '<style>' + _INJECT_CSS + '</style>\n</head>', 1)
    # Add the Update Master List button right after <body> if present
    if body_inject and '<body' in html:
        html = html.replace('<body>', '<body>\n' + body_inject, 1)
        if html.count(body_inject) > 1:
            # dedupe — only one button
            first = html.find(body_inject)
            second = html.find(body_inject, first + 1)
            if second != -1:
                html = html[:second] + html[second + len(body_inject):]
    # Append modal + JS before </body>
    modal_js = """
<!-- Update Master List Modal -->
<div class="modal-overlay" id="masterModal">
<div class="modal">
  <div class="modal-header">
    <h2>📋 Update Master Sensus</h2>
    <button class="modal-close" onclick="closeMasterModal()">&times;</button>
  </div>
  <div class="modal-body">
    <p class="body-label" style="margin-bottom:12px; color:#e65100; font-weight:600;">Paste teks sensus pasien dari WA/residen (format: Nama / NORM / TTL / DX / Status per baris)</p>
    <textarea id="sensusInput" placeholder="Contoh:
MOCHIL LANTAI 5
1. (506.3). Aisya Sidiqia / 1339986 / 11 April 2022 / Leukemia L1 / KJS

PICU
1. (Bed 1) Alisha Irfan / 1690930 / 20 September 2022 / Neurofibromatosis / KJS"></textarea>
    <div class="actions">
      <button class="btn-parse" onclick="parseSensus()">🔍 Parse Sensus</button>
    </div>
    <div id="parseStatus" style="margin-top:10px; font-size:13px; min-height:20px;"></div>
    <div id="previewArea"></div>
    <div id="dischargedArea"></div>
  </div>
  <div class="modal-footer">
    <div id="footerStatus" style="font-size:13px; color:#666;">Paste sensus & klik "Parse"</div>
    <div style="display:flex; gap:8px; align-items:center;">
      <label id="confirmCheckWrap" style="display:none; font-size:13px; margin-right:8px; cursor:pointer;"><input type="checkbox" id="confirmCheck" style="margin-right:4px;"> Saya sudah cek datanya</label>
      <button class="btn-cancel" onclick="closeMasterModal()">Batal</button>
      <button class="btn-apply" id="btnApply" disabled onclick="applySensus()">✅ Apply & Rebuild</button>
    </div>
  </div>
</div>
</div>
<script>
// ====== Update Master List Modal ======
var ParsedPatients = [];
var DischargedList = [];
var BaruList = [], BerubahList = [], TetapList = [], UnclearList = [];

function openMasterModal() {
  document.getElementById('masterModal').classList.add('active');
  document.getElementById('sensusInput').value = '';
  document.getElementById('parseStatus').textContent = '';
  document.getElementById('previewArea').innerHTML = '';
  document.getElementById('dischargedArea').innerHTML = '';
  document.getElementById('btnApply').disabled = true;
  document.getElementById('footerStatus').textContent = 'Paste sensus & klik "Parse"';
  ParsedPatients = [];
  DischargedList = [];
}
function closeMasterModal() {
  document.getElementById('masterModal').classList.remove('active');
}
function esc(s) {
  var d = document.createElement('div');
  d.appendChild(document.createTextNode(s || ''));
  return d.innerHTML;
}
function parseSensus() {
  var text = document.getElementById('sensusInput').value.trim();
  if (!text) { alert('Isi dulu teks sensusnya'); return; }
  var status = document.getElementById('parseStatus');
  status.textContent = '⏳ Parsing...';
  var parsed = [];
  var currentFloor = '';
  var currentRoom = '';
  var lines = text.split('\\n');
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim();
    // Floor header
    var allCapsLine = line.replace(/\\s+/g,' ').toUpperCase();
    if (allCapsLine.match(/^(LANTAI|MOCHIL|PICU|PCC|PJT|ICU|IRD|HCU|PINANG|LONTARA|PALEM|CARDIO|NON KEMO|RUANG)\\s*/i) && line.length < 50 && !line.match(/\\/(\\d{3,})/) && !line.match(/^(\\d+[\\.\\)])/)) {
      currentFloor = line.replace(/\\s+/g,' ').trim();
      currentRoom = '';
      continue;
    }
    // Room/bed headers
    var rm = line.match(/^\\((\\d+)\\.\\d+\\)\\.?$/i);
    if (rm && line.length < 20) { currentRoom = 'Kamar ' + rm[1]; continue; }
    var bm = line.match(/^\\(Bed\\s*\\d+\\)$/i);
    if (bm && line.length < 20) { currentRoom = 'Bed ' + line.replace(/[()]/g,'').trim(); continue; }
    var cm = line.match(/^(KAMAR)\\s+(\\d+)$/i);
    if (cm && line.length < 30 && !line.match(/\\/|\\d{5,}/)) { currentRoom = 'Kamar ' + cm[2]; continue; }
    var iso = line.match(/^KAMAR\\s+(ISO|ISOLASI)$/i);
    if (iso && line.length < 30) { currentRoom = 'Kamar ' + iso[1].toUpperCase(); continue; }
    if (!line || line === '-' || line.match(/^[-•–—]+$/)) continue;
    // Patient entries — robust split-based approach
    var bed = '', room = '', name = '', norm = '', ttl = '', dx = '', st = '';
    // strip leading "Number." or "Number)"
    var entryLine = line.replace(/^\\s*\\d+[\\.\\)]\\s*/, '');
    // inline (506.3) / (508.2) room.bed -> Kamar/room, bed
    var rb = entryLine.match(/^\\((\\d+)\\.\\s*(\\d+)\\)\\.?\\s*/);
    if (rb) { room = 'Kamar ' + rb[1]; bed = rb[2]; entryLine = entryLine.slice(rb[0].length); }
    // inline (Bed 1) -> bed
    if (!rb) {
      var bd = entryLine.match(/^\\(?\\s*[Bb]ed\\s*(\\d+)\\)?\\s*/);
      if (bd) { bed = bd[1]; entryLine = entryLine.slice(bd[0].length); }
    }
    // split rest by '/' -> segments; NORM can be at any position (TTL may precede it)
    var segs = entryLine.split('/').map(function(s){ return s.trim(); });
    if (segs.length >= 2) {
      // find NORM = first pure-digit segment 3-8 chars
      var normIdx = -1;
      for (var si=0; si<segs.length; si++) {
        if (/^\d{3,8}$/.test(segs[si])) { normIdx = si; break; }
      }
      if (normIdx === -1) { continue; }
      name = segs[0];
      norm = segs[normIdx];
      var restSegs = segs.slice(1).filter(function(s,ki){ return ki+1 !== normIdx; });
      var lastSeg = restSegs.length ? restSegs[restSegs.length-1] : '';
      var haveStatus = /^(LEADER|KJS|Leader|Kls\s?J|KLS J)$/i.test(lastSeg);
      if (haveStatus) { st = lastSeg; restSegs = restSegs.slice(0, -1); }
      // TTL = a date-like segment (may be before or after NORM)
      if (restSegs.length) {
        if (/^\d{1,2}[\-\/\s](?:Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des|\d)/i.test(restSegs[0]) || /^\d{1,2}[-/.]\d{2,4}$/.test(restSegs[0]) || /^\d{1,2}\s*(?:Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des)[a-z]*\s*\d{2,4}$/i.test(restSegs[0]) || /tahun|bulan/i.test(restSegs[0])) {
          ttl = restSegs[0];
          restSegs = restSegs.slice(1);
        }
        dx = restSegs.join(' / ').trim();
      }
      if (!st) st = (bed ? 'KJS' : 'LEADER');
      name = name.replace(/\s+/g, ' ').trim();
      norm = norm.replace(/\D/g, '');
      dx = dx.replace(/\s+/g, ' ').trim();
      st = st.toUpperCase().replace(/KLS\s?J|KLSJ/, 'KJS').replace(/LEADER\.?/i, 'LEADER').replace(/KJS\.?/, 'KJS').trim();
      if (!/^(LEADER|KJS|MCC\d+)$/.test(st)) st = 'KJS';
    } else {
      continue;
    }
    if (name && norm) {
      var floorLabel = currentFloor || 'Tidak Diketahui';
      var roomLabel = room || currentRoom || 'Tidak Diketahui';
      if (bed && !room && !currentRoom) roomLabel = 'Bed ' + bed;
      parsed.push({
        norm: norm,
        nama: name,
        ttl: ttl,
        dx: dx,
        status: st,
        lantai: floorLabel,
        kamar: roomLabel,
        bed: bed || '-'
      });
    }
  }
  if (parsed.length === 0) {
    status.textContent = '❌ Tidak ada pasien ter-parse. Format: Nama / NORM / TTL / DX / Status';
    return;
  }
  status.textContent = '✅ Ditemukan ' + parsed.length + ' pasien.';
  ParsedPatients = parsed;
  showPreview();
}
function showPreview() {
  var preview = document.getElementById('previewArea');
  var discharged = document.getElementById('dischargedArea');
  var btn = document.getElementById('btnApply');
  var footer = document.getElementById('footerStatus');
  fetch('/api/masters/current')
    .then(function(r){ return r.json(); })
    .then(function(resp) {
      var master = resp.patients || [];
      var masterMap = {};
      master.forEach(function(p){ masterMap[p.norm] = p; });

      // Categorize
      BaruList = []; BerubahList = []; TetapList = []; DischargedList = []; UnclearList = [];
      ParsedPatients.forEach(function(np) {
        var old = masterMap[np.norm];
        if (!old) { BaruList.push(np); return; }
        // detect changes
        var changes = [];
        if (old.nama && np.nama && old.nama.trim() !== np.nama.trim()) changes.push('Nama');
        if (!np.lantai || np.lantai === 'Tidak Diketahui') { /* unclear floor */ }
        if (np.lantai === 'Tidak Diketahui' || !np.kamar || np.kamar === 'Tidak Diketahui') {
          UnclearList.push(np); return;
        }
        if (changes.length) { np.changes = changes; BerubahList.push(np); }
        else TetapList.push(np);
      });
      // discharged = in master but not in parsed
      master.forEach(function(p) {
        if (!ParsedPatients.some(function(pp){ return pp.norm === p.norm; })) DischargedList.push(p);
      });

      renderConfirm();
    })
    .catch(function() {
      // fallback: no master available
      BaruList = ParsedPatients.slice(); BerubahList = []; TetapList = []; DischargedList = []; UnclearList = [];
      renderConfirm();
    });
}

function renderConfirm() {
  var preview = document.getElementById('previewArea');
  var discharged = document.getElementById('dischargedArea');
  var btn = document.getElementById('btnApply');
  var footer = document.getElementById('footerStatus');

  var total = ParsedPatients.length;
  var nBaru = BaruList.length, nBerubah = BerubahList.length, nTetap = TetapList.length, nDisc = DischargedList.length, nUnclear = UnclearList.length;

  var h = '';
  h += '<div style="background:#f5f5f5;padding:8px 12px;border-radius:6px;font-size:12px;margin-bottom:10px;">Parse: <b>'+total+'</b> pasien &nbsp;|&nbsp; 🟢 Baru '+nBaru+' &nbsp; 🔵 Berubah '+nBerubah+' &nbsp; ⚪ Tetap '+nTetap+' &nbsp; 🔴 Pulang '+nDisc+' &nbsp; ⚠️ Tidak jelas '+nUnclear+'</div>';

  function section(title, list, color, icon) {
    if (!list.length) return '';
    var s = '<div style="margin:8px 0;"><div style="font-weight:700;color:'+color+';font-size:13px;cursor:pointer;" onclick="toggleCollapse(this)">'+icon+' '+title+' ('+list.length+') <span style="font-size:11px;color:#999;">▾</span></div><div class="confirm-collapse">';
    s += '<table class="preview-table"><tr><th>Nama</th><th>NORM</th><th>Lokasi</th><th>Bed</th><th>Status</th></tr>';
    list.forEach(function(p){
      var lok = (p.lantai && p.lantai!=='Tidak Diketahui' ? p.lantai : '?') + ' / ' + (p.kamar && p.kamar!=='Tidak Diketahui' ? p.kamar : '?');
      s += '<tr><td>'+esc(p.nama)+'</td><td>'+p.norm+'</td><td>'+esc(lok)+'</td><td>'+esc(p.bed||'-')+'</td><td>'+esc(p.status)+'</td></tr>';
    });
    s += '</table></div></div>';
    return s;
  }

  h += section('PASIEN BARU', BaruList, '#2e7d32', '🟢');
  h += section('PERUBAHAN (pindah ruang/kamar/bed/status)', BerubahList, '#1565c0', '🔵');
  h += section('TETAP (tanpa perubahan)', TetapList, '#666', '⚪');
  h += section('LOKASI TIDAK JELAS — perlu cek SIMRS', UnclearList, '#e65100', '⚠️');

  preview.innerHTML = h;

  // SIMRS fetch button for unclear
  if (UnclearList.length) {
    preview.innerHTML += '<div style="margin:10px 0;" id="simrsBtnWrap"><button class="btn-parse" onclick="fetchLokasiSIMRS()">🔍 Ambil Lokasi dari SIMRS ('+UnclearList.length+')</button> <span id="simrsStatus" style="font-size:12px;color:#999;"></span></div>';
  }

  // discharged
  if (nDisc > 0) {
    var dh = '<div class="discharged-list"><h4>🔴 '+nDisc+' Pasien PULANG / tidak ada di list baru:</h4>';
    DischargedList.forEach(function(p){ dh += '<div>- '+esc(p.nama)+' (RM '+p.norm+')</div>'; });
    dh += '</div>';
    discharged.innerHTML = dh;
  } else {
    discharged.innerHTML = '<div style="background:#e8f5e9;padding:12px;border-radius:6px;margin:10px 0;font-size:13px;">✅ Tidak ada pasien pulang</div>';
  }

  // checkbox + apply button state
  var cbwrap = document.getElementById('confirmCheckWrap');
  if (cbwrap) {
    cbwrap.style.display = 'block';
    document.getElementById('confirmCheck').checked = false;
  }
  // enable apply only if no unclear
  if (nUnclear === 0) btn.disabled = false; else btn.disabled = true;
  if (nDisc > 15) {
    footer.innerHTML = '⚠️ <b>PERHATIAN:</b> '+nDisc+' pasien pulang — pastikan list sudah lengkap!';
  } else {
    footer.textContent = 'Review konfirmasi di atas, centang "sudah dicek", lalu Apply.';
  }
}

function toggleCollapse(el) {
  var body = el.nextElementSibling;
  if (body && body.classList) {
    if (body.style.display === 'none') { body.style.display = ''; el.querySelector('span').textContent = '▾'; }
    else { body.style.display = 'none'; el.querySelector('span').textContent = '▸'; }
  }
}

function fetchLokasiSIMRS() {
  var st = document.getElementById('simrsStatus');
  st.textContent = '⏳ Fetching SIMRS...';
  var pending = UnclearList.slice();
  var done = 0;
  pending.forEach(function(p, idx) {
    fetch('/api/pasien/lokasi/' + p.norm)
      .then(function(r){ return r.json(); })
      .then(function(resp) {
        done++;
        if (resp.success) {
          p.lantai = resp.lantai || p.lantai;
          p.kamar = resp.kamar || p.kamar;
          p.bed = resp.bed || p.bed;
          p.simrsRuangan = resp.ruangan;
        }
        st.textContent = '✅ ' + done + '/' + pending.length + ' ter-resolve dari SIMRS';
        if (done >= pending.length) {
          // re-evaluate: move resolved out of unclear if now clear
          UnclearList = UnclearList.filter(function(u){ return !u.lantai || u.lantai === 'Tidak Diketahui'; });
          if (UnclearList.length === 0) {
            document.getElementById('btnApply').disabled = false;
            st.textContent = '✅ Semua lokasi jelas — siap Apply';
          } else {
            st.textContent = '⚠️ ' + UnclearList.length + ' masih tidak jelas';
          }
          renderConfirm();
        }
      })
      .catch(function(){ done++; if(done>=pending.length){ st.textContent='❌ Fetch gagal sebagian'; renderConfirm(); } });
  });
}

function applySensus() {
  if (!ParsedPatients.length) { alert('Parse dulu!'); return; }
  if (UnclearList.length) { alert('Masih ada '+UnclearList.length+' pasien dengan lokasi tidak jelas. Ambil dari SIMRS dulu.'); return; }
  var cb = document.getElementById('confirmCheck');
  if (cb && !cb.checked) { alert('Centang "Saya sudah cek datanya" dulu.'); return; }
  var statusEl = document.getElementById('footerStatus');
  statusEl.innerHTML = '⏳ Mengirim...';
  document.getElementById('btnApply').disabled = true;
  fetch('/api/master/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      patients: ParsedPatients,
      discharged: DischargedList.map(function(p){ return p.norm; }),
      rebuild: true
    })
  })
  .then(function(r){ return r.json(); })
  .then(function(resp) {
    if (resp.success) {
      statusEl.innerHTML = '✅ <b>BERHASIL!</b> Pipeline rebuild started. Job: '+resp.job_id;
      pollBuildStatus(resp.job_id);
    } else {
      statusEl.innerHTML = '❌ Error: ' + resp.error;
      document.getElementById('btnApply').disabled = false;
    }
  })
  .catch(function(e) {
    statusEl.innerHTML = '❌ Request gagal: ' + e;
    document.getElementById('btnApply').disabled = false;
  });
}
function pollBuildStatus(jobId) {
  var tries = 0;
  var poll = setInterval(function() {
    tries++;
    if (tries > 180) { clearInterval(poll); document.getElementById('footerStatus').innerHTML = '⚠️ Timeout (15 menit). Cek manual.'; return; }
    fetch('/api/build/status/' + jobId)
      .then(function(r){ return r.json(); })
      .then(function(resp) {
        var fs = document.getElementById('footerStatus');
        if (resp.status === 'done') {
          clearInterval(poll);
          fs.innerHTML = '✅ <b>REBUILD SELESAI!</b> Memuat ulang...';
          setTimeout(function(){ window.location.href = '/'; }, 1500);
        } else if (resp.status === 'error') {
          clearInterval(poll);
          fs.innerHTML = '❌ <b>GAGAL:</b> ' + (resp.error || resp.progress || 'unknown');
        } else {
          fs.innerHTML = '⏳ ' + (resp.status || 'processing').toUpperCase() + ': ' + (resp.progress || '') + ' (' + tries + ')';
        }
      }).catch(function(){ fs.innerHTML = '⏳ menunggu...'; });
  }, 5000);
}
</script>
"""
    html = html.replace('</body>', modal_js + '\n</body>')
    return html.encode('utf-8')

def find_html():
    cands = []
    for fn in os.listdir(OUT):
        if fn.startswith('Rekap Lab Hemato') and fn.endswith('.html'):
            fp = os.path.join(OUT, fn)
            cands.append((os.path.getmtime(fp), fp))
    if not cands: return None
    cands.sort(reverse=True)
    return cands[0][1]

def get_cached_html():
    with _html_cache['lock']:
        path = find_html()
        if not path: return None
        mtime = os.path.getmtime(path)
        if _html_cache['path'] == path and _html_cache['mtime'] == mtime and _html_cache['data']:
            return _html_cache['data']
        with open(path, 'rb') as f:
            data = f.read()
        _html_cache['path'] = path
        _html_cache['mtime'] = mtime
        _html_cache['data'] = data
        return data

def get_patient_data(norm):
    """Return full lab data for a NORM from pickle with clustering support."""
    try:
        from datetime import datetime
        def cluster_labs(raw_groups):
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
                    existing_names = {p.get('name', '').lower().strip() for p in last['params']}
                    for p in params:
                        pn = p.get('name', '').lower().strip()
                        if pn not in existing_names:
                            last['params'].append(p)
                            existing_names.add(pn)
                else:
                    clustered.append({'dt': dt, 'tgl': tgl_str, 'params': list(params)})
            return clustered

        if not os.path.exists(PICKLE): return None
        d = pickle.load(open(PICKLE, 'rb'))
        for k, v in d.items():
            if str(k).split('_')[0] == str(norm):
                raw_labs = v.get('labs') or v.get('visits') or []
                c_visits = cluster_labs(raw_labs)
                return {
                    'name': v.get('name'),
                    'visits': [{'tgl': vis.get('tgl'), 'params': vis.get('params', [])} for vis in c_visits]
                }
    except Exception as e:
        print(f"ERR get_patient_data: {e}")
        return None
    return None

class H(SimpleHTTPRequestHandler):
    def _send_gzip(self, data, ctype='text/html; charset=utf-8'):
        accept = self.headers.get('Accept-Encoding', '')
        if 'gzip' in accept and len(data) > 1024:
            compressed = gzip.compress(data)
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(compressed)))
            self.send_header('Cache-Control', 'public, max-age=30')
            self.end_headers()
            self.wfile.write(compressed)
        else:
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'public, max-age=30')
            self.end_headers()
            self.wfile.write(data)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            html = get_cached_html()
            if not html:
                self.send_response(404); self.end_headers(); return
            # Inject "Update Master List" button + modal into main report HTML
            injected = inject_update_master_button(html)
            self._send_gzip(injected)
        elif self.path in ('/lookup.html',):
            lpath = os.path.join(os.path.dirname(__file__), 'lookup.html')
            if not os.path.exists(lpath):
                self.send_response(404); self.end_headers(); return
            with open(lpath, 'rb') as f:
                self._send_gzip(f.read(), 'text/html; charset=utf-8')
        elif self.path in ('/paste-master.html', '/paste'):
            ppath = os.path.join(os.path.dirname(__file__), 'paste_master.html')
            if not os.path.exists(ppath):
                self.send_response(404); self.end_headers(); return
            with open(ppath, 'rb') as f:
                html = f.read()
            # No cache for paste-master (always fresh)
            accept = self.headers.get('Accept-Encoding', '')
            if 'gzip' in accept:
                html = gzip.compress(html)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            if 'gzip' in accept:
                self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(html)))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            self.wfile.write(html)
        elif self.path in ('/special.html',):
            import glob
            files = sorted(glob.glob(os.path.join(OUT, 'PA-Rad-BMP Hemato * Pasien - *.html')), reverse=True)
            if not files:
                self.send_response(404); self.end_headers(); return
            with open(files[0], 'rb') as f:
                self._send_gzip(f.read(), 'text/html; charset=utf-8')
        elif self.path.startswith('/api/patient/'):
            norm = self.path.split('/')[-1]
            data = get_patient_data(norm)
            if not data:
                self.send_response(404); self.end_headers(); return
            body = json.dumps(data, ensure_ascii=False).encode()
            self._send_gzip(body, 'application/json; charset=utf-8')
        elif self.path.startswith('/api/lookup-job/start/'):
            if not lookup_enabled():
                self.send_response(403); self.end_headers(); return
            from urllib.parse import urlparse, parse_qs
            u = urlparse(self.path)
            norm = u.path.strip('/').split('/')[-1]
            qs = parse_qs(u.query)
            with_sp = qs.get('special', ['1'])[0] in ('1', 'true', 'yes')
            # refresh=1 → bypass 6h disk cache (explicit refetch, e.g. from
            # the "Refetch" button in lookup.html). Default jobs USE the cache
            # so a repeat lookup of the same RM finishes in <1s.
            force_fresh = qs.get('refresh', ['0'])[0] in ('1', 'true', 'yes')
            try:
                norm = str(int(norm))
            except Exception:
                body = json.dumps({'success':False,'error':'NORM harus angka'}).encode()
                self._send_gzip(body, 'application/json; charset=utf-8'); return
            # Reuse a running job for the same RM rather than duplicate SIMRS load.
            with _lookup_jobs_lock:
                now = time.time()
                for jid in list(_lookup_jobs):
                    job = _lookup_jobs[jid]
                    if now - job.get('created_at', now) > 3600:
                        _lookup_jobs.pop(jid, None)
                existing = next((jid for jid, job in _lookup_jobs.items()
                                 if job.get('norm') == norm and job.get('status') == 'running'), None)
                job_id = existing or uuid.uuid4().hex[:16]
                if not existing:
                    _lookup_jobs[job_id] = {'status':'running','norm':norm,'created_at':now}
            if not existing:
                threading.Thread(target=_run_full_lookup_job,
                                 args=(job_id, norm, with_sp, not force_fresh), daemon=True).start()
            body = json.dumps({'success':True,'job_id':job_id,'status':'running'}).encode()
            self._send_gzip(body, 'application/json; charset=utf-8')
        elif self.path.startswith('/api/lookup-job/status/'):
            job_id = self.path.split('?', 1)[0].rstrip('/').split('/')[-1]
            with _lookup_jobs_lock:
                job = dict(_lookup_jobs.get(job_id) or {})
            if not job:
                body = json.dumps({'success':False,'error':'Job tidak ditemukan'}).encode()
            elif job.get('status') == 'done':
                body = json.dumps({'success':True,'status':'done','result':job.get('result')}, ensure_ascii=False).encode()
            elif job.get('status') == 'error':
                body = json.dumps({'success':False,'status':'error','error':job.get('error','Unknown error')}, ensure_ascii=False).encode()
            else:
                body = json.dumps({'success':True,'status':'running','norm':job.get('norm')}).encode()
            self._send_gzip(body, 'application/json; charset=utf-8')
        elif self.path == '/api/jobs/recent':
            # Return recent jobs for notification polling
            import os as _os, glob
            _JD = '/tmp/hema_jobs'
            jobs = []
            if _os.path.exists(_JD):
                for jf in sorted(glob.glob(f'{_JD}/*.json'), key=lambda x: _os.path.getmtime(x), reverse=True)[:5]:
                    try:
                        j = json.loads(open(jf).read())
                        jobs.append({'job_id': j.get('job_id'), 'status': j.get('status'), 'progress': j.get('progress', ''), 'type': j.get('type', '')})
                    except: pass
            # Also include in-memory running jobs
            with _lookup_jobs_lock:
                for jid, job in _lookup_jobs.items():
                    if job.get('status') in ('running', 'pending') and jid not in [j.get('job_id') for j in jobs]:
                        jobs.append({'job_id': jid, 'status': job.get('status'), 'progress': job.get('progress', ''), 'type': job.get('type', '')})
            body = json.dumps({'success': True, 'jobs': jobs[:5]}, ensure_ascii=False).encode()
            self._send_gzip(body, 'application/json; charset=utf-8')
        elif self.path.startswith('/api/lookup/'):
            if not lookup_enabled():
                self.send_response(403); self.send_header('Content-Type','application/json'); self.end_headers()
                self.wfile.write(b'{"success":false,"error":"Lookup disabled"}'); return
            
            # parse url & query params
            from urllib.parse import urlparse, parse_qs
            u = urlparse(self.path)
            norm = u.path.strip('/').split('/')[-1]
            qs = parse_qs(u.query)
            with_sp = qs.get('special', ['1'])[0] in ('1', 'true', 'yes')
            quick = qs.get('quick', ['0'])[0] in ('1', 'true', 'yes')
            max_c = int(qs.get('max', ['3'])[0]) if qs.get('max') else 3

            try:
                import hematology_lookup as HL
                result = HL.lookup(norm, include_special=with_sp, quick=quick, max_clusters=max_c)
                body = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self._send_gzip(body, 'application/json; charset=utf-8')
            except Exception as e:
                self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
                self.wfile.write(json.dumps({'success':False,'error':f'Server error: {str(e)}'},ensure_ascii=False).encode('utf-8'))
        elif self.path.startswith('/api/special/'):
            # Return PA/Rad/BMP/LCS/Immuno/IHC for a NORM (from pre-fetched pickle)
            norm = self.path.split('/')[-1]
            try:
                import pickle as _pk
                PKL='/tmp/simrs_special_15agustus.pkl'
                if not os.path.exists(PKL):
                    raise Exception('Pickle not found - run fetch first')
                data=_pk.load(open(PKL,'rb'))
                v=data.get(norm)
                if not v:
                    raise Exception('NORM not in list')
                result={'success':True,'norm':norm,'name':v.get('name'),
                        'pa':v.get('pa',[]),'rad':v.get('rad',[]),'bmp':v.get('bmp',[]),
                        'lcs':v.get('lcs',[]),'immuno':v.get('immuno',[]),'ihc':v.get('ihc',[])}
                body=json.dumps(result,ensure_ascii=False).encode()
                self._send_gzip(body,'application/json; charset=utf-8')
            except Exception as e:
                self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
                self.wfile.write(json.dumps({'success':False,'error':f'Server error: {type(e).__name__}'},ensure_ascii=False).encode())
        elif self.path.startswith('/api/notes'):
            notes = {}
            if os.path.exists(NOTES_FILE):
                try: notes = json.load(open(NOTES_FILE))
                except Exception: notes = {}
            body = json.dumps(notes, ensure_ascii=False).encode()
            self._send_gzip(body, 'application/json; charset=utf-8')
        elif self.path == '/api/master/paste':
            # GET: return saved paste content
            try:
                paste_file = '/tmp/master_paste_raw.txt'
                meta_file = '/tmp/master_paste_meta.json'
                if os.path.exists(paste_file):
                    with open(paste_file, 'r') as f:
                        content = f.read()
                    meta = {}
                    if os.path.exists(meta_file):
                        try: meta = json.load(open(meta_file))
                        except: pass
                    body = json.dumps({'success': True, 'content': content, 'saved_at': meta.get('saved_at')}, ensure_ascii=False).encode()
                else:
                    body = json.dumps({'success': False, 'error': 'No saved paste'}).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
            except Exception as e:
                body = json.dumps({'success': False, 'error': str(e)}).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
        elif self.path == '/api/master/paste-status':
            # Return status: last saved, last processed, cron next
            try:
                import hashlib
                meta_file = '/tmp/master_paste_meta.json'
                meta = {}
                if os.path.exists(meta_file):
                    try: meta = json.load(open(meta_file))
                    except: pass
                
                # Cron next: every 8h (00:00, 08:00, 16:00 WITA = UTC+8)
                from datetime import datetime, timedelta
                now = datetime.now()
                hours = [0, 8, 16]
                next_run = None
                for h in hours:
                    candidate = now.replace(hour=h, minute=0, second=0, microsecond=0)
                    if candidate > now:
                        next_run = candidate
                        break
                if not next_run:
                    next_run = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                
                body = json.dumps({
                    'success': True,
                    'last_saved': meta.get('saved_at'),
                    'last_processed': meta.get('last_processed'),
                    'patients_count': meta.get('patients_count'),
                    'cron_next': next_run.timestamp()
                }, ensure_ascii=False).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
            except Exception as e:
                body = json.dumps({'success': False, 'error': str(e)}).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
        else:
            self._handle_master_api()

    def _handle_master_api(self):
        """Handle master list API endpoints (GET: /api/masters/current, /api/build/status/)"""
        if self.path.startswith('/api/masters/current'):
            try:
                sys.path.insert(0, '/home/lenovo')
                import patients_29agustus as P
                # all_patients is list of dicts
                ap = getattr(P, 'all_patients', [])
                if ap and isinstance(ap[0], dict):
                    patients = [{'norm': str(p['norm']),'nama': p['nama'],'status': p['status']} for p in ap]
                elif hasattr(P, 'PATIENTS'):
                    # PATIENTS is list of tuples (lantai,bed,nama,norm,dx,status)
                    patients = [{'norm': str(t[3]),'nama': t[2],'status': t[5]} for t in P.PATIENTS]
                else:
                    patients = []
                body = json.dumps({'success':True,'patients':patients},ensure_ascii=False).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
            except Exception as e:
                self.send_response(500); self.send_header('Content-Type','application/json'); self.end_headers()
                self.wfile.write(json.dumps({'success':False,'error':str(e)}).encode())
        elif self.path.startswith('/api/build/status/'):
            try:
                jid = self.path.strip('/').split('/')[-1]
                jp = _os.path.join(_JOB_DIR, jid + '.json')
                if _os.path.exists(jp):
                    job = json.loads(open(jp).read())
                else:
                    job = {}
                body = json.dumps({'success':True,'status':job.get('status','error') if job else 'not_found','progress':job.get('progress',''),'patients':job.get('patients_updated'),'discharged':job.get('discharged'),'patients_done':job.get('patients_done',0),'total_patients':job.get('total_patients',0),'diff':job.get('diff',{})},ensure_ascii=False).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
            except Exception as e:
                self.send_response(500); self.send_header('Content-Type','application/json'); self.end_headers()
                self.wfile.write(json.dumps({'success':False,'error':str(e)}).encode())
        elif self.path.startswith('/api/pasien/lokasi/'):
            # Fetch active room/location from SIMRS for a NORM (fallback for unclear floor)
            try:
                norm = self.path.strip('/').split('/')[-1]
                norm = str(int(norm))
                import hematology_lookup as HL
                s = HL.make_session()
                if not s:
                    body = json.dumps({'success':False,'error':'SIMRS login gagal'}).encode()
                    self._send_gzip(body, 'application/json; charset=utf-8'); return
                r = s.get(f"{HL.BASE}/pendaftaran/kunjungan", params={"NORM": norm, "STATUS": 2, "limit": 50}, timeout=25)
                d = r.json()
                rows = d.get('data', []) if isinstance(d, dict) else d
                aktif = [k for k in rows if not k.get('KELUAR')] if isinstance(rows, list) else []
                if not aktif and isinstance(rows, list):
                    aktif = sorted(rows, key=lambda x: x.get('MASUK',''), reverse=True)[:1]
                if not aktif:
                    body = json.dumps({'success':False,'error':'Tidak ada kunjungan aktif'}).encode()
                    self._send_gzip(body, 'application/json; charset=utf-8'); return
                k = aktif[-1]
                ref = k.get('REFERENSI') or {}
                ruangan = ref.get('RUANGAN') or {}
                desk = ruangan.get('DESKRIPSI', '') if isinstance(ruangan, dict) else str(ruangan)
                lantai, kamar, bed = self._parse_lokasi(desk)
                body = json.dumps({'success':True,'norm':norm,'ruangan':desk,'lantai':lantai,'kamar':kamar,'bed':bed,'masuk':k.get('MASUK')},ensure_ascii=False).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
            except Exception as e:
                self.send_response(500); self.send_header('Content-Type','application/json'); self.end_headers()
                self.wfile.write(json.dumps({'success':False,'error':str(e)}).encode())
        else:
            self.send_response(404); self.end_headers()

    @staticmethod
    def _parse_lokasi(desk):
        """Map SIMRS room description -> (lantai, kamar, bed). Best-effort; keep raw desk visible."""
        import re
        if not desk:
            return ('Tidak Diketahui', '', '')
        d = desk.strip()
        lantai = ''; kamar = ''; bed = ''
        # explicit floor marker "Perawatan Lt. 7" / "Lt 5" / "Lantai 6"
        m = re.search(r'(?:Perawatan\s*)?Lt\.?\s*(\d+)', d, re.I) or re.search(r'Lantai\s*(\d+)', d, re.I)
        if m:
            lantai = 'Lantai ' + m.group(1)
        up = d.upper()
        if 'PICU' in up:
            lantai = 'Lantai 4 PICU'
        elif 'NICU' in up:
            lantai = 'Lantai 3 NICU'
        elif 'ICU' in up:
            lantai = lantai or 'ICU'
        # kamar/bed number if present
        mkam = re.search(r'Kamar\s*(\d+)', d, re.I)
        if mkam:
            kamar = 'Kamar ' + mkam.group(1)
        mbed = re.search(r'(?:Bed|Tempat\s*Tidur|TT)\s*\.?\s*(\d+)', d, re.I)
        if mbed:
            bed = mbed.group(1)
        # fallback: if we still have no lantai, use the raw description as the "lantai" hint
        if not lantai:
            lantai = d  # keep raw SIMRS room name so user can see it
        return (lantai, kamar, bed)

    @staticmethod
    def _parse_census_minimal(text):
        """Parse census text - extract NORM, lantai, kamar, dx using regex.
        Fast, no AI. Handles various formats."""
        import re
        
        patients = []
        current_lantai = ''
        current_kamar = ''
        lines = text.split('\n')
        
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped == '-':
                continue
            
            upper = stripped.upper()
            
            # Skip pure headers
            if upper.startswith(('LANTAI', 'MOCHIL', 'PICU', 'PCC', 'PJT', 'ICU', 'IRD', 'HCU', 'NICU', 'PINANG', 'LONTARA', 'PALEM', 'CARDIO', 'NON KEMO', 'KAMAR ISO')):
                if len(stripped) < 60 and not re.search(r'\d{6}', stripped):
                    current_lantai = stripped
                    # Extract floor number if present
                    m = re.search(r'(\d+)', stripped)
                    if m and 'LANTAI' in upper:
                        current_lantai = f'Lantai {m.group(1)} {stripped.replace("LANTAI " + m.group(1), "").replace(m.group(1), "", 1).strip()}'
                    continue
            
            # Room/bed headers
            rm = re.match(r'^\((\d+)\)(\.(\d+))?\)\.?$', stripped) or re.match(r'^\((\d+)\.(\d+)\)\.?$', stripped)
            if rm and len(stripped) < 20:
                current_kamar = f'Kamar {rm.group(1)}'
                continue
            bm = re.match(r'^\(\s*Bed\s*(\d+)\s*\)\.?$', stripped, re.I)
            if bm and len(stripped) < 20:
                current_kamar = f'Bed {bm.group(1)}'
                continue
            cm = re.match(r'^\s*KAMAR\s+(\d+)\s*$', stripped, re.I)
            if cm and len(stripped) < 30:
                current_kamar = f'Kamar {cm.group(1)}'
                continue
            
            # Patient entry - look for NORM (6+ digits)
            norm_match = re.search(r'(\d{6,7})', stripped)
            if norm_match:
                norm = norm_match.group(1).lstrip('0')
                # Extract name (before NORM)
                before = stripped[:norm_match.start()]
                # Remove leading numbering like "1. " or "1) "
                before = re.sub(r'^\d+[\.\)]\s*', '', before).strip()
                # Remove room prefix like "(506.3) "
                before = re.sub(r'^\(\d+\.\d+\)\.?\s*', '', before).strip()
                # Extract name (take first part before / )
                name_parts = re.split(r'\s*/\s*', before)
                nama = name_parts[0].strip() if name_parts else 'Pasien'
                
                # Extract diagnosis (after NORM, split by /)
                after = stripped[norm_match.end():]
                # Split by / but filter out TTL-like strings (dates)
                diag_parts = []
                for p in re.split(r'\s*/\s*', after):
                    p = p.strip()
                    if p:
                        # Skip if looks like a date (TTL) - various formats
                        # 01 Jan, 01 Jan 2020, 01/01/2020, 01-01-2020, Januari 2020
                        if (re.match(r'^\d{1,2}\s+\w+', p) and re.search(r'\d{4}', p) or re.match(r'^\d{1,2}\s+\w{3,}', p)
                            or re.match(r'^\d{1,2}[/\-]\d{1,2}[/\-]\d{4}', p)
                            or re.match(r'^\w+\s+\d{4}', p)):
                            continue
                        diag_parts.append(p)
                # First non-date part = diagnosis
                dx = diag_parts[0] if diag_parts else ''
                
                patients.append({
                    'norm': norm,
                    'nama': nama,
                    'lantai': current_lantai or 'Tidak Diketahui',
                    'kamar': current_kamar or '',
                    'dx': dx
                })
        
        return patients

    def do_POST(self):
        if self.path.startswith('/api/notes/'):
            ln = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(ln).decode() if ln else '{}'
            try: notes = json.loads(raw)
            except Exception: notes = {}
            with open(NOTES_FILE, 'w') as f:
                json.dump(notes, f, ensure_ascii=False, indent=1)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        elif self.path == '/api/master/paste':
            # POST: save raw paste content
            ln = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(ln)
            try:
                import hashlib
                data = json.loads(raw)
                content = data.get('content', '')
                if not content:
                    body = json.dumps({'success': False, 'error': 'Empty content'}).encode()
                    self._send_gzip(body, 'application/json; charset=utf-8')
                    return
                
                paste_file = '/tmp/master_paste_raw.txt'
                meta_file = '/tmp/master_paste_meta.json'
                
                with open(paste_file, 'w') as f:
                    f.write(content)
                
                hash_val = hashlib.sha256(content.encode()).hexdigest()[:16]
                saved_at = time.time()
                
                # Load existing meta to preserve last_processed
                meta = {}
                if os.path.exists(meta_file):
                    try: meta = json.load(open(meta_file))
                    except: pass
                
                meta.update({
                    'saved_at': saved_at,
                    'hash': hash_val,
                    'length': len(content)
                })
                
                with open(meta_file, 'w') as f:
                    json.dump(meta, f, indent=2)
                
                print(f'[PASTE SAVED] {saved_at} hash={hash_val[:8]} len={len(content)}', flush=True)
                
                body = json.dumps({'success': True, 'saved_at': saved_at, 'hash': hash_val}, ensure_ascii=False).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
            except Exception as e:
                body = json.dumps({'success': False, 'error': f'{type(e).__name__}: {e}'}).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
        elif self.path == '/api/refresh':
            # Trigger full lab refresh for all patients
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            try:
                job_id = 'refresh_' + uuid.uuid4().hex[:12]
                _job_write(job_id, type='rebuild', status='pending', created_at=time.time(), progress='Starting full lab refresh...')
                threading.Thread(target=_run_rebuild, args=(job_id,), daemon=True).start()
                body = json.dumps({'success': True, 'job_id': job_id}, ensure_ascii=False).encode()
                self.wfile.write(body)
            except Exception as e:
                body = json.dumps({'success': False, 'error': str(e)}).encode()
                self.wfile.write(body)
        elif self.path == '/api/master/parse':
            # Simple regex parser - extract NORM, lantai, kamar, dx (fast, no AI needed)
            ln = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(ln)
            try:
                import re
                data = json.loads(raw)
                content = data.get('content', '')
                
                if not content:
                    body = json.dumps({'success': False, 'error': 'Empty content'}).encode()
                    self._send_gzip(body, 'application/json; charset=utf-8')
                    return
                
                patients = self._parse_census_minimal(content)
                
                body = json.dumps({
                    'success': True, 
                    'patients': patients,
                    'count': len(patients)
                }, ensure_ascii=False).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
            except Exception as e:
                body = json.dumps({'success': False, 'error': f'{type(e).__name__}: {e}'}).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
        elif self.path == '/api/master/execute':
            # Execute full pipeline from saved .md file
            ln = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(ln)
            try:
                data = json.loads(raw)
                filepath = data.get('filepath', '')
                
                if not filepath or not os.path.exists(filepath):
                    body = json.dumps({'success': False, 'error': f'File not found: {filepath}'}).encode()
                    self._send_gzip(body, 'application/json; charset=utf-8')
                    return
                
                # Read the .md file
                with open(filepath, 'r') as f:
                    content = f.read()
                
                # Parse it
                patients = self._parse_census_minimal(content)
                
                if not patients:
                    body = json.dumps({'success': False, 'error': 'No patients parsed from file'}).encode()
                    self._send_gzip(body, 'application/json; charset=utf-8')
                    return
                
                # Save the parsed patients to master file
                master_path = '/home/lenovo/patients_29agustus.py'
                new_entries = []
                tuple_entries = []
                for p in patients:
                    try:
                        norm_val = int(str(p.get('norm','')).lstrip('0') or '0')
                    except (ValueError, TypeError):
                        norm_val = 0
                    dx_esc = p.get('dx','').replace('"','\\"')
                    nama_esc = p.get('nama','').replace('"','\\"')
                    lok = p.get('lantai','Tidak Diketahui').replace('"','\\"')
                    kam = (p.get('kamar','') or '').replace('"','\\"')
                    st_esc = 'KJS'
                    bed_val = '"bed": None'
                    
                    new_entries.append(f'    {{"norm": {norm_val}, "nama": "{nama_esc}", "ttl": "(lihat sensus)", "dx": "{dx_esc}", "status": "{st_esc}", {bed_val}}},')
                    tuple_entries.append(f'    ("{lok}", "{kam}", "{nama_esc}", {norm_val}, "{dx_esc}", "{st_esc}"),')
                
                new_block = 'all_patients = (\n' + '\n'.join(new_entries) + '\n)\n\nPATIENTS = [\n' + '\n'.join(tuple_entries) + '\n]\n'
                header = '# AUTO-GENERATED master census\n# Generated via Execute from File (hema.ark-kay.my.id)\n# Format: {"norm", "nama", "ttl", "dx", "status", "bed"}\n# status in {LEADER, KJS, MCC####}\n\n'
                
                with open(master_path, 'w') as f:
                    f.write(header + new_block)
                
                print(f'[MASTER EXECUTE] {len(patients)} patients saved from {filepath}', flush=True)
                
                # Compare with old master for diff notification
                try:
                    sys.path.insert(0, '/home/lenovo')
                    import patients_29agustus as OLD
                    old_all = getattr(OLD, 'all_patients', [])
                    old_norms = {str(p.get('norm','')) if isinstance(p, dict) else str(p[3]) for p in old_all}
                    new_norms = {p['norm'] for p in patients}
                    
                    diff = {
                        'baru': len(new_norms - old_norms),
                        'pulang': len(old_norms - new_norms), 
                        'sama': len(new_norms & old_norms)
                    }
                except Exception:
                    diff = {'baru': len(patients), 'pulang': 0, 'sama': 0}
                
                # Store diff in job
                with _lookup_jobs_lock:
                    _lookup_jobs[job_id] = {
                        'job_id': job_id, 'type': 'rebuild', 'status': 'pending',
                        'created_at': time.time(), 'progress': 'Starting pipeline rebuild...',
                        'patients_updated': len(patients), 'discharged': 0,
                        'diff': diff
                    }
                threading.Thread(target=_run_rebuild, args=(job_id,), daemon=True).start()
                
                # Update meta
                meta_file = '/tmp/master_paste_meta.json'
                meta = {}
                if os.path.exists(meta_file):
                    try: meta = json.load(open(meta_file))
                    except: pass
                meta['last_processed'] = time.time()
                meta['patients_count'] = len(patients)
                with open(meta_file, 'w') as f:
                    json.dump(meta, f, indent=2)
                
                body = json.dumps({'success': True, 'job_id': job_id, 'patients': len(patients), 'diff': diff}, ensure_ascii=False).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
                
            except Exception as e:
                body = json.dumps({'success': False, 'error': f'{type(e).__name__}: {e}'}).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
        elif self.path.startswith('/api/master/update'):
            # Complete rewrite of broken endpoint
            # Input: {patients: [{norm, nama, lantai, kamar, dx, status, bed}], rebuild: true}
            ln = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(ln)
            try:
                import re
                data = json.loads(raw)
                patients = data.get('patients', [])
                rebuild = data.get('rebuild', True)
                
                if not patients:
                    body = json.dumps({'success': False, 'error': 'No patients provided'}).encode()
                    self._send_gzip(body, 'application/json; charset=utf-8')
                    return
                
                master_path = '/home/lenovo/patients_29agustus.py'
                new_entries = []
                tuple_entries = []
                discharged = []
                
                for p in patients:
                    bed = p.get('bed', '')
                    bed_val = f'"bed": {int(bed)}' if bed and str(bed).isdigit() else '"bed": None'
                    try:
                        norm_val = int(str(p.get('norm', '')).lstrip('0') or '0')
                    except (ValueError, TypeError):
                        norm_val = 0
                    ttl = p.get('ttl', '')
                    ttl_val = f'"{ttl}"' if ttl else '"(lihat sensus)"'
                    dx_esc = p.get('dx', '').replace('"', '\\"')
                    nama_esc = p.get('nama', '').replace('"', '\\"')
                    lok = p.get('lantai', 'Tidak Diketahui').replace('"', '\\"')
                    kam = p.get('kamar', '').replace('"', '\\"')
                    st_esc = p.get('status', 'KJS').replace('"', '\\"')
                    new_entries.append(f'    {{"norm": {norm_val}, "nama": "{nama_esc}", "ttl": {ttl_val}, "dx": "{dx_esc}", "status": "{st_esc}", {bed_val}}},')
                    tuple_entries.append(f'    ("{lok}", "{kam}", "{nama_esc}", {norm_val}, "{dx_esc}", "{st_esc}"),')
                
                new_block = 'all_patients = (\n' + '\n'.join(new_entries) + '\n)\n\nPATIENTS = [\n' + '\n'.join(tuple_entries) + '\n]\n'
                header = (
                    '# AUTO-GENERATED master census\n'
                    '# Generated via Update Master List (hema.ark-kay.my.id)\n'
                    '# Format: {"norm", "nama", "ttl", "dx", "status", "bed"}\n'
                    '# status in {LEADER, KJS, MCC####}\n\n'
                )
                content = header + new_block
                with open(master_path, 'w') as f:
                    f.write(content)
                
                print(f'[MASTER UPDATE] {len(patients)} patients saved', flush=True)
                
                job_id = 'master_' + uuid.uuid4().hex[:12]
                
                if rebuild:
                    # Diff calculation
                    try:
                        sys.path.insert(0, '/home/lenovo')
                        import patients_29agustus as OLD
                        old_all = getattr(OLD, 'all_patients', [])
                        old_norms = {str(p.get('norm', '')) if isinstance(p, dict) else str(p[3]) for p in old_all}
                        new_norms = {str(p.get('norm', '')) for p in patients}
                        diff = {
                            'baru': len(new_norms - old_norms),
                            'pulang': len(old_norms - new_norms),
                            'sama': len(new_norms & old_norms)
                        }
                    except Exception:
                        diff = {'baru': len(patients), 'pulang': 0, 'sama': 0}
                    
                    with _lookup_jobs_lock:
                        _lookup_jobs[job_id] = {
                            'job_id': job_id, 'type': 'rebuild', 'status': 'pending',
                            'created_at': time.time(), 'progress': 'Starting pipeline rebuild...',
                            'patients_updated': len(patients), 'discharged': len(discharged),
                            'diff': diff
                        }
                    threading.Thread(target=_run_rebuild, args=(job_id,), daemon=True).start()
                    _job_write(job_id, type='rebuild', status='pending', created_at=time.time(), progress='Starting pipeline rebuild...', patients_updated=len(patients), discharged=len(discharged))
                    
                    body = json.dumps({'success': True, 'job_id': job_id, 'patients': len(patients), 'diff': diff}, ensure_ascii=False).encode()
                    self._send_gzip(body, 'application/json; charset=utf-8')
                else:
                    body = json.dumps({'success': True, 'patients': len(patients)}, ensure_ascii=False).encode()
                    self._send_gzip(body, 'application/json; charset=utf-8')
                    
            except Exception as e:
                body = json.dumps({'success': False, 'error': f'{type(e).__name__}: {e}'}, ensure_ascii=False).encode()
                self._send_gzip(body, 'application/json; charset=utf-8')
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass

import os as _os
_JOB_DIR = '/tmp/hema_jobs'
def _job_write(job_id, **kw):
    try:
        _os.makedirs(_JOB_DIR, exist_ok=True)
        jp = _os.path.join(_JOB_DIR, job_id + '.json')
        cur = {}
        if _os.path.exists(jp):
            try: cur = json.loads(open(jp).read())
            except: cur = {}
        cur.update(kw)
        cur['job_id'] = job_id
        cur['updated_at'] = time.time()
        open(jp, 'w').write(json.dumps(cur, ensure_ascii=False))
    except Exception as e:
        print('[JOB WRITE ERR]', e, flush=True)

def _run_rebuild(job_id):
    """Background pipeline rebuild after master update. 
    Tracks per-patient progress for notification."""
    try:
        # Get patient count
        sys.path.insert(0, '/home/lenovo')
        total = 0
        try:
            import patients_29agustus as P
            ap = getattr(P, 'all_patients', [])
            if ap and isinstance(ap[0], dict):
                total = len(ap)
            elif hasattr(P, 'PATIENTS'):
                total = len(P.PATIENTS)
        except:
            total = '?'
        
        _job_write(job_id, type='rebuild', status='running', progress=f'Phase 1: Update Lab (0/{total})', patients_done=0, total_patients=total)
        import subprocess
        
        # Skip progress tracker thread — causing issues
        
        try:
            # Run update_lab WITHOUT capturing output to avoid deadlock
            # (stdout buffer fills up and causes hang)
            proc = subprocess.Popen([sys.executable, '/home/lenovo/update_lab.py'],
                cwd='/home/lenovo')
            
            # Monitor with timeout
            start = time.time()
            while proc.poll() is None:
                elapsed = time.time() - start
                # Estimate progress (assume ~15s per patient avg based on actual timing)
                estimated = min(int(elapsed / 15), total if isinstance(total, int) else 0)
                _job_write(job_id, progress=f'Phase 1: Update Lab ({estimated}/{total})', patients_done=estimated, total_patients=total)
                time.sleep(5)
                
                # Timeout after 20 minutes (SIMRS can be very slow)
                if elapsed > 1200:
                    proc.terminate()
                    time.sleep(3)
                    if proc.poll() is None:
                        proc.kill()
                        time.sleep(1)
                    _job_write(job_id, status='error', error='Timeout after 20 minutes — check SIMRS connection')
                    return
            
            # Process finished — check if pickle was updated (success indicator)
            import os
            pkl_exists = os.path.exists('/tmp/simrs_15agustus_full.pkl')
            pkl_recent = False
            if pkl_exists:
                pkl_mtime = os.path.getmtime('/tmp/simrs_15agustus_full.pkl')
                pkl_recent = (time.time() - pkl_mtime) < 200  # updated within 3+ min
            
            if proc.returncode != 0 and not pkl_recent:
                _job_write(job_id, status='error', error=f'Lab update failed (rc={proc.returncode}, pkl_recent={pkl_recent})')
                return
            
            # If pickle is recent, consider it success even if returncode != 0
            done_counter = [total if isinstance(total, int) else 0]
            _job_write(job_id, progress=f'Phase 1 done (Lab) - {done_counter[0]}/{total}', patients_done=done_counter[0], total_patients=total)
        except Exception as e:
            _job_write(job_id, progress=f'Phase 1 failed: {str(e)[:100]}', status='error', error=str(e))
            return
            
        _job_write(job_id, progress='Phase 2: Update Special Modules (PA, Rad, BMP, LCS, Immuno, IHC)')
        try:
            import update_special
            update_special.main()
            _job_write(job_id, progress='Phase 2 done (Special)')
        except Exception as e:
            _job_write(job_id, progress=f'Phase 2 failed: {str(e)[:100]}')
            
        _job_write(job_id, progress='Phase 3: Build HTML')
        try:
            # Run HTML generation directly (not via import to avoid module cache)
            result = subprocess.run([sys.executable, '/home/lenovo/gen_16agustus_html.py'],
                capture_output=True, text=True, timeout=30, cwd='/home/lenovo')
            if result.returncode == 0:
                _job_write(job_id, progress='Phase 3 done: HTML generated')
            else:
                _job_write(job_id, progress=f'Phase 3 warning: {result.stderr[:100]}')
        except Exception as e:
            _job_write(job_id, progress=f'Phase 3 failed: {str(e)[:100]}')
            
        _job_write(job_id, status='done', progress='Complete ✅ — semua pipeline selesai', patients_done=total, total_patients=total)
        
        # Restart server for cache refresh
        try:
            subprocess.run(['systemctl', '--user', 'restart', 'hema-report.service'], check=False, timeout=10)
        except: pass
    except Exception as e:
        _job_write(job_id, status='error', error=str(e))

if __name__ == '__main__':
    print(f"Hemato notes server on :{PORT} (gzip+cache enabled)", flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
