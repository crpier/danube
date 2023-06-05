import asyncio
from collections.abc import Callable, Coroutine

Task = Callable[[], Coroutine[None, None, None]]


async def waiting_task(duration: int = 10) -> None:
    print("Starting task")
    await asyncio.sleep(duration)
    print("Task done")


class TasksService:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._max_concurrent_tasks = 5
        self._task_counter = 0
        self._loop = asyncio.new_event_loop()

    def do_task(self, coro: Task, *args, **kwargs) -> None:
        new_task = self._loop.create_task(
            coro(*args, **kwargs),
            name=f"{coro.__name__}-{self._task_counter}",
        )
        self._task_counter += 1
        self._tasks.add(new_task)
        new_task.add_done_callback(self._tasks.discard)

    def get_tasks(self):
        print(self._tasks)
