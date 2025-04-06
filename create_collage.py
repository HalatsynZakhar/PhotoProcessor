import os
import sys
import time
import logging
from PIL import Image, ImageDraw

# Настраиваем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

def create_collage(source_dir, output_path, max_images=9):
    """
    Создает коллаж из изображений в указанной папке и сохраняет по указанному пути.
    
    :param source_dir: Путь к папке с исходными изображениями
    :param output_path: Путь для сохранения коллажа
    :param max_images: Максимальное количество изображений в коллаже
    :return: True если успешно, False если ошибка
    """
    log.info(f"Начинаем создание коллажа. Папка: {source_dir}, Выходной файл: {output_path}")
    start_time = time.time()
    
    try:
        # Проверка существования директории с изображениями
        if not os.path.isdir(source_dir):
            log.error(f"Директория не существует: {source_dir}")
            return False
        
        # Получение списка файлов изображений
        supported_extensions = ('.png', '.jpg', '.jpeg', '.bmp', '.gif')
        image_files = [f for f in os.listdir(source_dir) 
                     if os.path.isfile(os.path.join(source_dir, f)) 
                     and f.lower().endswith(supported_extensions)]
        
        if not image_files:
            log.error(f"В папке {source_dir} не найдены изображения")
            return False
            
        # Если изображений слишком много, ограничиваем количество
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
            
            # Если размер изображения не совпадает с максимальным, меняем размер и центрируем
            if img.width != max_width or img.height != max_height:
                ratio = min(max_width / img.width, max_height / img.height)
                new_width = int(img.width * ratio)
                new_height = int(img.height * ratio)
                
                try:
                    resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    
                    # Центрируем изображение в ячейке
                    x_offset = (max_width - new_width) // 2
                    y_offset = (max_height - new_height) // 2
                    
                    # Вставляем изображение
                    if resized_img.mode == 'RGBA':
                        collage.paste(resized_img, (x + x_offset, y + y_offset), resized_img)
                    else:
                        collage.paste(resized_img, (x + x_offset, y + y_offset))
                except Exception as e:
                    log.warning(f"Ошибка изменения размера изображения {i}: {e}")
                    try:
                        if img.mode == 'RGBA':
                            collage.paste(img, (x, y), img)
                        else:
                            collage.paste(img, (x, y))
                    except Exception as paste_err:
                        log.warning(f"Ошибка вставки исходного изображения {i}: {paste_err}")
            else:
                # Вставляем изображение без изменения размера
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
        
        # Создаем директорию для сохранения, если её нет
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        # Сохраняем коллаж
        log.info(f"Сохраняем коллаж в {output_path}")
        collage.save(output_path, quality=95)
        
        # Проверяем, что файл создался
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            log.info(f"Коллаж успешно сохранен. Размер файла: {file_size} байт")
            collage.close()
            
            total_time = time.time() - start_time
            log.info(f"Создание коллажа завершено за {total_time:.2f} секунд")
            return True
        else:
            log.error(f"Файл коллажа не найден после сохранения: {output_path}")
            collage.close()
            return False
            
    except Exception as e:
        log.error(f"Ошибка при создании коллажа: {e}")
        import traceback
        traceback.print_exc()
        return False

def print_usage():
    print("Использование:")
    print("  python create_collage.py <папка_с_изображениями> <имя_выходного_файла>")
    print("")
    print("Пример:")
    print("  python create_collage.py C:\\Users\\zakhar\\Downloads collage.jpg")
    print("  python create_collage.py C:\\Users\\zakhar\\Downloads C:\\Users\\zakhar\\Desktop\\my_collage.jpg")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Ошибка: недостаточно аргументов.")
        print_usage()
        sys.exit(1)
    
    input_folder = sys.argv[1]
    output_file = sys.argv[2]
    
    # Если output_file не содержит полный путь, сохраняем в ту же папку что и входные изображения
    if not os.path.dirname(output_file):
        output_file = os.path.join(input_folder, output_file)
    
    print(f"Создание коллажа из изображений в: {input_folder}")
    print(f"Выходной файл: {output_file}")
    
    # Создаем коллаж
    if create_collage(input_folder, output_file):
        print(f"\nКоллаж успешно создан и сохранен по пути: {output_file}")
    else:
        print("\nНе удалось создать коллаж. Проверьте журнал ошибок выше.")
        sys.exit(1) 