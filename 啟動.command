#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  task_python=""
  for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3,10))'; then
      task_python="$candidate"
      break
    fi
  done
  if [[ -z "$task_python" ]]; then
    print '需要 Python 3.10 以上。請先安裝 Python 再重試。'
    read '?按 Enter 離開'
    exit 1
  fi
  "$task_python" -m venv .venv || exit 1
  .venv/bin/python -m pip install -r requirements.txt || exit 1
fi
(.venv/bin/python -c 'import time, webbrowser; time.sleep(2); webbrowser.open("http://127.0.0.1:8876")') &
exec .venv/bin/python app.py
