#!/usr/bin/env python3
"""Consumer-side probe for virtual Wolf pads: libudev, evdev, hidraw and SDL2 reads, plus PS-type pad output reports.
Run INSIDE the Wolf app container (needs python3, libudev.so.1 and libSDL2):
  podman exec -i APP python3 -I - VERB [ARGS] < probe.py
  enum TYPE   /run/udev/data listing; libudev enumeration of subsystem "input" (what an app that starts after the pad
              was plugged sees); the Chromium test per node (an evdev or joydev node without ID_INPUT_JOYSTICK is
              ignored); SDL2 joystick enumeration.
  monitor S [SUB]  libudev monitor on the "udev" netlink group, subsystem SUB (default input), for S seconds.
  sdl S       SDL2 joystick subsystem initialised BEFORE the plug, polls events for S seconds; per added device: name,
              path, GUID (byte 14 = 'h' is SDL's HIDAPI driver), gamecontroller flag.
  read S      read every Wolf pad evdev node and PS-type pad hidraw node for S seconds, one line per EV_KEY event / per
              hidraw report whose button bytes change (8..10 in USB report 0x01, 9..11 in BT report 0x31).
  open        try to open each /dev/hidraw*, print the result (errno name as returned).
  motion S    for S seconds: the PS-type pad hidraw input report's gyro[3] and accel[3] (int16 LE at report offset 16 and
              22 for USB report 0x01, 17 and 23 for BT 0x31); and the pad's evdev "Motion Sensors" node (ABS_X..Z accel,
              ABS_RX..RZ gyro, as hid-playstation scales them); one line per change.
  out GAP NAME...  write PS-type pad USB output reports (id 0x02, 63 bytes) to the Wolf hidraw node, GAP seconds apart;
              NAME from OUT below; one line per write with the wall time.
  sdlsensor S  SDL2 game controller 0 with gyro and accel enabled; S seconds of SDL_GameControllerGetSensorData, one
              line per change (SDL units: rad/s, m/s^2).
  touch S     for S seconds: the PS-type pad hidraw input report's two touch points (offset 33 for USB 0x01, 34 for BT
              0x31; byte 0 = id:7 | contact:1, contact 1 = no touch), one line per change; and the pad's evdev
              "Touchpad" node read back after every SYN_REPORT with EVIOCGMTSLOTS (ABS_MT_TRACKING_ID per slot, -1 = no
              contact), ABS_MT_POSITION_X/Y and EVIOCGKEY BTN_TOUCH, one line per change; a final state line at the end.
  ident HIDAPI  SDL2 with SDL_JOYSTICK_HIDAPI=HIDAPI (1|0) set before SDL_Init: per joystick, name, path, GUID, the SDL
              driver that claims it (GUID byte 14: 'h' HIDAPI, 0 Linux evdev), SDL_GetJoystickGUIDInfo
              vendor/product/version/crc16, device vendor/product/version, controller type/name, serial,
              SDL_GameControllerMappingForGUID and ...ForDeviceIndex; then MappingForGUID for GUID variants (bus 03,
              version 0x8100, version 0) to show which database entry each would hit.
  sysid       sysfs and libudev identity of each Wolf PS-type pad input device and hidraw node: name, uniq, phys,
              id/bustype/vendor/product/version, the kernel HID driver bound, udev NAME/UNIQ/PHYS/PRODUCT.
enum prints only Wolf pad devices (parent input device name starts with "Wolf" and is not mouse/keyboard/touch);
monitor prints every input event with a devnode (only fake-udev messages reach the container's netns)."""
import ctypes, ctypes.util, os, select, struct, sys, time

def outrep(f0=0, f1=0, f2=0, mr=0, ml=0, rt=0, rp=(), lt=0, lp=(), rgb=(0, 0, 0)):
    """dualsense_output_report_usb: id, then dualsense_output_report_common at byte 1: valid_flag0 1, valid_flag1 2,
    motor_right 3, motor_left 4, reserved[4] 5-8, mute_button_led 9, power_save_control 10, right_trigger_effect_type 11,
    right_trigger_effect[10] 12-21, left_trigger_effect_type 22, left_trigger_effect[10] 23-32, reserved2[6] 33-38,
    valid_flag2 39, reserved3[2] 40-41, lightbar_setup 42, led_brightness 43, player_leds 44, lightbar r g b 45-47;
    reserved[15] 48-62 (63 bytes = the kernel's DS_OUTPUT_REPORT_USB_SIZE)."""
    b = bytearray(63); b[0] = 0x02; b[1], b[2], b[3], b[4] = f0, f1, mr, ml
    b[11] = rt; b[12:12 + len(rp)] = bytes(rp); b[22] = lt; b[23:23 + len(lp)] = bytes(lp); b[39] = f2; b[45:48] = bytes(rgb)
    return bytes(b)

# FLAG0: MOTOR_OR_COMPATIBLE_VIBRATION 0x01, RIGHT_TRIGGER_EFFECT 0x04, LEFT_TRIGGER_EFFECT 0x08; FLAG1: LIGHTBAR_ENABLE 0x04.
# inputtino keys trigger de-dup on the byte sum of the 10 param bytes only (not the type): the C* rows collide on purpose.
OUT = {
    "Z_R": outrep(f0=0x04, rt=0x05, rp=[0] * 10),               # first right effect on a fresh pad: "off", params sum 0
    "T1L": outrep(f0=0x08, lt=0x01, lp=[0x40, 0xa0]),           # left: type 1, params sum 224
    "T1R": outrep(f0=0x04, rt=0x02, rp=[0x20, 0x80]),           # right: type 2, params sum 160
    "C1_L": outrep(f0=0x08, lt=0x01, lp=[0xa0, 0x40]),          # left: same type, bytes swapped, sum 224
    "C2_L": outrep(f0=0x08, lt=0x02, lp=[0xa0, 0x40]),          # left: other type, same bytes, sum 224
    "CTRL_L": outrep(f0=0x08, lt=0x01, lp=[0x40, 0xa1]),        # left: control, sum 225
    "C3_R": outrep(f0=0x04, rt=0x06, rp=[0x50, 0x50]),          # right: other type and bytes, sum 160
    "Z_R2": outrep(f0=0x04, rt=0x05, rp=[0] * 10),              # right: "off" again, sum 0 after 160
    "T1L_RE": outrep(f0=0x08, lt=0x01, lp=[0x40, 0xa0]),        # left: T1L again (sum 224 after 225), re-arms the cache
    "P_L": outrep(f0=0x08, lt=0x02, lp=[0x40, 0xa0]),           # left: type 2, params identical to T1L (type-only change)
    "L1": outrep(f1=0x04, rgb=(0x11, 0x22, 0x33)),              # lightbar
    "R1": outrep(f0=0x01, mr=0x80, ml=0x40),                    # rumble right 0x80 left 0x40
    "R0": outrep(),                                             # all flags 0: inputtino's rumble stop
}

def wolf_hidraw():
    import glob
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        if "HID_NAME=Wolf" in open(d + "/device/uevent").read(): return "/dev/" + os.path.basename(d)
    return None

if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"): sys.exit(__doc__)

L = ctypes.CDLL("libudev.so.1")
vp, cp = ctypes.c_void_p, ctypes.c_char_p
for f, res, args in [
    ("udev_new", vp, []), ("udev_enumerate_new", vp, [vp]), ("udev_enumerate_add_match_subsystem", ctypes.c_int, [vp, cp]),
    ("udev_enumerate_scan_devices", ctypes.c_int, [vp]), ("udev_enumerate_get_list_entry", vp, [vp]),
    ("udev_list_entry_get_next", vp, [vp]), ("udev_list_entry_get_name", cp, [vp]),
    ("udev_device_new_from_syspath", vp, [vp, cp]), ("udev_device_get_devnode", cp, [vp]),
    ("udev_device_get_sysname", cp, [vp]), ("udev_device_get_property_value", cp, [vp, cp]),
    ("udev_device_get_parent_with_subsystem_devtype", vp, [vp, cp, cp]), ("udev_device_get_sysattr_value", cp, [vp, cp]),
    ("udev_device_get_action", cp, [vp]), ("udev_device_unref", vp, [vp]),
    ("udev_monitor_new_from_netlink", vp, [vp, cp]), ("udev_monitor_filter_add_match_subsystem_devtype", ctypes.c_int, [vp, cp, cp]),
    ("udev_monitor_enable_receiving", ctypes.c_int, [vp]), ("udev_monitor_get_fd", ctypes.c_int, [vp]),
    ("udev_monitor_receive_device", vp, [vp]), ("udev_device_get_subsystem", cp, [vp])]:
    fn = getattr(L, f); fn.restype = res; fn.argtypes = args

def s(b): return b.decode() if b else "-"

def pad_name(d):
    p = L.udev_device_get_parent_with_subsystem_devtype(d, b"input", None)
    n = s(L.udev_device_get_sysattr_value(p, b"name")) if p else "-"
    return n

def is_pad(n): return n.startswith("Wolf") and not any(k in n.lower() for k in ("mouse", "keyboard", "touch", "tablet"))

def describe(d):
    node = s(L.udev_device_get_devnode(d))
    j = s(L.udev_device_get_property_value(d, b"ID_INPUT_JOYSTICK"))
    i = s(L.udev_device_get_property_value(d, b"ID_INPUT"))
    kind = "js" if node.startswith("/dev/input/js") else "event" if node.startswith("/dev/input/event") else "other"
    chromium = "taken" if kind in ("js", "event") and j != "-" else "ignored"
    return f"node={node} kind={kind} ID_INPUT={i} ID_INPUT_JOYSTICK={j} in_container={int(os.path.exists(node))} chromium={chromium}"

udev = L.udev_new()
verb = sys.argv[1]
if verb == "enum":
    data = sorted(os.listdir("/run/udev/data")) if os.path.isdir("/run/udev/data") else []
    print("data_files=" + " ".join(data))
    for f in data:
        try: print(f"data[{f}]=" + open("/run/udev/data/" + f).read().replace("\n", "|"))
        except OSError as e: print(f"data[{f}]=ERR {e}")
    e = L.udev_enumerate_new(udev)
    L.udev_enumerate_add_match_subsystem(e, b"input")
    L.udev_enumerate_scan_devices(e)
    ent = L.udev_enumerate_get_list_entry(e)
    while ent:
        d = L.udev_device_new_from_syspath(udev, L.udev_list_entry_get_name(ent))
        if d and L.udev_device_get_devnode(d):
            n = pad_name(d)
            if is_pad(n): print(f"enum name={n.replace(' ', '_')} " + describe(d))
        if d: L.udev_device_unref(d)
        ent = L.udev_list_entry_get_next(ent)
    try:
        S = ctypes.CDLL("libSDL2-2.0.so.0")
        S.SDL_JoystickNameForIndex.restype = cp; S.SDL_JoystickPathForIndex.restype = cp; S.SDL_GetError.restype = cp
        rc = S.SDL_Init(0x200 | 0x2000)
        n = S.SDL_NumJoysticks()
        print(f"sdl2 init={rc} joysticks={n}" + ("" if rc == 0 else f" err={s(S.SDL_GetError())}"))
        for k in range(max(n, 0)):
            print(f"sdl2 [{k}] name={s(S.SDL_JoystickNameForIndex(k)).replace(' ', '_')} path={s(S.SDL_JoystickPathForIndex(k))} gamecontroller={S.SDL_IsGameController(k)}")
        S.SDL_Quit()
    except OSError as ex:
        print(f"sdl2 unavailable {ex}")
elif verb == "monitor":
    sub = (sys.argv[3] if len(sys.argv) > 3 else "input").encode()
    m = L.udev_monitor_new_from_netlink(udev, b"udev")
    L.udev_monitor_filter_add_match_subsystem_devtype(m, sub, None)
    L.udev_monitor_enable_receiving(m)
    fd = L.udev_monitor_get_fd(m)
    end = time.time() + float(sys.argv[2])
    print(f"monitor ready sub={sub.decode()}", flush=True)
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 1.0)
        if not r: continue
        d = L.udev_monitor_receive_device(m)
        if not d: continue
        n = pad_name(d)  # only fake-udev messages reach the container's netns; a removed node has no parent name
        if L.udev_device_get_devnode(d):
            print(f"{time.time():.3f} hotplug sub={s(L.udev_device_get_subsystem(d))} action={s(L.udev_device_get_action(d))} name={n.replace(' ', '_')} " + describe(d), flush=True)
        L.udev_device_unref(d)
elif verb == "sdl":
    S = ctypes.CDLL("libSDL2-2.0.so.0")
    S.SDL_JoystickNameForIndex.restype = cp; S.SDL_JoystickPathForIndex.restype = cp; S.SDL_GetError.restype = cp
    class GUID(ctypes.Structure): _fields_ = [("data", ctypes.c_uint8 * 16)]
    S.SDL_JoystickGetDeviceGUID.restype = GUID; S.SDL_GetRevision.restype = cp
    rc = S.SDL_Init(0x200 | 0x2000)
    print(f"{time.time():.3f} sdl ready init={rc} rev={s(S.SDL_GetRevision())} joysticks={S.SDL_NumJoysticks()}", flush=True)
    ev = (ctypes.c_uint8 * 64)()
    end = time.time() + float(sys.argv[2])
    count = 0
    while time.time() < end:
        S.SDL_JoystickUpdate()
        n = S.SDL_NumJoysticks()
        if n != count:
            print(f"{time.time():.3f} sdl count={n} " + " ".join(f"[{k}]{s(S.SDL_JoystickPathForIndex(k))}" for k in range(n)), flush=True); count = n
        while S.SDL_PollEvent(ctypes.byref(ev)):
            et = struct.unpack_from("<I", ev, 0)[0]; which = struct.unpack_from("<i", ev, 8)[0]
            if et == 0x605:
                g = bytes(S.SDL_JoystickGetDeviceGUID(which).data)
                print(f"{time.time():.3f} sdl added index={which} name={s(S.SDL_JoystickNameForIndex(which)).replace(' ', '_')} "
                      f"path={s(S.SDL_JoystickPathForIndex(which))} guid={g.hex()} hidapi={int(g[14] == 0x68)} "
                      f"gamecontroller={S.SDL_IsGameController(which)}", flush=True)
            elif et == 0x606:
                print(f"{time.time():.3f} sdl removed instance={which}", flush=True)
        time.sleep(0.02)
    S.SDL_Quit()
elif verb == "read":
    import glob
    fds = {}
    for d in sorted(glob.glob("/sys/class/input/event*")):
        n = open(d + "/device/name").read().strip() if os.path.exists(d + "/device/name") else ""
        if is_pad(n) or n.startswith("Wolf DualSense"):
            dev = "/dev/input/" + os.path.basename(d)
            try: fds[os.open(dev, os.O_RDONLY | os.O_NONBLOCK)] = (dev, n.replace(" ", "_"), "evdev")
            except OSError as e: print(f"open {dev} {n.replace(' ', '_')} ERR {e.strerror}")
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        ue = open(d + "/device/uevent").read()
        if "HID_NAME=Wolf" in ue:
            dev = "/dev/" + os.path.basename(d)
            try: fds[os.open(dev, os.O_RDONLY | os.O_NONBLOCK)] = (dev, "hidraw", "hidraw")
            except OSError as e: print(f"open {dev} hidraw ERR {e.strerror}")
    print(f"{time.time():.3f} read ready " + " ".join(f"{v[0]}={v[1]}" for v in fds.values()), flush=True)
    last = {}
    end = time.time() + float(sys.argv[2])
    while time.time() < end:
        r, _, _ = select.select(list(fds), [], [], 0.5)
        for fd in r:
            dev, n, kind = fds[fd]
            try: b = os.read(fd, 4096)
            except BlockingIOError: continue
            if kind == "evdev":
                for off in range(0, len(b) - len(b) % 24, 24):
                    _, _, t, c, v = struct.unpack_from("llHHi", b, off)
                    if t == 1: print(f"{time.time():.3f} key dev={dev} name={n} code={c} value={v}", flush=True)
            else:
                o = 9 if b and b[0] == 0x31 else 8  # BT report 0x31 carries one extra header byte before the common report
                k = b[o:o + 3].hex() if len(b) >= o + 3 else b.hex()
                if last.get(dev) != k:
                    print(f"{time.time():.3f} hid dev={dev} id={b[0] if b else -1} len={len(b)} btn={k}", flush=True); last[dev] = k
elif verb == "motion":
    import fcntl, glob
    fds = {}
    hr = wolf_hidraw()
    if hr: fds[os.open(hr, os.O_RDONLY | os.O_NONBLOCK)] = ("hid", hr)
    for d in sorted(glob.glob("/sys/class/input/event*")):
        n = open(d + "/device/name").read().strip() if os.path.exists(d + "/device/name") else ""
        if n.startswith("Wolf DualSense") and n.endswith("Motion Sensors"):
            dev = "/dev/input/" + os.path.basename(d)
            fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK); fds[fd] = ("evm", dev)
            ai = []
            for code in range(6):  # EVIOCGABS(code) = _IOR('E', 0x40 + code, struct input_absinfo[6 x s32])
                buf = bytearray(24); fcntl.ioctl(fd, 0x80184540 + code, buf); ai.append(struct.unpack("6i", buf))
            print("evm absinfo " + " ".join(f"c{c}=val{a[0]},min{a[1]},max{a[2]},res{a[5]}" for c, a in enumerate(ai)), flush=True)
    print(f"{time.time():.6f} motion ready " + " ".join(f"{v[0]}={v[1]}" for v in fds.values()), flush=True)
    last = {}; absv = [None] * 6
    end = time.time() + float(sys.argv[2])
    while time.time() < end:
        r, _, _ = select.select(list(fds), [], [], 0.2)
        for fd in r:
            kind, dev = fds[fd]
            try: b = os.read(fd, 4096)
            except BlockingIOError: continue
            if kind == "hid":
                o = 17 if b and b[0] == 0x31 else 16
                if len(b) < o + 12: continue
                g = struct.unpack_from("<3h", b, o); a = struct.unpack_from("<3h", b, o + 6)
                if last.get(dev) != (g, a):
                    print(f"{time.time():.6f} hid id={b[0]} len={len(b)} gyro={g[0]},{g[1]},{g[2]} accel={a[0]},{a[1]},{a[2]}", flush=True); last[dev] = (g, a)
            else:
                for off in range(0, len(b) - len(b) % 24, 24):
                    _, _, t, c, v = struct.unpack_from("llHHi", b, off)
                    if t == 3 and c < 6: absv[c] = v
                    elif t == 0 and last.get(dev) != tuple(absv):
                        last[dev] = tuple(absv)
                        print(f"{time.time():.6f} evm accel={absv[0]},{absv[1]},{absv[2]} gyro={absv[3]},{absv[4]},{absv[5]}", flush=True)
elif verb == "out":
    gap = float(sys.argv[2]); hr = wolf_hidraw()
    fd = os.open(hr, os.O_RDWR)
    print(f"{time.time():.6f} out ready dev={hr}", flush=True)
    for name in sys.argv[3:]:
        rep = OUT[name]; t = time.time()
        try: n = os.write(fd, rep); res = f"wrote={n}"
        except OSError as e: res = f"ERR={e.strerror}"
        print(f"out t={t:.6f} name={name} {res} hex={rep[:48].hex()}", flush=True)
        time.sleep(gap)
    os.close(fd)
elif verb == "sdlsensor":
    S = ctypes.CDLL("libSDL2-2.0.so.0")
    S.SDL_GameControllerOpen.restype = vp; S.SDL_GameControllerName.restype = cp; S.SDL_GameControllerName.argtypes = [vp]
    S.SDL_GameControllerHasSensor.argtypes = [vp, ctypes.c_int]; S.SDL_GameControllerSetSensorEnabled.argtypes = [vp, ctypes.c_int, ctypes.c_int]
    S.SDL_GameControllerGetSensorData.argtypes = [vp, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int]
    S.SDL_GameControllerGetSensorDataRate.restype = ctypes.c_float; S.SDL_GameControllerGetSensorDataRate.argtypes = [vp, ctypes.c_int]
    S.SDL_JoystickPathForIndex.restype = cp; S.SDL_GetError.restype = cp
    rc = S.SDL_Init(0x200 | 0x2000 | 0x8000)
    n = S.SDL_NumJoysticks()
    idx = next((k for k in range(n) if S.SDL_IsGameController(k)), -1)
    gc = S.SDL_GameControllerOpen(idx) if idx >= 0 else None
    if not gc: print(f"sdl init={rc} joysticks={n} open=fail err={s(S.SDL_GetError())}"); sys.exit()
    has = {k: S.SDL_GameControllerHasSensor(gc, v) for k, v in (("accel", 1), ("gyro", 2))}
    en = {k: S.SDL_GameControllerSetSensorEnabled(gc, v, 1) for k, v in (("accel", 1), ("gyro", 2))}
    print(f"{time.time():.6f} sdl ready name={s(S.SDL_GameControllerName(gc)).replace(' ', '_')} path={s(S.SDL_JoystickPathForIndex(idx))} "
          f"has={has} enable_rc={en} rate_gyro={S.SDL_GameControllerGetSensorDataRate(gc, 2):.1f}", flush=True)
    buf = (ctypes.c_float * 3)(); last = None
    end = time.time() + float(sys.argv[2])
    while time.time() < end:
        S.SDL_GameControllerUpdate()
        vals = []
        for v in (2, 1):
            rc = S.SDL_GameControllerGetSensorData(gc, v, buf, 3); vals.append((rc, tuple(round(x, 5) for x in buf)))
        if vals != last:
            print(f"{time.time():.6f} sdl gyro={','.join(str(x) for x in vals[0][1])} accel={','.join(str(x) for x in vals[1][1])} rc={vals[0][0]},{vals[1][0]}", flush=True); last = vals
        time.sleep(0.01)
    S.SDL_Quit()
elif verb == "touch":
    import fcntl, glob
    def gslots(fd, code, n=2):  # EVIOCGMTSLOTS(len) = _IOC(_IOC_READ, 'E', 0x0a, len); buf = u32 code + s32 values[n]
        buf = bytearray(struct.pack("I", code) + bytes(4 * n)); fcntl.ioctl(fd, (2 << 30) | (len(buf) << 16) | 0x450a, buf)
        return struct.unpack_from(f"{n}i", buf, 4)
    def gkey(fd, code):  # EVIOCGKEY(96)
        buf = bytearray(96); fcntl.ioctl(fd, 0x80604518, buf); return (buf[code >> 3] >> (code & 7)) & 1
    def evstate(fd):
        tid = gslots(fd, 0x39); x = gslots(fd, 0x35); y = gslots(fd, 0x36)
        return f"tid={tid[0]},{tid[1]} x={x[0]},{x[1]} y={y[0]},{y[1]} btn_touch={gkey(fd, 0x14a)} tool_finger={gkey(fd, 0x145)} doubletap={gkey(fd, 0x14d)}"
    def pts(b):
        o = 34 if b[0] == 0x31 else 33
        if len(b) < o + 8: return None
        out = []
        for k in range(2):
            p0, xl, xy, yh = b[o + 4 * k: o + 4 * k + 4]
            out.append(f"p{k}=contact{p0 >> 7},id{p0 & 0x7f},x{xl | (xy & 0x0f) << 8},y{(xy >> 4) | yh << 4}")
        return " ".join(out)
    fds = {}
    hr = wolf_hidraw()
    if hr: fds[os.open(hr, os.O_RDONLY | os.O_NONBLOCK)] = ("hid", hr)
    for d in sorted(glob.glob("/sys/class/input/event*")):
        n = open(d + "/device/name").read().strip() if os.path.exists(d + "/device/name") else ""
        if n.startswith("Wolf DualSense") and n.endswith("Touchpad"):
            dev = "/dev/input/" + os.path.basename(d)
            fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK); fds[fd] = ("evt", dev)
            buf = bytearray(24); fcntl.ioctl(fd, 0x80184540 + 0x2f, buf); a = struct.unpack("6i", buf)
            print(f"evt absinfo ABS_MT_SLOT min{a[1]},max{a[2]} name={n.replace(' ', '_')}", flush=True)
            print(f"{time.time():.6f} evt start {evstate(fd)}", flush=True)
    print(f"{time.time():.6f} touch ready " + " ".join(f"{v[0]}={v[1]}" for v in fds.values()), flush=True)
    last = {}
    end = time.time() + float(sys.argv[2])
    while time.time() < end:
        r, _, _ = select.select(list(fds), [], [], 0.2)
        for fd in r:
            kind, dev = fds[fd]
            try: b = os.read(fd, 4096)
            except BlockingIOError: continue
            if kind == "hid":
                v = pts(b) if b else None
                if v and last.get(dev) != v:
                    print(f"{time.time():.6f} hid id={b[0]} len={len(b)} {v}", flush=True); last[dev] = v
            else:
                if any(struct.unpack_from("llHHi", b, off)[2] == 0 for off in range(0, len(b) - len(b) % 24, 24)):
                    v = evstate(fd)
                    if last.get(dev) != v:
                        print(f"{time.time():.6f} evt {v}", flush=True); last[dev] = v
    for fd, (kind, dev) in fds.items():
        print(f"{time.time():.6f} final {kind} " + (evstate(fd) if kind == "evt" else last.get(dev, "-")), flush=True)
elif verb == "open":
    import errno, glob
    for dev in sorted(glob.glob("/dev/hidraw*")):
        try: os.close(os.open(dev, os.O_RDWR)); print(f"open {dev} ok")
        except OSError as e: print(f"open {dev} {errno.errorcode.get(e.errno, e.errno)} {e.strerror}")
elif verb == "ident":
    os.environ["SDL_JOYSTICK_HIDAPI"] = sys.argv[2]
    S = ctypes.CDLL("libSDL2-2.0.so.0")
    class GUID(ctypes.Structure): _fields_ = [("data", ctypes.c_uint8 * 16)]
    u16 = ctypes.c_uint16
    for f, res, args in [("SDL_JoystickNameForIndex", cp, [ctypes.c_int]), ("SDL_JoystickPathForIndex", cp, [ctypes.c_int]),
                         ("SDL_GetError", cp, []), ("SDL_GetRevision", cp, []), ("SDL_GetHint", cp, [cp]),
                         ("SDL_JoystickGetDeviceGUID", GUID, [ctypes.c_int]),
                         ("SDL_JoystickGetDeviceVendor", u16, [ctypes.c_int]), ("SDL_JoystickGetDeviceProduct", u16, [ctypes.c_int]),
                         ("SDL_JoystickGetDeviceProductVersion", u16, [ctypes.c_int]), ("SDL_JoystickGetDeviceType", ctypes.c_int, [ctypes.c_int]),
                         ("SDL_GameControllerTypeForIndex", ctypes.c_int, [ctypes.c_int]), ("SDL_GameControllerNameForIndex", cp, [ctypes.c_int]),
                         ("SDL_GameControllerMappingForGUID", vp, [GUID]), ("SDL_GameControllerMappingForDeviceIndex", vp, [ctypes.c_int]),
                         ("SDL_GetJoystickGUIDInfo", None, [GUID, ctypes.POINTER(u16), ctypes.POINTER(u16), ctypes.POINTER(u16), ctypes.POINTER(u16)]),
                         ("SDL_GameControllerOpen", vp, [ctypes.c_int]), ("SDL_GameControllerMapping", vp, [vp]),
                         ("SDL_GameControllerGetSerial", cp, [vp]), ("SDL_GameControllerClose", None, [vp]), ("SDL_free", None, [vp])]:
        fn = getattr(S, f); fn.restype = res; fn.argtypes = args
    def cstr(ptr):
        if not ptr: return "NULL"
        v = ctypes.string_at(ptr).decode(); S.SDL_free(ptr); return v
    def mapping_for(g):
        m = cstr(S.SDL_GameControllerMappingForGUID(g))
        return m.replace(" ", "_")
    def info(g):
        v, p, ver, crc = u16(), u16(), u16(), u16()
        S.SDL_GetJoystickGUIDInfo(g, ctypes.byref(v), ctypes.byref(p), ctypes.byref(ver), ctypes.byref(crc))
        return f"{v.value:04x}:{p.value:04x} ver={ver.value:04x} crc16={crc.value:04x}"
    rc = S.SDL_Init(0x200 | 0x2000)
    end = time.time() + 4
    while time.time() < end and S.SDL_NumJoysticks() < 1:
        S.SDL_JoystickUpdate(); time.sleep(0.05)
    time.sleep(0.5); S.SDL_JoystickUpdate()
    n = S.SDL_NumJoysticks()
    print(f"ident hidapi_env={sys.argv[2]} init={rc} rev={s(S.SDL_GetRevision())} joysticks={n}", flush=True)
    for k in range(max(n, 0)):
        g = S.SDL_JoystickGetDeviceGUID(k); b = bytes(g.data)
        drv = {0x68: "HIDAPI", 0x76: "virtual"}.get(b[14], "linux-evdev" if b[14] == 0 else f"0x{b[14]:02x}")
        print(f"ident[{k}] name={s(S.SDL_JoystickNameForIndex(k)).replace(' ', '_')} path={s(S.SDL_JoystickPathForIndex(k))} "
              f"guid={b.hex()} sdl_driver={drv} guidinfo={info(g)} dev={S.SDL_JoystickGetDeviceVendor(k):04x}:"
              f"{S.SDL_JoystickGetDeviceProduct(k):04x} devver={S.SDL_JoystickGetDeviceProductVersion(k):04x} "
              f"jstype={S.SDL_JoystickGetDeviceType(k)} gctype={S.SDL_GameControllerTypeForIndex(k)} "
              f"gcname={s(S.SDL_GameControllerNameForIndex(k)).replace(' ', '_')} gc={S.SDL_IsGameController(k)}", flush=True)
        print(f"ident[{k}] map_for_guid_before_open={mapping_for(g)}", flush=True)
        print(f"ident[{k}] map_for_device_index={cstr(S.SDL_GameControllerMappingForDeviceIndex(k)).replace(' ', '_')}", flush=True)
        gc = S.SDL_GameControllerOpen(k)
        if gc:
            print(f"ident[{k}] opened serial={s(S.SDL_GameControllerGetSerial(gc))} map_open={cstr(S.SDL_GameControllerMapping(gc)).replace(' ', '_')}", flush=True)
            S.SDL_GameControllerClose(gc)
        else:
            print(f"ident[{k}] open=fail err={s(S.SDL_GetError())}", flush=True)
        for tag, bus, ver in [("bus03", 0x03, None), ("v8100", None, 0x8100), ("v0000", None, 0x0000), ("bus03_v8111", 0x03, 0x8111), ("crc0", None, None)]:
            if b[14] != 0: continue  # variants only make sense for the plain (database) GUID form
            v = bytearray(b); v[2] = v[3] = 0
            if bus is not None: v[0:2] = bus.to_bytes(2, "little")
            if ver is not None: v[12:14] = ver.to_bytes(2, "little")
            gv = GUID(); gv.data[:] = list(v)
            print(f"ident[{k}] variant={tag} guid={bytes(v).hex()} map_for_guid={mapping_for(gv)}", flush=True)
    S.SDL_Quit()
elif verb == "sysid":
    import glob
    def rd(path):
        try: return open(path).read().strip().replace(" ", "_") or "-"
        except OSError as e: return f"ERR:{e.strerror.replace(' ', '_')}"
    for d in sorted(glob.glob("/sys/class/input/input*"), key=lambda x: int(x.rsplit("input", 1)[1])):
        n = rd(d + "/name")
        if not n.startswith("Wolf_DualSense"): continue
        ids = " ".join(f"{k}={rd(d + '/id/' + k)}" for k in ("bustype", "vendor", "product", "version"))
        hs = " ".join(sorted(os.path.basename(x) for x in glob.glob(d + "/event*") + glob.glob(d + "/js*")))
        drv = os.path.basename(os.path.realpath(d + "/device/driver")) if os.path.exists(d + "/device/driver") else "-"
        print(f"sysid input={os.path.basename(d)} name={n} uniq={rd(d + '/uniq')} phys={rd(d + '/phys')} {ids} handlers={hs.replace(' ', ',')} hid_driver={drv}")
        ud = L.udev_device_new_from_syspath(udev, d.encode())
        if ud:
            props = " ".join(f"udev_{k}={s(L.udev_device_get_property_value(ud, k.encode())).replace(' ', '_')}" for k in ("NAME", "UNIQ", "PHYS", "PRODUCT", "ID_INPUT_JOYSTICK"))
            print(f"sysid input={os.path.basename(d)} {props} udev_sysattr_name={s(L.udev_device_get_sysattr_value(ud, b'name')).replace(' ', '_')} udev_sysattr_uniq={s(L.udev_device_get_sysattr_value(ud, b'uniq'))}")
            L.udev_device_unref(ud)
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        ue = open(d + "/device/uevent").read()
        if "HID_NAME=Wolf" not in ue: continue
        kv = dict(l.split("=", 1) for l in ue.splitlines() if "=" in l)
        print(f"sysid hidraw={os.path.basename(d)} " + " ".join(f"{k}={kv.get(k, '-').replace(' ', '_')}" for k in ("DRIVER", "HID_ID", "HID_NAME", "HID_UNIQ", "HID_PHYS")))
else:
    sys.exit(__doc__)
