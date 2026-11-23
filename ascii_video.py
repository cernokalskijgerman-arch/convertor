"""
ASCII Video Player - Сверхвысокое разрешение и максимальный FPS в терминале.
Воспроизводит видео (mp4, mkv, avi, webm, mov, gif) из букв, цифр и символов в 24-bit TrueColor.
"""

import os
import sys
import time
import shutil
import ctypes
import msvcrt
from pathlib import Path
import cv2
import numpy as np

# Включение VT100 ANSI режима в Windows
def enable_windows_vt():
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            STD_OUTPUT_HANDLE = -11
            ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            h_out = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(h_out, ctypes.byref(mode)):
                mode.value |= ENABLE_VIRTUAL_TERMINAL_PROCESSING
                kernel32.SetConsoleMode(h_out, mode)
        except Exception:
            pass

enable_windows_vt()

# Безопасный UTF-8 вывод
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 70 символов: буквы, цифры и символы от темных к ярким
RAMP_STR = " `.-':_,^=;><+!rc*z?sLTv)J7(|Fi{C}fI31tlu[neoZ5Yxjya]2ESwqkP6h9d4VpOGbUAKXHm8&%QW#NMD0$@"
RAMP_CHARS = np.array(list(RAMP_STR))
RAMP_LEN = len(RAMP_CHARS)

FONT_ASPECT = 0.48


def get_terminal_dimensions(default_w=120, default_h=40):
    """Возвращает ширину и высоту окна терминала в символах."""
    try:
        size = shutil.get_terminal_size(fallback=(default_w, default_h))
        return max(40, size.columns - 1), max(20, size.lines - 3)
    except Exception:
        return default_w, default_h


def render_frame_truecolor_chars(resized_rgb: np.ndarray, gray: np.ndarray, width: int, height: int, bg_color: bool = False) -> str:
    """
    Высокоскоростная генерация кадра из букв, цифр и символов в TrueColor 24-bit ANSI.
    """
    # Вычисление индекса символа по яркости
    indices = (gray.astype(np.float32) / 255.0 * (RAMP_LEN - 1)).astype(np.int32)
    chars = RAMP_CHARS[indices]
    
    rows = []
    for y in range(height):
        row = []
        last_c = None
        for x in range(width):
            r, g, b = resized_rgb[y, x]
            ch = chars[y, x]
            if bg_color:
                bg_r, bg_g, bg_b = r >> 2, g >> 2, b >> 2
                row.append(f"\x1b[48;2;{bg_r};{bg_g};{bg_b}m\x1b[38;2;{r};{g};{b}m{ch}")
            else:
                # Квантование цвета для сжатия одинаковых ANSI-блоков
                qr, qg, qb = int(r) & 0xFC, int(g) & 0xFC, int(b) & 0xFC
                c = (qr, qg, qb)
                if c != last_c:
                    row.append(f"\x1b[38;2;{r};{g};{b}m{ch}")
                    last_c = c
                else:
                    row.append(ch)
        row.append("\x1b[0m")
        rows.append("".join(row))
    return "\n".join(rows)


def render_frame_halfblocks(resized_rgb: np.ndarray, width: int, height_pixels: int) -> str:
    """
    Ультра-разрешение 2x по вертикали с помощью полублоков ▀.
    Каждая ячейка терминала отображает два вертикальных пикселя в TrueColor.
    """
    top = resized_rgb[0::2]
    bot = resized_rgb[1::2]
    num_rows = min(top.shape[0], bot.shape[0])
    
    rows = []
    for y in range(num_rows):
        row = []
        for x in range(width):
            rt, gt, bt = top[y, x]
            rb, gb, bb = bot[y, x]
            row.append(f"\x1b[38;2;{rt};{gt};{bt};48;2;{rb};{gb};{bb}m▀")
        row.append("\x1b[0m")
        rows.append("".join(row))
    return "\n".join(rows)


def render_frame_monochrome(gray: np.ndarray, width: int, height: int) -> str:
    """
    Монохромный ASCII рендеринг (максимальная скорость передачи в терминал).
    """
    indices = (gray.astype(np.float32) / 255.0 * (RAMP_LEN - 1)).astype(np.int32)
    chars = RAMP_CHARS[indices]
    rows = []
    for y in range(height):
        rows.append("".join(chars[y]))
    return "\n".join(rows)


def play_video(video_path: Path, width: int = None, mode: int = 1, loop: bool = True):
    """
    Воспроизводит видео с максимальным FPS и точностью синхронизации времени.
    Управление:
      Space - Пауза/Возобновление
      1-4   - Переключение режима
      +/-   - Изменение ширины/разрешения
      R     - Перемотка в начало
      F     - Показать/скрыть FPS
      Q/Esc - Выход в меню
    """
    if not video_path.exists():
        print(f"\x1b[31m[!] Видеофайл не найден: {video_path}\x1b[0m")
        return

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"\x1b[31m[!] Не удалось открыть видео: {video_path}\x1b[0m")
        return

    orig_fps = cap.get(cv2.CAP_PROP_FPS)
    if orig_fps <= 0 or orig_fps > 120 or np.isnan(orig_fps):
        orig_fps = 30.0

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    aspect = orig_h / max(1, orig_w)

    term_w, term_h = get_terminal_dimensions()
    if width is None or width <= 0:
        # Автоматическая ширина по размеру терминала
        # Учитываем как ширину, так и высоту окна
        w_by_width = term_w
        if mode == 4:
            # Half-block: каждая строка содержит 2 пикселя
            w_by_height = int((term_h - 2) * 2 / aspect)
        else:
            w_by_height = int((term_h - 2) / (aspect * FONT_ASPECT))
        width = max(30, min(w_by_width, w_by_height))

    # Скрытие курсора и очистка экрана
    sys.stdout.write("\x1b[?25l\x1b[2J\x1b[H")
    sys.stdout.flush()

    show_fps = True
    paused = False
    frame_idx = 0
    t_start = time.perf_counter()
    fps_counter = 0
    fps_display = orig_fps
    t_fps_measure = time.perf_counter()

    mode_names = {
        1: "TrueColor Буквы/Цифры/Символы",
        2: "Символы + Цвет фона",
        3: "Монохромный ASCII",
        4: "Ultra Half-Block (2x резкость)"
    }

    try:
        while True:
            # Обработка клавиатуры в реальном времени (неблокирующий ввод)
            while msvcrt.kbhit():
                key = msvcrt.getch()
                if key in (b'q', b'Q', b'\x1b'): # Q или Esc
                    return
                elif key == b' ': # Пробел (пауза)
                    paused = not paused
                    if not paused:
                        t_start = time.perf_counter() - (frame_idx / orig_fps)
                elif key in (b'1', b'2', b'3', b'4'):
                    mode = int(key.decode('ascii'))
                elif key in (b'f', b'F', b'\xd4', b'\xf4'): # F (переключение оверлея FPS)
                    show_fps = not show_fps
                elif key in (b'r', b'R', b'\xca', b'\xea'): # R (перемотка в начало)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    frame_idx = 0
                    t_start = time.perf_counter()
                elif key in (b'+', b'='):
                    width = min(term_w, width + 10)
                elif key in (b'-', b'_'):
                    width = max(30, width - 10)

            if paused:
                time.sleep(0.05)
                continue

            # Расчет синхронизации кадров
            now = time.perf_counter()
            target_time = frame_idx / orig_fps
            elapsed = now - t_start

            # Пропуск отстающих кадров, если терминал не успевает
            if elapsed > target_time + (2.0 / orig_fps):
                # Пропускаем чтение кадров
                cap.grab()
                frame_idx += 1
                continue

            # Задержка, если рендеринг идет с опережением
            wait_time = target_time - elapsed
            if wait_time > 0.001:
                time.sleep(wait_time)

            ret, frame = cap.read()
            if not ret:
                if loop:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    frame_idx = 0
                    t_start = time.perf_counter()
                    continue
                else:
                    break

            frame_idx += 1
            fps_counter += 1
            if time.perf_counter() - t_fps_measure >= 1.0:
                fps_display = fps_counter / (time.perf_counter() - t_fps_measure)
                fps_counter = 0
                t_fps_measure = time.perf_counter()

            # Преобразование кадра
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            if mode == 4:
                # Half-block: 2 вертикальных пикселя на строку
                h_pix = max(2, int(width * aspect))
                if h_pix % 2 != 0:
                    h_pix += 1
                resized = cv2.resize(frame_rgb, (width, h_pix), interpolation=cv2.INTER_AREA)
                rendered_str = render_frame_halfblocks(resized, width, h_pix)
            else:
                h_chars = max(1, int(width * aspect * FONT_ASPECT))
                resized = cv2.resize(frame_rgb, (width, h_chars), interpolation=cv2.INTER_AREA)
                gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)
                if mode == 2:
                    rendered_str = render_frame_truecolor_chars(resized, gray, width, h_chars, bg_color=True)
                elif mode == 3:
                    rendered_str = render_frame_monochrome(gray, width, h_chars)
                else:
                    rendered_str = render_frame_truecolor_chars(resized, gray, width, h_chars, bg_color=False)

            # Формирование информационного оверлея
            if show_fps:
                info_line = f"\x1b[1;30;47m {video_path.name} | FPS: {fps_display:.1f}/{orig_fps:.1f} | Ширина: {width} | Режим [{mode}]: {mode_names.get(mode, '')} | [Пробел]=Пауза [1-4]=Режим [Q]=Выход \x1b[0m"
            else:
                info_line = ""

            # Вывод кадра: перемещение курсора в начало (0,0) без cls для 100% плавности
            out_buffer = f"\x1b[H{info_line}\n{rendered_str}"
            sys.stdout.write(out_buffer)
            sys.stdout.flush()

    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        # Восстановление курсора и сброс цветов
        sys.stdout.write("\x1b[?25h\x1b[0m\n")
        sys.stdout.flush()


def main():
    enable_windows_vt()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1].strip('\"\'& '))
        w = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
        m = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 1
        play_video(path, width=w, mode=m)
    else:
        print("Использование: py ascii_video.py <видеофайл> [ширина] [режим 1-4]")


if __name__ == "__main__":
    main()
