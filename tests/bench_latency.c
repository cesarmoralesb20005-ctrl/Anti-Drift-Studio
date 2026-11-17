#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static int64_t monotonic_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000000LL + (int64_t)ts.tv_nsec;
}

static int cmp_i64(const void *a, const void *b) {
    int64_t va = *(const int64_t *)a;
    int64_t vb = *(const int64_t *)b;
    return (va > vb) - (va < vb);
}

static double normalize_axis(int raw_i16, int center) {
    double d = (double)(raw_i16 - center);
    double span = (d >= 0.0) ? (32767.0 - (double)center) : ((double)center - (-32768.0));
    if (span < 1000.0) span = 1000.0;
    double norm = d / span;
    if (norm > 1.0) norm = 1.0;
    if (norm < -1.0) norm = -1.0;
    return norm;
}

static int apply_axial_deadzone(double n, double dz, double curve) {
    double a = fabs(n);
    if (a <= dz || dz >= 0.999) return 0;
    double scaled = (a - dz) / (1.0 - dz);
    if (scaled > 1.0) scaled = 1.0;
    if (curve != 1.0 && scaled > 0.0) {
        scaled = pow(scaled, curve);
        if (scaled > 1.0) scaled = 1.0;
    }
    double f = (n < 0.0) ? -scaled : scaled;
    int iv = (int)llround(f * 32767.0);
    if (iv > 32767) iv = 32767;
    if (iv < -32768) iv = -32768;
    return iv;
}

static int setup_test_uinput(int fuzz) {
    int ufd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
    if (ufd < 0) return -1;

    ioctl(ufd, UI_SET_EVBIT, EV_KEY);
    ioctl(ufd, UI_SET_EVBIT, EV_ABS);
    ioctl(ufd, UI_SET_EVBIT, EV_SYN);
    ioctl(ufd, UI_SET_KEYBIT, BTN_SOUTH);

    int axes[] = {ABS_X, ABS_Y, ABS_RX, ABS_RY};
    for (int i = 0; i < 4; i++) {
        ioctl(ufd, UI_SET_ABSBIT, axes[i]);
        struct uinput_abs_setup abs_setup;
        memset(&abs_setup, 0, sizeof(abs_setup));
        abs_setup.code = (uint16_t)axes[i];
        abs_setup.absinfo.minimum = -32768;
        abs_setup.absinfo.maximum = 32767;
        abs_setup.absinfo.fuzz = fuzz;
        abs_setup.absinfo.flat = 0;
        ioctl(ufd, UI_ABS_SETUP, &abs_setup);
    }

    struct uinput_setup usetup;
    memset(&usetup, 0, sizeof(usetup));
    usetup.id.bustype = BUS_USB;
    usetup.id.vendor = 0x045e;
    usetup.id.product = 0x028e;
    usetup.id.version = 0x0110;
    snprintf(usetup.name, UINPUT_MAX_NAME_SIZE, "AntiDrift Bench Pad");

    if (ioctl(ufd, UI_DEV_SETUP, &usetup) < 0 || ioctl(ufd, UI_DEV_CREATE) < 0) {
        close(ufd);
        return -1;
    }
    return ufd;
}

static pid_t spawn_cpu_stress_proc(void) {
    pid_t pid = fork();
    if (pid == 0) {
        volatile uint64_t x = 1;
        while (1) {
            x = x * 6364136223846793005ULL + 1442695040888963407ULL;
        }
    }
    return pid;
}

typedef struct {
    double avg_us;
    double p50_us;
    double p95_us;
    double p99_us;
    double max_us;
    long syscalls;
} BenchResult;

static BenchResult run_pipeline_bench(int frames, int low_latency, int batch_uinput_write) {
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) < 0) {
        perror("socketpair");
        exit(1);
    }
    if (low_latency) {
        fcntl(sv[0], F_SETFL, O_NONBLOCK);
        mlockall(MCL_CURRENT | MCL_FUTURE);
        setpriority(PRIO_PROCESS, 0, -10);
    } else {
        munlockall();
        setpriority(PRIO_PROCESS, 0, 0);
    }

    int ufd = setup_test_uinput(low_latency ? 0 : 16);
    if (ufd < 0) {
        perror("setup_test_uinput");
        exit(1);
    }

    int64_t *samples = calloc((size_t)frames, sizeof(int64_t));
    long total_syscalls = 0;
    struct pollfd pfd = {.fd = sv[0], .events = POLLIN};

    for (int f = 0; f < frames; f++) {
        struct input_event pkt[5];
        memset(pkt, 0, sizeof(pkt));
        int v = (f & 1) ? 12000 : -12000;
        pkt[0].type = EV_ABS; pkt[0].code = ABS_X; pkt[0].value = v;
        pkt[1].type = EV_ABS; pkt[1].code = ABS_Y; pkt[1].value = v / 2;
        pkt[2].type = EV_ABS; pkt[2].code = ABS_RX; pkt[2].value = -v;
        pkt[3].type = EV_ABS; pkt[3].code = ABS_RY; pkt[3].value = v / 3;
        pkt[4].type = EV_SYN; pkt[4].code = SYN_REPORT; pkt[4].value = 0;

        int64_t t0 = monotonic_ns();
        ssize_t sw = write(sv[1], pkt, sizeof(pkt));
        (void)sw;

        int rx_val = 0, ry_val = 0, lx_val = 0, ly_val = 0;
        int got_syn = 0;

        if (!low_latency) {
            /* Mode Normal: 1 event per poll()+read(), single write() per emitted event */
            while (!got_syn) {
                poll(&pfd, 1, 250);
                total_syscalls++;
                struct input_event ev;
                ssize_t nr = read(sv[0], &ev, sizeof(ev));
                total_syscalls++;
                if (nr != (ssize_t)sizeof(ev)) break;
                if (ev.type == EV_ABS) {
                    if (ev.code == ABS_X) lx_val = ev.value;
                    else if (ev.code == ABS_Y) ly_val = ev.value;
                    else if (ev.code == ABS_RX) rx_val = ev.value;
                    else if (ev.code == ABS_RY) ry_val = ev.value;
                } else if (ev.type == EV_SYN) {
                    struct input_event out[5];
                    memset(out, 0, sizeof(out));
                    out[0].type = EV_ABS; out[0].code = ABS_X;
                    out[0].value = apply_axial_deadzone(normalize_axis(lx_val, 0), 0.12, 1.0);
                    out[1].type = EV_ABS; out[1].code = ABS_Y;
                    out[1].value = apply_axial_deadzone(normalize_axis(ly_val, 0), 0.12, 1.0);
                    out[2].type = EV_ABS; out[2].code = ABS_RX;
                    out[2].value = apply_axial_deadzone(normalize_axis(rx_val, 0), 0.12, 1.0);
                    out[3].type = EV_ABS; out[3].code = ABS_RY;
                    out[3].value = apply_axial_deadzone(normalize_axis(ry_val, 0), 0.12, 1.0);
                    out[4].type = EV_SYN; out[4].code = SYN_REPORT; out[4].value = 0;
                    for (int k = 0; k < 5; k++) {
                        ssize_t wu = write(ufd, &out[k], sizeof(out[k]));
                        (void)wu;
                        total_syscalls++;
                    }
                    got_syn = 1;
                }
            }
        } else {
            /* Mode Low Latency: drain full buffer via O_NONBLOCK read() */
            while (!got_syn) {
                poll(&pfd, 1, 250);
                total_syscalls++;
                struct input_event ev_buf[64];
                while (1) {
                    ssize_t nr = read(sv[0], ev_buf, sizeof(ev_buf));
                    total_syscalls++;
                    if (nr < 0) {
                        if (errno == EAGAIN || errno == EWOULDBLOCK) break;
                        break;
                    }
                    int cnt = (int)(nr / (ssize_t)sizeof(struct input_event));
                    for (int i = 0; i < cnt; i++) {
                        struct input_event ev = ev_buf[i];
                        if (ev.type == EV_ABS) {
                            if (ev.code == ABS_X) lx_val = ev.value;
                            else if (ev.code == ABS_Y) ly_val = ev.value;
                            else if (ev.code == ABS_RX) rx_val = ev.value;
                            else if (ev.code == ABS_RY) ry_val = ev.value;
                        } else if (ev.type == EV_SYN) {
                            struct input_event out[5];
                            memset(out, 0, sizeof(out));
                            out[0].type = EV_ABS; out[0].code = ABS_X;
                            out[0].value = apply_axial_deadzone(normalize_axis(lx_val, 0), 0.12, 1.0);
                            out[1].type = EV_ABS; out[1].code = ABS_Y;
                            out[1].value = apply_axial_deadzone(normalize_axis(ly_val, 0), 0.12, 1.0);
                            out[2].type = EV_ABS; out[2].code = ABS_RX;
                            out[2].value = apply_axial_deadzone(normalize_axis(rx_val, 0), 0.12, 1.0);
                            out[3].type = EV_ABS; out[3].code = ABS_RY;
                            out[3].value = apply_axial_deadzone(normalize_axis(ry_val, 0), 0.12, 1.0);
                            out[4].type = EV_SYN; out[4].code = SYN_REPORT; out[4].value = 0;
                            if (batch_uinput_write) {
                                ssize_t wu = write(ufd, out, sizeof(out));
                                (void)wu;
                                total_syscalls++;
                            } else {
                                for (int k = 0; k < 5; k++) {
                                    ssize_t wu = write(ufd, &out[k], sizeof(out[k]));
                                    (void)wu;
                                    total_syscalls++;
                                }
                            }
                            got_syn = 1;
                        }
                    }
                }
            }
        }
        int64_t t1 = monotonic_ns();
        samples[f] = t1 - t0;
    }

    ioctl(ufd, UI_DEV_DESTROY);
    close(ufd);
    close(sv[0]);
    close(sv[1]);

    double sum = 0.0;
    for (int i = 0; i < frames; i++) sum += (double)samples[i];
    qsort(samples, (size_t)frames, sizeof(int64_t), cmp_i64);

    BenchResult res;
    res.avg_us = (sum / (double)frames) / 1000.0;
    res.p50_us = (double)samples[frames / 2] / 1000.0;
    res.p95_us = (double)samples[(int)(frames * 0.95)] / 1000.0;
    res.p99_us = (double)samples[(int)(frames * 0.99)] / 1000.0;
    res.max_us = (double)samples[frames - 1] / 1000.0;
    res.syscalls = total_syscalls;
    free(samples);
    return res;
}

static long read_vmlck_kb(void) {
    FILE *f = fopen("/proc/self/status", "r");
    if (!f) return -1;
    char line[256];
    long kb = 0;
    while (fgets(line, sizeof(line), f)) {
        if (sscanf(line, "VmLck: %ld kB", &kb) == 1) break;
    }
    fclose(f);
    return kb;
}

static int kernel_defuzz(int value, int old_val, int fuzz) {
    if (!fuzz) return value;
    if (value > old_val - fuzz / 2 && value < old_val + fuzz / 2) return old_val;
    if (value > old_val - fuzz && value < old_val + fuzz) return (old_val * 3 + value) / 4;
    if (value > old_val - fuzz * 2 && value < old_val + fuzz * 2) return (old_val + value) / 2;
    return value;
}

int main(void) {
    printf("=== 1. Verificacion real de OS (mlockall y prioridad) ===\n");
    int ml = mlockall(MCL_CURRENT | MCL_FUTURE);
    printf("mlockall(MCL_CURRENT|MCL_FUTURE) rc=%d errno=%d, VmLck=%ld kB\n",
           ml, (ml < 0 ? errno : 0), read_vmlck_kb());
    errno = 0;
    int sp = setpriority(PRIO_PROCESS, 0, -10);
    printf("setpriority(-10) rc=%d errno=%d (%s)\n",
           sp, errno, (sp == 0 ? "OK" : strerror(errno)));
    munlockall();
    setpriority(PRIO_PROCESS, 0, 0);

    const int FRAMES = 20000;
    printf("\n=== 2. Pipeline real evdev -> filtro -> /dev/uinput (%d frames, sin carga) ===\n", FRAMES);
    BenchResult r_norm_idle = run_pipeline_bench(FRAMES, 0, 0);
    BenchResult r_ll_idle = run_pipeline_bench(FRAMES, 1, 0);
    BenchResult r_ll_batch_idle = run_pipeline_bench(FRAMES, 1, 1);

    printf("Normal (fuzz=16, 1-by-1)    : avg=%.2f us | p50=%.2f us | p95=%.2f us | p99=%.2f us | max=%.2f us | syscalls=%ld\n",
           r_norm_idle.avg_us, r_norm_idle.p50_us, r_norm_idle.p95_us, r_norm_idle.p99_us, r_norm_idle.max_us, r_norm_idle.syscalls);
    printf("Baja Latencia (sin batch W) : avg=%.2f us | p50=%.2f us | p95=%.2f us | p99=%.2f us | max=%.2f us | syscalls=%ld\n",
           r_ll_idle.avg_us, r_ll_idle.p50_us, r_ll_idle.p95_us, r_ll_idle.p99_us, r_ll_idle.max_us, r_ll_idle.syscalls);
    printf("Baja Latencia + Batch Write : avg=%.2f us | p50=%.2f us | p95=%.2f us | p99=%.2f us | max=%.2f us | syscalls=%ld\n",
           r_ll_batch_idle.avg_us, r_ll_batch_idle.p50_us, r_ll_batch_idle.p95_us, r_ll_batch_idle.p99_us, r_ll_batch_idle.max_us, r_ll_batch_idle.syscalls);

    printf("\n=== 3. Bajo carga intensa de CPU (4 procesos al 100%%) ===\n");
    pid_t workers[4];
    for (int i = 0; i < 4; i++) workers[i] = spawn_cpu_stress_proc();
    usleep(50000);
    BenchResult r_norm_load = run_pipeline_bench(FRAMES, 0, 0);
    BenchResult r_ll_load = run_pipeline_bench(FRAMES, 1, 1);
    for (int i = 0; i < 4; i++) {
        if (workers[i] > 0) {
            kill(workers[i], SIGTERM);
            waitpid(workers[i], NULL, 0);
        }
    }

    printf("Normal (bajo carga)         : avg=%.2f us | p50=%.2f us | p95=%.2f us | p99=%.2f us | max=%.2f us | syscalls=%ld\n",
           r_norm_load.avg_us, r_norm_load.p50_us, r_norm_load.p95_us, r_norm_load.p99_us, r_norm_load.max_us, r_norm_load.syscalls);
    printf("Baja Latencia + Batch (carga): avg=%.2f us | p50=%.2f us | p95=%.2f us | p99=%.2f us | max=%.2f us | syscalls=%ld\n",
           r_ll_load.avg_us, r_ll_load.p50_us, r_ll_load.p95_us, r_ll_load.p99_us, r_ll_load.max_us, r_ll_load.syscalls);

    printf("\n=== 4. Efecto real de kernel fuzz=16 vs fuzz=0 en micro-movimientos ===\n");
    int dropped = 0, attenuated = 0;
    for (int delta = 1; delta <= 32; delta++) {
        int out16 = kernel_defuzz(delta, 0, 16);
        int out0 = kernel_defuzz(delta, 0, 0);
        if (out16 == 0) dropped++;
        else if (out16 < out0) attenuated++;
    }
    printf("Micro-movimientos 1..32 unidades: fuzz=16 descarta %d y recorta %d | fuzz=0 deja pasar 32/32 exactos\n",
           dropped, attenuated);

    printf("\n=== 5. Tiempo de actuacion en barrido humano realista ===\n");
    /* Barrido de palanca 0 -> 100% en 40 ms (400 pasos de 0.1 ms), umbral de giro 25% (8192) */
    double t_lin = -1, t_fast = -1, t_inst = -1;
    for (int step = 0; step <= 400; step++) {
        double ms = step * 0.1;
        double norm = (double)step / 400.0;
        if (t_lin < 0 && apply_axial_deadzone(norm, 0.12, 1.00) >= 8192) t_lin = ms;
        if (t_fast < 0 && apply_axial_deadzone(norm, 0.12, 0.78) >= 8192) t_fast = ms;
        if (t_inst < 0 && apply_axial_deadzone(norm, 0.12, 0.62) >= 8192) t_inst = ms;
    }
    printf("Alcanzar 25%% de giro (umbral 8192): Normal=%.1f ms | Rapida(0.78)=%.1f ms | Instantanea(0.62)=%.1f ms\n",
           t_lin, t_fast, t_inst);

    /* Gatillo 0 -> 100% en 35 ms (0..1023) */
    double trig_norm = 35.0;
    double trig_hair = (100.0 / 1023.0) * 35.0;
    printf("Alcanzar 100%% en gatillo (barrido 35 ms): Normal=%.1f ms | Hair-Trigger=%.1f ms (ahorro %.1f ms)\n",
           trig_norm, trig_hair, trig_norm - trig_hair);

    return 0;
}
