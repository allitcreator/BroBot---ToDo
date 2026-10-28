"""Откат «порядка дня»: снимает служебный блок BroBot из заметок всех задач
(открытых и закрытых). Текст заметки над блоком и после него не меняется.

По умолчанию только считает; пишет с `--apply`. Названия задач не выводит.

Запуск:
    ssh vps4 "cd ~/todobot && docker compose exec -T todobot python -" < deploy/strip_task_meta.py
    ssh vps4 "cd ~/todobot && docker compose exec -T todobot python - --apply" < deploy/strip_task_meta.py
"""
import asyncio
import sys

from services import ms_todo, task_meta


async def all_tasks() -> list[dict]:
    """Все задачи списка, открытые и закрытые. Без потолка `get_tasks`
    (500 задач): блок могли оставить в задаче, закрытой давно."""
    data = await ms_todo._request("GET", ms_todo._list_path(), params={"$top": "100"})
    tasks = list(data.get("value", []))
    while next_link := data.get("@odata.nextLink"):
        data = await ms_todo._request("GET", next_link.removeprefix(ms_todo.GRAPH_URL))
        tasks.extend(data.get("value", []))
    return tasks


async def main(write: bool) -> int:
    tasks = await all_tasks()
    found = failed = 0
    for task in tasks:
        content = (task.get("body") or {}).get("content") or ""
        stripped = task_meta.apply(content, task_meta.Meta())
        if stripped == content:
            continue
        found += 1
        if not write:
            continue
        try:
            await ms_todo.update_task_body(task["id"], stripped)
        except Exception as exc:
            failed += 1
            print(f"Не удалось: {task['id'][-8:]} ({type(exc).__name__})")
    print(f"Задач всего: {len(tasks)}, с блоком: {found}" + (f", снято: {found - failed}" if write else " (без --apply ничего не записано)"))
    await ms_todo.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--apply" in sys.argv[1:])))
