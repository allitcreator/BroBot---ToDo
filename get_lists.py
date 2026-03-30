"""Выводит все списки MS Todo с их ID"""
import asyncio
import httpx
from dotenv import load_dotenv
import os

load_dotenv()

TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_URL = "https://graph.microsoft.com/v1.0"


async def main():
    async with httpx.AsyncClient() as client:
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
        print("Токен получен\n")

        resp = await client.get(
            f"{GRAPH_URL}/me/todo/lists",
            headers={"Authorization": f"Bearer {token}"},
        )
        if not resp.is_success:
            print(f"Ошибка: {resp.text}")
            return

        lists = resp.json().get("value", [])
        print("Твои списки MS Todo:\n")
        for lst in lists:
            print(f"  Название: {lst['displayName']}")
            print(f"  ID:       {lst['id']}")
            print()

asyncio.run(main())
