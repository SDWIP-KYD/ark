#!/usr/bin/env python3
"""Module Update Khusus Lab Rutin (Hybrid: Max 200 records + True Total).
TIDAK MENYENTUH SPECIAL DATA (PA/Rad/BMP/LCS/Immuno/IHC).
"""
import os, sys, signal, subprocess, argparse, pickle, time, json
from collections import OrderedDict

PKL_LAB = "/tmp/simrs_15agustus_full.pkl"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from patients_29agustus import PATIENTS

def kill_stale_lab():
    my_pid = os.getpid()
    parent_pid = os.getppid()
    for line in subprocess.run(["ps", "-eo", "pid,ppid,args"], capture_output=True, text=True).stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3: continue
        try: 
            pid = int(parts[0])
            ppid = int(parts[1])
        except: continue
        args = parts[2]
        # Skip self, parent, and direct children of parent (siblings launched together)
        if pid == my_pid or pid == parent_pid or ppid == parent_pid: continue
        # Only kill direct python3 update_lab.py processes (not timeout wrappers)
        if "python3 update_lab.py" in args or "python update_lab.py" in args:
            # Skip if this is a subprocess of current session (timeout wrapper of us)
            if str(my_pid) in args or str(parent_pid) in args: continue
            try: 
                os.kill(pid, signal.SIGKILL)
                print(f"Killed stale process {pid}", flush=True)
            except: pass

def fetch_lab_patient(s, norm, base_url):
    try:
        r = s.get(f"{base_url}/layanan/hasillab", params={"NORM": norm, "limit": 200}, timeout=45).json()
        if not r.get("success"): return None
        total = r.get("total", 0) or 0
        vd = OrderedDict()
        for it in (r.get("data") or []):
            vid = str(it.get("TINDAKAN_MEDIS") or it.get("KUNJUNGAN_LAB") or "?")
            tgl = (it.get("TANGGAL") or "?")[:19]
            if vid not in vd: vd[vid] = {"tgl": tgl, "vid": vid, "params": []}
            rf = it.get("REFERENSI", {})
            if isinstance(rf, str):
                try: rf = json.loads(rf)
                except: rf = {}
            prf = rf.get("PARAMETER_TINDAKAN", {}) if isinstance(rf, dict) else {}
            if isinstance(prf, str):
                try: prf = json.loads(prf)
                except: prf = {}
            if not isinstance(prf, dict): prf = {}
            pn = str(prf.get("PARAMETER", "?") or "")
            if not pn or pn == "-": continue
            sat = prf.get("SATUAN")
            if isinstance(sat, str):
                try: sat = json.loads(sat)
                except: sat = {}
            vd[vid]["params"].append({
                "name": pn,
                "hasil": str(it.get("HASIL", "") or ""),
                "normal": str(prf.get("NILAI_RUJUKAN", "") or prf.get("NILAI_NORMAL", "") or ""),
                "satuan": str(sat.get("DESKRIPSI", "") if isinstance(sat, dict) else ""),
            })
        if vd:
            return {"labs": list(vd.values()), "latest": max((l["tgl"] for l in vd.values()), default=""),
                    "total": total, "shown": len(vd)}
    except Exception as e:
        print(f"  ERR lab {norm}: {e}")
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    kill_stale_lab()
    import fetch_special_15agustus as FS
    s = FS.make_session()
    if not s:
        print("SIMRS Login Failed"); sys.exit(1)

    lab_d = pickle.load(open(PKL_LAB, "rb")) if os.path.exists(PKL_LAB) else {}
    items = [(str(int(p[3])), p[2]) for p in PATIENTS if (args.only is None or str(int(p[3])) == args.only)]
    print(f"Updating LAB for {len(items)} patients...", flush=True)

    for i, (norm, name) in enumerate(items):
        if i > 0 and i % 15 == 0:
            try:
                s.close()  # Close old session before making new one
                s = FS.make_session()
            except: pass
        entry = fetch_lab_patient(s, norm, FS.BASE)
        if entry:
            entry["name"] = name
            lab_d[norm] = entry
            pickle.dump(lab_d, open(PKL_LAB, "wb"))
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(items)} lab updated", flush=True)
    
    # Cleanup session before exit
    try:
        s.close()
    except:
        pass
    print("DONE Lab Update.", flush=True)
    sys.exit(0)

if __name__ == "__main__":
    main()
