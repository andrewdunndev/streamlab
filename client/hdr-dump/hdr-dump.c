// SPDX-License-Identifier: GPL-3.0-or-later
/*
 * hdr-dump: a headless Moonlight client that records the received video as
 * raw Annex-B HEVC and logs what the server negotiated and signalled. No
 * display, no decode. Pairing state is read from moonlight-qt's settings
 * file, so a host paired with `moonlight pair` is already usable.
 *
 * Log lines on stderr that streamlab hdr-verify reads:
 *   SERVERINFO codec_mode=0x... appversion=... gfe=...
 *   NEGOTIATED format=0x... width=... height=... fps=...
 *   HDR_MODE enable=0|1
 *   HDR_META when=callback|end valid=0|1 nonzero=0|1 ...
 *   FRAME0 hdr_active=0|1 colorspace=N
 *   SUMMARY frames=N bytes=N
 *
 * --tap FILE writes the same stream with the SEI NALs moonlight-common-c
 * drops (needs common-c-sei-tap.patch, applied by build.sh). Real clients
 * get HDR metadata from control message 0x010e, not from the SEI; the tap
 * exists to check what the encoder wrote.
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <curl/curl.h>
#include <openssl/rand.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "Limelight.h"

#ifndef COMMON_C_REV
#define COMMON_C_REV "unknown"
#endif

#define HTTP_PORT 47989
#define HTTPS_PORT_DEFAULT 47984

static volatile int terminated;
static FILE *dump, *tap;
/* SEI NALs the depacketizer dropped for the frame being assembled. */
static char sei_buf[16384];
static int sei_len;
extern void (*LiSeiTap)(const char *nal, int length);
static unsigned long frames, bytes;
static int hdr_requested;

static void die(const char *fmt, ...) __attribute__((noreturn));
static void die(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fputs("hdr-dump: ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
    exit(2);
}

struct buf {
    char *p;
    size_t n;
};

static size_t on_body(void *d, size_t s, size_t n, void *u)
{
    struct buf *b = u;
    size_t k = s * n;
    char *q = realloc(b->p, b->n + k + 1);
    if (!q)
        return 0;
    b->p = q;
    memcpy(b->p + b->n, d, k);
    b->n += k;
    b->p[b->n] = 0;
    return k;
}

static char *slurp(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return NULL;
    size_t cap = 1 << 16, n = 0;
    char *p = malloc(cap);
    size_t r;
    while (p && (r = fread(p + n, 1, cap - n - 1, f)) > 0) {
        n += r;
        if (cap - n < 2) {
            cap *= 2;
            p = realloc(p, cap);
        }
    }
    fclose(f);
    if (p)
        p[n] = 0;
    return p;
}

/* Value of a QSettings line "key=...": strips quotes and @ByteArray(...),
 * and undoes the \n, \\ and \" escapes. */
static char *conf_get(const char *txt, const char *key)
{
    size_t kl = strlen(key);
    const char *line = txt;
    while (line && *line) {
        if (strncmp(line, key, kl) == 0 && line[kl] == '=') {
            const char *v = line + kl + 1;
            const char *e = strchr(v, '\n');
            size_t n = e ? (size_t)(e - v) : strlen(v);
            while (n && (v[n - 1] == '\r'))
                n--;
            if (n >= 2 && v[0] == '"' && v[n - 1] == '"') {
                v++;
                n -= 2;
            }
            if (n > 12 && strncmp(v, "@ByteArray(", 11) == 0 && v[n - 1] == ')') {
                v += 11;
                n -= 12;
            }
            char *out = malloc(n + 1), *o = out;
            for (size_t i = 0; i < n; i++) {
                if (v[i] == '\\' && i + 1 < n) {
                    i++;
                    *o++ = v[i] == 'n' ? '\n' : v[i];
                } else {
                    *o++ = v[i];
                }
            }
            *o = 0;
            return out;
        }
        line = strchr(line, '\n');
        if (line)
            line++;
    }
    return NULL;
}

static char *xml_tag(const char *xml, const char *name)
{
    char open[64], close[64];
    snprintf(open, sizeof open, "<%s>", name);
    snprintf(close, sizeof close, "</%s>", name);
    const char *a = strstr(xml, open);
    if (!a)
        return NULL;
    a += strlen(open);
    const char *b = strstr(a, close);
    if (!b)
        return NULL;
    return strndup(a, (size_t)(b - a));
}

struct ident {
    char *cert, *key, *uid;
};

static char *hexrand(int nbytes)
{
    unsigned char r[32];
    char *h = malloc((size_t)nbytes * 2 + 1);
    if (RAND_bytes(r, nbytes) != 1)
        die("no random bytes");
    for (int i = 0; i < nbytes; i++)
        sprintf(h + i * 2, "%02x", r[i]);
    return h;
}

static char *http_get(const struct ident *id, int tls, const char *host, int port,
                      const char *cmd, const char *args)
{
    char url[1024];
    char *uuid = hexrand(16);
    snprintf(url, sizeof url, "%s://%s:%d/%s?uniqueid=%s&uuid=%s%s%s", tls ? "https" : "http", host,
             port, cmd, id->uid, uuid, args ? "&" : "", args ? args : "");
    free(uuid);
    struct buf b = {NULL, 0};
    CURL *c = curl_easy_init();
    if (!c)
        die("curl init failed");
    curl_easy_setopt(c, CURLOPT_URL, url);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, on_body);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &b);
    curl_easy_setopt(c, CURLOPT_TIMEOUT, 120L);
    curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
    if (tls) {
        struct curl_blob cb = {id->cert, strlen(id->cert), CURL_BLOB_NOCOPY};
        struct curl_blob kb = {id->key, strlen(id->key), CURL_BLOB_NOCOPY};
        curl_easy_setopt(c, CURLOPT_SSLCERT_BLOB, &cb);
        curl_easy_setopt(c, CURLOPT_SSLCERTTYPE, "PEM");
        curl_easy_setopt(c, CURLOPT_SSLKEY_BLOB, &kb);
        curl_easy_setopt(c, CURLOPT_SSLKEYTYPE, "PEM");
        curl_easy_setopt(c, CURLOPT_SSL_VERIFYPEER, 0L);
        curl_easy_setopt(c, CURLOPT_SSL_VERIFYHOST, 0L);
    }
    CURLcode rc = curl_easy_perform(c);
    curl_easy_cleanup(c);
    if (rc != CURLE_OK)
        die("GET /%s failed: %s", cmd, curl_easy_strerror(rc));
    return b.p ? b.p : strdup("");
}

static void require_ok(const char *what, const char *xml)
{
    if (!strstr(xml, "status_code=\"200\"")) {
        char *m = xml_tag(xml, "statusMessage");
        const char *at = strstr(xml, "status_message=\"");
        die("%s refused: %s", what, m ? m : at ? at : xml);
    }
}

/* Callbacks. */

static void cl_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fputs("lib: ", stderr);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
}

static void cl_stage_failed(int stage, int err)
{
    fprintf(stderr, "STAGE_FAILED stage=%s error=%d\n", LiGetStageName(stage), err);
}

static void cl_terminated(int err)
{
    fprintf(stderr, "TERMINATED error=%d\n", err);
    terminated = 1;
}

static void log_hdr_meta(const char *when)
{
    SS_HDR_METADATA m;
    memset(&m, 0, sizeof m);
    int valid = LiGetHdrMetadata(&m);
    static const SS_HDR_METADATA zero;
    int nonzero = memcmp(&m, &zero, sizeof m) != 0;
    fprintf(stderr,
            "HDR_META when=%s valid=%d nonzero=%d prim=%u,%u/%u,%u/%u,%u white=%u,%u "
            "max_display=%u min_display=%u max_cll=%u max_fall=%u max_full_frame=%u\n",
            when, valid, nonzero, m.displayPrimaries[0].x, m.displayPrimaries[0].y,
            m.displayPrimaries[1].x, m.displayPrimaries[1].y, m.displayPrimaries[2].x,
            m.displayPrimaries[2].y, m.whitePoint.x, m.whitePoint.y, m.maxDisplayLuminance,
            m.minDisplayLuminance, m.maxContentLightLevel, m.maxFrameAverageLightLevel,
            m.maxFullFrameLuminance);
}

static void cl_hdr_mode(bool on)
{
    fprintf(stderr, "HDR_MODE enable=%d\n", on ? 1 : 0);
    log_hdr_meta("callback");
}

static void cl_noop_rumble(unsigned short a, unsigned short b, unsigned short c)
{
    (void)a;
    (void)b;
    (void)c;
}

static void cl_noop_status(int s)
{
    (void)s;
}

static int dr_setup(int fmt, int w, int h, int fps, void *ctx, int flags)
{
    (void)ctx;
    (void)flags;
    fprintf(stderr, "NEGOTIATED format=0x%04x width=%d height=%d fps=%d\n", fmt, w, h, fps);
    return 0;
}

static void dr_noop(void)
{
}

/* Runs on the receive thread, which also calls dr_submit (direct submit). */
static void sei_tap(const char *nal, int length)
{
    if (sei_len + length <= (int)sizeof sei_buf) {
        memcpy(sei_buf + sei_len, nal, (size_t)length);
        sei_len += length;
    }
}

static int dr_submit(PDECODE_UNIT du)
{
    if (tap && sei_len > 0) {
        fwrite(sei_buf, 1, (size_t)sei_len, tap);
        sei_len = 0;
    }
    if (frames == 0)
        fprintf(stderr, "FRAME0 hdr_active=%d colorspace=%d\n", du->hdrActive ? 1 : 0, du->colorspace);
    for (PLENTRY e = du->bufferList; e; e = e->next) {
        if (fwrite(e->data, 1, (size_t)e->length, dump) != (size_t)e->length)
            die("short write to the dump file");
        if (tap)
            fwrite(e->data, 1, (size_t)e->length, tap);
        bytes += (unsigned long)e->length;
    }
    frames++;
    return DR_OK;
}

static int ar_init(int cfg, const POPUS_MULTISTREAM_CONFIGURATION oc, void *ctx, int flags)
{
    (void)cfg;
    (void)oc;
    (void)ctx;
    (void)flags;
    return 0;
}

static void ar_play(char *d, int n)
{
    (void)d;
    (void)n;
}

static void usage(void)
{
    fputs("usage: hdr-dump HOST APP --out FILE [--secs N] [--res WxH] [--fps N] [--bitrate KBPS]\n"
          "                [--hdr] [--conf FILE] [--tap FILE]\n"
          "       hdr-dump --version\n",
          stderr);
    exit(2);
}

int main(int argc, char **argv)
{
    const char *host = NULL, *app = NULL, *out = NULL, *conf = NULL, *tapfile = NULL;
    int secs = 15, w = 1920, h = 1080, fps = 60, bitrate = 20000;

    if (argc == 2 && strcmp(argv[1], "--version") == 0) {
        printf("hdr-dump moonlight-common-c %s\n", COMMON_C_REV);
        return 0;
    }
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--out") && i + 1 < argc)
            out = argv[++i];
        else if (!strcmp(argv[i], "--secs") && i + 1 < argc)
            secs = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--res") && i + 1 < argc) {
            if (sscanf(argv[++i], "%dx%d", &w, &h) != 2)
                usage();
        } else if (!strcmp(argv[i], "--fps") && i + 1 < argc)
            fps = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--bitrate") && i + 1 < argc)
            bitrate = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--tap") && i + 1 < argc)
            tapfile = argv[++i];
        else if (!strcmp(argv[i], "--conf") && i + 1 < argc)
            conf = argv[++i];
        else if (!strcmp(argv[i], "--hdr"))
            hdr_requested = 1;
        else if (argv[i][0] != '-' && !host)
            host = argv[i];
        else if (argv[i][0] != '-' && !app)
            app = argv[i];
        else
            usage();
    }
    if (!host || !app || !out || secs < 1)
        usage();

    char confpath[1024];
    if (!conf) {
        const char *x = getenv("XDG_CONFIG_HOME");
        const char *home = getenv("HOME");
        if (x && *x)
            snprintf(confpath, sizeof confpath, "%s/Moonlight Game Streaming Project/Moonlight.conf", x);
        else
            snprintf(confpath, sizeof confpath,
                     "%s/.config/Moonlight Game Streaming Project/Moonlight.conf", home ? home : "");
        conf = confpath;
    }
    char *txt = slurp(conf);
    if (!txt)
        die("cannot read %s", conf);
    struct ident id = {conf_get(txt, "certificate"), conf_get(txt, "key"), conf_get(txt, "uniqueid")};
    if (!id.cert || !id.key || !id.uid)
        die("%s has no certificate, key and uniqueid; pair this client first", conf);

    curl_global_init(CURL_GLOBAL_DEFAULT);

    char *xml = http_get(&id, 0, host, HTTP_PORT, "serverinfo", NULL);
    char *hp = xml_tag(xml, "HttpsPort");
    int https_port = hp ? atoi(hp) : HTTPS_PORT_DEFAULT;
    free(xml);
    xml = http_get(&id, 1, host, https_port, "serverinfo", NULL);
    require_ok("serverinfo", xml);
    char *pair = xml_tag(xml, "PairStatus");
    if (!pair || atoi(pair) != 1)
        die("this client is not paired with %s", host);
    char *scm = xml_tag(xml, "ServerCodecModeSupport");
    char *appver = xml_tag(xml, "appversion");
    char *gfever = xml_tag(xml, "GfeVersion");
    char *cur = xml_tag(xml, "currentgame");
    int codec_mode = scm ? atoi(scm) : 0;
    fprintf(stderr, "SERVERINFO codec_mode=0x%x appversion=%s gfe=%s hdr_requested=%d\n", codec_mode,
            appver ? appver : "", gfever ? gfever : "", hdr_requested);
    free(xml);

    xml = http_get(&id, 1, host, https_port, "applist", NULL);
    require_ok("applist", xml);
    int appid = 0;
    for (const char *p = xml; (p = strstr(p, "<App>")); p++) {
        const char *e = strstr(p, "</App>");
        if (!e)
            break;
        char *blk = strndup(p, (size_t)(e - p));
        char *t = xml_tag(blk, "AppTitle"), *i = xml_tag(blk, "ID");
        if (t && i && strcmp(t, app) == 0) {
            appid = atoi(i);
            free(blk);
            break;
        }
        free(blk);
    }
    free(xml);
    if (!appid)
        die("app '%s' not in the server's app list", app);

    STREAM_CONFIGURATION sc;
    LiInitializeStreamConfiguration(&sc);
    sc.width = w;
    sc.height = h;
    sc.fps = fps;
    sc.bitrate = bitrate;
    sc.packetSize = 1392;
    sc.streamingRemotely = STREAM_CFG_LOCAL;
    sc.audioConfiguration = AUDIO_CONFIGURATION_STEREO;
    sc.supportedVideoFormats = VIDEO_FORMAT_H265 | (hdr_requested ? VIDEO_FORMAT_H265_MAIN10 : 0);
    sc.clientRefreshRateX100 = fps * 100;
    sc.colorSpace = hdr_requested ? COLORSPACE_REC_2020 : COLORSPACE_REC_709;
    sc.colorRange = COLOR_RANGE_LIMITED;
    sc.encryptionFlags = ENCFLG_NONE;
    if (RAND_bytes((unsigned char *)sc.remoteInputAesKey, sizeof sc.remoteInputAesKey) != 1 ||
        RAND_bytes((unsigned char *)sc.remoteInputAesIv, sizeof sc.remoteInputAesIv) != 1)
        die("no random bytes");
    unsigned char *iv = (unsigned char *)sc.remoteInputAesIv;
    int rikeyid = (int)(((unsigned)iv[0] << 24) | ((unsigned)iv[1] << 16) | ((unsigned)iv[2] << 8) | iv[3]);
    char rikey[33];
    for (int i = 0; i < 16; i++)
        sprintf(rikey + i * 2, "%02x", (unsigned char)sc.remoteInputAesKey[i]);

    int resume = cur && atoi(cur) == appid;
    if (cur && atoi(cur) != 0 && !resume)
        die("the server is running app id %s, not '%s'; quit it first", cur, app);
    char args[1024];
    snprintf(args, sizeof args,
             "appid=%d&mode=%dx%dx%d&additionalStates=1&sops=0&rikey=%s&rikeyid=%d%s"
             "&localAudioPlayMode=0&surroundAudioInfo=196610&remoteControllersBitmap=0&gcmap=0&gcpersist=0%s",
             appid, w, h, fps, rikey, rikeyid,
             hdr_requested ? "&hdrMode=1&clientHdrCapVersion=0&clientHdrCapSupportedFlagsInUint32=0"
                             "&clientHdrCapMetaDataId=NV_STATIC_METADATA_TYPE_1"
                             "&clientHdrCapDisplayData=0x0x0x0x0x0x0x0x0x0x0"
                           : "",
             LiGetLaunchUrlQueryParameters());
    xml = http_get(&id, 1, host, https_port, resume ? "resume" : "launch", args);
    require_ok(resume ? "resume" : "launch", xml);
    char *rtsp = xml_tag(xml, "sessionUrl0");
    if (!rtsp)
        die("no sessionUrl0 in the launch response");

    if (tapfile) {
        tap = fopen(tapfile, "wb");
        if (!tap)
            die("cannot open %s", tapfile);
        LiSeiTap = sei_tap;
    }
    dump = fopen(out, "wb");
    if (!dump)
        die("cannot open %s", out);

    SERVER_INFORMATION si;
    LiInitializeServerInformation(&si);
    si.address = host;
    si.serverInfoAppVersion = appver;
    si.serverInfoGfeVersion = gfever;
    si.rtspSessionUrl = rtsp;
    si.serverCodecModeSupport = codec_mode;

    CONNECTION_LISTENER_CALLBACKS cl;
    LiInitializeConnectionCallbacks(&cl);
    cl.logMessage = cl_log;
    cl.stageFailed = cl_stage_failed;
    cl.connectionTerminated = cl_terminated;
    cl.setHdrMode = cl_hdr_mode;
    cl.rumble = cl_noop_rumble;
    cl.connectionStatusUpdate = cl_noop_status;

    DECODER_RENDERER_CALLBACKS dr;
    LiInitializeVideoCallbacks(&dr);
    dr.setup = dr_setup;
    dr.start = dr_noop;
    dr.stop = dr_noop;
    dr.cleanup = dr_noop;
    dr.submitDecodeUnit = dr_submit;
    dr.capabilities = CAPABILITY_DIRECT_SUBMIT;

    AUDIO_RENDERER_CALLBACKS ar;
    LiInitializeAudioCallbacks(&ar);
    ar.init = ar_init;
    ar.start = dr_noop;
    ar.stop = dr_noop;
    ar.cleanup = dr_noop;
    ar.decodeAndPlaySample = ar_play;

    int rc = LiStartConnection(&si, &sc, &cl, &dr, &ar, NULL, 0, NULL, 0);
    if (rc != 0) {
        fprintf(stderr, "START_FAILED code=%d\n", rc);
        fclose(dump);
        return 2;
    }
    fprintf(stderr, "STARTED\n");
    time_t t0 = time(NULL);
    while (!terminated && time(NULL) - t0 < secs)
        usleep(100000);
    log_hdr_meta("end");
    LiStopConnection();
    fclose(dump);
    if (tap)
        fclose(tap);
    fprintf(stderr, "SUMMARY frames=%lu bytes=%lu\n", frames, bytes);
    return frames > 0 ? 0 : 3;
}
