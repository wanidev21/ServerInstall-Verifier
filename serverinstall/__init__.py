"""서버 설치 검증기 (ServerInstall Verifier).

새로 설치한 서버의 OS 안에서 실행해 HW(BMC)와 OS가 모두 정상인지 점검하고,
화면 요약과 반출용 HTML 보고서를 만든다.

- OS 쪽: /proc, /sys 와 표준 명령(dmidecode, lspci, dmesg 등)을 읽는다 (source.py, oscollect.py)
- HW 쪽: BMC Redfish 를 LAN over USB 로 읽는다 (redfish.py)
- 판정: 두 쪽을 교차 확인한다 (checks.py)

대상 서버의 Python 3.6 (RHEL 8 platform-python) 에서도 돌도록 3.6 문법과 표준 라이브러리만 쓴다.
"""

__version__ = "0.2.0"
APP_NAME = "서버 설치 검증기"
