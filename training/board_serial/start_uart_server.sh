#!/bin/bash
# Jalankan uart_server.py di port console (dideteksi otomatis) TANPA perlu SSH.
# Dijalankan dari sesi serial MobaXterm (COM16):
#     sudo bash start_uart_server.sh cpu
#     sudo bash start_uart_server.sh fpga --bit neuroedge.bit
#     sudo bash start_uart_server.sh fpga --base 0xA0000000 --fclk 100e6
# Variabel opsional: IDLE=300 (detik tanpa paket sebelum berhenti), BAUD=115200,
#                    TTY=ttyPS1 (default: dideteksi otomatis dari terminal tempat skrip dijalankan)
#
# Yang terjadi:
#   1. server dijalankan sebagai layanan systemd "snnuart" (tidak ikut mati saat console putus)
#   2. 5 detik kemudian login console (serial-getty) dihentikan -> sesi MobaXterm ini terputus
#   3. server memakai port console itu untuk data spike dari laptop
#   4. setelah IDLE detik tanpa paket, server berhenti dan login console dinyalakan lagi
# Kalau ada yang macet: cabut-pasang power board -> login console kembali normal (stop tidak permanen).
set -e
if [ "$(id -u)" != "0" ]; then echo "jalankan dengan sudo"; exit 1; fi
BACKEND=${1:-cpu}; shift || true
DIR="$(cd "$(dirname "$0")" && pwd)"
IDLE=${IDLE:-180}
BAUD=${BAUD:-115200}

# port console = terminal tempat skrip ini dijalankan (kecuali TTY=... diberikan)
if [ -z "$TTY" ]; then
    T=$(tty 2>/dev/null || true)
    case "$T" in
        /dev/ttyPS*|/dev/ttyS*|/dev/ttyUL*|/dev/ttyAMA*) TTY=${T#/dev/} ;;
        *) echo "Tidak bisa mendeteksi port console (tty = '$T')."
           echo "Jalankan dari sesi serial MobaXterm, atau beri nama port: sudo TTY=ttyPS1 bash $0 $BACKEND"
           exit 1 ;;
    esac
fi
if [ ! -c "/dev/$TTY" ]; then
    echo "/dev/$TTY bukan device serial (tidak ada / file biasa). Port yang ada:"
    ls -l /dev/ttyPS* /dev/ttyS* /dev/ttyUL* 2>/dev/null
    exit 1
fi
if ! systemctl is-active --quiet "serial-getty@$TTY.service"; then
    echo "serial-getty@$TTY.service tidak aktif -> login console mungkin memakai layanan lain:"
    systemctl list-units --all | grep -i getty
    echo "Kirim output ini ke Claude; skrip dihentikan supaya tidak mematikan layanan yang salah."
    exit 1
fi
echo "port console terdeteksi: /dev/$TTY"

python3 -c "import serial" 2>/dev/null || { echo "pyserial belum ada: sudo apt install python3-serial"; exit 1; }
systemctl stop snnuart 2>/dev/null || true
systemctl reset-failed snnuart 2>/dev/null || true

systemd-run --unit=snnuart --property=WorkingDirectory="$DIR" /bin/bash -c "
  sleep 5
  systemctl stop serial-getty@$TTY.service
  dmesg -n 1
  python3 -u uart_server.py --port /dev/$TTY --baud $BAUD --backend $BACKEND --idle-exit $IDLE $*
  systemctl start serial-getty@$TTY.service
"

echo
echo "=== server 'snnuart' dijadwalkan, console ini akan TERPUTUS dalam 5 detik ==="
echo "1. TUTUP sesi serial COM16 di MobaXterm sekarang"
echo "2. di laptop:  python send_spikes_uart.py --com=COM16 --baud $BAUD"
echo "3. server berhenti sendiri setelah $IDLE s tanpa paket, lalu login console kembali"
echo "4. setelah login lagi, lihat log server:  journalctl -u snnuart --no-pager | tail -30"
