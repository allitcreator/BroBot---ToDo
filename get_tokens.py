"""
Скрипт для получения refresh_token для MS и Google.
Запускать локально ОДИН РАЗ, потом вставить токены в .env

Использование:
  python get_tokens.py ms      — получить MS refresh_token
  python get_tokens.py google  — получить Google refresh_token
  python get_tokens.py all     — оба сразу
"""

import sys
import json
import urllib.parse
import http.server
import threading
import webbrowser
import urllib.request
from dotenv import load_dotenv
import os

load_dotenv()

REDIRECT_URI = "http://localhost:8080"
_auth_code = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<html><body><h2>Done! You can close this tab.</h2></body></html>")

    def log_message(self, *args):
        pass


def _wait_for_code(port: int = 8080) -> str:
    global _auth_code
    _auth_code = None
    server = http.server.HTTPServer(("localhost", port), _CallbackHandler)
    while not _auth_code:
        server.handle_request()
    return _auth_code


def get_ms_token():
    client_id = os.getenv("MS_CLIENT_ID")
    client_secret = os.getenv("MS_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("❌ Заполни MS_CLIENT_ID и MS_CLIENT_SECRET в .env")
        return

    scope = "Tasks.ReadWrite offline_access User.Read"
    auth_url = (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?"
        + urllib.parse.urlencode({
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": scope,
            "response_mode": "query",
        })
    )

    print(f"\n🌐 Открываю браузер для авторизации Microsoft...")
    webbrowser.open(auth_url)

    code = _wait_for_code()
    if not code:
        print("❌ Не удалось получить код авторизации")
        return

    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope,
    }).encode()

    req = urllib.request.Request(
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())

    refresh_token = result.get("refresh_token")
    if refresh_token:
        print(f"\n MS_REFRESH_TOKEN получен!")
        print(f"Добавь в .env:\nMS_REFRESH_TOKEN={refresh_token}\n")
    else:
        print(f"Ошибка: {result}")


def get_google_token():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("❌ Заполни GOOGLE_CLIENT_ID и GOOGLE_CLIENT_SECRET в .env")
        return

    scope = "https://www.googleapis.com/auth/calendar"
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urllib.parse.urlencode({
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": scope,
            "access_type": "offline",
            "prompt": "consent",
        })
    )

    print(f"\n🌐 Открываю браузер для авторизации Google...")
    webbrowser.open(auth_url)

    code = _wait_for_code()
    if not code:
        print("❌ Не удалось получить код авторизации")
        return

    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())

    refresh_token = result.get("refresh_token")
    if refresh_token:
        print(f"\n✅ GOOGLE_REFRESH_TOKEN получен!")
        print(f"Добавь в .env:\nGOOGLE_REFRESH_TOKEN={refresh_token}\n")
    else:
        print(f"❌ Ошибка: {result}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"

    if cmd in ("ms", "all"):
        get_ms_token()

    if cmd in ("google", "all"):
        get_google_token()
