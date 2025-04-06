import os
import time
import logging
from PIL import Image, ImageDraw

# Настраиваем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

def create_test_images(output_dir, count=5):
    """Создает тестовые изображения для проверки"""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        log.info(f"Создана директория: {output_dir}")
    
    image_files = []
    for i in range(count):
        width = 100 + i * 50
        height = 100 + i * 30
        img = Image.new('RGB', (width, height), color=(50 + i * 30, 100, 150))
        
        # Добавляем текст на изображение
        draw = ImageDraw.Draw(img)
        draw.text((10, 10), f"Test {i+1}", fill=(255, 255, 255))
        
        # Сохраняем изображение
        filename = os.path.join(output_dir, f"test_image_{i+1}.jpg")
        img.save(filename, "JPEG", quality=95)
        log.info(f"Создано изображение: {filename}, размер: {width}x{height}")
        image_files.append(filename)
    
    return image_files

def create_simple_collage(input_dir, output_path, cols=2):
    """Создает простой коллаж из всех изображений в директории"""
    log.info(f"Создание коллажа из {input_dir} в {output_path}")
    start_time = time.time()
    
    try:
        # Проверка входных данных
        if not os.path.exists(input_dir) or not os.path.isdir(input_dir):
            log.error(f"Директория не существует: {input_dir}")
            return False
        
        # Проверка/создание директории вывода
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
            log.info(f"Создана директория вывода: {output_dir}")
        
        # Собираем список файлов изображений
        image_files = [f for f in os.listdir(input_dir) 
                     if os.path.isfile(os.path.join(input_dir, f)) 
                     and f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp'))]
        
        if not image_files:
            log.error(f"В директории {input_dir} нет изображений")
            return False
        
        log.info(f"Найдено {len(image_files)} изображений")
        
        # Загружаем изображения
        images = []
        for img_file in image_files:
            try:
                img_path = os.path.join(input_dir, img_file)
                img = Image.open(img_path)
                images.append(img)
                log.info(f"Загружено: {img_file}, размер: {img.size}")
            except Exception as e:
                log.error(f"Ошибка загрузки {img_file}: {e}")
        
        if not images:
            log.error("Не удалось загрузить ни одного изображения")
            return False
        
        # Определяем размер сетки
        if cols <= 0:
            cols = int(pow(len(images), 0.5) + 0.5)  # корень из количества изображений, округленный вверх
        rows = (len(images) + cols - 1) // cols
        
        # Определяем максимальные размеры
        max_width = max(img.width for img in images)
        max_height = max(img.height for img in images)
        
        # Добавляем отступ между изображениями
        spacing = 10
        
        # Создаем холст для коллажа
        collage_width = cols * max_width + (cols + 1) * spacing
        collage_height = rows * max_height + (rows + 1) * spacing
        
        log.info(f"Создаем коллаж размером {collage_width}x{collage_height}, сетка {cols}x{rows}")
        
        # Создаем коллаж с белым фоном
        collage = Image.new('RGB', (collage_width, collage_height), (255, 255, 255))
        
        # Размещаем изображения в сетке
        for i, img in enumerate(images):
            if i >= len(images):
                break
            
            row = i // cols
            col = i % cols
            
            # Вычисляем позицию для изображения
            x = spacing + col * (max_width + spacing)
            y = spacing + row * (max_height + spacing)
            
            # Размещаем изображение
            collage.paste(img, (x, y))
        
        # Сохраняем коллаж
        log.info(f"Сохраняем коллаж в {output_path}")
        collage.save(output_path, "JPEG", quality=95)
        
        # Проверяем, что файл создан
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            log.info(f"Коллаж успешно создан! Размер файла: {file_size} байт")
            return True
        else:
            log.error(f"Не удалось создать файл коллажа: {output_path}")
            return False
            
    except Exception as e:
        log.error(f"Ошибка при создании коллажа: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        total_time = time.time() - start_time
        log.info(f"Время создания коллажа: {total_time:.2f} секунд")

if __name__ == "__main__":
    # Путь для тестирования
    test_dir = os.path.join(os.getcwd(), "test_images")
    output_file = os.path.join(test_dir, "collage_output.jpg")
    
    # Создаем тестовые изображения
    create_test_images(test_dir)
    
    # Создаем коллаж
    success = create_simple_collage(test_dir, output_file)
    
    if success:
        print(f"\nКоллаж успешно создан и сохранен по пути: {output_file}")
    else:
        print("\nНе удалось создать коллаж") 