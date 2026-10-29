# 서버 설치 검증기 (ServerInstall Verifier)

새로 설치한 서버의 **OS 안에서 한 번 실행**해, HW(BMC)와 OS가 모두 정상인지 점검하는 도구입니다.
서버 설치 → BMC 셋업 → OS 설치가 끝난 뒤에 실행합니다.

- 화면에 판정 요약을 보여 주고, 반출용 HTML 보고서(`install_check_<시리얼>_<시각>.html`)를 만듭니다.
- 인벤토리는 **있는 그대로** 보여 줍니다. 납품 사양과의 비교는 엔지니어가 합니다.
- 핵심은 **BMC와 OS의 교차 확인**입니다. 한쪽만 봐서는 안 보이는 문제를 잡습니다.

## 실행

```sh
# root 로 실행 (dmidecode 등에 필요)
python3 serverinstall.py --bmc-user USERID -o /tmp

# RHEL 8 처럼 python3 명령이 없으면
/usr/libexec/platform-python serverinstall.py --bmc-user USERID -o /tmp
```

- `serverinstall.py`, `serverinstall/`, `servercommon/` 를 함께 복사해야 합니다 (공통 오류 분류 규칙 포함). 이 저장소는 설치 검증기만 포함한 독립 실행 구성입니다.
- Python 3.6 이상, 표준 라이브러리만 씁니다. 추가 Python 패키지는 필요하지 않습니다. 전체 수집에는 dmidecode·lspci 등 OS 점검 명령과 root 권한이 필요합니다.
- Linux 실행기: `sh ./run_install_check.sh --bmc-user USERID`. Python 3와 RHEL 8 platform-python을 찾아 실행하며 보고서는 실행기 옆 `reports/`에 저장합니다. `-o`로 변경할 수 있습니다.
- BMC 암호는 실행 중에 입력하거나 `SERVERINSTALL_BMC_PASSWORD` 환경 변수로 줍니다.
- BMC 는 XCC LAN over USB 주소(`169.254.95.118`)로 접속합니다. 다르면 `--bmc-host` 로 지정합니다.
- BMC 에 닿지 않으면 HW 항목은 **생략**으로 표시하고 OS 점검은 끝까지 진행합니다.
  도구가 서버의 네트워크 설정을 바꾸지는 않습니다.
- 모니터에는 결과(PASS/WARN/FAIL), 문제 영역, 주요 사양(CPU·메모리·RAID·PCIe·NIC), 보고서 경로만 몇 줄로 보여 줍니다.
  GUI 없는 서버의 텍스트 콘솔은 한글을 못 그리므로 화면 문구는 영문입니다. 자세한 내용은 한글 HTML 보고서에 있습니다.
- 종료 코드: 이상(FAIL)은 1, 미확인 항목(INCOMPLETE)이나 실행 오류는 2, PASS·WARN은 0입니다. WARN은 보고서의 주의 항목을 확인해야 합니다.
- 콘솔과 HTML 상단의 종합 결과는 **FAIL / INCOMPLETE / WARN / PASS**입니다. 이상이 있으면 FAIL을 우선하고, 읽지 못한 항목이 있으면 전체 정상으로 판단하지 않습니다. INCOMPLETE여도 수집에 성공한 항목의 결과와 보고서는 남깁니다.

## 점검 항목

| 영역 | 내용 | 비교 |
|---|---|---|
| 시스템 | 시리얼 번호 | BMC ↔ OS(dmidecode) |
| CPU | 개수, 스레드 수, 모델, BMC 상태 | BMC ↔ OS |
| 메모리 | DIMM 개수·용량·슬롯, BMC 상태, OS 가용 메모리 비율, 동작 속도 | BMC ↔ OS |
| PCIe | 카드 단위(슬롯 순)로 BMC 와 짝지음(vendor/device ID·슬롯), OS 미인식 카드, 링크 폭·속도 저하, 드라이버 누락 | BMC ↔ OS |
| NIC | BMC 가 아는 포트(MAC)가 OS 에 모두 보이는지, 링크 상태, LAN over USB IP | BMC ↔ OS |
| 디스크 | BMC 물리 드라이브 상태, OS 로컬 디스크(FC·iSCSI LUN 은 한 줄로 묶음) | BMC, OS |
| RAID | 볼륨별 RAID 수준·구성 드라이브·상태, 대응하는 OS 디스크(용량 기준), 볼륨 밖 드라이브 | BMC ↔ OS |
| 전원·냉각 | PSU·이중화. 팬·온도는 이상일 때만 표시 | BMC |
| 커널 | 커널 로그의 HW·드라이버 오류 | OS |
| OS | 실패한 systemd 서비스, NTP 동기화, BMC↔OS 시계 차이 | OS, BMC ↔ OS |

판정: **이상**(바로 조치) / **주의**(확인 필요) / **정상** / **참고**(있는 그대로 정보) / **생략**(읽지 못함)

- 메모리는 개수·총용량과 함께, 이름을 대응할 수 있는 슬롯별 용량을 비교합니다. CPU1_DIMM1과 CPU2_DIMM1은 별개 슬롯입니다. 슬롯 표기가 달라 특정할 수 없으면 생략으로 남깁니다.
- PCIe의 동일한 vendor/device ID 후보가 여러 개이고 슬롯으로 특정할 수 없으면 임의로 연결하지 않습니다.
- RAID↔OS 디스크 연결은 용량 기반 후보입니다. 같은 용량의 디스크가 여러 개면 후보로 표시하고, 연결 확정이나 정상 인식으로 처리하지 않습니다. RAID 수준에 필요한 구성 드라이브 수가 부족하면 확인을 요청합니다.
- 팬·온도·전원·장치 목록을 읽지 못한 상태를 정상으로 표시하지 않습니다. 정상 결과도 지원하는 규칙과 수집 자료 범위 안에서의 결과입니다.

## 한계

- Redfish 수집은 표준 스키마 기준으로 만들었고 **실제 XCC 에서는 아직 검증하지 않았습니다.**
  첫 실장비 실행 결과를 보고 경로·필드를 맞춰야 합니다.
- Linux 전용입니다. Windows Server 는 OS 수집기만 추가하면 되도록 수집과 판정을 나눠 두었습니다.
- 보고서는 HW 구성에 집중합니다. BMC 이벤트 로그는 넣지 않습니다 (장애 분석은 서버 로그 분석기로).
- RAID 수준은 Redfish 볼륨 정보(RAIDType)에서 읽습니다. 펌웨어가 주지 않으면 "받지 못함"으로 표시합니다.
- 번인(부하) 테스트는 아직 없습니다 (옵션으로 추가 예정).


## 개발용 테스트

```sh
python -m unittest discover -s tests -v
```

테스트는 Python 3.8 이상에서 가상 자료로 실행합니다.
