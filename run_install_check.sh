#!/bin/sh
# 대상 Linux 서버에서 실행한다. 모듈과 보고서 기본 위치는 실행기 기준이다.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 2
for INSTALL_PYTHON in python3 /usr/libexec/platform-python python; do
    if command -v "$INSTALL_PYTHON" >/dev/null 2>&1 &&
       "$INSTALL_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 6) else 1)' 2>/dev/null; then
        exec "$INSTALL_PYTHON" "$SCRIPT_DIR/serverinstall.py" -o "$SCRIPT_DIR/reports" "$@"
    fi
done
printf '%s\n' 'Python 3.6 or newer is required (python3 or /usr/libexec/platform-python).' >&2
exit 2
