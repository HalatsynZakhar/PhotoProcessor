"""
Утилиты для многопроцессорной обработки изображений.
Позволяет обходить ограничения GIL и распределять нагрузку по всем ядрам процессора.
"""

import os
import sys
import time
import pickle
import logging
import tempfile
import traceback
from typing import List, Any, Dict, Tuple, Optional, Callable, Union
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from PIL import Image, ImageFile
import io

ImageFile.LOAD_TRUNCATED_IMAGES = True

# Настройки для работы с очень большими изображениями
Image.MAX_IMAGE_PIXELS = 500000000  # 500 миллионов пикселей
import warnings
warnings.filterwarnings("ignore", "(Possible|Image size).*exceeds limit", UserWarning)

# Настройка логирования
log = logging.getLogger(__name__)

def enable_multiprocessing():
    """
    Подготавливает систему к многопроцессорной обработке.
    Этот метод вызывается перед запуском многопроцессорной обработки.
    """
    # Больше не пытаемся переопределить ThreadPoolExecutor
    # Вместо этого мы будем напрямую использовать ProcessPoolExecutor
    
    # Проверяем, доступно ли многопроцессное выполнение
    cpu_count = multiprocessing.cpu_count()
    log.info(f"Multiprocessing enabled: system has {cpu_count} CPU cores available")
    
    # Настраиваем метод запуска для Windows
    if sys.platform == 'win32':
        try:
            # На Windows нужно использовать 'spawn' метод
            # Однако set_start_method можно вызвать только один раз
            # Поэтому проверяем текущий метод и устанавливаем только если еще не установлен
            current_method = multiprocessing.get_start_method(allow_none=True)
            log.info(f"Current multiprocessing start method: {current_method}")
            
            if current_method != 'spawn':
                multiprocessing.set_start_method('spawn', force=True)
                log.info("Set multiprocessing start method to 'spawn' for Windows")
            else:
                log.info("Multiprocessing start method 'spawn' already set")
        except RuntimeError as e:
            # Этот метод уже был установлен
            log.warning(f"Could not set start method to 'spawn': {e}")
            # Но это не критическая ошибка, продолжаем выполнение
            
    # Дополнительные проверки и настройки в зависимости от ОС
    log.info(f"multiprocessing.cpu_count() = {multiprocessing.cpu_count()}")
    log.info(f"Platform: {sys.platform}")
    
    # Отключаем отображение информации о подпроцессах в консоли на Windows
    # Это предотвращает открытие консольных окон для каждого процесса
    if sys.platform == 'win32':
        try:
            # Устанавливаем флаг CREATE_NO_WINDOW для процессов на Windows
            # Новый способ - не использовать внутренний атрибут _subprocess
            # CREATE_NO_WINDOW = 0x08000000 будет использовано при создании процессов
            # Устанавливаем переменную окружения для дочерних процессов
            os.environ['PYTHONNOWINDOW'] = '1'
            log.info("Set PYTHONNOWINDOW environment variable for Windows subprocesses")
        except Exception as e:
            log.warning(f"Failed to set process creation flags for Windows: {e}")

def init_worker():
    """
    Функция инициализации рабочего процесса.
    Устанавливает корректное логирование и другие необходимые настройки.
    """
    import os
    import sys
    import logging
    
    # Настраиваем логирование для процесса
    worker_logger = logging.getLogger(f"Process-{os.getpid()}")
    worker_logger.setLevel(logging.INFO)
    
    # Очищаем существующие обработчики, если они есть
    if worker_logger.handlers:
        for handler in worker_logger.handlers:
            worker_logger.removeHandler(handler)
    
    # Создаем и настраиваем новый обработчик для вывода в консоль
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
    worker_logger.addHandler(handler)
    
    worker_logger.info(f"Worker process {os.getpid()} initialized")
    
    # Убеждаемся, что дочерние процессы также не создают окон на Windows
    if sys.platform == 'win32':
        os.environ['PYTHONNOWINDOW'] = '1'
        worker_logger.info(f"Worker process {os.getpid()} set PYTHONNOWINDOW=1")
    
    # Устанавливаем обработчик LOAD_TRUNCATED_IMAGES для PIL
    from PIL import Image, ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    # Настройки для больших изображений
    Image.MAX_IMAGE_PIXELS = 500000000
    import warnings
    warnings.filterwarnings("ignore", "(Possible|Image size).*exceeds limit", UserWarning)
    
    # Настройки для работы с очень большими изображениями в воркере
    Image.MAX_IMAGE_PIXELS = 500000000  # 500 миллионов пикселей
    import warnings
    warnings.filterwarnings("ignore", "(Possible|Image size).*exceeds limit", UserWarning)
    
    # Добавляем дополнительную диагностику
    worker_logger.info(f"Worker process {os.getpid()} running on CPU cores: {multiprocessing.cpu_count()}")
    worker_logger.info(f"Worker process {os.getpid()} initial memory usage: {get_process_memory_info()}")

def get_process_memory_info():
    """
    Получает информацию о памяти текущего процесса.
    """
    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        return f"RSS: {mem_info.rss/1024/1024:.2f} MB, VMS: {mem_info.vms/1024/1024:.2f} MB"
    except ImportError:
        return "psutil not available"
    except Exception as e:
        return f"Error getting memory info: {e}"

def process_images_with_multiprocessing(files, processing_func, settings, max_workers=None):
    """
    Обрабатывает изображения с использованием ProcessPoolExecutor.
    Подходит для обработки больших изображений, когда требуется интенсивная CPU работа.
    
    Args:
        files: Список путей к файлам для обработки
        processing_func: Функция обработки, которая применяется к каждому файлу
        settings: Настройки обработки (будут сериализованы и переданы в процесс)
        max_workers: Максимальное количество процессов
        
    Returns:
        Список результатов обработки
    """
    if not files:
        return []
        
    import tempfile
    import pickle
    from concurrent.futures import ProcessPoolExecutor
    
    # Определяем количество процессов
    if max_workers is None or max_workers <= 0:
        max_workers = multiprocessing.cpu_count()
    
    log.info(f"Starting multiprocessing with {max_workers} workers for {len(files)} files")
    log.info(f"Main process ID: {os.getpid()}, memory usage: {get_process_memory_info()}")
    
    # Создаем временный файл для сохранения настроек
    settings_file = None
    try:
        # Используем NamedTemporaryFile для создания временного файла
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pkl') as temp_file:
            settings_file = temp_file.name
            # Сериализуем настройки
            pickle.dump(settings, temp_file)
            log.debug(f"Settings serialized to temporary file: {settings_file}")
        
        # Подготавливаем аргументы для каждого процесса
        process_args = [(i, file_path, settings_file) for i, file_path in enumerate(files)]
        
        # Запускаем обработку в пуле процессов
        log.info(f"Creating ProcessPoolExecutor with max_workers={max_workers}")
        results = []
        
        # Выводим подробную информацию о текущем процессе
        log.debug(f"Main process ID: {os.getpid()}")
        
        # Запускаем обработку в отдельных процессах
        with ProcessPoolExecutor(max_workers=max_workers, initializer=init_worker) as executor:
            log.info(f"ProcessPoolExecutor created, submitting {len(files)} tasks")
            
            # Проверяем, установлена ли переменная окружения PYTHONNOWINDOW
            if sys.platform == 'win32' and os.environ.get('PYTHONNOWINDOW', '') == '1':
                log.info("Using PYTHONNOWINDOW=1 for spawned processes to prevent console windows")
            
            # Отправляем задачи на выполнение
            futures = [executor.submit(processing_func, args) for args in process_args]
            
            # Получаем результаты по мере их готовности и логируем процесс
            for i, future in enumerate(futures):
                try:
                    log.info(f"Waiting for task {i+1}/{len(futures)} to complete...")
                    result = future.result()
                    if result:
                        log.info(f"Task {i+1}/{len(futures)} completed successfully")
                    else:
                        log.warning(f"Task {i+1}/{len(futures)} completed but returned None")
                    results.append(result)
                except Exception as e:
                    log.error(f"Error processing task {i+1}/{len(futures)}: {e}")
                    log.exception("Exception details")
                    results.append(None)
        
        log.info(f"All {len(files)} tasks completed. Got {sum(1 for r in results if r is not None)} successful results")
        return results
    
    except Exception as e:
        log.error(f"Error in process_images_with_multiprocessing: {e}")
        traceback.print_exc()
        return []
        
    finally:
        # Удаляем временный файл с настройками
        if settings_file and os.path.exists(settings_file):
            try:
                os.unlink(settings_file)
                log.debug(f"Temporary settings file removed: {settings_file}")
            except Exception as e:
                log.warning(f"Failed to remove temporary settings file: {e}")

def mp_process_individual_file(args):
    """
    Функция для обработки одного файла в отдельном процессе для режима individual processing.
    
    Args:
        args: Кортеж (index, file_path, settings_path)
        
    Returns:
        Путь к обработанному файлу или None при ошибке
    """
    import os
    import sys
    import logging
    import pickle
    from PIL import Image, ImageFile
    import io
    import time
    
    # Устанавливаем параметр для работы с усечёнными изображениями
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    # Настройки для больших изображений
    Image.MAX_IMAGE_PIXELS = 500000000
    import warnings
    warnings.filterwarnings("ignore", "(Possible|Image size).*exceeds limit", UserWarning)
    
    # Получаем логгер процесса
    log = logging.getLogger(f"Process-{os.getpid()}")
    
    i, file_path, settings_path = args
    
    log.info(f"Process {os.getpid()} started processing file {i+1}: {os.path.basename(file_path)}")
    
    # Проверка существования файла
    if not os.path.exists(file_path):
        log.error(f"File not found: {file_path}")
        return None
    
    # Проверка существования файла настроек
    if not os.path.exists(settings_path):
        log.error(f"Settings file not found: {settings_path}")
        return None
    
    try:
        start_time = time.time()
        
        # Загружаем настройки из файла
        try:
            with open(settings_path, 'rb') as f:
                settings = pickle.load(f)
            log.info(f"Settings loaded successfully from {settings_path}")
        except Exception as e:
            log.error(f"Failed to load settings: {e}")
            return None
        
        # Получаем настройки из словаря
        preprocessing_settings = settings.get('preprocessing_settings', {})
        whitening_settings = settings.get('whitening_settings', {})
        background_crop_settings = settings.get('background_crop_settings', {})
        padding_settings = settings.get('padding_settings', {})
        brightness_contrast_settings = settings.get('brightness_contrast_settings', {})
        individual_mode_settings = settings.get('individual_mode_settings', {})
        merge_settings = settings.get('merge_settings', {})
        paths = settings.get('paths', {})
        output_folder = paths.get('output_folder_path', '')
        output_format = individual_mode_settings.get('output_format', 'jpg')
        same_input_output = settings.get('same_input_output', False)
        
        # Проверка существования выходной директории
        if not os.path.isdir(output_folder):
            try:
                os.makedirs(output_folder, exist_ok=True)
                log.info(f"Created output directory: {output_folder}")
            except Exception as e:
                log.error(f"Failed to create output directory: {e}")
                return None
        
        # Импортируем функции обработки из processing_workflows
        try:
            log.info("Importing processing modules...")
            from processing_workflows import process_image_base, _apply_force_aspect_ratio, _apply_max_dimensions
            from processing_workflows import _apply_final_canvas_or_prepare, _merge_with_template, _save_image
        except ImportError as e:
            log.error(f"Failed to import required modules: {e}")
            return None
        
        # Обрабатываем изображение
        filename = os.path.basename(file_path)
        
        log.info(f"Starting base processing for file {i+1}: {filename}")
        
        # Используем базовый конвейер обработки для всех основных шагов
        try:
            img, image_metadata = process_image_base(
                file_path,
                preprocessing_settings,
                whitening_settings,
                background_crop_settings,
                padding_settings,
                brightness_contrast_settings
            )
            
            if img is None:
                log.error(f"Base processing failed for {filename}")
                return None
            
            log.info(f"Base processing completed successfully. Image size: {img.size}, mode: {img.mode}")
            
        except Exception as e:
            log.error(f"Error in base processing: {e}")
            return None
        
        # Применяем дополнительные преобразования
        # 1. Объединение с шаблоном, если включено
        enable_merge = merge_settings.get('enable_merge', False)
        if enable_merge:
            log.info("Merge with template step")
            try:
                # Проверяем наличие байтов шаблона в настройках
                template_image = None
                if 'template_image_bytes' in settings:
                    # Восстанавливаем шаблон из байтов
                    template_bytes = settings.get('template_image_bytes')
                    log.info(f"Template bytes received: {len(template_bytes)} bytes")
                    buffer = io.BytesIO(template_bytes)
                    template_image = Image.open(buffer)
                    template_image.load()  # Загружаем данные
                    log.info(f"Template loaded from bytes: {template_image.size} ({template_image.mode})")
                
                if template_image:
                    # Add jpg_background_color to merge_settings
                    merge_settings['jpg_background_color'] = individual_mode_settings.get('jpg_background_color', [255, 255, 255])
                    
                    # Merge image with template
                    img = _merge_with_template(img, template_image, merge_settings)
                    template_image.close()  # Закрываем шаблон
                    if img is None:
                        log.error(f"Failed to merge with template for {filename}")
                        return None
                    log.info(f"Template merged successfully")
                else:
                    log.warning("Template image not found, skipping merge")
            except Exception as e:
                log.error(f"Error merging with template: {e}")
                return None
        
        # 2. Применяем финальные настройки
        try:
            log.info("Applying final transformations")
            
            # Проверяем, является ли это первым файлом с особыми настройками
            is_first_file = (i == 0 and individual_mode_settings.get('special_first_file', False))
            settings_to_use = individual_mode_settings.get('first_file_settings', {}) if is_first_file else individual_mode_settings
            
            # Применяем соотношение сторон, если нужно
            if settings_to_use.get('enable_force_aspect_ratio', False):
                img = _apply_force_aspect_ratio(img, settings_to_use.get('force_aspect_ratio', [1, 1]))
                log.info(f"Force aspect ratio applied")
            
            # Применяем максимальные размеры, если нужно
            if settings_to_use.get('enable_max_dimensions', False):
                img = _apply_max_dimensions(img, 
                                        settings_to_use.get('max_output_width', 0),
                                        settings_to_use.get('max_output_height', 0))
                log.info(f"Max dimensions applied")
            
            # Применяем точный холст, если нужно
            if settings_to_use.get('enable_exact_canvas', False):
                img = _apply_final_canvas_or_prepare(img,
                                                  settings_to_use.get('final_exact_width', 0),
                                                  settings_to_use.get('final_exact_height', 0),
                                                  output_format,
                                                  individual_mode_settings.get('jpg_background_color', [255, 255, 255]))
                log.info(f"Exact canvas applied")
        except Exception as e:
            log.error(f"Error applying final transformations: {e}")
            if img:
                img.close()
            return None
        
        # 3. Сохраняем обработанное изображение
        try:
            log.info("Saving processed image")
            
            # Определяем имя выходного файла
            output_filename = filename
            if individual_mode_settings.get('enable_rename', False):
                article = individual_mode_settings.get('article_name', '')
                if article:
                    # Первый файл (i=0) получает имя просто АРТИКУЛ, остальные - АРТИКУЛ_INDEX
                    if i == 0:
                        output_filename = f"{article}.{output_format}"
                    else:
                        output_filename = f"{article}_{i}.{output_format}"
                    log.info(f"Renamed to: {output_filename}")
            else:
                # Гарантируем, что у выходного файла правильное расширение
                output_filename = os.path.splitext(filename)[0] + f".{output_format}"
            
            # Полный путь к выходному файлу
            output_path = os.path.join(output_folder, output_filename)
            
            # Сохраняем файл с правильными параметрами для формата
            if output_format == 'jpg':
                # Общие параметры
                jpeg_quality = individual_mode_settings.get('jpeg_quality', 95)
                remove_metadata = individual_mode_settings.get('remove_metadata', False)
                jpg_background_color = individual_mode_settings.get('jpg_background_color', [255, 255, 255])
                # Сохраняем JPEG
                success = _save_image(img, output_path, 'jpg', 
                                    jpeg_quality=jpeg_quality,
                                    jpg_background_color=jpg_background_color,
                                    remove_metadata=remove_metadata)
                log.info(f"Saved as JPEG with quality {jpeg_quality}")
            else:  # png
                # Общие параметры
                remove_metadata = individual_mode_settings.get('remove_metadata', False)
                png_transparent_background = individual_mode_settings.get('png_transparent_background', True)
                png_background_color = individual_mode_settings.get('png_background_color', [255, 255, 255])
                # Сохраняем PNG
                success = _save_image(img, output_path, 'png',
                                    png_transparent_background=png_transparent_background,
                                    png_background_color=png_background_color,
                                    remove_metadata=remove_metadata)
                log.info(f"Saved as PNG with transparent={png_transparent_background}")
            
            # Закрываем изображение
            if img:
                img.close()
                
            if success:
                elapsed_time = time.time() - start_time
                log.info(f"Process {os.getpid()} completed file {i+1} in {elapsed_time:.2f} seconds: {output_filename}")
                return output_path  # Возвращаем путь к выходному файлу при успехе
            else:
                log.error(f"Failed to save processed image to {output_path}")
                return None
                
        except Exception as e:
            log.error(f"Error saving processed image: {e}")
            if img:
                img.close()
            return None
            
    except Exception as e:
        log.error(f"Unexpected error processing {os.path.basename(file_path)}: {e}")
        import traceback
        log.error(traceback.format_exc())
        return None

def mp_process_collage_image(args):
    """
    Функция для обработки одного изображения в отдельном процессе для режима коллажа.
    Возвращает сериализованное изображение как байты.
    
    Args:
        args: Кортеж (index, file_path, settings_path)
        
    Returns:
        bytes: Сериализованное изображение или None при ошибке
    """
    import os
    import sys
    import logging
    import pickle
    import time
    from PIL import Image, ImageFile
    import io
    import traceback
    
    # Устанавливаем параметр для работы с усечёнными изображениями
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    # Настройки для больших изображений
    Image.MAX_IMAGE_PIXELS = 500000000
    import warnings
    warnings.filterwarnings("ignore", "(Possible|Image size).*exceeds limit", UserWarning)
    
    # Получаем логгер процесса
    log = logging.getLogger(f"Process-{os.getpid()}")
    
    i, file_path, settings_path = args
    
    log.info(f"Process {os.getpid()} started collage image processing {i+1}: {os.path.basename(file_path)}")
    
    # Проверка существования файла
    if not os.path.exists(file_path):
        log.error(f"File not found: {file_path}")
        return None
    
    # Проверка существования файла настроек
    if not os.path.exists(settings_path):
        log.error(f"Settings file not found: {settings_path}")
        return None
    
    try:
        start_time = time.time()
        
        # Загружаем настройки из файла
        try:
            with open(settings_path, 'rb') as f:
                settings = pickle.load(f)
            log.info(f"Settings loaded successfully from {settings_path}")
        except Exception as e:
            log.error(f"Failed to load settings: {e}")
            return None
        
        # Получаем настройки из словаря
        preprocessing_settings = settings.get('preprocessing_settings', {})
        whitening_settings = settings.get('whitening_settings', {})
        background_crop_settings = settings.get('background_crop_settings', {})
        padding_settings = settings.get('padding_settings', {})
        brightness_contrast_settings = settings.get('brightness_contrast_settings', {})
        jpg_background_color = settings.get('jpg_background_color', [255, 255, 255])
        
        # Импортируем функции обработки из processing_workflows
        try:
            log.info("Importing processing modules...")
            from processing_workflows import process_image_base
        except ImportError as e:
            log.error(f"Failed to import required modules: {e}")
            return None
        
        # Обрабатываем изображение
        filename = os.path.basename(file_path)
        
        # Используем базовый конвейер обработки для всех основных шагов
        try:
            log.info(f"Starting base processing for collage image {i+1}: {filename}")
            img, metadata = process_image_base(
                file_path,
                preprocessing_settings,
                whitening_settings,
                background_crop_settings,
                padding_settings,
                brightness_contrast_settings,
                {'jpg_background_color': jpg_background_color}  # Передаем цвет фона из настроек
            )
            
            if img is None:
                log.error(f"Base processing failed for {filename}")
                return None
                
            log.info(f"Base processing for collage completed. Image size: {img.size}, mode: {img.mode}")
                
        except Exception as e:
            log.error(f"Error in base processing: {e}")
            log.error(traceback.format_exc())
            return None
        
        # Убедимся, что изображение в формате RGBA для коллажа
        if img.mode != 'RGBA':
            try:
                log.info(f"Converting image to RGBA mode")
                rgba_img = img.convert('RGBA')
                if rgba_img is not img:
                    img.close()
                img = rgba_img
                log.info(f"Converted to RGBA mode")
            except Exception as e:
                log.error(f"Failed to convert to RGBA: {e}")
                log.error(traceback.format_exc())
                if img:
                    img.close()
                return None
        
        # Сериализуем изображение для передачи назад основному процессу
        try:
            log.info(f"Serializing processed image for transfer")
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            img_bytes = buffer.getvalue()
            buffer.close()
            
            # Освобождаем ресурсы
            if img:
                img.close()
                
            elapsed_time = time.time() - start_time
            log.info(f"Process {os.getpid()} completed collage image {i+1} in {elapsed_time:.2f} seconds, image size: {len(img_bytes)} bytes")
            
            # Возвращаем сериализованное изображение
            return img_bytes
            
        except Exception as e:
            log.error(f"Error serializing processed image: {e}")
            log.error(traceback.format_exc())
            if img:
                img.close()
            return None
            
    except Exception as e:
        log.error(f"Error in mp_process_collage_image: {e}")
        log.error(traceback.format_exc())
        return None