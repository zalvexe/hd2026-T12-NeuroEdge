"""Gabungkan results_*.json menjadi satu tabel perbandingan CPU vs FPGA.
Jalankan setelah bench_cpu.py, snn_cpu (+ power_mon.py), dan bench_fpga.py:
    python3 compare.py            # cetak tabel markdown + tulis comparison.md
"""
import glob, json, os
import numpy as np
from common import metrics

y = np.loadtxt("y_test.txt", dtype=np.int64)
rows = []
for f in sorted(glob.glob("results_*.json")):
    r = json.load(open(f))
    p = r["platform"]
    pf = f"pred_{p}.txt"
    m = metrics(y, np.loadtxt(pf, dtype=np.int64)) if os.path.exists(pf) else r.get("metrics")
    thr = r["throughput_inf_per_s"]
    pw = r.get("power")
    rows.append(dict(
        platform=p, acc=m["accuracy"] * 100, f1=m["macro_f1"], pdf1=m["pd_f1"],
        exact=f"{r['bit_exact_ok']}/{r['n']}",
        lat=r["latency_window"]["median_us"],
        hw=r["latency_hw"]["median_us"] if "latency_hw" in r else None,
        thr=thr,
        idle=pw["idle_w"] if pw else None, run=pw["run_w"] if pw else None,
        dw=pw["delta_w"] * 1e3 if pw else None,
        # energi: pakai hasil ukur berulang (throughput saat ukur daya, mis. mode loop FPGA) bila ada
        e_tot=(pw.get("energy_total_uj", pw["run_w"] / thr * 1e6) if pw else None),
        e_dyn=(pw.get("energy_dyn_uj", pw["delta_w"] / thr * 1e6) if pw else None)))

fmt = lambda v, s: "-" if v is None else format(v, s)
hdr = ("| Platform | Akurasi (%) | Macro-F1 | PD F1 | Bit-exact | Latency/window e2e (us) | Latency inti HW (us) "
       "| Throughput (window/s) | P idle (W) | P run (W) | dP (mW) | Energi/inferensi total (uJ) | Energi dinamis (uJ) |")
lines = [hdr, "|" + "---|" * 13]
for r in rows:
    lines.append(f"| {r['platform']} | {r['acc']:.2f} | {r['f1']:.4f} | {r['pdf1']:.4f} | {r['exact']} | "
                 f"{r['lat']:.2f} | {fmt(r['hw'], '.2f')} | {r['thr']:.1f} | {fmt(r['idle'], '.3f')} | "
                 f"{fmt(r['run'], '.3f')} | {fmt(r['dw'], '.1f')} | {fmt(r['e_tot'], '.2f')} | {fmt(r['e_dyn'], '.2f')} |")
fp = [r for r in rows if r["platform"] == "fpga"]
for r in rows:
    if fp and r["platform"] != "fpga":
        lines.append(f"\nSpeed-up e2e FPGA vs {r['platform']}: {r['lat']/fp[0]['lat']:.1f}x"
                     + (f" | inti HW vs {r['platform']}: {r['lat']/fp[0]['hw']:.1f}x" if fp[0]["hw"] else ""))
out = "\n".join(lines)
print(out)
open("comparison.md", "w", encoding="utf-8").write(out + "\n")
print("-> comparison.md")
