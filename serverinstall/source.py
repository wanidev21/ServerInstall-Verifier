"""OS 데이터 소스: 실행 중인 서버(LiveSource) 또는 풀어 둔 sosreport(SosSource).

수집기는 소스가 어느 쪽인지 모르고 같은 메서드만 부른다.
SosSource 는 Linux 장비 없이 개발·검증하려고 둔 것이다 (실제 점검은 LiveSource).
"""

import os
import re
import subprocess


def _decode(data):
    # Windows 로 복사·편집한 오프라인 자료도 Linux 줄바꿈과 동일하게 처리한다.
    return data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")

# 명령 이름 → (실행 인자, sosreport 안의 파일)
COMMANDS = {
    "dmidecode": (["dmidecode"], "sos_commands/hardware/dmidecode"),
    "lspci": (["lspci", "-nnvv"], "sos_commands/pci/lspci_-nnvv"),
    "dmesg": (["dmesg"], "sos_commands/kernel/dmesg"),
    "timedatectl": (["timedatectl"], "sos_commands/systemd/timedatectl"),
    "failed_units": (["systemctl", "list-units", "--failed", "--no-pager"],
                     "sos_commands/systemd/systemctl_list-units_--failed"),
    "ip_addr": (["ip", "-d", "address"], "sos_commands/networking/ip_-d_address"),
    "lsblk": (["lsblk"], "sos_commands/block/lsblk"),
    "uname": (["uname", "-a"], "sos_commands/kernel/uname_-a"),
}


class LiveSource(object):
    kind = "live"
    label = "이 서버"

    def __init__(self):
        self.missing = []  # 실행하지 못한 명령 (보고서에 남긴다)

    def read(self, path):
        try:
            with open(path, "rb") as f:
                return _decode(f.read())
        except (OSError, IOError):
            return None

    def cmd(self, name, *extra):
        args = COMMANDS[name][0] if name in COMMANDS else [name]
        try:
            env = dict(os.environ, LC_ALL="C", LANG="C")
            p = subprocess.Popen(list(args) + list(extra), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            out, _ = p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            self.missing.append(" ".join(list(args) + list(extra)) + " (timeout)")
            return None
        except OSError:
            self.missing.append(" ".join(list(args) + list(extra)))
            return None
        if p.returncode != 0:
            self.missing.append(" ".join(list(args) + list(extra)))
            return None
        return _decode(out)

    # --- 장치 목록: /sys 를 직접 읽는다

    def net_devices(self):
        """물리 NIC: [{name, mac, driver, bus, link, speed}]"""
        base = "/sys/class/net"
        out = []
        for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            d = os.path.join(base, name)
            if not os.path.exists(os.path.join(d, "device")):
                continue  # bond, bridge, lo 같은 가상 장치
            mac = (self.read(os.path.join(d, "bonding_slave/perm_hwaddr"))
                   or self.read(os.path.join(d, "address")) or "").strip()
            drv_path = os.path.join(d, "device/driver")
            drv = os.path.basename(os.path.realpath(drv_path)) if os.path.islink(drv_path) else ""
            bus = os.path.basename(os.path.realpath(os.path.join(d, "device")))
            carrier = (self.read(os.path.join(d, "carrier")) or "").strip()
            speed = (self.read(os.path.join(d, "speed")) or "").strip()
            out.append({
                "name": name, "mac": mac.lower(), "driver": drv, "bus": bus,
                "link": {"1": True, "0": False}.get(carrier),
                "speed": int(speed) if speed.lstrip("-").isdigit() and int(speed) > 0 else None,
                "ipv4": self._ipv4(name),
            })
        return out

    def _ipv4(self, name):
        text = self.cmd("ip", "-4", "-o", "address", "show", "dev", name) or ""
        return re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", text)

    def block_devices(self):
        """디스크: [{name, size_bytes, model, vendor, path}] (path 로 전송 방식을 판단)"""
        base = "/sys/block"
        out = []
        for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            d = os.path.join(base, name)
            sectors = (self.read(os.path.join(d, "size")) or "0").strip()
            out.append({
                "name": name,
                "size_bytes": int(sectors) * 512 if sectors.isdigit() else None,
                "model": (self.read(os.path.join(d, "device/model")) or "").strip(),
                "vendor": (self.read(os.path.join(d, "device/vendor")) or "").strip(),
                "path": os.path.realpath(d),
            })
        return out


class SosSource(object):
    """풀어 둔 sosreport 폴더. read('/proc/meminfo') → <root>/proc/meminfo"""
    kind = "sosreport"

    def __init__(self, root):
        if not os.path.isdir(root):
            raise ValueError("sosreport directory does not exist: %s" % root)
        self.root = root
        self.label = "sosreport (%s)" % os.path.basename(os.path.normpath(root))
        self.missing = []

    def read(self, path):
        full = os.path.join(self.root, *path.strip("/").split("/"))
        try:
            with open(full, "rb") as f:
                return _decode(f.read())
        except (OSError, IOError):
            return None

    def cmd(self, name, *extra):
        rel = COMMANDS[name][1] if name in COMMANDS else None
        text = self.read(rel) if rel else None
        if text is None:
            self.missing.append(name)
        return text

    def net_devices(self):
        net = "sos_commands/networking"
        folder = os.path.join(self.root, *net.split("/"))
        if not os.path.isdir(folder):
            self.missing.append("networking")
            return []
        names = [f[len("ethtool_-i_"):] for f in os.listdir(folder)
                 if f.startswith("ethtool_-i_")]
        links = _parse_ip_links(self.cmd("ip_addr") or "")
        out = []
        for name in sorted(names):
            info = dict(re.findall(r"^([\w-]+): ?(.*)$", self.read("%s/ethtool_-i_%s" % (net, name)) or "", re.M))
            bus = info.get("bus-info", "").strip()
            if not re.match(r"[0-9a-f]{4}:|usb-", bus):
                continue  # bridge, tap 같은 가상 장치
            eth = self.read("%s/ethtool_%s" % (net, name)) or ""
            m = re.search(r"Speed: (\d+)Mb/s", eth)
            ld = re.search(r"Link detected: (yes|no)", eth)
            lk = links.get(name, {})
            out.append({
                "name": name, "mac": lk.get("mac", ""), "driver": info.get("driver", ""), "bus": bus,
                "link": (ld.group(1) == "yes") if ld else None,
                "speed": int(m.group(1)) if m else None,
                "ipv4": lk.get("ipv4", []),
            })
        return out

    def block_devices(self):
        listing = self.read("sos_commands/block/ls_-lanR_.sys.block") or ""
        sizes = {}
        for line in (self.cmd("lsblk") or "").splitlines():
            m = re.match(r"^(\w+)\s+\d+:\d+\s+\d+\s+(\S+)\s+\d+\s+disk", line)
            if m:
                sizes[m.group(1)] = _size_bytes(m.group(2))
        out = []
        for m in re.finditer(r" (\S+) -> \.\./(devices/\S+)", listing):
            name = m.group(1)
            udev = self.read("sos_commands/block/udevadm_info_.dev.%s" % name) or ""
            model = re.search(r"^E: ID_MODEL=(.*)$", udev, re.M)
            vendor = re.search(r"^E: ID_VENDOR=(.*)$", udev, re.M)
            out.append({
                "name": name, "size_bytes": sizes.get(name),
                "model": model.group(1).replace("_", " ") if model else "",
                "vendor": vendor.group(1) if vendor else "",
                "path": "/sys/" + m.group(2),
            })
        return out


def _parse_ip_links(text):
    """ip -d address → {이름: {mac, ipv4}}. 본딩 슬레이브는 perm_hwaddr(원래 MAC)을 쓴다."""
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^\d+: ([^:@]+)[:@]", line)
        if m:
            cur = out.setdefault(m.group(1), {"mac": "", "ipv4": []})
            continue
        if cur is None:
            continue
        m = re.search(r"link/ether ([0-9a-f:]{17})", line)
        if m and not cur["mac"]:
            cur["mac"] = m.group(1)
        m = re.search(r"perm_hwaddr ([0-9a-f:]{17})", line)
        if m:
            cur["mac"] = m.group(1)
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
        if m:
            cur["ipv4"].append(m.group(1))
    return out


def _size_bytes(s):
    m = re.match(r"^([\d.]+)([KMGTP]?)$", s)
    if not m:
        return None
    return int(float(m.group(1)) * 1024 ** "BKMGTP".index(m.group(2) or "B"))
