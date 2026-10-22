"""Точка входу для запуску пакета: python -m friday."""

from __future__ import annotations

import argparse
import asyncio
import logging

from friday.app import FridayApp
from friday.config import load_config, setup_logging
from friday.util.timing import measure_time


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

    app = FridayApp(config=config)
    try:
        asyncio.run(app.run(once=args.once))
    except KeyboardInterrupt:
        log.info("Зупинено користувачем (Ctrl+C).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
