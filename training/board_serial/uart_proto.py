"""Protokol serial NeuroEdge (dipakai send_spikes_uart.py di laptop dan uart_server.py di Kria).

Laptop -> Kria (128 byte):
    [0xA5] [seq] [125 byte spike] [chk]
    byte spike t: bit c = spike channel c pada timestep t (sama dengan win_in.hex)
Kria -> laptop (15 byte):
    [0x5A] [seq] [status] [kelas] [cnt0] [cnt1] [cnt2] [cnt3] [cycles u16 LE] [proc_us u32 LE] [chk]
    status 0 = OK, 1 = checksum salah, 2 = error inferensi
    cycles  = clock inti FPGA (register LATENCY); 0 jika backend CPU
    proc_us = waktu proses di Kria (terima paket lengkap .. hasil siap), mikrodetik
chk = XOR semua byte setelah header (seq .. byte terakhir sebelum chk).
"""
import struct, time

T = 125
HDR_REQ, HDR_RSP = 0xA5, 0x5A
REQ_LEN = 1 + 1 + T + 1
RSP_LEN = 15
ST_OK, ST_BADCHK, ST_ERR = 0, 1, 2
CLASS_NAMES = ["air", "void", "corona", "noise"]


def xor(bs):
    r = 0
    for b in bs:
        r ^= b
    return r


def spikes_to_bytes(x):
    """x [125, 8] (0/1) -> 125 byte (bit c = channel c)."""
    return bytes(int(sum(int(x[t, c]) << c for c in range(x.shape[1]))) for t in range(x.shape[0]))


def build_req(seq, payload):
    assert len(payload) == T
    body = bytes([seq & 0xFF]) + bytes(payload)
    return bytes([HDR_REQ]) + body + bytes([xor(body)])


def parse_req(frame):
    """frame REQ_LEN byte (termasuk header) -> (seq, payload) atau (seq, None) jika checksum salah."""
    body, chk = frame[1:-1], frame[-1]
    return body[0], (bytes(body[1:]) if xor(body) == chk else None)


def build_rsp(seq, status, cls, counts, cycles, proc_us):
    body = struct.pack("<BBB4BHI", seq & 0xFF, status, cls & 3, *[min(int(c), 255) for c in counts],
                       int(cycles) & 0xFFFF, min(int(proc_us), 0xFFFFFFFF))
    return bytes([HDR_RSP]) + body + bytes([xor(body)])


def parse_rsp(frame):
    """frame RSP_LEN byte -> dict, atau None jika checksum salah."""
    body, chk = frame[1:-1], frame[-1]
    if xor(body) != chk:
        return None
    seq, st, cls, c0, c1, c2, c3, cyc, pus = struct.unpack("<BBB4BHI", body)
    return dict(seq=seq, status=st, cls=cls, counts=[c0, c1, c2, c3], cycles=cyc, proc_us=pus)


def read_frame(ser, hdr, length, timeout, junk=None):
    """Cari byte header lalu baca sisa frame. timeout None = tunggu terus. Return bytes atau None.
    junk (bytearray, opsional) menampung byte sampah yang dibuang (untuk diagnosis)."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while deadline is None or time.monotonic() < deadline:
        b = ser.read(1)
        if not b:
            continue
        if b[0] != hdr:
            if junk is not None and len(junk) < 4096:
                junk += b
            continue                                   # buang byte sampah sampai ketemu header
        rest = b""
        while len(rest) < length - 1:
            if deadline is not None and time.monotonic() >= deadline:
                return None
            chunk = ser.read(length - 1 - len(rest))
            if chunk:
                rest += chunk
        return b + rest
    return None
