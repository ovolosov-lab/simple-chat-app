import asyncio
from typing import Callable
from loguru import logger
from datetime import datetime, timedelta


class AsyncPeriodicTask:
    def __init__(self, interval: int, task_func: Callable):
        self.interval: int = interval
        self.task_func: Callable = task_func
        self._task: asyncio.Task | None = None
        self._is_running: bool = False  # флаг - запущена-ли задача

    async def _run(self):
        while self._is_running:
            try:
                await self.task_func()
            except Exception as e:
                logger.error(f"Error in background task: {e}")
            await asyncio.sleep(self.interval)          # спим заданный интервал до след. запуска задачи

    def start(self):
        if not self._is_running:
            self._is_running = True
            # Создаем задачу в текущем Event Loop
            self._task = asyncio.create_task(self._run())
            logger.info(f"Background task started (interval {self.interval}s)")

    async def stop(self):
        if self._is_running:
            self._is_running = False
            if self._task:
                self._task.cancel()  # Прерываем asyncio.sleep
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            logger.info("Background task stopped.")

class AsyncDailyTask:
    def __init__(self, target_hour: int, target_minute: int, task_func: Callable):
        self.target_hour: int = target_hour
        self.target_minute: int = target_minute
        self.task_func: Callable = task_func
        self._task: asyncio.Task | None = None
        self._is_running: bool = False

    async def _run(self):
        while self._is_running:
            now = datetime.now()
            target = now.replace(hour=self.target_hour, minute=self.target_minute, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            sleep_seconds = (target - now).total_seconds()
            
            try:
                await asyncio.sleep(sleep_seconds)
            except asyncio.CancelledError:
                break
                
            if self._is_running:
                try:
                    await self.task_func()
                except Exception as e:
                    logger.error(f"Error in daily background task: {e}")

    def start(self):
        if not self._is_running:
            self._is_running = True
            self._task = asyncio.create_task(self._run())
            logger.info(f"Daily background task started (target {self.target_hour:02d}:{self.target_minute:02d})")

    async def stop(self):
        if self._is_running:
            self._is_running = False
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            logger.info("Daily background task stopped.")