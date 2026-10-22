"""Benchmark akselerator SNN di PL KV260 lewat AXI4-Lite (snn_axi_wrapper.v).
Peta register sama dengan snn_axi_wrapper.v:
  0x000 CTRL [0]=start [1]=core reset [2]=loop | 0x004 STATUS [0]=done [1]=busy [2]=pd
  0x008 COUNTS | 0x00C CLASS | 0x010 LATENCY (clock) | 0x014 ID "SNN1" | 0x018 RUNS | 0x100.. WINDOW (32 word)

Daya (sensor INA di SOM, total PS+PL+DDR): tiap ulangan
  idle : bitstream dimuat, inti diam, CPU tidur             -> P_idle
  run  : mode loop (inti mengulang 1 window terus), CPU tidur -> P_run
  delta = P_run - P_idle = daya dinamis akselerator ; energi/inferensi = delta / (run per detik)

Jalankan di board (biasanya perlu root):
  sudo python3 bench_fpga.py --bit neuroedge.bit           # PYNQ: muat overlay (.hwh harus satu folder, nama sama)
  sudo python3 bench_fpga.py --base 0xA0000000 --fclk 100e6 # bitstream sudah dimuat (xmutil/fpgautil), akses /dev/mem
Alamat base dan frekuensi PL clock harus sama dengan block design Vivado.

Yang diukur per window:
  hw      : register LATENCY / f_clk                -> latency inti SNN murni (sama dengan simulasi: ~376 clock)
  compute : tulis start .. done terbaca            -> inti + overhead polling dari CPU
  e2e     : tulis 32 word window .. hasil terbaca  -> yang dirasakan aplikasi (termasuk transfer AXI dari Python)
"""
import argparse, mmap, os, time
import numpy as np
from common import *

REG_CTRL, REG_STATUS, REG_COUNTS, REG_CLASS, REG_LAT, REG_ID, REG_RUNS, REG_WIN = \
    0x0, 0x4, 0x8, 0xC, 0x10, 0x14, 0x18, 0x100
CTRL_START, CTRL_RESET, CTRL_LOOP = 0x1, 0x2, 0x4
ID_SNN1 = 0x534E4E31


class DevMem:
    """Akses register lewat /dev/mem (tanpa PYNQ). Antarmuka read/write sama dengan pynq.MMIO."""
    def __init__(self, base, size=0x1000):
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.m = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=base)
        os.close(fd)
        self.a = np.frombuffer(self.m, np.uint32)

    def read(self, off):
        return int(self.a[off >> 2])

    def write(self, off, v):
        self.a[off >> 2] = v


def open_dev(a):
    if a.base is not None:
        return DevMem(int(a.base, 0)), (a.fclk or 100e6), f"/dev/mem @ {a.base}"
    from pynq import Overlay, MMIO
    ol = Overlay(a.bit)
    names = [n for n in ol.ip_dict if "snn" in n.lower()]
    if not names:
        raise SystemExit(f"IP 'snn' tidak ditemukan di overlay. IP yang ada: {list(ol.ip_dict)}")
    info = ol.ip_dict[names[0]]
    fclk = a.fclk
    if fclk is None:
        try:
            from pynq import Clocks
            fclk = Clocks.fclk0_mhz * 1e6
        except Exception:
            fclk = 100e6
            print("[peringatan] frekuensi PL clock tidak terbaca, asumsi 100 MHz (pakai --fclk)")
    return MMIO(info["phys_addr"], info["addr_range"]), fclk, f"overlay {a.bit}, IP {names[0]} @ {hex(info['phys_addr'])}"


def pack_windows(X):
    """[N,125,8] -> [N,32] uint32. Byte t = spike 8 channel (bit c = channel c), 4 byte per word, little-endian."""
    N, T, C = X.shape
    b = (X.astype(np.uint32) << np.arange(C, dtype=np.uint32)).sum(axis=2)       # [N, T]
    pad = np.zeros((N, 128), np.uint32)
    pad[:, :T] = b
    return (pad.reshape(N, 32, 4) << (8 * np.arange(4, dtype=np.uint32))).sum(axis=2).astype(np.uint32)


def wait_idle(dev, guard=1_000_000):
    """Tunggu inti tidak busy (dipakai setelah mematikan mode loop)."""
    g = 0
    while dev.read(REG_STATUS) & 2:
        g += 1
        if g > guard:
            raise RuntimeError("timeout menunggu busy turun")


def run_window(dev, words, guard=1_000_000):
    t0 = time.perf_counter_ns()
    for k, v in enumerate(words):
        dev.write(REG_WIN + 4 * k, v)
    t1 = time.perf_counter_ns()
    dev.write(REG_CTRL, 1)
    g = 0
    while not (dev.read(REG_STATUS) & 1):
        g += 1
        if g > guard:
            raise RuntimeError("timeout menunggu done (cek clock/reset PL)")
    t2 = time.perf_counter_ns()
    cnt = dev.read(REG_COUNTS)
    cls = dev.read(REG_CLASS) & 3
    cyc = dev.read(REG_LAT) & 0xFFFF
    t3 = time.perf_counter_ns()
    counts = [(cnt >> (8 * i)) & 0xFF for i in range(4)]
    return counts, cls, cyc, (t3 - t0, t2 - t1, t1 - t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="neuroedge.bit")
    ap.add_argument("--base", help="alamat AXI IP (mis. 0xA0000000) -> pakai /dev/mem, tanpa PYNQ")
    ap.add_argument("--fclk", type=float, help="frekuensi PL clock (Hz), mis. 100e6")
    ap.add_argument("--data", default=".")
    ap.add_argument("--power-seconds", type=float, default=20.0, help="lama fase run (mode loop) per ulangan; 0 = skip daya")
    ap.add_argument("--idle-seconds", type=float, default=15.0, help="lama fase idle per ulangan")
    ap.add_argument("--repeats", type=int, default=3, help="jumlah ulangan idle/run")
    ap.add_argument("--loop-window", type=int, default=0, help="indeks window yang diulang saat mode loop")
    ap.add_argument("--label", default="fpga")
    a = ap.parse_args()
    info = sys_info()

    X, y, W1, W2, P, exp = load_data(a.data)
    N = len(X)
    words = pack_windows(X).tolist()

    dev, fclk, desc = open_dev(a)
    ident = dev.read(REG_ID)
    print(f"[{a.label}] {desc} | f_clk {fclk/1e6:.1f} MHz | ID 0x{ident:08X}")
    print_sys_info(info)
    if ident != ID_SNN1:
        raise SystemExit("ID register bukan 'SNN1' -> alamat base salah atau bitstream belum dimuat")
    dev.write(REG_CTRL, CTRL_RESET)                 # reset inti
    dev.write(REG_CTRL, 0)

    for w in range(min(10, N)):                     # warm-up
        run_window(dev, words[w])

    counts = np.zeros((N, 4), np.int64)
    pred = np.zeros(N, np.int64)
    cyc = np.zeros(N, np.int64)
    e2e, comp, xfer = (np.zeros(N, np.int64) for _ in range(3))
    for w in range(N):
        c, k, cy, (te, tc, tx) = run_window(dev, words[w])
        counts[w], pred[w], cyc[w] = c, k, cy
        e2e[w], comp[w], xfer[w] = te, tc, tx

    mism = int(((counts != exp[:, :4]).any(axis=1) | (pred != exp[:, 4])).sum())
    m = metrics(y, pred)
    hw = latency_stats(cyc / fclk * 1e9)
    lc, le, lx = latency_stats(comp), latency_stats(e2e), latency_stats(xfer)
    print(f"  bit-exact vs win_exp.txt: {N - mism}/{N} window cocok")
    print_metrics(m)
    print(f"  clock per window (register): median {int(np.median(cyc))} (min {cyc.min()}, max {cyc.max()})")
    print_latency("hw: inti SNN (cycle/f_clk)", hw)
    print_latency("compute: start..done (CPU)", lc)
    print_latency("transfer window 32 word", lx)
    print_latency("e2e: tulis..baca hasil", le)

    thr = 1e6 / le["mean_us"]
    print(f"  throughput (e2e lewat Python): {thr:.1f} window/s | batas inti: {fclk/np.median(cyc):.0f} window/s")

    # ---------------- daya: mode loop, CPU tidur ----------------
    pw, loop_ok = None, None
    if a.power_seconds > 0:
        mon = PowerMonitor()
        dev.write(REG_CTRL, CTRL_LOOP)              # cek bitstream mendukung mode loop
        has_loop = bool(dev.read(REG_CTRL) & CTRL_LOOP)
        dev.write(REG_CTRL, 0)
        wait_idle(dev)
        if not mon.ok:
            print("  daya            : sensor hwmon tidak ditemukan -> cek 'python3 power_mon.py --list'")
        elif not has_loop:
            print("  daya            : bitstream belum punya mode loop (CTRL[2]) -> build ulang dengan snn_axi_wrapper.v terbaru")
        else:
            w0 = a.loop_window
            for k, v in enumerate(words[w0]):
                dev.write(REG_WIN + 4 * k, v)

            def run_fn(seconds):
                r0 = dev.read(REG_RUNS)
                dev.write(REG_CTRL, CTRL_LOOP)
                time.sleep(seconds)                 # CPU tidur, inti bekerja sendiri
                r1 = dev.read(REG_RUNS)
                dev.write(REG_CTRL, 0)
                wait_idle(dev)
                return (r1 - r0) & 0xFFFFFFFF

            print(f"  ukur daya ({mon.name}): {a.repeats} x [idle {a.idle_seconds:.0f} s -> loop {a.power_seconds:.0f} s], window {w0}")
            pw = measure_power_cycles(mon, a.idle_seconds, a.power_seconds, run_fn, a.repeats)
            cnt = dev.read(REG_COUNTS)
            loop_ok = bool([(cnt >> (8 * i)) & 0xFF for i in range(4)] == exp[w0, :4].tolist() and
                           (dev.read(REG_CLASS) & 3) == int(exp[w0, 4]))
            print(f"  hasil run terakhir mode loop vs golden window {w0}: {'cocok' if loop_ok else 'BEDA'}")
            if pw:
                print(f"  clock per run (loop): {fclk / pw['throughput']:.0f}")
    print_power(pw)

    save_pred(f"pred_{a.label}.txt", pred)
    save_json(f"results_{a.label}.json", dict(
        platform=a.label, sys=info, fclk_hz=fclk, bit_exact_ok=N - mism, n=N, metrics=m,
        cycles_per_window=int(np.median(cyc)), latency_hw=hw, latency_compute=lc,
        latency_transfer=lx, latency_window=le, throughput_inf_per_s=thr,
        throughput_core_only=float(fclk / np.median(cyc)), loop_result_ok=loop_ok, power=pw))


if __name__ == "__main__":
    main()
