"""Baseline CPU (Python/NumPy): SNN integer 8-128-4 dijalankan di CPU ARM KV260, batch = 1 window.
Aritmetika identik dengan lif_neuron.v / gen_win_vec.py (reset delayed, strict >, saturasi int16),
jadi hasilnya harus bit-exact dengan win_exp.txt.

Jalankan di board (dari folder board/):
    taskset -c 1 python3 bench_cpu.py                    # akurasi + latency + daya (3 ulangan idle/run)
    taskset -c 1 python3 bench_cpu.py --repeats 5        # lebih banyak ulangan
    python3 bench_cpu.py --power-seconds 0               # tanpa pengukuran daya
Daya: tiap ulangan = idle (CPU tidur) lalu run (inferensi terus). delta = run - idle = biaya inferensi di CPU.
"""
import argparse, time
import numpy as np
from common import *


def make_infer(W1, W2, P):
    L1, L2 = P["layers"]
    thr1, thr2 = L1["threshold_int"], L2["threshold_int"]
    d1, d2 = L1["decay_shift"], L2["decay_shift"]
    s1, s2 = L1["gain_shift"] + L1["frac_bits"], L2["gain_shift"] + L2["frac_bits"]
    H, O = W1.shape[0], W2.shape[0]

    def infer(x):                                  # x [T, 8] int64 (0/1)
        m1 = np.zeros(H, np.int64)
        m2 = np.zeros(O, np.int64)
        cnt = np.zeros(O, np.int64)
        for t in range(x.shape[0]):
            r1 = m1 > thr1                         # reset delayed: dari membran timestep lalu
            m1 = m1 - (m1 >> d1) + ((W1 @ x[t]) << s1) - r1 * thr1
            np.clip(m1, -32768, 32767, out=m1)
            sp1 = (m1 > thr1).astype(np.int64)
            r2 = m2 > thr2
            m2 = m2 - (m2 >> d2) + ((W2 @ sp1) << s2) - r2 * thr2
            np.clip(m2, -32768, 32767, out=m2)
            cnt += m2 > thr2
        return cnt

    return infer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=".")
    ap.add_argument("--power-seconds", type=float, default=20.0, help="lama fase run per ulangan (0 = skip daya)")
    ap.add_argument("--idle-seconds", type=float, default=15.0, help="lama fase idle per ulangan")
    ap.add_argument("--repeats", type=int, default=3, help="jumlah ulangan idle/run")
    ap.add_argument("--label", default="cpu_numpy")
    a = ap.parse_args()
    info = sys_info()

    X, y, W1, W2, P, exp = load_data(a.data)
    Xi = X.astype(np.int64)
    infer = make_infer(W1, W2, P)
    N = len(X)

    print(f"[{a.label}] {N} window, batch = 1")
    print_sys_info(info)
    for w in range(min(10, N)):                    # warm-up
        infer(Xi[w])

    counts = np.zeros((N, 4), np.int64)
    lat = np.zeros(N, np.int64)
    for w in range(N):
        t0 = time.perf_counter_ns()
        counts[w] = infer(Xi[w])
        lat[w] = time.perf_counter_ns() - t0
    pred = counts.argmax(axis=1)                   # seri -> indeks terkecil, sama dengan RTL

    mism = int(((counts != exp[:, :4]).any(axis=1) | (pred != exp[:, 4])).sum())
    m = metrics(y, pred)
    ls = latency_stats(lat)
    print(f"  bit-exact vs win_exp.txt: {N - mism}/{N} window cocok")
    print_metrics(m)
    print_latency("latency per window (CPU)", ls)

    # throughput + daya: loop semua window berulang selama power-seconds, diulang `repeats` kali
    def run_fn(seconds):
        n, t0 = 0, time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            infer(Xi[n % N])
            n += 1
        return n

    pw, thr = None, 1e6 / ls["mean_us"]
    if a.power_seconds > 0:
        mon = PowerMonitor()
        if mon.ok:
            print(f"  ukur daya ({mon.name}): {a.repeats} x [idle {a.idle_seconds:.0f} s -> run {a.power_seconds:.0f} s]")
            pw = measure_power_cycles(mon, a.idle_seconds, a.power_seconds, run_fn, a.repeats)
            if pw:
                thr = pw["throughput"]
        else:
            n = run_fn(min(a.power_seconds, 5.0))
            thr = n / min(a.power_seconds, 5.0)
    print(f"  throughput      : {thr:.1f} window/s")
    print_power(pw, thr)

    save_pred(f"pred_{a.label}.txt", pred)
    save_json(f"results_{a.label}.json", dict(
        platform=a.label, sys=info, bit_exact_ok=N - mism, n=N, metrics=m,
        latency_window=ls, throughput_inf_per_s=thr, power=pw))


if __name__ == "__main__":
    main()
