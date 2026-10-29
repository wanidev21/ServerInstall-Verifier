#!/usr/bin/env python3
"""서버 설치 검증기 - 새로 설치한 서버의 OS 안에서 실행 (root 권장).

    python3 serverinstall.py [--bmc-user USERID] [-o 출력폴더]

RHEL 8 처럼 python3 명령이 없으면:  /usr/libexec/platform-python serverinstall.py
BMC 암호는 SERVERINSTALL_BMC_PASSWORD 환경 변수로 주거나 실행 중에 입력한다.
화면에 판정 요약을 보여 주고 install_check_<시리얼>_<시각>.html 을 만든다.

개발·검증용: --sos <풀어 둔 sosreport 폴더>, --redfish-mock <DMTF mockup 폴더>
"""

import argparse
import getpass
import io
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from serverinstall import APP_NAME, __version__, checks, oscollect, redfish, report, source  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="%s v%s - 신규 설치 서버의 HW·OS 점검" % (APP_NAME, __version__))
    ap.add_argument("--bmc-host", default=redfish.DEFAULT_HOST,
                    help="BMC 주소 (기본: XCC LAN over USB %s)" % redfish.DEFAULT_HOST)
    ap.add_argument("--bmc-user", help="BMC 계정. 지정하지 않으면 물어본다")
    ap.add_argument("--no-bmc", action="store_true", help="BMC 를 읽지 않고 OS 만 점검")
    ap.add_argument("-o", "--output", default=".", help="보고서를 만들 폴더 (기본: 현재 폴더)")
    ap.add_argument("--sos", metavar="DIR", help=argparse.SUPPRESS)
    ap.add_argument("--redfish-mock", metavar="DIR", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    # 화면 문구는 영문만 쓴다: GUI 없는 서버의 텍스트 콘솔은 한글을 못 그린다 (보고서는 한글)
    def say(msg):
        print(msg, file=sys.stderr)

    if not args.sos and not sys.platform.startswith("linux"):
        say("Error: live checks require Linux. Use --sos for offline validation.")
        return 2
    if args.redfish_mock and not os.path.isdir(args.redfish_mock):
        say("Error: Redfish mock directory does not exist.")
        return 2
    try:
        src = source.SosSource(args.sos) if args.sos else source.LiveSource()
    except ValueError:
        say("Error: sosreport directory does not exist.")
        return 2
    if src.kind == "live" and hasattr(os, "geteuid") and os.geteuid() != 0:
        say("Note: run as root, otherwise dmidecode and some data cannot be read.")

    say("Collecting OS data...")
    osd = oscollect.collect(src)

    bmc, bmc_error = None, None
    if not args.no_bmc:
        if args.redfish_mock:
            client = redfish.Client(mock_dir=args.redfish_mock)
        else:
            user = args.bmc_user or _ask("BMC user (Enter to skip BMC checks): ")
            if user:
                pw = os.environ.get("SERVERINSTALL_BMC_PASSWORD") or getpass.getpass("BMC password: ")
                client = redfish.Client(args.bmc_host, user, pw)
            else:
                client, bmc_error = None, "BMC 계정을 입력하지 않아 생략했습니다."
        if client:
            target = args.redfish_mock or client.base
            say("Collecting BMC data (%s)..." % target)
            try:
                bmc = redfish.collect(client)
            except redfish.RedfishError as e:
                bmc_error = str(e)
                say("Cannot read BMC (unreachable or login failed). HW checks skipped.")
    else:
        bmc_error = "--no-bmc 로 BMC 점검을 생략했습니다."

    items = checks.run(osd, bmc, bmc_error)
    model, serial = report.ident(osd, bmc)
    try:
        os.makedirs(args.output, exist_ok=True)
        out = os.path.abspath(os.path.join(args.output, "install_check_%s_%s.html"
                                           % (_safe(serial), datetime.now().strftime("%Y%m%d_%H%M%S_%f"))))
        with open(out, "w", encoding="utf-8") as f:
            f.write(report.render_html(items, osd, bmc, bmc_error))
    except OSError as e:
        say("Error: cannot write HTML report (errno %s). Check output directory and permissions." % e.errno)
        return 2
    print(report.console(items, osd, bmc, out, shutil.get_terminal_size((80, 25)).columns))
    return 1 if report.result(items) == "FAIL" else (2 if report.result(items) == "INCOMPLETE" else 0)


def _ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _safe(s):
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in s) or "unknown"


if __name__ == "__main__":
    # 3.6 + C 로캘이면 출력 인코딩이 ascii 라 한글에서 죽는다 (3.7 부터는 자동으로 UTF-8)
    if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)
    sys.exit(main())
