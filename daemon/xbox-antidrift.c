#define _GNU_SOURCE
#include <dirent.h>
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
#include <time.h>
#include <unistd.h>

#define MAX_MACROS 32
#define MAX_STEPS 64
#define NUM_STD_KEYS 11
#define MAX_FF_EFFECTS 32

static volatile sig_atomic_t running = 1;
static volatile sig_atomic_t reload_cfg = 0;

static void handle_sig(int sig) {
    (void)sig;
    running = 0;
}

static void handle_hup(int sig) {
    (void)sig;
    reload_cfg = 1;
}

static int64_t monotonic_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000LL + (int64_t)(ts.tv_nsec / 1000000LL);
}

static const int STD_KEYS[NUM_STD_KEYS] = {
    304, 305, 307, 308, 310, 311, 314, 315, 316, 317, 318
};

static int std_key_index(int code) {
    for (int i = 0; i < NUM_STD_KEYS; i++) {
        if (STD_KEYS[i] == code) return i;
    }
    return -1;
}

static int parse_btn_name(const char *name) {
    if (strcmp(name, "A") == 0) return 304;
    if (strcmp(name, "B") == 0) return 305;
    if (strcmp(name, "X") == 0) return 307;
    if (strcmp(name, "Y") == 0) return 308;
    if (strcmp(name, "LB") == 0) return 310;
    if (strcmp(name, "RB") == 0) return 311;
    if (strcmp(name, "VIEW") == 0) return 314;
    if (strcmp(name, "MENU") == 0) return 315;
    if (strcmp(name, "GUIDE") == 0) return 316;
    if (strcmp(name, "LS") == 0) return 317;
    if (strcmp(name, "RS") == 0) return 318;
    return -1;
}

typedef struct {
    int center_lx;
    int center_ly;
    double dz_l;
    int center_rx;
    int center_ry;
    double dz_r;
    int dz_mode;        /* 0=axial, 1=radial, 2=hybrid */
    double outer_comp_l; /* 0.0..0.30 */
    double outer_comp_r; /* 0.0..0.30 */
    int low_latency;
    double response_curve;
    int hair_trigger;
    int debounce_ms;
    int rumble_gain;    /* 0..100 */
    int swap_nintendo;  /* 0 or 1 */
    char device_path[256];
} Config;

static Config load_config(void) {
    Config cfg = {
        .center_lx = 0,
        .center_ly = 0,
        .dz_l = 0.12,
        .center_rx = 0,
        .center_ry = 0,
        .dz_r = 0.12,
        .dz_mode = 0,
        .outer_comp_l = 0.0,
        .outer_comp_r = 0.0,
        .low_latency = 0,
        .response_curve = 1.0,
        .hair_trigger = 0,
        .debounce_ms = 0,
        .rumble_gain = 100,
        .swap_nintendo = 0,
    };
    snprintf(cfg.device_path, sizeof(cfg.device_path), "auto");

    const char *home = getenv("HOME");
    if (!home) return cfg;
    char path[512];
    snprintf(path, sizeof(path), "%s/.config/xbox-antidrift.conf", home);
    FILE *f = fopen(path, "r");
    if (!f) return cfg;

    char line[384];
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#' || line[0] == '\n') continue;
        char key[64];
        char strval[256];
        if (sscanf(line, " %63[^= ] = %255[^\r\n]", key, strval) == 2) {
            double val = strtod(strval, NULL);
            if (strcmp(key, "center_lx") == 0) cfg.center_lx = (int)val;
            else if (strcmp(key, "center_ly") == 0) cfg.center_ly = (int)val;
            else if (strcmp(key, "dz_l") == 0) cfg.dz_l = val;
            else if (strcmp(key, "center_rx") == 0) cfg.center_rx = (int)val;
            else if (strcmp(key, "center_ry") == 0) cfg.center_ry = (int)val;
            else if (strcmp(key, "dz_r") == 0) cfg.dz_r = val;
            else if (strcmp(key, "dz_mode") == 0) {
                if (strcmp(strval, "radial") == 0) cfg.dz_mode = 1;
                else if (strcmp(strval, "hybrid") == 0) cfg.dz_mode = 2;
                else cfg.dz_mode = (int)val;
                if (cfg.dz_mode < 0 || cfg.dz_mode > 2) cfg.dz_mode = 0;
            }
            else if (strcmp(key, "outer_comp_l") == 0) cfg.outer_comp_l = fmax(0.0, fmin(0.30, val));
            else if (strcmp(key, "outer_comp_r") == 0) cfg.outer_comp_r = fmax(0.0, fmin(0.30, val));
            else if (strcmp(key, "low_latency") == 0) cfg.low_latency = ((int)val != 0);
            else if (strcmp(key, "hair_trigger") == 0) cfg.hair_trigger = ((int)val != 0);
            else if (strcmp(key, "response_curve") == 0) cfg.response_curve = fmax(0.45, fmin(1.30, val));
            else if (strcmp(key, "debounce_ms") == 0) {
                cfg.debounce_ms = (int)val;
                if (cfg.debounce_ms < 0) cfg.debounce_ms = 0;
                if (cfg.debounce_ms > 80) cfg.debounce_ms = 80;
            }
            else if (strcmp(key, "rumble_gain") == 0) {
                cfg.rumble_gain = (int)val;
                if (cfg.rumble_gain < 0) cfg.rumble_gain = 0;
                if (cfg.rumble_gain > 100) cfg.rumble_gain = 100;
            }
            else if (strcmp(key, "swap_nintendo") == 0) cfg.swap_nintendo = ((int)val != 0);
            else if (strcmp(key, "device_path") == 0) {
                snprintf(cfg.device_path, sizeof(cfg.device_path), "%s", strval);
            }
        }
    }
    fclose(f);
    return cfg;
}

typedef enum {
    STEP_KEY = 1,
    STEP_WAIT,
    STEP_HAT,
    STEP_TRIG,
    STEP_STICK,
    STEP_STICK_REL
} StepKind;

typedef struct {
    StepKind kind;
    int code;
    int v1;
    int v2;
} MacroStep;

typedef enum {
    MODE_SEQUENCE = 0,
    MODE_HOLD_REPEAT = 1,
    MODE_TOGGLE = 2,
    MODE_HOLD_MIRROR = 3
} MacroMode;

typedef struct {
    int enabled;
    int trigger_code;
    MacroMode mode;
    int intercept;
    int repeat_ms;
    int num_steps;
    MacroStep steps[MAX_STEPS];

    int active;
    int toggle_on;
    int trigger_down;
    int step_idx;
    int64_t next_ms;

    uint16_t held_btns;
    uint8_t held_lt;
    uint8_t held_rt;
    uint8_t held_hat;
    uint8_t held_stick_l;
    uint8_t held_stick_r;
} MacroDef;

typedef struct {
    int count;
    MacroDef items[MAX_MACROS];
} MacroTable;

static void parse_steps(MacroDef *m, const char *raw) {
    m->num_steps = 0;
    char buf[2048];
    snprintf(buf, sizeof(buf), "%s", raw);
    char *saveptr = NULL;
    for (char *tok = strtok_r(buf, ";", &saveptr);
         tok && m->num_steps < MAX_STEPS;
         tok = strtok_r(NULL, ";", &saveptr)) {
        MacroStep st;
        memset(&st, 0, sizeof(st));
        char op[32], arg1[32];
        int a = 0, b = 0;

        if (sscanf(tok, "KEY:%31[^:]:%d", arg1, &a) == 2) {
            int code = parse_btn_name(arg1);
            if (code >= 0) {
                st.kind = STEP_KEY;
                st.code = code;
                st.v1 = a ? 1 : 0;
                m->steps[m->num_steps++] = st;
            }
        } else if (sscanf(tok, "WAIT:%d", &a) == 1) {
            if (a < 1) a = 1;
            if (a > 10000) a = 10000;
            st.kind = STEP_WAIT;
            st.v1 = a;
            m->steps[m->num_steps++] = st;
        } else if (sscanf(tok, "HAT:%d:%d", &a, &b) == 2) {
            st.kind = STEP_HAT;
            st.v1 = (a < 0) ? -1 : ((a > 0) ? 1 : 0);
            st.v2 = (b < 0) ? -1 : ((b > 0) ? 1 : 0);
            m->steps[m->num_steps++] = st;
        } else if (sscanf(tok, "TRIG:%31[^:]:%d", arg1, &a) == 2) {
            if (a < 0) a = 0;
            if (a > 1023) a = 1023;
            st.kind = STEP_TRIG;
            st.code = (strcmp(arg1, "LT") == 0) ? ABS_Z : ABS_RZ;
            st.v1 = a;
            m->steps[m->num_steps++] = st;
        } else if (sscanf(tok, "STICK:%31[^:]:%d:%d", arg1, &a, &b) == 3) {
            if (a < -32768) a = -32768;
            if (a > 32767) a = 32767;
            if (b < -32768) b = -32768;
            if (b > 32767) b = 32767;
            st.kind = STEP_STICK;
            st.code = (strcmp(arg1, "L") == 0) ? 0 : 1;
            st.v1 = a;
            st.v2 = b;
            m->steps[m->num_steps++] = st;
        } else if (sscanf(tok, "STICK_REL:%31s", op) == 1) {
            st.kind = STEP_STICK_REL;
            st.code = (strcmp(op, "L") == 0) ? 0 : 1;
            m->steps[m->num_steps++] = st;
        }
    }
}

static MacroTable load_macros(void) {
    MacroTable mt;
    memset(&mt, 0, sizeof(mt));

    const char *home = getenv("HOME");
    if (!home) return mt;
    char path[512];
    snprintf(path, sizeof(path), "%s/.config/xbox-antidrift-macros.conf", home);
    FILE *f = fopen(path, "r");
    if (!f) return mt;

    MacroDef *cur = NULL;
    char line[2300];
    while (fgets(line, sizeof(line), f)) {
        size_t len = strlen(line);
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r')) {
            line[--len] = '\0';
        }
        if (line[0] == '#' || line[0] == '\0') continue;

        if (strcmp(line, "[macro]") == 0) {
            if (mt.count < MAX_MACROS) {
                cur = &mt.items[mt.count++];
                memset(cur, 0, sizeof(*cur));
                cur->enabled = 1;
                cur->intercept = 1;
                cur->repeat_ms = 50;
            } else {
                cur = NULL;
            }
            continue;
        }
        if (!cur) continue;

        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        char *k = line;
        char *v = eq + 1;
        while (*k == ' ' || *k == '\t') k++;
        char *kend = k + strlen(k);
        while (kend > k && (kend[-1] == ' ' || kend[-1] == '\t')) *(--kend) = '\0';
        while (*v == ' ' || *v == '\t') v++;

        if (strcmp(k, "enabled") == 0) cur->enabled = atoi(v) ? 1 : 0;
        else if (strcmp(k, "trigger_code") == 0) cur->trigger_code = atoi(v);
        else if (strcmp(k, "intercept") == 0) cur->intercept = atoi(v) ? 1 : 0;
        else if (strcmp(k, "repeat_ms") == 0) {
            cur->repeat_ms = atoi(v);
            if (cur->repeat_ms < 10) cur->repeat_ms = 10;
        } else if (strcmp(k, "mode") == 0) {
            if (strcmp(v, "hold_repeat") == 0) cur->mode = MODE_HOLD_REPEAT;
            else if (strcmp(v, "toggle") == 0) cur->mode = MODE_TOGGLE;
            else if (strcmp(v, "hold_mirror") == 0) cur->mode = MODE_HOLD_MIRROR;
            else cur->mode = MODE_SEQUENCE;
        } else if (strcmp(k, "steps") == 0) {
            parse_steps(cur, v);
        }
    }
    fclose(f);
    return mt;
}

static int scale_hw_to_i16(int raw, int hw_min, int hw_max) {
    if (hw_min == -32768 && hw_max == 32767) return raw;
    if (hw_max <= hw_min) return 0;
    double frac = (double)(raw - hw_min) / (double)(hw_max - hw_min);
    if (frac < 0.0) frac = 0.0;
    if (frac > 1.0) frac = 1.0;
    int v = (int)llround(-32768.0 + frac * 65535.0);
    if (v < -32768) v = -32768;
    if (v > 32767) v = 32767;
    return v;
}

static int scale_trig_to_1023(int raw, int hw_min, int hw_max) {
    if (hw_min == 0 && hw_max == 1023) {
        if (raw < 0) return 0;
        if (raw > 1023) return 1023;
        return raw;
    }
    if (hw_max <= hw_min) return 0;
    double frac = (double)(raw - hw_min) / (double)(hw_max - hw_min);
    if (frac < 0.0) frac = 0.0;
    if (frac > 1.0) frac = 1.0;
    return (int)llround(frac * 1023.0);
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

static double shape_scalar(double mag, double dz, double outer_comp, double curve) {
    if (mag <= dz || dz >= 0.999) return 0.0;
    double reach = 1.0 - outer_comp;
    if (reach < dz + 0.10) reach = dz + 0.10;
    double scaled = (mag - dz) / (reach - dz);
    if (scaled > 1.0) scaled = 1.0;
    if (curve != 1.0 && scaled > 0.0) {
        scaled = pow(scaled, curve);
        if (scaled > 1.0) scaled = 1.0;
    }
    return scaled;
}

static int scalar_to_i16(double signed_val) {
    int iv = (int)llround(signed_val * 32767.0);
    if (iv > 32767) iv = 32767;
    if (iv < -32768) iv = -32768;
    return iv;
}

static void apply_stick_transform(
    double nx, double ny, double dz, int dz_mode,
    double outer_comp, double curve, int *ox, int *oy) {
    if (dz_mode == 1 || dz_mode == 2) {
        if (dz_mode == 2) {
            /* Hybrid: light axial snap around axes before radial scaling */
            double ax_dz = dz * 0.40;
            if (fabs(nx) <= ax_dz) nx = 0.0;
            if (fabs(ny) <= ax_dz) ny = 0.0;
        }
        double r = hypot(nx, ny);
        if (r <= dz || r < 1e-9) {
            *ox = 0;
            *oy = 0;
            return;
        }
        double sr = shape_scalar(r, dz, outer_comp, curve);
        double ux = nx / r;
        double uy = ny / r;
        *ox = scalar_to_i16(ux * sr);
        *oy = scalar_to_i16(uy * sr);
        return;
    }

    double sx = shape_scalar(fabs(nx), dz, outer_comp, curve);
    double sy = shape_scalar(fabs(ny), dz, outer_comp, curve);
    *ox = scalar_to_i16((nx < 0.0) ? -sx : sx);
    *oy = scalar_to_i16((ny < 0.0) ? -sy : sy);
}

static int apply_hair_trigger(int val, int hair_trigger) {
    if (!hair_trigger) return val;
    return (val >= 100) ? 1023 : 0;
}

static void apply_low_latency_os(int enable) {
    if (enable) {
        mlockall(MCL_CURRENT | MCL_FUTURE);
        setpriority(PRIO_PROCESS, 0, -10);
    } else {
        munlockall();
        setpriority(PRIO_PROCESS, 0, 0);
    }
}

static int bit_is_set(const unsigned char *bits, int code) {
    return (bits[code >> 3] & (1 << (code & 7))) != 0;
}

static int is_virtual_event(const char *ev_name) {
    char uevent_path[300], name_path[300], dev_name[128] = {0};
    snprintf(name_path, sizeof(name_path), "/sys/class/input/%s/device/name", ev_name);
    FILE *nf = fopen(name_path, "r");
    if (nf) {
        if (!fgets(dev_name, sizeof(dev_name), nf)) dev_name[0] = '\0';
        fclose(nf);
    }

    snprintf(uevent_path, sizeof(uevent_path), "/sys/class/input/%s/device/uevent", ev_name);
    FILE *uf = fopen(uevent_path, "r");
    int has_phys = 0;
    int is_virtual_devpath = 0;
    if (uf) {
        char uline[256];
        while (fgets(uline, sizeof(uline), uf)) {
            if (strncmp(uline, "PHYS=", 5) == 0 && strlen(uline) > 6) has_phys = 1;
            if (strncmp(uline, "DEVPATH=", 8) == 0 && strstr(uline, "/virtual/")) {
                is_virtual_devpath = 1;
            }
        }
        fclose(uf);
    }
    if (is_virtual_devpath) return 1;
    if (strstr(dev_name, "Microsoft X-Box 360 pad") && !has_phys) return 1;
    return 0;
}

static int score_gamepad_node(const char *ev_name, const char *dev_node) {
    if (is_virtual_event(ev_name)) return -1;

    char name_path[300], dev_name[160] = {0};
    snprintf(name_path, sizeof(name_path), "/sys/class/input/%s/device/name", ev_name);
    FILE *nf = fopen(name_path, "r");
    if (nf) {
        if (!fgets(dev_name, sizeof(dev_name), nf)) dev_name[0] = '\0';
        fclose(nf);
    }

    if (strstr(dev_name, "Motion Sensor") || strstr(dev_name, "Touchpad") ||
        strstr(dev_name, "Accelerometer") || strstr(dev_name, "Mouse")) {
        return -1;
    }

    int fd = open(dev_node, O_RDONLY | O_NONBLOCK);
    if (fd < 0) return -1;

    unsigned char key_bits[96] = {0};
    unsigned char abs_bits[8] = {0};
    int score = -1;
    if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits) >= 0 &&
        ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(abs_bits)), abs_bits) >= 0) {
        int has_xy = bit_is_set(abs_bits, ABS_X) && bit_is_set(abs_bits, ABS_Y);
        int has_mouse_left = bit_is_set(key_bits, BTN_LEFT);
        int has_south = bit_is_set(key_bits, BTN_SOUTH);
        int has_trigger_joy = bit_is_set(key_bits, BTN_TRIGGER);

        if (has_xy && !has_mouse_left && (has_south || has_trigger_joy)) {
            score = 10;
            if (has_south) score += 50;
            if (bit_is_set(abs_bits, ABS_RX) && bit_is_set(abs_bits, ABS_RY)) score += 25;
            if (bit_is_set(abs_bits, ABS_HAT0X)) score += 15;
            if (strstr(dev_name, "Xbox") || strstr(dev_name, "X-Box") ||
                strstr(dev_name, "DualSense") || strstr(dev_name, "Wireless Controller") ||
                strstr(dev_name, "Switch") || strstr(dev_name, "Pro Controller") ||
                strstr(dev_name, "8BitDo") || strstr(dev_name, "GameSir") ||
                strstr(dev_name, "Gamepad")) {
                score += 20;
            }
        }
    }
    close(fd);
    return score;
}

static int find_controller_path(const Config *cfg, char *out, size_t out_sz) {
    if (cfg->device_path[0] && strcmp(cfg->device_path, "auto") != 0) {
        if (access(cfg->device_path, R_OK) == 0) {
            const char *base = strrchr(cfg->device_path, '/');
            const char *ev_name = base ? (base + 1) : cfg->device_path;
            if (strncmp(ev_name, "event", 5) == 0) {
                if (score_gamepad_node(ev_name, cfg->device_path) >= 0) {
                    snprintf(out, out_sz, "%s", cfg->device_path);
                    return 0;
                }
            } else {
                snprintf(out, out_sz, "%s", cfg->device_path);
                return 0;
            }
        }
    }

    DIR *dir = opendir("/sys/class/input");
    if (!dir) return -1;

    int best_score = -1;
    char best_path[300] = {0};
    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (strncmp(ent->d_name, "event", 5) != 0) continue;
        char dev_node[300];
        snprintf(dev_node, sizeof(dev_node), "/dev/input/%s", ent->d_name);
        int sc = score_gamepad_node(ent->d_name, dev_node);
        if (sc > best_score) {
            best_score = sc;
            snprintf(best_path, sizeof(best_path), "%s", dev_node);
        }
    }
    closedir(dir);

    if (best_score >= 0) {
        snprintf(out, out_sz, "%s", best_path);
        return 0;
    }
    return -1;
}

typedef struct {
    int lx_code, ly_code;
    int rx_code, ry_code;
    int lt_code, rt_code;
    int dig_lt_code, dig_rt_code;
    int use_generic_joy_btns;
    int use_dpad_btns;
    int has_ff_rumble;

    int lx_min, lx_max;
    int ly_min, ly_max;
    int rx_min, rx_max;
    int ry_min, ry_max;
    int lt_min, lt_max;
    int rt_min, rt_max;
} HardwareMap;

static void query_abs_bounds(int fd, int code, int def_min, int def_max, int *out_min, int *out_max) {
    struct input_absinfo info;
    if (code >= 0 && ioctl(fd, EVIOCGABS(code), &info) == 0 && info.maximum > info.minimum) {
        *out_min = info.minimum;
        *out_max = info.maximum;
    } else {
        *out_min = def_min;
        *out_max = def_max;
    }
}

static HardwareMap probe_hardware_map(int src_fd, int src_is_rw) {
    HardwareMap hm;
    memset(&hm, 0, sizeof(hm));
    hm.lx_code = ABS_X;
    hm.ly_code = ABS_Y;
    hm.rx_code = -1;
    hm.ry_code = -1;
    hm.lt_code = -1;
    hm.rt_code = -1;
    hm.dig_lt_code = -1;
    hm.dig_rt_code = -1;

    unsigned char key_bits[96] = {0};
    unsigned char abs_bits[8] = {0};
    unsigned char ff_bits[16] = {0};
    ioctl(src_fd, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits);
    ioctl(src_fd, EVIOCGBIT(EV_ABS, sizeof(abs_bits)), abs_bits);
    if (src_is_rw && ioctl(src_fd, EVIOCGBIT(EV_FF, sizeof(ff_bits)), ff_bits) >= 0) {
        hm.has_ff_rumble = bit_is_set(ff_bits, FF_RUMBLE);
    }

    if (bit_is_set(abs_bits, ABS_RX) && bit_is_set(abs_bits, ABS_RY)) {
        hm.rx_code = ABS_RX;
        hm.ry_code = ABS_RY;
        if (bit_is_set(abs_bits, ABS_Z)) hm.lt_code = ABS_Z;
        if (bit_is_set(abs_bits, ABS_RZ)) hm.rt_code = ABS_RZ;
    } else if (bit_is_set(abs_bits, ABS_Z) && bit_is_set(abs_bits, ABS_RZ)) {
        hm.rx_code = ABS_Z;
        hm.ry_code = ABS_RZ;
    }

    if (hm.lt_code < 0 && bit_is_set(abs_bits, ABS_BRAKE)) hm.lt_code = ABS_BRAKE;
    if (hm.rt_code < 0 && bit_is_set(abs_bits, ABS_GAS)) hm.rt_code = ABS_GAS;

    if (hm.lt_code < 0 && bit_is_set(key_bits, BTN_TL2)) hm.dig_lt_code = BTN_TL2;
    if (hm.rt_code < 0 && bit_is_set(key_bits, BTN_TR2)) hm.dig_rt_code = BTN_TR2;

    if (!bit_is_set(key_bits, BTN_SOUTH) && bit_is_set(key_bits, BTN_TRIGGER)) {
        hm.use_generic_joy_btns = 1;
    }
    if (!bit_is_set(abs_bits, ABS_HAT0X) && bit_is_set(key_bits, BTN_DPAD_UP)) {
        hm.use_dpad_btns = 1;
    }

    query_abs_bounds(src_fd, hm.lx_code, -32768, 32767, &hm.lx_min, &hm.lx_max);
    query_abs_bounds(src_fd, hm.ly_code, -32768, 32767, &hm.ly_min, &hm.ly_max);
    query_abs_bounds(src_fd, hm.rx_code, -32768, 32767, &hm.rx_min, &hm.rx_max);
    query_abs_bounds(src_fd, hm.ry_code, -32768, 32767, &hm.ry_min, &hm.ry_max);
    query_abs_bounds(src_fd, hm.lt_code, 0, 1023, &hm.lt_min, &hm.lt_max);
    query_abs_bounds(src_fd, hm.rt_code, 0, 1023, &hm.rt_min, &hm.rt_max);

    return hm;
}

static int translate_generic_button(int code) {
    switch (code) {
        case 288: return 304;
        case 289: return 305;
        case 290: return 307;
        case 291: return 308;
        case 292: return 310;
        case 293: return 311;
        case 296: return 314;
        case 297: return 315;
        case 298: return 317;
        case 299: return 318;
        default: return -1;
    }
}

static int apply_nintendo_swap(int code, int swap_nintendo) {
    if (!swap_nintendo) return code;
    if (code == 304) return 305;
    if (code == 305) return 304;
    if (code == 307) return 308;
    if (code == 308) return 307;
    return code;
}

static int setup_uinput(int low_latency, int enable_ff) {
    int ufd = open("/dev/uinput", O_RDWR | O_NONBLOCK);
    if (ufd < 0) {
        perror("open /dev/uinput");
        return -1;
    }

    ioctl(ufd, UI_SET_EVBIT, EV_KEY);
    ioctl(ufd, UI_SET_EVBIT, EV_ABS);
    ioctl(ufd, UI_SET_EVBIT, EV_SYN);

    for (size_t i = 0; i < NUM_STD_KEYS; i++) {
        ioctl(ufd, UI_SET_KEYBIT, STD_KEYS[i]);
    }

    static const int axes[] = {ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y};
    for (size_t i = 0; i < sizeof(axes) / sizeof(axes[0]); i++) {
        int ax = axes[i];
        ioctl(ufd, UI_SET_ABSBIT, ax);
        struct uinput_abs_setup abs_setup;
        memset(&abs_setup, 0, sizeof(abs_setup));
        abs_setup.code = ax;
        if (ax == ABS_Z || ax == ABS_RZ) {
            abs_setup.absinfo.minimum = 0;
            abs_setup.absinfo.maximum = 1023;
        } else if (ax == ABS_HAT0X || ax == ABS_HAT0Y) {
            abs_setup.absinfo.minimum = -1;
            abs_setup.absinfo.maximum = 1;
        } else {
            abs_setup.absinfo.minimum = -32768;
            abs_setup.absinfo.maximum = 32767;
            abs_setup.absinfo.fuzz = low_latency ? 0 : 16;
            abs_setup.absinfo.flat = 0;
        }
        ioctl(ufd, UI_ABS_SETUP, &abs_setup);
    }

    if (enable_ff) {
        ioctl(ufd, UI_SET_EVBIT, EV_FF);
        ioctl(ufd, UI_SET_FFBIT, FF_RUMBLE);
    }

    struct uinput_setup usetup;
    memset(&usetup, 0, sizeof(usetup));
    usetup.id.bustype = BUS_USB;
    usetup.id.vendor = 0x045e;
    usetup.id.product = 0x028e;
    usetup.id.version = 0x0110;
    usetup.ff_effects_max = enable_ff ? 16 : 0;
    snprintf(usetup.name, UINPUT_MAX_NAME_SIZE, "Microsoft X-Box 360 pad");

    if (ioctl(ufd, UI_DEV_SETUP, &usetup) < 0 || ioctl(ufd, UI_DEV_CREATE) < 0) {
        perror("UI_DEV_CREATE");
        close(ufd);
        return -1;
    }
    return ufd;
}

static struct input_event g_ev_batch[64];
static int g_ev_batch_len = 0;

static void flush_ev(int ufd) {
    if (g_ev_batch_len > 0) {
        ssize_t unused = write(
            ufd, g_ev_batch, (size_t)g_ev_batch_len * sizeof(struct input_event));
        (void)unused;
        g_ev_batch_len = 0;
    }
}

static void emit_ev(int ufd, uint16_t type, uint16_t code, int32_t value) {
    struct input_event *ev = &g_ev_batch[g_ev_batch_len++];
    memset(ev, 0, sizeof(*ev));
    ev->type = type;
    ev->code = code;
    ev->value = value;
    if ((type == EV_SYN && code == SYN_REPORT) || g_ev_batch_len >= 64) {
        flush_ev(ufd);
    }
}

typedef struct {
    int phys_btn[NUM_STD_KEYS];
    int64_t btn_last_ms[NUM_STD_KEYS];
    int phys_lt, phys_rt;
    int phys_hx, phys_hy;
    int dpad_u, dpad_d, dpad_l, dpad_r;
    int raw_lx, raw_ly, raw_rx, raw_ry;
    int override_l, override_lx, override_ly;
    int override_r, override_rx, override_ry;
    int ff_map[MAX_FF_EFFECTS];
} PadRuntime;

static void handle_uinput_ff(int ufd, int src_fd, const Config *cfg, PadRuntime *rt) {
    struct input_event uev;
    while (read(ufd, &uev, sizeof(uev)) == (ssize_t)sizeof(uev)) {
        if (uev.type == EV_UINPUT) {
            if (uev.code == UI_FF_UPLOAD) {
                struct uinput_ff_upload up;
                memset(&up, 0, sizeof(up));
                up.request_id = (uint32_t)uev.value;
                if (ioctl(ufd, UI_BEGIN_FF_UPLOAD, &up) == 0) {
                    int uid = up.effect.id;
                    struct ff_effect eff = up.effect;
                    eff.id = (uid >= 0 && uid < MAX_FF_EFFECTS) ? rt->ff_map[uid] : -1;
                    if (eff.type == FF_RUMBLE) {
                        double g = (double)cfg->rumble_gain / 100.0;
                        eff.u.rumble.strong_magnitude = (uint16_t)llround(eff.u.rumble.strong_magnitude * g);
                        eff.u.rumble.weak_magnitude = (uint16_t)llround(eff.u.rumble.weak_magnitude * g);
                    }
                    if (ioctl(src_fd, EVIOCSFF, &eff) == 0 && uid >= 0 && uid < MAX_FF_EFFECTS) {
                        rt->ff_map[uid] = eff.id;
                    }
                    up.retval = 0;
                    ioctl(ufd, UI_END_FF_UPLOAD, &up);
                }
            } else if (uev.code == UI_FF_ERASE) {
                struct uinput_ff_erase er;
                memset(&er, 0, sizeof(er));
                er.request_id = (uint32_t)uev.value;
                if (ioctl(ufd, UI_BEGIN_FF_ERASE, &er) == 0) {
                    if (er.effect_id < MAX_FF_EFFECTS && rt->ff_map[er.effect_id] >= 0) {
                        ioctl(src_fd, EVIOCRMFF, rt->ff_map[er.effect_id]);
                        rt->ff_map[er.effect_id] = -1;
                    }
                    er.retval = 0;
                    ioctl(ufd, UI_END_FF_ERASE, &er);
                }
            }
        } else if (uev.type == EV_FF) {
            if (uev.code < MAX_FF_EFFECTS && rt->ff_map[uev.code] >= 0) {
                struct input_event play;
                memset(&play, 0, sizeof(play));
                play.type = EV_FF;
                play.code = (uint16_t)rt->ff_map[uev.code];
                play.value = (cfg->rumble_gain > 0) ? uev.value : 0;
                ssize_t unused = write(src_fd, &play, sizeof(play));
                (void)unused;
            }
        }
    }
}

static void emit_corrected_sticks(int ufd, const Config *cfg, const PadRuntime *rt, int which) {
    if (which == 0 || which == 2) {
        if (rt->override_l) {
            emit_ev(ufd, EV_ABS, ABS_X, rt->override_lx);
            emit_ev(ufd, EV_ABS, ABS_Y, rt->override_ly);
        } else {
            int ox, oy;
            apply_stick_transform(
                normalize_axis(rt->raw_lx, cfg->center_lx),
                normalize_axis(rt->raw_ly, cfg->center_ly),
                cfg->dz_l, cfg->dz_mode, cfg->outer_comp_l,
                cfg->response_curve, &ox, &oy);
            emit_ev(ufd, EV_ABS, ABS_X, ox);
            emit_ev(ufd, EV_ABS, ABS_Y, oy);
        }
    }
    if (which == 1 || which == 2) {
        if (rt->override_r) {
            emit_ev(ufd, EV_ABS, ABS_RX, rt->override_rx);
            emit_ev(ufd, EV_ABS, ABS_RY, rt->override_ry);
        } else {
            int ox, oy;
            apply_stick_transform(
                normalize_axis(rt->raw_rx, cfg->center_rx),
                normalize_axis(rt->raw_ry, cfg->center_ry),
                cfg->dz_r, cfg->dz_mode, cfg->outer_comp_r,
                cfg->response_curve, &ox, &oy);
            emit_ev(ufd, EV_ABS, ABS_RX, ox);
            emit_ev(ufd, EV_ABS, ABS_RY, oy);
        }
    }
}

static void release_macro_held(int ufd, MacroDef *m, const Config *cfg, PadRuntime *rt) {
    int changed = 0;
    for (int i = 0; i < NUM_STD_KEYS; i++) {
        if (m->held_btns & (1U << i)) {
            emit_ev(ufd, EV_KEY, (uint16_t)STD_KEYS[i], rt->phys_btn[i] ? 1 : 0);
            changed = 1;
        }
    }
    m->held_btns = 0;

    if (m->held_lt) {
        emit_ev(ufd, EV_ABS, ABS_Z, rt->phys_lt);
        m->held_lt = 0;
        changed = 1;
    }
    if (m->held_rt) {
        emit_ev(ufd, EV_ABS, ABS_RZ, rt->phys_rt);
        m->held_rt = 0;
        changed = 1;
    }
    if (m->held_hat) {
        emit_ev(ufd, EV_ABS, ABS_HAT0X, rt->phys_hx);
        emit_ev(ufd, EV_ABS, ABS_HAT0Y, rt->phys_hy);
        m->held_hat = 0;
        changed = 1;
    }
    if (m->held_stick_l) {
        rt->override_l = 0;
        emit_corrected_sticks(ufd, cfg, rt, 0);
        m->held_stick_l = 0;
        changed = 1;
    }
    if (m->held_stick_r) {
        rt->override_r = 0;
        emit_corrected_sticks(ufd, cfg, rt, 1);
        m->held_stick_r = 0;
        changed = 1;
    }
    if (changed) emit_ev(ufd, EV_SYN, SYN_REPORT, 0);
}

static void release_all_macros(int ufd, MacroTable *mt, const Config *cfg, PadRuntime *rt) {
    for (int i = 0; i < mt->count; i++) {
        mt->items[i].active = 0;
        mt->items[i].toggle_on = 0;
        release_macro_held(ufd, &mt->items[i], cfg, rt);
    }
}

static int step_macros(int ufd, MacroTable *mt, const Config *cfg, PadRuntime *rt) {
    int64_t now = monotonic_ms();
    int min_wait = 250;

    for (int i = 0; i < mt->count; i++) {
        MacroDef *m = &mt->items[i];
        if (!m->enabled || !m->active || m->num_steps <= 0) continue;

        while (m->active && now >= m->next_ms) {
            if (m->step_idx >= m->num_steps) {
                if (m->mode == MODE_HOLD_REPEAT && m->trigger_down) {
                    m->step_idx = 0;
                    m->next_ms = now + m->repeat_ms;
                    break;
                } else if (m->mode == MODE_HOLD_MIRROR && m->trigger_down) {
                    m->next_ms = now + 200;
                    break;
                } else if (m->mode == MODE_TOGGLE && m->toggle_on) {
                    m->step_idx = 0;
                    m->next_ms = now + m->repeat_ms;
                    break;
                } else {
                    m->active = 0;
                    release_macro_held(ufd, m, cfg, rt);
                    break;
                }
            }

            MacroStep *st = &m->steps[m->step_idx++];
            int dirty_syn = 0;
            if (st->kind == STEP_KEY) {
                int idx = std_key_index(st->code);
                if (idx >= 0) {
                    if (st->v1) m->held_btns |= (uint16_t)(1U << idx);
                    else m->held_btns &= (uint16_t)~(1U << idx);
                }
                emit_ev(ufd, EV_KEY, (uint16_t)st->code, st->v1);
                dirty_syn = 1;
            } else if (st->kind == STEP_HAT) {
                m->held_hat = 1;
                emit_ev(ufd, EV_ABS, ABS_HAT0X, st->v1);
                emit_ev(ufd, EV_ABS, ABS_HAT0Y, st->v2);
                dirty_syn = 1;
            } else if (st->kind == STEP_TRIG) {
                if (st->code == ABS_Z) m->held_lt = 1;
                else m->held_rt = 1;
                emit_ev(ufd, EV_ABS, (uint16_t)st->code, st->v1);
                dirty_syn = 1;
            } else if (st->kind == STEP_STICK) {
                if (st->code == 0) {
                    m->held_stick_l = 1;
                    rt->override_l = 1;
                    rt->override_lx = st->v1;
                    rt->override_ly = st->v2;
                    emit_corrected_sticks(ufd, cfg, rt, 0);
                } else {
                    m->held_stick_r = 1;
                    rt->override_r = 1;
                    rt->override_rx = st->v1;
                    rt->override_ry = st->v2;
                    emit_corrected_sticks(ufd, cfg, rt, 1);
                }
                dirty_syn = 1;
            } else if (st->kind == STEP_STICK_REL) {
                if (st->code == 0) {
                    m->held_stick_l = 0;
                    rt->override_l = 0;
                    emit_corrected_sticks(ufd, cfg, rt, 0);
                } else {
                    m->held_stick_r = 0;
                    rt->override_r = 0;
                    emit_corrected_sticks(ufd, cfg, rt, 1);
                }
                dirty_syn = 1;
            } else if (st->kind == STEP_WAIT) {
                m->next_ms = now + st->v1;
                break;
            }

            if (dirty_syn) emit_ev(ufd, EV_SYN, SYN_REPORT, 0);
            now = monotonic_ms();
        }

        if (m->active) {
            int64_t diff = m->next_ms - now;
            if (diff < 1) diff = 1;
            if (diff < min_wait) min_wait = (int)diff;
        }
    }
    return min_wait;
}

int main(void) {
    signal(SIGINT, handle_sig);
    signal(SIGTERM, handle_sig);
    signal(SIGHUP, handle_hup);

    while (running) {
        Config cfg = load_config();
        char dev_path[512];
        if (find_controller_path(&cfg, dev_path, sizeof(dev_path)) != 0) {
            sleep(2);
            continue;
        }

        int src_is_rw = 1;
        int src_fd = open(dev_path, O_RDWR | O_NONBLOCK);
        if (src_fd < 0) {
            src_is_rw = 0;
            src_fd = open(dev_path, O_RDONLY | O_NONBLOCK);
        }
        if (src_fd < 0) {
            sleep(2);
            continue;
        }

        if (ioctl(src_fd, EVIOCGRAB, (void *)1) < 0) {
            perror("EVIOCGRAB");
            close(src_fd);
            sleep(2);
            continue;
        }

        apply_low_latency_os(cfg.low_latency);
        HardwareMap hm = probe_hardware_map(src_fd, src_is_rw);
        int ufd = setup_uinput(cfg.low_latency, hm.has_ff_rumble);
        if (ufd < 0) {
            ioctl(src_fd, EVIOCGRAB, (void *)0);
            close(src_fd);
            sleep(2);
            continue;
        }

        MacroTable macros = load_macros();
        PadRuntime rt;
        memset(&rt, 0, sizeof(rt));
        for (int i = 0; i < MAX_FF_EFFECTS; i++) rt.ff_map[i] = -1;
        rt.raw_lx = cfg.center_lx;
        rt.raw_ly = cfg.center_ly;
        rt.raw_rx = cfg.center_rx;
        rt.raw_ry = cfg.center_ry;

        struct input_absinfo info;
        if (hm.lx_code >= 0 && ioctl(src_fd, EVIOCGABS(hm.lx_code), &info) == 0) {
            rt.raw_lx = scale_hw_to_i16(info.value, hm.lx_min, hm.lx_max);
        }
        if (hm.ly_code >= 0 && ioctl(src_fd, EVIOCGABS(hm.ly_code), &info) == 0) {
            rt.raw_ly = scale_hw_to_i16(info.value, hm.ly_min, hm.ly_max);
        }
        if (hm.rx_code >= 0 && ioctl(src_fd, EVIOCGABS(hm.rx_code), &info) == 0) {
            rt.raw_rx = scale_hw_to_i16(info.value, hm.rx_min, hm.rx_max);
        }
        if (hm.ry_code >= 0 && ioctl(src_fd, EVIOCGABS(hm.ry_code), &info) == 0) {
            rt.raw_ry = scale_hw_to_i16(info.value, hm.ry_min, hm.ry_max);
        }
        if (hm.lt_code >= 0 && ioctl(src_fd, EVIOCGABS(hm.lt_code), &info) == 0) {
            rt.phys_lt = scale_trig_to_1023(info.value, hm.lt_min, hm.lt_max);
        }
        if (hm.rt_code >= 0 && ioctl(src_fd, EVIOCGABS(hm.rt_code), &info) == 0) {
            rt.phys_rt = scale_trig_to_1023(info.value, hm.rt_min, hm.rt_max);
        }
        if (ioctl(src_fd, EVIOCGABS(ABS_HAT0X), &info) == 0) rt.phys_hx = info.value;
        if (ioctl(src_fd, EVIOCGABS(ABS_HAT0Y), &info) == 0) rt.phys_hy = info.value;

        emit_corrected_sticks(ufd, &cfg, &rt, 2);
        emit_ev(ufd, EV_SYN, SYN_REPORT, 0);

        int dirty_l = 0, dirty_r = 0;
        struct pollfd pfds[2] = {
            {.fd = src_fd, .events = POLLIN},
            {.fd = ufd, .events = POLLIN},
        };
        nfds_t nfds = hm.has_ff_rumble ? 2 : 1;

        while (running) {
            if (reload_cfg) {
                reload_cfg = 0;
                release_all_macros(ufd, &macros, &cfg, &rt);
                Config new_cfg = load_config();
                if (strcmp(new_cfg.device_path, cfg.device_path) != 0 ||
                    new_cfg.low_latency != cfg.low_latency) {
                    break;
                }
                cfg = new_cfg;
                macros = load_macros();
                emit_corrected_sticks(ufd, &cfg, &rt, 2);
                emit_ev(ufd, EV_ABS, ABS_Z, apply_hair_trigger(rt.phys_lt, cfg.hair_trigger));
                emit_ev(ufd, EV_ABS, ABS_RZ, apply_hair_trigger(rt.phys_rt, cfg.hair_trigger));
                emit_ev(ufd, EV_SYN, SYN_REPORT, 0);
            }

            int timeout_ms = step_macros(ufd, &macros, &cfg, &rt);
            int pr = poll(pfds, nfds, timeout_ms);
            if (pr < 0) {
                if (errno == EINTR) continue;
                break;
            }
            if (pr == 0) continue;

            if (pfds[0].revents & (POLLERR | POLLHUP | POLLNVAL)) {
                break;
            }
            if (hm.has_ff_rumble && (pfds[1].revents & (POLLERR | POLLHUP | POLLNVAL))) {
                break;
            }
            if (hm.has_ff_rumble && (pfds[1].revents & POLLIN)) {
                handle_uinput_ff(ufd, src_fd, &cfg, &rt);
            }
            if (!(pfds[0].revents & POLLIN)) continue;

            struct input_event ev_buf[64];
            int io_err = 0;
            while (running) {
                ssize_t n = read(src_fd, ev_buf, sizeof(ev_buf));
                if (n < 0) {
                    if (errno == EAGAIN || errno == EWOULDBLOCK) break;
                    if (errno == EINTR) continue;
                    io_err = 1;
                    break;
                }
                if (n == 0) {
                    io_err = 1;
                    break;
                }
                int count = (int)(n / (ssize_t)sizeof(struct input_event));
                int64_t ev_now_ms = (cfg.debounce_ms > 0) ? monotonic_ms() : 0;

                for (int ei = 0; ei < count; ei++) {
                    struct input_event ev = ev_buf[ei];
                    if (ev.type == EV_ABS) {
                        if (ev.code == hm.lx_code) {
                            rt.raw_lx = scale_hw_to_i16(ev.value, hm.lx_min, hm.lx_max);
                            dirty_l = 1;
                        } else if (ev.code == hm.ly_code) {
                            rt.raw_ly = scale_hw_to_i16(ev.value, hm.ly_min, hm.ly_max);
                            dirty_l = 1;
                        } else if (ev.code == hm.rx_code) {
                            rt.raw_rx = scale_hw_to_i16(ev.value, hm.rx_min, hm.rx_max);
                            dirty_r = 1;
                        } else if (ev.code == hm.ry_code) {
                            rt.raw_ry = scale_hw_to_i16(ev.value, hm.ry_min, hm.ry_max);
                            dirty_r = 1;
                        } else if (ev.code == hm.lt_code) {
                            rt.phys_lt = scale_trig_to_1023(ev.value, hm.lt_min, hm.lt_max);
                            emit_ev(ufd, EV_ABS, ABS_Z, apply_hair_trigger(rt.phys_lt, cfg.hair_trigger));
                        } else if (ev.code == hm.rt_code) {
                            rt.phys_rt = scale_trig_to_1023(ev.value, hm.rt_min, hm.rt_max);
                            emit_ev(ufd, EV_ABS, ABS_RZ, apply_hair_trigger(rt.phys_rt, cfg.hair_trigger));
                        } else if (ev.code == ABS_HAT0X) {
                            rt.phys_hx = (ev.value < 0) ? -1 : ((ev.value > 0) ? 1 : 0);
                            emit_ev(ufd, EV_ABS, ABS_HAT0X, rt.phys_hx);
                        } else if (ev.code == ABS_HAT0Y) {
                            rt.phys_hy = (ev.value < 0) ? -1 : ((ev.value > 0) ? 1 : 0);
                            emit_ev(ufd, EV_ABS, ABS_HAT0Y, rt.phys_hy);
                        }
                    } else if (ev.type == EV_SYN && ev.code == SYN_REPORT) {
                        if (dirty_l) {
                            emit_corrected_sticks(ufd, &cfg, &rt, 0);
                            dirty_l = 0;
                        }
                        if (dirty_r) {
                            emit_corrected_sticks(ufd, &cfg, &rt, 1);
                            dirty_r = 0;
                        }
                        emit_ev(ufd, EV_SYN, SYN_REPORT, 0);
                    } else if (ev.type == EV_KEY) {
                        int intercepted = 0;
                        for (int i = 0; i < macros.count; i++) {
                            MacroDef *m = &macros.items[i];
                            if (!m->enabled || m->trigger_code != ev.code) continue;
                            if (m->intercept) intercepted = 1;

                            if (ev.value == 1) {
                                m->trigger_down = 1;
                                if (m->mode == MODE_TOGGLE) {
                                    m->toggle_on = !m->toggle_on;
                                    if (m->toggle_on) {
                                        m->active = 1;
                                        m->step_idx = 0;
                                        m->next_ms = monotonic_ms();
                                    } else {
                                        m->active = 0;
                                        release_macro_held(ufd, m, &cfg, &rt);
                                    }
                                } else {
                                    m->active = 1;
                                    m->step_idx = 0;
                                    m->next_ms = monotonic_ms();
                                }
                            } else if (ev.value == 0) {
                                m->trigger_down = 0;
                                if (m->mode == MODE_HOLD_REPEAT || m->mode == MODE_HOLD_MIRROR) {
                                    m->active = 0;
                                    release_macro_held(ufd, m, &cfg, &rt);
                                }
                            }
                        }

                        if (intercepted) continue;

                        if (ev.code == hm.dig_lt_code) {
                            rt.phys_lt = ev.value ? 1023 : 0;
                            emit_ev(ufd, EV_ABS, ABS_Z, rt.phys_lt);
                            emit_ev(ufd, EV_SYN, SYN_REPORT, 0);
                            continue;
                        }
                        if (ev.code == hm.dig_rt_code) {
                            rt.phys_rt = ev.value ? 1023 : 0;
                            emit_ev(ufd, EV_ABS, ABS_RZ, rt.phys_rt);
                            emit_ev(ufd, EV_SYN, SYN_REPORT, 0);
                            continue;
                        }

                        if (hm.use_dpad_btns &&
                            (ev.code == BTN_DPAD_UP || ev.code == BTN_DPAD_DOWN ||
                             ev.code == BTN_DPAD_LEFT || ev.code == BTN_DPAD_RIGHT)) {
                            int v = ev.value ? 1 : 0;
                            if (ev.code == BTN_DPAD_UP) rt.dpad_u = v;
                            else if (ev.code == BTN_DPAD_DOWN) rt.dpad_d = v;
                            else if (ev.code == BTN_DPAD_LEFT) rt.dpad_l = v;
                            else if (ev.code == BTN_DPAD_RIGHT) rt.dpad_r = v;
                            rt.phys_hx = rt.dpad_r - rt.dpad_l;
                            rt.phys_hy = rt.dpad_d - rt.dpad_u;
                            emit_ev(ufd, EV_ABS, ABS_HAT0X, rt.phys_hx);
                            emit_ev(ufd, EV_ABS, ABS_HAT0Y, rt.phys_hy);
                            emit_ev(ufd, EV_SYN, SYN_REPORT, 0);
                            continue;
                        }

                        int target_code = hm.use_generic_joy_btns
                            ? translate_generic_button(ev.code)
                            : ev.code;
                        target_code = apply_nintendo_swap(target_code, cfg.swap_nintendo);
                        int idx = std_key_index(target_code);
                        if (idx >= 0) {
                            int state_val = ev.value ? 1 : 0;
                            if (ev.value != 2 && cfg.debounce_ms > 0) {
                                if (state_val == rt.phys_btn[idx]) continue;
                                if ((ev_now_ms - rt.btn_last_ms[idx]) < (int64_t)cfg.debounce_ms) {
                                    continue;
                                }
                                rt.btn_last_ms[idx] = ev_now_ms;
                            }
                            rt.phys_btn[idx] = state_val;
                            emit_ev(ufd, EV_KEY, (uint16_t)target_code, ev.value);
                        }
                    }
                }
            }
            if (io_err) break;
        }

        release_all_macros(ufd, &macros, &cfg, &rt);
        flush_ev(ufd);
        g_ev_batch_len = 0;
        ioctl(ufd, UI_DEV_DESTROY);
        close(ufd);
        ioctl(src_fd, EVIOCGRAB, (void *)0);
        close(src_fd);
    }
    return 0;
}
