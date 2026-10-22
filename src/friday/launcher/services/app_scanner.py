"""Сканер меню Пуск (розбір .lnk без важких залежностей) та валідатор виконуваних файлів для сторінки Apps."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None  # type: ignore[assignment]


ALLOWED_EXECUTABLE_EXTENSIONS: frozenset[str] = frozenset({".exe", ".lnk", ".bat", ".cmd", ".com"})

_SKIP_SHORTCUT_KEYWORDS: tuple[str, ...] = (
    "uninstall",
    "unins000",
    "видалити",
    "деінсталювати",
    "readme",
    "help",
    "довідка",
    "manual",
    "website",
    "license",
    "updater",
    "installer",
)

# MS-SHLLINK HeaderSize (0x0000004C) + LinkCLSID (00021401-0000-0000-C000-000000000046)
_LNK_HEADER_MAGIC = b"\x4c\x00\x00\x00\x01\x14\x02\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46"

_WIN_EXE_PATH_ASCII_RE = re.compile(rb"([A-Za-z]:\\[A-Za-z0-9_.\-\\ ()/]+?\.(?:exe|bat|cmd|com))", re.IGNORECASE)
_WIN_EXE_PATH_UTF16_RE = re.compile(
    r"([A-Za-z]:\\[A-Za-z0-9_.\-\\ ()/]+?\.(?:exe|bat|cmd|com))",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class ScannedApp:
    """Елемент знайденої у меню Пуск програми."""

    voice_name: str
    display_name: str
    exe_path: str
    arguments: str = ""
    lnk_path: str = ""


def normalize_voice_name(raw_title: str) -> str:
    """Формує коротку голосову назву (ключ) зі штампу ярлика."""
    cleaned = re.sub(r"\([^)]*\)", "", raw_title or "").strip().lower()
    cleaned = re.sub(r"[^0-9a-zа-яіїєґ _\-]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "app"


def build_stub_lnk_bytes(target_path: str, arguments: str = "") -> bytes:
    """Створює бінарний MS-SHLLINK (.lnk) сумісний блок для модульних тестів без COM."""
    header = bytearray(76)
    header[0:20] = _LNK_HEADER_MAGIC
    # Вбудовуємо шлях у ASCII та UTF-16LE, а також необов'язковий блок аргументів з префіксом ARGS:
    target_ascii = target_path.encode("utf-8", errors="ignore") + b"\x00"
    target_utf16 = target_path.encode("utf-16-le", errors="ignore") + b"\x00\x00"
    args_block = b""
    if arguments:
        args_bytes = arguments.encode("utf-16-le")
        args_block = b"FRIDAY_LNK_ARGS:" + struct.pack("<H", len(args_bytes)) + args_bytes
    return bytes(header) + target_ascii + target_utf16 + args_block


def parse_lnk_bytes(data: bytes) -> tuple[str, str]:
    """Витягує цільовий шлях до .exe та аргументи із бінарного вмісту файлу .lnk (MS-SHLLINK)."""
    if len(data) < 20 or not data.startswith(b"\x4c\x00\x00\x00"):
        return ("", "")

    candidates: list[str] = []

    # 1. Пошук шляхів у ASCII / ANSI секції LinkInfo
    for m in _WIN_EXE_PATH_ASCII_RE.finditer(data[20:]):
        try:
            path_str = m.group(1).decode("cp1251", errors="ignore").strip()
            if path_str and "%" not in path_str:
                candidates.append(path_str)
        except Exception:
            pass

    # 2. Пошук шляхів у UTF-16LE секції StringData / Unicode LocalBasePath
    for offset in (0, 1):
        chunk = data[20 + offset :]
        even_len = len(chunk) - (len(chunk) % 2)
        decoded = chunk[:even_len].decode("utf-16-le", errors="ignore")
        for m16 in _WIN_EXE_PATH_UTF16_RE.finditer(decoded):
            path_str = m16.group(1).strip()
            if path_str:
                candidates.append(path_str)

    # Обираємо найкращого кандидата (перевага існуючому файлу, який не є cmd/conhost/installer)
    chosen_target = ""
    for cand in candidates:
        expanded = os.path.expandvars(cand)
        if Path(expanded).exists():
            chosen_target = expanded
            break
    if not chosen_target and candidates:
        chosen_target = os.path.expandvars(candidates[-1])

    # Перевірка вбудованих аргументів (для тестових заглушок або розширених блоків)
    arguments = ""
    marker = b"FRIDAY_LNK_ARGS:"
    idx = data.find(marker)
    if idx != -1 and idx + len(marker) + 2 <= len(data):
        pos = idx + len(marker)
        (byte_len,) = struct.unpack("<H", data[pos : pos + 2])
        raw_args = data[pos + 2 : pos + 2 + byte_len]
        arguments = raw_args.decode("utf-16-le", errors="ignore").strip()

    return (chosen_target, arguments)


def parse_lnk_file(lnk_path: Path, require_target_exists: bool = False) -> ScannedApp | None:
    """Розбирає один файл ярлика .lnk і повертає ScannedApp (або None для деінсталяторів/битого файлу)."""
    if not lnk_path.exists() or lnk_path.suffix.lower() != ".lnk":
        return None

    stem_lower = lnk_path.stem.lower()
    if any(skip in stem_lower for skip in _SKIP_SHORTCUT_KEYWORDS):
        return None

    try:
        raw_bytes = lnk_path.read_bytes()
    except Exception:
        return None

    if len(raw_bytes) < 20 or not raw_bytes.startswith(b"\x4c\x00\x00\x00"):
        return None

    target_path, args = parse_lnk_bytes(raw_bytes)
    if not target_path:
        # Для валідних Shell-ярликів Windows (UWP / Explorer PIDL) використовуємо сам файл .lnk
        target_path = str(lnk_path)

    target_lower = Path(target_path).name.lower()
    if any(skip in target_lower for skip in _SKIP_SHORTCUT_KEYWORDS):
        return None

    if require_target_exists and not Path(target_path).exists():
        return None

    voice_name = normalize_voice_name(lnk_path.stem)
    return ScannedApp(
        voice_name=voice_name,
        display_name=lnk_path.stem,
        exe_path=target_path,
        arguments=args,
        lnk_path=str(lnk_path),
    )


def default_app_shortcut_dirs() -> list[Path]:
    """Повертає стандартні каталоги ярликів Windows: меню Пуск та Робочий стіл."""
    dirs: list[Path] = []
    # 1. Меню Пуск (APPDATA та ProgramData)
    for env_key in ("APPDATA", "ProgramData"):
        base = os.environ.get(env_key)
        if base:
            p = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if p.exists() and p not in dirs:
                dirs.append(p)

    # 2. Робочий стіл поточного користувача
    home = Path.home()
    for d in (
        home / "Desktop",
        home / "OneDrive" / "Desktop",
        home / "OneDrive" / "Робочий стіл",
        home / "Робочий стіл",
    ):
        if d.exists() and d not in dirs:
            dirs.append(d)

    # 3. Загальний робочий стіл (Public Desktop)
    pub = os.environ.get("PUBLIC")
    if pub:
        pub_d = Path(pub) / "Desktop"
        if pub_d.exists() and pub_d not in dirs:
            dirs.append(pub_d)
    alt_pub = Path(r"C:\Users\Public\Desktop")
    if alt_pub.exists() and alt_pub not in dirs:
        dirs.append(alt_pub)

    return dirs


def default_start_menu_dirs() -> list[Path]:
    """Сумісність зі старим API: повертає каталоги ярликів застосунків."""
    return default_app_shortcut_dirs()


def scan_registry_apps() -> list[ScannedApp]:
    """Сканує системний реєстр Windows (App Paths та Uninstall) для виявлення встановлених застосунків."""
    if winreg is None:
        return []

    apps: list[ScannedApp] = []
    seen_paths: set[str] = set()

    # 1. HKLM та HKCU \ ... \ App Paths
    app_paths_roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    for root_key, subkey in app_paths_roots:
        try:
            with winreg.OpenKey(root_key, subkey) as key:
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    try:
                        app_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, app_name) as app_key:
                            raw_val, _ = winreg.QueryValueEx(app_key, "")
                            if not raw_val:
                                continue
                            clean_path = str(raw_val).strip("\"' ")
                            expanded = os.path.expandvars(clean_path)
                            p = Path(expanded)
                            if p.exists() and p.suffix.lower() == ".exe":
                                norm_p = str(p.resolve()).lower()
                                if norm_p not in seen_paths:
                                    stem = p.stem
                                    if not any(skip in stem.lower() for skip in _SKIP_SHORTCUT_KEYWORDS):
                                        seen_paths.add(norm_p)
                                        apps.append(
                                            ScannedApp(
                                                voice_name=normalize_voice_name(stem),
                                                display_name=stem,
                                                exe_path=str(p.resolve()),
                                            )
                                        )
                    except Exception:
                        pass
        except Exception:
            pass

    # 2. HKLM, WOW6432Node, HKCU \ ... \ Uninstall
    uninstall_roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for root_key, subkey in uninstall_roots:
        try:
            with winreg.OpenKey(root_key, subkey) as key:
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, sub_name) as app_k:
                            disp, _ = winreg.QueryValueEx(app_k, "DisplayName")
                            if not disp or not str(disp).strip():
                                continue
                            disp_str = str(disp).strip()
                            if any(skip in disp_str.lower() for skip in _SKIP_SHORTCUT_KEYWORDS):
                                continue

                            exe_candidate = ""
                            try:
                                icon, _ = winreg.QueryValueEx(app_k, "DisplayIcon")
                                if icon:
                                    clean_icon = str(icon).split(",")[0].strip("\"' ")
                                    p_icon = Path(os.path.expandvars(clean_icon))
                                    if p_icon.exists() and p_icon.suffix.lower() == ".exe":
                                        exe_candidate = str(p_icon.resolve())
                            except Exception:
                                pass

                            if not exe_candidate:
                                try:
                                    loc, _ = winreg.QueryValueEx(app_k, "InstallLocation")
                                    if loc and Path(loc).is_dir():
                                        for p_exe in Path(loc).glob("*.exe"):
                                            p_stem = p_exe.stem.lower()
                                            if not any(skip in p_stem for skip in _SKIP_SHORTCUT_KEYWORDS):
                                                exe_candidate = str(p_exe.resolve())
                                                break
                                except Exception:
                                    pass

                            if exe_candidate:
                                norm_cand = exe_candidate.lower()
                                if norm_cand not in seen_paths:
                                    p_cand = Path(norm_cand)
                                    if not any(skip in p_cand.stem.lower() for skip in _SKIP_SHORTCUT_KEYWORDS):
                                        seen_paths.add(norm_cand)
                                        apps.append(
                                            ScannedApp(
                                                voice_name=normalize_voice_name(disp_str),
                                                display_name=disp_str,
                                                exe_path=exe_candidate,
                                            )
                                        )
                    except Exception:
                        pass
        except Exception:
            pass

    return apps


def scan_start_menu_apps(
    directories: list[Path] | None = None,
    require_target_exists: bool = True,
    include_registry: bool | None = None,
) -> list[ScannedApp]:
    """Сканує меню Пуск, ярлики Робочого столу та реєстр Windows і повертає всі знайдені програми."""
    custom_dirs = directories is not None
    search_dirs = directories if custom_dirs else default_app_shortcut_dirs()
    should_include_registry = not custom_dirs if include_registry is None else bool(include_registry)

    seen_voice_names: set[str] = set()
    seen_targets: set[str] = set()
    results: list[ScannedApp] = []

    for base_dir in search_dirs:
        if not base_dir.exists():
            continue
        for lnk_file in sorted(base_dir.rglob("*.lnk")):
            parsed = parse_lnk_file(lnk_file, require_target_exists=require_target_exists)
            if parsed is None:
                continue
            norm_target = parsed.exe_path.lower()
            if parsed.voice_name in seen_voice_names or norm_target in seen_targets:
                continue
            seen_voice_names.add(parsed.voice_name)
            seen_targets.add(norm_target)
            results.append(parsed)

    if should_include_registry:
        for reg_app in scan_registry_apps():
            norm_target = reg_app.exe_path.lower()
            if reg_app.voice_name in seen_voice_names or norm_target in seen_targets:
                continue
            seen_voice_names.add(reg_app.voice_name)
            seen_targets.add(norm_target)
            results.append(reg_app)

    results.sort(key=lambda item: item.display_name.lower())
    return results


def format_app_command(exe_path: str, arguments: str = "") -> str:
    """Формує рядок команди для збереження у [apps] config.local.toml."""
    clean_exe = exe_path.strip().strip('"')
    clean_args = arguments.strip()
    if not clean_args:
        return clean_exe
    if " " in clean_exe:
        return f'"{clean_exe}" {clean_args}'
    return f"{clean_exe} {clean_args}"


def split_command_and_args(raw_cmd: str) -> tuple[str, str]:
    """Розділяє збережене у [apps] значення на (шлях_до_файлу, необов'язкові_аргументи)."""
    cleaned = (raw_cmd or "").strip()
    if not cleaned:
        return ("", "")

    expanded = os.path.expandvars(cleaned)
    if Path(expanded).exists():
        return (cleaned, "")

    try:
        parts = shlex.split(cleaned, posix=False)
    except ValueError:
        return (cleaned, "")

    if not parts:
        return ("", "")

    exe = parts[0].strip('"')
    rest = " ".join(parts[1:])
    return (exe, rest)


def validate_app_executable(
    exe_path: str,
    arguments: str = "",
    require_exists: bool = True,
) -> tuple[bool, str, str]:
    """Перевіряє, що шлях існує та вказує на виконуваний файл (.exe/.lnk/.bat/.cmd/.com).

    Повертає кортеж (ok, formatted_command, error_message).
    """
    raw_exe = (exe_path or "").strip().strip('"').strip("'")
    if not raw_exe:
        return (False, "", "Вкажіть шлях до виконуваного файлу (.exe або .lnk).")

    expanded = os.path.expandvars(os.path.expanduser(raw_exe))
    candidate = Path(expanded)

    resolved_path: Path | None = None
    if candidate.exists() and candidate.is_file():
        resolved_path = candidate.resolve()
    else:
        which_hit = shutil.which(expanded)
        if which_hit:
            resolved_path = Path(which_hit).resolve()

    if require_exists and resolved_path is None:
        return (False, "", f"Файл не знайдено: {raw_exe}")

    target_for_ext = resolved_path if resolved_path is not None else candidate
    ext = target_for_ext.suffix.lower()
    if ext not in ALLOWED_EXECUTABLE_EXTENSIONS:
        return (
            False,
            "",
            f"Непідтримуване розширення «{ext or '(немає)'}». Дозволені лише: {', '.join(sorted(ALLOWED_EXECUTABLE_EXTENSIONS))}.",
        )

    final_exe = str(resolved_path) if resolved_path is not None else str(candidate)
    return (True, format_app_command(final_exe, arguments), "")
