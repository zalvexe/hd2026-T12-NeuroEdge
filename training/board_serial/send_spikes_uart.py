"""[LAPTOP] Kirim window spike lewat serial UART ke Kria, terima hasil klasifikasi SNN.
Pasangannya: uart_server.py yang jalan di Ubuntu Kria.

Contoh (PowerShell, dari folder board/):
    python send_spikes_uart.py --list                       # tampilkan COM port yang ada
    python send_spikes_uart.py --com=COM5                   # 600 window test (npz), 115200 baud
    python send_spikes_uart.py --com=COM5 --n 20 --verbose  # 20 window, cetak tiap window
    python send_spikes_uart.py --com=COM5 --source csv      # encode dari CSV mentah di laptop
    python send_spikes_uart.py --com=COM5 --baud 921600     # baud harus sama dengan server

Sumber spike (--source):
    npz : X_test di test_vectors.npz (default)
    hex : win_in.hex (1 byte per timestep, sama persis dengan yang dipakai testbench RTL)
    csv : dataset_1/*.csv -> normalisasi + encoding level-crossing di laptop (run_snn_cpu.encode)
Urutan window = urutan data test (baris 850..999 tiap kelas), sehingga bisa dicek bit-exact terhadap win_exp.txt.
"""
import argparse, json, os, sys, time
import numpy as np
import uart_proto as U


def load_windows(src, d):
    """-> (payloads list[bytes], labels np.array)"""
    if src == "npz":
        Z = np.load(os.path.join(d, "test_vectors.npz"))
        X, y = Z["X_test"].astype(np.uint8), Z["y_test"].astype(np.int64)
        return [U.spikes_to_bytes(x) for x in X], y
    if src == "hex":
        v = [int(t, 16) for t in open(os.path.join(d, "win_in.hex")).read().split()]
        y = np.loadtxt(os.path.join(d, "y_test.txt"), dtype=np.int64)
        return [bytes(v[i * U.T:(i + 1) * U.T]) for i in range(len(v) // U.T)], y
    import run_snn_cpu as R                            # csv
    P = json.load(open(os.path.join(d, "params.json")))
    th = np.asarray(P["thresholds_float"], np.float64)
    X, y, _ = R.load_csv(os.path.join(d, "dataset_1"), "test")
    return [U.spikes_to_bytes(R.encode(x, th)) for x in X], y


def stats(v):
    v = np.asarray(v, np.float64)
    return f"median {np.median(v):9.1f} | mean {v.mean():9.1f} | p95 {np.percentile(v, 95):9.1f} | min {v.min():9.1f}"


def main():
    ap = argparse.ArgumentParser(description="Kirim spike lewat UART ke Kria")
    ap.add_argument("--com", help="port serial laptop, mis. COM5 (Windows) atau /dev/ttyUSB1 (Linux)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--source", choices=["npz", "hex", "csv"], default="npz")
    ap.add_argument("--data", default=".", help="folder data (test_vectors.npz, win_exp.txt, ...)")
    ap.add_argument("--start", type=int, default=0, help="indeks window pertama")
    ap.add_argument("--n", type=int, default=0, help="jumlah window (0 = semua)")
    ap.add_argument("--timeout", type=float, default=3.0, help="detik menunggu balasan per window")
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--list", action="store_true", help="tampilkan COM port lalu keluar")
    ap.add_argument("--out", default="results_uart.json")
    a = ap.parse_args()

    import serial, serial.tools.list_ports
    if a.list or not a.com:
        ports = list(serial.tools.list_ports.comports())
        print("COM port terdeteksi:" if ports else "Tidak ada COM port terdeteksi (board tersambung? driver FTDI?)")
        for p in ports:
            print(f"  {p.device:8s} {p.description}  [{p.hwid}]")
        if not a.com:
            print("\nPakai: python send_spikes_uart.py --com=COMx   (pilih port yang tersambung ke uart_server.py)")
        return

    payloads, y = load_windows(a.source, a.data)
    end = len(payloads) if a.n <= 0 else min(len(payloads), a.start + a.n)
    idx = list(range(a.start, end))
    exp_path = os.path.join(a.data, "win_exp.txt")
    exp = np.loadtxt(exp_path, dtype=np.int64, ndmin=2) if os.path.exists(exp_path) else None

    ser = serial.Serial(a.com, a.baud, timeout=0.05, write_timeout=a.timeout)
    time.sleep(0.2)
    ser.reset_input_buffer()
    t_uart_us = (U.REQ_LEN + U.RSP_LEN) * 10 / a.baud * 1e6
    print(f"{a.com} @ {a.baud} baud | source {a.source} | window {a.start}..{end-1} ({len(idx)})")
    print(f"teori waktu kabel UART per window: {t_uart_us:.0f} us ({U.REQ_LEN}+{U.RSP_LEN} byte x 10 bit)")

    pred, counts, cyc, proc, rtt, ok_idx = [], [], [], [], [], []
    fails, mism, seq = 0, 0, 0
    t_start = time.perf_counter()
    try:
        for k, w in enumerate(idx):
            req = U.build_req(seq, payloads[w])
            r = None
            for attempt in range(a.retries + 1):
                junk = bytearray()
                t0 = time.perf_counter()
                ser.write(req)
                ser.flush()
                frame = U.read_frame(ser, U.HDR_RSP, U.RSP_LEN, a.timeout, junk)
                t1 = time.perf_counter()
                r = U.parse_rsp(frame) if frame else None
                if r and r["seq"] == seq and r["status"] == U.ST_OK:
                    break
                why = "timeout" if frame is None else ("checksum" if r is None else
                       f"seq {r['seq']}!={seq}" if r["seq"] != seq else f"status {r['status']}")
                print(f"  window {w}: percobaan {attempt+1} gagal ({why})")
                if junk:
                    print(f"    diterima {len(junk)} byte bukan balasan server: {bytes(junk[:160])!r}")
                    txt = junk.decode("ascii", "ignore").lower()
                    if "login" in txt or "password" in txt or "$" in txt:
                        print("    -> ini teks console Linux: login console (getty) masih aktif / server tidak jalan")
                elif frame is None:
                    print("    tidak ada byte apa pun dari Kria (server mati, port/baud salah, atau kabel)")
                r = None
                ser.reset_input_buffer()
            seq = (seq + 1) & 0xFF
            if r is None:
                fails += 1
                if fails >= 5 and not ok_idx:
                    sys.exit("5 window gagal tanpa satu pun balasan -> cek COM port, baud, dan uart_server.py di Kria")
                continue
            ok_idx.append(w); pred.append(r["cls"]); counts.append(r["counts"])
            cyc.append(r["cycles"]); proc.append(r["proc_us"]); rtt.append((t1 - t0) * 1e6)
            if exp is not None and (r["counts"] != exp[w, :4].tolist() or r["cls"] != exp[w, 4]):
                mism += 1
            if a.verbose:
                tag = "" if exp is None else ("" if r["counts"] == exp[w, :4].tolist() else "  <-- beda golden")
                print(f"  w{w:3d} label {U.CLASS_NAMES[y[w]]:>6} -> {U.CLASS_NAMES[r['cls']]:>6} counts {r['counts']} "
                      f"| rtt {(t1-t0)*1e3:6.2f} ms | kria {r['proc_us']} us | cyc {r['cycles']}{tag}")
            elif (k + 1) % 50 == 0:
                print(f"  {k+1}/{len(idx)} window ...")
    except KeyboardInterrupt:
        print("\ndihentikan (Ctrl+C), hasil sementara:")
    finally:
        ser.close()
    total_s = time.perf_counter() - t_start

    if not ok_idx:
        sys.exit("tidak ada hasil")
    pred, yy = np.array(pred), y[ok_idx]
    acc = float((pred == yy).mean())
    cm = np.zeros((4, 4), np.int64)
    for t, p in zip(yy, pred):
        cm[t, p] += 1
    pt, pp = yy != 3, pred != 3
    prec = (pt & pp).sum() / max(pp.sum(), 1)
    rec = (pt & pp).sum() / max(pt.sum(), 1)
    print(f"\nHasil {len(ok_idx)} window (gagal {fails}):")
    print(f"  akurasi 4 kelas : {acc*100:.2f}% ({int((pred == yy).sum())}/{len(ok_idx)})")
    print(f"  PD vs noise     : precision {prec:.4f} | recall {rec:.4f}")
    if exp is not None:
        print(f"  bit-exact vs win_exp.txt: {len(ok_idx)-mism}/{len(ok_idx)} cocok")
    print("  confusion (baris = benar; kolom = " + ", ".join(U.CLASS_NAMES) + ")")
    for n, row in zip(U.CLASS_NAMES, cm.tolist()):
        print(f"    {n:>6} {row}")
    print("Latency (us):")
    print(f"  round-trip laptop (kirim..balasan) : {stats(rtt)}")
    print(f"  proses di Kria (terima..hasil)     : {stats(proc)}")
    if max(cyc) > 0:
        print(f"  inti FPGA (clock, register)       : median {int(np.median(cyc))} clock")
    print(f"  throughput total: {len(ok_idx)/total_s:.1f} window/s (dibatasi kecepatan UART)")

    json.dump(dict(com=a.com, baud=a.baud, source=a.source, n=len(ok_idx), fails=fails,
                   accuracy=acc, pd_precision=float(prec), pd_recall=float(rec),
                   bit_exact_ok=(len(ok_idx) - mism) if exp is not None else None, confusion=cm.tolist(),
                   rtt_us_median=float(np.median(rtt)), kria_proc_us_median=float(np.median(proc)),
                   fpga_cycles_median=int(np.median(cyc)), windows=ok_idx, pred=pred.tolist(),
                   counts=counts), open(a.out, "w"), indent=2)
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
