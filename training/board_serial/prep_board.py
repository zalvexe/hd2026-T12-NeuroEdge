"""Jalankan di PC (dari folder board/): salin data yang dibutuhkan ke folder ini dan buat y_test.txt.
    python prep_board.py
Lalu salin seluruh folder board/ (+ neuroedge.bit/.hwh dari Vivado) ke KV260."""
import os, shutil
import numpy as np

SRC = ".."
FILES = ["test_vectors.npz", "params.json", "win_in.hex", "win_exp.txt", "w1_int8.mem", "w2_int8.mem"]
for f in FILES:
    shutil.copy(os.path.join(SRC, f), f)
    print("salin", f)

shutil.copytree(os.path.join(SRC, "dataset_1"), "dataset_1", dirs_exist_ok=True)
print("salin dataset_1/ (CSV mentah, untuk run_snn_cpu.py)")

Z = np.load("test_vectors.npz")
np.savetxt("y_test.txt", Z["y_test"].astype(np.int64), fmt="%d")
print("tulis y_test.txt")

# cek: bobot .mem == bobot di npz (keduanya harus dari export model yang sama)
for mem, key in [("w1_int8.mem", "w1"), ("w2_int8.mem", "w2")]:
    v = np.array([int(l, 16) for l in open(mem).read().split()], np.int64)
    v = np.where(v > 127, v - 256, v)
    ok = np.array_equal(v, Z[key].astype(np.int64).ravel())
    print(f"cek {mem} == npz['{key}']: {'OK' if ok else 'BEDA -> export ulang!'}")
