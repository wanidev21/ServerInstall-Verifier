"""OS 와 BMC 정보를 교차 확인해 항목별로 판정한다.

항목: {area, status, title, details: [문자열]}
status: 'fail'(이상) | 'warn'(주의) | 'ok'(정상) | 'info'(참고) | 'skip'(생략)
bmc 가 None 이면 BMC 를 읽지 못한 것이고, HW 쪽 항목은 생략으로 남긴다.
"""

import re
from collections import Counter

from servercommon.osrules import classify_line

from .inventory import pcie_cards, raid_volumes, unassigned_drives
from .oscollect import strip_dmesg

STATUS_ORDER = {"fail": 0, "warn": 1, "ok": 2, "info": 3, "skip": 4}
CLOCK_SKEW_SEC = 120     # BMC 와 OS 시계 차이가 이보다 크면 주의
MEM_VISIBLE_MIN = 0.90   # OS 가 보는 메모리가 장착량의 이 비율보다 작으면 주의
LIST_MAX = 15            # 한 항목에 늘어놓는 근거 줄 수


def run(osd, bmc, bmc_error=None):
    items = []

    def add(area, status, title, details=()):
        items.append({"area": area, "status": status, "title": title, "details": list(details)})

    hw = bmc is not None
    if not hw:
        add("BMC", "skip", "BMC 를 읽지 못해 HW 항목을 생략했습니다",
            [bmc_error or "", "접속 실패라면 XCC LAN over USB 가 켜져 있고 OS 쪽 USB NIC 에 IP 가 있는지 확인하세요."])

    _system(add, osd, bmc)
    _cpu(add, osd, bmc)
    _memory(add, osd, bmc)
    _pcie(add, osd, bmc)
    _nic(add, osd, bmc)
    _disk(add, osd, bmc)
    if hw:
        _power_thermal(add, bmc)
    _kernel(add, osd)
    _os_state(add, osd, bmc)
    if osd.get("missing"):
        add("수집", "skip", "실행하지 못한 명령 %d개" % len(osd["missing"]),
            ["%s (설치되어 있지 않거나 root 권한이 필요)" % m for m in osd["missing"]])
    if hw and bmc.get("errors"):
        add("수집", "skip", "BMC 에서 읽지 못한 항목 %d개" % len(bmc["errors"]), bmc["errors"][:LIST_MAX])
    items.sort(key=lambda x: STATUS_ORDER[x["status"]])
    return items


# ---------------------------------------------------------------- 항목별

def _system(add, osd, bmc):
    if not bmc:
        return
    o_sn, b_sn = (osd.get("serial") or "").strip(), (bmc.get("serial") or "").strip()
    invalid = {"", "?", "unknown", "none", "not specified", "to be filled by o.e.m.", "default string"}
    if o_sn.lower() in invalid or b_sn.lower() in invalid:
        add("시스템", "skip", "시리얼 번호를 양쪽에서 읽지 못해 일치 여부를 확인하지 못했습니다",
            ["BMC: %s / OS: %s" % (b_sn or "?", o_sn or "?")])
    elif o_sn.upper() != b_sn.upper():
        add("시스템", "fail", "시리얼 번호가 다릅니다 (BMC %s / OS %s)" % (b_sn, o_sn),
            ["메인보드 교체 후 VPD 미갱신 등을 의심합니다."])
    else:
        add("시스템", "ok", "시리얼 번호 일치 (%s)" % (b_sn or o_sn or "?"),
            ["BMC: %s / OS: %s" % (bmc.get("model", ""), osd.get("product", ""))])
    if bmc.get("health") and bmc["health"] != "OK":
        add("시스템", "fail", "BMC 전체 상태가 %s 입니다" % bmc["health"])


def _cpu_model(s):
    return re.sub(r"\(R\)|\(TM\)|CPU|@.*$|\s+", "", s or "", flags=re.I).lower()


def _cpu(add, osd, bmc):
    o_n = len(osd["cpus"])
    o_desc = "%d소켓, %d스레드, %s" % (o_n, osd["threads"], osd["cpus"][0]["model"] if osd["cpus"] else "?")
    if not bmc:
        add("CPU", "info", "OS: " + o_desc)
        return
    b = [c for c in bmc["cpus"] if c["state"] != "Absent"]
    bad = [c for c in b if c["health"] not in ("", "OK")]
    b_threads = sum(c["threads"] or 0 for c in b)
    detail = ["BMC: %d개, %s" % (len(b), b[0]["model"] if b else "?"), "OS: " + o_desc]
    if bad:
        add("CPU", "fail", "BMC 가 CPU 이상을 표시합니다",
            ["%s: %s" % (c["socket"], c["health"]) for c in bad] + detail)
    elif not b or not o_n:
        add("CPU", "skip", "CPU 정보를 양쪽에서 읽지 못해 비교를 생략했습니다", detail)
    elif len(b) != o_n:
        add("CPU", "fail", "CPU 개수가 다릅니다 (BMC %d / OS %d)" % (len(b), o_n), detail)
    elif b_threads and b_threads != osd["threads"]:
        add("CPU", "warn", "스레드 수가 다릅니다 (BMC %d / OS %d)" % (b_threads, osd["threads"]),
            detail + ["UEFI 에서 하이퍼스레딩이나 코어 수를 제한했는지 확인하세요."])
    elif b and osd["cpus"] and _cpu_model(b[0]["model"]) not in _cpu_model(osd["cpus"][0]["model"]) \
            and _cpu_model(osd["cpus"][0]["model"]) not in _cpu_model(b[0]["model"]):
        add("CPU", "warn", "CPU 모델 표기가 다릅니다", detail)
    else:
        add("CPU", "ok", "CPU %d개 일치 (%d스레드)" % (o_n, osd["threads"]), detail)


def _slot_key(s):
    # CPU1_DIMM1 / CPU2_DIMM1 처럼 끝 번호가 같은 슬롯을 합치지 않는다.
    value = re.sub(r"[\s_-]+", "", s or "").upper()
    return re.sub(r"\d+", lambda m: str(int(m.group())), value)


def _gib(v):
    return ("%.0f" % v) if abs(v - round(v)) < 0.05 else ("%.1f" % v)


def _memory(add, osd, bmc):
    od = osd.get("dimms")
    vis = osd.get("mem_total_gib")
    if od is None:
        add("메모리", "skip", "dmidecode 를 읽지 못해 DIMM 비교를 생략했습니다", ["root 권한으로 실행하세요."])
        od = []
    elif not od:
        add("메모리", "skip", "OS DIMM 목록이 비어 있어 슬롯 비교를 생략했습니다")
    o_total = sum(d["size_gib"] for d in od)
    o_desc = "OS: DIMM %d개, %sGB (%s)" % (len(od), _gib(o_total), _size_mix(od))
    if bmc:
        bd = bmc["dimms"]
        if not bd:
            add("메모리", "skip", "BMC DIMM 정보를 받지 못해 교차 확인을 생략했습니다")
        b_total = sum(d["size_gib"] for d in bd)
        detail = ["BMC: DIMM %d개, %sGB (%s)" % (len(bd), _gib(b_total), _size_mix(bd)), o_desc]
        bad = [d for d in bd if d["health"] not in ("", "OK") or d["state"] not in ("", "Enabled")]
        if bad:
            add("메모리", "fail", "BMC 가 DIMM %d개를 비정상으로 표시합니다" % len(bad),
                ["%s: %s / %s" % (d["slot"], d["state"], d["health"]) for d in bad] + detail)
        if bd and od and (len(bd) != len(od) or abs(b_total - o_total) > 0.5):
            b_slots = set(_slot_key(d["slot"]) for d in bd)
            o_slots = set(_slot_key(d["slot"]) for d in od)
            diff = ["OS 에 없는 슬롯: %s" % ", ".join(map(str, sorted(b_slots - o_slots, key=str)))] \
                if b_slots - o_slots else []
            add("메모리", "fail", "DIMM 구성이 다릅니다 (BMC %d개 %sGB / OS %d개 %sGB)"
                % (len(bd), _gib(b_total), len(od), _gib(o_total)), diff + detail)
        elif not bad and od and bd:
            add("메모리", "ok", "DIMM 개수·총용량 일치 (%d개, %sGB)" % (len(od), _gib(o_total)), detail)
            bs = dict((_slot_key(d["slot"]), d) for d in bd if d["slot"])
            oslots = dict((_slot_key(d["slot"]), d) for d in od if d["slot"])
            if len(bs) == len(bd) and len(oslots) == len(od) and set(bs) == set(oslots):
                mismatched = [k for k in bs if abs(bs[k]["size_gib"] - oslots[k]["size_gib"]) > 0.5]
                if mismatched:
                    add("메모리", "fail", "슬롯별 DIMM 용량이 다릅니다", [
                        "%s: BMC %sGB / OS %sGB" % (bs[k]["slot"], _gib(bs[k]["size_gib"]), _gib(oslots[k]["size_gib"]))
                        for k in sorted(mismatched)])
            else:
                add("메모리", "skip", "슬롯 이름을 일대일로 대조하지 못했습니다", ["BMC·OS 의 슬롯 표기를 직접 비교하세요."])
        elif not bad:
            add("메모리", "info", detail[0])
        installed = b_total or o_total
    else:
        if od:
            add("메모리", "info", o_desc)
        installed = o_total
    if vis and installed:
        ratio = vis / installed
        line = "OS 사용 가능 메모리 %.1fGiB / 장착 %sGB (%.0f%%)" % (vis, _gib(installed), ratio * 100)
        if ratio < MEM_VISIBLE_MIN:
            add("메모리", "warn", "OS 가 장착 메모리보다 적게 인식합니다",
                [line, "메모리 미러링·스페어링 설정이나 커널 예약(crashkernel 등)을 확인하세요."])
        else:
            add("메모리", "ok", line)
    slow = [d for d in od if d.get("speed") and d.get("configured_speed") and d["configured_speed"] < d["speed"]]
    if slow:
        add("메모리", "info", "DIMM %d개가 정격보다 낮은 속도로 동작합니다 (%d → %d MT/s)"
            % (len(slow), slow[0]["speed"], slow[0]["configured_speed"]),
            ["CPU 지원 속도나 채널당 DIMM 수에 따른 정상 동작일 수 있습니다."])


def _size_mix(dimms):
    c = Counter(_gib(d["size_gib"]) for d in dimms)
    return ", ".join("%sGB×%d" % (k, v) for k, v in sorted(c.items(), key=lambda kv: -float(kv[0]))) or "-"


def _pcie(add, osd, bmc):
    if not osd["pci"]:
        add("PCIe", "skip", "lspci 결과가 없어 PCIe 점검을 생략했습니다")
        return
    cards = pcie_cards(osd, bmc)
    if bmc and not bmc["pcie"]:
        add("PCIe", "skip", "BMC PCIe 목록을 받지 못해 OS 정보만 확인했습니다")

    def tag(c):
        return "슬롯 %s %s" % (c["slot"] or "온보드", c["name"])
    lost = [c for c in cards if c["seen"] == "bmc"]
    nodrv = [c for c in cards if c["no_driver"]]
    bad = [c for c in cards if c["health"] not in ("", "OK")]
    narrow = [c for c in cards if c["narrow"]]
    slow = [c for c in cards if c["slow"] and not c["narrow"]]
    ambiguous = [c for c in cards if c["seen"] == "ambiguous"]
    if ambiguous:
        add("PCIe", "skip", "동일 ID 카드가 여러 개여서 BMC·OS 를 특정하지 못했습니다",
            [tag(c) for c in ambiguous])
    if lost:
        add("PCIe", "fail", "BMC 에는 있는데 OS 에 보이지 않는 카드 %d개" % len(lost),
            [tag(c) for c in lost] + ["카드 인식 실패나 장착 불량을 의심합니다."])
    if nodrv:
        add("PCIe", "fail", "드라이버가 붙지 않은 카드 %d개" % len(nodrv),
            ["%s (%s)" % (tag(c), ", ".join(c["no_driver"])) for c in nodrv]
            + ["OS 에 드라이버가 없거나 장치 초기화에 실패했습니다."])
    if bad:
        add("PCIe", "fail", "BMC 가 카드 %d개를 비정상으로 표시합니다" % len(bad),
            ["%s: %s" % (tag(c), c["health"]) for c in bad])
    if narrow:
        add("PCIe", "warn", "링크 폭이 카드 최대치보다 좁은 카드 %d개" % len(narrow),
            ["%s: %s → %s" % (tag(c), c["max"], c["cur"]) for c in narrow]
            + ["슬롯 자체 배선 폭이 좁으면 정상입니다. 아니면 장착 상태를 확인하세요."])
    if slow:
        add("PCIe", "info", "링크 속도가 카드 최대치보다 낮은 카드 %d개" % len(slow),
            ["%s: %s → %s" % (tag(c), c["max"], c["cur"]) for c in slow]
            + ["유휴 시 절전으로 속도를 낮추는 장치도 있습니다."])
    if not (lost or nodrv or bad or narrow or ambiguous):
        add("PCIe", "ok", "카드 %d개 인식, 링크 저하·드라이버 누락 없음%s" % (
            len(cards), " (BMC 자료와 대조 가능한 장치 기준)" if bmc and bmc["pcie"] else ""))


def _nic(add, osd, bmc):
    nics = osd["nics"]
    usb = [n for n in nics if n["driver"] == "cdc_ether"]
    phys = [n for n in nics if n["driver"] != "cdc_ether"]
    if not nics:
        add("NIC", "skip", "OS 물리 네트워크 포트 정보를 받지 못해 비교를 생략했습니다")
        return
    down = [n for n in phys if n["link"] is False]
    up = [n for n in phys if n["link"]]
    add("NIC", "info", "물리 포트 %d개: 연결 %d / 미연결 %d" % (len(phys), len(up), len(down)),
        ["%s %s %s" % (n["name"], _speed(n["speed"]), n["driver"]) for n in up]
        + ["%s 미연결 (케이블 연결 계획과 비교하세요)" % n["name"] for n in down])
    for n in usb:
        if n["ipv4"]:
            add("NIC", "info", "BMC LAN over USB 인터페이스 %s: %s" % (n["name"], ", ".join(n["ipv4"])))
        else:
            add("NIC", "info", "BMC LAN over USB 인터페이스 %s 에 IP 가 없습니다" % n["name"],
                ["OS 안에서 BMC(Redfish)에 접속하려면 이 인터페이스에 IP 가 필요합니다."])
    if bmc and bmc["ports"]:
        os_macs = set(n["mac"].lower() for n in nics if n["mac"])
        lost = [p for p in bmc["ports"] if p["mac"] and p["mac"].lower() not in os_macs]
        if lost:
            add("NIC", "fail", "BMC 에는 있는데 OS 에 보이지 않는 포트 %d개" % len(lost),
                ["%s 포트 %s (%s)" % (p["adapter"], p["port"], p["mac"]) for p in lost][:LIST_MAX]
                + ["드라이버 누락이나 카드 인식 실패를 의심합니다."])
        elif any(p["mac"] for p in bmc["ports"]):
            add("NIC", "ok", "BMC 가 아는 포트 %d개가 모두 OS 에 보입니다 (MAC 기준)"
                % len([p for p in bmc["ports"] if p["mac"]]))
        if any(not p["mac"] for p in bmc["ports"]):
            add("NIC", "skip", "MAC 주소가 없는 BMC 포트는 OS 와 비교하지 못했습니다")
    elif bmc:
        add("NIC", "skip", "BMC 네트워크 포트 정보를 받지 못해 교차 확인을 생략했습니다")


def _speed(mbps):
    if not mbps:
        return ""
    return "%dG" % (mbps // 1000) if mbps >= 1000 else "%dM" % mbps


def _disk(add, osd, bmc):
    disks = osd["disks"]
    local = [d for d in disks if d["transport"] in ("local", "nvme")]
    if not disks:
        add("디스크", "skip", "OS 디스크 목록이 비어 있어 연결 여부를 확인하지 못했습니다")
    other = Counter(d["transport"] for d in disks if d["transport"] in ("fc", "iscsi", "usb"))
    add("디스크", "info", "OS 로컬 디스크 %d개%s" % (
        len(local), "".join(", %s %d개" % (k.upper(), v) for k, v in sorted(other.items()))),
        ["%s %s %s" % (d["name"], _bytes(d["size_bytes"]), (d["vendor"] + " " + d["model"]).strip())
         for d in local])
    if bmc:
        bad = [d for d in bmc["drives"] if d["health"] not in ("", "OK") or d["state"] not in ("", "Enabled")]
        detail = ["%s %s %s %s" % (d["name"], d["model"], _bytes(d["size_bytes"]), d["media"])
                  for d in bmc["drives"]]
        if bad:
            add("디스크", "fail", "BMC 가 드라이브 %d개를 비정상으로 표시합니다" % len(bad),
                ["%s: %s / %s" % (d["name"], d["state"], d["health"]) for d in bad])
        elif bmc["drives"]:
            add("디스크", "ok", "BMC 물리 드라이브 %d개 정상" % len(bmc["drives"]), detail[:LIST_MAX])
        else:
            add("디스크", "skip", "BMC 물리 드라이브 정보를 받지 못했습니다")
        vols = raid_volumes(osd, bmc)
        badv = [v for v in vols if v["health"] not in ("", "OK") or v["state"] not in ("", "Enabled")]
        if badv:
            add("RAID", "fail", "비정상 RAID 볼륨 %d개" % len(badv),
                ["%s %s: %s / %s" % (v["name"], v["raid"], v["state"], v["health"]) for v in badv]
                + ["드라이브 누락이나 재구성(rebuild) 중인지 확인하세요."])
        if vols:
            for v in vols:
                raid = v["raid"].upper().replace(" ", "")
                minimum = {"RAID0": 1, "RAID1": 2, "MIRRORED": 2, "RAID5": 3, "RAID6": 4, "RAID10": 4}.get(raid)
                if minimum and len(v["drives"]) < minimum:
                    add("RAID", "warn", "RAID 수준에 비해 구성 드라이브 정보가 부족합니다", [
                        "%s: %s, 확인한 드라이브 %d개 (최소 %d개)" % (v["name"], v["raid"], len(v["drives"]), minimum),
                        "구성 누락·드라이브 고장인지, BMC 에서 연결 정보를 일부만 제공하는지 확인하세요."])
            missing = [v for v in vols if not v["os_disk"]]
            if missing:
                add("RAID", "warn", "OS 디스크와 연결하지 못한 RAID 볼륨 %d개" % len(missing),
                    [v["name"] for v in missing] + ["용량 기준 후보 검색 결과입니다. 드라이버·디스크 정보를 확인하세요."])
            add("RAID", "info" if badv or missing else "ok", "RAID 볼륨 %d개: %s" % (len(vols), ", ".join(
                "%s(드라이브 %d개)" % (v["raid"] or "?", len(v["drives"])) for v in vols)),
                ["%s %s %s → OS %s" % (v["name"], v["raid"], _bytes(v["size_bytes"]), v["os_disk"] or "미확인")
                 for v in vols])
            free = unassigned_drives(bmc)
            if free:
                add("RAID", "info", "어느 볼륨에도 속하지 않은 드라이브 %d개" % len(free),
                    ["%s %s %s" % (d["name"], d["model"], _bytes(d["size_bytes"])) for d in free]
                    + ["핫스페어·JBOD 로 쓰려는 것이 아니면 RAID 구성을 확인하세요."])
        elif bmc["drives"]:
            add("RAID", "info", "BMC 에서 RAID 볼륨 정보를 받지 못했습니다",
                ["RAID 미구성이거나 이 펌웨어가 Redfish 볼륨 정보를 주지 않는 경우입니다."])


def _bytes(n):
    if not n:
        return ""
    for unit in ("TB", "GB"):
        div = 1000 ** (4 if unit == "TB" else 3)
        if n >= div:
            return "%.1f%s" % (n / float(div), unit)
    return "%dMB" % (n // 1000 ** 2)


def _power_thermal(add, bmc):
    bad_psu = [p for p in bmc["psus"] if p["health"] not in ("", "OK") or p["state"] not in ("", "Enabled")]
    if bad_psu:
        add("전원", "fail", "PSU %d개 비정상" % len(bad_psu), ["%s: %s" % (p["name"], p["health"]) for p in bad_psu])
    elif bmc["psus"]:
        add("전원", "ok", "PSU %d개 정상" % len(bmc["psus"]),
            ["%s %s %sW" % (p["name"], p["model"], p["watts"] or "?") for p in bmc["psus"]])
    else:
        add("전원", "skip", "BMC 전원 공급 장치 정보를 받지 못했습니다")
    if len(bmc["psus"]) == 1:
        add("전원", "warn", "PSU 가 1개뿐이라 이중화가 없습니다")
    for r in bmc["power_redundancy"]:
        if r["health"] not in ("", "OK"):
            add("전원", "warn", "전원 이중화 상태: %s (%s)" % (r["health"], r["name"]),
                ["두 PSU 모두에 전원 케이블이 연결됐는지 확인하세요."])
    bad_fan = [f for f in bmc["fans"] if f["health"] not in ("", "OK") or f["state"] not in ("", "Enabled")]
    if not bmc["fans"]:
        add("냉각", "skip", "BMC 팬 정보를 받지 못했습니다")
    if not bmc["temps"]:
        add("냉각", "skip", "BMC 온도 센서 정보를 받지 못했습니다")
    if bad_fan:
        add("냉각", "fail", "팬 %d개 비정상" % len(bad_fan), ["%s: %s" % (f["name"], f["health"]) for f in bad_fan])
    hot = [t for t in bmc["temps"] if t["health"] not in ("", "OK") or (t["upper"] and t["reading"] >= t["upper"])]
    if hot:
        add("냉각", "fail", "온도 이상 센서 %d개" % len(hot),
            ["%s: %s°C (한계 %s)" % (t["name"], t["reading"], t["upper"]) for t in hot])


def _kernel(add, osd):
    log = osd["kernel_log"]
    if not log:
        add("커널", "skip", "커널 로그(dmesg)를 읽지 못했습니다")
        return
    hits = {"E": Counter(), "W": Counter()}
    for line in log:
        msg = strip_dmesg(line)
        r = classify_line("kernel", msg)
        if r and r[2] in hits:
            hits[r[2]][msg.strip()[:200]] += 1
    for sev, status, label in (("E", "fail", "오류"), ("W", "warn", "경고")):
        if hits[sev]:
            add("커널", status, "커널 %s %d건" % (label, sum(hits[sev].values())),
                ["%s%s" % (m, " (×%d)" % n if n > 1 else "") for m, n in hits[sev].most_common(LIST_MAX)])
    if not hits["E"] and not hits["W"]:
        add("커널", "ok", "수집한 커널 로그에서 지원 규칙에 해당하는 오류·경고 미탐지 (%d줄)" % len(log))


def _os_state(add, osd, bmc):
    if osd["failed_units"]:
        add("OS", "warn", "실패한 systemd 서비스 %d개" % len(osd["failed_units"]), osd["failed_units"])
    t = osd["time"]
    if t["ntp_sync"] is False:
        add("시간", "warn", "OS 시계가 NTP 와 동기화되지 않았습니다", [t["tz"]])
    if bmc and bmc.get("bmc_time") and t["utc"]:
        skew = abs((bmc["bmc_time"] - t["utc"]).total_seconds())
        line = "BMC %s / OS %s (UTC)" % (bmc["bmc_time"].strftime("%H:%M:%S"), t["utc"].strftime("%H:%M:%S"))
        if skew > CLOCK_SKEW_SEC:
            add("시간", "warn", "BMC 와 OS 시계가 %d초 차이 납니다" % skew,
                [line, "장애 분석 때 BMC·OS 로그 시간을 맞추기 어려워집니다. BMC NTP 설정을 확인하세요."])
        else:
            add("시간", "ok", "BMC 와 OS 시계 차이 %d초" % skew, [line])
    add("OS", "info", "%s, 커널 %s" % (osd["os_name"], osd["kernel"] or "?"), [t["tz"]] if t["tz"] else [])
