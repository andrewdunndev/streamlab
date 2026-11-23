#!/usr/bin/env python3
"""Virtual pads in a Wolf session through /api/v1/sessions/input, as Moonlight sends them. Run ON the Wolf host as root
(the API socket is WOLF_SOCKET, default /run/wolf/wolf.sock).
usage: pad.py [--session SID] VERB ARGS     (--session defaults to the only session; several sessions is an error)
SLOT = Moonlight controller number 0..3, TYPE = xbox (1) | ps (2) | nintendo (3), LI_CTYPE_*.
  plug SLOT TYPE [CAPS]     CONTROLLER_ARRIVAL (CAPS default 3 = ANALOG_TRIGGERS|RUMBLE; hex ok), then MULTI_CONTROLLER
                            with the slot's bit set. CAPS 0xbf adds TRIGGER_RUMBLE|TOUCHPAD|ACCELEROMETER|GYRO|RGB_LED.
  unplug SLOT               MULTI_CONTROLLER with the slot's bit clear
  press SLOT BUTTON         BUTTON (a|b|x|y) down, 0.25 s, then up; prints t_down t_up
  motion SLOT gyro|accel X Y Z   CONTROLLER_MOTION 0x55000006 (gyro deg/s, accel m/s^2, as Moonlight sends them)
  ctouch SLOT EVT PID X Y   CONTROLLER_TOUCH 0x55000005 on the pad's touchpad (EVT = down|up|move|cancel|cancel_all or
                            a number; X Y normalised 0..1, pressure 1.0); prints the wall time
  touch EVT PID X Y         TOUCH 0x55000002 (the session's touch screen), rotation 0, pressure 1.0, contact area 0
  cycle SLOT TYPE N         N x (unplug, plug at once, 1.5 s, count host nodes); one row per cycle
  nodes                     host-side Wolf pad nodes (event, js, hidraw) with uniq
Every call prints one `key=value` line per action, with the wall time."""
import glob, http.client, json, os, socket, struct, sys, time

SOCK = os.environ.get("WOLF_SOCKET", "/run/wolf/wolf.sock")
CTYPE = {"xbox": 1, "ps": 2, "nintendo": 3}
BTN = {"a": 0x1000, "b": 0x2000, "x": 0x4000, "y": 0x8000}

def pkt(itype, body):
    n = len(body) + 4
    return struct.pack("<HH", 0x0206, n + 4) + struct.pack(">I", n) + struct.pack("<I", itype) + body

def arrival(slot, ctype, caps=3):  # caps byte (CONTROLLER_CAPABILITIES); its high byte and the button flags stay 0
    return pkt(0x55000004, struct.pack("<BBHI", slot, ctype, caps, 0))

MOTION = {"accel": 1, "gyro": 2}  # control.hpp MOTION_TYPE: ACCELERATION = 0x01, GYROSCOPE = 0x02

def motion(slot, kind, x, y, z):
    # CONTROLLER_MOTION_PACKET: controller_number u8, motion_type u8, zero[2], then x y z as utils::netfloat,
    # which utils.hpp reads with endian_load<float, 4, order::little>: an IEEE-754 float, little-endian.
    return pkt(0x55000006, struct.pack("<BB2x3f", slot, MOTION[kind], x, y, z))

TEVT = {"hover": 0, "down": 1, "up": 2, "move": 3, "cancel": 4, "button_only": 5, "hover_leave": 6, "cancel_all": 7}

def ctouch(slot, evt, pid, x, y, p=1.0):
    # CONTROLLER_TOUCH_PACKET: controller_number u8, event_type u8, zero[2], pointer_id u32 (little_to_native), x y pressure netfloat
    return pkt(0x55000005, struct.pack("<BB2xI3f", slot, evt, pid, x, y, p))

def touch(evt, pid, x, y, p=1.0):
    # TOUCH_PACKET: event_type u8, zero u8, rotation u16, pointer_id u32, x y pressure_or_distance contact_major contact_minor
    return pkt(0x55000002, struct.pack("<BxHI5f", evt, 0, pid, x, y, p, 0.0, 0.0))

def multi(slot, flags, mask):
    return pkt(0x0000000C, struct.pack("<5h2B4h3h", 0x1A, slot, mask, 0x14, flags, 0, 0, 0, 0, 0, 0, 0x9C, 0, 0x55))

class UHC(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX); self.sock.settimeout(10); self.sock.connect(SOCK)

def api(method, path, body=None):
    c = UHC("localhost", timeout=10)
    c.request(method, "/api/v1/" + path, json.dumps(body) if body else None, {"Content-Type": "application/json"})
    r = c.getresponse(); d = r.read()
    if r.status != 200: sys.exit(f"{path}: HTTP {r.status} {d[:200]!r}")
    return json.loads(d)

def send(sid, p): api("POST", "sessions/input", {"session_id": sid, "input_packet_hex": p.hex()})

def rd(p):
    try: return open(p).read().strip()
    except OSError: return "-"

def nodes():
    """Host nodes of Wolf pads: event/js under /sys/class/input, hidraw whose HID_NAME starts with Wolf."""
    out = []
    for d in sorted(glob.glob("/sys/class/input/*")):
        b = os.path.basename(d)
        if not (b.startswith("event") or b.startswith("js")): continue
        name = rd(d + "/device/name")
        if name.startswith("Wolf"):
            out.append(f"{b}={rd(d + '/dev')}({name.replace(' ', '_')},uniq={rd(d + '/device/uniq') or '-'})")
    for d in sorted(glob.glob("/sys/class/hidraw/*")):
        ue = rd(d + "/device/uevent")
        name = next((l[9:] for l in ue.splitlines() if l.startswith("HID_NAME=")), "")
        uniq = next((l[9:] for l in ue.splitlines() if l.startswith("HID_UNIQ=")), "-")
        if name.startswith("Wolf"):
            out.append(f"{os.path.basename(d)}={rd(d + '/dev')}({name.replace(' ', '_')},uniq={uniq})")
    return out

def sid(want=None):
    s = api("GET", "sessions")["sessions"]
    if want is not None: return want
    if not s: sys.exit("no session")
    if len(s) > 1: sys.exit("several sessions, pass --session SID: " + " ".join(str(x["client_id"]) for x in s))
    return str(s[0]["client_id"])

args = sys.argv[1:]
want = None
if "--session" in args:
    k = args.index("--session")
    if k + 1 >= len(args): sys.exit("--session needs SID")
    want = args[k + 1]; del args[k:k + 2]
if not args or args[0] in ("-h", "--help"): sys.exit(__doc__)
sys.argv = [sys.argv[0]] + args
verb = sys.argv[1]
if verb == "nodes":
    print(f"t={time.time():.3f} nodes=" + " ".join(nodes())); sys.exit()
if verb == "touch":
    e = sys.argv[2]; ev = TEVT[e] if e in TEVT else int(e, 0); pid = int(sys.argv[3]); x, y = float(sys.argv[4]), float(sys.argv[5])
    s = sid(want); t = time.time(); send(s, touch(ev, pid, x, y))
    print(f"touch t={t:.6f} evt={e}({ev}) pid={pid} x={x} y={y}"); sys.exit()
slot = int(sys.argv[2]); s = sid(want)
if verb == "plug":
    caps = int(sys.argv[4], 0) if len(sys.argv) > 4 else 3
    t = time.time(); send(s, arrival(slot, CTYPE[sys.argv[3]], caps)); send(s, multi(slot, 0, 1 << slot))
    time.sleep(1.5)
    print(f"t={t:.3f} plug_s{slot}_{sys.argv[3]}" + ("" if caps == 3 else f"_caps{caps:#x}") + "_host_nodes=" + " ".join(nodes()))
elif verb == "motion":
    kind = sys.argv[3]; x, y, z = (float(v) for v in sys.argv[4:7])
    t = time.time(); send(s, motion(slot, kind, x, y, z))
    print(f"motion t={t:.6f} slot={slot} kind={kind} x={x} y={y} z={z}")
elif verb == "ctouch":
    e = sys.argv[3]; ev = TEVT[e] if e in TEVT else int(e, 0); pid = int(sys.argv[4]); x, y = float(sys.argv[5]), float(sys.argv[6])
    t = time.time(); send(s, ctouch(slot, ev, pid, x, y))
    print(f"ctouch t={t:.6f} slot={slot} evt={e}({ev}) pid={pid} x={x} y={y}")
elif verb == "unplug":
    t = time.time(); send(s, multi(slot, 0, 0)); time.sleep(1.5)
    print(f"t={t:.3f} unplug_s{slot}_host_nodes=" + " ".join(nodes()))
elif verb == "press":
    b = sys.argv[3]; t0 = time.time(); send(s, multi(slot, BTN[b], 1 << slot)); time.sleep(0.25)
    t1 = time.time(); send(s, multi(slot, 0, 1 << slot))
    print(f"press slot={slot} button={b} t_down={t0:.3f} t_up={t1:.3f}")
elif verb == "cycle":
    t, n = sys.argv[3], int(sys.argv[4])
    for k in range(n):
        t0 = time.time(); send(s, multi(slot, 0, 0)); send(s, arrival(slot, CTYPE[t])); send(s, multi(slot, 0, 1 << slot))
        time.sleep(1.5)
        ns = nodes()
        nh = sum(1 for x in ns if x.startswith("hidraw")); ne = sum(1 for x in ns if x.startswith("event")); nj = sum(1 for x in ns if x.startswith("js"))
        uq = sorted({x.split("uniq=")[1].rstrip(")") for x in ns if "uniq=" in x and not x.endswith("uniq=-)")})
        print(f"cycle k={k} t={t0:.3f} hidraw={nh} event={ne} js={nj} uniq={','.join(uq) or '-'}", flush=True)
else:
    sys.exit(__doc__)
