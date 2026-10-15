#!/bin/sh
set -eu

# Keep Chromium out of the image layers. This runs once at container startup;
# Playwright caches the browser under PLAYWRIGHT_BROWSERS_PATH for later restarts.
playwright install --with-deps --no-shell chromium

# Chromium cannot authenticate against an upstream SOCKS5 proxy, so when
# SENTINEL_SOLVER_UPSTREAM is set we forward it to an auth-free local socks5 and
# point the browser at 127.0.0.1:1080 instead.
if [ -n "${SENTINEL_SOLVER_UPSTREAM:-}" ]; then
  gost -L socks5://127.0.0.1:1080 -F "$SENTINEL_SOLVER_UPSTREAM" &
  i=0
  while [ "$i" -lt 60 ]; do
    if python -c "import socket,sys;sys.exit(0 if socket.socket().connect_ex(('127.0.0.1',1080))==0 else 1)" 2>/dev/null; then
      break
    fi
    i=$((i + 1))
    sleep 0.5
  done
  echo "local auth-free socks5 ready on 127.0.0.1:1080"
fi

exec python app.py
