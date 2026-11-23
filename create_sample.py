"""
Генератор тестового изображения для проверки ASCII Photo Studio.
"""
from PIL import Image, ImageDraw, ImageFont
import math

def create_sample_photo():
    w, h = 600, 400
    img = Image.new("RGB", (w, h), color=(15, 20, 35))
    draw = ImageDraw.Draw(img)
    
    # Красивый градиентный фон (закат над горами)
    for y in range(h):
        ratio = y / h
        r = int(240 * (1 - ratio) + 20 * ratio)
        g = int(100 * (1 - ratio) + 30 * ratio)
        b = int(60 * (1 - ratio) + 90 * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
        
    # Солнце
    draw.ellipse([230, 70, 370, 210], fill=(255, 235, 120))
    
    # Горы на горизонте
    points1 = [(0, 300), (120, 180), (220, 260), (350, 140), (480, 270), (600, 200), (600, 400), (0, 400)]
    draw.polygon(points1, fill=(50, 30, 70))
    
    points2 = [(0, 340), (80, 270), (180, 330), (300, 240), (420, 320), (550, 260), (600, 300), (600, 400), (0, 400)]
    draw.polygon(points2, fill=(25, 18, 45))
    
    # Вода и отражения
    for y in range(320, h):
        wave = int(math.sin(y * 0.2) * 15)
        draw.line([(240 + wave, y), (360 + wave, y)], fill=(250, 180, 80))
        
    # Текст для проверки резкости букв и цифр
    draw.text((30, 30), "HIGH RESOLUTION 4K", fill=(255, 255, 255))
    draw.text((30, 50), "Буквы, Цифры, Символы 2026", fill=(255, 220, 150))
    
    img.save("sample_photo.jpg", quality=95)
    print("Создано тестовое фото: sample_photo.jpg (600x400)")

if __name__ == "__main__":
    create_sample_photo()
