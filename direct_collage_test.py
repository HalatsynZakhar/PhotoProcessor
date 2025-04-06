#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Автономный скрипт для создания коллажа, не зависящий от Streamlit.
Этот скрипт создаст коллаж из всех изображений в указанной папке.
"""

import os
import sys
import time
from PIL import Image, ImageDraw
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

def create_direct_collage(source_dir, output_path):
    """
    Прямая функция для создания коллажа из изображений в указанной папке.
    Предельно простая реализация для проверки работоспособности.
    """
    log.info(f"Начинаем создание коллажа. Папка: {source_dir}, Выходной файл: {output_path}")
    start_time = time.time()
    
    # Проверка существования директории с изображениями
    if not os.path.isdir(source_dir):
        log.error(f"Директория не существует: {source_dir}")
        return False
    
    # Проверка директории для выходного файла
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir, exist_ok=True)
            log.info(f"Создана директория вывода: {output_dir}")
        except Exception as e:
            log.error(f"Не удалось создать директорию вывода: {e}")
            return False
    
    # Получение списка файлов изображений
    supported_extensions = ('.png', '.jpg', '.jpeg', '.bmp', '.gif')
    image_files = [f for f in os.listdir(source_dir) 
                 if os.path.isfile(os.path.join(source_dir, f)) 
                 and f.lower().endswith(supported_extensions)]
    
    if not image_files:
        log.error(f"В папке {source_dir} не найдены изображения")
        return False
        
    # Загружаем изображения (максимум 9)
    max_images = 9
    if len(image_files) > max_images:
        log.info(f"Найдено {len(image_files)} изображений, ограничиваем до {max_images}")
        image_files = image_files[:max_images]
    else:
        log.info(f"Найдено {len(image_files)} изображений для коллажа")
    
    # Загружаем изображения
    images = []
    for img_file in image_files:
        img_path = os.path.join(source_dir, img_file)
        try:
            img = Image.open(img_path)
            log.info(f"Загружено изображение: {img_file}, размер: {img.size}")
            images.append(img)
        except Exception as e:
            log.warning(f"Ошибка при загрузке {img_file}: {e}")
    
    if not images:
        log.error("Не удалось загрузить ни одного изображения")
        return False
    
    # Определяем параметры коллажа
    cols = int(pow(len(images), 0.5) + 0.5)  # примерно квадратная сетка
    rows = (len(images) + cols - 1) // cols
    
    # Определяем максимальные размеры для ячеек
    max_width = max(img.width for img in images)
    max_height = max(img.height for img in images)
    
    # Отступы между изображениями (2% от максимального размера)
    spacing_px = int(max(max_width, max_height) * 0.02)
    
    # Создаем холст для коллажа
    collage_width = cols * max_width + (cols + 1) * spacing_px
    collage_height = rows * max_height + (rows + 1) * spacing_px
    
    log.info(f"Создаем холст коллажа: {collage_width}x{collage_height}, сетка: {cols}x{rows}")
    
    # Создаем коллаж с белым фоном
    collage = Image.new('RGB', (collage_width, collage_height), (255, 255, 255))
    draw = ImageDraw.Draw(collage)
    
    # Размещаем изображения
    for i, img in enumerate(images):
        if i >= len(images):
            break
            
        row = i // cols
        col = i % cols
        
        # Позиция для текущего изображения
        x = spacing_px + col * (max_width + spacing_px)
        y = spacing_px + row * (max_height + spacing_px)
        
        # Вставляем изображение (без изменения размера, для простоты)
        try:
            if img.mode == 'RGBA':
                collage.paste(img, (x, y), img)
            else:
                collage.paste(img, (x, y))
        except Exception as e:
            log.warning(f"Ошибка вставки изображения {i}: {e}")
    
    # Добавляем рамку
    try:
        draw.rectangle([(0, 0), (collage_width-1, collage_height-1)], outline=(0, 0, 0), width=2)
    except Exception as e:
        log.warning(f"Не удалось нарисовать рамку: {e}")
    
    # Закрываем исходные изображения
    for img in images:
        try:
            img.close()
        except:
            pass
    
    # Сохраняем коллаж
    log.info(f"Сохраняем коллаж в {output_path}")
    
    try:
        collage.save(output_path, quality=95)
        
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            log.info(f"Коллаж успешно сохранен. Размер файла: {file_size} байт")
            
            total_time = time.time() - start_time
            log.info(f"Создание коллажа завершено за {total_time:.2f} секунд")
            
            return True
        else:
            log.error(f"Файл коллажа не найден после сохранения: {output_path}")
            return False
    except Exception as e:
        log.error(f"Ошибка при сохранении коллажа: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            collage.close()
        except:
            pass

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование: python direct_collage_test.py <папка_с_изображениями> <имя_выходного_файла>")
        sys.exit(1)
    
    input_folder = sys.argv[1]
    output_file = sys.argv[2]
    
    # Если output_file не содержит полный путь, сохраняем в ту же папку что и входные изображения
    if not os.path.dirname(output_file):
        output_file = os.path.join(input_folder, output_file)
    
    print(f"Создание коллажа из {input_folder} в {output_file}")
    success = create_direct_collage(input_folder, output_file)
    
    if success:
        print(f"Коллаж успешно создан: {output_file}")
    else:
        print("Ошибка при создании коллажа")
    
    sys.exit(0 if success else 1)
