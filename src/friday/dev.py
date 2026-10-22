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

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
