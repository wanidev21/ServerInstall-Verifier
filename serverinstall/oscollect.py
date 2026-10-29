"""OS 쪽 정보 수집 → 공통 형식.

    {host, os_name, kernel, product, serial, uuid,
     cpus: [{socket, model}], threads, mem_total_gib,
     dimms: [{slot, size_gib, speed, configured_speed, manufacturer, part}],
     pci: [{addr, cls_code, cls, name, slot, cap_speed, cap_width, sta_speed, sta_width, driver}],
     nics: [{name, mac, driver, bus, link, speed, ipv4}],
     disks: [{name, size_bytes, model, vendor, path, transport}],
     kernel_log: [줄], time: {utc, ntp_sync, tz}, failed_units: [이름], missing: [못 읽은 명령]}
"""

import re
from datetime import datetime, timezone


def collect(src):
    o = {"source": src.label}
    osr = src.read("/etc/os-release") or ""
    m = re.search(r'^PRETTY_NAME="?([^"\n]*)', osr, re.M)
    o["os_name"] = (m.group(1) if m else (src.read("/etc/redhat-release") or "").strip()) or "?"
    ver = src.read("/proc/version") or ""
    m = (re.search(r"Linux version (\S+)", ver)
         or re.match(r"Linux \S+ (\S+)", src.cmd("uname") or ""))
    o["kernel"] = m.group(1) if m else ""
    o["host"] = ((src.read("/etc/hostname") or src.read("/proc/sys/kernel/hostname") or "").strip()
                 .split(".")[0])

    cpuinfo = src.read("/proc/cpuinfo")
    if not cpuinfo:
        src.missing.append("/proc/cpuinfo")
    _cpu(o, cpuinfo or "")
    mem = re.search(r"^MemTotal:\s+(\d+) kB", src.read("/proc/meminfo") or "", re.M)
    o["mem_total_gib"] = int(mem.group(1)) / 1024.0 / 1024 if mem else None
    if not mem:
        src.missing.append("/proc/meminfo")
    _dmi(o, src.cmd("dmidecode"))
    o["pci"] = parse_lspci(src.cmd("lspci") or "")
    o["nics"] = src.net_devices()
    o["disks"] = src.block_devices()
    for d in o["disks"]:
        d["transport"] = disk_transport(d)
    o["kernel_log"] = (src.cmd("dmesg") or "").splitlines()
    o["time"] = _time(src)
    o["failed_units"] = re.findall(r"^\W*(\S+\.(?:service|mount|socket|target|device|timer))\s+loaded\s+failed",
                                   src.cmd("failed_units") or "", re.M)
    o["missing"] = list(src.missing)
    return o


def _cpu(o, text):
    sockets, threads = {}, 0
    for block in text.split("\n\n"):
        if "processor" not in block:
            continue
        threads += 1
        pid = re.search(r"^physical id\s*: (\d+)", block, re.M)
        model = re.search(r"^model name\s*: (.*)$", block, re.M)
        sockets[pid.group(1) if pid else "0"] = model.group(1).strip() if model else ""
    o["cpus"] = [{"socket": k, "model": v} for k, v in sorted(sockets.items())]
    o["threads"] = threads


def _dmi(o, text):
    o["product"] = o["serial"] = o["uuid"] = ""
    o["dimms"] = []
    if not text:
        o["dimms"] = None  # dmidecode 를 못 읽음 (root 아님 등)
        return
    for block in re.split(r"\n(?=Handle 0x)", text):
        f = dict((k.strip(), v.strip()) for k, v in re.findall(r"^\t([^\t:][^:]*): (.*)$", block, re.M))
        if "\nSystem Information" in block:
            o["product"], o["serial"], o["uuid"] = f.get("Product Name", ""), f.get("Serial Number", ""), f.get("UUID", "")
        elif "\nMemory Device" in block:
            size = f.get("Size", "")
            m = re.match(r"(\d+) (MB|GB|TB)", size)
            if not m:
                continue  # No Module Installed
            gib = int(m.group(1)) * {"MB": 1 / 1024.0, "GB": 1, "TB": 1024}[m.group(2)]
            o["dimms"].append({
                "slot": f.get("Locator", ""), "size_gib": gib,
                "speed": _mts(f.get("Speed")), "configured_speed": _mts(f.get("Configured Memory Speed")
                                                                         or f.get("Configured Clock Speed")),
                "manufacturer": f.get("Manufacturer", ""), "part": f.get("Part Number", "").strip(),
            })


def _mts(v):
    m = re.match(r"(\d+)", v or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- PCIe

LSPCI_HEAD = re.compile(r"^([0-9a-f]{2,4}:[0-9a-f]{2}:[0-9a-f]{2}\.\d|[0-9a-f]{2}:[0-9a-f]{2}\.\d) (.+?) \[([0-9a-f]{4})\]: (.*)$")


def parse_lspci(text):
    out = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip("\n").splitlines()
        if not lines:
            continue
        m = LSPCI_HEAD.match(lines[0])
        if not m:
            continue
        body = "\n".join(lines[1:])
        cap = re.search(r"LnkCap:.*?Speed ([\d.]+)GT/s.*?Width x(\d+)", body)
        sta = re.search(r"LnkSta:\s*Speed ([\d.]+)GT/s[^,]*, Width x(\d+)", body)
        drv = re.search(r"Kernel driver in use: (\S+)", body)
        slot = re.search(r"Physical Slot: (\S+)", body)
        ids = re.findall(r"\[([0-9a-f]{4}):([0-9a-f]{4})\]", m.group(4))
        name = re.sub(r" \[[0-9a-f]{4}:[0-9a-f]{4}\]", "", m.group(4))
        out.append({
            "addr": m.group(1), "dev": m.group(1).rsplit(".", 1)[0],
            "vid": ids[-1][0] if ids else "", "did": ids[-1][1] if ids else "",
            "cls": m.group(2), "cls_code": m.group(3), "name": re.sub(r" \((rev \w+|prog-if [^)]*\)?)\)", "", name).strip(),
            "slot": slot.group(1) if slot else "",
            "cap_speed": float(cap.group(1)) if cap else None, "cap_width": int(cap.group(2)) if cap else None,
            "sta_speed": float(sta.group(1)) if sta else None, "sta_width": int(sta.group(2)) if sta else None,
            "driver": drv.group(1) if drv else "",
        })
    return out


# 드라이버가 붙어 있어야 하는 장치 종류 (PCI 클래스 앞자리)
NEEDS_DRIVER = {"01": "저장장치 컨트롤러", "02": "네트워크", "0c04": "Fibre Channel", "03": "디스플레이",
                "12": "가속기", "0b40": "보조 프로세서"}


def needs_driver(dev):
    code = dev["cls_code"]
    for k in sorted(NEEDS_DRIVER, key=len, reverse=True):
        if code.startswith(k):
            return NEEDS_DRIVER[k]
    return None


def is_endpoint(dev):
    """링크 저하 판정 대상: 브리지·루트 포트(06xx) 를 뺀 실제 카드/장치."""
    return not dev["cls_code"].startswith("06")


# ---------------------------------------------------------------- 디스크

def disk_transport(d):
    p, n = d.get("path", ""), d["name"]
    if n.startswith(("loop", "ram", "zram", "sr")):
        return "virtual"
    if n.startswith(("dm-", "md")):
        return "logical"
    if "/rport-" in p or "/fc_host" in p:
        return "fc"
    if re.search(r"/session\d+/", p):
        return "iscsi"
    if "/usb" in p:
        return "usb"
    if n.startswith("nvme"):
        return "nvme"
    return "local"


# ---------------------------------------------------------------- 시간

def _time(src):
    t = {"utc": None, "ntp_sync": None, "tz": ""}
    text = src.cmd("timedatectl") or ""
    m = re.search(r"Universal time: \w{3} (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC", text)
    if src.kind == "live":
        t["utc"] = datetime.now(timezone.utc)
    elif m:
        t["utc"] = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    m = re.search(r"(?:NTP synchronized|System clock synchronized): (yes|no)", text)
    if m:
        t["ntp_sync"] = m.group(1) == "yes"
    m = re.search(r"Time zone: (.*)$", text, re.M)
    t["tz"] = m.group(1).strip() if m else ""
    return t


def strip_dmesg(line):
    """'[   12.345] msg' → msg"""
    return re.sub(r"^\[\s*[\d.]+\]\s?", "", line)
