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
#define MAX_PADS 8
#define MAX_PATH_LEN 256
#define DEFAULT_VIRTUAL_NAME "Microsoft X-Box 360 pad"

#ifndef ABS_THROTTLE
#define ABS_THROTTLE 0x06
#endif
#ifndef ABS_RUDDER
#define ABS_RUDDER 0x07
#endif
#ifndef ABS_WHEEL
#define ABS_WHEEL 0x08
#endif
#ifndef ABS_GAS
#define ABS_GAS 0x09
#endif
#ifndef ABS_BRAKE
#define ABS_BRAKE 0x0a
#endif
#ifndef BTN_GEAR_DOWN
#define BTN_GEAR_DOWN 0x150
#endif
#ifndef BTN_GEAR_UP
#define BTN_GEAR_UP 0x151
#endif
#ifndef BTN_TRIGGER_HAPPY1
#define BTN_TRIGGER_HAPPY1 0x2c0
#endif
#ifndef BTN_TRIGGER_HAPPY20
#define BTN_TRIGGER_HAPPY20 0x2d3
#endif
#ifndef KEY_A
#define KEY_A 30
#endif
#ifndef KEY_ENTER
#define KEY_ENTER 28
#endif

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

static void trim_ws(char *s) {
    if (!s) return;
    size_t len = strlen(s);
    while (len > 0 && (s[len - 1] == ' ' || s[len - 1] == '\t' || s[len - 1] == '\r' || s[len - 1] == '\n')) {
        s[--len] = '\0';
    }
    char *p = s;
    while (*p == ' ' || *p == '\t') p++;
    if (p != s) memmove(s, p, strlen(p) + 1);
}

static void read_sys_line(const char *path, char *out, size_t out_sz) {
    if (!out || out_sz == 0) return;
    out[0] = '\0';
    FILE *f = fopen(path, "r");
    if (!f) return;
    if (!fgets(out, (int)out_sz, f)) {
        out[0] = '\0';
    }
    fclose(f);
    trim_ws(out);
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
    int multi_controller; /* 1=protect all connected controllers, 0=only device_path */
    int wheel_mode;         /* 0=auto, 1=wheel, 2=gamepad */
    int wheel_rotation_deg; /* 180..1080 */
    double wheel_steer_dz;  /* 0.0..0.30 */
    double wheel_steer_sat; /* 0.25..1.00 */
    double wheel_linearity; /* 0.45..1.60 */
    double wheel_dz_gas;    /* 0.0..0.35 */
    double wheel_dz_brake;  /* 0.0..0.35 */
    double wheel_max_gas;   /* 0.50..1.00 */
    double wheel_max_brake; /* 0.50..1.00 */
    int wheel_invert_gas;
    int wheel_invert_brake;
    int wheel_combined_pedals;
    double lt_dz, rt_dz;
    double lt_max, rt_max;
    double lt_curve, rt_curve;
    int wheel_shifter_mode;         /* 0=none, 1=paddles, 2=sequential, 3=h_shifter */
    char wheel_shifter_device[MAX_PATH_LEN];
    int wheel_shifter_down_code;
    int wheel_shifter_up_code;
    int wheel_handbrake_enabled;
    char wheel_handbrake_device[MAX_PATH_LEN];
    int wheel_handbrake_target_code;
    double wheel_handbrake_dz;
    int wheel_handbrake_invert;
    int wheel_clutch_mode;          /* 0=none, 1=x_btn, 2=lb_btn */
    char device_path[MAX_PATH_LEN];
    char virtual_name[UINPUT_MAX_NAME_SIZE];
} Config;

static void init_default_config(Config *cfg) {
    memset(cfg, 0, sizeof(*cfg));
    cfg->dz_l = 0.12;
    cfg->dz_r = 0.12;
    cfg->response_curve = 1.0;
    cfg->rumble_gain = 100;
    cfg->multi_controller = 1;
    cfg->lt_max = 1.00;
    cfg->rt_max = 1.00;
    cfg->lt_curve = 1.00;
    cfg->rt_curve = 1.00;
    cfg->wheel_mode = 0;
    cfg->wheel_rotation_deg = 270;
    cfg->wheel_steer_dz = 0.02;
    cfg->wheel_steer_sat = 1.00;
    cfg->wheel_linearity = 1.00;
    cfg->wheel_dz_gas = 0.03;
    cfg->wheel_dz_brake = 0.04;
    cfg->wheel_max_gas = 1.00;
    cfg->wheel_max_brake = 1.00;
    cfg->wheel_shifter_mode = 1;
    snprintf(cfg->wheel_shifter_device, sizeof(cfg->wheel_shifter_device), "auto");
    cfg->wheel_shifter_down_code = 310;
    cfg->wheel_shifter_up_code = 311;
    cfg->wheel_handbrake_enabled = 1;
    snprintf(cfg->wheel_handbrake_device, sizeof(cfg->wheel_handbrake_device), "auto");
    cfg->wheel_handbrake_target_code = 304;
    cfg->wheel_handbrake_dz = 0.20;
    cfg->wheel_clutch_mode = 0;
    snprintf(cfg->device_path, sizeof(cfg->device_path), "auto");
    snprintf(cfg->virtual_name, sizeof(cfg->virtual_name), "%s", DEFAULT_VIRTUAL_NAME);
}

static void apply_kv_to_config(Config *cfg, const char *key, const char *raw_val) {
    char strval[256];
    snprintf(strval, sizeof(strval), "%s", raw_val);
    trim_ws(strval);
    double val = strtod(strval, NULL);

    if (strcmp(key, "center_lx") == 0) cfg->center_lx = (int)val;
    else if (strcmp(key, "center_ly") == 0) cfg->center_ly = (int)val;
    else if (strcmp(key, "dz_l") == 0) cfg->dz_l = fmax(0.0, fmin(0.60, val));
    else if (strcmp(key, "center_rx") == 0) cfg->center_rx = (int)val;
    else if (strcmp(key, "center_ry") == 0) cfg->center_ry = (int)val;
    else if (strcmp(key, "dz_r") == 0) cfg->dz_r = fmax(0.0, fmin(0.60, val));
    else if (strcmp(key, "dz_mode") == 0) {
        if (strcmp(strval, "radial") == 0) cfg->dz_mode = 1;
        else if (strcmp(strval, "hybrid") == 0) cfg->dz_mode = 2;
        else if (strcmp(strval, "axial") == 0) cfg->dz_mode = 0;
        else cfg->dz_mode = (int)val;
        if (cfg->dz_mode < 0 || cfg->dz_mode > 2) cfg->dz_mode = 0;
    }
    else if (strcmp(key, "outer_comp_l") == 0) cfg->outer_comp_l = fmax(0.0, fmin(0.30, val));
    else if (strcmp(key, "outer_comp_r") == 0) cfg->outer_comp_r = fmax(0.0, fmin(0.30, val));
    else if (strcmp(key, "low_latency") == 0) cfg->low_latency = ((int)val != 0);
    else if (strcmp(key, "hair_trigger") == 0) cfg->hair_trigger = ((int)val != 0);
    else if (strcmp(key, "response_curve") == 0) cfg->response_curve = fmax(0.45, fmin(1.30, val));
    else if (strcmp(key, "debounce_ms") == 0) {
        cfg->debounce_ms = (int)val;
        if (cfg->debounce_ms < 0) cfg->debounce_ms = 0;
        if (cfg->debounce_ms > 80) cfg->debounce_ms = 80;
    }
    else if (strcmp(key, "rumble_gain") == 0) {
        cfg->rumble_gain = (int)val;
        if (cfg->rumble_gain < 0) cfg->rumble_gain = 0;
        if (cfg->rumble_gain > 100) cfg->rumble_gain = 100;
    }
    else if (strcmp(key, "swap_nintendo") == 0) cfg->swap_nintendo = ((int)val != 0);
    else if (strcmp(key, "multi_controller") == 0) cfg->multi_controller = ((int)val != 0);
    else if (strcmp(key, "wheel_mode") == 0) {
        if (strcmp(strval, "wheel") == 0) cfg->wheel_mode = 1;
        else if (strcmp(strval, "gamepad") == 0) cfg->wheel_mode = 2;
        else if (strcmp(strval, "auto") == 0) cfg->wheel_mode = 0;
        else cfg->wheel_mode = (int)val;
        if (cfg->wheel_mode < 0 || cfg->wheel_mode > 2) cfg->wheel_mode = 0;
    }
    else if (strcmp(key, "wheel_rotation_deg") == 0) {
        cfg->wheel_rotation_deg = (int)val;
        if (cfg->wheel_rotation_deg < 180) cfg->wheel_rotation_deg = 180;
        if (cfg->wheel_rotation_deg > 1080) cfg->wheel_rotation_deg = 1080;
    }
    else if (strcmp(key, "wheel_steer_dz") == 0) cfg->wheel_steer_dz = fmax(0.0, fmin(0.30, val));
    else if (strcmp(key, "wheel_steer_sat") == 0) cfg->wheel_steer_sat = fmax(0.25, fmin(1.00, val));
    else if (strcmp(key, "wheel_linearity") == 0) cfg->wheel_linearity = fmax(0.45, fmin(1.60, val));
    else if (strcmp(key, "wheel_dz_gas") == 0) cfg->wheel_dz_gas = fmax(0.0, fmin(0.35, val));
    else if (strcmp(key, "wheel_dz_brake") == 0) cfg->wheel_dz_brake = fmax(0.0, fmin(0.35, val));
    else if (strcmp(key, "wheel_max_gas") == 0) cfg->wheel_max_gas = fmax(0.50, fmin(1.00, val));
    else if (strcmp(key, "wheel_max_brake") == 0) cfg->wheel_max_brake = fmax(0.50, fmin(1.00, val));
    else if (strcmp(key, "wheel_invert_gas") == 0) cfg->wheel_invert_gas = ((int)val != 0);
    else if (strcmp(key, "wheel_invert_brake") == 0) cfg->wheel_invert_brake = ((int)val != 0);
    else if (strcmp(key, "wheel_combined_pedals") == 0) cfg->wheel_combined_pedals = ((int)val != 0);
    else if (strcmp(key, "lt_dz") == 0) cfg->lt_dz = fmax(0.0, fmin(0.40, val));
    else if (strcmp(key, "rt_dz") == 0) cfg->rt_dz = fmax(0.0, fmin(0.40, val));
    else if (strcmp(key, "lt_max") == 0) cfg->lt_max = fmax(0.10, fmin(1.00, val));
    else if (strcmp(key, "rt_max") == 0) cfg->rt_max = fmax(0.10, fmin(1.00, val));
    else if (strcmp(key, "lt_curve") == 0) cfg->lt_curve = fmax(0.40, fmin(2.50, val));
    else if (strcmp(key, "rt_curve") == 0) cfg->rt_curve = fmax(0.40, fmin(2.50, val));
    else if (strcmp(key, "wheel_shifter_mode") == 0) {
        if (strcmp(strval, "none") == 0) cfg->wheel_shifter_mode = 0;
        else if (strcmp(strval, "paddles") == 0) cfg->wheel_shifter_mode = 1;
        else if (strcmp(strval, "sequential") == 0) cfg->wheel_shifter_mode = 2;
        else if (strcmp(strval, "h_shifter") == 0) cfg->wheel_shifter_mode = 3;
    }
    else if (strcmp(key, "wheel_shifter_device") == 0) {
        snprintf(cfg->wheel_shifter_device, sizeof(cfg->wheel_shifter_device), "%s", strval[0] ? strval : "auto");
    }
    else if (strcmp(key, "wheel_shifter_down_btn") == 0) {
        int b = parse_btn_name(strval);
        if (b >= 0) cfg->wheel_shifter_down_code = b;
    }
    else if (strcmp(key, "wheel_shifter_up_btn") == 0) {
        int b = parse_btn_name(strval);
        if (b >= 0) cfg->wheel_shifter_up_code = b;
    }
    else if (strcmp(key, "wheel_handbrake_enabled") == 0) cfg->wheel_handbrake_enabled = ((int)val != 0);
    else if (strcmp(key, "wheel_handbrake_device") == 0) {
        snprintf(cfg->wheel_handbrake_device, sizeof(cfg->wheel_handbrake_device), "%s", strval[0] ? strval : "auto");
    }
    else if (strcmp(key, "wheel_handbrake_target") == 0) {
        int b = parse_btn_name(strval);
        if (b >= 0) cfg->wheel_handbrake_target_code = b;
    }
    else if (strcmp(key, "wheel_handbrake_dz") == 0) cfg->wheel_handbrake_dz = fmax(0.05, fmin(0.85, val));
    else if (strcmp(key, "wheel_handbrake_invert") == 0) cfg->wheel_handbrake_invert = ((int)val != 0);
    else if (strcmp(key, "wheel_clutch_mode") == 0) {
        if (strcmp(strval, "x_btn") == 0) cfg->wheel_clutch_mode = 1;
        else if (strcmp(strval, "lb_btn") == 0) cfg->wheel_clutch_mode = 2;
        else cfg->wheel_clutch_mode = 0;
    }
    else if (strcmp(key, "device_path") == 0) {
        if (strval[0]) snprintf(cfg->device_path, sizeof(cfg->device_path), "%s", strval);
        else snprintf(cfg->device_path, sizeof(cfg->device_path), "auto");
    }
    else if (strcmp(key, "virtual_name") == 0) {
        if (strval[0]) {
            snprintf(cfg->virtual_name, sizeof(cfg->virtual_name), "%.*s",
                     (int)sizeof(cfg->virtual_name) - 1, strval);
        } else {
            snprintf(cfg->virtual_name, sizeof(cfg->virtual_name), "%s", DEFAULT_VIRTUAL_NAME);
        }
    }
}

static Config load_global_config(void) {
    Config cfg;
    init_default_config(&cfg);

    const char *home = getenv("HOME");
    if (!home) return cfg;
    char path[512];
    snprintf(path, sizeof(path), "%s/.config/xbox-antidrift.conf", home);
    FILE *f = fopen(path, "r");
    if (!f) return cfg;

    char line[384];
    while (fgets(line, sizeof(line), f)) {
        trim_ws(line);
        if (line[0] == '#' || line[0] == '\0') continue;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        char *key = line;
        char *val = eq + 1;
        trim_ws(key);
        trim_ws(val);
        if (key[0]) apply_kv_to_config(&cfg, key, val);
    }
    fclose(f);
    return cfg;
}

static Config load_device_config(const Config *global_cfg, const char *stable_id) {
    Config cfg = *global_cfg;
    if (!stable_id || !stable_id[0]) return cfg;

    const char *home = getenv("HOME");
    if (!home) return cfg;
    char path[512];
    snprintf(path, sizeof(path), "%s/.config/xbox-antidrift-devices.conf", home);
    FILE *f = fopen(path, "r");
    if (!f) return cfg;

    char line[384];
    int in_section = 0;
    while (fgets(line, sizeof(line), f)) {
        trim_ws(line);
        if (line[0] == '#' || line[0] == '\0') continue;
        if (line[0] == '[') {
            in_section = 0;
            if (strncmp(line, "[device ", 8) == 0) {
                char *end = strrchr(line, ']');
                if (end) {
                    *end = '\0';
                    char *sec_id = line + 8;
                    trim_ws(sec_id);
                    if (strcmp(sec_id, stable_id) == 0) {
                        in_section = 1;
                    }
                }
            }
            continue;
        }
        if (!in_section) continue;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        char *key = line;
        char *val = eq + 1;
        trim_ws(key);
        trim_ws(val);
        if (key[0]) apply_kv_to_config(&cfg, key, val);
    }
    fclose(f);
    return cfg;
}

static void compute_device_stable_id(const char *ev_name, char *out, size_t out_sz) {
    char p_vid[320], p_pid[320], p_uniq[320], p_phys[320];
    char vid[32] = "0000", pid[32] = "0000", uniq[128] = "", phys[128] = "";

    snprintf(p_vid, sizeof(p_vid), "/sys/class/input/%s/device/id/vendor", ev_name);
    snprintf(p_pid, sizeof(p_pid), "/sys/class/input/%s/device/id/product", ev_name);
    snprintf(p_uniq, sizeof(p_uniq), "/sys/class/input/%s/device/uniq", ev_name);
    snprintf(p_phys, sizeof(p_phys), "/sys/class/input/%s/device/phys", ev_name);

    read_sys_line(p_vid, vid, sizeof(vid));
    read_sys_line(p_pid, pid, sizeof(pid));
    if (!vid[0]) snprintf(vid, sizeof(vid), "0000");
    if (!pid[0]) snprintf(pid, sizeof(pid), "0000");

    read_sys_line(p_uniq, uniq, sizeof(uniq));
    read_sys_line(p_phys, phys, sizeof(phys));

    if (uniq[0]) {
        snprintf(out, out_sz, "%s:%s|uniq:%s", vid, pid, uniq);
    } else if (phys[0]) {
        snprintf(out, out_sz, "%s:%s|%s", vid, pid, phys);
    } else {
        snprintf(out, out_sz, "%s:%s|node:%s", vid, pid, ev_name);
    }
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
        trim_ws(k);
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

static int compute_trigger_output(
    int raw_val, double dz, double max_out, double curve, int hair_trigger) {
    int clamped_raw = raw_val;
    if (clamped_raw < 0) clamped_raw = 0;
    if (clamped_raw > 1023) clamped_raw = 1023;
    double cap = fmax(0.10, fmin(1.0, max_out));
    if (hair_trigger) {
        return (clamped_raw >= 100) ? (int)llround(cap * 1023.0) : 0;
    }
    double dz_c = fmax(0.0, fmin(0.95, dz));
    double frac = (double)clamped_raw / 1023.0;
    if (frac <= dz_c) return 0;
    double scaled = (frac - dz_c) / (1.0 - dz_c);
    if (scaled > 1.0) scaled = 1.0;
    if (curve != 1.0 && scaled > 0.0) {
        scaled = pow(scaled, curve);
        if (scaled > 1.0) scaled = 1.0;
    }
    int out = (int)llround(scaled * cap * 1023.0);
    if (out < 0) out = 0;
    if (out > 1023) out = 1023;
    return out;
}

static int compute_wheel_steering(
    int raw_x, int center_x, double dz, double saturation, double linearity) {
    double nx = normalize_axis(raw_x, center_x);
    double mag = fabs(nx);
    double reach = fmax(dz + 0.05, fmin(1.0, saturation));
    if (mag <= dz || dz >= 0.999) return 0;
    double scaled = (mag - dz) / (reach - dz);
    if (scaled > 1.0) scaled = 1.0;
    if (linearity != 1.0 && scaled > 0.0) {
        scaled = pow(scaled, linearity);
        if (scaled > 1.0) scaled = 1.0;
    }
    return scalar_to_i16((nx < 0.0) ? -scaled : scaled);
}

static int compute_wheel_pedal(
    int raw_val, double dz, double max_reach, int invert, int hair_trigger) {
    int clamped_raw = raw_val;
    if (clamped_raw < 0) clamped_raw = 0;
    if (clamped_raw > 1023) clamped_raw = 1023;
    int v = invert ? (1023 - clamped_raw) : clamped_raw;
    double frac = (double)v / 1023.0;
    double reach = fmax(dz + 0.05, fmin(1.0, max_reach));
    int out = 0;
    if (frac > dz) {
        double scaled = (frac - dz) / (reach - dz);
        if (scaled > 1.0) scaled = 1.0;
        out = (int)llround(scaled * 1023.0);
    }
    return apply_hair_trigger(out, hair_trigger);
}

static void split_combined_pedals(int raw_i16, int swap_polarity, int *out_brake, int *out_gas) {
    int val = swap_polarity ? -raw_i16 : raw_i16;
    if (val < 0) {
        double f = fmin(1.0, (double)(-val) / 32768.0);
        *out_brake = 0;
        *out_gas = (int)llround(f * 1023.0);
    } else {
        double f = fmin(1.0, (double)val / 32767.0);
        *out_brake = (int)llround(f * 1023.0);
        *out_gas = 0;
    }
}

static int is_gamepad_device_name(const char *dev_name) {
    if (!dev_name || !dev_name[0]) return 0;
    static const char *const gamepad_tokens[] = {
        "xbox", "x-box", "xinput", "wireless controller",
        "dualsense", "dualshock", "playstation",
        "pro controller", "joy-con", "joycon", "nintendo",
        "gamesir", "8bitdo", "gamepad"
    };
    for (size_t i = 0; i < sizeof(gamepad_tokens) / sizeof(gamepad_tokens[0]); i++) {
        if (strcasestr(dev_name, gamepad_tokens[i]) != NULL) return 1;
    }
    return 0;
}

static int is_wheel_device_name(const char *dev_name) {
    if (!dev_name || !dev_name[0]) return 0;
    static const char *const wheel_tokens[] = {
        "thrustmaster", "t80", "t150", "t248", "t300", "t500", "tmx",
        "ferrari", "g29", "g920", "g923", "g27", "g25",
        "driving force", "fanatec", "moza", "simagic",
        "hori racing", "racing wheel", "steering", "wheel", "volante"
    };
    for (size_t i = 0; i < sizeof(wheel_tokens) / sizeof(wheel_tokens[0]); i++) {
        if (strcasestr(dev_name, wheel_tokens[i]) != NULL) return 1;
    }
    return 0;
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
    char dev_link[300], resolved[512] = {0};
    snprintf(dev_link, sizeof(dev_link), "/sys/class/input/%s/device", ev_name);
    if (realpath(dev_link, resolved) && strstr(resolved, "/virtual/input/") != NULL) {
        return 1;
    }

    char phys_path[300], phys_val[128] = {0};
    snprintf(phys_path, sizeof(phys_path), "/sys/class/input/%s/device/phys", ev_name);
    read_sys_line(phys_path, phys_val, sizeof(phys_val));
    if (strncmp(phys_val, "antidrift:", 10) == 0) {
        return 1;
    }

    char uevent_path[300], name_path[300], dev_name[128] = {0};
    snprintf(name_path, sizeof(name_path), "/sys/class/input/%s/device/name", ev_name);
    read_sys_line(name_path, dev_name, sizeof(dev_name));

    snprintf(uevent_path, sizeof(uevent_path), "/sys/class/input/%s/device/uevent", ev_name);
    FILE *uf = fopen(uevent_path, "r");
    int has_phys = (phys_val[0] != '\0');
    int is_virtual_devpath = 0;
    int is_antidrift_phys = 0;
    if (uf) {
        char uline[256];
        while (fgets(uline, sizeof(uline), uf)) {
            if (strncmp(uline, "PHYS=", 5) == 0 && strlen(uline) > 6) {
                char *pv = uline + 5;
                if (*pv == '"' || *pv == '\'') pv++;
                if (*pv && *pv != '"' && *pv != '\'') has_phys = 1;
                if (strncmp(pv, "antidrift:", 10) == 0) {
                    is_antidrift_phys = 1;
                }
            }
            if (strncmp(uline, "DEVPATH=", 8) == 0 && strstr(uline, "/virtual/input/")) {
                is_virtual_devpath = 1;
            }
        }
        fclose(uf);
    }
    if (is_antidrift_phys || is_virtual_devpath) return 1;
    if (strstr(dev_name, DEFAULT_VIRTUAL_NAME) && !has_phys) return 1;
    return 0;
}

static int score_gamepad_node(const char *ev_name, const char *dev_node) {
    if (is_virtual_event(ev_name)) return -1;

    char name_path[300], dev_name[160] = {0};
    snprintf(name_path, sizeof(name_path), "/sys/class/input/%s/device/name", ev_name);
    read_sys_line(name_path, dev_name, sizeof(dev_name));

    if (strstr(dev_name, "Motion Sensor") || strstr(dev_name, "Touchpad") ||
        strstr(dev_name, "Accelerometer") || strstr(dev_name, "Mouse") ||
        strstr(dev_name, "(IMU)")) {
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
        int has_rxry = bit_is_set(abs_bits, ABS_RX) && bit_is_set(abs_bits, ABS_RY);
        int has_steer = bit_is_set(abs_bits, ABS_X) || bit_is_set(abs_bits, ABS_WHEEL);
        int has_wheel_pedals = bit_is_set(abs_bits, ABS_GAS) ||
                               bit_is_set(abs_bits, ABS_BRAKE) ||
                               bit_is_set(abs_bits, ABS_RZ) ||
                               bit_is_set(abs_bits, ABS_Z) ||
                               bit_is_set(abs_bits, ABS_THROTTLE);
        int has_mouse_left = bit_is_set(key_bits, BTN_LEFT);
        int has_south = bit_is_set(key_bits, BTN_SOUTH);
        int has_trigger_joy = bit_is_set(key_bits, BTN_TRIGGER);
        int has_dpad_btns = bit_is_set(key_bits, BTN_DPAD_UP);
        int has_joycon_btns = bit_is_set(key_bits, BTN_EAST) ||
                              bit_is_set(key_bits, BTN_THUMBL) ||
                              bit_is_set(key_bits, BTN_THUMBR);
        int has_wheel_btns = bit_is_set(key_bits, BTN_GEAR_DOWN) ||
                             bit_is_set(key_bits, BTN_GEAR_UP);
        int wheel_hint = is_wheel_device_name(dev_name) ||
                         bit_is_set(abs_bits, ABS_WHEEL) ||
                         bit_is_set(abs_bits, ABS_GAS) ||
                         bit_is_set(abs_bits, ABS_BRAKE) ||
                         has_wheel_btns;

        if (((has_xy || has_rxry) || (has_steer && (has_wheel_pedals || wheel_hint))) &&
            !has_mouse_left &&
            (has_south || has_trigger_joy || has_dpad_btns || has_joycon_btns ||
             has_wheel_btns || has_wheel_pedals)) {
            score = 10;
            if (has_south) score += 50;
            if (has_xy && has_rxry) score += 25;
            if (wheel_hint) score += 45;
            if (bit_is_set(abs_bits, ABS_HAT0X) || has_dpad_btns) score += 15;
            if (strstr(dev_name, "Xbox") || strstr(dev_name, "X-Box") ||
                strstr(dev_name, "DualSense") || strstr(dev_name, "Wireless Controller") ||
                strstr(dev_name, "Switch") || strstr(dev_name, "Pro Controller") ||
                strstr(dev_name, "Joy-Con") || strstr(dev_name, "Nintendo") ||
                strstr(dev_name, "8BitDo") || strstr(dev_name, "GameSir") ||
                strstr(dev_name, "Gamepad") || wheel_hint) {
                score += 20;
            }
        }
    }
    close(fd);
    return score;
}

typedef struct {
    char ev_name[64];
    char dev_node[MAX_PATH_LEN];
    int score;
} GamepadCandidate;

static int cmp_candidates_desc(const void *a, const void *b) {
    const GamepadCandidate *ca = (const GamepadCandidate *)a;
    const GamepadCandidate *cb = (const GamepadCandidate *)b;
    if (cb->score != ca->score) return cb->score - ca->score;
    return strcmp(ca->dev_node, cb->dev_node);
}

static int list_gamepad_candidates(GamepadCandidate *out, int max_items) {
    DIR *dir = opendir("/sys/class/input");
    if (!dir) return 0;

    int count = 0;
    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL && count < max_items) {
        if (strncmp(ent->d_name, "event", 5) != 0) continue;
        if (strlen(ent->d_name) >= 48) continue;
        char dev_node[MAX_PATH_LEN];
        snprintf(dev_node, sizeof(dev_node), "/dev/input/%.48s", ent->d_name);
        int sc = score_gamepad_node(ent->d_name, dev_node);
        if (sc >= 0) {
            snprintf(out[count].ev_name, sizeof(out[count].ev_name), "%.48s", ent->d_name);
            snprintf(out[count].dev_node, sizeof(out[count].dev_node), "%s", dev_node);
            out[count].score = sc;
            count++;
        }
    }
    closedir(dir);

    if (count > 1) {
        qsort(out, (size_t)count, sizeof(GamepadCandidate), cmp_candidates_desc);
    }
    return count;
}

typedef struct {
    int lx_code, ly_code;
    int rx_code, ry_code;
    int lt_code, rt_code;
    int dig_lt_code, dig_rt_code;
    int is_wheel;
    int combo_pedal_code;
    int clutch_code;
    int handbrake_code;
    int use_generic_joy_btns;
    int use_dpad_btns;
    int wheel_use_gear_shifters;
    int has_ff_rumble;

    int lx_min, lx_max;
    int ly_min, ly_max;
    int rx_min, rx_max;
    int ry_min, ry_max;
    int lt_min, lt_max;
    int rt_min, rt_max;
    int combo_min, combo_max;
    int clutch_min, clutch_max;
    int handbrake_min, handbrake_max;
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
    hm.combo_pedal_code = -1;
    hm.clutch_code = -1;
    hm.handbrake_code = -1;

    unsigned char key_bits[96] = {0};
    unsigned char abs_bits[8] = {0};
    unsigned char ff_bits[16] = {0};
    char dev_name[160] = {0};
    ioctl(src_fd, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits);
    ioctl(src_fd, EVIOCGBIT(EV_ABS, sizeof(abs_bits)), abs_bits);
    ioctl(src_fd, EVIOCGNAME(sizeof(dev_name)), dev_name);
    if (src_is_rw && ioctl(src_fd, EVIOCGBIT(EV_FF, sizeof(ff_bits)), ff_bits) >= 0) {
        hm.has_ff_rumble = bit_is_set(ff_bits, FF_RUMBLE);
    }

    int has_x = bit_is_set(abs_bits, ABS_X);
    int has_y = bit_is_set(abs_bits, ABS_Y);
    int has_wheel_axis = bit_is_set(abs_bits, ABS_WHEEL);
    int has_gas = bit_is_set(abs_bits, ABS_GAS);
    int has_brake = bit_is_set(abs_bits, ABS_BRAKE);
    int has_throttle = bit_is_set(abs_bits, ABS_THROTTLE);
    int has_z = bit_is_set(abs_bits, ABS_Z);
    int has_rz = bit_is_set(abs_bits, ABS_RZ);
    int has_rxry = bit_is_set(abs_bits, ABS_RX) && bit_is_set(abs_bits, ABS_RY);
    int has_gear_btns = bit_is_set(key_bits, BTN_GEAR_DOWN) || bit_is_set(key_bits, BTN_GEAR_UP);
    hm.wheel_use_gear_shifters = has_gear_btns;

    int has_dual_sticks = has_x && has_y && has_rxry;
    int gamepad_name = is_gamepad_device_name(dev_name);
    int wheel_name = is_wheel_device_name(dev_name) && !gamepad_name;
    int pedals_without_dual_sticks = (has_gas || has_brake) && !has_dual_sticks && !gamepad_name;
    int no_y_pedals = has_x && !has_y && (has_z || has_rz || has_throttle);
    if ((wheel_name || has_wheel_axis || has_gear_btns || pedals_without_dual_sticks || no_y_pedals) &&
        !(has_dual_sticks && !has_wheel_axis && !wheel_name)) {
        hm.is_wheel = 1;
    }

    if (hm.is_wheel) {
        hm.lx_code = has_wheel_axis ? ABS_WHEEL : (has_x ? ABS_X : -1);
        hm.ly_code = -1;

        if (has_gas) hm.rt_code = ABS_GAS;
        else if (has_throttle) hm.rt_code = ABS_THROTTLE;
        else if (has_rz) hm.rt_code = ABS_RZ;
        else if (has_z && !has_brake) hm.rt_code = ABS_Z;

        if (has_brake) hm.lt_code = ABS_BRAKE;
        else if (has_z && hm.rt_code != ABS_Z) hm.lt_code = ABS_Z;
        else if (has_rz && hm.rt_code != ABS_RZ) hm.lt_code = ABS_RZ;
        else if (has_y && hm.rt_code >= 0 && hm.rt_code != ABS_Y && !has_rxry) hm.lt_code = ABS_Y;

        if (has_y && !has_gas && !has_brake && !has_throttle && !has_z && !has_rz) {
            hm.combo_pedal_code = ABS_Y;
        } else if (hm.rt_code >= 0 && hm.lt_code < 0) {
            hm.combo_pedal_code = hm.rt_code;
        } else if (hm.lt_code >= 0 && hm.rt_code < 0) {
            hm.combo_pedal_code = hm.lt_code;
        }

        if (bit_is_set(abs_bits, ABS_RUDDER)) hm.handbrake_code = ABS_RUDDER;
        if (has_throttle && hm.rt_code != ABS_THROTTLE && hm.lt_code != ABS_THROTTLE) {
            hm.clutch_code = ABS_THROTTLE;
        } else if (has_y && hm.lt_code >= 0 && hm.rt_code >= 0 &&
                   hm.lt_code != ABS_Y && hm.rt_code != ABS_Y && !has_rxry) {
            hm.clutch_code = ABS_Y;
        }
    } else {
        if (!has_x || !has_y) {
            hm.lx_code = -1;
            hm.ly_code = -1;
        }

        if (has_rxry) {
            hm.rx_code = ABS_RX;
            hm.ry_code = ABS_RY;
            if (has_z) hm.lt_code = ABS_Z;
            if (has_rz) hm.rt_code = ABS_RZ;
        } else if (has_z && has_rz) {
            hm.rx_code = ABS_Z;
            hm.ry_code = ABS_RZ;
        }

        if (hm.lt_code < 0 && has_brake) hm.lt_code = ABS_BRAKE;
        if (hm.rt_code < 0 && has_gas) hm.rt_code = ABS_GAS;
    }

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
    query_abs_bounds(src_fd, hm.combo_pedal_code, -32768, 32767, &hm.combo_min, &hm.combo_max);
    query_abs_bounds(src_fd, hm.clutch_code, 0, 1023, &hm.clutch_min, &hm.clutch_max);
    query_abs_bounds(src_fd, hm.handbrake_code, 0, 1023, &hm.handbrake_min, &hm.handbrake_max);

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
        case 300: return 316;
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

static int setup_uinput(int low_latency, int enable_ff, const char *virtual_name, const char *ev_name) {
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

    char vphys[96];
    snprintf(vphys, sizeof(vphys), "antidrift:%s", (ev_name && ev_name[0]) ? ev_name : "pad");
    ioctl(ufd, UI_SET_PHYS, vphys);

    const char *vname = (virtual_name && virtual_name[0]) ? virtual_name : DEFAULT_VIRTUAL_NAME;
    struct uinput_setup usetup;
    memset(&usetup, 0, sizeof(usetup));
    usetup.id.bustype = BUS_USB;
    usetup.id.vendor = 0x045e;
    usetup.id.product = 0x028e;
    usetup.id.version = 0x0110;
    usetup.ff_effects_max = enable_ff ? 16 : 0;
    snprintf(usetup.name, UINPUT_MAX_NAME_SIZE, "%s", vname);

    if (ioctl(ufd, UI_DEV_SETUP, &usetup) < 0 || ioctl(ufd, UI_DEV_CREATE) < 0) {
        perror("UI_DEV_CREATE");
        close(ufd);
        return -1;
    }
    return ufd;
}

typedef struct {
    int phys_btn[NUM_STD_KEYS];
    int aux_btn[NUM_STD_KEYS];
    int virt_btn[NUM_STD_KEYS];
    int64_t btn_last_ms[NUM_STD_KEYS];
    int phys_lt, phys_rt;
    int raw_combo_pedal;
    int raw_handbrake;
    int hb_active;
    int raw_clutch;
    int clutch_active;
    int cur_h_gear;
    int shift_queue;      /* >0 = pending upshifts, <0 = pending downshifts */
    int shift_phase;      /* 0=idle, 1=holding key, 2=release gap */
    int shift_held_code;
    int64_t shift_next_ms;
    int phys_hx, phys_hy;
    int dpad_u, dpad_d, dpad_l, dpad_r;
    int raw_lx, raw_ly, raw_rx, raw_ry;
    int override_l, override_lx, override_ly;
    int override_r, override_rx, override_ry;
    int ff_map[MAX_FF_EFFECTS];
} PadRuntime;

typedef struct {
    int active;
    char dev_path[MAX_PATH_LEN];
    char ev_name[64];
    char stable_id[192];
    int src_fd;
    int src_is_rw;
    int ufd;
    int aux_shifter_fd;
    char aux_shifter_path[MAX_PATH_LEN];
    int aux_shifter_grabbed;
    int aux_gear_codes[10];
    int aux_gear_count;
    int aux_hb_fd;
    char aux_hb_path[MAX_PATH_LEN];
    int aux_hb_axis;
    int aux_hb_min, aux_hb_max;
    int dirty_l;
    int dirty_r;
    Config cfg;
    HardwareMap hm;
    MacroTable macros;
    PadRuntime rt;
    struct input_event ev_batch[64];
    int ev_batch_len;
} PadInstance;

static void flush_ev(PadInstance *pad) {
    if (pad->ev_batch_len > 0 && pad->ufd >= 0) {
        ssize_t unused = write(
            pad->ufd, pad->ev_batch, (size_t)pad->ev_batch_len * sizeof(struct input_event));
        (void)unused;
        pad->ev_batch_len = 0;
    }
}

static void emit_ev(PadInstance *pad, uint16_t type, uint16_t code, int32_t value) {
    struct input_event *ev = &pad->ev_batch[pad->ev_batch_len++];
    memset(ev, 0, sizeof(*ev));
    ev->type = type;
    ev->code = code;
    ev->value = value;
    if ((type == EV_SYN && code == SYN_REPORT) || pad->ev_batch_len >= 64) {
        flush_ev(pad);
    }
}

static void handle_uinput_ff(PadInstance *pad) {
    struct input_event uev;
    while (read(pad->ufd, &uev, sizeof(uev)) == (ssize_t)sizeof(uev)) {
        if (uev.type == EV_UINPUT) {
            if (uev.code == UI_FF_UPLOAD) {
                struct uinput_ff_upload up;
                memset(&up, 0, sizeof(up));
                up.request_id = (uint32_t)uev.value;
                if (ioctl(pad->ufd, UI_BEGIN_FF_UPLOAD, &up) == 0) {
                    int uid = up.effect.id;
                    struct ff_effect eff = up.effect;
                    eff.id = (uid >= 0 && uid < MAX_FF_EFFECTS) ? pad->rt.ff_map[uid] : -1;
                    if (eff.type == FF_RUMBLE) {
                        double g = (double)pad->cfg.rumble_gain / 100.0;
                        eff.u.rumble.strong_magnitude = (uint16_t)llround(eff.u.rumble.strong_magnitude * g);
                        eff.u.rumble.weak_magnitude = (uint16_t)llround(eff.u.rumble.weak_magnitude * g);
                    }
                    if (ioctl(pad->src_fd, EVIOCSFF, &eff) == 0 && uid >= 0 && uid < MAX_FF_EFFECTS) {
                        pad->rt.ff_map[uid] = eff.id;
                    }
                    up.retval = 0;
                    ioctl(pad->ufd, UI_END_FF_UPLOAD, &up);
                }
            } else if (uev.code == UI_FF_ERASE) {
                struct uinput_ff_erase er;
                memset(&er, 0, sizeof(er));
                er.request_id = (uint32_t)uev.value;
                if (ioctl(pad->ufd, UI_BEGIN_FF_ERASE, &er) == 0) {
                    if (er.effect_id < MAX_FF_EFFECTS && pad->rt.ff_map[er.effect_id] >= 0) {
                        ioctl(pad->src_fd, EVIOCRMFF, pad->rt.ff_map[er.effect_id]);
                        pad->rt.ff_map[er.effect_id] = -1;
                    }
                    er.retval = 0;
                    ioctl(pad->ufd, UI_END_FF_ERASE, &er);
                }
            }
        } else if (uev.type == EV_FF) {
            if (uev.code < MAX_FF_EFFECTS && pad->rt.ff_map[uev.code] >= 0) {
                struct input_event play;
                memset(&play, 0, sizeof(play));
                play.type = EV_FF;
                play.code = (uint16_t)pad->rt.ff_map[uev.code];
                play.value = (pad->cfg.rumble_gain > 0) ? uev.value : 0;
                ssize_t unused = write(pad->src_fd, &play, sizeof(play));
                (void)unused;
            }
        }
    }
}

static int pad_is_wheel_active(const PadInstance *pad) {
    if (pad->cfg.wheel_mode == 1) return 1;
    if (pad->cfg.wheel_mode == 2) return 0;
    return pad->hm.is_wheel;
}

static int wheel_combined_active(const PadInstance *pad) {
    if (!pad_is_wheel_active(pad)) return 0;
    if (pad->cfg.wheel_combined_pedals) return 1;
    return (pad->hm.combo_pedal_code >= 0 && pad->hm.lt_code < 0 && pad->hm.rt_code < 0);
}

static void sync_std_button(PadInstance *pad, int code) {
    int idx = std_key_index(code);
    if (idx < 0 || pad->ufd < 0) return;
    int want = (pad->rt.phys_btn[idx] || pad->rt.aux_btn[idx]) ? 1 : 0;
    if (want != pad->rt.virt_btn[idx]) {
        pad->rt.virt_btn[idx] = want;
        emit_ev(pad, EV_KEY, (uint16_t)code, want);
    }
}

static void refresh_wheel_virtual_buttons(PadInstance *pad) {
    PadRuntime *rt = &pad->rt;
    const Config *cfg = &pad->cfg;
    int next_aux[NUM_STD_KEYS];
    memset(next_aux, 0, sizeof(next_aux));

    if (pad_is_wheel_active(pad)) {
        if (cfg->wheel_handbrake_enabled && rt->hb_active) {
            int idx = std_key_index(cfg->wheel_handbrake_target_code);
            if (idx >= 0) next_aux[idx] = 1;
        }
        if (cfg->wheel_clutch_mode == 1 && rt->clutch_active) {
            int idx = std_key_index(307); /* X */
            if (idx >= 0) next_aux[idx] = 1;
        } else if (cfg->wheel_clutch_mode == 2 && rt->clutch_active) {
            int idx = std_key_index(310); /* LB */
            if (idx >= 0) next_aux[idx] = 1;
        }
        if (rt->shift_phase == 1 && rt->shift_held_code > 0) {
            int idx = std_key_index(rt->shift_held_code);
            if (idx >= 0) next_aux[idx] = 1;
        }
    }

    int changed = 0;
    for (int i = 0; i < NUM_STD_KEYS; i++) {
        if (next_aux[i] != rt->aux_btn[i]) {
            rt->aux_btn[i] = next_aux[i];
            sync_std_button(pad, STD_KEYS[i]);
            changed = 1;
        }
    }
    if (changed) emit_ev(pad, EV_SYN, SYN_REPORT, 0);
}

static void emit_pedals(PadInstance *pad) {
    const Config *cfg = &pad->cfg;
    const PadRuntime *rt = &pad->rt;
    if (pad_is_wheel_active(pad)) {
        int raw_lt = rt->phys_lt;
        int raw_rt = rt->phys_rt;
        if (wheel_combined_active(pad)) {
            split_combined_pedals(rt->raw_combo_pedal, 0, &raw_lt, &raw_rt);
        }
        int out_lt = compute_wheel_pedal(
            raw_lt, cfg->wheel_dz_brake, cfg->wheel_max_brake,
            cfg->wheel_invert_brake, cfg->hair_trigger);
        int out_rt = compute_wheel_pedal(
            raw_rt, cfg->wheel_dz_gas, cfg->wheel_max_gas,
            cfg->wheel_invert_gas, cfg->hair_trigger);
        emit_ev(pad, EV_ABS, ABS_Z, out_lt);
        emit_ev(pad, EV_ABS, ABS_RZ, out_rt);
    } else {
        emit_ev(
            pad, EV_ABS, ABS_Z,
            compute_trigger_output(rt->phys_lt, cfg->lt_dz, cfg->lt_max, cfg->lt_curve, cfg->hair_trigger));
        emit_ev(
            pad, EV_ABS, ABS_RZ,
            compute_trigger_output(rt->phys_rt, cfg->rt_dz, cfg->rt_max, cfg->rt_curve, cfg->hair_trigger));
    }
}

static void emit_corrected_sticks(PadInstance *pad, int which) {
    const Config *cfg = &pad->cfg;
    const PadRuntime *rt = &pad->rt;
    int is_wheel = pad_is_wheel_active(pad);
    if (which == 0 || which == 2) {
        if (rt->override_l) {
            emit_ev(pad, EV_ABS, ABS_X, rt->override_lx);
            emit_ev(pad, EV_ABS, ABS_Y, rt->override_ly);
        } else if (is_wheel) {
            int ox = compute_wheel_steering(
                rt->raw_lx, cfg->center_lx, cfg->wheel_steer_dz,
                cfg->wheel_steer_sat, cfg->wheel_linearity);
            emit_ev(pad, EV_ABS, ABS_X, ox);
            emit_ev(pad, EV_ABS, ABS_Y, 0);
        } else {
            int ox, oy;
            apply_stick_transform(
                normalize_axis(rt->raw_lx, cfg->center_lx),
                normalize_axis(rt->raw_ly, cfg->center_ly),
                cfg->dz_l, cfg->dz_mode, cfg->outer_comp_l,
                cfg->response_curve, &ox, &oy);
            emit_ev(pad, EV_ABS, ABS_X, ox);
            emit_ev(pad, EV_ABS, ABS_Y, oy);
        }
    }
    if (which == 1 || which == 2) {
        if (rt->override_r) {
            emit_ev(pad, EV_ABS, ABS_RX, rt->override_rx);
            emit_ev(pad, EV_ABS, ABS_RY, rt->override_ry);
        } else if (is_wheel) {
            emit_ev(pad, EV_ABS, ABS_RX, 0);
            emit_ev(pad, EV_ABS, ABS_RY, 0);
        } else {
            int ox, oy;
            apply_stick_transform(
                normalize_axis(rt->raw_rx, cfg->center_rx),
                normalize_axis(rt->raw_ry, cfg->center_ry),
                cfg->dz_r, cfg->dz_mode, cfg->outer_comp_r,
                cfg->response_curve, &ox, &oy);
            emit_ev(pad, EV_ABS, ABS_RX, ox);
            emit_ev(pad, EV_ABS, ABS_RY, oy);
        }
    }
}

static void release_macro_held(PadInstance *pad, MacroDef *m) {
    PadRuntime *rt = &pad->rt;
    int changed = 0;
    for (int i = 0; i < NUM_STD_KEYS; i++) {
        if (m->held_btns & (1U << i)) {
            emit_ev(pad, EV_KEY, (uint16_t)STD_KEYS[i], rt->phys_btn[i] ? 1 : 0);
            changed = 1;
        }
    }
    m->held_btns = 0;

    if (m->held_lt) {
        emit_ev(pad, EV_ABS, ABS_Z, rt->phys_lt);
        m->held_lt = 0;
        changed = 1;
    }
    if (m->held_rt) {
        emit_ev(pad, EV_ABS, ABS_RZ, rt->phys_rt);
        m->held_rt = 0;
        changed = 1;
    }
    if (m->held_hat) {
        emit_ev(pad, EV_ABS, ABS_HAT0X, rt->phys_hx);
        emit_ev(pad, EV_ABS, ABS_HAT0Y, rt->phys_hy);
        m->held_hat = 0;
        changed = 1;
    }
    if (m->held_stick_l) {
        rt->override_l = 0;
        emit_corrected_sticks(pad, 0);
        m->held_stick_l = 0;
        changed = 1;
    }
    if (m->held_stick_r) {
        rt->override_r = 0;
        emit_corrected_sticks(pad, 1);
        m->held_stick_r = 0;
        changed = 1;
    }
    if (changed) emit_ev(pad, EV_SYN, SYN_REPORT, 0);
}

static void release_all_macros(PadInstance *pad) {
    for (int i = 0; i < pad->macros.count; i++) {
        pad->macros.items[i].active = 0;
        pad->macros.items[i].toggle_on = 0;
        release_macro_held(pad, &pad->macros.items[i]);
    }
}

static int step_macros(PadInstance *pad) {
    int64_t now = monotonic_ms();
    int min_wait = 250;
    PadRuntime *rt = &pad->rt;
    MacroTable *mt = &pad->macros;

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
                    release_macro_held(pad, m);
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
                emit_ev(pad, EV_KEY, (uint16_t)st->code, st->v1);
                dirty_syn = 1;
            } else if (st->kind == STEP_HAT) {
                m->held_hat = 1;
                emit_ev(pad, EV_ABS, ABS_HAT0X, st->v1);
                emit_ev(pad, EV_ABS, ABS_HAT0Y, st->v2);
                dirty_syn = 1;
            } else if (st->kind == STEP_TRIG) {
                if (st->code == ABS_Z) m->held_lt = 1;
                else m->held_rt = 1;
                emit_ev(pad, EV_ABS, (uint16_t)st->code, st->v1);
                dirty_syn = 1;
            } else if (st->kind == STEP_STICK) {
                if (st->code == 0) {
                    m->held_stick_l = 1;
                    rt->override_l = 1;
                    rt->override_lx = st->v1;
                    rt->override_ly = st->v2;
                    emit_corrected_sticks(pad, 0);
                } else {
                    m->held_stick_r = 1;
                    rt->override_r = 1;
                    rt->override_rx = st->v1;
                    rt->override_ry = st->v2;
                    emit_corrected_sticks(pad, 1);
                }
                dirty_syn = 1;
            } else if (st->kind == STEP_STICK_REL) {
                if (st->code == 0) {
                    m->held_stick_l = 0;
                    rt->override_l = 0;
                    emit_corrected_sticks(pad, 0);
                } else {
                    m->held_stick_r = 0;
                    rt->override_r = 0;
                    emit_corrected_sticks(pad, 1);
                }
                dirty_syn = 1;
            } else if (st->kind == STEP_WAIT) {
                m->next_ms = now + st->v1;
                break;
            }

            if (dirty_syn) emit_ev(pad, EV_SYN, SYN_REPORT, 0);
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

static void sync_initial_pad_state(PadInstance *pad) {
    struct input_absinfo info;
    HardwareMap *hm = &pad->hm;
    PadRuntime *rt = &pad->rt;
    if (hm->lx_code >= 0 && ioctl(pad->src_fd, EVIOCGABS(hm->lx_code), &info) == 0) {
        rt->raw_lx = scale_hw_to_i16(info.value, hm->lx_min, hm->lx_max);
    }
    if (hm->ly_code >= 0 && ioctl(pad->src_fd, EVIOCGABS(hm->ly_code), &info) == 0) {
        rt->raw_ly = scale_hw_to_i16(info.value, hm->ly_min, hm->ly_max);
    }
    if (hm->rx_code >= 0 && ioctl(pad->src_fd, EVIOCGABS(hm->rx_code), &info) == 0) {
        rt->raw_rx = scale_hw_to_i16(info.value, hm->rx_min, hm->rx_max);
    }
    if (hm->ry_code >= 0 && ioctl(pad->src_fd, EVIOCGABS(hm->ry_code), &info) == 0) {
        rt->raw_ry = scale_hw_to_i16(info.value, hm->ry_min, hm->ry_max);
    }
    if (hm->lt_code >= 0 && ioctl(pad->src_fd, EVIOCGABS(hm->lt_code), &info) == 0) {
        rt->phys_lt = scale_trig_to_1023(info.value, hm->lt_min, hm->lt_max);
    }
    if (hm->rt_code >= 0 && ioctl(pad->src_fd, EVIOCGABS(hm->rt_code), &info) == 0) {
        rt->phys_rt = scale_trig_to_1023(info.value, hm->rt_min, hm->rt_max);
    }
    if (hm->combo_pedal_code >= 0 && ioctl(pad->src_fd, EVIOCGABS(hm->combo_pedal_code), &info) == 0) {
        rt->raw_combo_pedal = scale_hw_to_i16(info.value, hm->combo_min, hm->combo_max);
    }
    if (ioctl(pad->src_fd, EVIOCGABS(ABS_HAT0X), &info) == 0) rt->phys_hx = info.value;
    if (ioctl(pad->src_fd, EVIOCGABS(ABS_HAT0Y), &info) == 0) rt->phys_hy = info.value;

    emit_corrected_sticks(pad, 2);
    emit_pedals(pad);
    emit_ev(pad, EV_SYN, SYN_REPORT, 0);
}

static int pad_already_attached(const PadInstance *pads, const char *dev_path) {
    for (int i = 0; i < MAX_PADS; i++) {
        if (pads[i].active && strcmp(pads[i].dev_path, dev_path) == 0) return 1;
    }
    return 0;
}

static void close_wheel_aux(PadInstance *pad) {
    if (pad->aux_shifter_fd >= 0) {
        if (pad->aux_shifter_grabbed) ioctl(pad->aux_shifter_fd, EVIOCGRAB, (void *)0);
        close(pad->aux_shifter_fd);
        pad->aux_shifter_fd = -1;
        pad->aux_shifter_grabbed = 0;
        pad->aux_shifter_path[0] = '\0';
        pad->aux_gear_count = 0;
    }
    if (pad->aux_hb_fd >= 0) {
        ioctl(pad->aux_hb_fd, EVIOCGRAB, (void *)0);
        close(pad->aux_hb_fd);
        pad->aux_hb_fd = -1;
        pad->aux_hb_axis = -1;
        pad->aux_hb_path[0] = '\0';
    }
    pad->rt.hb_active = 0;
    pad->rt.clutch_active = 0;
    pad->rt.shift_queue = 0;
    pad->rt.shift_phase = 0;
    pad->rt.shift_held_code = 0;
    refresh_wheel_virtual_buttons(pad);
}

static int find_aux_sim_device(
    const char *main_dev_path, const char *pref_path, int want_type,
    char *out_path, size_t out_sz) {
    out_path[0] = '\0';
    if (strcmp(pref_path, "none") == 0 || strcmp(pref_path, "same") == 0) return 0;

    if (pref_path[0] && strcmp(pref_path, "auto") != 0 && strcmp(pref_path, main_dev_path) != 0) {
        snprintf(out_path, out_sz, "%s", pref_path);
        return 1;
    }

    DIR *dir = opendir("/sys/class/input");
    if (!dir) return 0;

    int best_score = 0;
    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (strncmp(ent->d_name, "event", 5) != 0 || strlen(ent->d_name) >= 48) continue;
        if (is_virtual_event(ent->d_name)) continue;

        char dev_node[MAX_PATH_LEN];
        snprintf(dev_node, sizeof(dev_node), "/dev/input/%.48s", ent->d_name);
        if (strcmp(dev_node, main_dev_path) == 0) continue;

        char name_path[300], dev_name[160] = {0};
        snprintf(name_path, sizeof(name_path), "/sys/class/input/%s/device/name", ent->d_name);
        read_sys_line(name_path, dev_name, sizeof(dev_name));
        if (!dev_name[0] || strstr(dev_name, "Mouse") || strstr(dev_name, "Touchpad") ||
            strstr(dev_name, "Keyboard") || strstr(dev_name, "Motion") ||
            strstr(dev_name, "Power Button") || strstr(dev_name, "Video Bus")) {
            continue;
        }

        int fd = open(dev_node, O_RDONLY | O_NONBLOCK);
        if (fd < 0) continue;
        unsigned char key_bits[96] = {0};
        unsigned char abs_bits[8] = {0};
        int ok_io = (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits) >= 0 &&
                     ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(abs_bits)), abs_bits) >= 0);
        close(fd);
        if (!ok_io) continue;
        if (bit_is_set(key_bits, BTN_LEFT) ||
            (bit_is_set(key_bits, KEY_A) && bit_is_set(key_bits, KEY_ENTER))) {
            continue;
        }

        int abs_cnt = 0;
        for (int a = 0; a <= ABS_MISC; a++) {
            if (bit_is_set(abs_bits, a)) abs_cnt++;
        }
        int joy_btn_cnt = 0;
        for (int b = BTN_TRIGGER; b <= BTN_GEAR_UP; b++) {
            if (bit_is_set(key_bits, b)) joy_btn_cnt++;
        }
        for (int b = BTN_TRIGGER_HAPPY1; b <= BTN_TRIGGER_HAPPY20; b++) {
            if (bit_is_set(key_bits, b)) joy_btn_cnt++;
        }

        int sc = 0;
        if (want_type == 1) {
            int hb_name = (strcasestr(dev_name, "handbrake") != NULL ||
                           strcasestr(dev_name, "freno") != NULL ||
                           strcasestr(dev_name, "tss") != NULL ||
                           strcasestr(dev_name, "heusinkveld") != NULL);
            int sim_mcu = (strcasestr(dev_name, "arduino") != NULL ||
                           strcasestr(dev_name, "leonardo") != NULL ||
                           strcasestr(dev_name, "pro micro") != NULL ||
                           strcasestr(dev_name, "stm32") != NULL ||
                           strcasestr(dev_name, "esp32") != NULL ||
                           strcasestr(dev_name, "sim") != NULL);
            if (abs_cnt >= 1 && abs_cnt <= 3 && joy_btn_cnt <= 4 && (hb_name || sim_mcu)) {
                sc = hb_name ? 100 : 40;
            }
        } else if (want_type == 2) {
            int sh_name = (strcasestr(dev_name, "shifter") != NULL ||
                           strcasestr(dev_name, "palanca") != NULL ||
                           strcasestr(dev_name, "cambio") != NULL ||
                           strcasestr(dev_name, "th8") != NULL ||
                           strcasestr(dev_name, "shh") != NULL ||
                           strcasestr(dev_name, "gear") != NULL);
            int has_sticks = bit_is_set(abs_bits, ABS_X) && bit_is_set(abs_bits, ABS_Y) &&
                             bit_is_set(abs_bits, ABS_RX) && bit_is_set(abs_bits, ABS_RY);
            if (!has_sticks && joy_btn_cnt >= 2 && (sh_name || (abs_cnt == 0 && joy_btn_cnt <= 20))) {
                sc = sh_name ? 100 : 35;
            }
        }
        if (sc > best_score) {
            best_score = sc;
            snprintf(out_path, out_sz, "%s", dev_node);
        }
    }
    closedir(dir);
    return best_score > 0;
}

static void open_wheel_aux(PadInstance *pad) {
    close_wheel_aux(pad);
    if (!pad_is_wheel_active(pad)) return;

    const Config *cfg = &pad->cfg;
    if (cfg->wheel_shifter_mode == 2 || cfg->wheel_shifter_mode == 3) {
        char sh_path[MAX_PATH_LEN] = {0};
        if (find_aux_sim_device(pad->dev_path, cfg->wheel_shifter_device, 2, sh_path, sizeof(sh_path))) {
            int fd = open(sh_path, O_RDONLY | O_NONBLOCK);
            if (fd >= 0) {
                if (ioctl(fd, EVIOCGRAB, (void *)1) == 0) {
                    pad->aux_shifter_fd = fd;
                    pad->aux_shifter_grabbed = 1;
                    snprintf(pad->aux_shifter_path, sizeof(pad->aux_shifter_path), "%s", sh_path);

                    unsigned char kbits[96] = {0};
                    ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(kbits)), kbits);
                    pad->aux_gear_count = 0;
                    for (int b = BTN_TRIGGER; b <= BTN_GEAR_UP && pad->aux_gear_count < 10; b++) {
                        if (bit_is_set(kbits, b)) pad->aux_gear_codes[pad->aux_gear_count++] = b;
                    }
                    for (int b = BTN_TRIGGER_HAPPY1; b <= BTN_TRIGGER_HAPPY20 && pad->aux_gear_count < 10; b++) {
                        if (bit_is_set(kbits, b)) pad->aux_gear_codes[pad->aux_gear_count++] = b;
                    }
                } else {
                    close(fd);
                }
            }
        }
    }

    if (cfg->wheel_handbrake_enabled &&
        strcmp(cfg->wheel_handbrake_device, "none") != 0) {
        char hb_path[MAX_PATH_LEN] = {0};
        if (find_aux_sim_device(pad->dev_path, cfg->wheel_handbrake_device, 1, hb_path, sizeof(hb_path))) {
            int fd = open(hb_path, O_RDONLY | O_NONBLOCK);
            if (fd >= 0) {
                if (ioctl(fd, EVIOCGRAB, (void *)1) == 0) {
                    pad->aux_hb_fd = fd;
                    snprintf(pad->aux_hb_path, sizeof(pad->aux_hb_path), "%s", hb_path);
                    unsigned char abits[8] = {0};
                    ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(abits)), abits);
                    static const int hb_prefs[] = {ABS_X, ABS_Z, ABS_RZ, ABS_THROTTLE, ABS_RUDDER, ABS_BRAKE, ABS_RX, ABS_RY, ABS_Y};
                    pad->aux_hb_axis = -1;
                    for (size_t i = 0; i < sizeof(hb_prefs) / sizeof(hb_prefs[0]); i++) {
                        if (bit_is_set(abits, hb_prefs[i])) {
                            pad->aux_hb_axis = hb_prefs[i];
                            break;
                        }
                    }
                    query_abs_bounds(fd, pad->aux_hb_axis, 0, 1023, &pad->aux_hb_min, &pad->aux_hb_max);
                } else {
                    close(fd);
                }
            }
        }
    }
}

static int step_wheel_aux(PadInstance *pad) {
    PadRuntime *rt = &pad->rt;
    if (!pad_is_wheel_active(pad)) return 450;
    if (rt->shift_phase == 0 && rt->shift_queue == 0) return 450;

    int64_t now = monotonic_ms();
    if (rt->shift_phase == 0 && rt->shift_queue != 0) {
        if (rt->shift_queue > 0) {
            rt->shift_held_code = pad->cfg.wheel_shifter_up_code;
            rt->shift_queue--;
        } else {
            rt->shift_held_code = pad->cfg.wheel_shifter_down_code;
            rt->shift_queue++;
        }
        rt->shift_phase = 1;
        rt->shift_next_ms = now + 48;
        refresh_wheel_virtual_buttons(pad);
        return 48;
    }

    int64_t rem = rt->shift_next_ms - now;
    if (rem > 0) return (int)rem;

    if (rt->shift_phase == 1) {
        rt->shift_phase = 2;
        rt->shift_held_code = 0;
        rt->shift_next_ms = now + 35;
        refresh_wheel_virtual_buttons(pad);
        return 35;
    }

    rt->shift_phase = 0;
    if (rt->shift_queue != 0) return step_wheel_aux(pad);
    return 450;
}

static void detach_pad(PadInstance *pad) {
    if (!pad->active) return;
    close_wheel_aux(pad);
    if (pad->ufd >= 0) {
        release_all_macros(pad);
        flush_ev(pad);
        ioctl(pad->ufd, UI_DEV_DESTROY);
        close(pad->ufd);
        pad->ufd = -1;
    }
    if (pad->src_fd >= 0) {
        ioctl(pad->src_fd, EVIOCGRAB, (void *)0);
        close(pad->src_fd);
        pad->src_fd = -1;
    }
    pad->active = 0;
}

static int attach_pad(PadInstance *pads, const char *ev_name, const char *dev_path, const Config *global_cfg) {
    if (pad_already_attached(pads, dev_path)) return 0;

    int slot = -1;
    for (int i = 0; i < MAX_PADS; i++) {
        if (!pads[i].active) {
            slot = i;
            break;
        }
    }
    if (slot < 0) return -1;

    int src_is_rw = 1;
    int src_fd = open(dev_path, O_RDWR | O_NONBLOCK);
    if (src_fd < 0) {
        src_is_rw = 0;
        src_fd = open(dev_path, O_RDONLY | O_NONBLOCK);
    }
    if (src_fd < 0) return -1;

    if (ioctl(src_fd, EVIOCGRAB, (void *)1) < 0) {
        close(src_fd);
        return -1;
    }

    char stable_id[192];
    compute_device_stable_id(ev_name, stable_id, sizeof(stable_id));
    Config pad_cfg = load_device_config(global_cfg, stable_id);
    HardwareMap hm = probe_hardware_map(src_fd, src_is_rw);
    int ufd = setup_uinput(pad_cfg.low_latency, hm.has_ff_rumble, pad_cfg.virtual_name, ev_name);
    if (ufd < 0) {
        ioctl(src_fd, EVIOCGRAB, (void *)0);
        close(src_fd);
        return -1;
    }

    PadInstance *pad = &pads[slot];
    memset(pad, 0, sizeof(*pad));
    pad->active = 1;
    snprintf(pad->dev_path, sizeof(pad->dev_path), "%s", dev_path);
    snprintf(pad->ev_name, sizeof(pad->ev_name), "%s", ev_name);
    snprintf(pad->stable_id, sizeof(pad->stable_id), "%s", stable_id);
    pad->src_fd = src_fd;
    pad->src_is_rw = src_is_rw;
    pad->ufd = ufd;
    pad->aux_shifter_fd = -1;
    pad->aux_hb_fd = -1;
    pad->aux_hb_axis = -1;
    pad->cfg = pad_cfg;
    pad->hm = hm;
    pad->macros = load_macros();
    for (int i = 0; i < MAX_FF_EFFECTS; i++) pad->rt.ff_map[i] = -1;
    pad->rt.raw_lx = pad_cfg.center_lx;
    pad->rt.raw_ly = pad_cfg.center_ly;
    pad->rt.raw_rx = pad_cfg.center_rx;
    pad->rt.raw_ry = pad_cfg.center_ry;

    open_wheel_aux(pad);
    sync_initial_pad_state(pad);
    return 0;
}

static void refresh_low_latency(const PadInstance *pads, const Config *global_cfg) {
    int want_ll = global_cfg->low_latency;
    for (int i = 0; i < MAX_PADS; i++) {
        if (pads[i].active && pads[i].cfg.low_latency) want_ll = 1;
    }
    apply_low_latency_os(want_ll);
}

static void scan_and_sync_pads(PadInstance *pads, const Config *global_cfg) {
    GamepadCandidate cands[16];
    int count = list_gamepad_candidates(cands, 16);

    if (!global_cfg->multi_controller) {
        const char *chosen_ev = NULL;
        const char *chosen_dev = NULL;
        if (global_cfg->device_path[0] && strcmp(global_cfg->device_path, "auto") != 0) {
            for (int i = 0; i < count; i++) {
                if (strcmp(cands[i].dev_node, global_cfg->device_path) == 0) {
                    chosen_ev = cands[i].ev_name;
                    chosen_dev = cands[i].dev_node;
                    break;
                }
            }
        }
        if (!chosen_dev && count > 0) {
            chosen_ev = cands[0].ev_name;
            chosen_dev = cands[0].dev_node;
        }
        for (int i = 0; i < MAX_PADS; i++) {
            if (pads[i].active && (!chosen_dev || strcmp(pads[i].dev_path, chosen_dev) != 0)) {
                detach_pad(&pads[i]);
            }
        }
        if (chosen_dev && chosen_ev) {
            attach_pad(pads, chosen_ev, chosen_dev, global_cfg);
        }
    } else {
        for (int i = 0; i < count; i++) {
            attach_pad(pads, cands[i].ev_name, cands[i].dev_node, global_cfg);
        }
    }

    refresh_low_latency(pads, global_cfg);
}

static void reload_all_pads(PadInstance *pads, Config *global_cfg) {
    *global_cfg = load_global_config();

    for (int i = 0; i < MAX_PADS; i++) {
        PadInstance *pad = &pads[i];
        if (!pad->active) continue;

        if (!global_cfg->multi_controller &&
            global_cfg->device_path[0] &&
            strcmp(global_cfg->device_path, "auto") != 0 &&
            strcmp(pad->dev_path, global_cfg->device_path) != 0) {
            detach_pad(pad);
            continue;
        }

        release_all_macros(pad);
        Config new_cfg = load_device_config(global_cfg, pad->stable_id);
        int recreate_uinput = (
            strcmp(new_cfg.virtual_name, pad->cfg.virtual_name) != 0 ||
            new_cfg.low_latency != pad->cfg.low_latency
        );

        pad->cfg = new_cfg;
        pad->macros = load_macros();

        if (recreate_uinput) {
            flush_ev(pad);
            ioctl(pad->ufd, UI_DEV_DESTROY);
            close(pad->ufd);
            for (int f = 0; f < MAX_FF_EFFECTS; f++) pad->rt.ff_map[f] = -1;
            pad->ufd = setup_uinput(
                pad->cfg.low_latency, pad->hm.has_ff_rumble, pad->cfg.virtual_name, pad->ev_name);
            if (pad->ufd < 0) {
                detach_pad(pad);
                continue;
            }
        }

        emit_corrected_sticks(pad, 2);
        emit_pedals(pad);
        open_wheel_aux(pad);
        refresh_wheel_virtual_buttons(pad);
        emit_ev(pad, EV_SYN, SYN_REPORT, 0);
    }

    scan_and_sync_pads(pads, global_cfg);
}

static int process_pad_events(PadInstance *pad) {
    HardwareMap *hm = &pad->hm;
    PadRuntime *rt = &pad->rt;
    const Config *cfg = &pad->cfg;
    MacroTable *macros = &pad->macros;

    struct input_event ev_buf[64];
    int io_err = 0;

    while (running) {
        ssize_t n = read(pad->src_fd, ev_buf, sizeof(ev_buf));
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
        int64_t ev_now_ms = (cfg->debounce_ms > 0) ? monotonic_ms() : 0;

        for (int ei = 0; ei < count; ei++) {
            struct input_event ev = ev_buf[ei];
            if (ev.type == EV_ABS) {
                if (hm->combo_pedal_code >= 0 && ev.code == hm->combo_pedal_code) {
                    rt->raw_combo_pedal = scale_hw_to_i16(ev.value, hm->combo_min, hm->combo_max);
                    if (wheel_combined_active(pad)) {
                        emit_pedals(pad);
                    }
                }
                if (ev.code == hm->lx_code) {
                    rt->raw_lx = scale_hw_to_i16(ev.value, hm->lx_min, hm->lx_max);
                    pad->dirty_l = 1;
                } else if (ev.code == hm->ly_code) {
                    rt->raw_ly = scale_hw_to_i16(ev.value, hm->ly_min, hm->ly_max);
                    pad->dirty_l = 1;
                } else if (ev.code == hm->rx_code) {
                    rt->raw_rx = scale_hw_to_i16(ev.value, hm->rx_min, hm->rx_max);
                    pad->dirty_r = 1;
                } else if (ev.code == hm->ry_code) {
                    rt->raw_ry = scale_hw_to_i16(ev.value, hm->ry_min, hm->ry_max);
                    pad->dirty_r = 1;
                } else if (ev.code == hm->lt_code) {
                    rt->phys_lt = scale_trig_to_1023(ev.value, hm->lt_min, hm->lt_max);
                    if (!wheel_combined_active(pad)) emit_pedals(pad);
                } else if (ev.code == hm->rt_code) {
                    rt->phys_rt = scale_trig_to_1023(ev.value, hm->rt_min, hm->rt_max);
                    if (!wheel_combined_active(pad)) emit_pedals(pad);
                } else if (hm->handbrake_code >= 0 && ev.code == hm->handbrake_code) {
                    int hb = scale_trig_to_1023(ev.value, hm->handbrake_min, hm->handbrake_max);
                    if (cfg->wheel_handbrake_invert) hb = 1023 - hb;
                    rt->raw_handbrake = hb;
                    double frac = (double)hb / 1023.0;
                    int next_hb = rt->hb_active ? (frac >= cfg->wheel_handbrake_dz - 0.03) : (frac >= cfg->wheel_handbrake_dz);
                    if (next_hb != rt->hb_active) {
                        rt->hb_active = next_hb;
                        refresh_wheel_virtual_buttons(pad);
                    }
                } else if (hm->clutch_code >= 0 && ev.code == hm->clutch_code) {
                    int cl = scale_trig_to_1023(ev.value, hm->clutch_min, hm->clutch_max);
                    rt->raw_clutch = cl;
                    int next_cl = (cl >= 260);
                    if (next_cl != rt->clutch_active) {
                        rt->clutch_active = next_cl;
                        refresh_wheel_virtual_buttons(pad);
                    }
                } else if (ev.code == ABS_HAT0X) {
                    rt->phys_hx = (ev.value < 0) ? -1 : ((ev.value > 0) ? 1 : 0);
                    emit_ev(pad, EV_ABS, ABS_HAT0X, rt->phys_hx);
                } else if (ev.code == ABS_HAT0Y) {
                    rt->phys_hy = (ev.value < 0) ? -1 : ((ev.value > 0) ? 1 : 0);
                    emit_ev(pad, EV_ABS, ABS_HAT0Y, rt->phys_hy);
                }
            } else if (ev.type == EV_SYN && ev.code == SYN_REPORT) {
                if (pad->dirty_l) {
                    emit_corrected_sticks(pad, 0);
                    pad->dirty_l = 0;
                }
                if (pad->dirty_r) {
                    emit_corrected_sticks(pad, 1);
                    pad->dirty_r = 0;
                }
                emit_ev(pad, EV_SYN, SYN_REPORT, 0);
            } else if (ev.type == EV_KEY) {
                int intercepted = 0;
                for (int i = 0; i < macros->count; i++) {
                    MacroDef *m = &macros->items[i];
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
                                release_macro_held(pad, m);
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
                            release_macro_held(pad, m);
                        }
                    }
                }

                if (intercepted) continue;

                if (ev.code == hm->dig_lt_code) {
                    rt->phys_lt = ev.value ? 1023 : 0;
                    emit_pedals(pad);
                    emit_ev(pad, EV_SYN, SYN_REPORT, 0);
                    continue;
                }
                if (ev.code == hm->dig_rt_code) {
                    rt->phys_rt = ev.value ? 1023 : 0;
                    emit_pedals(pad);
                    emit_ev(pad, EV_SYN, SYN_REPORT, 0);
                    continue;
                }

                if (hm->use_dpad_btns &&
                    (ev.code == BTN_DPAD_UP || ev.code == BTN_DPAD_DOWN ||
                     ev.code == BTN_DPAD_LEFT || ev.code == BTN_DPAD_RIGHT)) {
                    int v = ev.value ? 1 : 0;
                    if (ev.code == BTN_DPAD_UP) rt->dpad_u = v;
                    else if (ev.code == BTN_DPAD_DOWN) rt->dpad_d = v;
                    else if (ev.code == BTN_DPAD_LEFT) rt->dpad_l = v;
                    else if (ev.code == BTN_DPAD_RIGHT) rt->dpad_r = v;
                    rt->phys_hx = rt->dpad_r - rt->dpad_l;
                    rt->phys_hy = rt->dpad_d - rt->dpad_u;
                    emit_ev(pad, EV_ABS, ABS_HAT0X, rt->phys_hx);
                    emit_ev(pad, EV_ABS, ABS_HAT0Y, rt->phys_hy);
                    emit_ev(pad, EV_SYN, SYN_REPORT, 0);
                    continue;
                }

                int target_code = ev.code;
                if (ev.code == BTN_GEAR_DOWN) {
                    target_code = (pad_is_wheel_active(pad) && cfg->wheel_shifter_mode != 0)
                        ? cfg->wheel_shifter_down_code : 310;
                } else if (ev.code == BTN_GEAR_UP) {
                    target_code = (pad_is_wheel_active(pad) && cfg->wheel_shifter_mode != 0)
                        ? cfg->wheel_shifter_up_code : 311;
                } else if (hm->use_generic_joy_btns) {
                    target_code = translate_generic_button(ev.code);
                }
                target_code = apply_nintendo_swap(target_code, cfg->swap_nintendo);
                int idx = std_key_index(target_code);
                if (idx >= 0) {
                    int state_val = ev.value ? 1 : 0;
                    if (ev.value != 2 && cfg->debounce_ms > 0) {
                        if (state_val == rt->phys_btn[idx]) continue;
                        if ((ev_now_ms - rt->btn_last_ms[idx]) < (int64_t)cfg->debounce_ms) {
                            continue;
                        }
                        rt->btn_last_ms[idx] = ev_now_ms;
                    }
                    rt->phys_btn[idx] = state_val;
                    sync_std_button(pad, target_code);
                }
            }
        }
    }
    return io_err ? -1 : 0;
}

static int process_aux_handbrake_events(PadInstance *pad) {
    struct input_event ev_buf[32];
    const Config *cfg = &pad->cfg;
    PadRuntime *rt = &pad->rt;
    while (running && pad->aux_hb_fd >= 0) {
        ssize_t n = read(pad->aux_hb_fd, ev_buf, sizeof(ev_buf));
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) break;
            if (errno == EINTR) continue;
            return -1;
        }
        if (n == 0) return -1;
        int count = (int)(n / (ssize_t)sizeof(struct input_event));
        for (int i = 0; i < count; i++) {
            struct input_event ev = ev_buf[i];
            if (ev.type == EV_ABS && ev.code == pad->aux_hb_axis) {
                int hb = scale_trig_to_1023(ev.value, pad->aux_hb_min, pad->aux_hb_max);
                if (cfg->wheel_handbrake_invert) hb = 1023 - hb;
                rt->raw_handbrake = hb;
                double frac = (double)hb / 1023.0;
                int next_hb = rt->hb_active ? (frac >= cfg->wheel_handbrake_dz - 0.03) : (frac >= cfg->wheel_handbrake_dz);
                if (next_hb != rt->hb_active) {
                    rt->hb_active = next_hb;
                    refresh_wheel_virtual_buttons(pad);
                }
            } else if (ev.type == EV_KEY) {
                int next_hb = ev.value ? 1 : 0;
                if (next_hb != rt->hb_active) {
                    rt->hb_active = next_hb;
                    refresh_wheel_virtual_buttons(pad);
                }
            }
        }
    }
    return 0;
}

static int process_aux_shifter_events(PadInstance *pad) {
    struct input_event ev_buf[32];
    const Config *cfg = &pad->cfg;
    PadRuntime *rt = &pad->rt;
    while (running && pad->aux_shifter_fd >= 0) {
        ssize_t n = read(pad->aux_shifter_fd, ev_buf, sizeof(ev_buf));
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) break;
            if (errno == EINTR) continue;
            return -1;
        }
        if (n == 0) return -1;
        int count = (int)(n / (ssize_t)sizeof(struct input_event));
        for (int i = 0; i < count; i++) {
            struct input_event ev = ev_buf[i];
            if (ev.type != EV_KEY || ev.value == 2) continue;

            if (cfg->wheel_shifter_mode == 2) {
                int idx_code = -1;
                if (ev.code == BTN_GEAR_DOWN || (pad->aux_gear_count >= 2 && ev.code == pad->aux_gear_codes[0])) {
                    idx_code = cfg->wheel_shifter_down_code;
                } else if (ev.code == BTN_GEAR_UP || (pad->aux_gear_count >= 2 && ev.code == pad->aux_gear_codes[1])) {
                    idx_code = cfg->wheel_shifter_up_code;
                }
                int idx = std_key_index(idx_code);
                if (idx >= 0) {
                    rt->aux_btn[idx] = ev.value ? 1 : 0;
                    sync_std_button(pad, idx_code);
                    emit_ev(pad, EV_SYN, SYN_REPORT, 0);
                }
            } else if (cfg->wheel_shifter_mode == 3 && ev.value == 1) {
                int new_gear = -1;
                for (int g = 0; g < pad->aux_gear_count; g++) {
                    if (pad->aux_gear_codes[g] == ev.code) {
                        new_gear = g + 1;
                        break;
                    }
                }
                if (new_gear > 0 && new_gear != rt->cur_h_gear) {
                    if (rt->cur_h_gear > 0) {
                        rt->shift_queue += (new_gear - rt->cur_h_gear);
                    } else {
                        rt->shift_queue += 1;
                    }
                    rt->cur_h_gear = new_gear;
                }
            }
        }
    }
    return 0;
}

int main(void) {
    signal(SIGINT, handle_sig);
    signal(SIGTERM, handle_sig);
    signal(SIGHUP, handle_hup);

    PadInstance pads[MAX_PADS];
    memset(pads, 0, sizeof(pads));
    for (int i = 0; i < MAX_PADS; i++) {
        pads[i].src_fd = -1;
        pads[i].ufd = -1;
        pads[i].aux_shifter_fd = -1;
        pads[i].aux_hb_fd = -1;
        pads[i].aux_hb_axis = -1;
    }

    Config global_cfg = load_global_config();
    int64_t last_rescan = 0;
    scan_and_sync_pads(pads, &global_cfg);
    last_rescan = monotonic_ms();

    while (running) {
        if (reload_cfg) {
            reload_cfg = 0;
            reload_all_pads(pads, &global_cfg);
            last_rescan = monotonic_ms();
        }

        int64_t now = monotonic_ms();
        if (now - last_rescan >= 1200) {
            last_rescan = now;
            scan_and_sync_pads(pads, &global_cfg);
        }

        struct pollfd pfds[MAX_PADS * 4];
        int pfd_pad[MAX_PADS * 4];
        int pfd_kind[MAX_PADS * 4]; /* 0=src, 1=ufd, 2=aux_shifter, 3=aux_hb */
        nfds_t nfds = 0;
        int min_wait = 450;

        for (int i = 0; i < MAX_PADS; i++) {
            if (!pads[i].active) continue;
            int mw = step_macros(&pads[i]);
            if (mw < min_wait) min_wait = mw;
            int aw = step_wheel_aux(&pads[i]);
            if (aw < min_wait) min_wait = aw;

            pfds[nfds].fd = pads[i].src_fd;
            pfds[nfds].events = POLLIN;
            pfds[nfds].revents = 0;
            pfd_pad[nfds] = i;
            pfd_kind[nfds] = 0;
            nfds++;

            if (pads[i].hm.has_ff_rumble) {
                pfds[nfds].fd = pads[i].ufd;
                pfds[nfds].events = POLLIN;
                pfds[nfds].revents = 0;
                pfd_pad[nfds] = i;
                pfd_kind[nfds] = 1;
                nfds++;
            }
            if (pads[i].aux_shifter_fd >= 0) {
                pfds[nfds].fd = pads[i].aux_shifter_fd;
                pfds[nfds].events = POLLIN;
                pfds[nfds].revents = 0;
                pfd_pad[nfds] = i;
                pfd_kind[nfds] = 2;
                nfds++;
            }
            if (pads[i].aux_hb_fd >= 0) {
                pfds[nfds].fd = pads[i].aux_hb_fd;
                pfds[nfds].events = POLLIN;
                pfds[nfds].revents = 0;
                pfd_pad[nfds] = i;
                pfd_kind[nfds] = 3;
                nfds++;
            }
        }

        if (nfds == 0) {
            struct timespec ts = { .tv_sec = 0, .tv_nsec = 450000000L };
            nanosleep(&ts, NULL);
            continue;
        }

        int pr = poll(pfds, nfds, min_wait);
        if (pr < 0) {
            if (errno == EINTR) continue;
            break;
        }
        if (pr == 0) continue;

        for (nfds_t k = 0; k < nfds; k++) {
            if (pfds[k].revents == 0) continue;
            int idx = pfd_pad[k];
            int kind = pfd_kind[k];
            if (!pads[idx].active) continue;

            if (pfds[k].revents & (POLLERR | POLLHUP | POLLNVAL)) {
                if (kind <= 1) {
                    detach_pad(&pads[idx]);
                } else {
                    close_wheel_aux(&pads[idx]);
                }
                continue;
            }
            if (pfds[k].revents & POLLIN) {
                if (kind == 1) {
                    handle_uinput_ff(&pads[idx]);
                } else if (kind == 2) {
                    if (process_aux_shifter_events(&pads[idx]) < 0) {
                        close_wheel_aux(&pads[idx]);
                    }
                } else if (kind == 3) {
                    if (process_aux_handbrake_events(&pads[idx]) < 0) {
                        close_wheel_aux(&pads[idx]);
                    }
                } else {
                    if (process_pad_events(&pads[idx]) < 0) {
                        detach_pad(&pads[idx]);
                    }
                }
            }
        }
    }

    for (int i = 0; i < MAX_PADS; i++) {
        detach_pad(&pads[i]);
    }
    return 0;
}
