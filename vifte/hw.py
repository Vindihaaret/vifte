"""Maskinvarelag: hwmon (sysfs) og NVIDIA (NVML).

Hver sensor/kontroll får en identifikator i samme format som
LibreHardwareMonitor / FanControl på Windows (f.eks. "/lpc/nct6799d/control/1",
"/amdcpu/0/temperature/2", "NVApiWrapper/0-AD103-A/sensor/0"), slik at
konfigurasjonsfiler kan brukes på begge operativsystemer.
"""
import ctypes as C
import glob
import logging
import os
import re

log = logging.getLogger("vifte.hw")

TEMP, FAN, CONTROL = "temperature", "fan", "control"


class Sensor:
    """En lesbar verdi: temperatur (°C) eller vifte (RPM)."""

    def __init__(self, ident, name, kind, hardware, reader, aliases=()):
        self.id = ident
        self.name = name
        self.kind = kind
        self.hardware = hardware
        self.reader = reader
        self.aliases = list(aliases)
        self.value = None

    def update(self):
        try:
            self.value = self.reader()
        except Exception:
            self.value = None
        return self.value


class Control:
    """En styrbar vifteutgang (0-100 %)."""

    def __init__(self, ident, name, hardware, backend, aliases=()):
        self.id = ident
        self.name = name
        self.kind = CONTROL
        self.hardware = hardware
        self.backend = backend
        self.aliases = list(aliases)
        self.value = None       # sist leste prosent
        self.applied = None     # sist skrevne prosent
        self.taken = False      # har vi tatt over styringen?

    def update(self):
        try:
            self.value = self.backend.read()
        except Exception:
            self.value = None
        return self.value

    def set(self, percent, force=False):
        percent = max(0.0, min(100.0, float(percent)))
        if not self.taken:
            self.backend.take()
            self.taken = True
            force = True
        elif not self.backend.is_manual():
            # F.eks. etter hvilemodus: BIOS har tatt tilbake styringen.
            log.info("%s: manuell modus tapt, setter på nytt", self.id)
            self.backend.take()
            force = True
        if force or self.applied is None or abs(self.applied - percent) >= 0.5:
            self.backend.write(percent)
            self.applied = percent

    def release(self):
        if self.taken:
            try:
                self.backend.release()
            except Exception as e:
                log.warning("%s: kunne ikke frigi: %s", self.id, e)
            self.taken = False
            self.applied = None


# ---------------------------------------------------------------- hwmon


def _read(path):
    with open(path) as f:
        return f.read().strip()


def _write(path, value):
    with open(path, "w") as f:
        f.write(str(value))


class HwmonPwm:
    def __init__(self, base, n):
        self.pwm = f"{base}/pwm{n}"
        self.enable = f"{base}/pwm{n}_enable"
        self.has_enable = os.path.exists(self.enable)
        self.original_enable = None
        self.dry_run = False

    def read(self):
        return int(_read(self.pwm)) * 100.0 / 255.0

    def is_manual(self):
        if not self.has_enable or self.dry_run:
            return True
        return _read(self.enable) == "1"

    def take(self):
        if self.has_enable and self.original_enable is None:
            self.original_enable = _read(self.enable)
        if self.dry_run:
            log.info("[tørrkjøring] %s = 1", self.enable)
        elif self.has_enable:
            _write(self.enable, 1)

    def write(self, percent):
        raw = int(round(percent * 255 / 100))
        if self.dry_run:
            log.info("[tørrkjøring] %s = %d (%.0f %%)", self.pwm, raw, percent)
        else:
            _write(self.pwm, raw)

    def release(self):
        if self.dry_run:
            log.info("[tørrkjøring] frigir %s", self.pwm)
            return
        if self.has_enable:
            # Tilbake til det BIOS/driveren hadde (vanligvis automatisk).
            orig = self.original_enable
            try:
                _write(self.enable, orig if orig not in (None, "1") else "2")
                return
            except OSError as e:
                log.warning("%s: kunne ikke gå tilbake til automatisk (%s), setter 100 %%", self.enable, e)
        _write(self.pwm, 255)


def _lhm_prefix(name, idx):
    """hwmon-navn -> LibreHardwareMonitor maskinvare-prefiks."""
    m = re.fullmatch(r"nct(\d{4})", name)
    if m:
        suffix = "f" if m.group(1) in ("6771", "6775", "6776") else "d"
        return f"/lpc/{name}{suffix}"
    if re.fullmatch(r"it\d{4}", name):
        return f"/lpc/{name}e"
    return {
        "k10temp": f"/amdcpu/{idx}",
        "zenpower": f"/amdcpu/{idx}",
        "coretemp": f"/intelcpu/{idx}",
        "amdgpu": f"/gpu-amd/{idx}",
        "nvme": f"/nvme/{idx}",
        "drivetemp": f"/hdd/{idx}",
    }.get(name, f"/hwmon/{name}/{idx}")


HW_TITLES = {
    "k10temp": "AMD CPU", "zenpower": "AMD CPU", "coretemp": "Intel CPU",
    "amdgpu": "AMD GPU", "nvme": "NVMe", "spd5118": "RAM", "jc42": "RAM",
    "asusec": "ASUS EC", "asus_wmi_sensors": "ASUS WMI", "corsairpsu": "Corsair PSU",
}


def _hw_title(name, idx, base):
    if re.fullmatch(r"nct\d{4}|it\d{4}", name):
        return f"Hovedkort ({name.upper()})"
    t = HW_TITLES.get(name, name)
    if name == "nvme":
        try:
            model = _read(f"{base}/device/model")
            return f"NVMe {idx} ({model})"
        except OSError:
            pass
    return f"{t} {idx}" if idx else t


def scan_hwmon(dry_run=False):
    sensors, controls = [], []
    chips = []
    for base in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            name = _read(f"{base}/name")
        except OSError:
            continue
        dev = os.path.realpath(f"{base}/device") if os.path.exists(f"{base}/device") else base
        chips.append((name, dev, base))
    chips.sort(key=lambda c: (c[0], c[1]))
    counter = {}
    for name, _dev, base in chips:
        idx = counter.get(name, 0)
        counter[name] = idx + 1
        prefix = _lhm_prefix(name, idx)
        hw = _hw_title(name, idx, base)
        files = os.listdir(base)

        def label(kind, n, default, base=base):
            try:
                return _read(f"{base}/{kind}{n}_label")
            except OSError:
                return default

        temps = sorted(int(m.group(1)) for f in files if (m := re.fullmatch(r"temp(\d+)_input", f)))
        ccds = []
        for n in temps:
            path = f"{base}/temp{n}_input"
            reader = (lambda p=path: int(_read(p)) / 1000.0)
            lab = label("temp", n, f"Temperature #{n}")
            ident, sname = f"{prefix}/temperature/{n - 1}", lab
            if name in ("k10temp", "zenpower"):
                if lab in ("Tctl", "Tdie") and not any(s.id == f"{prefix}/temperature/2" for s in sensors):
                    ident, sname = f"{prefix}/temperature/2", "Core (Tctl/Tdie)"
                elif (m := re.fullmatch(r"Tccd(\d+)", lab)):
                    k = int(m.group(1))
                    ident, sname = f"{prefix}/temperature/{2 + k}", f"CCD{k} (Tdie)"
                    ccds.append(reader)
                else:
                    ident = f"{prefix}/temperature/{20 + n}"
            sensors.append(Sensor(ident, sname, TEMP, hw, reader,
                                  aliases=[f"hwmon:{name}/{idx}/temp{n}"]))
        if ccds:
            sensors.append(Sensor(f"{prefix}/temperature/9", "CCDs Max (Tdie)", TEMP, hw,
                                  lambda r=ccds: max(f() for f in r)))
            sensors.append(Sensor(f"{prefix}/temperature/10", "CCDs Average (Tdie)", TEMP, hw,
                                  lambda r=ccds: sum(f() for f in r) / len(r)))

        for n in sorted(int(m.group(1)) for f in files if (m := re.fullmatch(r"fan(\d+)_input", f))):
            path = f"{base}/fan{n}_input"
            sensors.append(Sensor(f"{prefix}/fan/{n - 1}", label("fan", n, f"Fan #{n}"), FAN, hw,
                                  (lambda p=path: float(_read(p))),
                                  aliases=[f"hwmon:{name}/{idx}/fan{n}"]))

        for n in sorted(int(m.group(1)) for f in files if (m := re.fullmatch(r"pwm(\d+)", f))):
            path = f"{base}/pwm{n}"
            st = os.stat(path)
            if not st.st_mode & 0o222:
                continue  # skrivebeskyttet (f.eks. corsair-psu)
            be = HwmonPwm(base, n)
            be.dry_run = dry_run
            controls.append(Control(f"{prefix}/control/{n - 1}", label("pwm", n, f"Fan #{n}"), hw, be,
                                    aliases=[f"hwmon:{name}/{idx}/pwm{n}"]))
    return sensors, controls


# ---------------------------------------------------------------- NVIDIA / NVML

# PCI device-id -> GPU-brikke (brukes i NVApiWrapper-identifikatoren)
NV_CHIPS = [
    ((0x2B80, 0x2BFF), "GB202"), ((0x2C00, 0x2C7F), "GB203"), ((0x2C80, 0x2CFF), "GB205"),
    ((0x2D00, 0x2D7F), "GB206"), ((0x2D80, 0x2DFF), "GB207"),
    ((0x2680, 0x26FF), "AD102"), ((0x2700, 0x277F), "AD103"), ((0x2780, 0x27FF), "AD104"),
    ((0x2800, 0x287F), "AD106"), ((0x2880, 0x28FF), "AD107"),
    ((0x2200, 0x223F), "GA102"), ((0x2400, 0x247F), "GA103"), ((0x2480, 0x24FF), "GA104"),
    ((0x2500, 0x257F), "GA106"), ((0x2580, 0x25FF), "GA107"),
    ((0x1E00, 0x1E7F), "TU102"), ((0x1E80, 0x1EFF), "TU104"), ((0x1F00, 0x1F7F), "TU106"),
    ((0x2180, 0x21FF), "TU116"), ((0x1F80, 0x1FFF), "TU117"),
]


class _PciInfo(C.Structure):
    _fields_ = [("busIdLegacy", C.c_char * 16), ("domain", C.c_uint), ("bus", C.c_uint),
                ("device", C.c_uint), ("pciDeviceId", C.c_uint), ("pciSubSystemId", C.c_uint),
                ("busId", C.c_char * 32)]


class _FanSpeedInfo(C.Structure):
    _fields_ = [("version", C.c_uint), ("fan", C.c_uint), ("speed", C.c_uint)]


class Nvml:
    lib = None

    @classmethod
    def load(cls):
        if cls.lib is None:
            lib = C.CDLL("libnvidia-ml.so.1")
            if lib.nvmlInit_v2() != 0:
                raise OSError("nvmlInit feilet")
            cls.lib = lib
        return cls.lib


class NvFan:
    def __init__(self, handle, fan, minimum):
        self.h, self.fan, self.min = handle, fan, minimum
        self.dry_run = False
        self.manual = False

    def read(self):
        v = C.c_uint()
        if Nvml.lib.nvmlDeviceGetFanSpeed_v2(self.h, self.fan, C.byref(v)) != 0:
            return None
        return float(v.value)

    def is_manual(self):
        return True

    def take(self):
        self.manual = True

    def write(self, percent):
        if self.dry_run:
            log.info("[tørrkjøring] NVIDIA vifte %d = %.0f %%", self.fan, percent)
            return
        if percent <= 0:
            # Som i FanControl: 0 % gir styringen tilbake til kortet (0 RPM-modus).
            Nvml.lib.nvmlDeviceSetDefaultFanSpeed_v2(self.h, self.fan)
            return
        p = int(round(max(percent, self.min)))
        r = Nvml.lib.nvmlDeviceSetFanSpeed_v2(self.h, self.fan, p)
        if r != 0:
            raise OSError(f"nvmlDeviceSetFanSpeed_v2 feilet ({r}), kjører som root?")

    def release(self):
        if not self.dry_run:
            Nvml.lib.nvmlDeviceSetDefaultFanSpeed_v2(self.h, self.fan)


def scan_nvidia(dry_run=False):
    sensors, controls = [], []
    try:
        lib = Nvml.load()
    except OSError:
        return sensors, controls
    count = C.c_uint()
    lib.nvmlDeviceGetCount_v2(C.byref(count))
    for i in range(count.value):
        h = C.c_void_p()
        if lib.nvmlDeviceGetHandleByIndex_v2(i, C.byref(h)) != 0:
            continue
        buf = C.create_string_buffer(96)
        lib.nvmlDeviceGetName(h, buf, 96)
        gpu = buf.value.decode(errors="replace")
        pci = _PciInfo()
        chip = "GPU"
        if lib.nvmlDeviceGetPciInfo_v3(h, C.byref(pci)) == 0:
            dev = pci.pciDeviceId >> 16
            chip = next((c for (lo, hi), c in NV_CHIPS if lo <= dev <= hi), f"{dev:04X}")
        prefix = f"NVApiWrapper/{i}-{chip}-A"
        alt = [f"/gpu-nvidia/{i}"]

        def temp(h=h):
            v = C.c_uint()
            if lib.nvmlDeviceGetTemperature(h, 0, C.byref(v)) != 0:
                return None
            return float(v.value)

        sensors.append(Sensor(f"{prefix}/sensor/0", "GPU", TEMP, gpu, temp,
                              aliases=[f"{alt[0]}/temperature/0"]))
        nf = C.c_uint()
        lib.nvmlDeviceGetNumFans(h, C.byref(nf))
        mn, mx = C.c_uint(30), C.c_uint(100)
        lib.nvmlDeviceGetMinMaxFanSpeed(h, C.byref(mn), C.byref(mx))
        for f in range(nf.value):
            def rpm(h=h, f=f):
                info = _FanSpeedInfo(C.sizeof(_FanSpeedInfo) | (1 << 24), f, 0)
                if lib.nvmlDeviceGetFanSpeedRPM(h, C.byref(info)) != 0:
                    return None
                return float(info.speed)

            sensors.append(Sensor(f"{prefix}/fan/{f}", f"Fan {f + 1} - {gpu}", FAN, gpu, rpm,
                                  aliases=[f"{alt[0]}/fan/{f}"]))
            be = NvFan(h, f, mn.value)
            be.dry_run = dry_run
            controls.append(Control(f"{prefix}/control/{f}", f"Control {f + 1} - {gpu}", gpu, be,
                                    aliases=[f"{alt[0]}/control/{f}"]))
    return sensors, controls


def scan(dry_run=False):
    sensors, controls = scan_hwmon(dry_run)
    s2, c2 = scan_nvidia(dry_run)
    return sensors + s2, controls + c2
