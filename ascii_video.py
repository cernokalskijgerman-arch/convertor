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
import threading
import subprocess
from pathlib import Path
import cv2
import numpy as np

# Безопасный импорт аудио-библиотек (av + miniaudio)
try:
    import av
    import miniaudio
    AUDIO_SUPPORTED = True
except ImportError:
    AUDIO_SUPPORTED = False


class VideoAudioPlayer:
    """
    Безопасный аудиоплеер для видеофайлов на базе PyAV и miniaudio.
    Если у видео нет звука или возникают ошибки аудиодрайвера, плеер
    тихо отключается, гарантируя бесперебойное воспроизведение видео.
    """
    def __init__(self, video_path: Path):
        self.video_path = video_path
        self.has_audio = False
        self.audio_error = ""
        self.muted = False
        self.paused = False
        self.device = None
        self.pcm_data = b""
        self._pcm_view = None
        self.sample_rate = 44100
        self.nchannels = 2
        self.bytes_per_frame = 4  # 16-bit stereo = 4 bytes per frame
        self.cursor = 0
        self.total_samples = 0
        self._lock = threading.Lock()
        self._generator = None

        if not AUDIO_SUPPORTED:
            self.audio_error = "av/miniaudio not installed"
            return

        try:
            self._extract_audio()
        except Exception as e:
            self.audio_error = str(e)
            self.has_audio = False

    def _extract_audio(self):
        container = av.open(str(self.video_path))
        try:
            audio_streams = [s for s in container.streams if s.type == 'audio']
            if not audio_streams:
                return

            stream = audio_streams[0]
            src_rate = getattr(stream, "rate", None) or 44100
            self.sample_rate = 44100 if src_rate == 44100 else 48000
            resampler = av.AudioResampler(format='s16', layout='stereo', rate=self.sample_rate)

            chunks = []
            first_pts_handled = False

            for packet in container.demux(stream):
                try:
                    frames = packet.decode()
                except Exception:
                    continue

                for frame in frames:
                    if not first_pts_handled:
                        first_pts_handled = True
                        if frame.pts is not None and stream.time_base is not None:
                            try:
                                start_sec = float(frame.pts * stream.time_base)
                                if 0.05 < start_sec < 30.0:
                                    pad_frames = int(start_sec * self.sample_rate)
                                    chunks.append(b"\x00" * (pad_frames * self.bytes_per_frame))
                            except Exception:
                                pass
                    try:
                        resampled_list = resampler.resample(frame)
                    except Exception:
                        continue
                    for res_frame in resampled_list:
                        chunks.append(res_frame.to_ndarray().tobytes())

            # Сброс остатка буфера ресемплера
            try:
                for res_frame in resampler.resample(None):
                    chunks.append(res_frame.to_ndarray().tobytes())
            except Exception:
                pass
        finally:
            container.close()

        if chunks:
            self.pcm_data = b"".join(chunks)
            self.total_samples = len(self.pcm_data) // self.bytes_per_frame
            if self.total_samples > 0:
                self._pcm_view = memoryview(self.pcm_data)
                self.has_audio = True

    def _audio_stream_generator(self):
        """Генератор потока PCM-кадров для протокола miniaudio.PlaybackDevice."""
        silence_cache = {}

        def get_silence(nbytes: int) -> bytes:
            buf = silence_cache.get(nbytes)
            if buf is None:
                buf = b"\x00" * nbytes
                if len(silence_cache) < 32:
                    silence_cache[nbytes] = buf
            return buf

        num_frames = yield b""
        while True:
            if not num_frames or num_frames <= 0:
                num_frames = yield b""
                continue

            bytes_needed = num_frames * self.bytes_per_frame
            with self._lock:
                paused = self.paused
                muted = self.muted
                cursor = self.cursor
                total = self.total_samples

                # Курсор продвигается и в режиме mute, чтобы звук оставался синхронным при возврате
                if not paused and cursor < total:
                    frames_avail = min(num_frames, total - cursor)
                    self.cursor = cursor + frames_avail
                else:
                    frames_avail = 0

            if paused or muted or frames_avail == 0 or self._pcm_view is None:
                num_frames = yield get_silence(bytes_needed)
                continue

            byte_start = cursor * self.bytes_per_frame
            byte_end = byte_start + frames_avail * self.bytes_per_frame
            if frames_avail == num_frames:
                chunk = self._pcm_view[byte_start:byte_end]
            else:
                chunk = self.pcm_data[byte_start:byte_end] + get_silence(bytes_needed - (byte_end - byte_start))

            num_frames = yield chunk

    def start(self):
        if not self.has_audio or not self.pcm_data:
            return

        try:
            self.stop()
            gen = self._audio_stream_generator()
            next(gen)
            self._generator = gen

            self.device = miniaudio.PlaybackDevice(
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=self.nchannels,
                sample_rate=self.sample_rate,
                buffersize_msec=80,
            )
            self.device.start(gen)
        except Exception as e:
            self.audio_error = str(e)
            self.has_audio = False
            self.stop()

    def pause(self):
        with self._lock:
            self.paused = True

    def resume(self):
        with self._lock:
            self.paused = False

    def toggle_mute(self):
        with self._lock:
            self.muted = not self.muted
            return self.muted

    def seek_to_time(self, time_sec: float):
        if self.has_audio:
            with self._lock:
                target_sample = int(max(0.0, time_sec) * self.sample_rate)
                self.cursor = max(0, min(self.total_samples, target_sample))

    def rewind(self):
        with self._lock:
            self.cursor = 0

    def get_time(self) -> float:
        if not self.has_audio or self.sample_rate <= 0:
            return 0.0
        with self._lock:
            return self.cursor / self.sample_rate

    def stop(self):
        if self.device is not None:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None
        self._generator = None

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
    Высокоскоростная генерация кадра из букв, цифр и символов в TrueColor 24-bit ANSI с гамма-коррекцией.
    """
    # Нелинейная гамма-коррекция 0.85 вытягивает тени и мелкие фактуры
    indices = (np.power(gray.astype(np.float32) / 255.0, 0.85) * (RAMP_LEN - 1)).astype(np.int32)
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
        # Сверхширокое разрешение: заполняет всё окно терминала (не менее 160)
        width = max(160, term_w)

    # Инициализация аудиоплеера (тихо отключается, если звука нет или аудио недоступно)
    audio_player = VideoAudioPlayer(video_path)

    # Скрытие курсора и очистка экрана
    sys.stdout.write("\x1b[?25l\x1b[2J\x1b[H")
    sys.stdout.flush()

    if audio_player.has_audio:
        audio_player.start()

    show_fps = True
    paused = False
    frame_idx = 0
    consecutive_eof = 0
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
                key = msvcrt.getwch()
                if key in ('\x00', '\xe0'):
                    if msvcrt.kbhit():
                        msvcrt.getwch()
                    continue
                k_low = key.lower()
                if k_low in ('q', 'й', '\x1b'):  # Q или Esc
                    return
                elif key == ' ':  # Пробел (пауза)
                    paused = not paused
                    if paused:
                        audio_player.pause()
                    else:
                        video_time = frame_idx / orig_fps
                        audio_player.seek_to_time(video_time)
                        t_start = time.perf_counter() - video_time
                        audio_player.resume()
                elif k_low in ('m', 'ь'):  # M (Вкл/Выкл звук)
                    audio_player.toggle_mute()
                elif key in ('1', '2', '3', '4'):
                    mode = int(key)
                elif k_low in ('f', 'а'):  # F (переключение оверлея FPS)
                    show_fps = not show_fps
                elif k_low in ('r', 'к'):  # R (перемотка в начало)
                    if not cap.set(cv2.CAP_PROP_POS_FRAMES, 0):
                        cap.release()
                        cap = cv2.VideoCapture(str(video_path))
                    frame_idx = 0
                    consecutive_eof = 0
                    t_start = time.perf_counter()
                    audio_player.rewind()
                elif key in ('+', '='):
                    width = min(600, width + 20)
                elif key in ('-', '_'):
                    width = max(30, width - 20)

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
                if cap.grab():
                    frame_idx += 1
                    continue

            # Задержка, если рендеринг идет с опережением
            wait_time = target_time - elapsed
            if wait_time > 0.001:
                time.sleep(wait_time)

            ret, frame = cap.read()
            if not ret:
                if loop:
                    consecutive_eof += 1
                    if consecutive_eof > 1 or not cap.set(cv2.CAP_PROP_POS_FRAMES, 0):
                        cap.release()
                        cap = cv2.VideoCapture(str(video_path))
                        if consecutive_eof > 3:
                            break
                    frame_idx = 0
                    t_start = time.perf_counter()
                    audio_player.rewind()
                    continue
                else:
                    break

            consecutive_eof = 0

            frame_idx += 1
            fps_counter += 1
            if time.perf_counter() - t_fps_measure >= 1.0:
                fps_display = fps_counter / (time.perf_counter() - t_fps_measure)
                fps_counter = 0
                t_fps_measure = time.perf_counter()

            # Улучшение сочности и контраста кадра для фотореалистичного цвета
            frame_enhanced = cv2.convertScaleAbs(frame, alpha=1.12, beta=3)
            frame_rgb = cv2.cvtColor(frame_enhanced, cv2.COLOR_BGR2RGB)

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
                if audio_player.has_audio:
                    snd_icon = "🔇 Звук: Выкл" if audio_player.muted else "🔊 Звук: Вкл"
                else:
                    snd_icon = "🔇 Без аудиодорожки"
                info_line = f"\x1b[1;30;47m {video_path.name} | FPS: {fps_display:.1f}/{orig_fps:.1f} | {snd_icon} | Режим [{mode}]: {mode_names.get(mode, '')} | [Пробел]=Пауза [M]=Звук [Q]=Выход \x1b[0m"
            else:
                info_line = ""

            # Вывод кадра: перемещение курсора в начало (0,0) без cls для 100% плавности
            out_buffer = f"\x1b[H{info_line}\n{rendered_str}"
            sys.stdout.write(out_buffer)
            sys.stdout.flush()

    except KeyboardInterrupt:
        pass
    finally:
        audio_player.stop()
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
