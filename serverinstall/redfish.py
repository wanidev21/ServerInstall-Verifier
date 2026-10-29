"""BMC Redfish 수집 → 공통 형식.

    {model, serial, uuid, power, health, bmc_time(datetime UTC|None),
     cpus: [{socket, model, cores, threads, state, health}],
     dimms: [{slot, size_gib, speed, part, manufacturer, state, health}],
     pcie: [{name, ids: {(vid, did)}, slot, gen, max_gen, lanes, max_lanes, health}],
     ports: [{adapter, port, mac, link}],
     drives: [{id, name, model, size_bytes, media, state, health, controller}],
     psus: [{name, model, watts, state, health}], power_redundancy: [상태],
     fans: [{name, reading, state, health}], temps: [{name, reading, upper, health}],
     volumes: [{name, controller, raid, size_bytes, drives: [드라이브 id], state, health}],
     firmware: [{name, version}], errors: [읽지 못한 경로와 이유]}

표준 Redfish 스키마만 쓴다. Lenovo XCC 는 LAN over USB 로 OS 에서 169.254.95.118 에 닿는다.
mock_dir 를 주면 DMTF mockup 형식 폴더(<dir>/redfish/v1/.../index.json)를 대신 읽는다.
"""

import base64
import json
import os
import re
import ssl
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_HOST = "169.254.95.118"


class RedfishError(Exception):
    pass


class Client(object):
    def __init__(self, host=None, user=None, password=None, mock_dir=None, timeout=15):
        self.base = "https://%s" % (host or DEFAULT_HOST)
        self.mock_dir = mock_dir
        self.timeout = timeout
        self.errors = []
        self._auth = None
        if user is not None:
            self._auth = "Basic " + base64.b64encode(("%s:%s" % (user, password or "")).encode()).decode()
        # BMC 는 자체 서명 인증서를 쓴다. 내부 링크(LAN over USB) 접속이라 검증을 끈다.
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    def get(self, path, required=False):
        if not isinstance(path, str) or not path.startswith("/redfish/v1") or ".." in path.split("/"):
            if required:
                raise RedfishError("잘못된 Redfish 경로입니다: %s" % path)
            self.errors.append("잘못되었거나 누락된 Redfish 경로: %s" % path)
            return None
        path = path.split("#")[0]
        try:
            if self.mock_dir:
                full = os.path.join(self.mock_dir, *path.split("?")[0].strip("/").split("/"))
                with open(os.path.join(full, "index.json"), "rb") as f:
                    return json.loads(f.read().decode("utf-8"))
            req = Request(self.base + path, headers={"Accept": "application/json"})
            if self._auth:
                req.add_header("Authorization", self._auth)
            with urlopen(req, timeout=self.timeout, context=self._ctx) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except HTTPError as e:
            if e.code in (401, 403):
                raise RedfishError("BMC 로그인 실패 (HTTP %d). 계정과 암호를 확인하세요." % e.code)
            err = "HTTP %d" % e.code
        except (URLError, OSError, IOError, ValueError) as e:
            err = str(getattr(e, "reason", e))
        if required:
            raise RedfishError("BMC 에 접속할 수 없습니다: %s (%s)" % (self.base + path, err))
        self.errors.append("%s: %s" % (path, err))
        return None

    def members(self, path):
        """컬렉션의 각 항목을 읽어 돌려준다. path 가 없거나 비어 있으면 []."""
        if not path:
            return []
        if isinstance(path, dict):
            path = path.get("@odata.id")
        out = []
        seen = set()
        while path:
            if path in seen:
                self.errors.append("%s: 컬렉션 페이지가 반복됩니다" % path)
                break
            seen.add(path)
            col = self.get(path)
            if not isinstance(col, dict):
                break
            for m in col.get("Members", []):
                doc = self.resolve(m)
                if doc:
                    out.append(doc)
            path = col.get("Members@odata.nextLink")
        return out

    def resolve(self, value):
        """메타데이터가 붙은 참조와 이미 확장된 장치 문서를 구분한다."""
        if not isinstance(value, dict):
            return None
        if value.get("@odata.id") and not any(not k.startswith("@") for k in value):
            return self.get(value["@odata.id"])
        return value

    def linked(self, doc, key):
        """배열형 참조와 컬렉션 링크를 모두 읽는다."""
        value = (doc or {}).get(key)
        if isinstance(value, list):
            return [d for d in (self.resolve(v) for v in value) if d]
        return self.members(value)


def _link(doc, key):
    v = (doc or {}).get(key)
    return v.get("@odata.id") if isinstance(v, dict) else None


def _status(doc):
    st = (doc or {}).get("Status") or {}
    return st.get("State") or "", st.get("HealthRollup") or st.get("Health") or ""


def collect(client):
    root = client.get("/redfish/v1", required=True)
    systems = client.members(_link(root, "Systems") or "/redfish/v1/Systems")
    chassis = client.members(_link(root, "Chassis") or "/redfish/v1/Chassis")
    managers = client.members(_link(root, "Managers") or "/redfish/v1/Managers")
    if not systems:
        raise RedfishError("Redfish 에 시스템 정보가 없습니다 (/redfish/v1/Systems).")
    sysd = systems[0]
    b = {"model": sysd.get("Model", ""), "serial": sysd.get("SerialNumber", ""), "uuid": sysd.get("UUID", ""),
         "sku": sysd.get("SKU", ""), "power": sysd.get("PowerState", ""), "health": _status(sysd)[1],
         "bios": sysd.get("BiosVersion", "")}

    b["cpus"] = []
    for p in client.members(_link(sysd, "Processors")):
        if p.get("ProcessorType", "CPU") != "CPU":
            continue
        state, health = _status(p)
        b["cpus"].append({"socket": p.get("Socket") or p.get("Id", ""), "model": p.get("Model", ""),
                          "cores": p.get("TotalCores"), "threads": p.get("TotalThreads"),
                          "state": state, "health": health})

    b["dimms"] = []
    for d in client.members(_link(sysd, "Memory")):
        state, health = _status(d)
        if state == "Absent" or not d.get("CapacityMiB"):
            continue
        b["dimms"].append({"slot": d.get("DeviceLocator") or d.get("Name") or d.get("Id", ""),
                           "size_gib": d["CapacityMiB"] / 1024.0, "speed": d.get("OperatingSpeedMhz"),
                           "part": (d.get("PartNumber") or "").strip(), "manufacturer": d.get("Manufacturer", ""),
                           "state": state, "health": health})

    b["drives"], b["volumes"] = [], []
    for st in client.members(_link(sysd, "Storage")):
        ctrl = st.get("Name", "")
        for ref in st.get("Drives", []):
            d = client.get(ref.get("@odata.id", ""))
            if not d:
                continue
            state, health = _status(d)
            if state == "Absent":
                continue
            b["drives"].append({"id": ref["@odata.id"], "name": d.get("Name", ""),
                                "model": (d.get("Model") or "").strip(),
                                "size_bytes": d.get("CapacityBytes"), "media": d.get("MediaType", ""),
                                "state": state, "health": health, "controller": ctrl})
        for v in client.members(_link(st, "Volumes")):
            state, health = _status(v)
            refs = ((v.get("Links") or {}).get("Drives") or [])
            b["volumes"].append({"name": v.get("Name") or v.get("Id", ""), "controller": ctrl,
                                 # RAIDType(RAID1 등)이 없는 펌웨어는 VolumeType(Mirrored 등)만 준다
                                 "raid": v.get("RAIDType") or v.get("VolumeType") or "",
                                 "size_bytes": v.get("CapacityBytes"),
                                 "drives": [r.get("@odata.id") for r in refs if r.get("@odata.id")],
                                 "state": state, "health": health})

    b["pcie"], b["ports"], b["psus"], b["fans"], b["temps"], b["power_redundancy"] = [], [], [], [], [], []
    pcie_docs = client.linked(sysd, "PCIeDevices")
    for ch in chassis:
        pcie_docs += client.linked(ch, "PCIeDevices")
        for ad in client.members(_link(ch, "NetworkAdapters")):
            ports = client.members(_link(ad, "NetworkPorts")) or client.members(_link(ad, "Ports"))
            for p in ports:
                macs = p.get("AssociatedNetworkAddresses") or []
                eth = p.get("Ethernet") or {}
                macs = macs or eth.get("AssociatedMACAddresses") or []
                b["ports"].append({"adapter": ad.get("Name") or ad.get("Model", ""),
                                   "port": p.get("PhysicalPortNumber") or p.get("Id", ""),
                                   "mac": (macs[0] if macs else "").lower(),
                                   "link": p.get("LinkStatus", "")})
        pw = client.get(_link(ch, "Power")) if _link(ch, "Power") else None
        for ps in (pw or {}).get("PowerSupplies", []):
            state, health = _status(ps)
            if state == "Absent":
                continue
            b["psus"].append({"name": ps.get("Name", ""), "model": ps.get("Model", ""),
                              "watts": ps.get("PowerCapacityWatts"), "state": state, "health": health})
        for r in (pw or {}).get("Redundancy", []):
            b["power_redundancy"].append({"name": r.get("Name", ""), "mode": r.get("Mode", ""),
                                          "health": _status(r)[1], "state": _status(r)[0]})
        th = client.get(_link(ch, "Thermal")) if _link(ch, "Thermal") else None
        for fan in (th or {}).get("Fans", []):
            state, health = _status(fan)
            if state == "Absent":
                continue
            b["fans"].append({"name": fan.get("Name") or fan.get("FanName", ""), "reading": fan.get("Reading"),
                              "unit": fan.get("ReadingUnits", ""), "state": state, "health": health})
        for t in (th or {}).get("Temperatures", []):
            state, health = _status(t)
            if state == "Absent" or t.get("ReadingCelsius") is None:
                continue
            b["temps"].append({"name": t.get("Name", ""), "reading": t.get("ReadingCelsius"),
                               "upper": t.get("UpperThresholdCritical") or t.get("UpperThresholdNonCritical"),
                               "health": health})
    seen = set()
    for d in pcie_docs:
        ref = d.get("@odata.id") or json.dumps(d, sort_keys=True)
        if ref in seen:
            continue
        seen.add(ref)
        itf = d.get("PCIeInterface") or {}
        fn_refs = _link(d, "PCIeFunctions") or [r.get("@odata.id") for r in (d.get("Links") or {})
                                                .get("PCIeFunctions", [])]
        fns = client.members(fn_refs) if isinstance(fn_refs, str) else [client.get(r) for r in fn_refs if r]
        ids = set((_hex4(f.get("VendorId")), _hex4(f.get("DeviceId"))) for f in fns if f)
        ids = set(pair for pair in ids if all(pair))
        slot = ((d.get("Slot") or {}).get("Location") or {}).get("PartLocation", {}).get("LocationOrdinalValue")
        if slot is None:
            slot = ((d.get("Location") or {}).get("PartLocation") or {}).get("LocationOrdinalValue")
        b["pcie"].append({"name": d.get("Name") or d.get("Model", ""), "ids": ids,
                          "slot": "" if slot is None else str(slot),
                          "gen": itf.get("PCIeType", ""), "max_gen": itf.get("MaxPCIeType", ""),
                          "lanes": itf.get("LanesInUse"), "max_lanes": itf.get("MaxLanes"),
                          "health": _status(d)[1]})

    up = client.get(_link(root, "UpdateService") or "/redfish/v1/UpdateService")
    b["firmware"] = [{"name": f.get("Name") or f.get("Id", ""), "version": f.get("Version", "")}
                     for f in client.members(_link(up, "FirmwareInventory"))]

    b["bmc_time"] = None
    for mg in managers[:1]:
        b["bmc_time"] = _parse_ts(mg.get("DateTime"))
        b["bmc_fw"] = mg.get("FirmwareVersion", "")
    b["errors"] = list(client.errors)
    return b


def _hex4(v):
    """'0x15B3' → '15b3' (lspci 표기와 맞춘다)"""
    m = re.match(r"(?:0x)?([0-9a-fA-F]{1,4})$", str(v or "").strip())
    return m.group(1).lower().zfill(4) if m else ""


def _parse_ts(v):
    """'2023-07-24T10:23:28+00:00' → datetime UTC. 3.6 의 strptime %z 는 콜론을 못 읽어서 직접 뗀다."""
    if not v:
        return None
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?$", v.strip())
    if not m:
        return None
    ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    tz = m.group(2) or "Z"
    if tz == "Z":
        return ts.replace(tzinfo=timezone.utc)
    sign = 1 if tz[0] == "+" else -1
    hh, mm = int(tz[1:3]), int(tz[-2:])
    return (ts - sign * timedelta(hours=hh, minutes=mm)).replace(tzinfo=timezone.utc)
