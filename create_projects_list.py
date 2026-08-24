"""Создаёт список «Проекты» в MS Todo и печатает его ID для .env

Разовая операция, трогает реальный аккаунт. Если список с таким именем уже есть —
не создаёт второй, просто печатает существующий ID.
"""
import asyncio
import httpx
from dotenv import load_dotenv
import os

load_dotenv()

TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_URL = "https://graph.microsoft.com/v1.0"
LIST_NAME = "Проекты"


async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": os.getenv("MS_REFRESH_TOKEN"),
            "client_id": os.getenv("MS_CLIENT_ID"),
            "client_secret": os.getenv("MS_CLIENT_SECRET"),
            "scope": "Tasks.ReadWrite offline_access User.Read",
        })
        if not resp.is_success:
            print(f"Ошибка получения токена: {resp.text}")
            return
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(f"{GRAPH_URL}/me/todo/lists", headers=headers)
        if not resp.is_success:
            print(f"Ошибка чтения списков: {resp.text}")
            return

        for lst in resp.json().get("value", []):
            if lst["displayName"] == LIST_NAME:
                print(f"Список «{LIST_NAME}» уже существует, ничего не создаю.\n")
                print(f"MS_PROJECTS_LIST_ID={lst['id']}")
                return

        resp = await client.post(
            f"{GRAPH_URL}/me/todo/lists",
            headers={**headers, "Content-Type": "application/json"},
            json={"displayName": LIST_NAME},
        )
        if not resp.is_success:
            print(f"Ошибка создания списка: {resp.text}")
            return

        created = resp.json()
        print(f"Список «{LIST_NAME}» создан. Впиши в .env:\n")
        print(f"MS_PROJECTS_LIST_ID={created['id']}")

asyncio.run(main())
