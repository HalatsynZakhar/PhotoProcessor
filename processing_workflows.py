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

# Используем абсолютный импорт (если все файлы в одной папке)
import image_utils
import config_manager # Может понадобиться для дефолтных значений в редких случаях

try:
    from natsort import natsorted
except ImportError:
    logging.warning("Библиотека natsort не найдена. Сортировка будет стандартной.")
    natsorted = sorted

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


def _save_image(img, output_path, output_format, jpeg_quality, jpg_background_color=None):
    """(Helper) Сохраняет изображение в указанном формате с опциями."""
    if not img: log.error("! Cannot save None image."); return False
    if img.size[0] <= 0 or img.size[1] <= 0: log.error(f"! Cannot save zero-size image {img.size} to {output_path}"); return False
    
    # Проверяем и очищаем путь к файлу
    try:
        original_path = output_path
        # Получаем директорию и имя файла
        output_dir = os.path.dirname(output_path)
        filename = os.path.basename(output_path)
        
        # Проверяем наличие недопустимых символов в имени файла
        # Заменяем недопустимые символы в именах файлов Windows: \ / : * ? " < > |
        invalid_chars = r'[\\/:\*\?"<>\|]'
        safe_filename = re.sub(invalid_chars, '_', filename)
        
        # Проверяем на зарезервированные имена устройств в Windows
        reserved_names = ['CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 
                        'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2', 
                        'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9']
        
        filename_without_ext = os.path.splitext(safe_filename)[0].upper()
        if filename_without_ext in reserved_names:
            safe_filename = f"_{safe_filename}"
        
        # Собираем очищенный путь
        safe_output_path = os.path.join(output_dir, safe_filename)
        
        # Если путь изменился, логируем это
        if safe_output_path != original_path:
            log.warning(f"Unsafe filename detected. Changed from '{filename}' to '{safe_filename}'")
            output_path = safe_output_path
    except Exception as e:
        log.error(f"Error sanitizing filename: {e}")
        # Продолжаем с исходным путем
    
    log.info(f"  > Saving image to {output_path} (Format: {output_format.upper()})")
    log.debug(f"    Image details before save: Mode={img.mode}, Size={img.size}")
    
    try:
        save_options = {"optimize": True}
        img_to_save = img
        must_close_img_to_save = False
        
        if output_format == 'jpg':
            format_name = "JPEG"
            save_options["quality"] = int(jpeg_quality) # Убедимся что int
            save_options["subsampling"] = 0
            save_options["progressive"] = True
            if img.mode != 'RGB':
                log.warning(f"    Mode is {img.mode}, converting to RGB for JPEG save.")
                if jpg_background_color and img.mode in ('RGBA', 'LA', 'PA'):
                    # Create a new RGB image with the specified background color
                    rgb_image = Image.new("RGB", img.size, tuple(jpg_background_color))
                    # Paste the image onto the background
                    rgb_image.paste(img, (0, 0), img)
                    img_to_save = rgb_image
                else:
                    img_to_save = img.convert('RGB')
                    must_close_img_to_save = True
        elif output_format == 'png':
            format_name = "PNG"
            save_options["compress_level"] = 6
            # Для PNG важно сохранить прозрачность
            if img.mode != 'RGBA':
                log.warning(f"    Mode is {img.mode}, converting to RGBA for PNG save.")
                img_to_save = img.convert('RGBA')
                must_close_img_to_save = True
            # Убедимся, что альфа-канал сохранен
            if img_to_save.mode == 'RGBA':
                log.debug("    Preserving transparency for PNG save")
                save_options["optimize"] = False  # Отключаем оптимизацию для лучшего сохранения прозрачности
        else: log.error(f"! Unsupported output format for saving: {output_format}"); return False

        # Проверяем существование директории и создаем при необходимости
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
                log.info(f"Created output directory: {output_dir}")
            except Exception as e:
                log.error(f"Failed to create output directory: {e}")
                return False

        # Проверяем, не заблокирован ли файл
        try:
            if os.path.exists(output_path):
                # Пробуем открыть файл для записи, чтобы проверить, не заблокирован ли он
                with open(output_path, 'a'):
                    pass
        except PermissionError:
            # Файл заблокирован, используем временное имя
            base, ext = os.path.splitext(output_path)
            output_path = f"{base}_{int(time.time())}{ext}"
            log.warning(f"Original file is locked. Using alternative name: {os.path.basename(output_path)}")

        # Сохраняем файл
        try:
            img_to_save.save(output_path, format_name, **save_options)
        except OSError as e:
            # Особая обработка ошибок OSError
            if '[Errno 22]' in str(e) or 'Invalid argument' in str(e):
                # Пробуем с еще более безопасным именем файла
                base_dir = os.path.dirname(output_path)
                ext = os.path.splitext(output_path)[1]
                safe_path = os.path.join(base_dir, f"image_{int(time.time())}{ext}")
                log.warning(f"OSError when saving. Trying with generic filename: {os.path.basename(safe_path)}")
                img_to_save.save(safe_path, format_name, **save_options)
                output_path = safe_path  # Обновляем путь для логов
            else:
                # Пробрасываем другие ошибки OSError
                raise
                
        if must_close_img_to_save: image_utils.safe_close(img_to_save)
        log.info(f"    Successfully saved: {os.path.basename(output_path)}")
        return True
    except Exception as e:
        log.error(f"  ! Failed to save image {os.path.basename(output_path)}: {e}", exc_info=True)
        if os.path.exists(output_path):
            try: os.remove(output_path); log.warning(f"    Removed partially saved file: {os.path.basename(output_path)}")
            except Exception as del_err: log.error(f"    ! Failed to remove partially saved file: {del_err}")
        return False

# ==============================================================================
# === ОСНОВНАЯ ФУНКЦИЯ: ОБРАБОТКА ОТДЕЛЬНЫХ ФАЙЛОВ =============================
# ==============================================================================

def run_individual_processing(**all_settings: Dict[str, Any]) -> bool:
    """Обрабатывает отдельные файлы согласно настройкам."""
    try:
        log.info("--- Starting Individual File Processing ---")
        
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
        log.info(f"Article (Renaming): {'Enabled' if individual_mode_settings.get('enable_rename', False) else 'Disabled'}")
        log.info(f"Delete Originals: {individual_mode_settings.get('delete_originals', False)}")
        
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
                 f"Check Perimeter={background_crop_settings.get('check_perimeter', False)}")
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
                    except ImportError:
                        log.error("psd_tools library not installed. Cannot process PSD files.")
                        return False
                else:
                    processed_template = Image.open(template_path)
                    processed_template.load()
                    
                if process_template:
                    # Apply same processing steps to template
                    if preprocessing_settings.get('enable_preresize', False):
                        processed_template = _apply_preresize(processed_template, 
                                                         preprocessing_settings.get('preresize_width', 0),
                                                         preprocessing_settings.get('preresize_height', 0))
                    if whitening_settings.get('enable_whitening', False):
                        processed_template = image_utils.whiten_image_by_darkest_perimeter(processed_template, whitening_settings.get('whitening_cancel_threshold', 765))
                    if background_crop_settings.get('enable_bg_crop', False):
                        # Добавляем получение perimeter_mode из настроек
                        perimeter_mode = background_crop_settings.get('perimeter_mode', 'if_white')
                        processed_template = _apply_background_crop(
                            processed_template,
                            background_crop_settings.get('white_tolerance', 0),
                            background_crop_settings.get('perimeter_tolerance', 10),
                            background_crop_settings.get('crop_symmetric_absolute', False),
                            background_crop_settings.get('crop_symmetric_axes', False),
                            background_crop_settings.get('check_perimeter', False),
                            background_crop_settings.get('enable_crop', True),
                            perimeter_mode=perimeter_mode
                        )
                    if padding_settings.get('mode', 'never') != 'never':
                        processed_template = _apply_padding(processed_template, padding_settings)
                    if brightness_contrast_settings.get('enable_bc', False):
                        processed_template = image_utils.apply_brightness_contrast(processed_template, brightness_contrast_settings.get('brightness_factor', 1.0), brightness_contrast_settings.get('contrast_factor', 1.0))
                
                log.info(f"Template pre-processing completed. Size: {processed_template.size}, Mode: {processed_template.mode}")
            except Exception as e:
                log.error(f"Error pre-processing template: {e}")
                return False
        
        # Process each file
        for i, file_path in enumerate(files, 1):
            try:
                filename = os.path.basename(file_path)
                log.info(f"--- [{i}/{len(files)}] Processing: {filename} ---")
                
                # Load image
                img = Image.open(file_path)
                
                # Apply preprocessing steps
                if preprocessing_settings.get('enable_preresize', False):
                    img = _apply_preresize(img, 
                                         preprocessing_settings.get('preresize_width', 0),
                                         preprocessing_settings.get('preresize_height', 0))
                
                if whitening_settings.get('enable_whitening', False):
                    img = image_utils.whiten_image_by_darkest_perimeter(img, whitening_settings.get('whitening_cancel_threshold', 765))
                
                if background_crop_settings.get('enable_bg_crop', False):
                    # Добавляем получение perimeter_mode из настроек
                    perimeter_mode = background_crop_settings.get('perimeter_mode', 'if_white')
                    img = _apply_background_crop(
                        img,
                        background_crop_settings.get('white_tolerance', 0),
                        background_crop_settings.get('perimeter_tolerance', 10),
                        background_crop_settings.get('crop_symmetric_absolute', False),
                        background_crop_settings.get('crop_symmetric_axes', False),
                        background_crop_settings.get('check_perimeter', False),
                        background_crop_settings.get('enable_crop', True),
                        perimeter_mode=perimeter_mode
                    )
                
                if padding_settings.get('mode', 'never') != 'never':
                    img = _apply_padding(img, padding_settings)
                
                if brightness_contrast_settings.get('enable_bc', False):
                    img = image_utils.apply_brightness_contrast(img, brightness_contrast_settings.get('brightness_factor', 1.0), brightness_contrast_settings.get('contrast_factor', 1.0))
                
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
                        output_filename = f"{article}_{i}.{output_format}"
                else:
                    # Ensure the output filename has the correct extension
                    base_name = os.path.splitext(output_filename)[0]
                    output_filename = f"{base_name}.{output_format}"
                
                output_path = os.path.join(output_folder, output_filename)
                log.info(f"Saving image with format: {output_format.upper()}")
                if not _save_image(img, output_path, output_format, individual_mode_settings.get('jpeg_quality', 95), individual_mode_settings.get('jpg_background_color', [255, 255, 255])):
                    log.error(f"Failed to save image: {output_filename}")
                    log.info(f"--- Finished processing: {filename} (Failed) ---")
                    overall_success = False
                    continue
                
                # Backup original if enabled
                if backup_folder and individual_mode_settings.get('delete_originals', False):
                    try:
                        backup_path = os.path.join(backup_folder, filename)
                        shutil.copy2(file_path, backup_path)
                        os.remove(file_path)
                        log.info(f"Original backed up and deleted: {filename}")
                    except Exception as e:
                        log.error(f"Failed to backup/delete original: {e}")
                
                log.info(f"--- Finished processing: {filename} (Success) ---")
                
            except Exception as e:
                log.error(f"Error processing file {filename}: {e}")
                log.exception("Error details")
                overall_success = False
                continue
        
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
    (Preresize, Whitening, BG Removal, Padding, Brightness/Contrast)
    """
    log.debug(f"-- Starting processing for collage: {os.path.basename(image_path)}")
    img_current = None
    try:
        # 1. Открытие
        try:
            with Image.open(image_path) as img_opened: img_opened.load(); img_current = img_opened.convert('RGBA')
        except Exception as e: log.error(f"    ! Open/convert error: {e}"); return None
        if not img_current or img_current.size[0]<=0: log.error("    ! Zero size after open."); return None
        log.debug(f"    Opened RGBA Size: {img_current.size}")

        # 2. Пре-ресайз (если вкл)
        enable_preresize = prep_settings.get('enable_preresize', False)
        preresize_width = int(prep_settings.get('preresize_width', 0)) if enable_preresize else 0
        preresize_height = int(prep_settings.get('preresize_height', 0)) if enable_preresize else 0
        if enable_preresize: img_current = _apply_preresize(img_current, preresize_width, preresize_height)
        if not img_current: return None

        # 3. Отбеливание (если вкл)
        enable_whitening = white_settings.get('enable_whitening', False)
        whitening_cancel_threshold = int(white_settings.get('whitening_cancel_threshold', 765))  # Default to 765 (0% on slider)
        log.info(f"Whitening threshold from settings: {whitening_cancel_threshold}")
        
        # Проверяем и корректируем значение порога
        if whitening_cancel_threshold > 255 * 3:
            whitening_cancel_threshold = 765  # Значение по умолчанию в новой шкале
            log.info(f"Threshold too high, reset to default: {whitening_cancel_threshold}")
        elif whitening_cancel_threshold < 60:
            whitening_cancel_threshold = whitening_cancel_threshold * 3
            log.info(f"Converting old scale threshold to new scale: {whitening_cancel_threshold}")
        # Иначе оставляем как есть (вероятно уже конвертированное значение)
        if enable_whitening: img_current = image_utils.whiten_image_by_darkest_perimeter(img_current, whitening_cancel_threshold)
        if not img_current: return None

        # 4. Удаление фона/обрезка (если вкл)
        enable_bg_crop = bgc_settings.get('enable_bg_crop', False)
        white_tolerance = int(bgc_settings.get('white_tolerance', 0)) if enable_bg_crop else None
        perimeter_tolerance = int(bgc_settings.get('perimeter_tolerance', 10)) if enable_bg_crop else None
        crop_symmetric_absolute = bool(bgc_settings.get('crop_symmetric_absolute', False)) if enable_bg_crop else False
        crop_symmetric_axes = bool(bgc_settings.get('crop_symmetric_axes', False)) if enable_bg_crop else False
        check_perimeter = bool(bgc_settings.get('check_perimeter', True)) if enable_bg_crop else False
        enable_crop = bool(bgc_settings.get('enable_crop', True)) if enable_bg_crop else False
        perimeter_mode = str(bgc_settings.get('perimeter_mode', 'if_white')) if enable_bg_crop else 'if_white'

        if enable_bg_crop:
            img_current = _apply_background_crop(
                img_current,
                white_tolerance=white_tolerance,
                perimeter_tolerance=perimeter_tolerance,
                force_absolute_symmetry=crop_symmetric_absolute,
                force_axes_symmetry=crop_symmetric_axes,
                check_perimeter=check_perimeter,
                enable_crop=enable_crop,
                perimeter_mode=perimeter_mode
            )

        # 5. Добавление полей (если вкл)
        enable_padding = pad_settings.get('mode', 'never') != 'never' # Включено, если не равно 'never'
        padding_mode = pad_settings.get('mode', 'never')
        padding_percent = float(pad_settings.get('padding_percent', 0.0)) if enable_padding else 0.0
        perimeter_check_tolerance = int(pad_settings.get('perimeter_check_tolerance', 10)) if enable_padding else 0
        # perimeter_margin removed, always using 1 px margin
        
        # Определяем, нужно ли добавлять поля
        apply_padding = False
        if enable_padding:
            if padding_mode == 'always':
                # Всегда добавляем поля
                apply_padding = True
                log.debug(f"    Padding will be applied (mode: always).")
            else:
                # Режимы if_white или if_not_white - нужна проверка периметра
                # Используем специальный допуск для проверки периметра
                perimeter_is_white = image_utils.check_perimeter_is_white(
                    img_current, perimeter_check_tolerance, 1)
                
                if padding_mode == 'if_white' and perimeter_is_white:
                    apply_padding = True
                    log.debug(f"    Padding will be applied (mode: if_white, perimeter IS white).")
                elif padding_mode == 'if_not_white' and not perimeter_is_white:
                    apply_padding = True
                    log.debug(f"    Padding will be applied (mode: if_not_white, perimeter is NOT white).")
                else:
                    log.debug(f"    Padding skipped: Perimeter condition not met for mode '{padding_mode}'.")
        
        # Применяем padding только если нужно
        if apply_padding:
            img_current = image_utils.add_padding(img_current, padding_percent, allow_expansion)
            if not img_current: return None
            log.debug(f"    Padding applied. New size: {img_current.size}")
        elif enable_padding:
            log.debug(f"    Padding skipped based on conditions.")
        else:
            log.debug(f"    Padding disabled (mode: never).")

        # === ЯРКОСТЬ И КОНТРАСТ (для отдельных фото перед коллажом) ===
        if bc_settings.get('enable_bc'):
            log.debug("  Applying Brightness/Contrast to individual image for collage...")
            img_current = image_utils.apply_brightness_contrast(
                img_current, 
                brightness_factor=bc_settings.get('brightness_factor', 1.0),
                contrast_factor=bc_settings.get('contrast_factor', 1.0)
            )
            if not img_current: 
                log.warning(f"  Brightness/contrast failed for collage image.")
                return None # Не можем продолжить, если Я/К вернула None
        # =============================================================
        
        # Проверка RGBA (для коллажа нужен RGBA)
        if img_current.mode != 'RGBA':
             try: img_tmp = img_current.convert("RGBA"); image_utils.safe_close(img_current); img_current = img_tmp
             except Exception as e: log.error(f"    ! Final RGBA conversion failed: {e}"); return None

        log.debug(f"-- Finished processing for collage: {os.path.basename(image_path)}")
        return img_current

    except Exception as e:
        log.critical(f"!!! UNEXPECTED error in _process_image_for_collage for {os.path.basename(image_path)}: {e}", exc_info=True)
        image_utils.safe_close(img_current)
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
    try:
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

        proportional_placement = coll_settings.get('proportional_placement', False)
        placement_ratios = coll_settings.get('placement_ratios', [1.0])
        forced_cols = int(coll_settings.get('forced_cols', 0))
        spacing_percent = float(coll_settings.get('spacing_percent', 2.0))
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
    log.info(f"Output Format: {output_format.upper()}")
    if output_format == 'jpg': log.info(f"  JPG Bg: {valid_jpg_bg}, Quality: {jpeg_quality}")
    log.info("-" * 10 + " Base Image Processing " + "-" * 10)
    log.info(f"Preresize: {'Enabled' if prep_settings.get('enable_preresize') else 'Disabled'}")
    log.info(f"Whitening: {'Enabled' if white_settings.get('enable_whitening') else 'Disabled'}")
    log.info(f"BG Removal/Crop: {'Enabled' if bgc_settings.get('enable_bg_crop') else 'Disabled'}")
    if bgc_settings.get('enable_bg_crop'): 
        log.info(f"  Crop: {'Enabled' if bgc_settings.get('enable_crop', True) else 'Disabled'}, "
                 f"Symmetry: Abs={bgc_settings.get('force_absolute_symmetry', False)}, "
                 f"Axes={bgc_settings.get('force_axes_symmetry', False)}, "
                 f"Check Perimeter={bgc_settings.get('check_perimeter', False)}")
    log.info(f"Padding: {'Enabled' if pad_settings.get('mode') != 'never' else 'Disabled'}")
    log.info("-" * 10 + " Collage Assembly " + "-" * 10)
    log.info(f"Proportional Placement: {proportional_placement} (Ratios: {placement_ratios if proportional_placement else 'N/A'})")
    log.info(f"Columns: {forced_cols if forced_cols > 0 else 'Auto'}")
    log.info(f"Spacing: {spacing_percent}%")
    log.info(f"Force Aspect Ratio: {str(valid_collage_aspect_ratio) or 'Disabled'}")
    log.info(f"Max Dimensions: W:{max_collage_width or 'N/A'}, H:{max_collage_height or 'N/A'}")
    log.info(f"Final Exact Canvas: W:{final_collage_exact_width or 'N/A'}, H:{final_collage_exact_height or 'N/A'}")
    log.info("-" * 25)

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
    for idx, path in enumerate(input_files_sorted):
        log.info(f"-> Processing {idx+1}/{total_files_coll}: {os.path.basename(path)}")
        # Передаем настройки Я/К в _process_image_for_collage
        processed = _process_image_for_collage(
            image_path=path,
            prep_settings=prep_settings,
            white_settings=white_settings,
            bgc_settings=bgc_settings,
            pad_settings=pad_settings,
            bc_settings=bc_settings 
        )
        if processed: processed_images.append(processed)
        else: log.warning(f"  Skipping {os.path.basename(path)} due to processing errors.")

    num_processed = len(processed_images)
    if num_processed == 0:
        log.error("No images successfully processed for collage.")
        log.info(">>> Exiting: No images were successfully processed.")
        # Важно: Нужно очистить память от непроцессированных файлов, если они остались
        for img in processed_images: image_utils.safe_close(img)
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
                    temp_img = img.resize((nw, nh), Image.Resampling.LANCZOS)
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
    
    # Рассчитываем отступы на основе масштабированных размеров
    spacing_px_h = int(round(max_w_scaled * (spacing_percent / 100.0)))
    spacing_px_v = int(round(max_h_scaled * (spacing_percent / 100.0)))
    
    # Рассчитываем итоговый размер холста
    canvas_width = (grid_cols * max_w_scaled) + ((grid_cols + 1) * spacing_px_h)
    canvas_height = (grid_rows * max_h_scaled) + ((grid_rows + 1) * spacing_px_v)
    log.debug(f"  Grid: {grid_rows}x{grid_cols}, Max Scaled Cell: {max_w_scaled}x{max_h_scaled}, Space H/V: {spacing_px_h}/{spacing_px_v}, Final Canvas: {canvas_width}x{canvas_height}")

    collage_canvas = None; final_collage = None
    try:
        collage_canvas = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))
        log.debug(f"    Canvas created: {repr(collage_canvas)}") 
        current_idx = 0
        for r in range(grid_rows):
            for c in range(grid_cols):
                if current_idx >= num_final_images: break 
                img = scaled_images[current_idx]
                if img and img.width > 0 and img.height > 0:
                     # Позиция верхнего левого угла ячейки
                     cell_x = spacing_px_h + c * (max_w_scaled + spacing_px_h)
                     cell_y = spacing_px_v + r * (max_h_scaled + spacing_px_v)
                     # Центрируем изображение внутри ячейки
                     paste_x = cell_x + (max_w_scaled - img.width) // 2
                     paste_y = cell_y + (max_h_scaled - img.height) // 2
                     try: 
                         # Убедимся, что изображение в RGBA для корректной вставки с маской
                         img_to_paste = img if img.mode == 'RGBA' else img.convert('RGBA')
                         collage_canvas.paste(img_to_paste, (paste_x, paste_y), mask=img_to_paste)
                         if img_to_paste is not img: image_utils.safe_close(img_to_paste) # Закрываем временное RGBA
                     except Exception as e_paste: 
                         log.error(f"  ! Error pasting image {current_idx+1}: {e_paste}")
                         # Закрываем временное RGBA, если оно было создано при ошибке
                         if 'img_to_paste' in locals() and img_to_paste is not img: image_utils.safe_close(img_to_paste)
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
        save_successful = _save_image(final_collage, output_file_path, output_format, jpeg_quality, valid_jpg_bg)
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
    
    # Получаем настройки
    position = settings.get('position', 'center')
    template_position = settings.get('template_position', 'center')  # Новая настройка для позиции шаблона
    template_on_top = settings.get('template_on_top', True)
    no_scaling = settings.get('no_scaling', False)
    width_ratio = settings.get('width_ratio', [1.0, 1.0])
    enable_width_ratio = settings.get('enable_width_ratio', False)
    fit_image_to_template = settings.get('fit_image_to_template', False)
    fit_template_to_image = settings.get('fit_template_to_image', False)
    
    # Конвертируем изображения в RGBA если нужно
    if image.mode != 'RGBA':
        image = image.convert('RGBA')
    if template.mode != 'RGBA':
        template = template.convert('RGBA')
    
    # Получаем размеры
    image_width, image_height = image.size
    template_width, template_height = template.size
    
    log.info(f"  > Original sizes - Image: {image_width}x{image_height}, Template: {template_width}x{template_height}")
    
    # Определяем размеры холста и масштабирование
    canvas_width = max(image_width, template_width)
    canvas_height = max(image_height, template_height)
    scaled_image = image
    scaled_template = template
    
    if no_scaling:
        # В режиме без масштабирования используем максимальные размеры
        log.info("  > No scaling mode - Using maximum sizes")
        log.info(f"  > Canvas size: {canvas_width}x{canvas_height}")
    elif enable_width_ratio:
        # Вычисляем целевые размеры на основе соотношения площадей
        # Если соотношение 1:2, то площадь второго изображения должна быть в 2 раза больше
        target_area_ratio = width_ratio[0] / width_ratio[1]  # Соотношение площадей
        
        # Вычисляем площади
        image_area = image_width * image_height
        template_area = template_width * template_height
        
        # Определяем, какое изображение нужно увеличить
        if image_area < template_area * target_area_ratio:
            # Нужно увеличить изображение
            target_area = template_area * target_area_ratio
            area_scale = math.sqrt(target_area / image_area)
            
            new_width = int(image_width * area_scale)
            new_height = int(image_height * area_scale)
            
            # Масштабируем изображение
            scaled_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Обновляем размеры холста
            canvas_width = max(template_width, new_width)
            canvas_height = max(template_height, new_height)
            
            log.info(f"  > Width ratio enabled - Scaling image to match area ratio {target_area_ratio:.2f}")
            log.info(f"  > Area-based scale factor: {area_scale:.3f}, New size: {new_width}x{new_height}")
        else:
            # Нужно увеличить шаблон
            target_area = image_area / target_area_ratio
            area_scale = math.sqrt(target_area / template_area)
            
            new_width = int(template_width * area_scale)
            new_height = int(template_height * area_scale)
            
            # Масштабируем шаблон
            scaled_template = template.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Обновляем размеры холста
            canvas_width = max(image_width, new_width)
            canvas_height = max(image_height, new_height)
            
            log.info(f"  > Width ratio enabled - Scaling template to match area ratio {target_area_ratio:.2f}")
            log.info(f"  > Area-based scale factor: {area_scale:.3f}, New size: {new_width}x{new_height}")
        
        log.info(f"  > Canvas size: {canvas_width}x{canvas_height}")
    elif fit_image_to_template:
        # Масштабируем изображение, чтобы оно помещалось в шаблон
        # Если изображение меньше шаблона, увеличиваем его
        if image_width < template_width and image_height < template_height:
            # Изображение меньше шаблона, увеличиваем его
            width_ratio = template_width / image_width
            height_ratio = template_height / image_height
            scale_factor = max(width_ratio, height_ratio)  # Используем max для максимального увеличения
            
            new_width = int(image_width * scale_factor)
            new_height = int(image_height * scale_factor)
            
            # Масштабируем изображение
            scaled_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Обновляем размеры холста
            canvas_width = max(template_width, new_width)
            canvas_height = max(template_height, new_height)
            
            log.info(f"  > Fit image to template - Image is smaller, scaling up with factor: {scale_factor:.3f}")
            log.info(f"  > New image size: {new_width}x{new_height}")
        else:
            # Изображение больше шаблона, увеличиваем шаблон
            width_ratio = image_width / template_width
            height_ratio = image_height / template_height
            scale_factor = max(width_ratio, height_ratio)  # Используем max для максимального увеличения
            
            new_width = int(template_width * scale_factor)
            new_height = int(template_height * scale_factor)
            
            # Масштабируем шаблон
            scaled_template = template.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Обновляем размеры холста
            canvas_width = max(image_width, new_width)
            canvas_height = max(image_height, new_height)
            
            log.info(f"  > Fit image to template - Template is smaller, scaling up with factor: {scale_factor:.3f}")
            log.info(f"  > New template size: {new_width}x{new_height}")
        
        log.info(f"  > Canvas size: {canvas_width}x{canvas_height}")
    elif fit_template_to_image:
        # Масштабируем шаблон, чтобы он помещался в изображение
        # Если шаблон меньше изображения, увеличиваем его
        if template_width < image_width and template_height < image_height:
            # Шаблон меньше изображения, увеличиваем его
            width_ratio = image_width / template_width
            height_ratio = image_height / template_height
            scale_factor = max(width_ratio, height_ratio)  # Используем max для максимального увеличения
            
            new_width = int(template_width * scale_factor)
            new_height = int(template_height * scale_factor)
            
            # Масштабируем шаблон
            scaled_template = template.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Обновляем размеры холста
            canvas_width = max(image_width, new_width)
            canvas_height = max(image_height, new_height)
            
            log.info(f"  > Fit template to image - Template is smaller, scaling up with factor: {scale_factor:.3f}")
            log.info(f"  > New template size: {new_width}x{new_height}")
        else:
            # Шаблон больше изображения, увеличиваем изображение
            width_ratio = template_width / image_width
            height_ratio = template_height / image_height
            scale_factor = max(width_ratio, height_ratio)  # Используем max для максимального увеличения
            
            new_width = int(image_width * scale_factor)
            new_height = int(image_height * scale_factor)
            
            # Масштабируем изображение
            scaled_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Обновляем размеры холста
            canvas_width = max(template_width, new_width)
            canvas_height = max(template_height, new_height)
            
            log.info(f"  > Fit template to image - Image is smaller, scaling up with factor: {scale_factor:.3f}")
            log.info(f"  > New image size: {new_width}x{new_height}")
        
        log.info(f"  > Canvas size: {canvas_width}x{canvas_height}")
    
    # Создаем холст нужного размера
    canvas = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))
    log.info(f"  > Created canvas: {canvas_width}x{canvas_height}")
    
    # Вычисляем позиции для размещения
    image_pos = _calculate_paste_position(scaled_image.size, canvas.size, position)
    template_pos = _calculate_template_position(scaled_template.size, canvas.size, template_position)  # Используем специальную функцию для шаблона
    
    # Размещаем изображения в зависимости от порядка
    if template_on_top:
        canvas.paste(scaled_image, image_pos, scaled_image if scaled_image.mode == 'RGBA' else None)
        canvas.paste(scaled_template, template_pos, scaled_template if scaled_template.mode == 'RGBA' else None)
    else:
        canvas.paste(scaled_template, template_pos, scaled_template if scaled_template.mode == 'RGBA' else None)
        canvas.paste(scaled_image, image_pos, scaled_image if scaled_image.mode == 'RGBA' else None)
    
    log.info(f"  > Image position: {image_pos}")
    log.info(f"  > Template position: {template_pos}")
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
    """Находит все изображения в указанной папке."""
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

        log.info(f"Found {len(files)} image files")
        return files

    except Exception as e:
        log.exception("Error in get_image_files")
        return []

def _apply_background_crop(img: Image.Image, white_tolerance: int = 10, perimeter_tolerance: int = 10, 
                         force_absolute_symmetry: bool = False, force_axes_symmetry: bool = False, 
                         check_perimeter: bool = False, enable_crop: bool = True,
                         perimeter_mode: str = 'if_white') -> Optional[Image.Image]:
    """
    Applies background removal and cropping to an image based on provided parameters.
    
    Args:
        img: The image to process
        white_tolerance: Tolerance for white color detection
        perimeter_tolerance: Tolerance for perimeter check
        force_absolute_symmetry: Whether to force absolute symmetry
        force_axes_symmetry: Whether to force axes symmetry
        check_perimeter: Whether to check perimeter before processing
        enable_crop: Whether to enable cropping (new setting)
        perimeter_mode: Mode for perimeter check ('if_white' or 'if_not_white')
        
    Returns:
        Processed image or None if processing fails
    """
    try:
        log.info("=== Processing background and crop ===")
        log.info(f"Perimeter mode: {perimeter_mode}")
        
        # Convert to RGBA if needed
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # Check perimeter - результат определяет оба процесса (удаление фона и обрезку)
        should_proceed = True  # По умолчанию выполняем оба действия, если периметр не проверяется
        if check_perimeter:
            log.debug(f"Checking if image perimeter is white (tolerance: {perimeter_tolerance})")
            perimeter_is_white = image_utils.check_perimeter_is_white(img, perimeter_tolerance, 1)
            
            # Use the selected perimeter mode
            if perimeter_mode == 'if_white':
                # Process image if perimeter IS white
                if not perimeter_is_white:
                    log.info("Processing skipped: perimeter is NOT white (mode: if_white)")
                    should_proceed = False
                else:
                    log.info("Processing will proceed: perimeter IS white (mode: if_white)")
            elif perimeter_mode == 'if_not_white':
                # Process image if perimeter is NOT white
                if perimeter_is_white:
                    log.info("Processing skipped: perimeter IS white (mode: if_not_white)")
                    should_proceed = False
                else:
                    log.info("Processing will proceed: perimeter is NOT white (mode: if_not_white)")
            else:
                log.warning(f"Unknown perimeter mode: {perimeter_mode}. Defaulting to if_white.")
                if not perimeter_is_white:
                    log.info("Processing skipped: perimeter is NOT white (default if_white)")
                    should_proceed = False
        
        # Если периметр соответствует выбранному режиму - выполняем оба процесса
        if should_proceed:
            # Remove background if needed
            img_processed = img
            # Удаляем фон независимо от обрезки
            img_processed = image_utils.remove_white_background(img_processed, white_tolerance)
            if not img_processed:
                log.error("Failed to remove background")
                return None
            
            # Apply cropping as separate step
            if enable_crop:
                log.info("Cropping is enabled, applying crop")
                img_processed = image_utils.crop_image(img_processed, force_axes_symmetry, force_absolute_symmetry)
                if not img_processed:
                    log.error("Failed to crop image")
                    return None
            else:
                log.info("Cropping is disabled, keeping canvas size")
                
            return img_processed
        else:
            log.info("Background removal and cropping were skipped based on perimeter check")
            return img
        
    except Exception as e:
        log.error(f"Error in _apply_background_crop: {e}")
        log.exception("Error details")
        return None

def _scale_image(image, target_size, mode='fit'):
    """
    Масштабирует изображение с сохранением пропорций.
    
    Args:
        image: Исходное изображение
        target_size: Целевой размер (ширина, высота)
        mode: Режим масштабирования ('fit' - вписать в размер, 'fill' - заполнить размер)
        
    Returns:
        Image: Масштабированное изображение
    """
    source_width, source_height = image.size
    target_width, target_height = target_size
    
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
    
    # Масштабируем изображение
    return image.resize((new_width, new_height), Image.Resampling.LANCZOS)

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
        
    # Находим максимальные размеры среди всех изображений
    max_width = max(img.width for img in images)
    max_height = max(img.height for img in images)
    
    # Рассчитываем коэффициенты масштабирования для каждого изображения
    # (чтобы все вписались в max_width x max_height)
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
    # Это устанавливает базовый размер перед применением соотношений
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

    # Применяем пользовательские соотношения размеров, если они заданы
    # Используем прямой доступ к ключам в словаре settings ('collage_mode')
    if settings.get('proportional_placement', False):
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
         log.debug("  Proportional placement disabled.")

    # Рассчитываем итоговые размеры коллажа на основе масштабированных размеров
    # (Это определяет размер ячейки сетки)
    scaled_widths = [img.width * scale for img, scale in zip(images, scale_factors)]
    scaled_heights = [img.height * scale for img, scale in zip(images, scale_factors)]
    
    # Базовый размер - максимальный из масштабированных
    final_max_width = max(int(round(w)) for w in scaled_widths if w > 0) if any(w > 0 for w in scaled_widths) else 1
    final_max_height = max(int(round(h)) for h in scaled_heights if h > 0) if any(h > 0 for h in scaled_heights) else 1
    
    log.debug(f"  Calculated max scaled dimensions for grid cell: {final_max_width}x{final_max_height}")

    # Применяем ограничения максимального размера КОЛЛАЖА, если они заданы
    # Используем прямой доступ к ключам
    final_scale_adjustment = 1.0
    if settings.get('enable_max_dimensions', False):
        max_width_limit = settings.get('max_collage_width', 0)
        max_height_limit = settings.get('max_collage_height', 0)
        
        # Оцениваем примерный размер коллажа (нужны колонки)
        forced_cols = settings.get('forced_cols', 0)
        num_final_images = len(images)
        grid_cols = forced_cols if forced_cols > 0 else max(1, int(math.ceil(math.sqrt(num_final_images))))
        grid_rows = max(1, int(math.ceil(num_final_images / grid_cols))) 
        # Упрощенная оценка без учета spacing
        estimated_width = grid_cols * final_max_width
        estimated_height = grid_rows * final_max_height
        log.debug(f"  Estimated collage size for max dimension check: {estimated_width}x{estimated_height} (Grid: {grid_rows}x{grid_cols})")
        
        apply_limit = False
        limit_scale = 1.0
        if max_width_limit > 0 and estimated_width > max_width_limit:
            limit_scale = min(limit_scale, max_width_limit / estimated_width)
            apply_limit = True
        if max_height_limit > 0 and estimated_height > max_height_limit:
             limit_scale = min(limit_scale, max_height_limit / estimated_height)
             apply_limit = True

        if apply_limit and limit_scale < 1.0:
            log.info(f"  Applying max dimension limit. Scaling down by {limit_scale:.3f}")
            final_max_width = int(round(final_max_width * limit_scale))
            final_max_height = int(round(final_max_height * limit_scale))
            # Корректируем ИТОГОВЫЕ коэффициенты масштабирования
            final_scale_adjustment = limit_scale
            scale_factors = [factor * limit_scale for factor in scale_factors]
            log.info(f"  > Scale factors after max dimension limit: {scale_factors}")
        else:
             log.debug("  Max dimension limit not exceeded or not enabled.")

    # Возвращаем максимальные размеры ячейки (для построения сетки) и финальные факторы масштабирования
    return final_max_width, final_max_height, scale_factors

def _apply_padding(img, pad_settings):
    """
    Применяет отступы к изображению на основе настроек.
    
    Args:
        img: Изображение для обработки
        pad_settings: Словарь настроек отступов
    
    Returns:
        Обработанное изображение или None в случае ошибки
    """
    if not img:
        return None
        
    # Проверяем, включены ли отступы
    enable_padding = pad_settings.get('mode', 'never') != 'never'
    if not enable_padding:
        return img
        
    padding_mode = pad_settings.get('mode', 'never')
    padding_percent = float(pad_settings.get('padding_percent', 0.0))
    perimeter_check_tolerance = int(pad_settings.get('perimeter_check_tolerance', 10))
    # Добавляем получение параметра allow_expansion из настроек
    allow_expansion = bool(pad_settings.get('allow_expansion', True))
    
    # Определяем, нужно ли добавлять поля
    apply_padding = False
    if padding_mode == 'always':
        apply_padding = True
        log.debug(f"    Padding will be applied (mode: always).")
    else:
        # Режимы if_white или if_not_white - нужна проверка периметра
        perimeter_is_white = image_utils.check_perimeter_is_white(img, perimeter_check_tolerance, 1)
        
        if padding_mode == 'if_white' and perimeter_is_white:
            apply_padding = True
            log.debug(f"    Padding will be applied (mode: if_white, perimeter IS white).")
        elif padding_mode == 'if_not_white' and not perimeter_is_white:
            apply_padding = True
            log.debug(f"    Padding will be applied (mode: if_not_white, perimeter is NOT white).")
        else:
            log.debug(f"    Padding skipped: Perimeter condition not met for mode '{padding_mode}'.")
    
    # Применяем padding только если нужно
    if apply_padding:
        img = image_utils.add_padding(img, padding_percent, allow_expansion)
        if img:
            log.debug(f"    Padding applied. New size: {img.size}")
        else:
            log.error("    Failed to apply padding")
    else:
        log.debug(f"    Padding skipped based on conditions.")
    
    return img