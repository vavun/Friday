"""Інструмент розробника для ручного тестування навичок з командного рядка (python -m friday.dev)."""

from __future__ import annotations

import argparse
import logging

from friday.config import load_config, setup_logging
from friday.skills.apps import AppLauncher
from friday.skills.windows import WindowManager
from friday.util.monitors import list_monitors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FRIDAY Dev CLI — тестування вікон і моніторів")
    sub = parser.add_subparsers(dest="group", required=True)

    # monitors
    sub.add_parser("monitors", help="Показати список моніторів та їх work area")

    # window ...
    win_parser = sub.add_parser("window", help="Керування вікнами")
    win_sub = win_parser.add_subparsers(dest="action", required=True)

    move_p = win_sub.add_parser("move", help="Перемістити вікно на монітор")
    move_p.add_argument("target", help="Цільове вікно (browser, code, telegram, active)")
    move_p.add_argument("monitor", type=int, help="Індекс або номер монітора")
    move_p.add_argument(
        "mode",
        nargs="?",
        default="maximize",
        choices=["maximize", "left", "right", "center"],
    )

    snap_p = win_sub.add_parser("snap", help="Прикріпити вікно (left/right/center)")
    snap_p.add_argument("target")
    snap_p.add_argument("mode", choices=["left", "right", "center"])

    resize_p = win_sub.add_parser("resize", help="Змінити розмір вікна")
    resize_p.add_argument("target")
    resize_p.add_argument("mode", choices=["bigger", "smaller", "maximize", "restore"])

    focus_p = win_sub.add_parser("focus", help="Перевести фокус на вікно")
    focus_p.add_argument("target")

    # app ...
    app_parser = sub.add_parser("app", help="Керування програмами з білого списку")
    app_sub = app_parser.add_subparsers(dest="action", required=True)
    launch_p = app_sub.add_parser("launch", help="Запустити програму з config.toml")
    launch_p.add_argument("name")

    # gesture ...
    gest_parser = sub.add_parser("gesture", help="Запустити режим жестів (Етап A)")
    gest_parser.add_argument(
        "--seconds",
        type=float,
        default=10.0,
        help="Тривалість сесії жестів у секундах",
    )
    gest_parser.add_argument(
        "--mode",
        choices=["drag", "swipe"],
        default=None,
        help="Перевизначити режим (drag або swipe)",
    )

    # telegram ...
    tg_parser = sub.add_parser("telegram", help="Керування Telegram-клієнтом (авторизація Telethon)")
    tg_sub = tg_parser.add_subparsers(dest="action", required=True)
    tg_sub.add_parser("login", help="Інтерактивна одноразова авторизація Telethon")
    tg_sub.add_parser("dialogs", help="Показати список перших 20 діалогів")

    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    log = logging.getLogger("friday.dev")
    cfg = load_config()
    args = build_parser().parse_args(argv)

    if args.group == "monitors":
        for m in list_monitors():
            log.info(
                "Монітор #%d (primary=%s) device=%s work_area=%s",
                m.index,
                m.is_primary,
                m.device,
                m.work_area,
            )
        return 0

    if args.group == "window":
        wm = WindowManager()
        hwnd = wm.find_window(args.target)
        if args.action == "move":
            res = wm.move_to_monitor(hwnd, monitor_idx=args.monitor, mode=args.mode)
        elif args.action == "snap":
            res = wm.snap(hwnd, mode=args.mode)
        elif args.action == "resize":
            res = wm.resize(hwnd, mode=args.mode)
        elif args.action == "focus":
            res = wm.focus(hwnd)
        else:
            raise ValueError(f"Невідома дія: {args.action}")
        log.info("Результат: %s", res)
        return 0

    if args.group == "app" and args.action == "launch":
        launcher = AppLauncher(cfg.apps)
        res = launcher.launch_sync(args.name)
        log.info("Результат: %s", res)
        return 0

    if args.group == "gesture":
        import time
        from friday.gesture.controller import GestureRuntimeController

        if args.mode:
            cfg.gesture.mode = args.mode
        wm = WindowManager()
        ctrl = GestureRuntimeController(config=cfg.gesture, window_manager=wm)
        if not ctrl.start():
            log.error("Не вдалося запустити режим жестів.")
            return 1
        log.info("Режим жестів (%s) активний на %.1f с...", cfg.gesture.mode, args.seconds)
        try:
            t_end = time.perf_counter() + args.seconds
            while ctrl.is_running and time.perf_counter() < t_end:
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            stop_ms = ctrl.stop()
            log.info(
                "Завершено: stop_ms=%.2f мс, camera_release=%.2f мс, hand_to_window=%.2f мс",
                stop_ms,
                ctrl.last_camera_release_ms,
                ctrl.mover.last_hand_to_window_ms,
            )
        return 0

    if args.group == "telegram":
        from friday.skills.telegram import DEFAULT_SESSION_PATH

        if not cfg.secrets.telegram_api_id or not cfg.secrets.telegram_api_hash:
            log.error("TELEGRAM_API_ID або TELEGRAM_API_HASH не налаштовані у .env")
            return 1
        try:
            from telethon import TelegramClient
        except ImportError:
            log.error("telethon не встановлено. Встановіть через: .\\.venv\\Scripts\\pip.exe install telethon")
            return 1

        import asyncio

        client = TelegramClient(str(DEFAULT_SESSION_PATH), int(cfg.secrets.telegram_api_id), cfg.secrets.telegram_api_hash)

        if args.action == "login":
            import getpass
            import re
            import telethon.errors

            async def _do_login() -> None:
                log.info("Запуск одноразової авторизації Telegram (Telethon)...")
                await client.connect()
                if await client.is_user_authorized():
                    me = await client.get_me()
                    log.info("✅ Вже авторизовано як: %s (ID: %s)", getattr(me, "first_name", ""), me.id)
                    await client.disconnect()
                    return

                phone = input("\n📱 Введіть ваш номер телефону у форматі +380...: ").strip()
                if not phone.startswith("+"):
                    phone = "+" + phone

                try:
                    sent = await client.send_code_request(phone)
                except telethon.errors.FloodWaitError as e:
                    log.error(
                        "⏳ Telegram тимчасово обмежив запити через невдалі спроби. Зачекайте %d с (~%d хв) і спробуйте знову.",
                        e.seconds,
                        max(1, e.seconds // 60),
                    )
                    await client.disconnect()
                    return
                except Exception as e:
                    log.error("❌ Помилка відправки коду: %s", e)
                    await client.disconnect()
                    return

                type_name = type(sent.type).__name__
                if "App" in type_name:
                    print("\n" + "=" * 60)
                    print("👉 [ВАЖЛИВО] Код надіслано в ДОДАТОК Telegram!")
                    print("   Відкрийте Telegram на телефоні або ПК та перевірте чат 'Telegram' (з синьою галочкою).")
                    print("=" * 60 + "\n")
                elif "Sms" in type_name:
                    print(f"\n👉 [ВАЖЛИВО] Код надіслано в SMS на номер {phone}!\n")
                else:
                    print(f"\n👉 Код надіслано через: {type_name}\n")

                raw_code = input("🔢 Введіть 5-значний цифровий код з повідомлення: ").strip()
                m = re.search(r"\b\d{5,6}\b", raw_code)
                code = m.group(0) if m else raw_code

                try:
                    await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
                except telethon.errors.SessionPasswordNeededError:
                    pwd = getpass.getpass("🔒 Введіть ваш 2FA пароль (хмарний пароль Telegram): ")
                    await client.sign_in(password=pwd)
                except telethon.errors.PhoneCodeInvalidError:
                    log.error("❌ Невірний код підтвердження.")
                    await client.disconnect()
                    return

                me = await client.get_me()
                log.info("✅ Авторизація успішна! Увійшли як: %s (ID: %s)", getattr(me, "first_name", ""), me.id)
                await client.disconnect()

            asyncio.run(_do_login())
            return 0

        if args.action == "dialogs":
            async def _do_dialogs() -> None:
                await client.connect()
                if not await client.is_user_authorized():
                    log.error("❌ Сесія не авторизована. Спочатку виконайте: python -m friday.dev telegram login")
                    return
                dialogs = await client.get_dialogs(limit=20)
                log.info("Знайдено діалогів (перші %d):", len(dialogs))
                for d in dialogs:
                    log.info("  • %s (ID: %s)", d.name, d.id)
                await client.disconnect()

            asyncio.run(_do_dialogs())
            return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
