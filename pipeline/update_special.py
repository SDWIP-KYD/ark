#!/usr/bin/env python3
"""Module Update Khusus Special Data (PA/Rad/BMP/LCS/Immuno/IHC).
TIDAK MENYENTUH DATA LAB RUTIN.
"""
import os, sys, signal, subprocess, argparse, pickle, time
sys.path.insert(0, '/home/lenovo')
from patients_29agustus import PATIENTS

PKL_SPEC = "/tmp/simrs_special_15agustus.pkl"

def kill_stale_special():
    my_pid = os.getpid()
    parent_pid = os.getppid()
    for line in subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2: continue
        try: pid = int(parts[0])
        except: continue
        args = parts[1]
        if pid in (my_pid, parent_pid): continue
        if "update_special.py" in args or "fetch_special" in args:
            try: os.kill(pid, signal.SIGKILL)
            except: pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    kill_stale_special()
    import fetch_special_15agustus as FS
    s = FS.make_session()
    if not s:
        print("SIMRS Login Failed"); sys.exit(1)

    spec_d = pickle.load(open(PKL_SPEC, "rb")) if os.path.exists(PKL_SPEC) else {}
    mods = ["pa", "rad", "bmp", "lcs", "immuno", "ihc"]
    items = [p for p in PATIENTS if (args.only is None or str(int(p[3])) == args.only)]
    print(f"Updating SPECIAL for {len(items)} patients...", flush=True)

    for i, (lokasi, kamar, name, norm_raw, dx, status) in enumerate(items):
        norm = str(int(norm_raw))
        if i > 0 and i % 15 == 0:
            try: s = FS.make_session()
            except: pass
        
        cur = spec_d.get(norm, {})
        cur.update({"name": name, "lokasi": lokasi, "kamar": kamar, "diagnosis": dx, "status": status})
        
        for m in mods:
            fn = getattr(FS, f"fetch_{m}", None)
            if fn:
                try:
                    cur[m] = fn(s, norm)
                except Exception as e:
                    print(f"  ERR {norm}/{m}: {e}")
        
        spec_d[norm] = cur
        pickle.dump(spec_d, open(PKL_SPEC, "wb"))
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(items)} special updated", flush=True)
    print("DONE Special Update.")

if __name__ == "__main__":
    main()
