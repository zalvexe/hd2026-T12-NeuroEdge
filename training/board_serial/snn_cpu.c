// snn_cpu.c : baseline CPU (C, 1 thread) untuk SNN NeuroEdge 8-128-4.
// Aritmetika integer identik dengan lif_neuron.v / gen_win_vec.py:
//   reset delayed (dari membran timestep lalu), strict >, saturasi int16.
// Akumulasi event-driven: hanya input/hidden yang spike yang dijumlahkan (adil untuk CPU).
//
// Build di board : gcc -O2 -o snn_cpu snn_cpu.c
// Jalankan       : ./snn_cpu [detik_loop]      (default 20; 0 = tanpa loop throughput)
// Butuh di folder yang sama: win_in.hex, win_exp.txt, w1_int8.mem, w2_int8.mem, y_test.txt
// Keluaran       : pred_cpu_c.txt, results_cpu_c.json
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <time.h>
#endif

// harus sama dengan params.vh / params.json
#define T_STEPS   125
#define NIN       8
#define HID       128
#define NOUT      4
#define MAXWIN    600
#define THR1      944
#define THR2      860
#define DECAY     3
#define CUR_MUL   16        // << (GAIN_SHIFT 1 + FRAC_BITS 3)

static int8_t  W1T[NIN][HID];   // transpose: kolom per channel input
static int8_t  W2T[HID][NOUT];  // transpose: kolom per neuron hidden
static uint8_t WIN[MAXWIN][T_STEPS];
static int     EXP[MAXWIN][5];
static int     Y[MAXWIN];

static double now_s(void) {
#ifdef _WIN32
    LARGE_INTEGER f, c; QueryPerformanceFrequency(&f); QueryPerformanceCounter(&c);
    return (double)c.QuadPart / (double)f.QuadPart;
#else
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
#endif
}

static FILE *open_or_die(const char *p) {
    FILE *f = fopen(p, "r");
    if (!f) { fprintf(stderr, "ERROR: tidak bisa membuka %s\n", p); exit(1); }
    return f;
}

static void read_mem(const char *p, int8_t *dst, int n) {   // row-major (out, in)
    FILE *f = open_or_die(p); unsigned v;
    for (int i = 0; i < n; i++) {
        if (fscanf(f, "%x", &v) != 1) { fprintf(stderr, "ERROR: %s kurang data\n", p); exit(1); }
        dst[i] = (int8_t)(v & 0xFF);
    }
    fclose(f);
}

static inline int lif(int32_t *m, int32_t acc, int32_t thr) {
    int32_t v = *m;
    int32_t rst = (v > thr) ? thr : 0;           // reset delayed
    v = v - (v >> DECAY) + acc * CUR_MUL - rst;  // >> aritmetika (gcc)
    if (v > 32767) v = 32767; else if (v < -32768) v = -32768;
    *m = v;
    return v > thr;
}

static void infer(const uint8_t *win, int cnt[NOUT]) {
    int32_t m1[HID] = {0}, m2[NOUT] = {0};
    int32_t acc1[HID];
    int     hid_idx[HID];
    for (int o = 0; o < NOUT; o++) cnt[o] = 0;
    for (int t = 0; t < T_STEPS; t++) {
        uint8_t s = win[t];
        memset(acc1, 0, sizeof acc1);
        for (int c = 0; c < NIN; c++)
            if (s >> c & 1)
                for (int n = 0; n < HID; n++) acc1[n] += W1T[c][n];
        int nh = 0;
        for (int n = 0; n < HID; n++)
            if (lif(&m1[n], acc1[n], THR1)) hid_idx[nh++] = n;
        int32_t acc2[NOUT] = {0};
        for (int k = 0; k < nh; k++)
            for (int o = 0; o < NOUT; o++) acc2[o] += W2T[hid_idx[k]][o];
        for (int o = 0; o < NOUT; o++) cnt[o] += lif(&m2[o], acc2[o], THR2);
    }
}

static int argmax4(const int c[NOUT]) {         // seri -> indeks terkecil (sama dengan RTL)
    int b = 0;
    for (int o = 1; o < NOUT; o++) if (c[o] > c[b]) b = o;
    return b;
}

static int cmp_d(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

int main(int argc, char **argv) {
    double loop_s = argc > 1 ? atof(argv[1]) : 20.0;
    static int8_t w1[HID * NIN], w2[NOUT * HID];
    read_mem("w1_int8.mem", w1, HID * NIN);
    read_mem("w2_int8.mem", w2, NOUT * HID);
    for (int n = 0; n < HID; n++) for (int c = 0; c < NIN; c++) W1T[c][n] = w1[n * NIN + c];
    for (int o = 0; o < NOUT; o++) for (int n = 0; n < HID; n++) W2T[n][o] = w2[o * HID + n];

    FILE *fe = open_or_die("win_exp.txt"), *fy = open_or_die("y_test.txt"), *fi = open_or_die("win_in.hex");
    int N = 0;
    while (N < MAXWIN && fscanf(fe, "%d %d %d %d %d", &EXP[N][0], &EXP[N][1], &EXP[N][2], &EXP[N][3], &EXP[N][4]) == 5) N++;
    for (int w = 0; w < N; w++) {
        if (fscanf(fy, "%d", &Y[w]) != 1) { fprintf(stderr, "ERROR: y_test.txt kurang data\n"); return 1; }
        for (int t = 0; t < T_STEPS; t++) {
            unsigned v;
            if (fscanf(fi, "%x", &v) != 1) { fprintf(stderr, "ERROR: win_in.hex kurang data\n"); return 1; }
            WIN[w][t] = (uint8_t)v;
        }
    }
    fclose(fe); fclose(fy); fclose(fi);

    int cnt[NOUT], pred[MAXWIN];
    static double lat_us[MAXWIN], sorted_us[MAXWIN];
    for (int w = 0; w < 10 && w < N; w++) infer(WIN[w], cnt);   // warm-up

    int mism = 0, correct = 0;
    double sum_us = 0;
    for (int w = 0; w < N; w++) {
        double t0 = now_s();
        infer(WIN[w], cnt);
        lat_us[w] = (now_s() - t0) * 1e6;
        sum_us += lat_us[w];
        pred[w] = argmax4(cnt);
        if (cnt[0] != EXP[w][0] || cnt[1] != EXP[w][1] || cnt[2] != EXP[w][2] ||
            cnt[3] != EXP[w][3] || pred[w] != EXP[w][4]) mism++;
        if (pred[w] == Y[w]) correct++;
    }
    memcpy(sorted_us, lat_us, N * sizeof(double));
    qsort(sorted_us, N, sizeof(double), cmp_d);
    double med = sorted_us[N / 2], p95 = sorted_us[(int)(0.95 * (N - 1))];
    double mean = sum_us / N;

    double thr = 1e6 / mean;
    long n_inf = 0;
    if (loop_s > 0) {
        double t0 = now_s(), el = 0;
        while (el < loop_s) {
            infer(WIN[n_inf % N], cnt);
            n_inf++;
            if ((n_inf & 63) == 0) el = now_s() - t0;
        }
        el = now_s() - t0;
        thr = n_inf / el;
    }

    printf("[cpu_c] %d window, batch = 1\n", N);
    printf("  bit-exact vs win_exp.txt: %d/%d window cocok\n", N - mism, N);
    printf("  akurasi 4 kelas : %.2f%% (%d/%d)\n", 100.0 * correct / N, correct, N);
    printf("  latency per window (CPU C): median %.2f us | mean %.2f | p95 %.2f | min %.2f\n",
           med, mean, p95, sorted_us[0]);
    printf("  throughput      : %.1f window/s\n", thr);

    FILE *fp = fopen("pred_cpu_c.txt", "w");
    for (int w = 0; w < N; w++) fprintf(fp, "%d\n", pred[w]);
    fclose(fp);
    FILE *fj = fopen("results_cpu_c.json", "w");
    fprintf(fj, "{\n  \"platform\": \"cpu_c\",\n  \"n\": %d,\n  \"bit_exact_ok\": %d,\n"
                "  \"latency_window\": {\"n\": %d, \"median_us\": %.4f, \"mean_us\": %.4f, \"p95_us\": %.4f, "
                "\"min_us\": %.4f, \"max_us\": %.4f},\n"
                "  \"throughput_inf_per_s\": %.4f,\n  \"power\": null\n}\n",
            N, N - mism, N, med, mean, p95, sorted_us[0], sorted_us[N - 1], thr);
    fclose(fj);
    printf("-> pred_cpu_c.txt, results_cpu_c.json\n");
    return mism ? 2 : 0;
}
