"""Fungsi bersama benchmark NeuroEdge di KV260: data, metrik, statistik latency, monitor daya.
Dipakai oleh bench_cpu.py, bench_fpga.py, power_mon.py, compare.py."""
import glob, json, os, threading, time
import numpy as np

CLASS_NAMES = ["air", "void", "corona", "noise"]


# ---------------- data ----------------
def load_data(d="."):
    """X [N,125,8] uint8, y [N], W1 [128,8], W2 [4,128], params, exp [N,5] (4 counts + kelas golden)."""
    Z = np.load(os.path.join(d, "test_vectors.npz"))
    P = json.load(open(os.path.join(d, "params.json")))
    X = Z["X_test"].astype(np.uint8)
    y = Z["y_test"].astype(np.int64)
    W1 = Z["w1"].astype(np.int64)
    W2 = Z["w2"].astype(np.int64)
    exp = np.loadtxt(os.path.join(d, "win_exp.txt"), dtype=np.int64, ndmin=2)
    return X, y, W1, W2, P, exp


# ---------------- metrik akurasi ----------------
def metrics(y, pred, n_cls=4, noise_cls=3):
    y, pred = np.asarray(y), np.asarray(pred)
    cm = np.zeros((n_cls, n_cls), np.int64)
    for t, p in zip(y, pred):
        cm[t, p] += 1
    f1 = []
    for k in range(n_cls):
        tp = cm[k, k]; fp = cm[:, k].sum() - tp; fn = cm[k].sum() - tp
        f1.append(2 * tp / max(2 * tp + fp + fn, 1))
    pt, pp = (y != noise_cls), (pred != noise_cls)
    tp = int((pt & pp).sum())
    prec = tp / max(int(pp.sum()), 1)
    rec = tp / max(int(pt.sum()), 1)
    return dict(n=int(len(y)), accuracy=float(np.trace(cm) / cm.sum()),
                macro_f1=float(np.mean(f1)),
                pd_precision=float(prec), pd_recall=float(rec),
                pd_f1=float(2 * prec * rec / max(prec + rec, 1e-12)),
                confusion=cm.tolist())


def print_metrics(m):
    print(f"  akurasi 4 kelas : {m['accuracy']*100:.2f}%  ({int(round(m['accuracy']*m['n']))}/{m['n']})")
    print(f"  macro-F1        : {m['macro_f1']:.4f}")
    print(f"  PD vs noise     : precision {m['pd_precision']:.4f} | recall {m['pd_recall']:.4f} | F1 {m['pd_f1']:.4f}")
    print("  confusion (baris = benar; kolom = " + ", ".join(CLASS_NAMES) + ")")
    for name, row in zip(CLASS_NAMES, m["confusion"]):
        print(f"    {name:>6} {row}")


# ---------------- latency ----------------
def latency_stats(ns):
    us = np.asarray(ns, np.float64) / 1e3
    return dict(n=int(len(us)), median_us=float(np.median(us)), mean_us=float(us.mean()),
                p95_us=float(np.percentile(us, 95)), min_us=float(us.min()), max_us=float(us.max()))


def print_latency(label, s):
    print(f"  {label:<28}: median {s['median_us']:9.2f} us | mean {s['mean_us']:9.2f} | "
          f"p95 {s['p95_us']:9.2f} | min {s['min_us']:9.2f}")


# ---------------- daya (hwmon, mis. INA260 di KV260) ----------------
def find_power_sensor():
    """Cari sensor daya di /sys/class/hwmon. Utamakan ina260 (sensor daya SOM di KV260).
    Satuan power1_input di hwmon = mikrowatt."""
    cands = []
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        f = os.path.join(d, "power1_input")
        if not os.path.exists(f):
            continue
        try:
            name = open(os.path.join(d, "name")).read().strip()
        except OSError:
            name = "?"
        cands.append((name, f))
    for name, f in cands:
        if name.startswith("ina260"):
            return name, f
    return cands[0] if cands else (None, None)


class PowerMonitor:
    """Sampling daya di thread terpisah selama start()..stop()."""
    def __init__(self, period=0.02):
        self.name, self.path = find_power_sensor()
        self.period = period
        self.samples = []

    @property
    def ok(self):
        return self.path is not None

    def _read(self):
        with open(self.path) as f:
            return int(f.read()) / 1e6          # uW -> W

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.samples.append(self._read())
            except OSError:
                pass
            self._stop.wait(self.period)

    def start(self):
        self.samples = []
        if self.ok:
            self._stop = threading.Event()
            self._th = threading.Thread(target=self._loop, daemon=True)
            self._th.start()
        return self

    def stop(self):
        if self.ok:
            self._stop.set()
            self._th.join()
        if not self.samples:
            return None
        s = np.asarray(self.samples)
        return dict(mean_w=float(s.mean()), std_w=float(s.std()), n_samples=int(len(s)))

    def measure_idle(self, seconds):
        self.start()
        time.sleep(seconds)
        return self.stop()


def summarize_power(sensor, rows):
    """rows: list dict(idle_w, run_w, throughput) dari tiap ulangan -> ringkasan rata-rata +- std."""
    if not rows:
        return None
    a = {k: np.array([r[k] for r in rows], np.float64) for k in ("idle_w", "run_w", "throughput")}
    d = a["run_w"] - a["idle_w"]
    e_tot = a["run_w"] / a["throughput"] * 1e6
    e_dyn = d / a["throughput"] * 1e6
    return dict(sensor=sensor, repeats=rows, n_repeats=len(rows),
                idle_w=float(a["idle_w"].mean()), run_w=float(a["run_w"].mean()),
                delta_w=float(d.mean()), delta_std_w=float(d.std()),
                run_std_w=float(a["run_w"].std()),
                throughput=float(a["throughput"].mean()),
                energy_total_uj=float(e_tot.mean()), energy_dyn_uj=float(e_dyn.mean()),
                energy_dyn_std_uj=float(e_dyn.std()))


def measure_power_cycles(mon, idle_s, run_s, run_fn, repeats):
    """Ulangi `repeats` kali: idle (CPU tidur idle_s detik) lalu run (run_fn(run_s) -> jumlah inferensi).
    Daya rata-rata tiap fase dibaca dari sensor; throughput = inferensi / waktu run."""
    rows = []
    for r in range(repeats):
        idle = mon.measure_idle(idle_s)
        mon.start()
        t0 = time.perf_counter()
        n = run_fn(run_s)
        el = time.perf_counter() - t0
        run = mon.stop()
        if not idle or not run or n <= 0:
            print("  [peringatan] sampel daya kosong / tidak ada inferensi, ulangan dilewati")
            continue
        row = dict(idle_w=idle["mean_w"], run_w=run["mean_w"], throughput=n / el,
                   inferences=int(n), run_seconds=el, samples_idle=idle["n_samples"], samples_run=run["n_samples"])
        rows.append(row)
        print(f"  [{r+1}/{repeats}] idle {row['idle_w']:.3f} W | run {row['run_w']:.3f} W | "
              f"delta {(row['run_w']-row['idle_w'])*1e3:+.1f} mW | {row['throughput']:.1f} inferensi/s")
    return summarize_power(mon.name, rows)


def sys_info():
    """Kondisi sistem yang mempengaruhi daya/latency (dicatat di JSON hasil)."""
    def rd(p):
        try:
            return open(p).read().strip()
        except OSError:
            return None
    f = rd("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    return dict(cpu_governor=rd("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
                cpu_freq_mhz=(int(f) / 1000 if f else None),
                fpga_state=rd("/sys/class/fpga_manager/fpga0/state"),
                hostname=os.uname().nodename if hasattr(os, "uname") else None)


def print_sys_info(info):
    print(f"  sistem          : governor={info['cpu_governor']} | cpu {info['cpu_freq_mhz']} MHz | "
          f"fpga_manager={info['fpga_state']}")
    if info["cpu_governor"] not in (None, "performance"):
        print("  [saran] set governor 'performance' supaya frekuensi CPU stabil (lihat langkah di panduan)")


def power_block(sensor, idle, run):
    """Gabungkan hasil idle dan run ke satu dict (None jika sensor tidak ada)."""
    if not idle or not run:
        return None
    return dict(sensor=sensor, idle_w=idle["mean_w"], run_w=run["mean_w"],
                delta_w=run["mean_w"] - idle["mean_w"], run_std_w=run["std_w"],
                n_samples_idle=idle["n_samples"], n_samples_run=run["n_samples"])


def print_power(pw, throughput=None):
    if not pw:
        print("  daya            : sensor hwmon tidak ditemukan -> cek 'python3 power_mon.py --list' atau ukur manual (PSU/multimeter)")
        return
    n = pw.get("n_repeats", 1)
    print(f"  daya idle       : {pw['idle_w']:.3f} W   (rata-rata {n} ulangan)")
    print(f"  daya saat jalan : {pw['run_w']:.3f} W  (std antar ulangan {pw['run_std_w']*1e3:.1f} mW)")
    print(f"  delta (dinamis) : {pw['delta_w']*1e3:.1f} mW +- {pw.get('delta_std_w', 0)*1e3:.1f}")
    if "energy_dyn_uj" in pw:
        print(f"  throughput saat ukur daya: {pw['throughput']:.1f} inferensi/s")
        print(f"  energi/inferensi: total {pw['energy_total_uj']:.3f} uJ | "
              f"dinamis {pw['energy_dyn_uj']:.3f} +- {pw['energy_dyn_std_uj']:.3f} uJ")
    elif throughput:
        print(f"  energi/inferensi: total {pw['run_w']/throughput*1e6:.2f} uJ | "
              f"dinamis (delta) {pw['delta_w']/throughput*1e6:.2f} uJ")
    if pw["delta_w"] < 0.02:
        print("  [catatan] delta < 20 mW: dekat resolusi sensor, perpanjang durasi / tambah ulangan")


def save_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
    print(f"-> {path}")


def save_pred(path, pred):
    np.savetxt(path, np.asarray(pred, np.int64), fmt="%d")
