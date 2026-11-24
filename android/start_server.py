"""
Локальный Wi-Fi сервер и QR-код для мгновенного открытия ASCII Studio на Android.
"""

import os
import sys
import socket
import http.server
import functools
from pathlib import Path

if sys.platform == "win32" and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _ip_priority(ip: str) -> tuple:
    """Сортирует IPv4-адреса, отдавая приоритет реальной локальной сети (192.168.x.x)."""
    if ip.startswith("192.168."):
        # Исключаем стандартные виртуальные подсети вроде VirtualBox (.56.) на шаг ниже
        return (0 if not ip.startswith("192.168.56.") else 1, ip)
    if ip.startswith("10."):
        return (2, ip)
    if ip.startswith("172."):
        try:
            second = int(ip.split(".")[1])
            if 16 <= second <= 31:
                # 172.19.0.1 / 172.17.x.x часто бывают VPN/Docker/WSL
                return (4 if ip.endswith(".0.1") else 3, ip)
        except ValueError:
            pass
    return (5, ip)


def get_local_ips() -> list[str]:
    """Возвращает список локальных IPv4-адресов в порядке приоритета для Wi-Fi/LAN."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr and not addr.startswith("127.") and not addr.startswith("169.254."):
                ips.add(addr)
    except Exception:
        pass

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        addr = s.getsockname()[0]
        if addr and not addr.startswith("127."):
            ips.add(addr)
    except Exception:
        pass
    finally:
        s.close()

    if not ips:
        return ["127.0.0.1"]
    return sorted(ips, key=_ip_priority)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def find_server(directory: Path, start_port: int = 8080):
    handler = functools.partial(QuietHandler, directory=str(directory))
    for port in range(start_port, start_port + 20):
        try:
            server = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
            return server, port
        except OSError:
            continue
    server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), handler)
    return server, server.server_address[1]


def main():
    android_dir = Path(__file__).resolve().parent
    ips = get_local_ips()
    primary_ip = ips[0]
    server, port = find_server(android_dir, 8080)
    url = f"http://{primary_ip}:{port}/"

    print("=" * 64)
    print("  ASCII STUDIO ANDROID — ЗАПУСК НА ТЕЛЕФОНЕ ПО WI-FI")
    print("=" * 64)
    print(f"\n  Откройте на Android в Chrome:  {url}")
    if len(ips) > 1:
        others = ", ".join(f"http://{x}:{port}/" for x in ips[1:])
        print(f"  Альтернативные адреса:         {others}")
    print()

    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        print("  Наведите камеру телефона на QR-код:\n")
        qr.print_ascii(invert=True)
    except Exception:
        pass

    print("\n  Совет: В Chrome нажмите '⋮' -> 'Добавить на главный экран',")
    print("  чтобы использовать как обычное приложение даже без ПК/интернета.")
    print("\n  Для остановки сервера нажмите Ctrl+C.")
    print("=" * 64)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
