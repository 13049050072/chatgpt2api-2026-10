ARG PYTHON_IMAGE=python:3.13-slim

FROM ${PYTHON_IMAGE}

# Build-time proxy for pip and the Chromium download (same convention as the app image).
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt ./
# 只安装完整 Chromium（新版无头模式），headless shell 过不了 Cloudflare
RUN pip install --no-cache-dir -r requirements.txt

# gost：Chromium 无法为带认证的 SOCKS5 上游做代理认证，入口脚本用它在本机转发一层。
RUN python -c "import gzip, urllib.request; data = urllib.request.urlopen('https://github.com/ginuerzh/gost/releases/download/v2.11.5/gost-linux-amd64-2.11.5.gz', timeout=120).read(); open('/usr/local/bin/gost', 'wb').write(gzip.decompress(data))" \
    && chmod +x /usr/local/bin/gost \
    && /usr/local/bin/gost -V

COPY app.py vm.js entrypoint.sh ./

EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
