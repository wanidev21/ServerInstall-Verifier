"""BMC 와 OS 정보를 한 줄로 합친 인벤토리 (보고서 표와 판정이 같이 쓴다).

- pcie_cards: OS 의 PCI 기능(function)을 카드 단위로 묶고, BMC 장치와 vendor/device ID·슬롯으로 짝짓는다
- raid_volumes: BMC 의 RAID 볼륨에 구성 드라이브와 OS 디스크를 붙인다
"""

import re

from .oscollect import is_endpoint, needs_driver

GEN = {2.5: "Gen1", 5.0: "Gen2", 8.0: "Gen3", 16.0: "Gen4", 32.0: "Gen5", 64.0: "Gen6"}
KIND = [("0c04", "FC"), ("0104", "RAID"), ("0106", "SATA"), ("0107", "SAS"), ("0108", "NVMe"),
        ("01", "저장장치"), ("02", "네트워크"), ("03", "디스플레이"), ("12", "가속기")]
PLACEHOLDER = re.compile(r"^Adapter [0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}$", re.I)  # XCC 가 이름을 모를 때


def kind(cls_code):
    for k, label in KIND:
        if cls_code.startswith(k):
            return label
    return ""


def link_text(speed, width):
    if not width:
        return ""
    return "%s x%d" % (GEN.get(speed, "%gGT/s" % speed if speed else "?"), width)


def pcie_cards(osd, bmc):
    """카드 목록: [{slot, name, kind, functions, drivers, cur, max, narrow, slow, no_driver, health, seen}]
    seen: 'both' | 'os' | 'bmc' (BMC 에만 있으면 OS 가 카드를 못 본 것)"""
    groups = {}
    for f in osd["pci"]:
        if is_endpoint(f):
            groups.setdefault(f["dev"], []).append(f)
    cards = []
    for dev, fns in groups.items():
        # 대표 기능: 드라이버가 필요한 종류를 우선 (MROM 과 SATA 가 한 장치에 섞인 칩셋 등)
        f0 = ([f for f in fns if needs_driver(f)] or fns)[0]
        if not (f0["slot"] or needs_driver(f0)):
            continue  # 칩셋 내부 장치
        cards.append({
            "dev": dev, "slot": f0["slot"], "name": f0["name"], "kind": kind(f0["cls_code"]),
            "ids": set((f["vid"], f["did"]) for f in fns), "functions": len([f for f in fns if f["cls_code"] == f0["cls_code"]]),
            "drivers": sorted(set(f["driver"] for f in fns if f["driver"])),
            "no_driver": [f["addr"] for f in fns if needs_driver(f) and not f["driver"]],
            "cur": link_text(f0["sta_speed"], f0["sta_width"]), "max": link_text(f0["cap_speed"], f0["cap_width"]),
            "narrow": bool(f0["sta_width"] and f0["cap_width"] and f0["sta_width"] < f0["cap_width"]),
            "slow": bool(f0["sta_speed"] and f0["cap_speed"] and f0["sta_speed"] < f0["cap_speed"]),
            "health": "", "seen": "os",
        })
    for b in (bmc or {}).get("pcie", []):
        cand = [c for c in cards if c["ids"] & b["ids"] and c["seen"] == "os"]
        pick = [c for c in cand if b["slot"] and c["slot"] == b["slot"]] or (cand if len(cand) == 1 else [])
        if pick:
            c = pick[0]
            c["seen"], c["health"] = "both", b["health"]
            if b["name"] and not PLACEHOLDER.match(b["name"]):
                c["os_name"], c["name"] = c["name"], b["name"]
            if not c["slot"] and b["slot"]:
                c["slot"] = b["slot"]
        elif b["ids"] and not PLACEHOLDER.match(b["name"]):
            cards.append({"dev": "", "slot": b["slot"], "name": b["name"], "kind": "", "ids": b["ids"], "functions": 0,
                          "drivers": [], "no_driver": [], "narrow": False, "slow": False,
                          "cur": "", "max": "", "health": b["health"], "seen": "ambiguous" if cand else "bmc"})
    cards.sort(key=_slot_order)
    return cards


def _slot_order(c):
    s = c["slot"]
    return (0, int(s), c["dev"]) if s.isdigit() and s != "0" else (1, 0, c["dev"])


def raid_volumes(osd, bmc):
    """[{controller, name, raid, size_bytes, drives: [드라이브], os_disk, state, health}]"""
    drives = dict((d["id"], d) for d in (bmc or {}).get("drives", []))
    local = [d for d in osd["disks"] if d["transport"] in ("local", "nvme") and d["size_bytes"]]
    out = []
    for v in (bmc or {}).get("volumes", []):
        os_disk, candidates = "", []
        if v["size_bytes"]:
            near = [d for d in local if abs(d["size_bytes"] - v["size_bytes"]) <= v["size_bytes"] * 0.01]
            candidates = [d["name"] for d in near]
            os_disk = candidates[0] if len(candidates) == 1 else ""
        out.append(dict(v, drives=[drives[i] for i in v["drives"] if i in drives],
                        os_disk=os_disk, os_candidates=candidates))
    return out


def unassigned_drives(bmc):
    """어느 RAID 볼륨에도 속하지 않은 드라이브 (볼륨 정보를 받은 경우에만 의미 있음)"""
    if not (bmc or {}).get("volumes"):
        return []
    used = set(i for v in bmc["volumes"] for i in v["drives"])
    return [d for d in bmc["drives"] if d["id"] not in used]
