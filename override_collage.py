#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Скрипт-обертка для перехвата вызова run_collage_processing.
Запустите его командой: streamlit run override_collage.py
"""

import os
import sys
import time
import logging
from PIL import Image, ImageDraw
import streamlit as st

# Настраиваем логирование перед импортом других модулей
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
log = logging.getLogger(__name__)

print("=== OVERRIDE COLLAGE MODULE LOADED ===")

# Импортируем модули приложения
import processing_workflows
import streamlit as st

# Сохраняем оригинальную функцию
original_run_collage_processing = processing_workflows.run_collage_processing

def create_direct_collage(source_dir, output_path, max_images=9, jpeg_quality=95, forced_cols=0, spacing_percent=2.0):
    """
    Создает коллаж из изображений в указанной папке и сохраняет по указанному пути.
    
    :param source_dir: Путь к папке с исходными изображениями
    :param output_path: Путь для сохранения коллажа
    :param max_images: Максимальное количество изображений в коллаже
    :param jpeg_quality: Качество JPEG (1-100)
    :param forced_cols: Принудительное количество колонок (0 = авто)
    :param spacing_percent: Отступы между изображениями в процентах
    :return: True если успешно, False если ошибка
    """
    log.info(f"Начинаем создание коллажа. Папка: {source_dir}, Выходной файл: {output_path}")
    print(f"--- PRINT: Начинаем создание коллажа. Папка: {source_dir}, Выходной файл: {output_path} ---")
    st.write(f"Начинаем создание коллажа...")
    
    start_time = time.time()
    
    try:
        # Проверка существования директории с изображениями
        if not os.path.isdir(source_dir):
            error_msg = f"Директория не существует: {source_dir}"
            log.error(error_msg)
            print(f"--- PRINT: ERROR: {error_msg} ---")
            st.error(error_msg)
            return False
        
        # Проверка выходного пути
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
                log.info(f"Создана директория вывода: {output_dir}")
                print(f"--- PRINT: Создана директория вывода: {output_dir} ---")
            except Exception as e:
                error_msg = f"Не удалось создать директорию вывода: {e}"
                log.error(error_msg)
                print(f"--- PRINT: ERROR: {error_msg} ---")
                st.error(error_msg)
                return False
        
        # Проверка прав на запись
        try:
            test_file = os.path.join(output_dir or source_dir, "_test_permissions.txt")
            with open(test_file, 'w') as f:
                f.write("Test write permission")
            os.remove(test_file)
            log.info("Проверка прав на запись: успешно")
            print("--- PRINT: Проверка прав на запись: успешно ---")
        except Exception as e:
            error_msg = f"Проверка прав на запись: ошибка - {e}"
            log.error(error_msg)
            print(f"--- PRINT: ERROR: {error_msg} ---")
            st.error(error_msg)
            return False
        
        # Получение списка файлов изображений
        supported_extensions = ('.png', '.jpg', '.jpeg', '.bmp', '.gif')
        image_files = [f for f in os.listdir(source_dir) 
                     if os.path.isfile(os.path.join(source_dir, f)) 
                     and f.lower().endswith(supported_extensions)
                     and not f.startswith("_test_")
                     and os.path.normcase(os.path.join(source_dir, f)) != os.path.normcase(output_path)]
        
        if not image_files:
            error_msg = f"В папке {source_dir} не найдены изображения"
            log.error(error_msg)
            print(f"--- PRINT: ERROR: {error_msg} ---")
            st.error(error_msg)
            return False
            
        # Если изображений слишком много, ограничиваем количество
        if len(image_files) > max_images:
            log.info(f"Найдено {len(image_files)} изображений, ограничиваем до {max_images}")
            print(f"--- PRINT: Найдено {len(image_files)} изображений, ограничиваем до {max_images} ---")
            image_files = image_files[:max_images]
        else:
            log.info(f"Найдено {len(image_files)} изображений для коллажа")
            print(f"--- PRINT: Найдено {len(image_files)} изображений для коллажа ---")
        
        st.info(f"Найдено {len(image_files)} изображений, создаю коллаж...")
        
        # Загружаем изображения
        images = []
        for img_file in image_files:
            img_path = os.path.join(source_dir, img_file)
            try:
                img = Image.open(img_path)
                log.info(f"Загружено изображение: {img_file}, размер: {img.size}")
                print(f"--- PRINT: Загружено изображение: {img_file}, размер: {img.size} ---")
                images.append(img)
            except Exception as e:
                log.warning(f"Ошибка при загрузке {img_file}: {e}")
                print(f"--- PRINT: WARNING: Ошибка при загрузке {img_file}: {e} ---")
        
        if not images:
            error_msg = "Не удалось загрузить ни одного изображения"
            log.error(error_msg)
            print(f"--- PRINT: ERROR: {error_msg} ---")
            st.error(error_msg)
            return False
        
        # Определяем параметры коллажа
        cols = forced_cols if forced_cols > 0 else int(pow(len(images), 0.5) + 0.5)  # примерно квадратная сетка
        rows = (len(images) + cols - 1) // cols
        
        # Определяем максимальные размеры для ячеек
        max_width = max(img.width for img in images)
        max_height = max(img.height for img in images)
        
        # Отступы между изображениями
        spacing_px = int(max(max_width, max_height) * (spacing_percent / 100.0))
        
        # Создаем холст для коллажа
        collage_width = cols * max_width + (cols + 1) * spacing_px
        collage_height = rows * max_height + (rows + 1) * spacing_px
        
        log.info(f"Создаем холст коллажа: {collage_width}x{collage_height}, сетка: {cols}x{rows}")
        print(f"--- PRINT: Создаем холст коллажа: {collage_width}x{collage_height}, сетка: {cols}x{rows} ---")
        
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
                    print(f"--- PRINT: WARNING: Ошибка изменения размера изображения {i}: {e} ---")
                    try:
                        if img.mode == 'RGBA':
                            collage.paste(img, (x, y), img)
                        else:
                            collage.paste(img, (x, y))
                    except Exception as paste_err:
                        log.warning(f"Ошибка вставки исходного изображения {i}: {paste_err}")
                        print(f"--- PRINT: WARNING: Ошибка вставки исходного изображения {i}: {paste_err} ---")
            else:
                # Вставляем изображение без изменения размера
                try:
                    if img.mode == 'RGBA':
                        collage.paste(img, (x, y), img)
                    else:
                        collage.paste(img, (x, y))
                except Exception as e:
                    log.warning(f"Ошибка вставки изображения {i}: {e}")
                    print(f"--- PRINT: WARNING: Ошибка вставки изображения {i}: {e} ---")
        
        # Добавляем рамку
        try:
            draw.rectangle([(0, 0), (collage_width-1, collage_height-1)], outline=(0, 0, 0), width=2)
        except Exception as e:
            log.warning(f"Не удалось нарисовать рамку: {e}")
            print(f"--- PRINT: WARNING: Не удалось нарисовать рамку: {e} ---")
        
        # Закрываем исходные изображения
        for img in images:
            try:
                img.close()
            except:
                pass
        
        # Сохраняем коллаж - метод 1: прямое сохранение
        log.info(f"Сохраняем коллаж в {output_path}")
        print(f"--- PRINT: Сохраняем коллаж в {output_path} ---")
        
        save_success = False
        
        # Метод 1: Прямое сохранение
        try:
            st.info("Сохраняю коллаж - метод 1...")
            collage.save(output_path, quality=jpeg_quality)
            
            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                log.info(f"Метод 1: Коллаж успешно создан! Размер файла: {file_size} байт")
                print(f"--- PRINT: Метод 1: Коллаж успешно создан! Размер файла: {file_size} байт ---")
                save_success = True
            else:
                log.error("Метод 1: Файл не найден после сохранения")
                print("--- PRINT: Метод 1: Файл не найден после сохранения ---")
        except Exception as e:
            log.error(f"Метод 1: Ошибка сохранения: {e}")
            print(f"--- PRINT: Метод 1: Ошибка сохранения: {e} ---")
            st.warning(f"Метод 1 не сработал: {e}. Пробую альтернативный метод...")
        
        # Метод 2: Через BytesIO
        if not save_success:
            try:
                from io import BytesIO
                log.info("Пробуем метод 2: сохранение через BytesIO")
                print("--- PRINT: Пробуем метод 2: сохранение через BytesIO ---")
                st.info("Сохраняю коллаж - метод 2...")
                
                buffer = BytesIO()
                collage.save(buffer, format="JPEG", quality=jpeg_quality)
                buffer.seek(0)
                
                with open(output_path, 'wb') as f:
                    f.write(buffer.getvalue())
                
                if os.path.exists(output_path):
                    file_size = os.path.getsize(output_path)
                    log.info(f"Метод 2: Коллаж успешно создан! Размер файла: {file_size} байт")
                    print(f"--- PRINT: Метод 2: Коллаж успешно создан! Размер файла: {file_size} байт ---")
                    save_success = True
                else:
                    log.error("Метод 2: Файл не найден после сохранения")
                    print("--- PRINT: Метод 2: Файл не найден после сохранения ---")
            except Exception as e:
                log.error(f"Метод 2: Ошибка сохранения: {e}")
                print(f"--- PRINT: Метод 2: Ошибка сохранения: {e} ---")
                st.warning(f"Метод 2 не сработал: {e}. Пробую последний метод...")
        
        # Метод 3: Через временный файл
        if not save_success:
            try:
                import tempfile
                log.info("Пробуем метод 3: сохранение через временный файл")
                print("--- PRINT: Пробуем метод 3: сохранение через временный файл ---")
                st.info("Сохраняю коллаж - метод 3...")
                
                with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp:
                    temp_path = tmp.name
                
                collage.save(temp_path, quality=jpeg_quality)
                
                import shutil
                shutil.copy2(temp_path, output_path)
                os.unlink(temp_path)
                
                if os.path.exists(output_path):
                    file_size = os.path.getsize(output_path)
                    log.info(f"Метод 3: Коллаж успешно создан! Размер файла: {file_size} байт")
                    print(f"--- PRINT: Метод 3: Коллаж успешно создан! Размер файла: {file_size} байт ---")
                    save_success = True
                else:
                    log.error("Метод 3: Файл не найден после сохранения")
                    print("--- PRINT: Метод 3: Файл не найден после сохранения ---")
            except Exception as e:
                log.error(f"Метод 3: Ошибка сохранения: {e}")
                print(f"--- PRINT: Метод 3: Ошибка сохранения: {e} ---")
                st.error(f"Не удалось сохранить коллаж: {e}")
        
        # Закрываем коллаж
        try:
            collage.close()
        except:
            pass
        
        # Финальный результат
        total_time = time.time() - start_time
        log.info(f"Время создания коллажа: {total_time:.2f} секунд")
        print(f"--- PRINT: Время создания коллажа: {total_time:.2f} секунд ---")
        
        if save_success:
            log.info("--- Коллаж успешно создан! ---")
            print("--- PRINT: Коллаж успешно создан! ---")
            st.success(f"Коллаж успешно создан и сохранен по пути: {output_path}")
            return True
        else:
            error_msg = "Не удалось сохранить коллаж после нескольких попыток"
            log.error(error_msg)
            print(f"--- PRINT: ERROR: {error_msg} ---")
            st.error(error_msg)
            return False
            
    except Exception as e:
        error_msg = f"Ошибка при создании коллажа: {e}"
        log.error(error_msg)
        print(f"--- PRINT: ERROR: {error_msg} ---")
        st.exception(e)
        import traceback
        traceback.print_exc()
        return False

# Функция-перехватчик для вызова create_direct_collage
def override_run_collage_processing(**all_settings):
    """
    Заменяет функцию run_collage_processing из processing_workflows.py
    Извлекает параметры из all_settings и вызывает create_direct_collage
    """
    log.info("--- [OVERRIDE] Запуск создания коллажа ---")
    print("--- PRINT: [OVERRIDE] Запуск создания коллажа ---")
    
    try:
        # Извлекаем настройки
        paths_settings = all_settings.get('paths', {})
        collage_settings = all_settings.get('collage_mode', {})
        
        source_dir = paths_settings.get('input_folder_path', '')
        output_filename = paths_settings.get('output_filename', '')
        
        # Если output_filename не содержит путь, сохраняем в папку с изображениями
        if output_filename and not os.path.dirname(output_filename):
            output_path = os.path.join(source_dir, output_filename)
        else:
            output_path = output_filename
        
        # Получаем остальные параметры
        jpeg_quality = int(collage_settings.get('jpeg_quality', 95))
        forced_cols = int(collage_settings.get('forced_cols', 0))
        spacing_percent = float(collage_settings.get('spacing_percent', 2.0))
        
        # Вызываем функцию создания коллажа
        result = create_direct_collage(
            source_dir=source_dir,
            output_path=output_path,
            max_images=9,
            jpeg_quality=jpeg_quality,
            forced_cols=forced_cols,
            spacing_percent=spacing_percent
        )
        
        return result
    except Exception as e:
        log.error(f"Ошибка в override_run_collage_processing: {e}")
        print(f"--- PRINT: ERROR в override_run_collage_processing: {e} ---")
        st.exception(e)
        return False

# Заменяем оригинальную функцию нашей
import processing_workflows
processing_workflows.run_collage_processing = override_run_collage_processing

# Запускаем оригинальный Streamlit app
if __name__ == "__main__":
    # Уведомляем пользователя, что мы используем переопределенную функцию
    print("=== Запуск Streamlit с переопределенной функцией создания коллажа ===")
    
    # Запускаем оригинальное приложение
    app_module = __import__("app")
    # Все остальные функции и переменные будут взяты из оригинального модуля 