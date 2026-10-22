"""Ukur daya sambil menjalankan perintah lain (dipakai untuk baseline C).
Tiap ulangan: ukur daya idle (CPU tidur) -> jalankan perintah sambil sampling.
Throughput diambil dari file JSON hasil perintah itu (field throughput_inf_per_s),
lalu ringkasan daya ditulis ke field "power" di file yang sama.

Contoh di board:
    python3 power_mon.py --list
    python3 power_mon.py --idle 15 --repeats 3 --merge results_cpu_c.json -- taskset -c 1 ./snn_cpu 20
"""
import argparse, glob, json, os, subprocess, sys, time
from common import PowerMonitor, summarize_power, print_power, sys_info, print_sys_info


def main():
    argv = sys.argv[1:]
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle", type=float, default=15.0, help="detik idle per ulangan")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--merge", help="file JSON hasil benchmark (dibaca throughput-nya, ditambah field power)")
    ap.add_argument("--list", action="store_true", help="tampilkan semua sensor hwmon")
    a = ap.parse_args(argv)

    if a.list:
        for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            try:
                name = open(os.path.join(d, "name")).read().strip()
            except OSError:
                name = "?"
            vals = []
            for f in sorted(glob.glob(os.path.join(d, "*_input"))):
                try:
                    vals.append(f"{os.path.basename(f)}={open(f).read().strip()}")
                except OSError:
                    pass
            print(f"{d}: {name}  {' '.join(vals)}")
        print("(satuan hwmon: power*_input uW, curr*_input mA, in*_input mV, temp*_input m°C)")
        return

    mon = PowerMonitor()
    if not mon.ok:
        sys.exit("Sensor daya hwmon tidak ditemukan. Coba: python3 power_mon.py --list, atau ukur manual.")
    if not cmd or not a.merge:
        sys.exit("Contoh: python3 power_mon.py --merge results_cpu_c.json -- ./snn_cpu 20")

    info = sys_info()
    print(f"sensor: {mon.name} ({mon.path})")
    print_sys_info(info)
    rows = []
    for r in range(a.repeats):
        idle = mon.measure_idle(a.idle)
        mon.start()
        t0 = time.perf_counter()
        rc = subprocess.call(cmd, stdout=subprocess.DEVNULL)
        el = time.perf_counter() - t0
        run = mon.stop()
        res = json.load(open(a.merge))
        thr = res.get("throughput_inf_per_s")
        if rc not in (0,) or not idle or not run or not thr:
            print(f"  [{r+1}/{a.repeats}] dilewati (exit {rc}, throughput {thr})")
            continue
        rows.append(dict(idle_w=idle["mean_w"], run_w=run["mean_w"], throughput=thr,
                         run_seconds=el, samples_idle=idle["n_samples"], samples_run=run["n_samples"]))
        print(f"  [{r+1}/{a.repeats}] idle {idle['mean_w']:.3f} W | run {run['mean_w']:.3f} W | "
              f"delta {(run['mean_w']-idle['mean_w'])*1e3:+.1f} mW | {thr:.1f} inferensi/s | {el:.1f} s")

    pw = summarize_power(mon.name, rows)
    if pw:
        pw["command"] = " ".join(cmd)
    print_power(pw)
    res = json.load(open(a.merge))
    res["power"] = pw
    res["sys"] = info
    with open(a.merge, "w") as f:
        json.dump(res, f, indent=2)
    print(f"-> power ditambahkan ke {a.merge}")


if __name__ == "__main__":
    main()
