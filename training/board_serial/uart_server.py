"""[KRIA / Ubuntu] Terima window spike lewat serial, jalankan SNN, kirim hasil balik.
Pasangannya: send_spikes_uart.py di laptop. Format paket: uart_proto.py.

Backend:
    fpga : akselerator di PL lewat AXI4-Lite (butuh bitstream; sama dengan bench_fpga.py)
    cpu  : SNN integer di CPU ARM (NumPy) -> untuk menguji jalur serial sebelum bitstream siap

Contoh (dari folder board/ di Kria):
    sudo python3 uart_server.py --port /dev/ttyPS1 --backend cpu
    sudo python3 uart_server.py --port /dev/ttyPS1 --backend fpga --bit neuroedge.bit
    sudo python3 uart_server.py --port /dev/ttyPS1 --backend fpga --base 0xA0000000 --fclk 100e6
Port serial yang dipakai TIDAK boleh sedang dipakai login console (lihat panduan: stop serial-getty).
Hentikan dengan Ctrl+C.
"""
import argparse, sys, time
import numpy as np
import uart_proto as U


def make_cpu_backend(data_dir):
    """Hanya butuh bobot + parameter (sama dengan isi bitstream): params.json, w1_int8.mem, w2_int8.mem.
    Data spike datang dari laptop, jadi test_vectors.npz tidak dipakai."""
    import json, os
    from bench_cpu import make_infer
    from run_snn_cpu import load_mem
    P = json.load(open(os.path.join(data_dir, "params.json")))
    W1 = load_mem(os.path.join(data_dir, "w1_int8.mem"), P["layers"][0]["shape"])
    W2 = load_mem(os.path.join(data_dir, "w2_int8.mem"), P["layers"][1]["shape"])
    infer = make_infer(W1, W2, P)

    def run(payload):
        x = np.array([[(b >> c) & 1 for c in range(8)] for b in payload], np.int64)
        cnt = infer(x)
        return [int(v) for v in cnt], int(np.argmax(cnt)), 0
    return run, "CPU (NumPy)"


def make_fpga_backend(a):
    import bench_fpga as B
    dev, fclk, desc = B.open_dev(a)
    ident = dev.read(B.REG_ID)
    if ident != B.ID_SNN1:
        sys.exit(f"ID register 0x{ident:08X} bukan 'SNN1' -> alamat base salah atau bitstream belum dimuat")
    dev.write(B.REG_CTRL, B.CTRL_RESET)
    dev.write(B.REG_CTRL, 0)

    def run(payload):
        p = bytes(payload) + bytes(128 - len(payload))          # 125 -> 128 byte, 32 word little-endian
        words = [p[4 * k] | p[4 * k + 1] << 8 | p[4 * k + 2] << 16 | p[4 * k + 3] << 24 for k in range(32)]
        counts, cls, cyc, _ = B.run_window(dev, words)
        return counts, cls, cyc
    return run, f"FPGA ({desc}, f_clk {fclk/1e6:.0f} MHz)"


def serve(ser, run, quiet=False, max_frames=None, idle_exit=0):
    """Loop utama: tunggu paket -> inferensi -> balas.
    idle_exit > 0: berhenti jika tidak ada paket selama idle_exit detik (dihitung juga sejak start).
    max_frames dipakai untuk uji."""
    n = bad = 0
    t_last = t_frame = time.monotonic()
    while max_frames is None or n + bad < max_frames:
        frame = U.read_frame(ser, U.HDR_REQ, U.REQ_LEN, timeout=1.0)
        if frame is None:
            if idle_exit > 0 and time.monotonic() - t_frame > idle_exit:
                print(f"tidak ada paket selama {idle_exit:.0f} s -> server berhenti")
                break
            continue
        t_frame = time.monotonic()
        t0 = time.perf_counter_ns()
        seq, payload = U.parse_req(frame)
        if payload is None:
            bad += 1
            ser.write(U.build_rsp(seq, U.ST_BADCHK, 0, [0, 0, 0, 0], 0, 0))
            if not quiet:
                print(f"  paket seq {seq}: checksum salah")
            continue
        try:
            counts, cls, cyc = run(payload)
            st = U.ST_OK
        except Exception as e:                     # jangan matikan server karena satu window
            print(f"  error inferensi: {e}")
            counts, cls, cyc, st = [0, 0, 0, 0], 0, 0, U.ST_ERR
        proc_us = (time.perf_counter_ns() - t0) // 1000
        ser.write(U.build_rsp(seq, st, cls, counts, cyc, proc_us))
        ser.flush()
        n += 1
        if not quiet and (n % 50 == 0 or time.monotonic() - t_last > 5):
            print(f"  {n} window diproses (checksum salah {bad}) | terakhir: kelas {U.CLASS_NAMES[cls]} "
                  f"counts {counts} | {proc_us} us")
            t_last = time.monotonic()
    return n, bad


def main():
    ap = argparse.ArgumentParser(description="Server UART NeuroEdge di Kria")
    ap.add_argument("--port", required=True, help="serial di Kria, mis. /dev/ttyPS1 (cek dengan perintah tty)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--backend", choices=["fpga", "cpu"], default="fpga")
    ap.add_argument("--bit", default="neuroedge.bit", help="(fpga) bitstream PYNQ, .hwh satu folder")
    ap.add_argument("--base", help="(fpga) alamat AXI IP, mis. 0xA0000000 -> /dev/mem tanpa PYNQ")
    ap.add_argument("--fclk", type=float, help="(fpga) frekuensi PL clock (Hz)")
    ap.add_argument("--data", default=".")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--idle-exit", type=float, default=0,
                    help="berhenti otomatis jika tidak ada paket selama N detik (0 = jalan terus)")
    a = ap.parse_args()

    import serial
    run, desc = make_cpu_backend(a.data) if a.backend == "cpu" else make_fpga_backend(a)
    ser = serial.Serial(a.port, a.baud, timeout=0.05)
    ser.reset_input_buffer()
    print(f"NeuroEdge UART server | {a.port} @ {a.baud} | backend {desc}")
    print("menunggu paket dari laptop ... (Ctrl+C untuk berhenti)", flush=True)
    try:
        n, bad = serve(ser, run, a.quiet, idle_exit=a.idle_exit)
        print(f"selesai: {n} window diproses, {bad} checksum salah")
    except KeyboardInterrupt:
        print("\nberhenti.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
