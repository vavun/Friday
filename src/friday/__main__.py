"""Точка входу для запуску пакета: python -m friday."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import logging

from friday.app import FridayApp
from friday.config import load_config, setup_logging
from friday.util.timing import measure_time


def _acquire_core_mutex() -> tuple[object | None, bool]:
    """Захищає ядро FRIDAY від випадкового запуску двох копій одночасно."""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ctypes.set_last_error(0)
        handle = k32.CreateMutexW(None, False, "Local\\FridayCoreSingleInstanceMutex")
        already_running = ctypes.get_last_error() == 183
        return handle, not already_running
    except Exception:
        return None, True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Розбирає аргументи командного рядка."""
    parser = argparse.ArgumentParser(description="FRIDAY — локальний голосовий асистент")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ініціалізувати конфігурацію, перевірити готовність і одразу вийти",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Ініціалізує конфіг, логування та запускає асистента."""
    args = parse_args(argv)
    log_file = setup_logging()
    log = logging.getLogger("friday.main")

    with measure_time("startup.load_config"):
        config = load_config()

    log.info("Лог-файл: %s", log_file)
    log.info("Завантажено білий список програм: %s", list(config.apps.keys()))

    mutex_handle: object | None = None
    if not args.once:
        mutex_handle, acquired = _acquire_core_mutex()
        if not acquired:
            log.warning("Ядро FRIDAY вже запущено в іншому процесі — другий екземпляр завершено.")
            return 0

    app = FridayApp(config=config)
    try:
        asyncio.run(app.run(once=args.once))
    except KeyboardInterrupt:
        log.info("Зупинено користувачем (Ctrl+C).")
    finally:
        if mutex_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(mutex_handle)
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
