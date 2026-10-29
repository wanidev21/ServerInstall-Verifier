# 서버 설치 검증기

납품확인서와 실제 설치 결과를 대조하면서 고객에게 장비 구성과 상태를 설명하기 위해 만들었습니다. 납품 사양과의 최종 비교는 엔지니어가 수행합니다.

### 주요 기능

- BMC(Redfish)와 OS의 CPU·메모리·PCIe·NIC·RAID 정보 대조
- PASS·WARN·FAIL·INCOMPLETE 구분 및 미수집 항목 표시
- 콘솔 요약과 반출용 단일 HTML 보고서

## 내려받기

```sh
git clone https://github.com/wanidev21/ServerInstall-Verifier.git
cd ServerInstall-Verifier
```

설치가 끝난 Linux 서버에서 BMC와 OS 정보를 비교하는 도구입니다. 이 폴더 전체만 복사하면 되며 로그 분석기는 필요하지 않습니다. Python 3.6 이상이 필요합니다.

## 대상 서버에서 실행

압축을 풀고 `ServerInstall-Verifier` 폴더로 이동한 뒤 root로 실행하세요.

```sh
sh ./run_install_check.sh --bmc-user USERID
```

BMC 암호는 실행 중에 입력합니다. Python 3가 없으면 RHEL 8의 `/usr/libexec/platform-python`을 자동으로 찾습니다. 서버 설정은 변경하지 않습니다.

보고서는 이 폴더의 `reports/`에 생성됩니다. 저장 위치를 지정하려면:

```sh
sh ./run_install_check.sh --bmc-user USERID -o /tmp/install-reports
```

BMC 연결 없이 OS만 확인하려면:

```sh
sh ./run_install_check.sh --no-bmc
```

기본 출력 폴더에 쓰기 권한이 없으면 `-o`로 쓸 수 있는 위치를 지정하세요. Windows에서는 도움말 확인과 오프라인 자료 시험만 가능합니다.

## 결과 확인

- PASS: 수집한 정보에서 지원 규칙에 해당하는 이상·주의·미확인 항목 없음
- WARN: 확인이 필요한 주의 항목 있음
- INCOMPLETE: 읽지 못했거나 대응을 확정하지 못한 항목 있음
- FAIL: 이상 항목 있음

종료 코드는 PASS·WARN 0, FAIL 1, INCOMPLETE·실행 오류 2입니다. 생성된 HTML을 PC로 가져와 브라우저에서 확인하세요.

다른 서버로 옮길 때는 `serverinstall.py`, `serverinstall/`, `servercommon/`, 실행기를 포함한 폴더 전체를 복사하세요. 실제 XCC·RHEL 서버 검증은 아직 필요합니다.

상세 설명: [사용 설명서](MANUAL.md)

## 실행 환경과 검증 범위

Python 추가 패키지는 필요하지 않습니다. 전체 OS 수집에는 `dmidecode`, `lspci`(pciutils),
`lsblk`, `ip` 등 대상 OS의 점검 명령과 root 권한이 필요합니다. 도구는 패키지를 자동 설치하지 않습니다.
명령이나 권한이 없으면 해당 정보를 미확인으로 남기므로 INCOMPLETE 결과가 나올 수 있습니다.
실제 XCC·RHEL 서버에서의 수집 경로·구성 대조 검증은 아직 필요합니다.

## 개발용 테스트

```sh
python -m unittest discover -s tests -v
```

테스트는 Python 3.8 이상에서 실행합니다. 실제 고객 로그·접속 정보는 포함하지 않습니다.
공통 분류 규칙인 `servercommon/`도 이 저장소에 포함되어 있으므로 다른 도구 저장소가 필요하지 않습니다.
