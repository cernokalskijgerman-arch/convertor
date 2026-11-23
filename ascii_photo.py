"""
ASCII Photo & Video Studio - Сверхвысокое разрешение фото и видео в терминале.
Поддерживает TrueColor 24-bit, буфер обмена (clipboard), Drag-and-Drop фото и видео, выбор файла и экспорт в HTML.
"""

import os
import sys
import shutil
import ctypes
import traceback
import urllib.parse
from pathlib import Path
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageGrab

# Импорт видеоплеера
try:
    from ascii_video import play_video
    VIDEO_SUPPORT = True
except ImportError:
    VIDEO_SUPPORT = False

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.gif', '.wmv', '.flv', '.m4v', '.3gp'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.tif'}

# Включение поддержки ANSI-цветов в консоли Windows (VT100 mode)
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

# Настройка безопасного вывода UTF-8
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Палитра символов: 70 символов из букв, цифр и спецсимволов от темных к ярким
RAMP_LETTERS_DIGITS_SYMBOLS = (
    " `.-':_,^=;><+!rc*z?sLTv)J7(|Fi{C}fI31tlu[neoZ5Yxjya]2ESwqkP6h9d4VpOGbUAKXHm8&%QW#NMD0$@"
)

# Соотношение сторон символов в терминале (обычно высота в ~2 раза больше ширины)
FONT_ASPECT_RATIO = 0.48


def get_terminal_width(default=120):
    """Определяет текущую ширину окна терминала."""
    try:
        size = shutil.get_terminal_size(fallback=(default, 40))
        return max(size.columns - 2, 40)
    except Exception:
        return default


def clean_file_path(raw_str: str) -> Path:
    """Очищает путь к файлу от любых кавычек, пробелов, префиксов PowerShell и file:///."""
    if not raw_str:
        return Path("")
    s = raw_str.strip()
    
    # Удаление невидимых спецсимволов Windows (LTR/RTL marks, BOM)
    s = s.replace('\u200e', '').replace('\u200f', '').replace('\ufeff', '')
    
    # Удаление префикса запуска PowerShell: & '...' или & "..."
    if s.startswith('&'):
        s = s[1:].strip()
        
    # Удаление внешних кавычек
    s = s.strip("\"'").strip()
    
    # Обработка file:/// и file://
    if s.lower().startswith("file:///"):
        s = s[8:]
        s = urllib.parse.unquote(s)
    elif s.lower().startswith("file://"):
        s = s[7:]
        s = urllib.parse.unquote(s)
        
    s = s.strip("\"'").strip()
    
    # На Windows убираем лишний начальный слэш если получилось /C:/...
    if len(s) >= 3 and s[0] == '/' and s[2] == ':':
        s = s[1:]
        
    return Path(s)


def preprocess_image(image: Image.Image, sharpness=1.6, contrast=1.25, color_boost=1.2, brightness=1.0) -> Image.Image:
    """
    Максимальное качество: авто-уровни (autocontrast), насыщенность цвета,
    микроконтраст и unsharp mask для предельной детализации мелких текстур.
    """
    img = image.convert("RGB")
    
    # 1. Автоматическое расширение динамического диапазона
    try:
        img = ImageOps.autocontrast(img, cutoff=0.5)
    except Exception:
        pass
        
    # 2. Насыщенность цвета (TrueColor 24-bit выглядит сочно и кинематографично)
    if color_boost != 1.0:
        img = ImageEnhance.Color(img).enhance(color_boost)
        
    # 3. Регулировка яркости
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
        
    # 4. Контраст (границы объектов и текстуры не сливаются)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
        
    # 5. Повышение резкости и unsharp mask
    if sharpness != 1.0:
        img = ImageEnhance.Sharpness(img).enhance(sharpness)
        img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=150, threshold=1))
        
    return img


def render_ascii_truecolor(image: Image.Image, width: int, ramp=RAMP_LETTERS_DIGITS_SYMBOLS,
                           contrast=1.25, sharpness=1.6, invert=False, bg_color=False):
    """
    Рендерит фото из цветных букв, цифр и символов в 24-bit TrueColor с гамма-коррекцией.
    """
    orig_w, orig_h = image.size
    aspect = orig_h / orig_w
    height = max(1, int(width * aspect * FONT_ASPECT_RATIO))
    
    # Высококачественный ресайз Lanczos с предварительной обработкой
    img_sharpened = preprocess_image(image, sharpness=sharpness, contrast=contrast, color_boost=1.2)
    img_resized = img_sharpened.resize((width, height), Image.Resampling.LANCZOS)
    
    ramp_len = len(ramp)
    lines = []
    html_lines = []
    
    pixels = img_resized.load()
    
    for y in range(height):
        row = []
        html_row = []
        last_color = None
        
        for x in range(width):
            r, g, b = pixels[x, y]
            
            # Воспринимаемая яркость с нелинейной гамма-коррекцией (вытягивает тени и текстуры)
            lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
            luminance = lum ** 0.85
            if invert:
                luminance = 1.0 - luminance
                
            idx = int(luminance * (ramp_len - 1))
            idx = max(0, min(ramp_len - 1, idx))
            ch = ramp[idx]
            
            if bg_color:
                bg_r, bg_g, bg_b = int(r * 0.35), int(g * 0.35), int(b * 0.35)
                color_code = f"\x1b[48;2;{bg_r};{bg_g};{bg_b}m\x1b[38;2;{r};{g};{b}m"
                row.append(f"{color_code}{ch}")
                html_row.append(f'<span style="color:rgb({r},{g},{b});background-color:rgb({bg_r},{bg_g},{bg_b})">{html_escape(ch)}</span>')
            else:
                color_code = f"\x1b[38;2;{r};{g};{b}m"
                if color_code != last_color:
                    row.append(f"{color_code}{ch}")
                    last_color = color_code
                else:
                    row.append(ch)
                html_row.append(f'<span style="color:rgb({r},{g},{b})">{html_escape(ch)}</span>')
                
        row.append("\x1b[0m")
        lines.append("".join(row))
        html_lines.append("".join(html_row))
        
    return "\n".join(lines), "\n".join(html_lines)


def render_ascii_halfblocks(image: Image.Image, width: int, contrast=1.1, sharpness=1.3):
    """
    Рендерит фото с удвоенным вертикальным разрешением с помощью полублоков '▀'.
    """
    orig_w, orig_h = image.size
    aspect = orig_h / orig_w
    height_pixels = max(2, int(width * aspect))
    if height_pixels % 2 != 0:
        height_pixels += 1
    
    img_prep = preprocess_image(image, sharpness=sharpness, contrast=contrast)
    img_resized = img_prep.resize((width, height_pixels), Image.Resampling.LANCZOS)
    pixels = img_resized.load()
    
    lines = []
    html_lines = []
    
    for y in range(0, height_pixels, 2):
        row = []
        html_row = []
        for x in range(width):
            r_top, g_top, b_top = pixels[x, y]
            r_bot, b_bot, g_bot = (pixels[x, y + 1] if (y + 1 < height_pixels) else (0, 0, 0))
            code = f"\x1b[38;2;{r_top};{g_top};{b_top}m\x1b[48;2;{r_bot};{b_bot};{g_bot}m▀"
            row.append(code)
            html_row.append(f'<span style="color:rgb({r_top},{g_top},{b_top});background-color:rgb({r_bot},{b_bot},{g_bot})">▀</span>')
            
        row.append("\x1b[0m")
        lines.append("".join(row))
        html_lines.append("".join(html_row))
        
    return "\n".join(lines), "\n".join(html_lines)


def render_ascii_monochrome(image: Image.Image, width: int, ramp=RAMP_LETTERS_DIGITS_SYMBOLS,
                            contrast=1.3, sharpness=1.5, invert=False):
    """
    Классический черно-белый рендер из букв, цифр и символов.
    """
    orig_w, orig_h = image.size
    aspect = orig_h / orig_w
    height = max(1, int(width * aspect * FONT_ASPECT_RATIO))
    
    img_prep = preprocess_image(image, sharpness=sharpness, contrast=contrast)
    gray = img_prep.convert("L")
    resized = gray.resize((width, height), Image.Resampling.LANCZOS)
    pixels = resized.load()
    
    ramp_len = len(ramp)
    lines = []
    html_lines = []
    
    for y in range(height):
        row = []
        for x in range(width):
            val = pixels[x, y]
            if invert:
                val = 255 - val
            idx = int((val / 255.0) * (ramp_len - 1))
            ch = ramp[idx]
            row.append(ch)
        row_str = "".join(row)
        lines.append(row_str)
        html_lines.append(html_escape(row_str))
        
    return "\n".join(lines), "\n".join(html_lines)


def html_escape(text: str) -> str:
    """Экранирует символы HTML."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#39;")
                .replace(" ", "&nbsp;"))


def save_html_viewer(html_content: str, output_path: Path, title="ASCII Photo"):
    """Создает HTML-файл для просмотра фото в браузере с зумом."""
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            background-color: #0b0d13;
            color: #ffffff;
            margin: 0;
            padding: 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
            font-family: system-ui, -apple-system, sans-serif;
            min-height: 100vh;
        }}
        .header {{
            margin-bottom: 8px;
            color: #93bbf8;
            font-size: 16px;
            font-weight: 500;
        }}
        .sub {{
            margin-bottom: 16px;
            color: #9aa0a6;
            font-size: 13px;
        }}
        .controls {{
            position: sticky;
            top: 10px;
            z-index: 100;
            margin-bottom: 20px;
            display: flex;
            gap: 10px;
            background: rgba(18, 22, 33, 0.9);
            backdrop-filter: blur(8px);
            padding: 8px 16px;
            border-radius: 30px;
            border: 1px solid #2d3748;
            box-shadow: 0 4px 20px rgba(0,0,0,0.5);
        }}
        button {{
            background: #252e42;
            color: #e2e8f0;
            border: 1px solid #4a5568;
            padding: 6px 14px;
            border-radius: 20px;
            cursor: pointer;
            font-size: 13px;
            transition: all 0.2s;
        }}
        button:hover {{
            background: #3b82f6;
            border-color: #60a5fa;
            color: #ffffff;
        }}
        .container {{
            max-width: 100%;
            overflow: auto;
            display: flex;
            justify-content: center;
        }}
        pre#canvas {{
            font-family: "Consolas", "Courier New", "Cascadia Code", monospace;
            font-size: 5px;
            line-height: 5px;
            letter-spacing: 0px;
            white-space: pre;
            background: #000000;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 15px 50px rgba(0,0,0,0.9);
            user-select: text;
        }}
    </style>
</head>
<body>
    <div class="header">🎨 ASCII Photo Studio Viewer — Ultra-HD Resolution</div>
    <div class="sub">Сверхвысокое разрешение из букв, цифр и символов | Масштабируйте: Ctrl + Колесико мыши</div>
    <div class="controls">
        <button onclick="changeSize(1)">➕ Увеличить шрифт (+)</button>
        <button onclick="changeSize(-1)">➖ Уменьшить шрифт (-)</button>
        <button onclick="resetSize()">↺ Исходный размер (5px)</button>
        <button onclick="toggleInvert()">🌓 Инвертировать фон</button>
    </div>
    <div class="container">
        <pre id="canvas">{html_content}</pre>
    </div>
    <script>
        let currentSize = 5;
        const canvas = document.getElementById('canvas');
        function changeSize(delta) {{
            currentSize = Math.max(1, currentSize + delta);
            canvas.style.fontSize = currentSize + 'px';
            canvas.style.lineHeight = currentSize + 'px';
        }}
        function resetSize() {{
            currentSize = 5;
            canvas.style.fontSize = '5px';
            canvas.style.lineHeight = '5px';
        }}
        let inverted = false;
        function toggleInvert() {{
            inverted = !inverted;
            canvas.style.background = inverted ? '#ffffff' : '#000000';
        }}
        window.addEventListener('wheel', (e) => {{
            if (e.ctrlKey) {{
                e.preventDefault();
                changeSize(e.deltaY < 0 ? 1 : -1);
            }}
        }}, {{ passive: false }});
    </script>
</body>
</html>"""
    try:
        output_path.write_text(html, encoding="utf-8")
    except Exception as e:
        print(f"\x1b[33m[!] Предупреждение: Не удалось сохранить HTML ({e})\x1b[0m")


def pick_file_dialog() -> str:
    """Открывает стандартное диалоговое окно Windows для выбора изображения или видео."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        filetypes = [
            ("Медиафайлы (Фото и Видео)", "*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif;*.tiff;*.mp4;*.mkv;*.avi;*.mov;*.webm"),
            ("Видеофайлы", "*.mp4;*.mkv;*.avi;*.mov;*.webm;*.gif;*.wmv;*.flv"),
            ("Изображения", "*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tiff"),
            ("Все файлы", "*.*")
        ]
        path = filedialog.askopenfilename(title="Выберите фото или видео для ASCII-режима", filetypes=filetypes)
        root.destroy()
        return path
    except Exception as e:
        print(f"\x1b[33m[!] Ошибка вызова диалога: {e}\x1b[0m")
        return ""


def get_image_from_clipboard():
    """Извлекает изображение из буфера обмена."""
    try:
        data = ImageGrab.grabclipboard()
        if isinstance(data, Image.Image):
            return data
        elif isinstance(data, list) and len(data) > 0:
            path = clean_file_path(str(data[0]))
            if path.is_file():
                return Image.open(path)
    except Exception:
        pass
    return None


def print_banner():
    banner = """
\x1b[1;36m========================================================================\x1b[0m
\x1b[1;37m     ASCII PHOTO & VIDEO STUDIO - ВЫСОКОЕ РАЗРЕШЕНИЕ В ТЕРМИНАЛЕ\x1b[0m
\x1b[1;32m   Рендеринг любых фото и видео из букв, цифр и символов в 24-bit TrueColor\x1b[0m
\x1b[1;36m========================================================================\x1b[0m
"""
    print(banner)


def handle_file(path: Path, current_width: int, current_mode: int):
    """Определяет тип файла (фото или видео) и запускает соответствующий обработчик."""
    ext = path.suffix.lower()
    
    # Видеофайл
    if ext in VIDEO_EXTENSIONS:
        if not VIDEO_SUPPORT:
            print("\x1b[31m[!] Модуль ascii_video не найден или не установлен opencv-python.\x1b[0m")
            return None, "video"
        print(f"\x1b[32m✔ Запуск видео: {path.name}\x1b[0m")
        play_video(path, width=current_width, mode=current_mode)
        return None, path.stem

    # Фото / изображение
    try:
        image = Image.open(path)
        img_name = path.stem
        print(f"\x1b[32m✔ Успешно загружено фото: {path.name} ({image.width}x{image.height} px)\x1b[0m")
        draw_and_display(image, img_name, current_width, current_mode)
        return image, img_name
    except Exception as e:
        # Проверяем, может это видео без стандартного расширения
        if VIDEO_SUPPORT:
            try:
                import cv2
                cap = cv2.VideoCapture(str(path))
                if cap.isOpened() and cap.get(cv2.CAP_PROP_FRAME_COUNT) > 1:
                    cap.release()
                    print(f"\x1b[32m✔ Распознано как видео: {path.name}\x1b[0m")
                    play_video(path, width=current_width, mode=current_mode)
                    return None, path.stem
                cap.release()
            except Exception:
                pass
        print(f"\x1b[31m[!] Ошибка открытия файла '{path.name}': {e}\x1b[0m")
        return None, "photo"


def draw_and_display(image: Image.Image, img_name: str, width: int, mode: int):
    """Отрисовывает изображение в терминале и сохраняет HTML."""
    print(f"\n\x1b[33m⏳ Отрисовка фото '{img_name}' (Ширина: {width} символов)...\x1b[0m\n")
    
    if mode == 2:
        rendered_ansi, rendered_html = render_ascii_truecolor(image, width, bg_color=True)
    elif mode == 3:
        rendered_ansi, rendered_html = render_ascii_monochrome(image, width)
    elif mode == 4:
        rendered_ansi, rendered_html = render_ascii_halfblocks(image, width)
    else:
        rendered_ansi, rendered_html = render_ascii_truecolor(image, width, bg_color=False)

    # Вывод в терминал
    print(rendered_ansi)
    print("\n" + "=" * 60)
    print(f"\x1b[1;32m✔ Фото успешно отрисовано ({width} символов в ширину)!\x1b[0m")
    print("\x1b[36m💡 Совет:\x1b[0m Зажмите \x1b[1mCtrl\x1b[0m и покрутите \x1b[1mколесико мыши вниз\x1b[0m в терминале для фото-четкости.")

    # Сохранение HTML
    html_file = Path(f"{img_name}_ascii.html").resolve()
    save_html_viewer(rendered_html, html_file, title=f"ASCII - {img_name}")
    print(f"\x1b[32m✔ Сохранен HTML для браузера:\x1b[0m {html_file}")
    return html_file


def main():
    enable_windows_vt()
    print_banner()

    # Считывание аргументов командной строки, если они есть
    initial_file = None
    if len(sys.argv) > 1:
        # Соединяем аргументы на случай, если путь с пробелами был разбит
        combined = " ".join(sys.argv[1:])
        parsed_p = clean_file_path(combined)
        if parsed_p.exists():
            initial_file = parsed_p
        else:
            first_p = clean_file_path(sys.argv[1])
            if first_p.exists():
                initial_file = first_p

    current_image = None
    current_name = "photo"
    # Сверхвысокое разрешение по умолчанию: 200 символов (в 2 раза больше стандартного!)
    current_width = max(200, get_terminal_width(200))
    current_mode = 1

    # Если файл передан сразу (например, перетаскивание на run.bat или через аргумент)
    if initial_file:
        res_img, res_name = handle_file(initial_file, current_width, current_mode)
        if res_img is not None:
            current_image = res_img
            current_name = res_name

    # Главный интерактивный цикл — ТЕРМИНАЛ НИКОГДА НЕ ЗАКРЫВАЕТСЯ САМ!
    while True:
        print("\n\x1b[1;33m--- ЧТО ДЕЛАТЬ ДАЛЬШЕ: ---\x1b[0m")
        print("  • \x1b[1;32mПеретащите сюда фото или видео (Drag & Drop)\x1b[0m прямо в это окно и нажмите Enter")
        print("  • Нажмите \x1b[1m[F]\x1b[0m — Выбрать фото или видео через окно проводника Windows")
        print("  • Нажмите \x1b[1m[C]\x1b[0m — Вставить фото из буфера обмена (Ctrl+C / скриншот)")
        if current_image is not None:
            print(f"  • Нажмите \x1b[1m[+] / [-]\x1b[0m — Увеличить / уменьшить размер (текущая ширина: \x1b[1;32m{current_width}\x1b[0m симв.)")
            print(f"  • Нажмите \x1b[1m[W]\x1b[0m — Выбрать пресет разрешения (160 / 200 / 240 / 320 / 400)")
            print(f"  • Нажмите \x1b[1m[1-4]\x1b[0m — Перерисовать в другом режиме (1: Цвета, 2: Фон, 3: ЧБ, 4: Блоки)")
            print("  • Нажмите \x1b[1m[H]\x1b[0m — Открыть текущий арт в браузере (HTML)")
        print("  • Нажмите \x1b[1;31m[Q]\x1b[0m — Выйти")
        print()

        try:
            raw_input = input("\x1b[1;37mПеретащите файл или введите команду: \x1b[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход из программы.")
            break

        if not raw_input:
            # Если пустой ввод и есть картинка в буфере — берем ее
            clip_img = get_image_from_clipboard()
            if clip_img:
                current_image = clip_img
                current_name = "clipboard_image"
                print("\x1b[32m✔ Получено изображение из буфера обмена!\x1b[0m")
                draw_and_display(current_image, current_name, current_width, current_mode)
            else:
                print("\x1b[33mПеретащите файл фото или видео в окно и нажмите Enter, либо введите F / C.\x1b[0m")
            continue

        cmd = raw_input.lower()

        if cmd in ('q', 'quit', 'exit', 'й'):
            print("\nДо свидания!")
            break

        if cmd in ('+', '='):
            current_width = min(800, current_width + 40)
            if current_image is not None:
                draw_and_display(current_image, current_name, current_width, current_mode)
            else:
                print(f"\x1b[32mШирина увеличена до {current_width} символов.\x1b[0m")
            continue

        if cmd in ('-', '_'):
            current_width = max(40, current_width - 40)
            if current_image is not None:
                draw_and_display(current_image, current_name, current_width, current_mode)
            else:
                print(f"\x1b[32mШирина уменьшена до {current_width} символов.\x1b[0m")
            continue

        if cmd in ('f', 'file', 'а'):
            chosen = pick_file_dialog()
            if chosen:
                p = clean_file_path(chosen)
                if p.is_file():
                    res_img, res_name = handle_file(p, current_width, current_mode)
                    if res_img is not None:
                        current_image = res_img
                        current_name = res_name
            continue

        if cmd in ('c', 'clip', 'v', 'paste', 'с', 'м'):
            clip_img = get_image_from_clipboard()
            if clip_img:
                current_image = clip_img
                current_name = "clipboard_image"
                print("\x1b[32m✔ Успешно получено изображение из буфера обмена!\x1b[0m")
                draw_and_display(current_image, current_name, current_width, current_mode)
            else:
                print("\x1b[31mВ буфере обмена сейчас нет скопированной картинки.\x1b[0m")
            continue

        if current_image is not None and cmd in ('h', 'html', 'р'):
            html_p = Path(f"{current_name}_ascii.html").resolve()
            if html_p.exists():
                print(f"\x1b[32mОткрываю в браузере: {html_p}\x1b[0m")
                os.system(f'start "" "{html_p}"')
            else:
                print("\x1b[33mHTML файл еще не создан. Отрисуйте сначала фото.\x1b[0m")
            continue

        if current_image is not None and cmd in ('1', '2', '3', '4'):
            current_mode = int(cmd)
            draw_and_display(current_image, current_name, current_width, current_mode)
            continue

        if cmd in ('w', 'width', 'ц'):
            print("\n\x1b[1;36mВыберите масштаб разрешения (ширина в символах):\x1b[0m")
            print("  [1] Стандартное высокое (160 символов)")
            print("  [2] \x1b[1;32mСверхвысокое (200 символов) — Оптимально для большинства фото\x1b[0m")
            print("  [3] \x1b[1;35mУльтра-четкость (260 символов) — Максимальная детализация\x1b[0m")
            print("  [4] \x1b[1;33mЭкстремальное 4K (340 символов) — Для огромных экранов\x1b[0m")
            print("  [5] По ширине текущего окна консоли")
            print("  [6] Ввести точное число вручную (например: 180, 280, 450)")
            w_choice = input("Ваш выбор [1-6]: ").strip()
            if w_choice == '1':
                current_width = 160
            elif w_choice == '2':
                current_width = 200
            elif w_choice == '3':
                current_width = 260
            elif w_choice == '4':
                current_width = 340
            elif w_choice == '5':
                current_width = get_terminal_width(160)
            elif w_choice == '6':
                custom = input("Введите точную ширину: ").strip()
                try:
                    current_width = max(30, min(1000, int(custom)))
                except ValueError:
                    current_width = 200
            else:
                current_width = 200
            if current_image is not None:
                draw_and_display(current_image, current_name, current_width, current_mode)
            else:
                print(f"\x1b[32mУстановлено разрешение: {current_width} символов в ширину.\x1b[0m")
            continue

        # В остальных случаях проверяем перетащенный путь к файлу
        p = clean_file_path(raw_input)
        if not p.exists():
            print(f"\x1b[31m[!] Файл не найден: {p}\x1b[0m")
            print("\x1b[33mУбедитесь, что перетащили файл в окно или ввели правильный путь.\x1b[0m")
            continue

        res_img, res_name = handle_file(p, current_width, current_mode)
        if res_img is not None:
            current_image = res_img
            current_name = res_name


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nВыход из программы.")
    except Exception as e:
        import traceback
        print("\n\x1b[1;31m" + "=" * 60)
        print("ПРОИЗОШЛА ОШИБКА:")
        print("=" * 60 + "\x1b[0m")
        traceback.print_exc()
        print("\n\x1b[1;33m[!] Окно не закроется автоматически, чтобы вы могли прочесть ошибку.\x1b[0m")
        try:
            input("Нажмите Enter для выхода...")
        except Exception:
            pass
