/* Local Xvfb viewer/input for Half-Life. Rendering and HTTP run on ARM. */
#define _GNU_SOURCE
#include <X11/Xlib.h>
#include <alsa/asoundlib.h>
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <zlib.h>

extern int XTestFakeKeyEvent(Display *, unsigned int, Bool, unsigned long);
extern int XTestFakeButtonEvent(Display *, unsigned int, Bool, unsigned long);
extern int XTestFakeMotionEvent(Display *, int, int, int, unsigned long);

static Display *display;
static volatile sig_atomic_t running = 1;
static unsigned char held_keys[256], held_buttons[6];
static int physical[4], physical_forward, physical_dig, physical_jump, triple;
static double middle_pressed, last_turn, last_http_input, jump_until;
static unsigned long frames;
static int width = 320, height = 240;
static snd_ctl_t *audio_ctl;

static int audio_element(const char *name, snd_ctl_elem_type_t type, long values[2], int write) {
    snd_ctl_elem_info_t *info;
    snd_ctl_elem_value_t *value;
    snd_ctl_elem_info_alloca(&info);
    snd_ctl_elem_value_alloca(&value);
    snd_ctl_elem_info_set_interface(info, SND_CTL_ELEM_IFACE_MIXER);
    snd_ctl_elem_info_set_name(info, name);
    int err = snd_ctl_elem_info(audio_ctl, info);
    if (err < 0) return err;
    unsigned count = snd_ctl_elem_info_get_count(info);
    if (!count || count > 2 || snd_ctl_elem_info_get_type(info) != type) return -EINVAL;
    snd_ctl_elem_value_set_interface(value, SND_CTL_ELEM_IFACE_MIXER);
    snd_ctl_elem_value_set_name(value, name);
    if ((err = snd_ctl_elem_read(audio_ctl, value)) < 0) return err;
    for (unsigned i = 0; i < count; i++) {
        if (write) {
            if (type == SND_CTL_ELEM_TYPE_BOOLEAN) snd_ctl_elem_value_set_boolean(value, i, values[i]);
            else {
                if (snd_ctl_elem_info_is_user(info) || snd_ctl_elem_info_get_min(info) != 0 ||
                    snd_ctl_elem_info_get_max(info) != 255) return -EINVAL;
                snd_ctl_elem_value_set_integer(value, i, values[i]);
            }
        } else values[i] = type == SND_CTL_ELEM_TYPE_BOOLEAN ?
            snd_ctl_elem_value_get_boolean(value, i) : snd_ctl_elem_value_get_integer(value, i);
    }
    if (!write && count == 1) values[1] = values[0];
    return write ? snd_ctl_elem_write(audio_ctl, value) : 0;
}
/* The old user-created Master control is bypassed by this direct PCM path.
 * TAS5805M hardware gain: raw 207 = 0 dB, each lower step is -0.5 dB.
 * Give the browser a -60..0 dB fader; zero is a separate hard mute. */
static const char *audio_master = "TAS5805M Digital Playback Volume";
enum { AUDIO_MIN_RAW = 87, AUDIO_MAX_RAW = 207 };
/* On this board, off/off disables the amplifier output. */
static const char *audio_switch = "TAS5805M Master Playback Digital Mute Switc";
static int audio_read(int *percent, int *muted) {
    long raw[2] = {0}, enabled[2] = {0};
    int err = audio_element(audio_master, SND_CTL_ELEM_TYPE_INTEGER, raw, 0);
    if (err < 0) return err;
    if ((err = audio_element(audio_switch, SND_CTL_ELEM_TYPE_BOOLEAN, enabled, 0)) < 0) return err;
    *muted = !enabled[0] || !enabled[1] || !raw[0] || !raw[1];
    *percent = *muted ? 0 : 1 + (int)(((raw[0] - AUDIO_MIN_RAW) * 99 + 60) / 120);
    if (!*muted && *percent < 1) *percent = 1;
    if (*percent > 100) *percent = 100;
    return 0;
}
static int audio_set(int percent) {
    long level = percent ? AUDIO_MIN_RAW + ((percent - 1) * (AUDIO_MAX_RAW - AUDIO_MIN_RAW) + 49) / 99 : 0;
    long off[2] = {0, 0}, raw[2] = {level, level};
    int err = audio_element(audio_switch, SND_CTL_ELEM_TYPE_BOOLEAN, off, 1);
    if (err < 0) return err;
    if ((err = audio_element(audio_master, SND_CTL_ELEM_TYPE_INTEGER, raw, 1)) < 0) return err;
    if (percent) {
        long on[2] = {1, 1};
        if ((err = audio_element(audio_switch, SND_CTL_ELEM_TYPE_BOOLEAN, on, 1)) < 0) goto fail;
    }
    int actual, muted;
    if ((err = audio_read(&actual, &muted)) < 0 || actual != percent || muted != !percent) {
        if (err >= 0) err = -EIO;
        goto fail;
    }
    return 0;
fail:
    audio_element(audio_switch, SND_CTL_ELEM_TYPE_BOOLEAN, off, 1);
    return err;
}

static Window game_window(void) {
    Window root, parent, *children = NULL, result = None;
    unsigned int count;
    if (XQueryTree(display, DefaultRootWindow(display), &root, &parent, &children, &count)) {
        for (unsigned int i = 0; i < count; i++) {
            XWindowAttributes a;
            if (XGetWindowAttributes(display, children[i], &a) && a.map_state == IsViewable &&
                a.width == width && a.height == height) result = children[i];
        }
        if (children) XFree(children);
    }
    return result;
}
static int game_running(void) {
    FILE *f = fopen("/hl-game.pid", "r"); int pid = 0;
    if (!f) return 0;
    int parsed = fscanf(f, "%d", &pid); fclose(f);
    if (parsed != 1 || pid < 2) return 0;
    char path[80], data[4096]; snprintf(path, sizeof(path), "/proc/%d/cmdline", pid);
    f = fopen(path, "rb"); if (!f) return 0;
    size_t n = fread(data, 1, sizeof(data), f); fclose(f);
    const char *main_class = "xash3d";
    return memmem(data, n, main_class, strlen(main_class)) != NULL;
}

static double now(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static void stop(int sig) { (void)sig; running = 0; }
static void key(const char *name, int down) {
    KeyCode code = XKeysymToKeycode(display, XStringToKeysym(name));
    if (code && held_keys[code] != !!down) {
        XTestFakeKeyEvent(display, code, down, CurrentTime);
        held_keys[code] = !!down;
    }
}
static void button(int code, int down) {
    if (code >= 1 && code <= 5 && held_buttons[code] != !!down) {
        XTestFakeButtonEvent(display, code, down, CurrentTime);
        held_buttons[code] = !!down;
    }
}
static void reset_input(void) {
    for (int i = 1; i < 256; i++) if (held_keys[i]) {
        XTestFakeKeyEvent(display, i, False, CurrentTime); held_keys[i] = 0;
    }
    for (int i = 1; i < 6; i++) button(i, 0);
    physical_forward = physical_dig = physical_jump = 0;
    jump_until = 0;
    XFlush(display);
}
static void move_relative(int dx, int dy) {
    Window root, child; int x, y, wx, wy; unsigned int mask;
    if (XQueryPointer(display, DefaultRootWindow(display), &root, &child,
                      &x, &y, &wx, &wy, &mask))
        XTestFakeMotionEvent(display, -1, x + dx, y + dy, CurrentTime);
}
static int bounded(int value, int lo, int hi) {
    return value < lo ? lo : value > hi ? hi : value;
}
static int parameter(const char *query, const char *name, char *out, size_t size) {
    size_t n = strlen(name);
    while (query && *query) {
        const char *end = strchr(query, '&');
        if (!end) end = query + strlen(query);
        if ((size_t)(end - query) > n && !strncmp(query, name, n) && query[n] == '=') {
            size_t len = end - query - n - 1;
            if (len >= size) return 0;
            memcpy(out, query + n + 1, len); out[len] = 0; return 1;
        }
        query = *end ? end + 1 : NULL;
    }
    return 0;
}
static int number(const char *q, const char *name) {
    char value[32]; return parameter(q, name, value, sizeof(value)) ? atoi(value) : 0;
}
static void input(const char *query) {
    char type[32], name[32];
    if (!parameter(query, "type", type, sizeof(type))) return;
    Window window = game_window();
    if (window) XSetInputFocus(display, window, RevertToParent, CurrentTime);
    if (!strcmp(type, "key") && parameter(query, "key", name, sizeof(name))) {
        const char *allowed[] = {"space","Escape","Shift_L","Shift_R","Control_L","Control_R",
            "Return","BackSpace","Tab","F3","F5","F9","grave","asciitilde","slash","minus","equal","period","comma"};
        int permitted = strlen(name) == 1 && isalnum((unsigned char)name[0]);
        for (size_t i = 0; i < sizeof(allowed) / sizeof(*allowed); i++)
            if (!strcmp(name, allowed[i])) permitted = 1;
        if (permitted) key(name, number(query, "down") != 0);
    } else if (!strcmp(type, "button")) {
        button(number(query, "button"), number(query, "down") != 0);
    } else if (!strcmp(type, "move")) {
        XTestFakeMotionEvent(display, -1, bounded(number(query, "x"), 0, width - 1),
                            bounded(number(query, "y"), 0, height - 1), CurrentTime);
    } else if (!strcmp(type, "look")) {
        move_relative(bounded(number(query, "dx"), -100, 100),
                      bounded(number(query, "dy"), -100, 100));
    } else if (!strcmp(type, "reset")) reset_input();
    else if (!strcmp(type, "quit") && window) {
        XEvent e = {0}; e.xclient.type = ClientMessage; e.xclient.window = window;
        e.xclient.message_type = XInternAtom(display, "WM_PROTOCOLS", False);
        e.xclient.format = 32;
        e.xclient.data.l[0] = XInternAtom(display, "WM_DELETE_WINDOW", False);
        e.xclient.data.l[1] = CurrentTime;
        XSendEvent(display, window, False, NoEventMask, &e);
    }
    last_http_input = now(); XFlush(display);
}

static uint32_t be32(const unsigned char *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static void put32(unsigned char *p, uint32_t n) {
    p[0] = n >> 24; p[1] = n >> 16; p[2] = n >> 8; p[3] = n;
}
static int send_all(int fd, const void *data, size_t size) {
    const unsigned char *p = data;
    while (size) {
        ssize_t n = send(fd, p, size, MSG_NOSIGNAL);
        if (n <= 0) return -1;
        p += n; size -= n;
    }
    return 0;
}
static void header(int fd, int code, const char *mime, size_t size) {
    char h[512]; int n = snprintf(h, sizeof(h),
        "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %zu\r\n"
        "Cache-Control: no-store\r\nConnection: close\r\n"
        "X-Content-Type-Options: nosniff\r\n\r\n",
        code, code == 200 ? "OK" : "Unavailable", mime, size);
    send_all(fd, h, n);
}
static int chunk(int fd, const char *type, const void *data, uint32_t size) {
    unsigned char h[8], crc[4]; put32(h, size); memcpy(h + 4, type, 4);
    uLong sum = crc32(0, (const Bytef *)type, 4);
    if (size) sum = crc32(sum, data, size);
    put32(crc, sum);
    if (send_all(fd, h, sizeof(h)) || (size && send_all(fd, data, size)) ||
        send_all(fd, crc, sizeof(crc))) return -1;
    return 0;
}
static void frame(int fd) {
    unsigned char h[100]; FILE *f = fopen("/tmp/frames/Xvfb_screen0", "rb");
    if (!f || fread(h, 1, sizeof(h), f) != sizeof(h)) {
        if (f) fclose(f); header(fd, 503, "text/plain", 0); return;
    }
    uint32_t w = be32(h + 16), ht = be32(h + 20), bpp = be32(h + 44),
             stride = be32(h + 48), colors = be32(h + 76), start = be32(h);
    uint32_t masks[] = {be32(h + 56), be32(h + 60), be32(h + 64)};
    if (!w || !ht || w > 1024 || ht > 768 || (bpp != 32 && bpp != 24) ||
        stride < w * (bpp / 8) || stride > 8192 || start > 65536 || colors > 65536) {
        fclose(f); header(fd, 503, "text/plain", 0); return;
    }
    width = w; height = ht;
    size_t scan = 1 + w * 3, raw_size = scan * ht;
    unsigned char *raw = malloc(raw_size), *row = malloc(stride);
    if (!raw || !row) { free(raw); free(row); fclose(f); header(fd, 503, "text/plain", 0); return; }
    fseek(f, start + colors * 12, SEEK_SET);
    int shift[3]; uint32_t maximum[3];
    for (int c = 0; c < 3; c++) {
        shift[c] = 0;
        if (!masks[c]) { maximum[c] = 1; continue; }
        while (!((masks[c] >> shift[c]) & 1)) shift[c]++;
        maximum[c] = masks[c] >> shift[c];
    }
    for (uint32_t y = 0; y < ht; y++) {
        if (fread(row, 1, stride, f) != stride) {
            free(raw); free(row); fclose(f); header(fd, 503, "text/plain", 0); return;
        }
        raw[y * scan] = 0;
        if (bpp == 32 && !be32(h + 28) && masks[0] == 0xff0000 &&
            masks[1] == 0xff00 && masks[2] == 0xff) {
            unsigned char *dest = raw + y * scan + 1;
            for (uint32_t x = 0; x < w; x++) {
                dest[x * 3] = row[x * 4 + 2];
                dest[x * 3 + 1] = row[x * 4 + 1];
                dest[x * 3 + 2] = row[x * 4];
            }
            continue;
        }
        for (uint32_t x = 0; x < w; x++) {
            uint32_t pixel = 0; unsigned char *p = row + x * (bpp / 8);
            for (uint32_t i = 0; i < bpp / 8; i++) {
                if (be32(h + 28)) pixel = pixel << 8 | p[i];
                else pixel |= (uint32_t)p[i] << (8 * i);
            }
            for (int c = 0; c < 3; c++)
                raw[y * scan + 1 + x * 3 + c] = ((pixel & masks[c]) >> shift[c]) * 255 / maximum[c];
        }
    }
    fclose(f); free(row);
    uLongf length = compressBound(raw_size); unsigned char *png = malloc(length);
    if (!png || compress2(png, &length, raw, raw_size, 1) != Z_OK) {
        free(raw); free(png); header(fd, 503, "text/plain", 0); return;
    }
    free(raw);
    unsigned char ihdr[13] = {0}; put32(ihdr, w); put32(ihdr + 4, ht); ihdr[8] = 8; ihdr[9] = 2;
    header(fd, 200, "image/png", 8 + 25 + 12 + length + 12);
    if (!send_all(fd, "\211PNG\r\n\032\n", 8) && !chunk(fd, "IHDR", ihdr, 13) &&
        !chunk(fd, "IDAT", png, length)) chunk(fd, "IEND", NULL, 0);
    free(png); frames++;
}
static void serve(int fd) {
    struct timeval limit = {.tv_sec = 1};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &limit, sizeof(limit));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &limit, sizeof(limit));
    char request[4096], path[2048], method[8]; int used = 0;
    while (used < (int)sizeof(request) - 1) {
        ssize_t n = recv(fd, request + used, sizeof(request) - 1 - used, 0);
        if (n <= 0) return;
        used += n; request[used] = 0;
        if (strstr(request, "\r\n\r\n")) break;
    }
    if (sscanf(request, "%7s %2047s HTTP/1.%*d", method, path) != 2) {
        header(fd, 400, "text/plain", 0); return;
    }
    int audio_write = !strcmp(method, "POST") && !strncmp(path, "/audio?volume=", 14);
    if (strcmp(method, "GET") && !audio_write) { header(fd, 405, "text/plain", 0); return; }
    if (!strcmp(path, "/audio") || audio_write) {
        if (audio_write) {
            const char *start = path + 14;
            char *end;
            errno = 0;
            long percent = strtol(start, &end, 10);
            if (!*start || strlen(start) > 3 || strspn(start, "0123456789") != strlen(start) ||
                *end || errno || percent < 0 || percent > 100) {
                header(fd, 400, "text/plain", 0); return;
            }
            int err = audio_set(percent);
            if (err < 0) { fprintf(stderr, "Audio write: %s\n", snd_strerror(err)); header(fd, 503, "text/plain", 0); return; }
        }
        int volume, muted, err = audio_read(&volume, &muted);
        if (err < 0) { header(fd, 503, "text/plain", 0); return; }
        char data[96]; int size = snprintf(data, sizeof(data), "{\"volume\":%d,\"muted\":%s}\n", volume, muted ? "true" : "false");
        header(fd, 200, "application/json", size); send_all(fd, data, size); return;
    }
    if (!strcmp(path, "/") || !strcmp(path, "/index.html")) {
        FILE *f = fopen("/www/index.html", "rb");
        if (!f) { header(fd, 503, "text/plain", 0); return; }
        fseek(f, 0, SEEK_END); long len = ftell(f); rewind(f);
        if (len < 0 || len > 65536) { fclose(f); header(fd, 503, "text/plain", 0); return; }
        char *data = malloc(len);
        if (!data || fread(data, 1, len, f) != (size_t)len) {
            free(data); fclose(f); header(fd, 503, "text/plain", 0); return;
        }
        fclose(f); header(fd, 200, "text/html; charset=utf-8", len); send_all(fd, data, len); free(data);
    } else if (!strcmp(path, "/quality?mode=fast") || !strcmp(path, "/quality?mode=normal")) {
        int fast = !strcmp(path, "/quality?mode=fast");
        FILE *f = fopen("/performance.properties.new", "w");
        if (!f) { header(fd, 503, "text/plain", 0); return; }
        int failed = fprintf(f, "maxFps=30\nfastLightmap=%s\nfastFog=%s\nprofile=false\n", fast ? "true" : "false", fast ? "true" : "false") < 0;
        if (fclose(f)) failed = 1;
        if (failed || rename("/performance.properties.new", "/performance.properties")) {
            header(fd, 503, "text/plain", 0); return;
        }
        header(fd, 200, "text/plain", 0);
    } else if (!strcmp(path, "/performance")) {
        char data[8192]; FILE *f = fopen("/tmp/performance.properties", "rb");
        if (!f) { header(fd, 503, "text/plain", 0); return; }
        size_t len = fread(data, 1, sizeof(data), f); fclose(f);
        header(fd, 200, "text/plain", len); send_all(fd, data, len);
    } else if (!strncmp(path, "/frame.png", 10) && (path[10] == 0 || path[10] == '?')) frame(fd);
    else if (!strncmp(path, "/input?", 7)) { input(path + 7); header(fd, 200, "text/plain", 0); }
    else if (!strcmp(path, "/status")) {
        char data[256]; int len = snprintf(data, sizeof(data),
            "{\"renderer\":\"Station ARM32 software\",\"game\":\"Half-Life\",\"game_running\":%s,\"width\":%d,\"height\":%d,\"frames_sent\":%lu,\"web_pid\":%d}\n",
            game_running() ? "true" : "false", width, height, frames, getpid());
        header(fd, 200, "application/json", len); send_all(fd, data, len);
    } else header(fd, 404, "text/plain", 0);
}
static void physical_tick(void) {
    double t = now(); int both = physical[1] && physical[2];
    int all = both && physical[0];
    if (all && !triple) { button(1, 0); button(3, 1); button(3, 0); }
    triple = all;
    int forward = physical[0] && !all && t - middle_pressed >= .25;
    if (forward != physical_forward) { key("w", forward); physical_forward = forward; }
    int jump = physical[3] || t < jump_until;
    if (jump != physical_jump) { key("space", jump); physical_jump = jump; }
    int dig = both && !all;
    if (dig != physical_dig) { button(1, dig); physical_dig = dig; }
    if (t - last_turn >= .04 && physical[1] != physical[2]) {
        move_relative(physical[1] ? 5 : -5, 0); last_turn = t;
    }
    if (last_http_input && t - last_http_input > 15 && !physical[0] && !physical[1] && !physical[2] && !jump) {
        reset_input(); last_http_input = 0;
    }
    XFlush(display);
}
static void evdev(int fd) {
    struct input_event e;
    while (read(fd, &e, sizeof(e)) == sizeof(e)) if (e.type == EV_KEY && e.value != 2) {
        /* This Lite's capacitive pause button emits KEY_VOICECOMMAND (582). */
        int i = e.code == KEY_MUTE ? 0 : e.code == KEY_VOLUMEUP ? 1 : e.code == KEY_VOLUMEDOWN ? 2 :
            (e.code == KEY_VOICECOMMAND || e.code == KEY_PLAYPAUSE || e.code == KEY_PAUSE) ? 3 : -1;
        if (i < 0) continue;
        Window window = game_window();
        if (window) XSetInputFocus(display, window, RevertToParent, CurrentTime);
        if (i == 0 && e.value) middle_pressed = now();
        if (i == 3) {
            /* Keep short taps visible for several game ticks, even if evdev
             * batches press+release while a PNG request is being served. */
            if (e.value && !physical[3]) jump_until = now() + .12;
            fprintf(stderr, "Physical pause %s -> jump\n", e.value ? "down" : "up");
        }
        physical[i] = !!e.value;
    }
}
int main(int argc, char **argv) {
    signal(SIGTERM, stop); signal(SIGINT, stop); signal(SIGPIPE, SIG_IGN);
    setenv("ALSA_CONFIG_PATH", "/halflife/asound.conf", 1);
    int err = snd_ctl_open(&audio_ctl, "default", 0);
    if (err >= 0 && argc == 2 && !strcmp(argv[1], "--audio-info")) {
        const char *names[] = {"Master Playback Volume", audio_master};
        for (unsigned i = 0; i < 2; i++) {
            snd_ctl_elem_info_t *info;
            snd_ctl_elem_info_alloca(&info);
            snd_ctl_elem_info_set_interface(info, SND_CTL_ELEM_IFACE_MIXER);
            snd_ctl_elem_info_set_name(info, names[i]);
            if ((err = snd_ctl_elem_info(audio_ctl, info)) < 0) break;
            printf("%s: user_created=%d, channels=%u, range=%ld..%ld\n", names[i],
                snd_ctl_elem_info_is_user(info), snd_ctl_elem_info_get_count(info),
                snd_ctl_elem_info_get_min(info), snd_ctl_elem_info_get_max(info));
        }
        snd_ctl_close(audio_ctl); return err < 0;
    }
    if (err < 0 || (err = audio_set(0)) < 0) {
        fprintf(stderr, "Cannot initialise muted audio: %s\n", snd_strerror(err)); return 1;
    }
    if (argc == 2 && !strcmp(argv[1], "--mute")) { snd_ctl_close(audio_ctl); return 0; }
    display = XOpenDisplay(":42");
    if (!display) { fprintf(stderr, "Cannot open local display :42\n"); return 1; }
    int server = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0), yes = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in addr = {.sin_family = AF_INET, .sin_port = htons(12080), .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (bind(server, (struct sockaddr *)&addr, sizeof(addr)) || listen(server, 8)) { perror("HTTP bind"); return 1; }
    struct pollfd fds[] = {{.fd = server, .events = POLLIN},
        {.fd = open("/dev/input/event0", O_RDONLY | O_NONBLOCK), .events = POLLIN},
        {.fd = open("/dev/input/event1", O_RDONLY | O_NONBLOCK), .events = POLLIN}};
    fprintf(stderr, "Station viewer listening on :12080, evdev=%d,%d\n", fds[1].fd, fds[2].fd);
    while (running) {
        if (poll(fds, 3, 20) > 0) {
            for (int i = 1; i < 3; i++) if (fds[i].revents & POLLIN) evdev(fds[i].fd);
            if (fds[0].revents & POLLIN) {
                int fd = accept4(server, NULL, NULL, SOCK_CLOEXEC);
                if (fd >= 0) { serve(fd); close(fd); }
            }
        }
        physical_tick();
    }
    audio_set(0);
    reset_input(); close(server); XCloseDisplay(display); snd_ctl_close(audio_ctl); return 0;
}
