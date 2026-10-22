
import argparse, json, mmap, os, struct, sys, time
import numpy as np

REG_CTRL, REG_STATUS, REG_IDX, REG_DATA = 0x00, 0x04, 0x08, 0x0C
REG_CLASS, REG_COUNTS, REG_CYCLES, REG_ID = 0x10, 0x14, 0x18, 0x1C
CTRL_START, CTRL_RESET, CTRL_CLRDONE = 1, 2, 4
ST_BUSY, ST_DONE, ST_ERR, ST_READY, ST_FULL = 1, 2, 4, 8, 16
ID_SNN1 = 0x534E4E31
CLASS_NAMES = ["air", "void", "corona", "noise"]


class DevMem:
    """Akses register lewat /dev/mem (pengganti pynq.MMIO). Interface sama: read(off), write(off, val)."""
    def __init__(self, base, size=0x1000):
        self.fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.mm = mmap.mmap(self.fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=base)

    def read(self, off):
        return struct.unpack_from("<I", self.mm, off)[0]

    def write(self, off, val):
        struct.pack_into("<I", self.mm, off, int(val) & 0xFFFFFFFF)


def open_ip(a):
    if a.base:
        return DevMem(int(a.base, 0)), f"/dev/mem @ {a.base}"
    from pynq import Overlay, MMIO
    ol = Overlay(a.bit, download=not a.pynq_loaded)
    name = next((k for k in ol.ip_dict if "snn" in k.lower()), None)
    if name is None:
        sys.exit(f"IP SNN tidak ada di overlay. IP yang ada: {list(ol.ip_dict)}")
    ip = ol.ip_dict[name]
    return MMIO(ip["phys_addr"], ip["addr_range"]), f"PYNQ {name} @ 0x{ip['phys_addr']:X}"


def to_bytes(x):
    """x [125, 8] (0/1) -> 125 byte, bit c = channel c (sama dengan win_in.hex)."""
    return (x.astype(np.uint8) << np.arange(8, dtype=np.uint8)).sum(axis=1).astype(np.uint8)


def run_window(ip, spk_bytes, timeout_s=1.0):
    """125 byte -> (counts[4], kelas, cycles, waktu_tulis_s, waktu_total_s)."""
    t0 = time.perf_counter()
    ip.write(REG_CTRL, CTRL_CLRDONE)
    ip.write(REG_IDX, 0)
    for b in spk_bytes:                       # 125 x tulis 8 bit spike ke 0x0C
        ip.write(REG_DATA, int(b))
    t1 = time.perf_counter()
    st = ip.read(REG_STATUS)
    if not st & ST_FULL:
        raise RuntimeError(f"belum 125 byte tertulis (STATUS=0x{st:02X})")
    ip.write(REG_CTRL, CTRL_START)
    deadline = time.perf_counter() + timeout_s
    while not (st := ip.read(REG_STATUS)) & ST_DONE:
        if time.perf_counter() > deadline:
            raise RuntimeError(f"timeout menunggu DONE (STATUS=0x{st:02X})")
    t2 = time.perf_counter()
    if st & ST_ERR:
        raise RuntimeError(f"core error (STATUS=0x{st:02X})")
    c = ip.read(REG_COUNTS)
    counts = [(c >> (8 * k)) & 0xFF for k in range(4)]
    return counts, ip.read(REG_CLASS) & 3, ip.read(REG_CYCLES), t1 - t0, t2 - t0


def ref_starter(X, w1, w2, th=(944, 860)):
    """Model NumPy yang sama persis dengan RTL starter (lif_step.v): mem - mem>>3 + sum<<4,
    saturasi int16, spike jika >= threshold, reset dengan pengurangan (langsung di timestep itu).
    -> counts [N, 4]. Dipakai untuk cek bit-exact hasil FPGA."""
    N = X.shape[0]
    m1 = np.zeros((N, w1.shape[0]), np.int64)
    m2 = np.zeros((N, w2.shape[0]), np.int64)
    cnt = np.zeros((N, w2.shape[0]), np.int64)
    for t in range(X.shape[1]):
        m1 = np.clip(m1 - (m1 >> 3) + ((X[:, t].astype(np.int64) @ w1.T) << 4), -32768, 32767)
        s1 = m1 >= th[0]
        m1 = m1 - s1 * th[0]
        m2 = np.clip(m2 - (m2 >> 3) + ((s1.astype(np.int64) @ w2.T) << 4), -32768, 32767)
        s2 = m2 >= th[1]
        m2 = m2 - s2 * th[1]
        cnt += s2
    return cnt


def main(argv=None):
    ap = argparse.ArgumentParser(description="npz -> IP SNN95 lewat AXI4-Lite (125 x tulis 0x0C per window)")
    ap.add_argument("--npz", default="test_vectors.npz")
    ap.add_argument("--bit", default="snn_soc_kv260.bit", help="(PYNQ) bitstream, .hwh harus satu folder")
    ap.add_argument("--pynq-loaded", action="store_true", help="(PYNQ) jangan download ulang bitstream")
    ap.add_argument("--base", help="alamat AXI IP, mis. 0xA0000000 -> pakai /dev/mem tanpa PYNQ")
    ap.add_argument("--fclk", type=float, default=100e6, help="clock PL (Hz), untuk konversi CYCLES -> us")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--n", type=int, default=0, help="jumlah window (0 = semua)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--out", default="results_npz_axi.json")
    a = ap.parse_args(argv)          # argv list -> bisa dipanggil dari notebook

    Z = np.load(a.npz)
    X, y = Z["X_test"], Z["y_test"].astype(np.int64)
    end = len(X) if a.n <= 0 else min(len(X), a.start + a.n)
    idx = np.arange(a.start, end)
    ref = ref_starter(X[idx], Z["w1"], Z["w2"]) if "w1" in Z else None

    ip, desc = open_ip(a)
    ident = ip.read(REG_ID)
    if ident != ID_SNN1:
        sys.exit(f"ID = 0x{ident:08X}, bukan 0x{ID_SNN1:08X} ('SNN1') -> alamat salah / bitstream belum dimuat")
    ip.write(REG_CTRL, CTRL_RESET)
    print(f"IP SNN95 OK ({desc}) | window {a.start}..{end-1} ({len(idx)})")

    pred, counts, cyc, t_wr, t_tot = [], [], [], [], []
    mism = 0
    for k, w in enumerate(idx):
        c, cls, cy, tw, tt = run_window(ip, to_bytes(X[w]))
        pred.append(cls); counts.append(c); cyc.append(cy); t_wr.append(tw); t_tot.append(tt)
        bad = ref is not None and c != ref[k].tolist()
        mism += bad
        if a.verbose:
            print(f"  w{w:3d} label {CLASS_NAMES[y[w]]:>6} -> {CLASS_NAMES[cls]:>6} counts {c} | cycles {cy} "
                  f"| tulis {tw*1e6:7.1f} us | total {tt*1e6:7.1f} us" + ("  <-- beda referensi" if bad else ""))
        elif (k + 1) % 100 == 0:
            print(f"  {k+1}/{len(idx)} window ...")

    pred = np.array(pred)
    yy = y[idx]
    acc = float((pred == yy).mean())
    cm = np.zeros((4, 4), np.int64)
    for t, p in zip(yy, pred):
        cm[t, p] += 1
    print(f"\nHasil {len(idx)} window:")
    print(f"  akurasi 4 kelas : {acc*100:.2f}% ({int((pred == yy).sum())}/{len(idx)})")
    if ref is not None:
        print(f"  bit-exact vs model NumPy starter (reset langsung, >=): {len(idx)-mism}/{len(idx)} cocok")
    if "pred_golden" in Z:
        print(f"  sama dengan pred_golden di npz: {int((pred == Z['pred_golden'][idx]).sum())}/{len(idx)}")
    print("  confusion (baris = benar; kolom = " + ", ".join(CLASS_NAMES) + ")")
    for n, row in zip(CLASS_NAMES, cm.tolist()):
        print(f"    {n:>6} {row}")
    cyc_med = int(np.median(cyc))
    print("Latency:")
    print(f"  inti FPGA (CYCLES) : median {cyc_med} clock = {cyc_med / a.fclk * 1e6:.1f} us @ {a.fclk/1e6:.0f} MHz")
    print(f"  tulis 125 byte AXI : median {np.median(t_wr)*1e6:.1f} us")
    print(f"  total per window   : median {np.median(t_tot)*1e6:.1f} us (tulis + hitung + baca, dari Python)")

    json.dump(dict(source=desc, n=len(idx), accuracy=acc, confusion=cm.tolist(),
                   bit_exact_ok=(len(idx) - mism) if ref is not None else None,
                   cycles_median=cyc_med, fclk=a.fclk, write_us_median=float(np.median(t_wr) * 1e6),
                   total_us_median=float(np.median(t_tot) * 1e6), windows=idx.tolist(),
                   pred=pred.tolist(), counts=counts), open(a.out, "w"), indent=2)
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
