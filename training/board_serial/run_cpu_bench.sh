#!/bin/bash
# Jalankan semua benchmark CPU di KV260 (akurasi + latency + daya INA) lalu buat tabel.
# Pakai dari folder board/:   sudo bash run_cpu_bench.sh
# (sudo diperlukan untuk set governor CPU; tanpa sudo tetap jalan tapi governor tidak diubah)
set -e
cd "$(dirname "$0")"
REPEATS=${REPEATS:-3}      # ulangan idle/run, bisa diubah: REPEATS=5 sudo -E bash run_cpu_bench.sh
CORE=${CORE:-1}            # core yang dipakai inferensi

echo "=== 0. cek sensor daya ==="
python3 power_mon.py --list

echo "=== 1. governor CPU -> performance (supaya frekuensi stabil) ==="
for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo performance > "$g" 2>/dev/null || echo "  (tidak bisa set $g, lanjut)"
done
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || true

echo "=== 2. baseline CPU Python/NumPy ==="
taskset -c "$CORE" python3 bench_cpu.py --repeats "$REPEATS"

echo "=== 3. baseline CPU C ==="
gcc -O2 -o snn_cpu snn_cpu.c
taskset -c "$CORE" ./snn_cpu 5
python3 power_mon.py --idle 15 --repeats "$REPEATS" --merge results_cpu_c.json -- taskset -c "$CORE" ./snn_cpu 20

echo "=== 4. tabel perbandingan ==="
python3 compare.py
