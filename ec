#!/bin/bash
# ec — ecampus-collector 실행 스크립트 (매번 명령어 까먹지 않게)
#
#   ./ec              자동 수집 (= python collect.py --auto)
#   ./ec login        자동 로그인용 아이디/비밀번호를 키체인에 등록 (1회)
#   ./ec analyze      마감/놓친 항목 분석 (= python collect.py --analyze)
#   ./ec cleanup      output/ 잔재 폴더 정리 (중복 병합·개명, 수집 안 함)
#   ./ec manual       수동 캡처 (= python collect.py)
#   ./ec <인자...>    collect.py에 인자 그대로 전달
#                     예: ./ec --auto --menus 공지,과제

cd "$(dirname "$0")" || exit 1

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "⚠️  .venv가 없거나 깨져 있습니다. 다시 만들어 주세요:"
    echo "    python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

case "$1" in
    "")        exec "$PY" collect.py --auto ;;
    login)     exec "$PY" collect.py --save-login ;;
    analyze)   exec "$PY" collect.py --analyze ;;
    cleanup)   exec "$PY" collect.py --cleanup ;;
    manual)    exec "$PY" collect.py ;;
    *)         exec "$PY" collect.py "$@" ;;
esac
