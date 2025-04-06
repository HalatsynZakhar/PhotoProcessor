# processing_workflows.py
import time
import os
import shutil
import math
import logging
import traceback
import gc # Для сборки мусора при MemoryError
from typing import Dict, Any, Optional, Tuple, List
import uuid

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


def _save_image(img, output_path, output_format, jpeg_quality):
    """(Helper) Сохраняет изображение в указанном формате с опциями."""
    if not img: log.error("! Cannot save None image."); return False
    # ... (код функции _save_image из предыдущего ответа, с log.*) ...
    if img.size[0] <= 0 or img.size[1] <= 0: log.error(f"! Cannot save zero-size image {img.size} to {output_path}"); return False
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
                img_to_save = img.convert('RGB')
                must_close_img_to_save = True
        elif output_format == 'png':
            format_name = "PNG"
            save_options["compress_level"] = 6
            if img.mode != 'RGBA':
                log.warning(f"    Mode is {img.mode}, converting to RGBA for PNG save.")
                img_to_save = img.convert('RGBA')
                must_close_img_to_save = True
        else: log.error(f"! Unsupported output format for saving: {output_format}"); return False

        img_to_save.save(output_path, format_name, **save_options)
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

def run_individual_processing(**all_settings: Dict[str, Any]):
    """
    Оркестрирует обработку отдельных файлов: поиск, цикл, вызов image_utils,
    сохранение, переименование, удаление.
    Принимает все настройки как один словарь.
    """
    log.info("--- Starting Individual File Processing ---")
    start_time = time.time()

    # --- 1. Извлечение и Валидация Параметров ---
    log.debug("Extracting settings for individual mode...")
    try:
        paths_settings = all_settings.get('paths', {})
        prep_settings = all_settings.get('preprocessing', {})
        white_settings = all_settings.get('whitening', {})
        bgc_settings = all_settings.get('background_crop', {})
        pad_settings = all_settings.get('padding', {})
        bc_settings = all_settings.get('brightness_contrast', {})
        ind_settings = all_settings.get('individual_mode', {})

        input_path = paths_settings.get('input_folder_path')
        output_path = paths_settings.get('output_folder_path')
        backup_folder_path = paths_settings.get('backup_folder_path')

        article_name = ind_settings.get('article_name') # Может быть '' или None
        delete_originals = ind_settings.get('delete_originals', False)
        output_format = str(ind_settings.get('output_format', 'jpg')).lower()
        jpeg_quality = int(ind_settings.get('jpeg_quality', 95))
        jpg_background_color = ind_settings.get('jpg_background_color', [255, 255, 255])
        force_aspect_ratio = ind_settings.get('force_aspect_ratio') # None или [W, H]
        max_output_width = int(ind_settings.get('max_output_width', 0))
        max_output_height = int(ind_settings.get('max_output_height', 0))
        final_exact_width = int(ind_settings.get('final_exact_width', 0))
        final_exact_height = int(ind_settings.get('final_exact_height', 0))

        enable_preresize = prep_settings.get('enable_preresize', False)
        preresize_width = int(prep_settings.get('preresize_width', 0)) if enable_preresize else 0
        preresize_height = int(prep_settings.get('preresize_height', 0)) if enable_preresize else 0

        enable_whitening = white_settings.get('enable_whitening', False)
        whitening_cancel_threshold = int(white_settings.get('whitening_cancel_threshold', 550))

        enable_bg_crop = bgc_settings.get('enable_bg_crop', False)
        white_tolerance = int(bgc_settings.get('white_tolerance', 0)) if enable_bg_crop else None # None если выключено
        crop_symmetric_absolute = bool(bgc_settings.get('crop_symmetric_absolute', False)) if enable_bg_crop else False
        crop_symmetric_axes = bool(bgc_settings.get('crop_symmetric_axes', False)) if enable_bg_crop else False

        enable_padding = pad_settings.get('enable_padding', False)
        padding_percent = float(pad_settings.get('padding_percent', 0.0)) if enable_padding else 0.0
        perimeter_margin = int(pad_settings.get('perimeter_margin', 0)) if enable_padding else 0
        allow_expansion = bool(pad_settings.get('allow_expansion', True)) if enable_padding else False

        # Дополнительная валидация
        if output_format not in ['jpg', 'png']:
            raise ValueError(f"Unsupported output format: {output_format}")
        if not input_path or not output_path:
            raise ValueError("Input or Output path is missing.")

        # Конвертация в кортежи где нужно
        valid_jpg_bg = tuple(jpg_background_color) if isinstance(jpg_background_color, list) else (255, 255, 255)
        valid_aspect_ratio = tuple(force_aspect_ratio) if force_aspect_ratio else None

        log.debug("Settings extracted successfully.")

    except (KeyError, ValueError, TypeError) as e:
        log.critical(f"Error processing settings: {e}. Aborting.", exc_info=True)
        return

    # --- 2. Подготовка Путей и Папок ---
    abs_input_path = os.path.abspath(input_path)
    abs_output_path = os.path.abspath(output_path)
    abs_backup_path = os.path.abspath(backup_folder_path) if backup_folder_path and str(backup_folder_path).strip() else None

    if not os.path.isdir(abs_input_path):
        log.error(f"Input path is not a valid directory: {abs_input_path}"); return

    backup_enabled = False
    if abs_backup_path:
        if abs_backup_path == abs_input_path or abs_backup_path == abs_output_path:
             log.warning(f"Backup path is same as input/output. Disabling backup.")
        else:
             try:
                 if not os.path.exists(abs_backup_path): os.makedirs(abs_backup_path); log.info(f"Created backup dir: {abs_backup_path}")
                 elif not os.path.isdir(abs_backup_path): log.error(f"Backup path not a directory: {abs_backup_path}");
                 else: backup_enabled = True # Папка существует и это директория
             except Exception as e: log.error(f"Error creating backup dir {abs_backup_path}: {e}")

    safe_to_delete = abs_input_path != abs_output_path
    effective_delete_originals = delete_originals and safe_to_delete
    if delete_originals and not safe_to_delete: log.warning("Deletion disabled: input/output paths are same.")

    try: # Создание папки результатов
        if not os.path.exists(abs_output_path): os.makedirs(abs_output_path); log.info(f"Created output dir: {abs_output_path}")
        elif not os.path.isdir(abs_output_path): log.error(f"Output path not a directory: {abs_output_path}"); return
    except Exception as e: log.error(f"Error creating output dir {abs_output_path}: {e}"); return

    # --- 3. Логирование параметров ---
    log.info("--- Processing Parameters (Individual Mode) ---")
    log.info(f"Input Path: {abs_input_path}")
    log.info(f"Output Path: {abs_output_path}")
    log.info(f"Backup Path: {abs_backup_path if backup_enabled else 'Disabled'}")
    log.info(f"Article (Renaming): {article_name or 'Disabled'}")
    log.info(f"Delete Originals: {effective_delete_originals}")
    log.info(f"Output Format: {output_format.upper()}")
    if output_format == 'jpg': log.info(f"  JPG Bg: {valid_jpg_bg}, Quality: {jpeg_quality}")
    log.info("---------- Steps ----------")
    log.info(f"1. Preresize: {'Enabled' if enable_preresize else 'Disabled'} (W:{preresize_width}, H:{preresize_height})")
    log.info(f"2. Whitening: {'Enabled' if enable_whitening else 'Disabled'} (Thresh:{whitening_cancel_threshold})")
    log.info(f"3. BG Removal/Crop: {'Enabled' if enable_bg_crop else 'Disabled'} (Tol:{white_tolerance})")
    if enable_bg_crop: log.info(f"  Crop Symmetry: Abs={crop_symmetric_absolute}, Axes={crop_symmetric_axes}")
    log.info(f"4. Padding: {'Enabled' if enable_padding else 'Disabled'}" + 
             (f" (%:{padding_percent:.1f}, Margin:{perimeter_margin}, Expand:{allow_expansion})" if pad_settings.get('enable_padding') else ""))
    
    # === ВОССТАНОВЛЕН ВЫЗОВ ЯРКОСТИ И КОНТРАСТА ===
    enable_bc = bc_settings.get('enable_bc', False)
    log.info(f"5. Brightness/Contrast: {'Enabled' if enable_bc else 'Disabled'}" + 
             (f" (B:{bc_settings.get('brightness_factor', 1.0):.2f}, C:{bc_settings.get('contrast_factor', 1.0):.2f})" if enable_bc else ""))
    # ===================================
    
    # === УЛУЧШЕНО ЛОГИРОВАНИЕ ФЛАГА ===
    enable_ratio_log = ind_settings.get('enable_force_aspect_ratio', False)
    ratio_value_log = ind_settings.get('force_aspect_ratio')
    log.info(f"6. Force Aspect Ratio: {'Enabled' if enable_ratio_log else 'Disabled'} " + 
             (f"(Value: {ratio_value_log})" if enable_ratio_log and ratio_value_log else ""))
    # ==================================
    
    log.info(f"7. Max Dimensions: W:{max_output_width or 'N/A'}, H:{max_output_height or 'N/A'}")
    log.info(f"8. Final Exact Canvas: W:{final_exact_width or 'N/A'}, H:{final_exact_height or 'N/A'}")
    log.info("-------------------------")

    # --- 4. Поиск Файлов ---
    try:
        all_entries = os.listdir(abs_input_path)
        SUPPORTED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff', '.webp', '.tif')
        files_unsorted = [f for f in all_entries if os.path.isfile(os.path.join(abs_input_path, f)) and not f.startswith(("__temp_", ".")) and f.lower().endswith(SUPPORTED_EXTENSIONS)]
        files = natsorted(files_unsorted)
        log.info(f"Found {len(files)} files to process.")
        if not files: return
    except Exception as e: log.error(f"Error reading input directory {abs_input_path}: {e}"); return

    # --- 5. Инициализация для Цикла ---
    processed_files_count = 0; skipped_files_count = 0; error_files_count = 0
    source_files_to_potentially_delete = []
    processed_output_file_map = {} # {final_output_path: original_basename}
    output_ext = f".{output_format}"

    # --- 6. Основной Цикл Обработки ---
    total_files = len(files)
    for file_index, file in enumerate(files):
        source_file_path = os.path.join(abs_input_path, file)
        log.info(f"--- [{file_index + 1}/{total_files}] Processing: {file} ---")
        img_current = None
        original_basename = os.path.splitext(file)[0]
        success_flag = False # Успех для текущего файла

        try:
            # 6.1. Бекап
            if backup_enabled:
                try: shutil.copy2(source_file_path, os.path.join(abs_backup_path, file)); log.debug("  > Backup created.")
                except Exception as backup_err: log.error(f"  ! Backup failed for {file}: {backup_err}")

            # 6.2. Открытие
            try:
                with Image.open(source_file_path) as img_opened:
                    img_opened.load()
                    img_current = img_opened.copy()
                    log.debug(f"  > Opened. Orig size: {img_current.size}, Mode: {img_current.mode}")
            except UnidentifiedImageError: log.error(f"  ! Cannot identify image: {file}"); skipped_files_count += 1; continue
            except FileNotFoundError: log.error(f"  ! File not found during open: {file}"); skipped_files_count += 1; continue
            except Exception as open_err: log.error(f"  ! Error opening {file}: {open_err}", exc_info=True); error_files_count += 1; continue
            if not img_current or img_current.size[0] <= 0 or img_current.size[1] <= 0:
                log.error(f"  ! Image empty/zero size after open: {file}"); error_files_count += 1; image_utils.safe_close(img_current); continue

            # --- Конвейер Обработки ---
            step_counter = 1
            log.debug(f"  Step {step_counter}: Pre-resize")
            if enable_preresize: img_current = _apply_preresize(img_current, preresize_width, preresize_height)
            if not img_current: raise ValueError("Image became None after pre-resize.")
            step_counter += 1

            log.debug(f"  Step {step_counter}: Whitening")
            if enable_whitening:
                 img_original = img_current
                 img_current = image_utils.whiten_image_by_darkest_perimeter(img_current, whitening_cancel_threshold)
                 if img_current is not img_original: log.debug("    Whitening applied.")
            if not img_current: raise ValueError("Image became None after whitening.")
            step_counter += 1

            # Периметр (проверяем, только если включены поля и задан маржин)
            perimeter_is_white = False # Важно инициализировать
            if enable_padding and perimeter_margin > 0:
                 log.debug(f"  Step {step_counter}.{1}: Perimeter check (Margin: {perimeter_margin}px)")
                 current_perimeter_tolerance = white_tolerance if enable_bg_crop and white_tolerance is not None else 0
                 perimeter_is_white = image_utils.check_perimeter_is_white(img_current, current_perimeter_tolerance, perimeter_margin)
            # Если padding выключен или margin=0, проверка периметра не выполняется и perimeter_is_white остается False
            step_counter += 1 

            pre_crop_width, pre_crop_height = img_current.size

            log.debug(f"  Step {step_counter}: BG Removal / Crop (Enabled: {enable_bg_crop})")
            cropped_image_dimensions = img_current.size # Запомним размер ДО
            if enable_bg_crop:
                img_original = img_current
                img_current = image_utils.remove_white_background(img_current, white_tolerance)
                if not img_current: raise ValueError("Image became None after background removal.")
                if img_current is not img_original: log.debug("    Background removed/converted.")

                img_original = img_current # Обновляем для сравнения после обрезки
                img_current = image_utils.crop_image(img_current, crop_symmetric_axes, crop_symmetric_absolute)
                if not img_current: raise ValueError("Image became None after cropping.")
                if img_current is not img_original: log.debug("    Cropping applied.")
                cropped_image_dimensions = img_current.size # Обновляем размер ПОСЛЕ обрезки
            step_counter += 1

            log.debug(f"  Step {step_counter}: Padding (Mode: {enable_padding})")
            apply_padding = False # По умолчанию не добавляем
            
            if enable_padding:
                if perimeter_margin > 0:
                    if not perimeter_is_white:
                        current_w, current_h = cropped_image_dimensions
                        if current_w > 0 and current_h > 0 and padding_percent > 0:
                            padding_pixels = int(round(max(current_w, current_h) * (padding_percent / 100.0)))
                            if padding_pixels > 0:
                                potential_padded_w = current_w + 2 * padding_pixels
                                potential_padded_h = current_h + 2 * padding_pixels
                                size_check_passed = (potential_padded_w <= pre_crop_width and potential_padded_h <= pre_crop_height)
                                
                                if allow_expansion or size_check_passed:
                                    apply_padding = True # Все проверки пройдены
                                    log.info("    Padding will be applied (mode condition met, size conditions met).")
                                else:
                                    log.info("    Padding skipped: Size check failed & expansion disabled.")
                            else:
                                log.info("    Padding skipped: Calculated padding is zero pixels.")
                        else:
                            log.info("    Padding skipped: Cropped image has zero size.")
                    else: # perimeter_margin > 0 AND perimeter_is_white is True
                        log.info("    Padding skipped: Perimeter margin is set, and perimeter is already white.")
                else: # perimeter_margin <= 0
                    log.info("    Padding skipped: Perimeter margin is not set (> 0), padding only applied if perimeter is NOT white.")
            else: # enable_padding is False
                log.debug("    Padding step skipped: 'enable_padding' is False.")

            # Вызываем функцию добавления полей, только если флаг установлен
            if apply_padding:
                img_original = img_current
                img_current = image_utils.add_padding(img_current, padding_percent)
                if not img_current: raise ValueError("Image became None after padding.")
                if img_current is not img_original: log.info(f"    Padding applied successfully. New size: {img_current.size}")
            step_counter += 1

            log.debug(f"  Step {step_counter}: Brightness/Contrast")
            if enable_bc:
                log.debug("  Calling apply_brightness_contrast...") # Доп. лог
                img_current = image_utils.apply_brightness_contrast(
                    img_current, 
                    brightness_factor=bc_settings.get('brightness_factor', 1.0),
                    contrast_factor=bc_settings.get('contrast_factor', 1.0)
                )
                if not img_current: 
                    log.warning(f"  Skipping file after brightness/contrast failed (returned None).")
                    continue
                log.info(f"    Brightness/Contrast applied. New size: {img_current.size}") # Доп. лог

            log.debug(f"  Step {step_counter}: Force Aspect Ratio")
            if ind_settings.get('enable_force_aspect_ratio'): # Проверяем флаг
                aspect_ratio_ind = ind_settings.get('force_aspect_ratio')
                if aspect_ratio_ind:
                    # Вызов ТОЛЬКО если флаг True и значение есть
                    img_current = _apply_force_aspect_ratio(img_current, aspect_ratio_ind)
                    if not img_current: log.warning(f"  Skipping file after force aspect ratio failed."); continue
                    log.info(f"    Force Aspect Ratio applied. New size: {img_current.size}")
                else:
                    log.warning("  Force aspect ratio enabled but ratio value is missing/invalid.")
            step_counter += 1

            log.debug(f"  Step {step_counter}: Max Dimensions")
            if ind_settings.get('enable_max_dimensions'): # Проверяем флаг
                img_current = _apply_max_dimensions(img_current, max_output_width, max_output_height)
                if not img_current: raise ValueError("Image became None after max dimensions.")
            step_counter += 1

            log.debug(f"  Step {step_counter}: Final Canvas / Prepare")
            # --- Логика точного холста / подготовки --- 
            canvas_applied = False
            img_before_prepare = img_current # Сохраняем ссылку для лога
            log.debug(f"    Image before final step: {repr(img_before_prepare)}") 
            
            if ind_settings.get('enable_exact_canvas'): # Проверяем флаг точного холста
                exact_w = ind_settings.get('final_exact_width', 0)
                exact_h = ind_settings.get('final_exact_height', 0)
                if exact_w > 0 and exact_h > 0:
                    img_processed = _apply_final_canvas_or_prepare(
                        img_current, exact_w, exact_h, output_format, valid_jpg_bg
                    )
                    if not img_processed: log.warning(f"  Skipping file after exact canvas failed."); continue
                    log.info(f"    Exact Canvas applied. New size: {img_processed.size}")
                    img_current = None 
                    canvas_applied = True
                else:
                    log.warning("  Exact canvas enabled but width/height are zero.")
            
            if not canvas_applied:
                log.debug("  Applying prepare mode (no exact canvas applied).")
                img_processed = _apply_final_canvas_or_prepare(
                    img_current, 0, 0, output_format, valid_jpg_bg
                )
                if not img_processed: log.warning(f"  Skipping file after prepare mode failed."); continue
                img_current = None
                
            log.debug(f"    Image after final step: {repr(img_processed)}") # Логируем результат
            step_counter += 1
            # ----------------------------------------

            # 6.3. Сохранение
            log.debug(f"  Step {step_counter}: Saving")
            temp_output_filename = f"{original_basename}{output_ext}"
            final_output_path = os.path.join(abs_output_path, temp_output_filename)
            # === ПЕРЕДАЕМ img_processed В СОХРАНЕНИЕ ===
            save_successful = _save_image(img_processed, final_output_path, output_format, jpeg_quality)
            
            # Закрываем img_processed после сохранения
            image_utils.safe_close(img_processed)

            if save_successful:
                processed_files_count += 1
                success_flag = True
                processed_output_file_map[final_output_path] = original_basename
                if os.path.exists(source_file_path) and source_file_path not in source_files_to_potentially_delete:
                     source_files_to_potentially_delete.append(source_file_path)
            else:
                error_files_count += 1
                log.error(f"Failed to save processed file: {file}")

        # --- Обработка Ошибок для Файла ---
        except MemoryError as e:
             log.critical(f"!!! MEMORY ERROR processing {file}: {e}. Attempting GC.", exc_info=True)
             error_files_count += 1; gc.collect(); success_flag = False
        except ValueError as e: # Ловим наши ошибки None
             log.error(f"!!! PROCESSING error for {file}: {e}", exc_info=False) # Не нужен полный трейсбек
             error_files_count += 1; success_flag = False
        except Exception as e:
             log.critical(f"!!! UNEXPECTED error processing {file}: {e}", exc_info=True)
             error_files_count += 1; success_flag = False
        finally:
            image_utils.safe_close(img_current) # Закрываем в любом случае
            if not success_flag and source_file_path in source_files_to_potentially_delete:
                 try: source_files_to_potentially_delete.remove(source_file_path); log.warning(f"  Removed {os.path.basename(source_file_path)} from deletion list due to error.")
                 except ValueError: pass
            log.info(f"--- Finished processing: {file} {'(Success)' if success_flag else '(Failed)'} ---")


    # --- 7. Финальные Действия (Статистика, Удаление, Переименование) ---
    log.info("\n" + "=" * 30)
    log.info("--- Final Summary ---")
    log.info(f"Successfully processed: {processed_files_count}")
    log.info(f"Skipped (unreadable/not found): {skipped_files_count}")
    log.info(f"Errors during processing/saving: {error_files_count}")
    log.info(f"Total analyzed: {processed_files_count + skipped_files_count + error_files_count} / {total_files}")
    total_time = time.time() - start_time
    log.info(f"Total processing time: {total_time:.2f} seconds")

    # 7.1. Удаление оригиналов
    if effective_delete_originals and source_files_to_potentially_delete:
        log.info(f"\n--- Deleting {len(source_files_to_potentially_delete)} original files from '{abs_input_path}' ---")
        removed_count = 0; remove_errors = 0
        for file_to_remove in list(source_files_to_potentially_delete):
            try:
                if os.path.exists(file_to_remove): os.remove(file_to_remove); removed_count += 1; log.debug(f"  Deleted: {os.path.basename(file_to_remove)}")
                else: log.warning(f"  File to delete not found: {os.path.basename(file_to_remove)}")
            except Exception as remove_error: log.error(f"  Error deleting {os.path.basename(file_to_remove)}: {remove_error}"); remove_errors += 1
        log.info(f"  -> Successfully deleted: {removed_count}. Deletion errors: {remove_errors}.")
        source_files_to_potentially_delete.clear()
    elif delete_originals: log.info("\n--- Deletion of originals skipped (check paths or successful processing). ---")
    else: log.info("\n--- Deletion of originals disabled. ---")

    # 7.2. Переименование
    enable_renaming_actual = bool(article_name and str(article_name).strip())
    if enable_renaming_actual and processed_output_file_map:
        log.info(f"\n--- Renaming {len(processed_output_file_map)} files in '{abs_output_path}' using article '{article_name}' ---")
        files_to_rename = list(processed_output_file_map.items()) # [(path, orig_basename), ...]
        if not files_to_rename: log.info("  No files available for renaming stage.")
        else:
            # ... (Полный код переименования с двумя этапами и логированием, как в предыдущем ответе) ...
            try: sorted_files_for_rename = natsorted(files_to_rename, key=lambda item: item[1])
            except Exception as sort_err: log.error(f"! Error sorting for renaming: {sort_err}"); sorted_files_for_rename = files_to_rename
            temp_rename_map = {}; rename_step1_errors = 0; temp_prefix = f"__temp_{os.getpid()}_"; log.info("  Step 1: Renaming to temporary names...")
            for i, (current_path, original_basename) in enumerate(sorted_files_for_rename):
                temp_filename = f"{temp_prefix}{i}_{original_basename}{output_ext}"; temp_path = os.path.join(abs_output_path, temp_filename)
                try: log.debug(f"    '{os.path.basename(current_path)}' -> '{temp_filename}'"); os.rename(current_path, temp_path); temp_rename_map[temp_path] = original_basename
                except Exception as rename_error: log.error(f"  ! Temp rename error for '{os.path.basename(current_path)}': {rename_error}"); rename_step1_errors += 1
            if rename_step1_errors > 0: log.warning(f"  ! Temp renaming errors: {rename_step1_errors}")
            log.info("  Step 2: Renaming to final names...")
            rename_step2_errors = 0; renamed_final_count = 0; occupied_final_names = set()
            existing_temp_paths = [p for p, ob in temp_rename_map.items() if os.path.exists(p)]
            if not existing_temp_paths: log.warning("  ! No temporary files found for final renaming.")
            else:
                all_temp_files_sorted = sorted(existing_temp_paths)
                found_exact_match = False; exact_match_orig_name = None
                for temp_p in all_temp_files_sorted:
                     orig_bn = temp_rename_map.get(temp_p)
                     if orig_bn and orig_bn.lower() == str(article_name).lower(): found_exact_match = True; exact_match_orig_name = orig_bn; log.info(f"    * Found exact match for '{article_name}' (original: '{orig_bn}')."); break
                if not found_exact_match: log.info(f"    * Exact match for '{article_name}' not found.")
                base_name_assigned = False; numeric_counter = 1
                for temp_path in all_temp_files_sorted:
                    original_basename = temp_rename_map.get(temp_path, "unknown_original")
                    target_filename = None; target_path = None; assign_base = False
                    is_exact_match = original_basename.lower() == str(article_name).lower()
                    if is_exact_match and not base_name_assigned: assign_base = True; log.debug(f"    Assigning base name '{article_name}' to exact match '{original_basename}'.")
                    elif not found_exact_match and not base_name_assigned: assign_base = True; log.debug(f"    Assigning base name '{article_name}' to first file '{original_basename}'.")
                    if assign_base:
                        target_filename = f"{article_name}{output_ext}"; target_path = os.path.join(abs_output_path, target_filename); norm_target_path = os.path.normcase(target_path)
                        if norm_target_path in occupied_final_names or (os.path.exists(target_path) and os.path.normcase(temp_path) != norm_target_path): log.warning(f"    ! Conflict: Base name '{target_filename}' exists/occupied. Numbering."); assign_base = False
                        else: base_name_assigned = True
                    if not assign_base:
                        while True:
                            target_filename = f"{article_name}_{numeric_counter}{output_ext}"; target_path = os.path.join(abs_output_path, target_filename); norm_target_path = os.path.normcase(target_path)
                            if norm_target_path not in occupied_final_names and not (os.path.exists(target_path) and os.path.normcase(temp_path) != norm_target_path): break
                            numeric_counter += 1
                        numeric_counter += 1
                    try: log.debug(f"    '{os.path.basename(temp_path)}' -> '{target_filename}'"); os.rename(temp_path, target_path); renamed_final_count += 1; occupied_final_names.add(os.path.normcase(target_path))
                    except Exception as rename_error: log.error(f"    ! Final renaming error '{os.path.basename(temp_path)}' -> '{target_filename}': {rename_error}"); rename_step2_errors += 1
            log.info(f"  -> Files renamed: {renamed_final_count}. Step 2 errors: {rename_step2_errors}.")
            try:
                remaining_temp = [f for f in os.listdir(abs_output_path) if f.startswith(temp_prefix) and os.path.isfile(os.path.join(abs_output_path, f))]
                if remaining_temp: log.warning(f"  ! Temp files might remain: {remaining_temp}")
            except Exception as list_err: log.error(f"! Could not check for remaining temp files: {list_err}")
    elif enable_renaming_actual: log.info("\n--- Renaming skipped: No files successfully processed. ---")
    else: log.info("\n--- Renaming disabled. ---")

    log.info("=" * 30)
    log.info("--- Individual File Processing Function Finished ---")


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
        whitening_cancel_threshold = int(white_settings.get('whitening_cancel_threshold', 550))
        if enable_whitening: img_current = image_utils.whiten_image_by_darkest_perimeter(img_current, whitening_cancel_threshold)
        if not img_current: return None

        # 4. Удаление фона/обрезка (если вкл)
        enable_bg_crop = bgc_settings.get('enable_bg_crop', False)
        white_tolerance = int(bgc_settings.get('white_tolerance', 0)) if enable_bg_crop else None
        crop_symmetric_absolute = bool(bgc_settings.get('crop_symmetric_absolute', False)) if enable_bg_crop else False
        crop_symmetric_axes = bool(bgc_settings.get('crop_symmetric_axes', False)) if enable_bg_crop else False
        if enable_bg_crop:
            img_current = image_utils.remove_white_background(img_current, white_tolerance)
            if not img_current: return None
            img_current = image_utils.crop_image(img_current, crop_symmetric_axes, crop_symmetric_absolute)
            if not img_current: return None

        # 5. Добавление полей (если вкл)
        enable_padding = pad_settings.get('enable_padding', False)
        padding_percent = float(pad_settings.get('padding_percent', 0.0)) if enable_padding else 0.0
        perimeter_margin = int(pad_settings.get('perimeter_margin', 0)) if enable_padding else 0
        allow_expansion = bool(pad_settings.get('allow_expansion', True)) if enable_padding else False
        if enable_padding:
            img_current = image_utils.add_padding(img_current, padding_percent)
            if not img_current: return None
            log.debug(f"    Padding applied. New size: {img_current.size}")

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
        jpg_background_color = coll_settings.get('jpg_background_color', [255, 255, 255])
        jpeg_quality = int(coll_settings.get('jpeg_quality', 95))

        if not source_dir or not output_filename_base:
             raise ValueError("Source directory or output filename base missing.")
        output_filename_base = str(output_filename_base).strip() # Убираем пробелы по краям
        if not output_filename_base: raise ValueError("Output filename base cannot be empty.")
        # Формируем имя файла с расширением
        output_filename_with_ext = f"{os.path.splitext(output_filename_base)[0]}.{output_format}"

        if output_format not in ['jpg', 'png']: raise ValueError(f"Unsupported collage output format: {output_format}")

        valid_jpg_bg = tuple(jpg_background_color) if isinstance(jpg_background_color, list) and len(jpg_background_color) == 3 else (255, 255, 255)
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
    log.info(f"Padding: {'Enabled' if pad_settings.get('enable_padding') else 'Disabled'}")
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


    # --- 6. Пропорциональное Масштабирование (опц.) ---
    scaled_images: List[Image.Image] = []
    if proportional_placement and num_processed > 0:
        # ... (логика масштабирования с логированием как в предыдущем ответе) ...
        log.info("Applying proportional scaling...")
        base_img = processed_images[0]; base_w, base_h = base_img.size
        if base_w > 0 and base_h > 0:
            log.debug(f"  Base size: {base_w}x{base_h}")
            ratios = placement_ratios if placement_ratios else [1.0] * num_processed
            for i, img in enumerate(processed_images):
                 temp_img = None; current_w, current_h = img.size; target_w, target_h = base_w, base_h
                 if i < len(ratios):
                      try: ratio = max(0.01, float(ratios[i])); target_w, target_h = int(round(base_w*ratio)), int(round(base_h*ratio))
                      except: pass # ignore ratio error
                 if current_w > 0 and current_h > 0 and target_w > 0 and target_h > 0:
                      scale = min(target_w / current_w, target_h / current_h)
                      nw, nh = max(1, int(round(current_w * scale))), max(1, int(round(current_h * scale)))
                      if nw != current_w or nh != current_h:
                           try:
                               log.debug(f"  Scaling image {i+1} ({current_w}x{current_h} -> {nw}x{nh})")
                               temp_img = img.resize((nw, nh), Image.Resampling.LANCZOS); scaled_images.append(temp_img); image_utils.safe_close(img)
                           except Exception as e_scale: log.error(f"  ! Error scaling image {i+1}: {e_scale}"); scaled_images.append(img)
                      else: scaled_images.append(img)
                 else: scaled_images.append(img)
            processed_images = []
        else: log.error("  Base image zero size. Scaling skipped."); scaled_images = processed_images; processed_images = []
    else: log.info("Proportional scaling disabled or no images."); scaled_images = processed_images; processed_images = []


    # --- 7. Сборка Коллажа ---
    num_final_images = len(scaled_images)
    if num_final_images == 0:
        log.error("No images left after scaling step (if enabled). Cannot create collage.")
        log.info(">>> Exiting: No images left after scaling.")
        # Очистка, если что-то осталось в scaled_images (хотя не должно)
        for img in scaled_images: image_utils.safe_close(img)
        return False # Возвращаем False
    log.info(f"--- Assembling collage ({num_final_images} images) ---")
    
    # === Убедимся, что расчет grid_cols и grid_rows на месте ===
    grid_cols = forced_cols if forced_cols > 0 else max(1, int(math.ceil(math.sqrt(num_final_images))))
    grid_rows = max(1, int(math.ceil(num_final_images / grid_cols))) 
    # ==========================================================

    max_w = max((img.width for img in scaled_images if img), default=1)
    max_h = max((img.height for img in scaled_images if img), default=1)
    spacing_px_h = int(round(max_w * (spacing_percent / 100.0)))
    spacing_px_v = int(round(max_h * (spacing_percent / 100.0)))
    canvas_width = (grid_cols * max_w) + ((grid_cols + 1) * spacing_px_h)
    canvas_height = (grid_rows * max_h) + ((grid_rows + 1) * spacing_px_v)
    log.debug(f"  Grid: {grid_rows}x{grid_cols}, Cell: {max_w}x{max_h}, Space H/V: {spacing_px_h}/{spacing_px_v}, Canvas: {canvas_width}x{canvas_height}")

    collage_canvas = None; final_collage = None
    try:
        collage_canvas = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))
        # === ЛОГ 1 ===
        log.debug(f"    Canvas created: {repr(collage_canvas)}") 
        # ============
        current_idx = 0
        for r in range(grid_rows):
            for c in range(grid_cols):
                if current_idx >= num_final_images: break # Этот break внутри цикла
                img = scaled_images[current_idx]
                if img and img.width > 0 and img.height > 0:
                     px = spacing_px_h + c * (max_w + spacing_px_h); py = spacing_px_v + r * (max_h + spacing_px_v)
                     paste_x = px + (max_w - img.width) // 2; paste_y = py + (max_h - img.height) // 2
                     try: collage_canvas.paste(img, (paste_x, paste_y), mask=img)
                     except Exception as e_paste: log.error(f"  ! Error pasting image {current_idx+1}: {e_paste}")
                current_idx += 1
            if current_idx >= num_final_images: break # Этот break внутри цикла
        
        log.info("  Images placed on collage canvas.")
        for img in scaled_images: image_utils.safe_close(img) # Закрываем исходники
        scaled_images = []
        
        final_collage = collage_canvas # Передаем владение
        # === ЛОГ 2 ===
        log.debug(f"    final_collage assigned: {repr(final_collage)}") 
        # ============
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
        save_successful = _save_image(final_collage, output_file_path, output_format, jpeg_quality)
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