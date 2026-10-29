"""점검 결과 → 화면 요약(텍스트)과 반출용 단일 HTML 보고서."""

import html
import re
import unicodedata
from collections import Counter
from datetime import datetime

from . import APP_NAME, __version__
from .inventory import pcie_cards, raid_volumes, unassigned_drives
from .checks import _slot_key

STATUS_META = {  # 화면 표시, HTML 배지 클래스
    "fail": ("이상", "fail"), "warn": ("주의", "warn"), "ok": ("정상", "ok"),
    "info": ("참고", "info"), "skip": ("생략", "skip"),
}


def esc(v):
    return html.escape(str(v if v is not None else ""))


def ident(osd, bmc):
    model = (bmc or {}).get("sku") or (bmc or {}).get("model") or osd.get("product") or "?"
    serial = (bmc or {}).get("serial") or osd.get("serial") or "?"
    return model, serial


AREA_EN = {"시스템": "System", "메모리": "Memory", "디스크": "Disk", "전원": "Power", "냉각": "Cooling",
           "커널": "Kernel", "시간": "Time", "수집": "Collect"}


def result(items):
    """미수집 항목이 있으면 전체 정상으로 표시하지 않는다."""
    statuses = set(it["status"] for it in items)
    for status, label in (("fail", "FAIL"), ("skip", "INCOMPLETE"), ("warn", "WARN")):
        if status in statuses:
            return label
    return "PASS" if "ok" in statuses else "INCOMPLETE"


def console(items, osd, bmc, out_path, width=80):
    """모니터에 바로 보여 줄 짧은 요약 (결과·문제 영역·주요 사양·보고서 경로).
    GUI 없는 서버의 텍스트 콘솔은 한글을 못 그리므로 영문 ASCII 로만 쓴다. 자세한 내용은 HTML 보고서."""
    model, serial = ident(osd, bmc)
    st = set(it["status"] for it in items)
    verdict = result(items)
    lines = ["", "=== Install Check: %s / %s (%s) ===" % (model, serial, osd.get("host") or "?"),
             "Result : %s" % verdict]
    for status, label in (("fail", "FAIL"), ("warn", "WARN"), ("skip", "SKIP")):
        areas = _unique(AREA_EN.get(it["area"], it["area"]) for it in items if it["status"] == status)
        if areas:
            lines.append("  %-5s: %s" % (label, ", ".join(areas)))
    lines += _spec_lines(osd, bmc)
    lines = [_cut(l, width, "...") for l in lines]
    lines.append("Report : %s" % out_path)  # 파일을 찾아야 하므로 길어도 자르지 않는다
    return "\n".join(lines)


def _spec_lines(osd, bmc):
    cpus = (bmc or {}).get("cpus") or osd["cpus"]
    # 'Intel(R) Xeon(R) Gold 5222 CPU @ 3.80GHz' → 'Xeon Gold 5222'
    short = re.sub(r"\((R|TM)\)|\s*CPU\b.*$|\s*@.*$|\bIntel\b|\bAMD\b|\s*\d+-Core Processor.*$", "",
                   cpus[0]["model"] if cpus else "?").strip()
    dimms = (bmc or {}).get("dimms") or osd.get("dimms") or []
    if dimms:
        mem = "%sGB (%s)" % (_g(sum(d["size_gib"] for d in dimms)), _size_mix_en(dimms))
    else:
        mem = "%.0fGiB" % (osd.get("mem_total_gib") or 0)
    out = ["CPU    : %d x %s" % (len(cpus), short), "Memory : %s" % mem]
    vols = raid_volumes(osd, bmc)
    if vols:
        out.append("RAID   : " + ", ".join("%s %s" % (v["raid"] or "?", _b(v["size_bytes"])) for v in vols))
    if bmc and bmc["drives"]:
        out.append("Drives : %d" % len(bmc["drives"]))
    cards = pcie_cards(osd, bmc)
    nics = [n for n in osd["nics"] if n["driver"] != "cdc_ether"]
    out.append("PCIe   : %d cards (slots %s) | NIC ports %d up / %d" % (
        len(cards), ",".join(c["slot"] for c in cards if c["slot"]) or "-",
        len([n for n in nics if n["link"]]), len(nics)))
    return out


def _size_mix_en(dimms):
    c = Counter(_g(d["size_gib"]) for d in dimms)
    return ", ".join("%sGB x%d" % (k, v) for k, v in sorted(c.items(), key=lambda kv: -float(kv[0])))


def _unique(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _w(ch):
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def _cut(s, width, mark="…"):
    """표시 폭 기준으로 잘라 줄바꿈되지 않게 한다."""
    out, w = [], 0
    for ch in s:
        if w + _w(ch) > width - len(mark):
            return "".join(out) + mark
        out.append(ch)
        w += _w(ch)
    return s


# ---------------------------------------------------------------- HTML

def _table(headers, rows, cls=""):
    if not rows:
        return '<p class="muted">없음</p>'
    h = "".join("<th>%s</th>" % esc(x) for x in headers)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % esc(c) for c in r) for r in rows)
    return '<div class="tw"><table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (cls, h, body)


def _section(title, body, sub=""):
    return '<section><h2>%s%s</h2>%s</section>' % (esc(title), " <small>%s</small>" % esc(sub) if sub else "", body)


def render_html(items, osd, bmc, bmc_error=None):
    model, serial = ident(osd, bmc)
    c = Counter(it["status"] for it in items)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    tiles = "".join(
        '<div class="tile t-%s"><div class="tile-label">%s</div><div class="tile-value">%d</div></div>'
        % (k, STATUS_META[k][0], c[k]) for k in ("fail", "warn", "ok", "info", "skip"))

    li = []
    for it in items:
        lab, cls = STATUS_META[it["status"]]
        det = "".join("<li>%s</li>" % esc(d) for d in it["details"] if d)
        li.append('<li class="item s-%s"><span class="badge b-%s">%s</span><span class="area">%s</span>'
                  '<div><div class="title">%s</div>%s</div></li>'
                  % (cls, cls, lab, esc(it["area"]), esc(it["title"]), "<ul>%s</ul>" % det if det else ""))
    checks = '<ul class="items">%s</ul>' % "".join(li)

    sysinfo = [("모델", model), ("시리얼", serial), ("호스트", osd.get("host")), ("OS", osd.get("os_name")),
               ("커널", osd.get("kernel")), ("BMC 전원 상태", (bmc or {}).get("power")),
               ("BIOS/UEFI", (bmc or {}).get("bios")), ("BMC 펌웨어", (bmc or {}).get("bmc_fw")),
               ("OS 데이터", osd.get("source")), ("시간대", osd["time"].get("tz"))]
    kv = '<dl class="grid">%s</dl>' % "".join(
        '<div class="kv"><dt>%s</dt><dd>%s</dd></div>' % (esc(k), esc(v or "-")) for k, v in sysinfo)

    sections = [_section("점검 결과", checks), _section("시스템", kv)]
    sections += _inventory(osd, bmc)
    if bmc_error:
        sections.append(_section("BMC 접속", "<p>%s</p>" % esc(bmc_error)))

    return HTML.format(
        title=esc("%s - %s %s" % (APP_NAME, model, serial)), app=esc(APP_NAME), ver=esc(__version__),
        result=esc(result(items)),
        model=esc(model), serial=esc(serial), host=esc(osd.get("host") or ""), now=esc(now), tiles=tiles,
        body="".join(sections))


def _inventory(osd, bmc):
    out = []
    # 메모리: 슬롯 기준 나란히
    bd = {(_slot_num(d["slot"])): d for d in (bmc or {}).get("dimms", [])}
    od = {(_slot_num(d["slot"])): d for d in (osd.get("dimms") or [])}
    rows = []
    for k in sorted(set(bd) | set(od), key=lambda x: (isinstance(x, str), x)):
        b, o = bd.get(k, {}), od.get(k, {})
        rows.append([b.get("slot") or o.get("slot"), _g(b.get("size_gib")), _g(o.get("size_gib")),
                     o.get("configured_speed") or b.get("speed") or "", o.get("part") or b.get("part", ""),
                     " / ".join(x for x in (b.get("state"), b.get("health")) if x)])
    out.append(_section("메모리", _table(["슬롯", "BMC(GB)", "OS(GB)", "속도(MT/s)", "부품 번호", "BMC 상태"], rows)))

    rows = []
    for c in pcie_cards(osd, bmc):
        link = c["cur"] if c["cur"] == c["max"] else "%s%s (최대 %s)" % (c["cur"], " ▼" if c["narrow"] else "", c["max"])
        n = c["functions"]
        rows.append([c["slot"] or "온보드", c["name"], c["kind"],
                     ("포트 %d" % n if c["kind"] in ("네트워크", "FC") else str(n)) if n else "",
                     ", ".join(c["drivers"]) or ("(없음)" if c["no_driver"] else ""), link,
                     {"both": c["health"] or "확인", "os": "OS만", "bmc": "OS 미인식", "ambiguous": "장치 대조 미확정"}[c["seen"]]])
    out.append(_section("PCIe 카드", _table(["슬롯", "카드", "종류", "기능", "드라이버", "링크", "BMC 대조"], rows),
                        "슬롯 순서, 온보드는 마지막"))

    rows = [[n["name"], n["mac"], n["driver"], n["bus"], {True: "연결", False: "미연결"}.get(n["link"], "?"),
             _sp(n["speed"]), ", ".join(n["ipv4"])] for n in osd["nics"]]
    out.append(_section("네트워크 포트 (OS)", _table(["이름", "MAC", "드라이버", "버스", "링크", "속도", "IPv4"], rows)))

    rows = [[d["name"], d["transport"], _b(d["size_bytes"]), (d["vendor"] + " " + d["model"]).strip()]
            for d in osd["disks"] if d["transport"] in ("local", "nvme", "usb")]
    for t in ("fc", "iscsi"):  # 외부 스토리지 LUN 은 수십 개라 한 줄로 묶는다
        luns = [d for d in osd["disks"] if d["transport"] == t]
        if luns:
            models = sorted(set((d["vendor"] + " " + d["model"]).strip() for d in luns))
            rows.append(["LUN %d개" % len(luns), t, "합계 " + _b(sum(d["size_bytes"] or 0 for d in luns)),
                         ", ".join(models)])
    out.append(_section("디스크 (OS)", _table(["이름", "연결", "용량", "모델"], rows)))

    if bmc:
        rows = [[d["name"], d["controller"], d["model"], _b(d["size_bytes"]), d["media"], d["state"], d["health"]]
                for d in bmc["drives"]]
        out.append(_section("드라이브 (BMC)", _table(["이름", "컨트롤러", "모델", "용량", "종류", "상태", "건강"], rows)))
        vols = raid_volumes(osd, bmc)
        if vols or bmc["drives"]:
            rows = [[v["controller"], v["name"], v["raid"] or "?", _b(v["size_bytes"]),
                     ", ".join(d["name"] for d in v["drives"]) or "-",
                     v["os_disk"] or ("후보: " + ", ".join(v["os_candidates"]) if v["os_candidates"] else "미확인"),
                     " / ".join(x for x in (v["state"], v["health"]) if x)] for v in vols]
            free = unassigned_drives(bmc)
            rows += [[d["controller"], "(볼륨 없음)", "-", _b(d["size_bytes"]), d["name"], "-", ""] for d in free]
            out.append(_section("RAID 구성", _table(
                ["컨트롤러", "볼륨", "RAID", "용량", "구성 드라이브", "OS 디스크", "상태"], rows)))
        rows = [[p["name"], p["model"], p["watts"] or "", p["state"], p["health"]] for p in bmc["psus"]]
        rows += [["이중화: " + r["name"], r["mode"], "", r["state"], r["health"]] for r in bmc["power_redundancy"]]
        out.append(_section("전원", _table(["이름", "모델", "용량(W)", "상태", "건강"], rows)))
        out.append(_section("펌웨어", _table(["항목", "버전"], [[f["name"], f["version"]] for f in bmc["firmware"]])))
    return out


def _slot_num(s):
    return _slot_key(s)


def _g(v):
    if v is None:
        return ""
    return ("%.0f" % v) if abs(v - round(v)) < 0.05 else ("%.1f" % v)


def _sp(mbps):
    return ("%dG" % (mbps // 1000) if mbps >= 1000 else "%dM" % mbps) if mbps else ""


def _b(n):
    if not n:
        return ""
    return "%.1fTB" % (n / 1e12) if n >= 1e12 else "%.1fGB" % (n / 1e9)


HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --border: rgba(11,11,11,0.10);
  --crit: #d03b3b; --warn: #fab219; --good: #0ca30c; --info: #898781; --accent: #2a78d6;
  --crit-bg: rgba(208,59,59,0.08); --warn-bg: rgba(250,178,25,0.12);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --border: rgba(255,255,255,0.10); --crit-bg: rgba(208,59,59,0.16); --accent: #3987e5;
  }}
}}
:root[data-theme="dark"] {{
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --border: rgba(255,255,255,0.10); --crit-bg: rgba(208,59,59,0.16); --accent: #3987e5;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.55 "Pretendard", "Malgun Gothic", "Apple SD Gothic Neo", system-ui, sans-serif; }}
.wrap {{ max-width: 1200px; margin: 0 auto; padding: 24px 16px 64px; }}
header h1 {{ font-size: 24px; margin: 0 0 4px; }}
header .sub {{ color: var(--ink-2); font-size: 13px; }}
section {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 20px; margin-top: 20px; }}
h2 {{ font-size: 17px; margin: 0 0 12px; }}
h2 small {{ font-weight: normal; color: var(--muted); font-size: 13px; margin-left: 6px; }}
.muted {{ color: var(--muted); }}
dl.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px 24px; margin: 0; }}
.kv dt {{ color: var(--muted); font-size: 12px; }}
.kv dd {{ margin: 0; word-break: break-all; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; margin-top: 20px; }}
.tile {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; }}
.tile-label {{ color: var(--ink-2); font-size: 12px; }}
.tile-value {{ font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; }}
.tile.t-fail {{ border-left: 4px solid var(--crit); }}
.tile.t-warn {{ border-left: 4px solid var(--warn); }}
.tile.t-ok {{ border-left: 4px solid var(--good); }}
.items {{ list-style: none; margin: 0; padding: 0; }}
.item {{ display: grid; grid-template-columns: auto 90px 1fr; gap: 12px; padding: 10px 0; border-top: 1px solid var(--grid); align-items: start; }}
.item:first-child {{ border-top: 0; }}
.item.s-fail {{ background: var(--crit-bg); margin: 0 -20px; padding: 10px 20px; }}
.item.s-warn {{ background: var(--warn-bg); margin: 0 -20px; padding: 10px 20px; }}
.area {{ color: var(--ink-2); font-size: 13px; padding-top: 2px; }}
.title {{ font-weight: 600; }}
.item ul {{ margin: 4px 0 0; padding-left: 18px; color: var(--ink-2); font-size: 13px; }}
.badge {{ display: inline-block; min-width: 44px; text-align: center; padding: 2px 8px; border-radius: 12px;
  font-size: 12px; font-weight: 700; color: #fff; }}
.b-fail {{ background: var(--crit); }}
.b-warn {{ background: var(--warn); color: #0b0b0b; }}
.b-ok {{ background: var(--good); }}
.b-info {{ background: var(--info); }}
.b-skip {{ background: transparent; color: var(--muted); border: 1px solid var(--border); }}
.tw {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--grid); vertical-align: top; }}
th {{ color: var(--muted); font-weight: 600; white-space: nowrap; }}
footer {{ margin-top: 24px; color: var(--muted); font-size: 12px; text-align: center; }}
@media (max-width: 600px) {{ .item {{ grid-template-columns: auto 1fr; }} .item > div {{ grid-column: 1 / -1; }} }}
@media print {{ section {{ break-inside: avoid; }} }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>{model} · {serial}</h1>
  <div class="sub">{host} · 점검 {now} · {app} v{ver}</div>
  <p><strong>종합 결과: {result}</strong> · INCOMPLETE 는 확인하지 못한 항목이 있다는 뜻입니다.</p>
</header>
<div class="tiles">{tiles}</div>
{body}
<footer>{app} v{ver}</footer>
</div>
</body>
</html>
"""
