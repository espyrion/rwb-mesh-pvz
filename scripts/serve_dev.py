"""Статический сервер UI + прокси на API — без Docker, только стандартная библиотека.

Зачем: фронтенд (`web/src`) обращается к относительному пути `/api/v1`, поэтому открыть
`index.html` напрямую или отдать каталог через `python -m http.server` нельзя — запросы к API
уйдут в никуда. Этот скрипт делает то же, что nginx в контейнере `web`: отдаёт файлы
`web/src` и проксирует `/api/`, `/health`, `/docs`, `/openapi.json` в запущенный API.

Запуск (API уже слушает 8000):

    python3 scripts/serve_dev.py

Опции через переменные окружения:
    PORT      — порт UI (по умолчанию 5173)
    API_URL   — адрес API (по умолчанию http://127.0.0.1:8000)
"""

from __future__ import annotations

import http.client
import os
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web" / "src"
API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000")
PORT = int(os.environ.get("PORT", "5173"))

# Префиксы, которые уходят в API, а не в статику (как в web/nginx.conf).
PROXY_PREFIXES = ("/api/", "/health", "/docs", "/openapi.json")


class Handler(SimpleHTTPRequestHandler):
    """Отдаёт статику из web/src и проксирует API-запросы в uvicorn."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — имя задано базовым классом
        if self.path.startswith(PROXY_PREFIXES):
            sys.stderr.write("  api %s\n" % self.path)

    def _should_proxy(self) -> bool:
        return self.path.startswith(PROXY_PREFIXES)

    def _proxy(self, method: str) -> None:
        url = urllib.parse.urlsplit(API_URL)
        host = url.hostname or "127.0.0.1"
        conn_cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(host, url.port or (443 if url.scheme == "https" else 80), timeout=60)

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        headers = {k: v for k, v in self.headers.items() if k.lower() not in {"host", "connection"}}
        headers["Host"] = url.netloc
        try:
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            payload = resp.read()
        except OSError as exc:
            self.send_error(502, f"API недоступен по {API_URL}: {exc}")
            return
        finally:
            conn.close()

        self.send_response(resp.status)
        for key, value in resp.getheaders():
            if key.lower() not in {"transfer-encoding", "connection", "content-length"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 — имена заданы базовым классом
        if self._should_proxy():
            self._proxy("GET")
        else:
            super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802
        if self._should_proxy():
            self._proxy("HEAD")
        else:
            super().do_HEAD()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy("POST") if self._should_proxy() else self.send_error(405)

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy("PUT") if self._should_proxy() else self.send_error(405)

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy("PATCH") if self._should_proxy() else self.send_error(405)

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy("DELETE") if self._should_proxy() else self.send_error(405)


def main() -> int:
    if not (WEB_DIR / "index.html").is_file():
        print(f"Не найден {WEB_DIR / 'index.html'} — запустите скрипт из корня репозитория.", file=sys.stderr)
        return 1
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as exc:
        # Самая частая причина — порт занят (в том числе контейнером mesh-web из Docker).
        print(f"Не удалось занять порт {PORT}: {exc}", file=sys.stderr)
        print(f"Порт занят? Проверьте: ss -ltnp | grep {PORT} (Linux) или "
              f"netstat -ano | findstr {PORT} (Windows).", file=sys.stderr)
        print(f"Либо запустите на другом порту: PORT=8080 python3 scripts/serve_dev.py", file=sys.stderr)
        return 2
    print(f"UI:  http://localhost:{PORT}")
    print(f"API: {API_URL} (проксируются /api/, /health, /docs, /openapi.json)")
    print("Ctrl+C — остановить.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
