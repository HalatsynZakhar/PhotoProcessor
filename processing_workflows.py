# processing_workflows.py
import time
import os
import shutil
import math
import logging
import traceback
import gc # Для сборки мусора при MemoryError
from typing import Dict, Any, Optional, Tuple, List, Union
import uuid
import re
from datetime import datetime, timedelta
import random
import multiprocessing
import sys

# Используем абсолютный импорт (если все файлы в одной папке)
import image_utils
import config_manager # Может понадобиться для дефолтных значений в редких случаях

"""
Унифицированная архитектура обработки изображений PhotoProcessor:

Модуль реализует единый конвейер обработки изображений для всех режимов работы:
1. Обработка отдельных изображений (run_individual_processing)
2. Создание коллажей (run_collage_processing)
3. Объединение с шаблоном (в run_individual_processing)

Базовый конвейер обработки (process_image_base) применяет 5 основных шагов:
1. Изменение размера (preresize)
2. Отбеливание (whitening)
3. Удаление фона и обрезка (background crop)
4. Добавление отступов (padding)
5. Яркость и контрастность (brightness/contrast)

После базовой обработки каждый режим выполняет специфические операции:
- Индивидуальная обработка: объединение с шаблоном, изменение соотношения сторон, 
  установка максимальных размеров, применение точного холста
- Коллаж: размещение изображений на общем холсте, масштабирование, промежутки,
  установка соотношения сторон коллажа, применение точного холста

Все этапы обработки используют общий словарь метаданных для передачи информации
между шагами (например, о состоянии периметра изображения), что позволяет 
избежать повторных проверок и повысить производительность.
"""

try:
    from natsort import natsorted, ns
except ImportError:
    natsorted = None
    ns = None
    log.warning("natsort module not found, natural sorting will be disabled")

from PIL import Image, UnidentifiedImageError, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True

log = logging.getLogger(__name__) # Используем логгер, настроенный в app.py


# ==============================================================================
# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ОБРАБОТКИ ИЗОБРАЖЕНИЙ (ШАГИ КОНВЕЙЕРА) ===
# ==============================================================================
# Эти функции инкапсулируют отдельные шаги обработки, вызываемые из
# run_individual_processing или _process_image_for_collage.

def _apply_preresize(img, preresize_width, preresize_height):
    """(Helper) Применяет предварительное уменьшение размера, сохраняя пропорции."""
    if not img or (preresize_width <= 0 and preresize_height <= 0):
        return img
    # ... (код функции _apply_preresize из предыдущего ответа, с log.*) ...
    prw = preresize_width if preresize_width > 0 else float('inf')
    prh = preresize_height if preresize_height > 0 else float('inf')
    ow, oh = img.size
    if ow <= 0 or oh <= 0 or (ow <= prw and oh <= prh):
        return img
    ratio = 1.0
    if ow > prw: ratio = min(ratio, prw / ow)
    if oh > prh: ratio = min(ratio, prh / oh)
    if ratio >= 1.0: return img
    nw = max(1, int(round(ow * ratio)))
    nh = max(1, int(round(oh * ratio)))
    log.info(f"  > Pre-resizing image from {ow}x{oh} to {nw}x{nh}")
    resized_img = None
    try:
        resized_img = img.resize((nw, nh), Image.Resampling.LANCZOS)
        if resized_img is not img: image_utils.safe_close(img)
        return resized_img
    except Exception as e:
        log.error(f"  ! Error during pre-resize to {nw}x{nh}: {e}")
        image_utils.safe_close(resized_img)
        return img

def _apply_force_aspect_ratio(img, aspect_ratio_tuple):
    """(Helper) Вписывает изображение в холст с заданным соотношением сторон."""
    if not img or not aspect_ratio_tuple: return img
    # ... (код функции _apply_force_aspect_ratio из предыдущего ответа, с log.*) ...
    if not (isinstance(aspect_ratio_tuple, (tuple, list)) and len(aspect_ratio_tuple) == 2): return img
    try:
        target_w_ratio, target_h_ratio = map(float, aspect_ratio_tuple)
        if target_w_ratio <= 0 or target_h_ratio <= 0: return img
    except (ValueError, TypeError): return img
    current_w, current_h = img.size
    if current_w <= 0 or current_h <= 0: return img
    target_aspect = target_w_ratio / target_h_ratio
    current_aspect = current_w / current_h
    if abs(current_aspect - target_aspect) < 0.001: return img
    log.info(f"  > Applying force aspect ratio {target_w_ratio}:{target_h_ratio}")
    if current_aspect > target_aspect: canvas_w, canvas_h = current_w, max(1, int(round(current_w / target_aspect)))
    else: canvas_w, canvas_h = max(1, int(round(current_h * target_aspect))), current_h
    canvas = None; img_rgba = None
    try:
        canvas = Image.new('RGBA', (canvas_w, canvas_h), (0, 0, 0, 0))
        paste_x, paste_y = (canvas_w - current_w) // 2, (canvas_h - current_h) // 2
        img_rgba = img if img.mode == 'RGBA' else img.convert('RGBA')
        canvas.paste(img_rgba, (paste_x, paste_y), mask=img_rgba)
        if img_rgba is not img: image_utils.safe_close(img_rgba)
        image_utils.safe_close(img)
        log.debug(f"    New size after aspect ratio: {canvas.size}")
        return canvas
    except Exception as e:
        log.error(f"  ! Error applying force aspect ratio: {e}")
        image_utils.safe_close(canvas)
        if img_rgba is not img: image_utils.safe_close(img_rgba)
        return img

def _apply_max_dimensions(img, max_width, max_height):
    """(Helper) Уменьшает изображение, если оно больше максимальных размеров."""
    if not img or (max_width <= 0 and max_height <= 0): return img
    # ... (код функции _apply_max_dimensions из предыдущего ответа, с log.*) ...
    max_w = max_width if max_width > 0 else float('inf')
    max_h = max_height if max_height > 0 else float('inf')
    ow, oh = img.size
    if ow <= 0 or oh <= 0 or (ow <= max_w and oh <= max_h): return img
    ratio = 1.0
    if ow > max_w: ratio = min(ratio, max_w / ow)
    if oh > max_h: ratio = min(ratio, max_h / oh)
    if ratio >= 1.0: return img
    nw, nh = max(1, int(round(ow * ratio))), max(1, int(round(oh * ratio)))
    log.info(f"  > Resizing to fit max dimensions: {ow}x{oh} -> {nw}x{nh}")
    resized_img = None
    try:
        resized_img = img.resize((nw, nh), Image.Resampling.LANCZOS)
        if resized_img is not img: image_utils.safe_close(img)
        return resized_img
    except Exception as e:
        log.error(f"  ! Error during max dimensions resize to {nw}x{nh}: {e}")
        image_utils.safe_close(resized_img)
        return img

def _apply_final_canvas_or_prepare(img, exact_width, exact_height, output_format, jpg_background_color):
    """(Helper) Применяет холст точного размера ИЛИ подготавливает режим для сохранения."""
    if not img: return None
    # ... (код функции _apply_final_canvas_or_prepare из предыдущего ответа, с log.*) ...
    ow, oh = img.size
    if ow <= 0 or oh <= 0: log.error("! Cannot process zero-size image for final step."); return None
    perform_final_canvas = exact_width > 0 and exact_height > 0
    if perform_final_canvas:
        log.info(f"  > Applying final canvas {exact_width}x{exact_height}")
        target_w, target_h = exact_width, exact_height
        final_canvas = None; resized_content = None; img_rgba_content = None; content_to_paste = None
        try:
            ratio = min(target_w / ow, target_h / oh) if ow > 0 and oh > 0 else 1.0
            content_nw, content_nh = max(1, int(round(ow * ratio))), max(1, int(round(oh * ratio)))
            resized_content = img.resize((content_nw, content_nh), Image.Resampling.LANCZOS)
            target_mode = 'RGBA' if output_format == 'png' else 'RGB'
            bg_color = (0, 0, 0, 0) if target_mode == 'RGBA' else tuple(jpg_background_color)
            final_canvas = Image.new(target_mode, (target_w, target_h), bg_color)
            paste_x, paste_y = (target_w - content_nw) // 2, (target_h - content_nh) // 2
            paste_mask = None; content_to_paste = resized_content
            if resized_content.mode in ('RGBA', 'LA', 'PA'):
                 img_rgba_content = resized_content.convert('RGBA')
                 paste_mask = img_rgba_content
                 content_to_paste = img_rgba_content.convert('RGB') if target_mode == 'RGB' else img_rgba_content
            elif target_mode == 'RGB' and resized_content.mode != 'RGB': content_to_paste = resized_content.convert('RGB')
            elif target_mode == 'RGBA' and resized_content.mode != 'RGBA': content_to_paste = resized_content.convert('RGBA')
            final_canvas.paste(content_to_paste, (paste_x, paste_y), mask=paste_mask)
            log.debug(f"    Final canvas created. Size: {final_canvas.size}, Mode: {final_canvas.mode}")
            image_utils.safe_close(img); image_utils.safe_close(resized_content)
            if img_rgba_content and img_rgba_content is not content_to_paste: image_utils.safe_close(img_rgba_content)
            if content_to_paste is not resized_content: image_utils.safe_close(content_to_paste)
            return final_canvas
        except Exception as e:
            log.error(f"  ! Error applying final canvas: {e}")
            image_utils.safe_close(final_canvas); image_utils.safe_close(resized_content)
            if img_rgba_content and img_rgba_content is not content_to_paste: image_utils.safe_close(img_rgba_content)
            if content_to_paste is not resized_content: image_utils.safe_close(content_to_paste)
            return img
    else: # Prepare mode for saving without final canvas
        log.debug("  > Final canvas disabled. Preparing mode for saving.")
        target_mode = 'RGBA' if output_format == 'png' else 'RGB'
        if img.mode == target_mode: log.debug(f"    Image already in target mode {target_mode}."); return img
        elif target_mode == 'RGBA':
            converted_img = None
            try: log.debug(f"    Converting {img.mode} -> RGBA"); converted_img = img.convert('RGBA'); image_utils.safe_close(img); return converted_img
            except Exception as e: log.error(f"    ! Failed to convert to RGBA: {e}"); image_utils.safe_close(converted_img); return img
        else: # target_mode == 'RGB'
            rgb_image = None; temp_rgba = None; image_to_paste = img; paste_mask = None
            try:
                log.info(f"    Preparing {img.mode} -> RGB with background {jpg_background_color}.")
                rgb_image = Image.new("RGB", img.size, tuple(jpg_background_color))
                if img.mode in ('RGBA', 'LA'): paste_mask = img
                elif img.mode == 'PA': temp_rgba = img.convert('RGBA'); paste_mask = temp_rgba; image_to_paste = temp_rgba
                rgb_image.paste(image_to_paste, (0, 0), mask=paste_mask)
                if temp_rgba is not img: image_utils.safe_close(temp_rgba)
                image_utils.safe_close(img)
                log.debug(f"    Prepared RGB image. Size: {rgb_image.size}")
                return rgb_image
            except Exception as e:
                log.error(f"    ! Failed preparing RGB background: {e}. Trying simple convert.")
                image_utils.safe_close(rgb_image); image_utils.safe_close(temp_rgba)
                converted_img = None
                try: log.debug("    Attempting simple RGB conversion as fallback."); converted_img = img.convert('RGB'); image_utils.safe_close(img); return converted_img
                except Exception as e_conv: log.error(f"    ! Simple RGB conversion failed: {e_conv}"); image_utils.safe_close(converted_img); return img


def _save_image(img, output_file_path, output_format, jpeg_quality=95, 
                  jpg_background_color=None, png_transparent_background=False,
                  png_background_color=None, remove_metadata=False):
    """
    Сохраняет изображение в заданном формате.
    
    Args:
        img: Исходное изображение
        output_file_path: Путь для сохранения
        output_format: Формат сохранения (jpg, png, ...)
        jpeg_quality: Качество для JPEG
        jpg_background_color: Цвет фона для JPG
        png_transparent_background: Использовать ли прозрачность в PNG
        png_background_color: Цвет фона для PNG (если не прозрачный)
        remove_metadata: Удалять ли метаданные
        
    Returns:
        bool: Успешно ли выполнено сохранение
    """
    try:
        # Проверяем наличие директории для сохранения
        os.makedirs(os.path.dirname(os.path.abspath(output_file_path)), exist_ok=True)
        
        save_options = {}
        img_to_save = None
        
        # Определяем и обрабатываем сохранение в зависимости от формата
        if output_format.lower() in ['jpg', 'jpeg']:
            save_options['format'] = 'JPEG'
            save_options['quality'] = jpeg_quality
            save_options['optimize'] = True
            
            # Ensure color is a valid tuple or int for Image.new
            default_bg_color = (255, 255, 255)
            bg_color = default_bg_color
            
            if jpg_background_color is not None:
                if isinstance(jpg_background_color, (tuple, list)) and len(jpg_background_color) == 3:
                    bg_color = tuple(map(int, jpg_background_color))
                elif isinstance(jpg_background_color, int):
                    bg_color = jpg_background_color
                else:
                    log.warning(f"Invalid jpg_background_color format: {jpg_background_color}, using default white")
            
            # Для JPEG не может быть прозрачности, поэтому преобразуем в RGB
            if img.mode == 'RGBA':
                # Применяем цвет фона
                background = Image.new('RGB', img.size, bg_color)
                background.paste(img, (0, 0), img)
                img_to_save = background
            elif img.mode != 'RGB':
                img_to_save = img.convert('RGB')
            else:
                img_to_save = img.copy()
        
        elif output_format.lower() == 'png':
            save_options['format'] = 'PNG'
            save_options['optimize'] = True
            
            # Ensure color is a valid tuple or int for Image.new
            default_bg_color = (255, 255, 255)
            png_bg_color = default_bg_color
            
            if png_background_color is not None:
                if isinstance(png_background_color, (tuple, list)) and len(png_background_color) == 3:
                    png_bg_color = tuple(map(int, png_background_color))
                elif isinstance(png_background_color, int):
                    png_bg_color = png_background_color
                else:
                    log.warning(f"Invalid png_background_color format: {png_background_color}, using default white")
            
            # Специальная обработка для предотвращения ореолов в PNG
            if png_transparent_background:
                if img.mode == 'RGBA':
                    # Делаем строго бинарную прозрачность
                    r, g, b, alpha = img.split()
                    # Используем очень высокий порог для предотвращения ореолов
                    threshold = 245
                    binary_alpha = alpha.point(lambda x: 255 if x >= threshold else 0)
                    img_to_save = Image.merge('RGBA', (r, g, b, binary_alpha))
                elif 'transparency_mask' in img.info:
                    # Если есть сохраненная маска, применяем ее
                    mask = img.info['transparency_mask']
                    rgba_img = img.convert("RGBA")
                    r, g, b, _ = rgba_img.split()
                    # Создаем бинарную маску
                    binary_mask = mask.point(lambda x: 0 if x == 0 else 255)
                    final_alpha = binary_mask.point(lambda x: 0 if x > 0 else 255)  # Инвертируем
                    img_to_save = Image.merge("RGBA", (r, g, b, final_alpha))
            else:
                # Для PNG без прозрачности
                if img.mode == 'RGBA':
                    # Создаем RGB с белым фоном
                    background = Image.new('RGB', img.size, png_bg_color)
                    
                    # Создаем бинарную маску для четких границ
                    r, g, b, alpha = img.split()
                    binary_alpha = alpha.point(lambda x: 255 if x >= 245 else 0)
                    clean_img = Image.merge('RGBA', (r, g, b, binary_alpha))
                    
                    # Вставляем с бинарной маской
                    background.paste(clean_img, (0, 0), clean_img)
                    img_to_save = background
                    image_utils.safe_close(clean_img)
                else:
                    img_to_save = img.copy()
                    
                # Принудительно устанавливаем RGB для режима без прозрачности
                if img_to_save.mode != 'RGB':
                    tmp = img_to_save.convert('RGB')
                    image_utils.safe_close(img_to_save)
                    img_to_save = tmp
        
        else:  # GIF, BMP и т.д.
            save_options['format'] = output_format.upper()
            img_to_save = img.copy()
            
        # Удаляем метаданные, если нужно
        if remove_metadata and hasattr(img_to_save, 'info') and img_to_save.info:
            # Полностью очищаем все метаданные
            preserved_info = {}
            
            # Сохраняем только маску прозрачности для PNG, если включена прозрачность
            if output_format.lower() == 'png' and png_transparent_background:
                if 'transparency_mask' in img_to_save.info:
                    preserved_info['transparency_mask'] = img_to_save.info['transparency_mask']
                    
            # Устанавливаем новый словарь info с минимальными или нулевыми данными    
            img_to_save.info = preserved_info
            
            # Удаляем EXIF и другие метаданные
            if hasattr(img_to_save, 'getexif'):
                try:
                    # Для PIL >= 7.0.0
                    exif = img_to_save.getexif()
                    if exif:
                        exif.clear()
                except Exception as e:
                    log.debug(f"No EXIF data or error clearing EXIF: {e}")
                    
            log.info(f"Metadata removed from output file")
            
        # Сохраняем изображение
        img_to_save.save(output_file_path, **save_options)
        
        # Закрываем созданную копию
        if img_to_save is not img:
            image_utils.safe_close(img_to_save)
            
        log.info(f"Успешно сохранено изображение в {output_file_path}")
        return True
        
    except Exception as e:
        log.error(f"Ошибка при сохранении {output_file_path}: {e}")
        if img_to_save is not None and img_to_save is not img:
            image_utils.safe_close(img_to_save)
        return False

# ==============================================================================
# === ОСНОВНАЯ ФУНКЦИЯ: ОБРАБОТКА ОТДЕЛЬНЫХ ФАЙЛОВ =============================
# ==============================================================================

def _create_backup(input_folder: str, template_path: str, backup_folder_base: str) -> bool:
    """
    Создает резервную копию исходных файлов и шаблона перед началом обработки.
    Создает подпапку с датой и временем внутри backup_folder_base.
    
    Args:
        input_folder: Путь к папке с исходными файлами.
        template_path: Путь к файлу шаблона (может быть пустым).
        backup_folder_base: Путь к основной папке для резервных копий.
        
    Returns:
        bool: True если резервное копирование выполнено успешно (или не требовалось),
              False в случае ошибки при копировании.
    """
    if not backup_folder_base:
        log.info("Backup folder not specified, skipping backup.")
        return True # Считаем успехом, т.к. бэкап не требовался
        
    try:
        # 1. Создаем основную папку для резервных копий, если она не существует
        if not os.path.exists(backup_folder_base):
            os.makedirs(backup_folder_base)
            log.info(f"Created main backup folder: {backup_folder_base}")
            
        # 2. Создаем подпапку с датой и временем
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dated_backup_folder = os.path.join(backup_folder_base, f"backup_{timestamp}")
        os.makedirs(dated_backup_folder)
        log.info(f"Created dated backup folder: {dated_backup_folder}")
        
        files_backed_up_count = 0
        template_backed_up = False
        
        # 3. Копируем шаблон, если он указан и существует
        if template_path and os.path.isfile(template_path):
            try:
                template_filename = os.path.basename(template_path)
                template_backup_path = os.path.join(dated_backup_folder, template_filename)
                shutil.copy2(template_path, template_backup_path)
                log.info(f"Template backed up to: {template_backup_path}")
                template_backed_up = True
            except Exception as e_tmpl:
                log.error(f"Failed to back up template '{template_path}': {e_tmpl}")
                # Не прерываем процесс, но логируем ошибку
        
        # 4. Копируем все файлы из входной папки, если она указана и существует
        if input_folder and os.path.isdir(input_folder):
            # Создаем подпапку для исходных файлов внутри папки с датой
            source_files_backup_dir = os.path.join(dated_backup_folder, "source_files")
            os.makedirs(source_files_backup_dir)
            log.debug(f"Created source files backup subfolder: {source_files_backup_dir}")
            
            # Получаем список файлов до начала обработки
            image_files = get_image_files(input_folder)
            
            if not image_files:
                log.warning(f"No image files found in input folder for backup: {input_folder}")
            else:
                log.info(f"Starting backup of {len(image_files)} source files...")
                for file_path in image_files:
                    try:
                        filename = os.path.basename(file_path)
                        dest_path = os.path.join(source_files_backup_dir, filename)
                        shutil.copy2(file_path, dest_path) # copy2 сохраняет метаданные
                        files_backed_up_count += 1
                        log.debug(f"Backed up: {filename}")
                    except Exception as e_file:
                        log.error(f"Failed to back up source file '{filename}': {e_file}")
                        # Продолжаем копировать остальные файлы
                log.info(f"Finished backup. Successfully backed up {files_backed_up_count}/{len(image_files)} source files to {source_files_backup_dir}")
        else:
            log.warning(f"Input folder '{input_folder}' not found or not a directory. Skipping source files backup.")

        # Считаем общий успех, если хотя бы что-то скопировалось или папка создана
        return True
        
    except Exception as e:
        log.error(f"Critical error during backup process to '{backup_folder_base}': {e}")
        log.exception("Backup error details")
        return False # Критическая ошибка при создании папок и т.д.

def run_individual_processing(**all_settings: Dict[str, Any]) -> bool:
    """Обрабатывает отдельные файлы согласно настройкам."""
    try:
        log.info("--- Starting Individual File Processing ---")
        
        # Проверяем настройки многопроцессорной обработки
        performance_settings = all_settings.get('performance', {})
        enable_multiprocessing = performance_settings.get('enable_multiprocessing', False)
        max_workers = performance_settings.get('max_workers', None)
        
        # Выводим подробную информацию о производительности
        log.info("Performance settings:")
        log.info(f"  enable_multiprocessing: {enable_multiprocessing}")
        log.info(f"  max_workers: {max_workers}")
        log.info(f"  CPU count: {multiprocessing.cpu_count()}")
        log.info(f"  Platform: {sys.platform}")
        
        if enable_multiprocessing:
            log.info(f"Multiprocessing enabled with {max_workers if max_workers else 'auto'} workers")
            # Импортируем модуль многопроцессорной обработки
            try:
                import multiprocessing_utils
                # Инициализируем многопроцессорную обработку
                multiprocessing_utils.enable_multiprocessing()
                log.info(f"Initialized multiprocessing, CPU count: {multiprocessing.cpu_count()}")
                
                # Дополнительная проверка метода запуска процессов
                try:
                    method = multiprocessing.get_start_method()
                    log.info(f"Current process start method: {method}")
                except Exception as m_err:
                    log.warning(f"Unable to get start method: {m_err}")
            except ImportError:
                log.warning("Failed to import multiprocessing_utils. Falling back to single-process mode")
                enable_multiprocessing = False
        else:
            log.info("Multiprocessing disabled. Using single-process mode")
        
        # Extract merge settings
        merge_settings = all_settings.get('merge_settings', {})
        enable_merge = merge_settings.get('enable_merge', False)
        template_path = merge_settings.get('template_path', '')
        # Remove any quotes from template path
        if template_path:
            template_path = template_path.strip('"\'')
            template_path = os.path.normpath(template_path)
            # Update the template path in merge_settings
            merge_settings['template_path'] = template_path
        process_template = merge_settings.get('process_template', False)
        
        # Extract other settings
        input_folder = all_settings.get('paths', {}).get('input_folder_path', '')
        output_folder = all_settings.get('paths', {}).get('output_folder_path', '')
        backup_folder = all_settings.get('paths', {}).get('backup_folder_path', '')
        
        # Проверяем, совпадают ли папки ввода и вывода
        same_input_output = False
        if input_folder and output_folder:
            if os.path.normcase(os.path.abspath(input_folder)) == os.path.normcase(os.path.abspath(output_folder)):
                same_input_output = True
                log.warning("Input and output folders are the same. Special processing will be applied.")
        
        # --- Создаем резервную копию ПЕРЕД началом обработки ---
        if backup_folder:
            log.info(f"Creating backup in: {backup_folder}")
            backup_successful = _create_backup(input_folder, template_path, backup_folder)
            if not backup_successful:
                log.error("Backup failed! Processing aborted to prevent data loss.")
                return False # Прерываем обработку, если бэкап не удался
            log.info("Backup completed successfully")
        else:
            log.warning("Backup folder not specified, skipping backup")
            backup_successful = True
        # --------------------------------------------------------
        
        # Extract processing settings
        preprocessing_settings = all_settings.get('preprocessing', {})
        whitening_settings = all_settings.get('whitening', {})
        background_crop_settings = all_settings.get('background_crop', {})
        padding_settings = all_settings.get('padding', {})
        brightness_contrast_settings = all_settings.get('brightness_contrast', {})
        individual_mode_settings = all_settings.get('individual_mode', {})
        
        # Log settings
        log.info("--- Processing Parameters (Individual Mode) ---")
        log.info(f"Input Path: {input_folder}")
        log.info(f"Output Path: {output_folder}")
        log.info(f"Backup Path: {backup_folder}")
        log.info(f"Backup Status: {'Successful' if backup_successful else ('Skipped' if not backup_folder else 'Failed')}")
        log.info(f"Article (Renaming): {'Enabled' if individual_mode_settings.get('enable_rename', False) else 'Disabled'}")
        log.info(f"Delete Originals: {individual_mode_settings.get('delete_originals', False)}")
        log.info(f"Same Input/Output: {same_input_output}")
        
        # Output format settings
        output_format = individual_mode_settings.get('output_format', 'jpg').lower()
        log.info(f"Output Format: {output_format.upper()}")
        if output_format == 'jpg':
            jpg_background_color = individual_mode_settings.get('jpg_background_color', [255, 255, 255])
            jpeg_quality = individual_mode_settings.get('jpeg_quality', 95)
            log.info(f"  JPG Bg: {tuple(jpg_background_color)}, Quality: {jpeg_quality}")
        
        # Log processing steps
        log.info("---------- Steps ----------")
        log.info(f"1. Preresize: {'Enabled' if preprocessing_settings.get('enable_preresize', False) else 'Disabled'} "
                f"(W:{preprocessing_settings.get('preresize_width', 0)}, H:{preprocessing_settings.get('preresize_height', 0)})")
        log.info(f"2. Whitening: {'Enabled' if whitening_settings.get('enable_whitening', False) else 'Disabled'} "
                f"(Thresh:{whitening_settings.get('whitening_cancel_threshold', 765)})")
        log.info(f"3. BG Removal/Crop: {'Enabled' if background_crop_settings.get('enable_bg_crop', False) else 'Disabled'} "
                f"(Tol:{background_crop_settings.get('white_tolerance', 10)})")
        log.info(f"  Crop: {'Enabled' if background_crop_settings.get('enable_crop', True) else 'Disabled'}, "
                 f"Symmetry: Abs={background_crop_settings.get('force_absolute_symmetry', False)}, "
                 f"Axes={background_crop_settings.get('force_axes_symmetry', False)}, "
                 f"Check Perimeter={background_crop_settings.get('check_perimeter', False)}, "
                 f"Mask instead transparency={background_crop_settings.get('use_mask_instead_of_transparency', False)}")
        log.info(f"4. Padding: {'Enabled' if padding_settings.get('mode', 'never') != 'never' else 'Disabled'}")
        log.info(f"5. Brightness/Contrast: {'Enabled' if brightness_contrast_settings.get('enable_bc', False) else 'Disabled'}")
        log.info(f"6. Force Aspect Ratio: {'Enabled' if individual_mode_settings.get('enable_force_aspect_ratio', False) else 'Disabled'} ")
        log.info(f"7. Max Dimensions: W:{individual_mode_settings.get('max_output_width', 'N/A')}, "
                f"H:{individual_mode_settings.get('max_output_height', 'N/A')}")
        log.info(f"8. Final Exact Canvas: W:{individual_mode_settings.get('final_exact_width', 'N/A')}, "
                f"H:{individual_mode_settings.get('final_exact_height', 'N/A')}")
        log.info("-------------------------")

        # Get list of files to process
        files = get_image_files(input_folder)
        log.info(f"Found {len(files)} files to process.")
        
        # Track overall success
        overall_success = True
        
        # Список для хранения выходных имен файлов, чтобы не удалять их
        processed_output_files = []
        
        # Список для хранения путей к обработанным файлам, используется для установки дат
        processed_file_paths = []
        
        # Pre-process template if merge is enabled
        processed_template = None
        if enable_merge and template_path:
            try:
                log.info("--- Pre-processing template ---")
                # Load template
                if template_path.lower().endswith('.psd'):                    
                    try:
                        from psd_tools import PSDImage
                        psd = PSDImage.open(template_path)
                        # Convert PSD to PIL Image using the correct method
                        processed_template = psd.topil()
                        if processed_template.mode == 'CMYK':
                            processed_template = processed_template.convert('RGB')
                        log.info(f"Loaded PSD template: {os.path.basename(template_path)}")
                    except ImportError:
                        log.error("psd_tools library not installed. Cannot process PSD files.")
                        return False
                else:
                    try:
                        processed_template = Image.open(template_path)
                        processed_template.load()
                        log.info(f"Loaded image template: {os.path.basename(template_path)}")
                    except Exception as e:
                        log.error(f"Failed to load template image: {e}")
                        return False
                
                if process_template:
                    # Применяем базовый конвейер обработки к шаблону
                    processed_template, template_metadata = process_image_base(
                        processed_template,
                        preprocessing_settings,
                        whitening_settings,
                        background_crop_settings,
                        padding_settings,
                        brightness_contrast_settings
                    )
                    
                    if processed_template is None:
                        log.error("Template processing failed")
                        return False
                
                log.info(f"Template pre-processing completed. Size: {processed_template.size}, Mode: {processed_template.mode}")
            except Exception as e:
                log.error(f"Error pre-processing template: {e}")
                return False
        
        # Если включена многопроцессорная обработка и все подготовительные шаги выполнены успешно
        if enable_multiprocessing and files:
            try:
                log.info("Starting multiprocessing processing mode")
                
                # Подготовка настроек для передачи в процессы
                process_settings = {
                    'preprocessing_settings': preprocessing_settings,
                    'whitening_settings': whitening_settings,
                    'background_crop_settings': background_crop_settings,
                    'padding_settings': padding_settings,
                    'brightness_contrast_settings': brightness_contrast_settings,
                    'individual_mode_settings': individual_mode_settings, 
                    'merge_settings': merge_settings,
                    'paths': {
                        'output_folder_path': output_folder
                    },
                    'same_input_output': same_input_output
                }
                
                # Если включено объединение с шаблоном, сериализуем его
                if enable_merge and processed_template is not None:
                    # Сохраняем шаблон как байты
                    import io
                    template_buffer = io.BytesIO()
                    processed_template.save(template_buffer, format='PNG')
                    process_settings['template_image_bytes'] = template_buffer.getvalue()
                    template_buffer.close()
                
                # Вызываем многопроцессорную обработку
                import multiprocessing_utils
                results = multiprocessing_utils.process_images_with_multiprocessing(
                    files,
                    multiprocessing_utils.mp_process_individual_file,
                    process_settings,
                    max_workers
                )
                
                successful_files = []  # Добавляем определение переменной, которая не была объявлена
                
                # Обрабатываем результаты
                for i, result in enumerate(results):
                    if result and isinstance(result, str) and os.path.exists(result):
                        filename = os.path.basename(files[i])
                        log.info(f"Successfully processed file {i+1}/{len(files)}: {filename}")
                        successful_files.append(filename)
                        processed_file_paths.append(result)
                        processed_output_files.append(result)
                    else:
                        log.error(f"Failed to process file {i+1}/{len(files)}: {os.path.basename(files[i])}")
                        overall_success = False
                
                # Если были ошибки, отмечаем это
                num_processed = len(successful_files)
                if num_processed < len(files):
                    overall_success = False
                    log.warning(f"Failed to process {len(files) - num_processed} files")
                
                log.info(f"Multiprocessing completed. Processed {num_processed}/{len(files)} files.")
                
                # Очищаем ресурсы
                if processed_template:
                    processed_template.close()
                
                # Устанавливаем даты создания файлов, если нужно
                if individual_mode_settings.get('preserve_file_dates', False) and processed_file_paths:
                    try:
                        log.info("Trying to preserve file dates...")
                        # Код для сохранения дат файлов...
                    except Exception as date_err:
                        log.error(f"Failed to preserve file dates: {date_err}")
                
                # Удаляем оригиналы при необходимости
                if individual_mode_settings.get('delete_originals', False) and not same_input_output:
                    try:
                        log.info("Deleting original files...")
                        for file_path in files:
                            # Проверяем, не является ли этот файл выходным
                            if file_path not in processed_output_files:
                                try:
                                    os.remove(file_path)
                                    log.debug(f"Deleted original: {file_path}")
                                except Exception as del_err:
                                    log.warning(f"Failed to delete original {file_path}: {del_err}")
                    except Exception as del_all_err:
                        log.error(f"Error during delete originals: {del_all_err}")
                
                # Возвращаем результат обработки
                return overall_success
                
            except Exception as mp_err:
                log.error(f"Error during multiprocessing: {mp_err}")
                log.exception("Multiprocessing details")
                log.warning("Falling back to single-process mode")
                # Продолжаем выполнение в однопоточном режиме
        
        # Process each file (однопоточный режим)
        for i, file_path in enumerate(files, 1):
            try:
                filename = os.path.basename(file_path)
                log.info(f"--- [{i}/{len(files)}] Processing: {filename} ---")
                
                # Используем базовый конвейер обработки для всех основных шагов
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
                    log.info(f"--- Finished processing: {filename} (Failed) ---")
                    overall_success = False
                    continue
                
                # Apply merge with template if enabled
                if enable_merge and processed_template is not None:
                    try:
                        # Add jpg_background_color to merge_settings
                        merge_settings['jpg_background_color'] = individual_mode_settings.get('jpg_background_color', [255, 255, 255])
                        
                        # Merge image with template
                        img = _merge_with_template(img, processed_template, merge_settings)
                        if img is None:
                            log.error(f"Failed to merge with template for {filename}")
                            log.info(f"--- Finished processing: {filename} (Failed) ---")
                            overall_success = False
                            continue
                    except Exception as e:
                        log.error(f"Error merging with template: {e}")
                        log.info(f"--- Finished processing: {filename} (Failed) ---")
                        overall_success = False
                        continue
                
                # Apply final adjustments
                if i == 1 and individual_mode_settings.get('special_first_file', False):
                    # Используем специальные настройки для первого файла
                    first_file_settings = individual_mode_settings.get('first_file_settings', {})
                    
                    if first_file_settings.get('enable_force_aspect_ratio', False):
                        img = _apply_force_aspect_ratio(img, first_file_settings.get('force_aspect_ratio', [1, 1]))
                    
                    if first_file_settings.get('enable_max_dimensions', False):
                        img = _apply_max_dimensions(img, 
                                                 first_file_settings.get('max_output_width', 0),
                                                 first_file_settings.get('max_output_height', 0))
                    
                    if first_file_settings.get('enable_exact_canvas', False):
                        img = _apply_final_canvas_or_prepare(img,
                                                           first_file_settings.get('final_exact_width', 0),
                                                           first_file_settings.get('final_exact_height', 0),
                                                           output_format,
                                                           individual_mode_settings.get('jpg_background_color', [255, 255, 255]))
                else:
                    # Используем обычные настройки для остальных файлов
                    if individual_mode_settings.get('enable_force_aspect_ratio', False):
                        img = _apply_force_aspect_ratio(img, individual_mode_settings.get('force_aspect_ratio', [1, 1]))
                    
                    if individual_mode_settings.get('enable_max_dimensions', False):
                        img = _apply_max_dimensions(img, 
                                                 individual_mode_settings.get('max_output_width', 0),
                                                 individual_mode_settings.get('max_output_height', 0))
                    
                    if individual_mode_settings.get('enable_exact_canvas', False):
                        img = _apply_final_canvas_or_prepare(img,
                                                           individual_mode_settings.get('final_exact_width', 0),
                                                           individual_mode_settings.get('final_exact_height', 0),
                                                           output_format,
                                                           individual_mode_settings.get('jpg_background_color', [255, 255, 255]))
                
                # Save processed image
                output_filename = filename
                if individual_mode_settings.get('enable_rename', False):
                    article = individual_mode_settings.get('article_name', '')
                    if article:
                        # Первый файл (i=1) получает имя просто АРТИКУЛ, остальные - АРТИКУЛ_INDEX
                        if i == 1:
                            output_filename = f"{article}.{output_format}"
                        else:
                            output_filename = f"{article}_{i-1}.{output_format}"
                else:
                    # Ensure the output filename has the correct extension
                    base_name = os.path.splitext(output_filename)[0]
                    output_filename = f"{base_name}.{output_format}"
                
                output_path = os.path.join(output_folder, output_filename)
                log.info(f"Saving image with format: {output_format.upper()}")
                if not _save_image(img, output_path, output_format, 
                                 individual_mode_settings.get('jpeg_quality', 95), 
                                 individual_mode_settings.get('jpg_background_color', [255, 255, 255]),
                                 individual_mode_settings.get('png_transparent_background', True),
                                 individual_mode_settings.get('png_background_color', [255, 255, 255]),
                                 individual_mode_settings.get('remove_metadata', False)):
                    log.error(f"Failed to save image: {output_filename}")
                    log.info(f"--- Finished processing: {filename} (Failed) ---")
                    overall_success = False
                    continue
                
                # Сохраняем имя выходного файла для проверки при удалении оригиналов
                processed_output_files.append(output_filename)
                log.info(f"Added to preserved files list: {output_filename}")
                
                # Сохраняем полный путь к обработанному файлу для установки даты
                processed_file_paths.append(output_path)
                
                # Откладываем удаление оригиналов до конца обработки всех файлов
                # Это предотвратит удаление файлов, которые соответствуют выходным именам
                
                log.info(f"--- Finished processing: {filename} (Success) ---")
                
            except Exception as e:
                log.error(f"Error processing file {filename}: {e}")
                log.exception("Error details")
                overall_success = False
                continue
        
        # Установка единой даты для файлов, если нужно
        if individual_mode_settings.get('remove_metadata', False) and processed_file_paths:
            try:
                log.info("Setting unified file dates...")
                # Получаем текущее время как базовое
                base_time = time.time()
                # Получаем настройку обратного порядка дат
                reverse_order = individual_mode_settings.get('reverse_date_order', False)
                
                # Упорядочиваем файлы
                if reverse_order:
                    log.info("Using reverse date order (newest to oldest)")
                    # В обратном порядке: первый файл самый новый, каждый следующий старше на 2 секунды
                    files_with_times = [(f, base_time - i * 2) for i, f in enumerate(processed_file_paths)]
                else:
                    log.info("Using normal date order (oldest to newest)")
                    # В прямом порядке: первый файл самый старый, каждый следующий новее на 2 секунды
                    files_with_times = [(f, base_time + i * 2) for i, f in enumerate(processed_file_paths)]
                
                # Применяем даты к файлам
                for file_path, file_time in files_with_times:
                    try:
                        # Устанавливаем время доступа и модификации
                        os.utime(file_path, (file_time, file_time))
                        log.debug(f"Set date for file: {os.path.basename(file_path)} to {time.ctime(file_time)}")
                    except Exception as e:
                        log.error(f"Failed to set date for {file_path}: {e}")
                
                log.info(f"Successfully set dates for {len(processed_file_paths)} files")
            except Exception as e:
                log.error(f"Error setting file dates: {e}")
                log.exception("Date setting error details")
                # Продолжаем выполнение, даже если установка дат не удалась
                
        # Теперь, когда все файлы обработаны, мы можем безопасно удалить оригиналы (если нужно)
        if individual_mode_settings.get('delete_originals', False) and overall_success:
            log.info("Processing completed. Now cleaning up original files...")
            
            for file_path in files:
                orig_filename = os.path.basename(file_path)
                
                # Проверка: не является ли оригинал одним из выходных файлов
                if same_input_output and orig_filename in processed_output_files:
                    log.info(f"Skipping deletion of original file that matches output: {orig_filename}")
                    continue
                
                try:
                    os.remove(file_path)
                    log.info(f"Original deleted: {orig_filename}")
                except Exception as e:
                    log.error(f"Failed to delete original: {e}")
        
        # Clean up
        if processed_template:
            processed_template.close()
        
        return overall_success
        
    except Exception as e:
        log.critical(f"!!! UNEXPECTED error in run_individual_processing: {e}")
        log.exception("Error details")
        return False

# ==============================================================================
# === ОСНОВНАЯ ФУНКЦИЯ: СОЗДАНИЕ КОЛЛАЖА =======================================
# ==============================================================================

def _process_image_for_collage(image_path: str, prep_settings, white_settings, bgc_settings, pad_settings, bc_settings) -> Optional[Image.Image]:
    """
    Применяет базовые шаги обработки к одному изображению для коллажа.
    Использует общую функцию process_image_base для унификации обработки.
    """
    log.debug(f"-- Starting processing for collage: {os.path.basename(image_path)}")
    
    # Вызываем базовый конвейер обработки
    img, metadata = process_image_base(
        image_path,
        prep_settings,
        white_settings,
        bgc_settings,
        pad_settings,
        bc_settings
    )
    
    if img:
        # Убедимся, что изображение в формате RGBA для коллажа
        if img.mode != 'RGBA':
            try:
                rgba_img = img.convert('RGBA')
                image_utils.safe_close(img)
                img = rgba_img
            except Exception as e:
                log.error(f"Failed to convert image to RGBA for collage: {e}")
                return None
                
        log.debug(f"-- Successfully processed image for collage: {os.path.basename(image_path)}")
        return img
    else:
        log.error(f"-- Failed to process image for collage: {os.path.basename(image_path)}")
        return None

def _check_padding_perimeter(img, tolerance, margin=1):
    """Проверяет, является ли периметр изображения белым с учетом допуска."""
    log.debug(f"  Checking padding perimeter (Margin: {margin}px, Tolerance: {tolerance})")
    perimeter_is_white = image_utils.check_perimeter_is_white(img, tolerance, margin)
    log.debug(f"    Padding Perimeter is white: {perimeter_is_white}")
    return perimeter_is_white

def run_collage_processing(**all_settings: Dict[str, Any]) -> bool:
    """
    Создает коллаж из обработанных изображений.
    Возвращает True при успехе, False при любой ошибке или если коллаж не был создан.
    """
    log.info("====== Entered run_collage_processing function ======")
    log.info("--- Starting Collage Processing ---")
    start_time = time.time()
    success_flag = False # Флаг для финального return

    # --- 1. Извлечение и Валидация Параметров ---
    log.debug("Extracting settings for collage mode...")
    log.info("Backup is disabled for collage mode for better performance.")
    try:
        # Проверяем настройки многопроцессорной обработки
        performance_settings = all_settings.get('performance', {})
        enable_multiprocessing = performance_settings.get('enable_multiprocessing', False)
        max_workers = performance_settings.get('max_workers', None)
        
        if enable_multiprocessing:
            log.info(f"Multiprocessing enabled for collage with {max_workers if max_workers else 'auto'} workers")
            # Импортируем модуль многопроцессорной обработки
            try:
                import multiprocessing_utils
                # Инициализируем многопроцессорную обработку
                multiprocessing_utils.enable_multiprocessing()
                log.info(f"Initialized multiprocessing for collage, CPU count: {multiprocessing.cpu_count()}")
            except ImportError:
                log.warning("Failed to import multiprocessing_utils. Falling back to single-process mode")
                enable_multiprocessing = False
        else:
            log.info("Multiprocessing disabled for collage. Using single-process mode")
            
        paths_settings = all_settings.get('paths', {})
        prep_settings = all_settings.get('preprocessing', {})
        white_settings = all_settings.get('whitening', {})
        bgc_settings = all_settings.get('background_crop', {})
        pad_settings = all_settings.get('padding', {})
        bc_settings = all_settings.get('brightness_contrast', {})
        coll_settings = all_settings.get('collage_mode', {})
        merge_settings = all_settings.get('merge_settings', {})

        source_dir = paths_settings.get('input_folder_path')
        output_filename_base = paths_settings.get('output_filename') # Имя без расширения от пользователя
        backup_folder = paths_settings.get('backup_folder_path', '')
        
        # --- Резервное копирование отключено для режима коллажа ---
        # Для коллажа не создаем резервные копии, но сохраняем переменную для совместимости с остальным кодом
        backup_successful = False
        # --------------------------------------------------------
        
        proportional_placement = coll_settings.get('proportional_placement', False)
        placement_ratios = coll_settings.get('placement_ratios', [1.0])
        forced_cols = int(coll_settings.get('forced_cols', 0))
        
        # Получаем настройки отступов между фото
        enable_spacing = coll_settings.get('enable_spacing', True)
        spacing_percent = float(coll_settings.get('spacing_percent', 2.0)) if enable_spacing else 0.0
        
        # Получаем настройки внешних полей
        enable_outer_margins = coll_settings.get('enable_outer_margins', True)
        outer_margins_percent = float(coll_settings.get('outer_margins_percent', 2.0)) if enable_outer_margins else 0.0
        
        force_collage_aspect_ratio = coll_settings.get('force_collage_aspect_ratio')
        max_collage_width = int(coll_settings.get('max_collage_width', 0))
        max_collage_height = int(coll_settings.get('max_collage_height', 0))
        final_collage_exact_width = int(coll_settings.get('final_collage_exact_width', 0))
        final_collage_exact_height = int(coll_settings.get('final_collage_exact_height', 0))
        output_format = str(coll_settings.get('output_format', 'jpg')).lower()
        jpeg_quality = int(coll_settings.get('jpeg_quality', 95))
        jpg_background_color = coll_settings.get('jpg_background_color', [255, 255, 255])
        valid_jpg_bg = tuple(jpg_background_color) if isinstance(jpg_background_color, list) and len(jpg_background_color) == 3 else (255, 255, 255)
        
        # Add jpg_background_color to merge_settings
        merge_settings['jpg_background_color'] = jpg_background_color

        if not source_dir or not output_filename_base:
             raise ValueError("Source directory or output filename base missing.")
        output_filename_base = str(output_filename_base).strip() # Убираем пробелы по краям
        if not output_filename_base: raise ValueError("Output filename base cannot be empty.")
        # Формируем имя файла с расширением
        output_filename_with_ext = f"{os.path.splitext(output_filename_base)[0]}.{output_format}"

        if output_format not in ['jpg', 'png']: raise ValueError(f"Unsupported collage output format: {output_format}")

        valid_collage_aspect_ratio = tuple(force_collage_aspect_ratio) if force_collage_aspect_ratio and len(force_collage_aspect_ratio) == 2 else None

        log.debug("Collage settings extracted.")

    except (KeyError, ValueError, TypeError) as e:
        log.critical(f"Error processing collage settings: {e}. Aborting.", exc_info=True)
        return False # Возвращаем False при ошибке настроек

    # --- 2. Подготовка Путей ---
    abs_source_dir = os.path.abspath(source_dir)
    if not os.path.isdir(abs_source_dir):
        log.error(f"Source directory not found: {abs_source_dir}")
        log.info(">>> Exiting: Source directory not found.")
        return False # Возвращаем False
    # Используем имя файла с расширением для пути
    output_file_path = os.path.abspath(os.path.join(abs_source_dir, output_filename_with_ext))
    if os.path.isdir(output_file_path):
        log.error(f"Output filename points to a directory: {output_file_path}")
        log.info(">>> Exiting: Output filename is a directory.")
        return False # Возвращаем False

    # --- 3. Логирование Параметров ---
    log.info("--- Processing Parameters (Collage Mode) ---")
    log.info(f"Source Directory: {abs_source_dir}")
    # Логируем имя с расширением и путь
    log.info(f"Output Filename: {output_filename_with_ext} (Path: {output_file_path})")
    log.info(f"Backup: Disabled for collage mode")
    log.info(f"Output Format: {output_format.upper()}")
    if output_format == 'jpg': log.info(f"  JPG Bg: {valid_jpg_bg}, Quality: {jpeg_quality}")
    
    # Логируем активные настройки
    log.info(f"Spacing between images: {'Enabled' if enable_spacing else 'Disabled'}")
    if enable_spacing:
        log.info(f"  Spacing value: {spacing_percent}%")
    
    log.info(f"Outer margins: {'Enabled' if enable_outer_margins else 'Disabled'}")
    if enable_outer_margins:
        log.info(f"  Outer margins value: {outer_margins_percent}%")
    
    # ... (остальной код функции без изменений) ...

    # --- 4. Поиск Файлов ---
    log.info(f"Searching for images (excluding output file)...")
    input_files_found = []
    SUPPORTED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff', '.webp', '.tif')
    norm_output_path = os.path.normcase(output_file_path)
    try:
        for entry in os.listdir(abs_source_dir):
            entry_path = os.path.join(abs_source_dir, entry)
            # Сравниваем с нормализованным путем выходного файла
            if os.path.isfile(entry_path) and \
               entry.lower().endswith(SUPPORTED_EXTENSIONS) and \
               os.path.normcase(entry_path) != norm_output_path:
                input_files_found.append(entry_path)

        if not input_files_found:
            log.warning("No suitable image files found in the source directory.") # Меняем на warning, т.к. это не критическая ошибка
            log.info(">>> Exiting: No suitable image files found.")
            return False # Возвращаем False
        input_files_sorted = natsorted(input_files_found)
        log.info(f"Found {len(input_files_sorted)} images for collage.")
    except Exception as e:
        log.error(f"Error searching for files: {e}")
        log.info(">>> Exiting: Error during file search.")
        return False # Возвращаем False


    # --- 5. Обработка Индивидуальных Изображений ---
    processed_images: List[Image.Image] = []
    log.info("--- Processing individual images for collage ---")
    total_files_coll = len(input_files_sorted)
    
    # Логируем параметры обработки вначале для лучшей диагностики
    log.info("--- Processing Parameters for Collage Images ---")
    log.info(f"1. Preresize: {'Enabled' if prep_settings.get('enable_preresize', False) else 'Disabled'} " +
            f"(W:{prep_settings.get('preresize_width', 0)}, H:{prep_settings.get('preresize_height', 0)})")
    log.info(f"2. Whitening: {'Enabled' if white_settings.get('enable_whitening', False) else 'Disabled'} " +
            f"(Thresh:{white_settings.get('whitening_cancel_threshold', 765)})")
    
    # Добавляем параметр extra_crop_percent в логирование
    extra_crop_percent = float(bgc_settings.get('extra_crop_percent', 0.0))
    log.info(f"3. BG Removal/Crop: {'Enabled' if bgc_settings.get('enable_bg_crop', False) else 'Disabled'} "
            f"(Tol:{bgc_settings.get('white_tolerance', 10)}, Extra Crop:{extra_crop_percent}%, "
            f"Mask instead transparency:{bgc_settings.get('use_mask_instead_of_transparency', False)})")
    log.info(f"  Crop: {'Enabled' if bgc_settings.get('enable_crop', True) else 'Disabled'}, "
             f"Symmetry: Abs={bgc_settings.get('force_absolute_symmetry', False)}, "
             f"Axes={bgc_settings.get('force_axes_symmetry', False)}, "
             f"Check Perimeter={bgc_settings.get('check_perimeter', False)}")
    log.info(f"4. Padding: {'Enabled' if pad_settings.get('mode', 'never') != 'never' else 'Disabled'} " +
            f"(Mode: {pad_settings.get('mode', 'never')}, Percentage: {pad_settings.get('padding_percent', 0)}%)")
    log.info(f"5. Brightness/Contrast: {'Enabled' if bc_settings.get('enable_bc', False) else 'Disabled'} " +
            f"(B:{bc_settings.get('brightness_factor', 1.0)}, C:{bc_settings.get('contrast_factor', 1.0)})")
            
    # Если включена многопроцессорная обработка, используем ее для предварительной обработки изображений
    if enable_multiprocessing and input_files_sorted:
        try:
            log.info("Starting multiprocessing for collage image preprocessing")
            
            # Подготовка настроек для передачи в процессы
            process_settings = {
                'preprocessing_settings': prep_settings,
                'whitening_settings': white_settings,
                'background_crop_settings': bgc_settings,
                'padding_settings': pad_settings,
                'brightness_contrast_settings': bc_settings,
                'jpg_background_color': jpg_background_color,
                'process_type': 'collage'
            }
            
            # Вызываем многопроцессорную обработку
            import multiprocessing_utils
            results = multiprocessing_utils.process_images_with_multiprocessing(
                input_files_sorted,
                multiprocessing_utils.mp_process_collage_image,
                process_settings,
                max_workers
            )
            
            processed_images = []  # Для хранения обработанных изображений
            
            # Обрабатываем результаты - десериализуем изображения
            for i, result in enumerate(results):
                if result and isinstance(result, bytes):
                    try:
                        # Восстанавливаем изображение из байтов
                        import io
                        img_buffer = io.BytesIO(result)
                        img = Image.open(img_buffer)
                        img.load()  # Загружаем данные в память
                        processed_images.append(img)
                        log.info(f"  Successfully processed image {i+1}/{total_files_coll}: {os.path.basename(input_files_sorted[i])}")
                    except Exception as img_err:
                        log.error(f"  Error deserializing image data for {os.path.basename(input_files_sorted[i])}: {img_err}")
                else:
                    log.warning(f"  Skipping {os.path.basename(input_files_sorted[i])} due to processing errors.")
            
            log.info(f"Multiprocessing preprocessing completed. Processed {len(processed_images)}/{total_files_coll} images.")
            
        except Exception as mp_err:
            log.error(f"Error during multiprocessing: {mp_err}")
            log.exception("Multiprocessing details")
            log.warning("Falling back to single-process mode for collage image preprocessing")
            # Очищаем список изображений в случае ошибки
            processed_images = []
    
    # Если многопроцессорная обработка не включена или произошла ошибка, используем обычную обработку
    if not enable_multiprocessing or not processed_images:
        for idx, path in enumerate(input_files_sorted):
            log.info(f"-> Processing {idx+1}/{total_files_coll}: {os.path.basename(path)}")
            
            # Используем базовый конвейер обработки
            img, metadata = process_image_base(
                path,
                prep_settings,
                white_settings,
                bgc_settings,
                pad_settings,
                bc_settings,
                {'jpg_background_color': jpg_background_color}  # Передаем цвет фона из настроек
            )
            
            if img:
                # Убедимся, что изображение в формате RGBA для коллажа
                if img.mode != 'RGBA':
                    try:
                        rgba_img = img.convert('RGBA')
                        image_utils.safe_close(img)
                        img = rgba_img
                    except Exception as e:
                        log.error(f"Failed to convert to RGBA: {e}")
                        image_utils.safe_close(img)
                        continue
                        
                processed_images.append(img)
                log.info(f"  Successfully processed image {os.path.basename(path)}")
            else:
                log.warning(f"  Skipping {os.path.basename(path)} due to processing errors.")
                
    num_processed = len(processed_images)
    if num_processed == 0:
        log.error("No images were successfully processed for collage. Cannot continue.")
        log.info(">>> Exiting: No successfully processed images.")
        return False # Возвращаем False
    log.info(f"--- Successfully processed {num_processed} images. Starting assembly... ---")


    # --- 6. Вычисление Размеров и Масштабирование Коллажа ---
    log.info("--- Calculating collage dimensions and scaling factors ---")
    # Передаем все настройки, включая 'collage_mode'
    collage_width, collage_height, scale_factors = _calculate_collage_dimensions(processed_images, coll_settings) 
    
    log.info(f"  Calculated base collage size: {collage_width}x{collage_height}")
    log.info(f"  Calculated scale factors: {scale_factors}")
    
    scaled_images: List[Image.Image] = []
    if num_processed > 0 and len(scale_factors) == num_processed:
        for i, img in enumerate(processed_images):
            try:
                current_w, current_h = img.size
                scale_factor = scale_factors[i]
                # Вычисляем новые размеры на основе scale_factor
                # scale_factor уже нормализован так, чтобы подогнать изображения под max_width/max_height
                # и учесть proportional_placement
                nw = max(1, int(round(current_w * scale_factor)))
                nh = max(1, int(round(current_h * scale_factor)))
                
                if nw != current_w or nh != current_h:
                    log.debug(f"  Scaling image {i+1} ({current_w}x{current_h} -> {nw}x{nh}) using factor {scale_factor:.3f}")
                    # Получаем настройку прозрачности для правильного масштабирования
                    use_transparency = coll_settings.get('png_transparent_background', True)
                    
                    # Используем улучшенную функцию масштабирования, которая предотвращает ореолы
                    temp_img = _scale_image(img, (nw, nh), 'fit', use_transparency)
                    scaled_images.append(temp_img)
                    image_utils.safe_close(img) # Закрываем оригинал
                else:
                    log.debug(f"  Image {i+1} already at target size ({nw}x{nh}). No scaling needed.")
                    scaled_images.append(img) # Используем оригинал без масштабирования
            except Exception as e_scale:
                log.error(f"  ! Error scaling image {i+1}: {e_scale}")
                scaled_images.append(img) # Добавляем оригинал в случае ошибки
        processed_images = [] # Очищаем старый список
    else:
        log.warning("Scale factors calculation error or mismatch. Using original processed images.")
        scaled_images = processed_images # Используем то, что есть
        processed_images = []

    # --- 7. Сборка Коллажа ---
    num_final_images = len(scaled_images)
    if num_final_images == 0:
        log.error("No images left after scaling step. Cannot create collage.")
        log.info(">>> Exiting: No images left after scaling.")
        # Очистка, если что-то осталось в scaled_images
        for img in scaled_images: image_utils.safe_close(img)
        return False # Возвращаем False
    log.info(f"--- Assembling collage ({num_final_images} images) ---")
    
    # Определяем сетку
    # Используем значение forced_cols ТОЛЬКО если включен ручной выбор
    enable_forced_cols = coll_settings.get('enable_forced_cols', False) # Получаем состояние галочки
    if enable_forced_cols and forced_cols > 0:
        grid_cols = forced_cols
        log.info(f"  Using forced columns: {grid_cols}")
    else:
        grid_cols = max(1, int(math.ceil(math.sqrt(num_final_images))))
        log.info(f"  Using automatic columns: {grid_cols}")
    
    grid_rows = max(1, int(math.ceil(num_final_images / grid_cols)))
    
    # Определяем максимальные размеры *после* масштабирования
    max_w_scaled = max((img.width for img in scaled_images if img), default=1)
    max_h_scaled = max((img.height for img in scaled_images if img), default=1)
    
    # Рассчитываем отступы между фото на основе масштабированных размеров
    spacing_px_h = int(round(max_w_scaled * (spacing_percent / 100.0)))
    spacing_px_v = int(round(max_h_scaled * (spacing_percent / 100.0)))
    
    # Рассчитываем внешние поля коллажа
    outer_margin_px_h = int(round(max_w_scaled * (outer_margins_percent / 100.0)))
    outer_margin_px_v = int(round(max_h_scaled * (outer_margins_percent / 100.0)))
    
    # Добавляем проверку для отрицательных отступов
    # При отрицательных отступах нужно убедиться, что изображения не будут полностью перекрывать друг друга
    min_spacing_px_h = -max_w_scaled + 10  # Минимум 10 пикселей должно быть видно
    min_spacing_px_v = -max_h_scaled + 10  # Минимум 10 пикселей должно быть видно
    
    if spacing_px_h < min_spacing_px_h:
        log.warning(f"Spacing too negative for horizontal direction. Limiting to {min_spacing_px_h} pixels")
        spacing_px_h = min_spacing_px_h
    
    if spacing_px_v < min_spacing_px_v:
        log.warning(f"Spacing too negative for vertical direction. Limiting to {min_spacing_px_v} pixels")
        spacing_px_v = min_spacing_px_v
    
    # Рассчитываем итоговый размер холста
    # При отрицательных отступах формула немного меняется
    if spacing_px_h >= 0:
        # Положительные отступы между фото + внешние поля с обеих сторон
        canvas_width = (grid_cols * max_w_scaled) + ((grid_cols - 1) * spacing_px_h) + (2 * outer_margin_px_h)
    else:
        # При отрицательных отступах изображения накладываются друг на друга + внешние поля
        # Общая ширина = ширина первого элемента + ширина всех остальных с учетом перекрытия + внешние поля
        if grid_cols > 1:
            canvas_width = max_w_scaled + (grid_cols - 1) * (max_w_scaled + spacing_px_h) + (2 * outer_margin_px_h)
        else:
            canvas_width = max_w_scaled + (2 * outer_margin_px_h)
    
    if spacing_px_v >= 0:
        # Положительные отступы между фото + внешние поля с обеих сторон
        canvas_height = (grid_rows * max_h_scaled) + ((grid_rows - 1) * spacing_px_v) + (2 * outer_margin_px_v)
    else:
        # При отрицательных отступах изображения накладываются друг на друга + внешние поля
        # Общая высота = высота первого элемента + высота всех остальных с учетом перекрытия + внешние поля
        if grid_rows > 1:
            canvas_height = max_h_scaled + (grid_rows - 1) * (max_h_scaled + spacing_px_v) + (2 * outer_margin_px_v)
        else:
            canvas_height = max_h_scaled + (2 * outer_margin_px_v)
    
    # Добавляем информацию о типе отступов (положительные или отрицательные)
    if spacing_percent < 0:
        log.info(f"  Using negative spacing between images: {spacing_percent}% ({spacing_px_h}px horizontal, {spacing_px_v}px vertical)")
        log.info(f"  Images will overlap each other")
    else:
        log.info(f"  Using positive spacing between images: {spacing_percent}% ({spacing_px_h}px horizontal, {spacing_px_v}px vertical)")
    
    # Добавляем информацию о внешних полях
    if outer_margins_percent < 0:
        log.info(f"  Using negative outer margins: {outer_margins_percent}% ({outer_margin_px_h}px horizontal, {outer_margin_px_v}px vertical)")
        log.info(f"  Images will be partially cropped at the edges")
    else:
        log.info(f"  Using positive outer margins: {outer_margins_percent}% ({outer_margin_px_h}px horizontal, {outer_margin_px_v}px vertical)")
    
    log.debug(f"  Grid: {grid_rows}x{grid_cols}, Max Scaled Cell: {max_w_scaled}x{max_h_scaled}")
    log.debug(f"  Final Canvas: {canvas_width}x{canvas_height}")

    collage_canvas = None; final_collage = None
    try:
        # Определяем, нужна ли прозрачность
        use_transparency = coll_settings.get('png_transparent_background', True)
        
        # Используем правильный цвет фона в зависимости от настроек прозрачности
        if use_transparency:
            # Если прозрачность включена, используем прозрачный фон
            collage_canvas = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))
        else:
            # Если прозрачность отключена, используем цвет фона из настроек или белый по умолчанию
            bg_color = coll_settings.get('jpg_background_color', [255, 255, 255])
            # Преобразуем список в кортеж, который требуется для PIL.Image.new()
            if isinstance(bg_color, list) and len(bg_color) >= 3:
                bg_color = tuple(bg_color[:3])
            else:
                bg_color = (255, 255, 255)  # Белый по умолчанию если формат некорректный
                
            collage_canvas = Image.new('RGB', (canvas_width, canvas_height), bg_color)
            
        log.debug(f"    Canvas created: {repr(collage_canvas)}") 
        current_idx = 0
        
        # First, count number of images in the last row
        items_in_last_row = num_final_images % grid_cols
        if items_in_last_row == 0 and num_final_images > 0:
            items_in_last_row = grid_cols  # Last row is full
        
        log.debug(f"    Images in last row: {items_in_last_row} of {grid_cols} columns")
        
        for r in range(grid_rows):
            # Calculate row offset for centering the last row
            row_offset = 0
            is_last_row = (r == grid_rows - 1)
            
            if is_last_row and items_in_last_row < grid_cols:
                # Calculate centering offset for the last row
                empty_cells = grid_cols - items_in_last_row
                row_offset = (empty_cells * (max_w_scaled + spacing_px_h)) // 2
                log.debug(f"    Centering last row with offset: {row_offset}px ({empty_cells} empty cells)")
            
            for c in range(grid_cols):
                if current_idx >= num_final_images: break 
                img = scaled_images[current_idx]
                if img and img.width > 0 and img.height > 0:
                     # Позиция верхнего левого угла ячейки, с учетом смещения для последнего ряда
                     if spacing_px_h >= 0:
                         # Стандартный расчет для положительных отступов
                         cell_x = outer_margin_px_h + c * (max_w_scaled + spacing_px_h)
                         if is_last_row and items_in_last_row < grid_cols:
                             cell_x += row_offset
                     else:
                         # Для отрицательных отступов - изображения накладываются друг на друга
                         cell_x = outer_margin_px_h + c * (max_w_scaled + spacing_px_h)
                         if is_last_row and items_in_last_row < grid_cols:
                             # Корректируем смещение для последнего ряда с учетом отрицательных отступов
                             adjusted_row_offset = (empty_cells * (max_w_scaled + spacing_px_h)) // 2
                             cell_x += adjusted_row_offset
                     
                     if spacing_px_v >= 0:
                         # Стандартный расчет для положительных отступов
                         cell_y = outer_margin_px_v + r * (max_h_scaled + spacing_px_v)
                     else:
                         # Для отрицательных отступов - изображения накладываются друг на друга
                         cell_y = outer_margin_px_v + r * (max_h_scaled + spacing_px_v)
                     
                     # Центрируем изображение внутри ячейки
                     paste_x = cell_x + (max_w_scaled - img.width) // 2
                     paste_y = cell_y + (max_h_scaled - img.height) // 2
                     
                     try:
                         # Определяем, нужна ли прозрачность
                         use_transparency = coll_settings.get('png_transparent_background', True)
                         
                         # Готовим изображение для вставки с бинарной маской
                         if img.mode == 'RGBA':
                             # Создаем бинарную маску для четких краев
                             red, green, blue, alpha = img.split()
                             binary_alpha = alpha.point(lambda x: 255 if x > 240 else 0)
                             
                             # Применяем оригинальное изображение с бинарной маской
                             collage_canvas.paste(img, (paste_x, paste_y), binary_alpha)
                         elif 'transparency_mask' in img.info:
                             # Используем сохраненную маску
                             mask = img.info['transparency_mask']
                             
                             # Преобразуем в бинарную маску, если нужно
                             binary_mask = mask.point(lambda x: 0 if x == 0 else 255)
                             collage_canvas.paste(img, (paste_x, paste_y), binary_mask)
                         else:
                             # Обычная вставка без маски
                             collage_canvas.paste(img, (paste_x, paste_y))
                             
                     except Exception as e:
                         log.error(f"Error pasting image {i+1}: {e}")
                         # Резервный вариант - простая вставка без маски
                         collage_canvas.paste(img, (paste_x, paste_y))
                current_idx += 1
            if current_idx >= num_final_images: break
        
        log.info("  Images placed on collage canvas.")
        for img in scaled_images: image_utils.safe_close(img) # Закрываем масштабированные исходники
        scaled_images = []
        
        final_collage = collage_canvas # Передаем владение
        log.debug(f"    final_collage assigned: {repr(final_collage)}") 
        collage_canvas = None # Очищаем старую переменную

        # --- 8. Трансформации Коллажу ---
        log.info("--- Applying transformations to collage ---")
        # Яркость/Контраст
        if bc_settings.get('enable_bc'):
            final_collage = image_utils.apply_brightness_contrast(
                final_collage,
                brightness_factor=bc_settings.get('brightness_factor', 1.0),
                contrast_factor=bc_settings.get('contrast_factor', 1.0)
            )
            if not final_collage: raise ValueError("Collage became None after brightness/contrast.")
        
        # Соотношение сторон
        # === ИСПРАВЛЕНА ПРОВЕРКА ФЛАГА ===
        if coll_settings.get('enable_force_aspect_ratio'): # Проверяем флаг
            aspect_ratio_coll = coll_settings.get('force_collage_aspect_ratio')
            if aspect_ratio_coll:
                # Вызов ТОЛЬКО если флаг True и значение есть
                final_collage = _apply_force_aspect_ratio(final_collage, aspect_ratio_coll)
                if not final_collage: raise ValueError("Collage became None after aspect ratio.")
                log.info(f"    Collage Force Aspect Ratio applied. New size: {final_collage.size}")
            else:
                log.warning("Collage force aspect ratio enabled but ratio value is missing/invalid.")
        
        # Макс. размер
        if coll_settings.get('enable_max_dimensions'): # Проверяем флаг
            max_w_coll = coll_settings.get('max_collage_width', 0)
            max_h_coll = coll_settings.get('max_collage_height', 0)
            if max_w_coll > 0 or max_h_coll > 0:
                 final_collage = _apply_max_dimensions(final_collage, max_w_coll, max_h_coll)
                 if not final_collage: raise ValueError("Collage became None after max dimensions.")
            else:
                 log.warning("Collage max dimensions enabled but width/height are zero.")

        # Точный холст
        # --- Логика точного холста --- 
        canvas_applied_coll = False
        if coll_settings.get('enable_exact_canvas'): # Проверяем флаг
            exact_w_coll = coll_settings.get('final_collage_exact_width', 0)
            exact_h_coll = coll_settings.get('final_collage_exact_height', 0)
            if exact_w_coll > 0 and exact_h_coll > 0:
                # Вызов ТОЛЬКО если флаг True и значения корректны
                final_collage = _apply_final_canvas_or_prepare(final_collage, exact_w_coll, exact_h_coll, output_format, valid_jpg_bg)
                if not final_collage: raise ValueError("Collage became None after exact canvas.")
                log.info(f"    Collage Exact Canvas applied. New size: {final_collage.size}")
                canvas_applied_coll = True
            else:
                log.warning("Collage exact canvas enabled but width/height are zero.")
        
        # Вызываем _apply_final_canvas_or_prepare в режиме подготовки,
        # только если точный холст не был применен выше.
        if not canvas_applied_coll:
            log.debug("  Applying prepare mode to collage (no exact canvas applied).")
            final_collage = _apply_final_canvas_or_prepare(final_collage, 0, 0, output_format, valid_jpg_bg)
            if not final_collage: raise ValueError("Collage became None after prepare mode.")
        # ----------------------------- 

        # --- 9. Сохранение Коллажа ---
        log.info("--- Saving final collage ---")
        save_successful = _save_image(final_collage, output_file_path, output_format, 
                                    jpeg_quality, valid_jpg_bg,
                                    coll_settings.get('png_transparent_background', True),
                                    coll_settings.get('png_background_color', [255, 255, 255]),
                                    coll_settings.get('save_options', {}).get('remove_metadata', False))
        if save_successful:
            log.info(f"--- Collage processing finished successfully! Saved to {output_file_path} ---")
            success_flag = True # Устанавливаем флаг успеха
        else:
            log.error("--- Collage processing failed during final save. ---")
            success_flag = False # Флаг неудачи

    except Exception as e:
        log.critical(f"!!! Error during collage assembly/transform/save: {e}", exc_info=True)
        success_flag = False # Флаг неудачи при исключении
    finally:
        log.debug("Cleaning up collage resources...")
        image_utils.safe_close(collage_canvas); image_utils.safe_close(final_collage)
        # Очищаем оставшиеся списки на всякий случай
        for img in processed_images: image_utils.safe_close(img)
        for img in scaled_images: image_utils.safe_close(img)
        gc.collect() # Принудительная сборка мусора

    total_time = time.time() - start_time
    log.info(f"Total collage processing time: {total_time:.2f} seconds. Success: {success_flag}")
    log.info("--- Collage Processing Function Finished ---")
    return success_flag # Возвращаем флаг

def _merge_with_template(image, template_path_or_image, settings=None):
    """
    Объединяет изображение с шаблоном с учетом настроек позиционирования и масштабирования.
    Всегда использует максимально возможный размер и качество, увеличивая меньшее изображение.
    
    Args:
        image: Исходное изображение
        template_path_or_image: Путь к файлу шаблона или объект Image
        settings: Словарь с настройками
        
    Returns:
        Image: Результат объединения или None в случае ошибки
    """
    if settings is None:
        settings = {}
        
    try:
        # Загружаем шаблон
        if isinstance(template_path_or_image, str):
            if not os.path.exists(template_path_or_image):
                log.error(f"  > Template file not found: {template_path_or_image}")
                return None
            else:
                template = Image.open(template_path_or_image)
                template.load()
        elif isinstance(template_path_or_image, Image.Image):
            template = template_path_or_image
        else:
            log.error(f"Invalid template type: {type(template_path_or_image)}")
            return None
    except Exception as e:
        log.error(f"  > Error loading template: {str(e)}")
        return None
        
    log.info(f"  > Template loaded. Size: {template.size}, mode: {template.mode}")
    
    # Создаем копии оригинальных изображений для масштабирования перед обработкой
    original_image = image.copy()
    original_template = template.copy()
    
    # Получаем настройки
    position = settings.get('position', 'center')
    template_position = settings.get('template_position', 'center')  # Новая настройка для позиции шаблона
    template_on_top = settings.get('template_on_top', True)
    no_scaling = settings.get('no_scaling', False)
    width_ratio = settings.get('width_ratio', [1.0, 1.0])
    enable_width_ratio = settings.get('enable_width_ratio', False)
    fit_image_to_template = settings.get('fit_image_to_template', False)
    fit_template_to_image = settings.get('fit_template_to_image', False)
    
    # Получаем цвет фона, установленный пользователем (или белый по умолчанию)
    jpg_background_color = settings.get('jpg_background_color', [255, 255, 255])
    # Проверяем тип и формат цвета фона
    default_bg_color = (255, 255, 255)
    bg_color = default_bg_color
    
    if jpg_background_color is not None:
        if isinstance(jpg_background_color, (tuple, list)) and len(jpg_background_color) == 3:
            bg_color = tuple(map(int, jpg_background_color))
        elif isinstance(jpg_background_color, int):
            bg_color = jpg_background_color
        else:
            log.warning(f"Invalid jpg_background_color format: {jpg_background_color}, using default white")
    
    log.info(f"  > Using background color: {bg_color}")
    
    # Конвертируем изображения в RGBA если нужно
    # Сохраняем исходные режимы для правильной финальной конвертации
    original_image_mode = original_image.mode
    original_template_mode = original_template.mode
    
    if image.mode != 'RGBA':
        image = image.convert('RGBA')
    if template.mode != 'RGBA':
        template = template.convert('RGBA')
    
    # Получаем размеры
    image_width, image_height = image.size
    template_width, template_height = template.size
    
    log.info(f"  > Original sizes - Image: {image_width}x{image_height} ({original_image_mode}), Template: {template_width}x{template_height} ({original_template_mode})")
    
    # Определяем размеры холста и коэффициенты масштабирования
    canvas_width = max(image_width, template_width)
    canvas_height = max(image_height, template_height)
    
    # Флаги для отслеживания, какие изображения нужно масштабировать
    image_needs_scaling = False
    template_needs_scaling = False
    image_scale_factor = 1.0
    template_scale_factor = 1.0
    
    # Определяем необходимость и параметры масштабирования, но не применяем
    if no_scaling:
        # В режиме без масштабирования используем максимальные размеры
        log.info("  > No scaling mode - Using maximum sizes")
        log.info(f"  > Canvas size: {canvas_width}x{canvas_height}")
    elif enable_width_ratio:
        # Вычисляем целевые размеры на основе соотношения площадей
        target_area_ratio = width_ratio[0] / width_ratio[1]  # Соотношение площадей
        
        # Вычисляем площади
        image_area = image_width * image_height
        template_area = template_width * template_height
        
        # Определяем, какое изображение нужно увеличить
        if image_area < template_area * target_area_ratio:
            # Нужно увеличить изображение
            target_area = template_area * target_area_ratio
            image_scale_factor = math.sqrt(target_area / image_area)
            image_needs_scaling = True
            
            log.info(f"  > Width ratio enabled - Will scale image with factor {image_scale_factor:.3f} to match area ratio {target_area_ratio:.2f}")
        else:
            # Нужно увеличить шаблон
            target_area = image_area / target_area_ratio
            template_scale_factor = math.sqrt(target_area / template_area)
            template_needs_scaling = True
            
            log.info(f"  > Width ratio enabled - Will scale template with factor {template_scale_factor:.3f} to match area ratio {target_area_ratio:.2f}")
    elif fit_image_to_template:
        # Масштабируем изображение или шаблон, чтобы они соответствовали друг другу
        if image_width <= template_width and image_height <= template_height:
            # Изображение меньше шаблона по обоим измерениям - увеличиваем изображение
            width_ratio = template_width / image_width
            height_ratio = template_height / image_height
            # Используем минимальный коэффициент, чтобы изображение касалось шаблона по одной стороне
            image_scale_factor = min(width_ratio, height_ratio)
            image_needs_scaling = True
            
            log.info(f"  > Fit image to template - Will scale image up with factor: {image_scale_factor:.3f}")
        else:
            # Изображение больше шаблона хотя бы по одному измерению - увеличиваем шаблон
            width_ratio = image_width / template_width
            height_ratio = image_height / template_height
            # Используем минимальный коэффициент, чтобы шаблон касался изображения по одной стороне
            template_scale_factor = min(width_ratio, height_ratio)
            template_needs_scaling = True
            
            log.info(f"  > Fit image to template - Will scale template up with factor: {template_scale_factor:.3f}")
    elif fit_template_to_image:
        # Масштабируем шаблон, чтобы он помещался в изображение
        if template_width < image_width and template_height < image_height:
            # Шаблон меньше изображения, увеличиваем его
            width_ratio = image_width / template_width
            height_ratio = image_height / template_height
            template_scale_factor = max(width_ratio, height_ratio)  # Используем max для максимального увеличения
            template_needs_scaling = True
            
            log.info(f"  > Fit template to image - Will scale template up with factor: {template_scale_factor:.3f}")
        else:
            # Шаблон больше изображения, увеличиваем изображение
            width_ratio = template_width / image_width
            height_ratio = template_height / image_height
            image_scale_factor = max(width_ratio, height_ratio)  # Используем max для максимального увеличения
            image_needs_scaling = True
            
            log.info(f"  > Fit template to image - Will scale image up with factor: {image_scale_factor:.3f}")
    
    # Масштабируем оригинальные изображения до обработки
    scaled_image = original_image
    scaled_template = original_template
    
    # Применяем масштабирование к оригиналам, если нужно
    if image_needs_scaling and image_scale_factor > 1.0:
        new_width = int(round(image_width * image_scale_factor))
        new_height = int(round(image_height * image_scale_factor))
        
        log.info(f"  > Scaling original image: {image_width}x{image_height} -> {new_width}x{new_height}")
        
        # Создаем фон с пользовательским цветом
        background = Image.new('RGB', (new_width, new_height), bg_color)
        
        # Масштабируем исходное изображение
        if original_image.mode == 'RGBA':
            # Для RGBA масштабируем с использованием NEAREST для предотвращения ореолов
            temp_scaled = _scale_image(original_image, (new_width, new_height), 'fit', use_transparency=False)
            # Накладываем на фон с сохранением прозрачности
            background.paste(temp_scaled, (0, 0), temp_scaled)
            image_utils.safe_close(temp_scaled)
        else:
            # Для RGB сначала масштабируем
            temp_scaled = _scale_image(original_image, (new_width, new_height), 'fit', use_transparency=False)
            # Затем просто копируем на фон с пользовательским цветом (прозрачности нет)
            background.paste(temp_scaled, (0, 0))
            image_utils.safe_close(temp_scaled)
        
        scaled_image = background
        
        # Обновляем размеры холста
        canvas_width = max(canvas_width, new_width)
        canvas_height = max(canvas_height, new_height)
    
    if template_needs_scaling and template_scale_factor > 1.0:
        new_width = int(round(template_width * template_scale_factor))
        new_height = int(round(template_height * template_scale_factor))
        
        log.info(f"  > Scaling original template: {template_width}x{template_height} -> {new_width}x{new_height}")
        
        # Создаем фон с пользовательским цветом
        background = Image.new('RGB', (new_width, new_height), bg_color)
        
        # Масштабируем исходный шаблон
        if original_template.mode == 'RGBA':
            # Для RGBA масштабируем с использованием NEAREST для предотвращения ореолов
            temp_scaled = _scale_image(original_template, (new_width, new_height), 'fit', use_transparency=False)
            # Накладываем на фон с сохранением прозрачности
            background.paste(temp_scaled, (0, 0), temp_scaled)
            image_utils.safe_close(temp_scaled)
        else:
            # Для RGB сначала масштабируем
            temp_scaled = _scale_image(original_template, (new_width, new_height), 'fit', use_transparency=False)
            # Затем просто копируем на фон с пользовательским цветом (прозрачности нет)
            background.paste(temp_scaled, (0, 0))
            image_utils.safe_close(temp_scaled)
        
        scaled_template = background
        
        # Обновляем размеры холста
        canvas_width = max(canvas_width, new_width)
        canvas_height = max(canvas_height, new_height)
    
    # Очищаем оригиналы, которые больше не нужны
    if original_image is not scaled_image:
        image_utils.safe_close(original_image)
    if original_template is not scaled_template:
        image_utils.safe_close(original_template)
    
    # Конвертируем в RGBA для корректного наложения
    if scaled_image.mode != 'RGBA':
        temp_img = scaled_image.convert('RGBA')
        image_utils.safe_close(scaled_image)
        scaled_image = temp_img
        
    if scaled_template.mode != 'RGBA':
        temp_tmpl = scaled_template.convert('RGBA')
        image_utils.safe_close(scaled_template)
        scaled_template = temp_tmpl
    
    # Создаем холст нужного размера с пользовательским фоном
    # Используем RGBA для холста, чтобы правильно работать с прозрачностью
    canvas = Image.new('RGBA', (canvas_width, canvas_height), (bg_color[0], bg_color[1], bg_color[2], 255))
    log.info(f"  > Created canvas: {canvas_width}x{canvas_height} with background color: {bg_color}")
    
    # Вычисляем позиции для размещения
    image_pos = _calculate_paste_position(scaled_image.size, canvas.size, position)
    template_pos = _calculate_template_position(scaled_template.size, canvas.size, template_position)
    
    # Размещаем изображения в зависимости от порядка
    if template_on_top:
        canvas.paste(scaled_image, image_pos, scaled_image)
        canvas.paste(scaled_template, template_pos, scaled_template)
    else:
        canvas.paste(scaled_template, template_pos, scaled_template)
        canvas.paste(scaled_image, image_pos, scaled_image)
    
    log.info(f"  > Image position: {image_pos}, size: {scaled_image.size}")
    log.info(f"  > Template position: {template_pos}, size: {scaled_template.size}")
    log.info(f"  > Template on top: {template_on_top}")
    
    return canvas

def _process_image_for_merge(img: Image.Image, merge_settings: Dict[str, Any]) -> Optional[Image.Image]:
    """Обрабатывает изображение перед слиянием с шаблоном."""
    try:
        log.info("=== Starting _process_image_for_merge function ===")
        log.info(f"Input image size: {img.size}, mode: {img.mode}")
        log.info(f"Settings: {merge_settings}")
        
        # Get template path and clean it
        template_path = merge_settings.get('template_path', '')
        if template_path:
            # Check if path contains quotes
            if template_path.startswith('"') or template_path.startswith("'"):
                log.warning(f"Template path contains quotes: {template_path}")
                # Remove quotes and clean the path
                template_path = template_path.strip('"\'')
                template_path = os.path.normpath(template_path)
                log.info(f"Cleaned template path: {template_path}")
            
            if not os.path.exists(template_path):
                log.error(f"Template file not found: {template_path}")
                return None
            
            try:
                # Check if file is PSD
                if template_path.lower().endswith('.psd'):                    
                    try:
                        from psd_tools import PSDImage
                        psd = PSDImage.open(template_path)
                        # Convert PSD to PIL Image
                        template = psd.compose()
                        if template.mode == 'CMYK':
                            template = template.convert('RGB')
                    except ImportError:
                        log.error("psd_tools library not installed. Cannot process PSD files.")
                        return None
                else:
                    template = Image.open(template_path)
                    template.load()
                
                # Process template if enabled
                if merge_settings.get('process_template', False):
                    log.info("  > Processing template image...")
                    template = _process_image_for_collage(template, merge_settings)
                    if not template:
                        log.error("Failed to process template image")
                        return None
                    log.info(f"  > Template processed. New size: {template.size}, mode: {template.mode}")
                
                # Convert to RGBA for transparency
                if template.mode != 'RGBA':
                    template = template.convert('RGBA')
                
                # Merge with template
                result = _merge_with_template(img, template, merge_settings)
                if not result:
                    log.error("Failed to merge with template")
                    return None
                
                return result
                
            except Exception as e:
                log.error(f"Error processing template: {e}")
                if isinstance(e, FileNotFoundError):
                    log.error(f"Template file not found at path: {template_path}")
                elif isinstance(e, OSError) and "Invalid argument" in str(e):
                    log.error(f"Invalid template path format: {template_path}")
                log.exception("Error details")
                return None
        
        return img
        
    except Exception as e:
        log.error(f"Error in _process_image_for_merge: {e}")
        log.exception("Error details")
        return None

def _calculate_paste_position(image_size, canvas_size, position='center'):
    """
    Вычисляет позицию для размещения изображения на холсте.
    
    Args:
        image_size (tuple): Размер изображения (width, height)
        canvas_size (tuple): Размер холста (width, height)
        position (str): Позиция ('center', 'top', 'bottom', 'left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right')
        
    Returns:
        tuple: Координаты (x, y) для размещения изображения
    """
    x = (canvas_size[0] - image_size[0]) // 2
    y = (canvas_size[1] - image_size[1]) // 2
    
    if position == 'center':
        pass  # Используем значения по умолчанию
    elif position == 'top':
        y = 0
    elif position == 'bottom':
        y = canvas_size[1] - image_size[1]
    elif position == 'left':
        x = 0
    elif position == 'right':
        x = canvas_size[0] - image_size[0]
    elif position == 'top-left':
        x = 0
        y = 0
    elif position == 'top-right':
        x = canvas_size[0] - image_size[0]
        y = 0
    elif position == 'bottom-left':
        x = 0
        y = canvas_size[1] - image_size[1]
    elif position == 'bottom-right':
        x = canvas_size[0] - image_size[0]
        y = canvas_size[1] - image_size[1]
    else:
        logger.warning(f"Неизвестная позиция '{position}', используем центр")
    
    return (x, y)

def _calculate_template_position(template_size, canvas_size, position='center'):
    """
    Вычисляет позицию для размещения шаблона на холсте.
    
    Args:
        template_size (tuple): Размер шаблона (width, height)
        canvas_size (tuple): Размер холста (width, height)
        position (str): Позиция ('center', 'top', 'bottom', 'left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right')
        
    Returns:
        tuple: Координаты (x, y) для размещения шаблона
    """
    x = (canvas_size[0] - template_size[0]) // 2
    y = (canvas_size[1] - template_size[1]) // 2
    
    if position == 'center':
        pass  # Используем значения по умолчанию
    elif position == 'top':
        y = 0
    elif position == 'bottom':
        y = canvas_size[1] - template_size[1]
    elif position == 'left':
        x = 0
    elif position == 'right':
        x = canvas_size[0] - template_size[0]
    elif position == 'top-left':
        x = 0
        y = 0
    elif position == 'top-right':
        x = canvas_size[0] - template_size[0]
        y = 0
    elif position == 'bottom-left':
        x = 0
        y = canvas_size[1] - template_size[1]
    elif position == 'bottom-right':
        x = canvas_size[0] - template_size[0]
        y = canvas_size[1] - template_size[1]
    else:
        logger.warning(f"Неизвестная позиция '{position}', используем центр")
    
    return (x, y)

def get_image_files(folder_path: str) -> List[str]:
    """
    Находит все изображения в указанной папке и возвращает их в естественном порядке.
    
    Args:
        folder_path: Путь к папке с изображениями
        
    Returns:
        List[str]: Список путей к изображениям в естественном порядке
    """
    try:
        log.info(f"Searching for images in: {folder_path}")
        if not os.path.exists(folder_path):
            log.error(f"Folder does not exist: {folder_path}")
            return []

        # Добавляем поддержку PSD файлов
        image_extensions = ('.jpg', '.jpeg', '.png', '.webp', '.tiff', '.bmp', '.gif', '.psd')
        files = []
        for file in os.listdir(folder_path):
            if file.lower().endswith(image_extensions):
                files.append(os.path.join(folder_path, file))

        # Сортируем файлы в естественном порядке
        from natsort import natsorted, ns
        files = natsorted(files, alg=ns.IGNORECASE)
        
        log.info(f"Found {len(files)} image files")
        log.debug(f"Files in natural order: {[os.path.basename(f) for f in files]}")
        return files

    except Exception as e:
        log.exception("Error in get_image_files")
        return []

def _apply_background_crop(img, white_tolerance, perimeter_tolerance, symmetric_absolute=False, 
                         symmetric_axes=False, check_perimeter=True, enable_crop=True,
                         perimeter_mode='if_white', image_metadata=None, extra_crop_percent=0.0,
                         removal_mode='full', use_mask_instead_of_transparency=False,
                         halo_reduction_level=0, analyze_only=False, boundaries=None,
                         background_color=None, padding_settings=None):
    """
    Удаляет белый фон и/или обрезает изображение.
    
    Args:
        img: PIL Image
        white_tolerance: Допуск для определения белого цвета (0-255)
        perimeter_tolerance: Допуск для проверки периметра (0-255)
        symmetric_absolute: Обрезать симметрично во всех направлениях
        symmetric_axes: Обрезать симметрично по осям X/Y
        check_perimeter: Проверять периметр перед обработкой
        enable_crop: Включить обрезку или только удалить фон
        perimeter_mode: 'if_white', 'if_not_white', 'always'
        image_metadata: Словарь метаданных для передачи информации
        extra_crop_percent: Дополнительная обрезка (в процентах)
        removal_mode: Режим удаления фона ('full' или 'edges')
        use_mask_instead_of_transparency: Использовать маску вместо прозрачности
        analyze_only: Анализировать только границы, не применять изменения
        boundaries: Предварительно вычисленные границы для обрезки
        background_color: Цвет фона для указания при создании маски
        padding_settings: Настройки отступов для проверки white_perimeter
    
    Returns:
        Изображение без белого фона / обрезанное или None при ошибке
        В режиме analyze_only возвращает словарь границ обрезки
    """
    log.debug(f"DIAGNOSTICS: _apply_background_crop start, img ID: {id(img)}, mode: {img.mode}, size: {img.size}")
    log.debug(f"DIAGNOSTICS: Parameters: white_tolerance={white_tolerance}, perimeter_tolerance={perimeter_tolerance}, "
              f"symmetric_absolute={symmetric_absolute}, symmetric_axes={symmetric_axes}, check_perimeter={check_perimeter}, "
              f"enable_crop={enable_crop}, perimeter_mode={perimeter_mode}, analyze_only={analyze_only}, "
              f"has_boundaries={boundaries is not None}, removal_mode={removal_mode}")
    
    if image_metadata is None:
        image_metadata = {}

    # Устанавливаем цвет фона по умолчанию
    bg_color = (255, 255, 255)
    if background_color:
        bg_color = background_color
    
    try:
        # Шаг 1: Проверка периметра с использованием perimeter_tolerance
        # Важно: для проверки периметра используется perimeter_tolerance, а не white_tolerance
        log.info(f"Checking perimeter with tolerance: {perimeter_tolerance}")
        log.debug(f"DIAGNOSTICS: Before perimeter check, img ID: {id(img)}")
        
        perimeter_is_white = image_utils.check_perimeter_is_white(img, perimeter_tolerance, 1)
        log.debug(f"DIAGNOSTICS: After perimeter check, img ID: {id(img)}, still valid: {img is not None}")
        
        image_metadata["has_white_perimeter_before_crop"] = perimeter_is_white
        log.info(f"Perimeter check result: {'White' if perimeter_is_white else 'NOT White'} (Tolerance: {perimeter_tolerance})")
        
        # Дополнительно: Проверка периметра с использованием tolerance из настроек отступов
        if padding_settings and 'perimeter_check_tolerance' in padding_settings:
            padding_tolerance = int(padding_settings.get('perimeter_check_tolerance', 10))
            log.info(f"Also checking perimeter with padding tolerance: {padding_tolerance}")
            log.debug(f"DIAGNOSTICS: Before padding perimeter check, img ID: {id(img)}")
            
            padding_perimeter_is_white = image_utils.check_perimeter_is_white(img, padding_tolerance, 1)
            log.debug(f"DIAGNOSTICS: After padding perimeter check, img ID: {id(img)}, still valid: {img is not None}")
            
            image_metadata["has_white_perimeter_for_padding"] = padding_perimeter_is_white
            # ВАЖНО: Сохраняем состояние ДО обрезки в специальном ключе для if_white режима
            if padding_settings.get('mode') == 'if_white':
                image_metadata["has_white_perimeter_for_padding_before_crop"] = padding_perimeter_is_white
                log.debug(f"Saved pre-crop padding perimeter state for if_white mode: white_perimeter={padding_perimeter_is_white}")
            log.info(f"Padding perimeter check result: {'White' if padding_perimeter_is_white else 'NOT White'} (Tolerance: {padding_tolerance})")
        
        # ВАЖНОЕ ИСПРАВЛЕНИЕ: Специальная обработка режима analyze_only
        if analyze_only:
            log.debug(f"DIAGNOSTICS: analyze_only mode detected, checking perimeter conditions")
            
            # Если проверка периметра включена и режим не "always"
            if check_perimeter and perimeter_mode != 'always':
                # Если не соответствует условиям периметра - возвращаем безопасные границы
                if (perimeter_mode == 'if_white' and not perimeter_is_white) or (perimeter_mode == 'if_not_white' and perimeter_is_white):
                    log.debug(f"DIAGNOSTICS: analyze_only - perimeter check failed, returning safe boundaries")
                    # Возвращаем безопасные границы для всего изображения
                    width, height = img.size
                    return {
                        'left': 0,
                        'top': 0,
                        'right': width,
                        'bottom': height
                    }
                    
            # Продолжаем анализ для analyze_only, если соответствует условиям периметра
            log.debug(f"DIAGNOSTICS: analyze_only - performing boundary analysis")
            
            # Создаем копию изображения для анализа
            img_rgba = None
            try:
                if img.mode != 'RGBA':
                    img_rgba = img.convert('RGBA')
                    log.debug(f"DIAGNOSTICS: Converted to RGBA for analysis, ID: {id(img_rgba)}")
                else:
                    img_rgba = img.copy()
                    log.debug(f"DIAGNOSTICS: Created RGBA copy for analysis, ID: {id(img_rgba)}")
                
                # Применяем удаление фона
                img_no_bg = image_utils.remove_white_background(img_rgba, white_tolerance, mode=removal_mode)
                
                if not img_no_bg:
                    log.error("Background removal failed during analysis")
                    image_utils.safe_close(img_rgba)
                    # Возвращаем безопасные границы при ошибке
                    width, height = img.size
                    return {
                        'left': 0,
                        'top': 0,
                        'right': width,
                        'bottom': height
                    }
                
                # Находим границы обрезки
                crop_boundaries = image_utils.find_crop_boundaries(
                    img_no_bg, 
                    white_tolerance,
                    symmetric_absolute, 
                    symmetric_axes, 
                    extra_crop_percent
                )
                
                # Очищаем ресурсы
                image_utils.safe_close(img_rgba)
                image_utils.safe_close(img_no_bg)
                
                log.debug(f"DIAGNOSTICS: analyze_only returning boundaries: {crop_boundaries}")
                return crop_boundaries
                
            except Exception as e:
                log.error(f"Error during boundary analysis: {e}")
                if img_rgba:
                    image_utils.safe_close(img_rgba)
                
                # Возвращаем безопасные границы при ошибке
                width, height = img.size
                return {
                    'left': 0,
                    'top': 0,
                    'right': width,
                    'bottom': height
                }
        
        # Фаза 2: проверяем периметр для решения о продолжении всего процесса
        if not analyze_only:
            if check_perimeter and perimeter_mode != 'always':
                # ВАЖНОЕ ИЗМЕНЕНИЕ: Всегда проверяем периметр, независимо от наличия boundaries
                if perimeter_mode == 'if_white' and not perimeter_is_white:
                    log.info("Phase 2: Background removal and cropping skipped - perimeter is not white (mode: if_white)")
                    if boundaries is not None:
                        log.info("Pre-calculated boundaries ignored because perimeter does not match criteria")
                    
                    # Даже если обрезка пропускается, сохраняем информацию о периметре для padding
                    if padding_settings and 'perimeter_check_tolerance' in padding_settings:
                        padding_tolerance = int(padding_settings.get('perimeter_check_tolerance', 10))
                        image_metadata["has_white_perimeter_for_padding_after_crop"] = padding_perimeter_is_white
                        log.debug(f"Saved perimeter state for padding when skipping crop: white_perimeter={padding_perimeter_is_white} (tolerance={padding_tolerance})")
                        
                        # Сохраняем специальное значение для режима if_not_white
                        if padding_settings.get('mode') == 'if_not_white':
                            log.debug(f"Saved perimeter state for if_not_white mode when skipping crop: white_perimeter={padding_perimeter_is_white}")
                    
                    log.debug(f"DIAGNOSTICS: Skipping background removal due to perimeter check, returning original img ID: {id(img)}")
                    return img.copy()  # ИСПРАВЛЕНИЕ: Возвращаем копию вместо оригинала
                
                if perimeter_mode == 'if_not_white' and perimeter_is_white:
                    log.info("Phase 2: Background removal and cropping skipped - perimeter is white (mode: if_not_white)")
                    if boundaries is not None:
                        log.info("Pre-calculated boundaries ignored because perimeter does not match criteria")
                    
                    # Даже если обрезка пропускается, сохраняем информацию о периметре для padding
                    if padding_settings and 'perimeter_check_tolerance' in padding_settings:
                        padding_tolerance = int(padding_settings.get('perimeter_check_tolerance', 10))
                        image_metadata["has_white_perimeter_for_padding_after_crop"] = padding_perimeter_is_white
                        log.debug(f"Saved perimeter state for padding when skipping crop: white_perimeter={padding_perimeter_is_white} (tolerance={padding_tolerance})")
                        
                        # Сохраняем специальное значение для режима if_not_white
                        if padding_settings.get('mode') == 'if_not_white':
                            log.debug(f"Saved perimeter state for if_not_white mode when skipping crop: white_perimeter={padding_perimeter_is_white}")
                    
                    log.debug(f"DIAGNOSTICS: Skipping background removal due to perimeter check, returning original img ID: {id(img)}")
                    return img.copy()  # ИСПРАВЛЕНИЕ: Возвращаем копию вместо оригинала
            
            # Только для логирования
            if boundaries is not None:
                log.debug("Using pre-calculated boundaries (perimeter check passed)")
        
        # Создаем копию и конвертируем в RGBA, если еще не RGBA
        log.debug(f"DIAGNOSTICS: About to create RGBA copy for background processing, original img ID: {id(img)}")
        img_rgba = None
        if img.mode != 'RGBA':
            try:
                img_rgba = img.convert('RGBA')
                log.debug(f"Converted {img.mode} -> RGBA for background processing")
                log.debug(f"DIAGNOSTICS: Converted to RGBA, new img_rgba ID: {id(img_rgba)}")
            except Exception as e:
                log.error(f"Failed to convert to RGBA for background removal: {e}")
                log.debug(f"DIAGNOSTICS: Failed to convert to RGBA, returning original img ID: {id(img)}")
                return img.copy()  # ИСПРАВЛЕНИЕ: Возвращаем копию вместо оригинала
        else:
            img_rgba = img.copy()
            log.debug(f"DIAGNOSTICS: Created RGBA copy, img_rgba ID: {id(img_rgba)}")
        
        # Применяем удаление фона используя white_tolerance
        log.info(f"Removing white background using white_tolerance={white_tolerance}")
        log.debug(f"DIAGNOSTICS: Before removing background, img_rgba ID: {id(img_rgba)}")
        
        img_processed = image_utils.remove_white_background(img_rgba, white_tolerance, mode=removal_mode)
        
        if not img_processed:
            log.error("Background removal failed.")
            log.debug(f"DIAGNOSTICS: Background removal failed, cleaning up img_rgba ID: {id(img_rgba)}")
            image_utils.safe_close(img_rgba)
            log.debug(f"DIAGNOSTICS: Returning original img ID: {id(img)}")
            return img.copy()  # ИСПРАВЛЕНИЕ: Возвращаем копию вместо оригинала
        
        log.debug(f"DIAGNOSTICS: Background removed, img_processed ID: {id(img_processed)}")
        
        # Если включена обрезка, применяем ее
        if enable_crop:
            log.debug(f"DIAGNOSTICS: Crop is enabled, preparing to crop")
            if boundaries is not None:
                # Используем предварительно вычисленные границы
                log.info(f"Using pre-calculated crop boundaries: {boundaries}")
                log.debug(f"DIAGNOSTICS: Before applying crop with boundaries, img_processed ID: {id(img_processed)}")
                
                img_processed = image_utils.apply_crop_with_boundaries(img_processed, boundaries)
                
                if not img_processed:
                    log.error("Failed to apply pre-calculated crop boundaries")
                    log.debug(f"DIAGNOSTICS: Failed to apply crop, returning original img ID: {id(img)}")
                    return img.copy()  # ИСПРАВЛЕНИЕ: Возвращаем копию вместо оригинала
                
                log.debug(f"DIAGNOSTICS: After applying crop, img_processed ID: {id(img_processed)}")
            else:
                # Вычисляем границы и сразу применяем
                log.info(f"Applying standard crop with extra crop percent: {extra_crop_percent}%")
                log.debug(f"DIAGNOSTICS: Before standard crop, img_processed ID: {id(img_processed)}")
                
                img_processed = image_utils.crop_image(
                    img_processed, 
                    symmetric_axes, 
                    symmetric_absolute, 
                    extra_crop_percent
                )
                
                if not img_processed:
                    log.error("Failed to crop image")
                    log.debug(f"DIAGNOSTICS: Failed to crop, returning original img ID: {id(img)}")
                    return img.copy()  # ИСПРАВЛЕНИЕ: Возвращаем копию вместо оригинала
                
                log.debug(f"DIAGNOSTICS: After standard crop, img_processed ID: {id(img_processed)}")
        else:
            log.info("Cropping is disabled, keeping canvas size")
        
        # Проверяем и сохраняем состояние периметра ПОСЛЕ обработки для использования в _apply_padding
        # Используем perimeter_tolerance для последующих шагов
        try:
            log.debug(f"DIAGNOSTICS: Before checking perimeter after crop, img_processed ID: {id(img_processed)}")
            has_white_perimeter_after = image_utils.check_perimeter_is_white(img_processed, perimeter_tolerance, 1)
            log.debug(f"DIAGNOSTICS: After checking perimeter, img_processed ID: {id(img_processed)}, still valid: {img_processed is not None}")
            
            image_metadata["has_white_perimeter_after_crop"] = has_white_perimeter_after
            log.debug(f"Saved perimeter state AFTER crop/bg-removal: white_perimeter={has_white_perimeter_after}")
            
            # Также сохраняем состояние с использованием tolerance для padding, если он указан
            if padding_settings and 'perimeter_check_tolerance' in padding_settings:
                padding_tolerance = int(padding_settings.get('perimeter_check_tolerance', 10))
                log.debug(f"DIAGNOSTICS: Before checking padding perimeter, img_processed ID: {id(img_processed)}")
                
                padding_perimeter_after = image_utils.check_perimeter_is_white(img_processed, padding_tolerance, 1)
                log.debug(f"DIAGNOSTICS: After checking padding perimeter, img_processed ID: {id(img_processed)}, still valid: {img_processed is not None}")
                
                image_metadata["has_white_perimeter_for_padding_after_crop"] = padding_perimeter_after
                log.debug(f"Saved perimeter state for padding AFTER crop: white_perimeter={padding_perimeter_after} (tolerance={padding_tolerance})")
                        
                # Сохраняем специальное значение для режима if_not_white
                if padding_settings.get('mode') == 'if_not_white':
                    log.debug(f"Saved post-crop padding perimeter state for if_not_white mode: white_perimeter={padding_perimeter_after}")
        except Exception as e:
            log.warning(f"Failed to check perimeter after crop/bg-removal: {e}")
            log.debug(f"DIAGNOSTICS: Exception during post-processing perimeter check: {e}")
        
        # Применяем устранение ореолов, если уровень > 0
        if halo_reduction_level > 0 and img_processed.mode == 'RGBA':
            log.info(f"Applying halo reduction with level: {halo_reduction_level}")
            log.debug(f"DIAGNOSTICS: Before halo reduction, img_processed ID: {id(img_processed)}")
            
            img_processed = _reduce_halos(img_processed, halo_reduction_level)
            log.debug(f"DIAGNOSTICS: After halo reduction, img_processed ID: {id(img_processed)}")
        
        # Если нужно использовать маску вместо прозрачности
        if use_mask_instead_of_transparency:
            # Используем пользовательский цвет фона при создании маски
            log.debug(f"DIAGNOSTICS: Before creating mask, img_processed ID: {id(img_processed)}")
            
            img_processed = _create_mask_instead_of_transparency(img_processed, bg_color)
            log.debug(f"DIAGNOSTICS: After creating mask, img_processed ID: {id(img_processed)}")
            log.debug("Created mask instead of transparency with user-defined background color")
        
        # ИСПРАВЛЕНИЕ: Дополнительная проверка для избежания ошибки "Operation on closed image"
        if img_processed is None:
            log.warning("Safety check: img_processed is None at the end of _apply_background_crop. Returning copy of original.")
            return img.copy()
            
        log.debug(f"DIAGNOSTICS: Returning processed image, img_processed ID: {id(img_processed)}")
        return img_processed
    except Exception as e:
        log.error(f"Error in background/crop: {e}")
        log.debug(f"DIAGNOSTICS: Exception in _apply_background_crop: {e}, returning copy of original img")
        return img.copy()  # ИСПРАВЛЕНИЕ: Возвращаем копию вместо оригинала

def _reduce_halos(img, level=1):
    """
    Устраняет ореолы вокруг объектов с прозрачностью, 
    обрабатывая полупрозрачные пиксели по краям.
    
    Args:
        img: RGBA изображение
        level: Уровень устранения ореолов (1-5), где 1 - минимальное, 5 - максимальное
    
    Returns:
        RGBA изображение с уменьшенными ореолами
    """
    if img.mode != 'RGBA':
        return img
        
    if level <= 0:
        return img.copy()
        
    # Ограничиваем уровень от 1 до 5
    level = max(1, min(5, level))
    
    # Создаем копию для работы
    result = img.copy()
    
    try:
        # Разделяем каналы
        r, g, b, a = result.split()
        
        # Вычисляем пороговое значение прозрачности в зависимости от уровня
        # Чем выше уровень, тем строже порог
        # level 1: ~230, level 5: ~128
        threshold = 255 - (level * 25)
        
        # Создаем новый альфа-канал с удалением полупрозрачных пикселей
        # Пиксели со значением ниже порога становятся полностью прозрачными
        new_a = a.point(lambda x: 0 if x < threshold else x)
        
        # При высоких уровнях (4-5) также можно применить эрозию к альфа-каналу
        if level >= 4:
            from PIL import ImageFilter
            # Применяем размытие и снова бинаризуем для сглаживания краев
            new_a = new_a.filter(ImageFilter.MinFilter(3))  # Эрозия для удаления тонких краев
            
        # Объединяем каналы обратно
        result = Image.merge("RGBA", (r, g, b, new_a))
        
        log.debug(f"Halo reduction applied with level {level}")
        return result
    except Exception as e:
        log.error(f"Error in halo reduction: {e}")
        return img.copy()  # ИСПРАВЛЕНИЕ: Возвращаем копию вместо оригинала

def _scale_image(image, target_size, mode='fit', use_transparency=True):
    """
    Масштабирует изображение с сохранением пропорций.
    
    Args:
        image: Исходное изображение
        target_size: Целевой размер (ширина, высота)
        mode: Режим масштабирования ('fit' - вписать в размер, 'fill' - заполнить размер)
        use_transparency: Использовать ли прозрачность при масштабировании
        
    Returns:
        Image: Масштабированное изображение
    """
    source_width, source_height = image.size
    target_width, target_height = target_size
    
    # Если размеры совпадают, ничего не делаем
    if source_width == target_width and source_height == target_height:
        return image.copy()
        
    # Вычисляем коэффициенты масштабирования
    width_ratio = target_width / source_width
    height_ratio = target_height / source_height
    
    # Определяем итоговый коэффициент масштабирования
    if mode == 'fit':
        scale_factor = min(width_ratio, height_ratio)
    else:  # fill
        scale_factor = max(width_ratio, height_ratio)
    
    # Вычисляем новые размеры
    new_width = int(source_width * scale_factor)
    new_height = int(source_height * scale_factor)
    
    try:
        # Для режима без прозрачности и масштабирования больше 1.0 используем строгий подход
        if not use_transparency and scale_factor > 1.0:
            log.debug(f"Using NEAREST resampling for upscaling (scale={scale_factor:.2f}) to prevent halos")
            
            # Предварительная обработка, если в режиме RGBA
            if image.mode == 'RGBA':
                # Создаем бинарную прозрачность для четких краев
                r, g, b, alpha = image.split()
                binary_alpha = alpha.point(lambda x: 255 if x >= 240 else 0)
                clean_img = Image.merge('RGBA', (r, g, b, binary_alpha))
                
                # Масштабируем с ближайшим соседом для сохранения четких краев
                scaled_img = clean_img.resize((new_width, new_height), Image.Resampling.NEAREST)
                image_utils.safe_close(clean_img)
                return scaled_img
            
            # Для остальных режимов просто используем NEAREST
            return image.resize((new_width, new_height), Image.Resampling.NEAREST)
        
        # Для уменьшения используем LANCZOS, для увеличения - подходящий метод в зависимости от прозрачности
        if scale_factor < 1.0:
            # Для уменьшения размера используем Lanczos
            return image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        else:
            # Для увеличения выбираем метод в зависимости от прозрачности
            if use_transparency:
                # С прозрачностью используем LANCZOS для плавных переходов
                return image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            else:
                # Без прозрачности используем NEAREST для сохранения четких границ
                return image.resize((new_width, new_height), Image.Resampling.NEAREST)
    
    except Exception as e:
        log.error(f"Error in _scale_image: {e}")
        # Запасной вариант - просто NEAREST
        return image.resize((new_width, new_height), Image.Resampling.NEAREST)

def _calculate_collage_dimensions(images: List[Image.Image], settings: Dict[str, Any]) -> Tuple[int, int, List[float]]:
    """
    Вычисляет размеры коллажа и коэффициенты масштабирования для изображений.
    Нормализует коэффициенты масштабирования, делая наименьший из них равным 1.
    
    Args:
        images: Список изображений для коллажа
        settings: Настройки коллажа (словарь 'collage_mode' из общих настроек)
        
    Returns:
        Tuple[int, int, List[float]]: (ширина коллажа, высота коллажа, коэффициенты масштабирования)
    """
    if not images:
        return 0, 0, []
    
    # Проверяем, включено ли пропорциональное размещение
    proportional_placement = settings.get('proportional_placement', False)
    
    if proportional_placement:
        # Находим максимальные размеры среди всех изображений
        max_width = max(img.width for img in images)
        max_height = max(img.height for img in images)
        
        # Рассчитываем коэффициенты масштабирования для каждого изображения
        scale_factors = []
        for img in images:
            if img.width > 0 and img.height > 0:
                width_ratio = max_width / img.width
                height_ratio = max_height / img.height
                # Используем минимальный коэффициент для масштабирования (чтобы вписать)
                scale_factor = min(width_ratio, height_ratio)
                scale_factors.append(scale_factor)
            else:
                scale_factors.append(0.0) # Для изображений с нулевым размером
        
        # Нормализуем коэффициенты масштабирования, делая наименьший из них равным 1
        non_zero_scales = [s for s in scale_factors if s > 0]
        if non_zero_scales:
            min_scale = min(non_zero_scales)
            if min_scale > 0:  # Избегаем деления на ноль
                normalized_scale_factors = [(scale / min_scale) if scale > 0 else 0.0 for scale in scale_factors]
                scale_factors = normalized_scale_factors
                log.info(f"  > Base normalized scale factors (min=1): {scale_factors}")
            else: 
                log.warning("  Minimum scale factor is zero, skipping base normalization.")
        else:
            log.warning("  All scale factors are zero, skipping base normalization.")

        # Применяем пользовательские соотношения размеров
        placement_ratios = settings.get('placement_ratios', [1.0] * len(images))
        log.debug(f"  Applying proportional placement with ratios: {placement_ratios}")
        
        # Убедимся, что количество соотношений совпадает
        if len(placement_ratios) != len(images):
            log.warning(f"  Mismatch between number of images ({len(images)}) and ratios ({len(placement_ratios)}). Using default ratios [1.0, ...]")
            placement_ratios = [1.0] * len(images)
        
        # Нормализуем соотношения, делая наименьшее равным 1
        valid_ratios = [r for r in placement_ratios if isinstance(r, (int, float)) and r > 0]
        if valid_ratios:
            min_ratio = min(valid_ratios)
            normalized_ratios = [(ratio / min_ratio) if isinstance(ratio, (int, float)) and ratio > 0 else 1.0 for ratio in placement_ratios]
            log.info(f"  > Normalized placement ratios (min=1): {normalized_ratios}")
            
            # Применяем соотношения к коэффициентам масштабирования
            scale_factors = [scale * ratio for scale, ratio in zip(scale_factors, normalized_ratios)]
            log.info(f"  > Scale factors after applying ratios: {scale_factors}")
        else:
            log.warning("  No valid positive placement ratios found. Skipping ratio application.")
    else:
        # Если пропорциональное размещение выключено, используем оригинальные размеры
        log.debug("  Proportional placement disabled, using original sizes")
        scale_factors = [1.0] * len(images)
    
    # Рассчитываем итоговые размеры коллажа на основе масштабированных размеров
    scaled_widths = [img.width * scale for img, scale in zip(images, scale_factors)]
    scaled_heights = [img.height * scale for img, scale in zip(images, scale_factors)]
    
    # Базовый размер - максимальный из масштабированных
    final_max_width = max(int(round(w)) for w in scaled_widths if w > 0) if any(w > 0 for w in scaled_widths) else 1
    final_max_height = max(int(round(h)) for h in scaled_heights if h > 0) if any(h > 0 for h in scaled_heights) else 1
    
    log.debug(f"  Calculated max scaled dimensions for grid cell: {final_max_width}x{final_max_height}")

    # ВАЖНО: Убираем применение максимальных ограничений коллажа к отдельным изображениям
    # Размер будет ограничен только на финальном этапе для всего коллажа
    # Это предотвращает двойное масштабирование и появление ореолов
    
    if settings.get('enable_max_dimensions', False):
        max_width_limit = settings.get('max_collage_width', 0)
        max_height_limit = settings.get('max_collage_height', 0)
        
        # Оцениваем примерный размер коллажа (нужны колонки)
        forced_cols = settings.get('forced_cols', 0)
        num_final_images = len(images)
        grid_cols = forced_cols if forced_cols > 0 else max(1, int(math.ceil(math.sqrt(num_final_images))))
        grid_rows = max(1, int(math.ceil(num_final_images / grid_cols)))
        
        # Логируем информацию о предполагаемом размере, но НЕ применяем масштабирование
        # Упрощенная оценка без учета spacing
        estimated_width = grid_cols * final_max_width
        estimated_height = grid_rows * final_max_height
        log.debug(f"  Estimated collage size: {estimated_width}x{estimated_height} (Grid: {grid_rows}x{grid_cols})")
        
        if max_width_limit > 0 or max_height_limit > 0:
            log.info(f"  Max dimensions ({max_width_limit}x{max_height_limit}) will be applied only to the final collage")

    # Возвращаем максимальные размеры ячейки (для построения сетки) и финальные факторы масштабирования
    return final_max_width, final_max_height, scale_factors

def _apply_padding(img: Image.Image, pad_settings: dict, image_metadata: dict = None) -> Optional[Image.Image]:
    """
    Применяет padding к изображению в зависимости от настроек.
    Использует image_metadata для оптимизации проверок периметра.
    """
    enable_padding = pad_settings.get('mode', 'never') != 'never' # Включено, если не равно 'never'
    
    # Если padding не включен - просто возвращаем изображение
    if not enable_padding:
        return img

    # Если метаданные не переданы - создаем пустой словарь
    if image_metadata is None:
        image_metadata = {}

    try:
        padding_percent = int(pad_settings.get('padding_percent', 0)) # Процент padding'а
        padding_type = pad_settings.get('mode', 'never')  # Тип padding'а (always, never, if_white, if_not_white)
        allow_expansion = bool(pad_settings.get('allow_expansion', True))  # Разрешить увеличение размера
        perimeter_check_tolerance = int(pad_settings.get('perimeter_check_tolerance', 10))
        
        log.info(f"Padding settings - Mode: {padding_type}, Percentage: {padding_percent}%, Allow expansion={allow_expansion}")
        
        # Логируем все доступные метаданные для диагностики
        log.debug(f"Available metadata keys: {list(image_metadata.keys())}")
        if 'has_white_perimeter_for_padding_before_crop' in image_metadata:
            log.debug(f"has_white_perimeter_for_padding_before_crop = {image_metadata['has_white_perimeter_for_padding_before_crop']}")
        if 'has_white_perimeter_for_padding_after_crop' in image_metadata:
            log.debug(f"has_white_perimeter_for_padding_after_crop = {image_metadata['has_white_perimeter_for_padding_after_crop']}")
        if 'has_white_perimeter_for_padding' in image_metadata:
            log.debug(f"has_white_perimeter_for_padding = {image_metadata['has_white_perimeter_for_padding']}")
        if 'has_white_perimeter_after_crop' in image_metadata:
            log.debug(f"has_white_perimeter_after_crop = {image_metadata['has_white_perimeter_after_crop']}")
        if 'has_white_perimeter_before_whitening' in image_metadata:
            log.debug(f"has_white_perimeter_before_whitening = {image_metadata['has_white_perimeter_before_whitening']}")
        
        # Проверяем необходимость добавления padding на основе метаданных
        should_apply_padding = False
        
        if padding_type == 'always':
            should_apply_padding = True
            log.debug("Padding mode is 'always', applying padding")
        elif padding_type == 'never':
            should_apply_padding = False
            log.debug("Padding mode is 'never', skipping padding")
        elif padding_type == 'if_white' or padding_type == 'if_not_white':
            # Получаем информацию о периметре
            has_white_perimeter = None
            padded_perimeter_used = False
            
            if padding_type == 'if_white':
                # Для режима if_white используем информацию ДО обрезки
                if 'has_white_perimeter_for_padding_before_crop' in image_metadata:
                    has_white_perimeter = image_metadata['has_white_perimeter_for_padding_before_crop']
                    padded_perimeter_used = True
                    log.debug(f"Using pre-crop padding perimeter check (special for if_white): white_perimeter={has_white_perimeter}")
                elif 'has_white_perimeter_for_padding' in image_metadata:
                    has_white_perimeter = image_metadata['has_white_perimeter_for_padding']
                    padded_perimeter_used = True
                    log.debug(f"Using pre-computed padding-specific perimeter check: white_perimeter={has_white_perimeter}")
                else:
                    # Если нет сохраненной информации для if_white, используем текущее состояние
                    log.debug(f"No cached perimeter state found for if_white, checking perimeter now with tolerance={perimeter_check_tolerance}")
                    has_white_perimeter = image_utils.check_perimeter_is_white(img, perimeter_check_tolerance, 1)
            elif padding_type == 'if_not_white':
                # Для режима if_not_white используем НАЧАЛЬНОЕ состояние периметра
                # ИЗМЕНЕНИЕ: Используем состояние ДО обработки для if_not_white режима
                if 'has_white_perimeter_before_whitening' in image_metadata:
                    has_white_perimeter = image_metadata['has_white_perimeter_before_whitening']
                    padded_perimeter_used = True
                    log.debug(f"Using initial perimeter check for if_not_white mode: white_perimeter={has_white_perimeter}")
                elif 'has_white_perimeter_for_padding' in image_metadata:
                    has_white_perimeter = image_metadata['has_white_perimeter_for_padding']
                    padded_perimeter_used = True
                    log.debug(f"Using pre-computed padding-specific perimeter check: white_perimeter={has_white_perimeter}")
                else:
                    # Если нет сохраненной информации, проверяем текущее состояние
                    log.debug(f"No cached perimeter state found for if_not_white, checking perimeter now with tolerance={perimeter_check_tolerance}")
                    has_white_perimeter = image_utils.check_perimeter_is_white(img, perimeter_check_tolerance, 1)
            
            # Для диагностики, всегда выполняем проверку текущего состояния
            current_perimeter_white = image_utils.check_perimeter_is_white(img, perimeter_check_tolerance, 1)
            log.debug(f"Current perimeter check (for reference): white_perimeter={current_perimeter_white}")
            log.debug(f"Using {'padding-specific perimeter check' if padded_perimeter_used else 'regular perimeter check'} for decision")
            
            # Принимаем решение в зависимости от типа padding'а
            if padding_type == 'if_white':
                should_apply_padding = has_white_perimeter
                log.info(f"Padding mode is 'if_white', white perimeter={has_white_perimeter}, applying padding={should_apply_padding}")
            elif padding_type == 'if_not_white': 
                should_apply_padding = not has_white_perimeter
                log.info(f"Padding mode is 'if_not_white', white perimeter={has_white_perimeter}, applying padding={should_apply_padding}")
        
        # ИСПРАВЛЕНИЕ: Если не разрешено увеличение холста и размер изображения будет увеличен, пропускаем padding
        if not allow_expansion and should_apply_padding:
            # Вычисляем, будет ли изображение увеличено в размере
            original_size = img.size
            padding_pixels = min(original_size) * padding_percent / 100
            
            # Проверяем, действительно ли произойдет увеличение размера холста
            if padding_pixels > 0:
                log.info(f"Canvas expansion prevented: allow_expansion={allow_expansion}. Skipping padding.")
                should_apply_padding = False
        
        # Применяем padding, если нужно
        if should_apply_padding:
            log.info(f"Adding padding: {padding_percent}% ({int(min(img.size) * padding_percent / 100)}px). New size: {(img.width + int(img.width * padding_percent / 50), img.height + int(img.height * padding_percent / 50))}")
            old_size = img.size
            img = image_utils.add_padding(img, padding_percent)
            if img:
                log.debug(f"Padding applied. New size: {img.size} (was {old_size})")
            else:
                log.error("Failed to apply padding")
                return None
        else:
            log.info("Skipping padding based on settings and perimeter state")
            
        return img
        
    except Exception as e:
        log.error(f"Error in _apply_padding: {e}", exc_info=True)
        return None

def _process_single_image(file_path: str, prep_settings, white_settings, bgc_settings, pad_settings, bc_settings) -> Optional[Image.Image]:
    """
    Обрабатывает одно изображение с использованием базового конвейера обработки.
    
    Args:
        file_path: Путь к изображению
        prep_settings: Настройки предварительной обработки
        white_settings: Настройки отбеливания
        bgc_settings: Настройки удаления фона и обрезки
        pad_settings: Настройки отступов
        bc_settings: Настройки яркости/контрастности
    
    Returns:
        Обработанное изображение или None в случае ошибки
    """
    try:
        filename = os.path.basename(file_path)
        log.debug(f"-- Processing single image: {filename}")
        
        # Используем базовый конвейер обработки
        img, metadata = process_image_base(
            file_path,
            prep_settings,
            white_settings,
            bgc_settings,
            pad_settings,
            bc_settings
        )
        
        if img is None:
            log.error(f"Base processing failed for {filename}")
            return None
        
        # Проверяем и при необходимости конвертируем в RGBA
        if img.mode != 'RGBA':
            try:
                rgba_img = img.convert('RGBA')
                image_utils.safe_close(img)
                img = rgba_img
                log.debug(f"Converted image to RGBA mode")
            except Exception as e:
                log.error(f"Failed to convert final image to RGBA: {str(e)}")
                return None
        
        log.debug(f"-- Finished processing image: {filename}")
        return img
        
    except Exception as e:
        log.error(f"Unexpected error processing {os.path.basename(file_path)}: {str(e)}", exc_info=True)
        return None

def process_image_base(image_or_path, prep_settings, white_settings, bgc_settings, pad_settings, bc_settings, extra_options=None) -> Optional[Tuple[Image.Image, dict]]:
    """
    Базовый конвейер обработки изображения. Применяет 5 основных шагов обработки:
    1. Изменение размера (preresize)
    2. Отбеливание (whitening)
    3. Удаление фона и обрезка (background crop)
    4. Добавление отступов (padding)
    5. Яркость и контрастность (brightness/contrast)
    
    Args:
        image_or_path: PIL Image или путь к файлу изображения
        prep_settings: Настройки предварительной обработки
        white_settings: Настройки отбеливания
        bgc_settings: Настройки удаления фона и обрезки
        pad_settings: Настройки отступов
        bc_settings: Настройки яркости/контрастности
        extra_options: Дополнительные настройки (включая jpg_background_color)
    
    Returns:
        Tuple[Image.Image, dict]: (Обработанное изображение, словарь метаданных) или (None, {}) в случае ошибки
    """
    # Создаем словарь для хранения метаданных изображения
    image_metadata = {}
    img = None
    filename = None
    original_image = None  # Для хранения оригинала при применении двухэтапной обработки
    crop_boundaries = None  # Для хранения границ обрезки
    scale_factor = 1.0
    
    # Получаем дополнительные настройки, если они предоставлены
    if extra_options is None:
        extra_options = {}
    
    # Получаем цвет фона из дополнительных настроек или используем белый по умолчанию
    jpg_background_color = extra_options.get('jpg_background_color', [255, 255, 255])
    
    try:
        # 1. Загрузка изображения
        if isinstance(image_or_path, str):
            # Это путь к файлу
            filename = os.path.basename(image_or_path)
            log.debug(f"-- Starting base processing for: {filename}")
            try:
                img = Image.open(image_or_path)
                img.load()  # Заставляем PIL загрузить данные изображения
                # Сохраняем оригинальное изображение для двухэтапной обработки
                original_image = img.copy()
            except Exception as e:
                log.error(f"Failed to load image {image_or_path}: {str(e)}")
                return None, {}
        else:
            # Это уже объект Image
            img = image_or_path
            log.debug(f"-- Starting base processing for image object: {repr(img)}")
            # Сохраняем оригинальное изображение
            original_image = img.copy()
            
        # Проверка, что изображение валидно
        if img is None or img.width <= 0 or img.height <= 0:
            log.error(f"Invalid image or zero dimensions")
            return None, {}
        
        log.debug(f"Initial image: Size={img.size}, Mode={img.mode}")
        
        # 2. Предварительное изменение размера (если включено)
        if prep_settings.get('enable_preresize', False):
            img = _apply_preresize(img, 
                                 prep_settings.get('preresize_width', 0),
                                 prep_settings.get('preresize_height', 0))
            if img is None:
                log.error(f"Pre-resize failed")
                return None, {}
                
            log.debug(f"After preresize: Size={img.size}")
            
        # Сохраняем состояние периметра перед отбеливанием (для оптимизации проверок)
        perimeter_check_tolerance = int(pad_settings.get('perimeter_check_tolerance', 10))
        has_white_perimeter = image_utils.check_perimeter_is_white(img, perimeter_check_tolerance, 1)
        image_metadata["has_white_perimeter_before_whitening"] = has_white_perimeter
        log.debug(f"Saved perimeter state before whitening: white_perimeter={has_white_perimeter}")
        
        # 3. Отбеливание фона (если включено)
        if white_settings.get('enable_whitening', False):
            whitening_cancel_threshold = int(white_settings.get('whitening_cancel_threshold', 765))
            log.info(f"Applying whitening with threshold: {whitening_cancel_threshold}")
            
            img = image_utils.whiten_image_by_darkest_perimeter(img, whitening_cancel_threshold)
            if img is None:
                log.error(f"Whitening failed")
                return None, {}
                
            log.debug(f"After whitening: Size={img.size}")
            
            # Обновляем оригинальное изображение с примененным отбеливанием или без
            if original_image is not None:
                image_utils.safe_close(original_image)
                original_image = img.copy()
                log.debug("Original image copy updated after whitening phase")
            
            # Сохраняем состояние периметра после отбеливания
            has_white_perimeter = image_utils.check_perimeter_is_white(img, perimeter_check_tolerance, 1)
            image_metadata["has_white_perimeter_after_whitening"] = has_white_perimeter
            log.debug(f"Saved perimeter state after whitening: white_perimeter={has_white_perimeter}")
        else:
            # Обновляем оригинальное изображение даже если отбеливание отключено
            # Это необходимо для корректной работы двухфазной обработки фона
            if original_image is not None:
                image_utils.safe_close(original_image)
                original_image = img.copy()
            log.debug("Whitening disabled but original image copy updated for correct two-phase processing")
        
        # 4. Удаление фона и обрезка (если включено) - ДВУХЭТАПНАЯ ОБРАБОТКА
        if bgc_settings.get('enable_bg_crop', False):
            log.info(f"=== Processing background and crop (two-phase) ===")
            
            # Извлекаем параметры
            white_tolerance = int(bgc_settings.get('white_tolerance', 0))
            perimeter_tolerance = int(bgc_settings.get('perimeter_tolerance', 10))
            crop_symmetric_absolute = bool(bgc_settings.get('crop_symmetric_absolute', False))
            crop_symmetric_axes = bool(bgc_settings.get('crop_symmetric_axes', False))
            check_perimeter = bool(bgc_settings.get('check_perimeter', False))
            enable_crop = bool(bgc_settings.get('enable_crop', True))
            perimeter_mode = bgc_settings.get('perimeter_mode', 'if_white')
            extra_crop_percent = float(bgc_settings.get('extra_crop_percent', 0.0))
            removal_mode = bgc_settings.get('removal_mode', 'full')
            use_mask_instead_of_transparency = bool(bgc_settings.get('use_mask_instead_of_transparency', False))
            halo_reduction_level = int(bgc_settings.get('halo_reduction_level', 0))
            
            # Всегда используем двухэтапную обработку если у нас есть оригинальное изображение,
            # независимо от настроек прозрачности
            use_two_phase = original_image is not None
            
            # Предустановленные пользователем параметры масштабирования, если есть
            user_scale_factor = float(bgc_settings.get('scale_factor', 1.0))
            if user_scale_factor > 1.0:
                scale_factor = user_scale_factor
                log.info(f"Using user-defined scale factor: {scale_factor}")
            
            if use_two_phase and enable_crop:
                log.info(f"Using two-phase background/crop processing to prevent halos")
                
                # ФАЗА 1: Анализ границ на изображении после отбеливания
                log.info(f"Phase 1: Analyzing crop boundaries")
                crop_boundaries = _apply_background_crop(
                    img,  # Используем обработанное изображение для анализа границ
                    white_tolerance,
                    perimeter_tolerance,
                    crop_symmetric_absolute,
                    crop_symmetric_axes,
                    check_perimeter,
                    enable_crop,
                    perimeter_mode=perimeter_mode,
                    image_metadata=image_metadata,
                    extra_crop_percent=extra_crop_percent,
                    removal_mode=removal_mode,
                    analyze_only=True,  # Только анализируем, не применяем
                    padding_settings=pad_settings  # Передаем настройки отступов для проверки их tolerance
                )
                
                if not crop_boundaries:
                    log.error(f"Phase 1: Failed to get crop boundaries")
                    image_utils.safe_close(original_image)
                    return None, {}
                
                log.debug(f"Phase 1: Got crop boundaries: {crop_boundaries}")
                
                # ИСПРАВЛЕНИЕ: Проверяем, что crop_boundaries - это словарь, а не изображение
                if not isinstance(crop_boundaries, dict):
                    log.error(f"Phase 1: Invalid crop_boundaries type: {type(crop_boundaries)}, expected dict")
                    # Создаем безопасные границы - всё изображение
                    width, height = img.size
                    crop_boundaries = {
                        'left': 0,
                        'top': 0,
                        'right': width,
                        'bottom': height
                    }
                    log.info(f"Created safe boundaries instead: {crop_boundaries}")
                
                # Проверяем, что все необходимые ключи существуют
                required_keys = ['left', 'top', 'right', 'bottom']
                if not all(key in crop_boundaries for key in required_keys):
                    log.error(f"Phase 1: Missing required keys in crop_boundaries: {crop_boundaries}")
                    # Создаем безопасные границы - всё изображение
                    width, height = img.size
                    crop_boundaries = {
                        'left': 0,
                        'top': 0,
                        'right': width,
                        'bottom': height
                    }
                    log.info(f"Created safe boundaries instead: {crop_boundaries}")
                
                # Определяем оптимальный масштаб для предотвращения ореолов
                if scale_factor == 1.0:  # Автоматический режим
                    min_size = 200  # Минимальный размер для обрабатываемой части изображения
                    try:
                        crop_width = crop_boundaries['right'] - crop_boundaries['left']
                        crop_height = crop_boundaries['bottom'] - crop_boundaries['top']
                    except (TypeError, KeyError) as e:
                        log.error(f"Error calculating crop dimensions: {e}")
                        crop_width = img.width
                        crop_height = img.height
                    
                    if crop_width < min_size or crop_height < min_size:
                        width_scale = min_size / max(1, crop_width)
                        height_scale = min_size / max(1, crop_height)
                        scale_factor = max(width_scale, height_scale, 1.0)
                        log.info(f"Calculated optimal scale factor: {scale_factor:.2f} for crop size {crop_width}x{crop_height}")
                
                # Применяем масштабирование к оригинальному изображению
                if scale_factor > 1.0:
                    new_width = int(round(original_image.width * scale_factor))
                    new_height = int(round(original_image.height * scale_factor))
                    log.info(f"Scaling original image: {original_image.width}x{original_image.height} -> {new_width}x{new_height}")
                    
                    scaled_img = original_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    
                    # Масштабируем границы обрезки
                    scaled_boundaries = {
                        'left': int(crop_boundaries['left'] * scale_factor),
                        'top': int(crop_boundaries['top'] * scale_factor),
                        'right': int(crop_boundaries['right'] * scale_factor),
                        'bottom': int(crop_boundaries['bottom'] * scale_factor)
                    }
                    
                    # ФАЗА 2: Применение удаления фона и обрезки к масштабированному изображению
                    log.info(f"Phase 2: Applying processing to scaled image with pre-calculated boundaries")
                    log.debug(f"DIAGNOSTICS: PHASE 2 - Before calling _apply_background_crop with scaled image, ID: {id(scaled_img)}, crop={enable_crop}, check_perimeter={check_perimeter}, mode={perimeter_mode}")
                    img = _apply_background_crop(
                        scaled_img,
                        white_tolerance,
                        perimeter_tolerance,
                        crop_symmetric_absolute,
                        crop_symmetric_axes,
                        check_perimeter,
                        enable_crop,
                        perimeter_mode=perimeter_mode,
                        image_metadata=image_metadata,
                        extra_crop_percent=0.0,  # Не применяем дополнительную обрезку, так как она уже в границах
                        removal_mode=removal_mode,
                        use_mask_instead_of_transparency=use_mask_instead_of_transparency,
                        halo_reduction_level=halo_reduction_level,
                        boundaries=scaled_boundaries,
                        background_color=jpg_background_color,  # Добавляем пользовательский цвет фона
                        padding_settings=pad_settings  # Передаем настройки отступов
                    )
                    log.debug(f"DIAGNOSTICS: PHASE 2 - After _apply_background_crop with scaled image, returned ID: {id(img) if img else 'None'}")
                    image_utils.safe_close(scaled_img)
                else:
                    # ФАЗА 2: Применение удаления фона и обрезки к исходному изображению
                    log.info(f"Phase 2: Applying processing with pre-calculated boundaries (no scaling needed)")
                    log.debug(f"DIAGNOSTICS: PHASE 2 - Before calling _apply_background_crop with original image, ID: {id(original_image)}, crop={enable_crop}, check_perimeter={check_perimeter}, mode={perimeter_mode}")
                    img = _apply_background_crop(
                        original_image,
                        white_tolerance,
                        perimeter_tolerance,
                        crop_symmetric_absolute,
                        crop_symmetric_axes,
                        check_perimeter,
                        enable_crop,
                        perimeter_mode=perimeter_mode,
                        image_metadata=image_metadata,
                        extra_crop_percent=0.0,  # Не применяем дополнительную обрезку, так как она уже в границах
                        removal_mode=removal_mode,
                        use_mask_instead_of_transparency=use_mask_instead_of_transparency,
                        halo_reduction_level=halo_reduction_level,
                        boundaries=crop_boundaries,
                        background_color=jpg_background_color,  # Добавляем пользовательский цвет фона
                        padding_settings=pad_settings  # Передаем настройки отступов
                    )
                    log.debug(f"DIAGNOSTICS: PHASE 2 - After _apply_background_crop with original image, returned ID: {id(img) if img else 'None'}")
                
                # Очищаем оригинальное изображение после обработки
                image_utils.safe_close(original_image)
                original_image = None
                
                if img is None:
                    log.error(f"Background removal / crop failed")
                    return None, {}
                    
                log.debug(f"After background/crop: Size={img.size}")
                log.debug(f"DIAGNOSTICS: After both phases of background processing, final image ID: {id(img)}")
                
            else:
                # ОДНОЭТАПНАЯ ОБРАБОТКА
                log.info(f"Using standard one-phase background/crop processing")
                log.debug(f"DIAGNOSTICS: ONE-PHASE - Before calling _apply_background_crop, image ID: {id(img)}, crop={enable_crop}, check_perimeter={check_perimeter}, mode={perimeter_mode}")
                img = _apply_background_crop(
                    img,
                    white_tolerance,
                    perimeter_tolerance,
                    crop_symmetric_absolute,
                    crop_symmetric_axes,
                    check_perimeter,
                    enable_crop,
                    perimeter_mode=perimeter_mode,
                    image_metadata=image_metadata,
                    extra_crop_percent=extra_crop_percent,
                    removal_mode=removal_mode,
                    use_mask_instead_of_transparency=use_mask_instead_of_transparency,
                    halo_reduction_level=halo_reduction_level,
                    background_color=jpg_background_color,  # Добавляем пользовательский цвет фона
                    padding_settings=pad_settings  # Передаем настройки отступов
                )
                log.debug(f"DIAGNOSTICS: ONE-PHASE - After _apply_background_crop, returned ID: {id(img) if img else 'None'}")
                
                if img is None:
                    log.error(f"Background removal / crop failed")
                    return None, {}
                    
                log.debug(f"After background/crop: Size={img.size}")
                log.debug(f"DIAGNOSTICS: After one-phase background processing, final image ID: {id(img)}")
            
            if img is None:
                log.error(f"Background removal / crop failed")
                image_utils.safe_close(original_image)
                return None, {}
                
            log.debug(f"After background/crop: Size={img.size}")
            
        # Очищаем оригинальное изображение, оно больше не нужно
        if original_image is not None and original_image is not img:
            image_utils.safe_close(original_image)
            original_image = None
        
        # 5. Добавление отступов (если включено)
        if pad_settings.get('mode', 'never') != 'never':
            log.info(f"Padding settings - Mode: {pad_settings.get('mode', 'never')}, " +
                   f"Percentage: {pad_settings.get('padding_percent', 0)}%, " +
                   f"Allow expansion: {pad_settings.get('allow_expansion', True)}")
            
            # Передаем metadata с информацией о периметре
            img = _apply_padding(img, pad_settings, image_metadata)
            
            if img is None:
                log.error(f"Padding failed")
                return None, {}
                
            log.debug(f"After padding: Size={img.size}")
        
        # 6. Яркость и контрастность (если включено)
        if bc_settings.get('enable_bc', False):
            brightness_factor = bc_settings.get('brightness_factor', 1.0)
            contrast_factor = bc_settings.get('contrast_factor', 1.0)
            log.info(f"Applying brightness/contrast - B:{brightness_factor}, C:{contrast_factor}")
            
            img = image_utils.apply_brightness_contrast(img, brightness_factor, contrast_factor)
            
            if img is None:
                log.error(f"Brightness/contrast adjustment failed")
                return None, {}
                
            log.debug(f"After brightness/contrast: Size={img.size}")
        
        # Возвращаем обработанное изображение и словарь метаданных
        return img, image_metadata
        
    except Exception as e:
        log.error(f"Unexpected error in base processing: {str(e)}", exc_info=True)
        # Очистка ресурсов
        if original_image is not None and original_image is not img:
            image_utils.safe_close(original_image)
        return None, {}

# ==============================================================================
# === ФУНКЦИИ НОРМАЛИЗАЦИИ АРТИКУЛЕЙ ==========================================
# ==============================================================================

def normalize_article(article: str) -> str:
    """
    Нормализует артикул, удаляя недопустимые символы и приводя к стандартному формату.
    
    Args:
        article: Исходный артикул
        
    Returns:
        Нормализованный артикул
    """
    if not article:
        return ""
        
    # Удаляем недопустимые символы (оставляем только буквы, цифры, дефис и подчеркивание)
    normalized = re.sub(r'[^\w\-]', '_', article)
    
    # Приводим к верхнему регистру для единообразия
    normalized = normalized.upper()
    
    # Удаляем множественные подчеркивания
    normalized = re.sub(r'_+', '_', normalized)
    
    # Удаляем подчеркивания в начале и конце
    normalized = normalized.strip('_')
    
    return normalized


def normalize_articles_in_folder(folder_path: str) -> Dict[str, str]:
    """
    Анализирует имена файлов в папке и создает словарь соответствия
    исходных имен файлов и нормализованных артикулей.
    
    Args:
        folder_path: Путь к папке с изображениями
        
    Returns:
        Словарь {исходное_имя: нормализованный_артикул}
    """
    # Получаем все файлы изображений
    image_files = get_image_files(folder_path)
    result = {}
    
    # Если нет файлов, возвращаем пустой словарь
    if not image_files:
        log.warning(f"No image files found in folder: {folder_path}")
        return result
        
    # Если только один файл, просто нормализуем его имя
    if len(image_files) == 1:
        base_name = os.path.splitext(os.path.basename(image_files[0]))[0]
        normalized = normalize_article(base_name)
        result[image_files[0]] = normalized
        log.info(f"Single file normalization: {base_name} -> {normalized}")
        return result
    
    # Пытаемся определить общий артикул
    common_article = guess_article_from_filenames(folder_path)
    log.info(f"Guessed common article: {common_article}")
    
    # Если общий артикул найден, применяем его к файлам
    if common_article:
        normalized_article = normalize_article(common_article)
        log.info(f"Normalized common article: {common_article} -> {normalized_article}")
        
        # Используем натуральную сортировку для файлов
        from natsort import natsorted
        sorted_files = natsorted(image_files, alg=ns.IGNORECASE)
        log.debug(f"Files sorted naturally: {[os.path.basename(f) for f in sorted_files]}")
        
        # Проверяем, есть ли файл с именем, совпадающим с артикулом
        article_file_exists = False
        for file_path in sorted_files:
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            if base_name.upper() == common_article.upper():
                article_file_exists = True
                # Этот файл станет основным
                result[file_path] = normalized_article
                log.info(f"Found main article file: {os.path.basename(file_path)} -> {normalized_article}")
                break
        
        # Индексируем остальные файлы
        index = 1
        for file_path in sorted_files:
            if file_path in result:  # Пропускаем файл, который уже назначен основным
                continue
                
            # Если нет файла с артикулом и это первый файл, он становится главным
            if not article_file_exists and index == 1:
                result[file_path] = normalized_article
                log.info(f"Using first file as main: {os.path.basename(file_path)} -> {normalized_article}")
            else:
                # Остальные файлы получают индекс
                result[file_path] = f"{normalized_article}_{index}"
                log.info(f"Indexed file: {os.path.basename(file_path)} -> {result[file_path]}")
                index += 1
    else:
        # Если общий артикул не найден, нормализуем каждое имя отдельно
        log.info("No common article found, normalizing each file separately")
        for file_path in image_files:
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            normalized = normalize_article(base_name)
            result[file_path] = normalized
            log.info(f"Individual normalization: {base_name} -> {normalized}")
    
    return result


def apply_normalized_articles(folder_path: str, article_mapping: Dict[str, str], rename_files: bool = False) -> bool:
    """
    Применяет нормализованные артикули к файлам или настройкам.
    
    Args:
        folder_path: Путь к папке с изображениями
        article_mapping: Словарь соответствия {исходное_имя: нормализованный_артикул}
        rename_files: Флаг, указывающий, нужно ли переименовать файлы (True) 
                      или только вернуть маппинг (False)
        
    Returns:
        bool: True если операция успешна, False в случае ошибки
    """
    if not article_mapping:
        log.warning("Empty article mapping provided")
        return False
        
    try:
        if rename_files:
            # Переименовываем файлы согласно маппингу
            log.info(f"Renaming files in {folder_path} according to normalized articles")
            
            # Получаем список файлов с их нормализованными артикулами
            files_to_rename = []
            article_name = None
            
            # Сортируем файлы натуральной сортировкой если доступно
            try:
                from natsort import natsorted
                files_list = natsorted(list(article_mapping.keys()))
            except ImportError:
                files_list = sorted(article_mapping.keys())
                
            log.debug(f"Files to rename (sorted): {len(files_list)}")
            for file_path in files_list:
                article = article_mapping[file_path]
                files_to_rename.append((file_path, article))
                if article_name is None:
                    article_name = article  # Используем первый артикул как основной
            
            if not files_to_rename:
                log.warning("No files to rename after sorting")
                return False
                
            # 1. Проверяем, есть ли файл с точным совпадением с article_name
            exact_match_file = None
            exact_match_path = None
            base_article = article_name
            
            for file_path, article in files_to_rename:
                if article == base_article:
                    file_ext = os.path.splitext(file_path)[1]
                    exact_match_file = os.path.basename(file_path)
                    exact_match_path = file_path
                    log.debug(f"Found exact match file: {exact_match_file}")
                    break
            
            # 2. Создаем временные имена для всех файлов
            temp_files = {}
            for i, (file_path, _) in enumerate(files_to_rename):
                file_ext = os.path.splitext(file_path)[1]
                temp_filename = f"temp_{uuid.uuid4().hex}{file_ext}"
                temp_path = os.path.join(folder_path, temp_filename)
                temp_files[file_path] = temp_path
                
                try:
                    os.rename(file_path, temp_path)
                    log.debug(f"Temporarily renamed: {os.path.basename(file_path)} -> {temp_filename}")
                except Exception as e:
                    log.error(f"Failed to create temporary file: {e}")
                    # Откатываем уже переименованные файлы
                    for old_path, tmp_path in temp_files.items():
                        if os.path.exists(tmp_path) and old_path != file_path:
                            try:
                                os.rename(tmp_path, old_path)
                            except:
                                pass
                    return False
            
            # 3. Переименовываем файлы в финальные имена
            renamed_count = 0
            counter = 1
            
            # Если был найден файл с точным совпадением, переименовываем его первым
            if exact_match_path and exact_match_path in temp_files:
                temp_path = temp_files[exact_match_path]
                file_ext = os.path.splitext(exact_match_path)[1]
                new_filename = f"{base_article}{file_ext}"
                new_path = os.path.join(folder_path, new_filename)
                
                try:
                    os.rename(temp_path, new_path)
                    log.info(f"Renamed main file: {exact_match_file} -> {new_filename}")
                    renamed_count += 1
                    del temp_files[exact_match_path]  # Удаляем обработанный файл из временных
                except Exception as e:
                    log.error(f"Failed to rename main file: {e}")
            
            # Теперь переименовываем остальные файлы
            for original_path, temp_path in temp_files.items():
                file_ext = os.path.splitext(original_path)[1]
                
                # Если нет главного файла, первый становится главным
                if renamed_count == 0:
                    new_filename = f"{base_article}{file_ext}"
                else:
                    new_filename = f"{base_article}_{counter}{file_ext}"
                    counter += 1
                    
                new_path = os.path.join(folder_path, new_filename)
                
                try:
                    os.rename(temp_path, new_path)
                    log.info(f"Renamed: {os.path.basename(original_path)} -> {new_filename}")
                    renamed_count += 1
                except Exception as e:
                    log.error(f"Failed to rename file: {e}")
                    continue
            
            log.info(f"Successfully renamed {renamed_count} of {len(files_to_rename)} files")
            return renamed_count > 0
        
        return True  # Если переименование не требуется, просто возвращаем True
        
    except Exception as e:
        log.error(f"Error applying normalized articles: {e}")
        log.exception("Exception details")
        return False

def guess_article_from_filenames(folder_path: str) -> str:
    """
    Анализирует имена файлов в папке и определяет наиболее часто встречающуюся общую часть,
    которая может являться артикулом.
    
    Args:
        folder_path: Путь к папке с изображениями
        
    Returns:
        str: Предполагаемый артикул или пустая строка, если не удалось определить
    """
    try:
        # Получаем список всех файлов изображений
        image_files = get_image_files(folder_path)
        if not image_files:
            log.warning("No image files found for article guessing")
            return ""
            
        # Извлекаем только имена файлов без расширений и пути
        filenames = []
        for path in image_files:
            filename = os.path.basename(path)
            name_without_ext = os.path.splitext(filename)[0]
            filenames.append(name_without_ext)
            log.debug(f"Processing filename: {name_without_ext}")
            
        if not filenames:
            return ""
            
        # Если получен только один файл, используем его имя как артикул
        if len(filenames) == 1:
            # Заменяем все спецсимволы на дефис и убираем висящие дефисы
            result = re.sub(r'[^\w\-]', '-', filenames[0])
            return result.strip('-')
            
        # Находим общую часть между именами файлов
        first_name = filenames[0]
        log.debug(f"Using first name as template: {first_name}")
        
        # Ищем общую часть, начиная с начала строки
        common_part = ""
        for i in range(len(first_name)):
            current_part = first_name[:i+1]
            # Проверяем, есть ли эта часть во всех остальных именах
            if all(current_part in name for name in filenames[1:]):
                common_part = current_part
            else:
                break
                
        log.debug(f"Found common part: {common_part}")
        
        if common_part:
            # Заменяем все спецсимволы на дефис в результате
            result = re.sub(r'[^\w\-]', '-', common_part)
            # Убираем висящие дефисы
            result = result.strip('-')
            log.debug(f"Final result: {result}")
            return result
            
        return ""
    except Exception as e:
        log.exception(f"Error in guess_article_from_filenames: {e}")
        return ""

def _create_mask_instead_of_transparency(img, background_color=(255, 255, 255)):
    """
    Вместо прозрачности создает и сохраняет маску, переводя изображение в RGB с указанным цветом фона.
    Это решает проблему ореолов при масштабировании, так как в процессе обработки изображение 
    не имеет прозрачности (никаких полупрозрачных пикселей).
    Прозрачность применяется только в момент сохранения PNG.
    
    Args:
        img: RGBA изображение с прозрачностью
        background_color: Цвет фона для RGB изображения (по умолчанию белый)
    
    Returns:
        RGB изображение с указанным цветом фона и сохраненной маской в img.info['transparency_mask']
    """
    if img.mode != 'RGBA':
        try:
            rgba_img = img.convert('RGBA')
        except Exception as e:
            log.error(f"Failed to convert to RGBA in _create_mask_instead_of_transparency: {e}")
            return img
    else:
        rgba_img = img
        
    try:
        # Создаем маску из альфа-канала (1 = непрозрачно, 0 = прозрачно)
        r, g, b, alpha = rgba_img.split()
        
        # Создаем более жесткую бинарную маску для предотвращения ореолов
        # Значения ниже 250 становятся полностью прозрачными (255),
        # остальные - полностью непрозрачными (0)
        threshold = 250  # Очень высокий порог для предотвращения ореолов
        mask = alpha.point(lambda x: 0 if x >= threshold else 255)
        
        # Создаем RGB изображение с указанным цветом фона
        rgb_img = Image.new("RGB", rgba_img.size, background_color)
        
        # Создаем RGBA с измененной альфой для четких краев (бинарная прозрачность)
        clean_edges_alpha = alpha.point(lambda x: 255 if x >= threshold else 0)
        clean_edges_img = Image.merge("RGBA", (r, g, b, clean_edges_alpha))
        
        # Вставляем изображение с четкими краями на указанный фон
        rgb_img.paste(clean_edges_img, (0, 0), clean_edges_img)
        
        # Сохраняем маску в info
        rgb_img.info['transparency_mask'] = mask
        
        # Очищаем промежуточный ресурс
        if rgba_img is not img:
            image_utils.safe_close(rgba_img)
        image_utils.safe_close(clean_edges_img)
        
        return rgb_img
    except Exception as e:
        log.error(f"Error in _create_mask_instead_of_transparency: {e}")
        if rgba_img is not img:
            image_utils.safe_close(rgba_img)
        return img

def _apply_mask_for_png_save(img):
    """
    Применяет сохраненную маску к изображению, делая его RGBA для сохранения в PNG.
    Эта функция вызывается только перед сохранением PNG, чтобы избежать проблем с ореолами.
    
    Args:
        img: RGB изображение с маской в img.info['transparency_mask']
    
    Returns:
        RGBA изображение с чистой прозрачностью (без полутонов)
    """
    try:
        if 'transparency_mask' in img.info:
            # Получаем маску
            mask = img.info['transparency_mask']
            
            # Проверяем совпадение размеров
            if mask.size != img.size:
                mask = mask.resize(img.size, Image.Resampling.NEAREST)
            
            # Создаем RGBA изображение
            rgba_img = img.convert("RGBA")
            
            # Применяем маску к альфа-каналу с полностью бинарным подходом
            r, g, b, _ = rgba_img.split()
            
            # Создаем полностью бинарную альфу: либо 0, либо 255, без промежуточных значений
            # Это устраняет любые полупрозрачные пиксели, которые могут вызвать ореолы
            binary_mask = mask.point(lambda x: 0 if x == 0 else 255)
            final_a = binary_mask.point(lambda x: 0 if x > 0 else 255)  # Инвертируем для RGBA
            
            # Объединяем каналы обратно
            return Image.merge("RGBA", (r, g, b, final_a))
        elif img.mode == 'RGBA':
            # Если нет сохраненной маски, но есть альфа-канал, создаем бинарную прозрачность
            r, g, b, alpha = img.split()
            binary_alpha = alpha.point(lambda x: 255 if x > 240 else 0)
            return Image.merge("RGBA", (r, g, b, binary_alpha))
            
        return img
    except Exception as e:
        log.error(f"Error applying mask for PNG save: {e}")
        return img