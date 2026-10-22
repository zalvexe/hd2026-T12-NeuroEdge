"""NeuroEdge SNN di CPU (mis. ARM Cortex-A53 KV260), end-to-end dari CSV mentah.
Alur per window (batch = 1), sama dengan notebook + RTL:
  1) normalisasi per-window   : x / (max|x| + 1e-12)
  2) encoding level-crossing  : d = diff(x) ; ch k = d >= th_k (naik), ch 4+k = d <= -th_k (turun)
  3) SNN integer 8-128-4      : reset delayed, strict >, saturasi int16 (identik lif_neuron.v)
  4) prediksi                 : argmax jumlah spike output (seri -> indeks terkecil)

Hanya butuh numpy. Susunan folder:
  run_snn_cpu.py
  params.json
  w1_int8.mem
  w2_int8.mem
  dataset_1/PD_in_Air_12kV.csv, PD_in_Void_25kV.csv, Corona_11kV.csv, Noise_NoPD.csv

Contoh:
  python3 run_snn_cpu.py                     # 600 window test (baris 850..999 tiap kelas)
  python3 run_snn_cpu.py --split all         # semua 4000 window
  python3 run_snn_cpu.py --power 20          # + ukur daya 20 detik (sensor hwmon, mis. INA260)
  taskset -c 1 python3 run_snn_cpu.py        # kunci ke 1 core supaya latency stabil
"""
import argparse, glob, json, os, threading, time
import numpy as np

FILES = ["PD_in_Air_12kV.csv", "PD_in_Void_25kV.csv", "Corona_11kV.csv", "Noise_NoPD.csv"]
CLASS_NAMES = ["air", "void", "corona", "noise"]
TEST_START = 850                       # block split notebook: train 0..699, val 700..849, test 850..999


# ---------------- data & parameter ----------------
def load_csv(data_dir, split):
    X, y, rows = [], [], []
    for c, name in enumerate(FILES):
        a = np.loadtxt(os.path.join(data_dir, name), delimiter=";", dtype=np.float64, ndmin=2).astype(np.float32)
        if a.shape != (1000, 125):
            raise SystemExit(f"{name}: ukuran {a.shape}, harusnya (1000, 125) -> file CSV tidak lengkap, salin ulang dataset_1/")
        r = np.arange(len(a)) if split == "all" else np.arange(TEST_START, len(a))
        X.append(a[r]); y.append(np.full(len(r), c)); rows.append(r)
    return np.concatenate(X), np.concatenate(y), np.concatenate(rows)


def load_mem(path, shape):
    v = np.array([int(t, 16) for t in open(path).read().split()], np.int64)
    v = np.where(v > 127, v - 256, v)                       # int8 two's complement
    return v.reshape(shape)                                 # row-major (out, in)


# ---------------- pipeline ----------------
def encode(x, th):
    """x [125] float32 -> spike [125, 8] (int64 0/1)."""
    xn = x / (np.abs(x).max() + 1e-12)
    d = np.diff(xn, prepend=xn[:1])
    return np.concatenate([d[:, None] >= th[None, :], d[:, None] <= -th[None, :]], axis=1).astype(np.int64)


def make_snn(W1, W2, P):
    L1, L2 = P["layers"]
    thr1, thr2 = L1["threshold_int"], L2["threshold_int"]
    d1, d2 = L1["decay_shift"], L2["decay_shift"]
    s1, s2 = L1["gain_shift"] + L1["frac_bits"], L2["gain_shift"] + L2["frac_bits"]

    def snn(spk):                                           # spk [T, 8] -> counts [4]
        m1 = np.zeros(W1.shape[0], np.int64)
        m2 = np.zeros(W2.shape[0], np.int64)
        cnt = np.zeros(W2.shape[0], np.int64)
        for t in range(spk.shape[0]):
            r1 = m1 > thr1                                  # reset dari membran timestep lalu
            m1 = m1 - (m1 >> d1) + ((W1 @ spk[t]) << s1) - r1 * thr1
            np.clip(m1, -32768, 32767, out=m1)
            h = (m1 > thr1).astype(np.int64)
            r2 = m2 > thr2
            m2 = m2 - (m2 >> d2) + ((W2 @ h) << s2) - r2 * thr2
            np.clip(m2, -32768, 32767, out=m2)
            cnt += m2 > thr2
        return cnt

    return snn


# ---------------- daya (opsional) ----------------
def find_power_sensor():
    cands = []
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        f = os.path.join(d, "power1_input")                 # satuan mikrowatt
        if os.path.exists(f):
            try:
                name = open(os.path.join(d, "name")).read().strip()
            except OSError:
                name = "?"
            cands.append((name, f))
    for c in cands:
        if c[0].startswith("ina260"):
            return c
    return cands[0] if cands else (None, None)


def sample_power(path, stop, out, period=0.02):
    while not stop.is_set():
        try:
            out.append(int(open(path).read()) / 1e6)
        except (OSError, ValueError):
            pass
        stop.wait(period)


def measure_power(path, seconds, work=None):
    """Rata-rata daya (W) selama `seconds`; jika work diberikan, work() dipanggil berulang. Return (W, jumlah work)."""
    stop, samples, n = threading.Event(), [], 0
    th = threading.Thread(target=sample_power, args=(path, stop, samples), daemon=True)
    th.start()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        if work:
            work(n); n += 1
        else:
            time.sleep(0.05)
    el = time.perf_counter() - t0
    stop.set(); th.join()
    return (float(np.mean(samples)) if samples else None), n, el


# ---------------- main ----------------
def stats_us(ns):
    us = np.asarray(ns) / 1e3
    return f"median {np.median(us):9.2f} us | mean {us.mean():9.2f} | p95 {np.percentile(us, 95):9.2f}"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(here, "dataset_1"), help="folder CSV dataset")
    ap.add_argument("--params", default=os.path.join(here, "params.json"))
    ap.add_argument("--w1", default=os.path.join(here, "w1_int8.mem"))
    ap.add_argument("--w2", default=os.path.join(here, "w2_int8.mem"))
    ap.add_argument("--split", choices=["test", "all"], default="test")
    ap.add_argument("--power", type=float, default=0, help="detik pengukuran daya (0 = tidak)")
    ap.add_argument("--out", default="pred_run_snn_cpu.txt")
    a = ap.parse_args()

    P = json.load(open(a.params))
    th = np.asarray(P["thresholds_float"], np.float64)
    W1 = load_mem(a.w1, P["layers"][0]["shape"])
    W2 = load_mem(a.w2, P["layers"][1]["shape"])
    snn = make_snn(W1, W2, P)
    X, y, rows = load_csv(a.data, a.split)
    N = len(X)
    print(f"NeuroEdge SNN CPU | {N} window ({a.split}) | model {P['n_in']}-{P['hidden']}-{P['n_out']} | T={P['T']}")

    for i in range(min(10, N)):                             # warm-up
        snn(encode(X[i], th))

    pred = np.zeros(N, np.int64)
    counts = np.zeros((N, 4), np.int64)
    t_enc, t_snn = np.zeros(N, np.int64), np.zeros(N, np.int64)
    n_in_spk = 0
    for i in range(N):
        t0 = time.perf_counter_ns()
        s = encode(X[i], th)
        t1 = time.perf_counter_ns()
        counts[i] = snn(s)
        t2 = time.perf_counter_ns()
        pred[i] = int(np.argmax(counts[i]))
        t_enc[i], t_snn[i] = t1 - t0, t2 - t1
        n_in_spk += int(s.sum())

    # ---- akurasi ----
    cm = np.zeros((4, 4), np.int64)
    for t, p in zip(y, pred):
        cm[t, p] += 1
    acc = np.trace(cm) / N
    f1 = [2 * cm[k, k] / max(cm[k].sum() + cm[:, k].sum(), 1) for k in range(4)]
    pt, pp = y != 3, pred != 3
    prec = (pt & pp).sum() / max(pp.sum(), 1)
    rec = (pt & pp).sum() / max(pt.sum(), 1)
    print(f"\nAkurasi 4 kelas : {acc*100:.2f}% ({np.trace(cm)}/{N}) | macro-F1 {np.mean(f1):.4f}")
    print(f"PD vs noise     : precision {prec:.4f} | recall {rec:.4f} | F1 {2*prec*rec/max(prec+rec,1e-12):.4f}")
    print("Confusion (baris = benar; kolom = " + ", ".join(CLASS_NAMES) + ")")
    for name, r in zip(CLASS_NAMES, cm.tolist()):
        print(f"  {name:>6} {r}")
    print(f"Spike input rata-rata/window: {n_in_spk / N:.1f}")
    exp_path = os.path.join(here, "win_exp.txt")             # golden yang dipakai RTL/FPGA
    if a.split == "test" and os.path.exists(exp_path):
        exp = np.loadtxt(exp_path, dtype=np.int64, ndmin=2)
        if len(exp) == N:
            ok = int(((counts == exp[:, :4]).all(axis=1) & (pred == exp[:, 4])).sum())
            print(f"Bit-exact vs win_exp.txt (golden RTL): {ok}/{N} window cocok")

    # ---- latency ----
    tot = t_enc + t_snn
    print("\nLatency per window (CPU, batch 1):")
    print(f"  encoding : {stats_us(t_enc)}")
    print(f"  SNN      : {stats_us(t_snn)}")
    print(f"  total    : {stats_us(tot)}")
    print(f"  real-time? window sinyal = 250 us -> total median {np.median(tot)/1e3:.1f} us "
          f"({'lebih cepat' if np.median(tot)/1e3 < 250 else 'LEBIH LAMBAT'} dari real-time)")

    # ---- daya ----
    if a.power > 0:
        name, path = find_power_sensor()
        if not path:
            print("\nDaya: sensor hwmon tidak ditemukan (cek 'ls /sys/class/hwmon/*/name' atau ukur dengan PSU/multimeter)")
        else:
            print(f"\nDaya ({name}): ukur idle {a.power:.0f} s ...")
            p_idle, _, _ = measure_power(path, a.power)
            print(f"  ukur saat inferensi {a.power:.0f} s ...")
            p_run, n, el = measure_power(path, a.power, lambda k: snn(encode(X[k % N], th)))
            thr = n / el
            print(f"  idle {p_idle:.3f} W | run {p_run:.3f} W | delta {(p_run-p_idle)*1e3:.1f} mW | {thr:.1f} window/s")
            print(f"  energi/inferensi: total {p_run/thr*1e6:.1f} uJ | dinamis {(p_run-p_idle)/thr*1e6:.1f} uJ")

    with open(a.out, "w") as f:
        for r, t, p, c in zip(rows, y, pred, counts):
            f.write(f"{CLASS_NAMES[t]} row {r}: pred {CLASS_NAMES[p]} counts {c.tolist()}\n")
    print(f"\nPrediksi per window -> {a.out}")


if __name__ == "__main__":
    main()
