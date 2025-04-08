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
                    img = _apply_background_crop(
                        img,
                        background_crop_settings.get('white_tolerance', 0),
                        background_crop_settings.get('perimeter_tolerance', 10),
                        background_crop_settings.get('force_absolute_symmetry', False),
                        background_crop_settings.get('force_axes_symmetry', False),
                        background_crop_settings.get('check_perimeter', False),
                        background_crop_settings.get('enable_crop', True)
                    )
                
                if padding_settings.get('mode', 'never') != 'never':
                    img = _apply_padding(img, padding_settings)
                
                if brightness_contrast_settings.get('enable_bc', False):
                    img = image_utils.apply_brightness_contrast(img, brightness_contrast_settings.get('brightness_factor', 1.0), brightness_contrast_settings.get('contrast_factor', 1.0))
                
                # Apply merge with template if enabled
                if enable_merge and template_path:
                    try:
                        # Load template
                        if template_path.lower().endswith('.psd'):
                            try:
                                from psd_tools import PSDImage
                                psd = PSDImage.open(template_path)
                                # Convert PSD to PIL Image using the correct method
                                template = psd.topil()
                                if template.mode == 'CMYK':
                                    template = template.convert('RGB')
                            except ImportError:
                                log.error("psd_tools library not installed. Cannot process PSD files.")
                                continue
                        else:
                            template = Image.open(template_path)
                            template.load()
                            
                        if process_template:
                            # Apply same processing steps to template
                            if preprocessing_settings.get('enable_preresize', False):
                                template = _apply_preresize(template, 
                                                         preprocessing_settings.get('preresize_width', 0),
                                                         preprocessing_settings.get('preresize_height', 0))
                            if whitening_settings.get('enable_whitening', False):
                                template = image_utils.whiten_image_by_darkest_perimeter(template, whitening_settings.get('whitening_cancel_threshold', 765))
                            if background_crop_settings.get('enable_bg_crop', False):
                                template = _apply_background_crop(
                                    template,
                                    background_crop_settings.get('white_tolerance', 0),
                                    background_crop_settings.get('perimeter_tolerance', 10),
                                    background_crop_settings.get('force_absolute_symmetry', False),
                                    background_crop_settings.get('force_axes_symmetry', False),
                                    background_crop_settings.get('check_perimeter', False),
                                    background_crop_settings.get('enable_crop', True)
                                )
                            if padding_settings.get('mode', 'never') != 'never':
                                template = _apply_padding(template, padding_settings)
                            if brightness_contrast_settings.get('enable_bc', False):
                                template = image_utils.apply_brightness_contrast(template, brightness_contrast_settings.get('brightness_factor', 1.0), brightness_contrast_settings.get('contrast_factor', 1.0))
                        
                        # Add jpg_background_color to merge_settings
                        merge_settings['jpg_background_color'] = individual_mode_settings.get('jpg_background_color', [255, 255, 255])
                        
                        # Merge image with template
                        img = _merge_with_template(img, template, merge_settings)
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
                
                # Delete original if enabled
                if individual_mode_settings.get('delete_originals', False):
                    try:
                        os.remove(file_path)
                        log.info(f"Deleted original file: {filename}")
                    except Exception as e:
                        log.error(f"Error deleting original file {filename}: {e}")
                
                log.info(f"--- Finished processing: {filename} (Success) ---")
                
            except Exception as e:
                log.critical(f"!!! UNEXPECTED error processing {filename}: {e}")
                log.exception("Error details")
                log.info(f"--- Finished processing: {filename} (Failed) ---")
                overall_success = False
        
        if not overall_success:
            log.error("--- Processing completed with errors ---")
            return False
        else:
            log.info("--- Processing completed successfully ---")
            return True
        
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

        if enable_bg_crop:
            img_current = _apply_background_crop(
                img_current,
                white_tolerance=white_tolerance,
                perimeter_tolerance=perimeter_tolerance,
                force_absolute_symmetry=crop_symmetric_absolute,
                force_axes_symmetry=crop_symmetric_axes,
                check_perimeter=check_perimeter,
                enable_crop=enable_crop
            )
        
        # 5. Добавление полей (если вкл)
        enable_padding = pad_settings.get('mode', 'never') != 'never' # Включено, если не равно 'never'
        padding_mode = pad_settings.get('mode', 'never')
        padding_percent = float(pad_settings.get('padding_percent', 0.0)) if enable_padding else 0.0
        allow_expansion = bool(pad_settings.get('allow_expansion', True)) if enable_padding else False
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
            img_current = image_utils.add_padding(img_current, padding_percent)
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


    # --- 6. Пропорциональное Масштабирование (опц.) ---
    scaled_images: List[Image.Image] = []
    if proportional_placement and num_processed > 0:
        # ... (логика масштабирования с логированием как в предыдущем ответе) ...
        log.info("Applying proportional scaling...")
        base_img = processed_images[0]; base_w, base_h = base_img.size
        if base_w > 0 and base_h > 0:
            log.debug(f"  Base size: {base_w}x{base_h}")
            base_area = base_w * base_h
            log.debug(f"  Base area: {base_area} pixels")
            ratios = placement_ratios if placement_ratios else [1.0] * num_processed
            for i, img in enumerate(processed_images):
                 temp_img = None; current_w, current_h = img.size; target_w, target_h = base_w, base_h
                 if i < len(ratios):
                      try: ratio = max(0.01, float(ratios[i])); target_w, target_h = int(round(base_w*ratio)), int(round(base_h*ratio))
                      except: pass # ignore ratio error
                 if current_w > 0 and current_h > 0 and target_w > 0 and target_h > 0:
                      # Вычисляем площади
                      current_area = current_w * current_h
                      target_area = target_w * target_h
                      
                      # Вычисляем коэффициент масштабирования на основе площадей
                      area_scale = math.sqrt(target_area / current_area)
                      
                      # Применяем масштабирование с сохранением пропорций
                      nw = max(1, int(round(current_w * area_scale)))
                      nh = max(1, int(round(current_h * area_scale)))
                      
                      log.debug(f"  Image {i+1} areas: current={current_area}, target={target_area}, scale={area_scale:.3f}")
                      
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

def _merge_with_template(image: Image.Image, template_path_or_image: Union[str, Image.Image], settings: Dict[str, Any]) -> Optional[Image.Image]:
    """Объединяет изображение с шаблоном."""
    try:
        log.info(f"  > Merging with template: {template_path_or_image}")
        
        # Загружаем шаблон или используем переданный объект
        try:
            if isinstance(template_path_or_image, str):
                # Check if file is PSD
                if template_path_or_image.lower().endswith('.psd'):
                    try:
                        from psd_tools import PSDImage
                        psd = PSDImage.open(template_path_or_image)
                        # Convert PSD to PIL Image using the correct method
                        template = psd.topil()
                        if template.mode == 'CMYK':
                            template = template.convert('RGB')
                    except ImportError:
                        log.error("psd_tools library not installed. Cannot process PSD files.")
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
        template_on_top = settings.get('template_on_top', True)
        process_template = settings.get('process_template', False)
        width_ratio = settings.get('width_ratio', [1.0, 1.0])
        enable_width_ratio = settings.get('enable_width_ratio', False)
        fit_image_to_template = settings.get('fit_image_to_template', False)
        fit_template_to_image = settings.get('fit_template_to_image', False)
        no_scaling = settings.get('no_scaling', False)  # New setting for no scaling mode
        
        # Проверяем соотношение размеров
        if not isinstance(width_ratio, (list, tuple)) or len(width_ratio) != 2:
            width_ratio = [1.0, 1.0]
            log.warning("  > Invalid width ratio format, using default [1.0, 1.0]")
        
        # Проверяем минимальные значения
        width_ratio_w = max(0.1, float(width_ratio[0]))
        width_ratio_h = max(0.1, float(width_ratio[1]))
        width_ratio = [width_ratio_w, width_ratio_h]
        
        log.info(f"  > Width ratio settings: enable={enable_width_ratio}, w={width_ratio_w}, h={width_ratio_h}")
        log.info(f"  > Fit settings: image_to_template={fit_image_to_template}, template_to_image={fit_template_to_image}")
        log.info(f"  > No scaling mode: {no_scaling}")
        
        # Конвертируем изображения в RGBA если нужно
        if image.mode != 'RGBA':
            image = image.convert('RGBA')
        if template.mode != 'RGBA':
            template = template.convert('RGBA')
            
        # Вычисляем размеры
        template_width, template_height = template.size
        image_width, image_height = image.size
        log.info(f"  > Original sizes - Image: {image_width}x{image_height}, Template: {template_width}x{template_height}")
        
        # Определяем новый размер изображения
        new_width = image_width
        new_height = image_height
        
        if no_scaling:
            # В режиме без масштабирования используем оригинальные размеры
            log.info("  > No scaling mode - Using original image size")
        elif enable_width_ratio:
            # Вычисляем целевые размеры на основе соотношения с шаблоном
            target_width = int(template_width * width_ratio_w)
            target_height = int(template_height * width_ratio_h)
            
            # Вычисляем площади
            template_area = template_width * template_height
            target_area = target_width * target_height
            image_area = image_width * image_height
            
            # Вычисляем коэффициент масштабирования на основе площадей
            # Используем квадратный корень из отношения целевой площади к площади изображения
            area_scale = math.sqrt(target_area / image_area)
            
            # Применяем масштабирование с сохранением пропорций
            new_width = int(image_width * area_scale)
            new_height = int(image_height * area_scale)
            
            log.info(f"  > Width ratio enabled - Target size: {target_width}x{target_height}")
            log.info(f"  > Area-based scale factor: {area_scale:.3f}, New size: {new_width}x{new_height}")
            log.info(f"  > Areas: template={template_area}, target={target_area}, image={image_area}")
            
            # Явно масштабируем изображение
            scaled_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            log.info(f"  > Image scaled to: {scaled_image.size}")
            
            # Создаем холст размером с максимальные размеры
            canvas_width = max(template_width, new_width)
            canvas_height = max(template_height, new_height)
            result = Image.new('RGBA', (canvas_width, canvas_height), (255, 255, 255, 0))
            
            # Вычисляем позиции для центрирования
            template_x = (canvas_width - template_width) // 2
            template_y = (canvas_height - template_height) // 2
            image_x = (canvas_width - new_width) // 2
            image_y = (canvas_height - new_height) // 2
            
            # Накладываем изображения в зависимости от порядка
            if template_on_top:
                # Сначала накладываем изображение
                result.paste(scaled_image, (image_x, image_y), scaled_image)
                # Затем накладываем шаблон поверх
                result.paste(template, (template_x, template_y), template)
            else:
                # Сначала накладываем шаблон
                result.paste(template, (template_x, template_y), template)
                # Затем накладываем изображение поверх
                result.paste(scaled_image, (image_x, image_y), scaled_image)
            
            log.info(f"  > Merged image size: {result.size}, mode: {result.mode}")
            return result
        
        elif fit_image_to_template:
            # Масштабируем изображение, чтобы оно помещалось в шаблон с сохранением пропорций
            width_ratio = template_width / image_width
            height_ratio = template_height / image_height
            scale_factor = min(width_ratio, height_ratio)
            new_width = int(image_width * scale_factor)
            new_height = int(image_height * scale_factor)
            
            log.info(f"  > Fit image to template - Scale factor: {scale_factor:.3f}, New size: {new_width}x{new_height}")
            
            # Создаем холст размером с шаблон
            result = Image.new('RGBA', template.size, (255, 255, 255, 0))
            
            # Вычисляем позицию для центрирования
            paste_x = (template_width - new_width) // 2
            paste_y = (template_height - new_height) // 2
            
            # Накладываем изображения в зависимости от порядка
            if template_on_top:
                # Сначала накладываем изображение
                result.paste(image, (paste_x, paste_y), image)
                # Затем накладываем шаблон поверх
                result = Image.alpha_composite(result, template)
            else:
                # Сначала накладываем шаблон
                result.paste(template, (0, 0), template)
                # Затем накладываем изображение поверх
                result.paste(image, (paste_x, paste_y), image)
            
            log.info(f"  > Merged image size: {result.size}, mode: {result.mode}")
            return result
            
        elif fit_template_to_image:
            # Масштабируем шаблон, чтобы он помещался в изображение с сохранением пропорций
            width_ratio = image_width / template_width
            height_ratio = image_height / template_height
            scale_factor = min(width_ratio, height_ratio)
            template_width = int(template_width * scale_factor)
            template_height = int(template_height * scale_factor)
            template = template.resize((template_width, template_height), Image.Resampling.LANCZOS)
            
            # Создаем новое изображение с размерами исходного изображения
            result = Image.new('RGBA', (image_width, image_height), (255, 255, 255, 0))
            
            # Вычисляем позицию для центрирования шаблона
            paste_x = (image_width - template_width) // 2
            paste_y = (image_height - template_height) // 2
            
            # Накладываем изображения в зависимости от порядка
            if template_on_top:
                # Сначала накладываем изображение
                result.paste(image, (0, 0), image)
                # Затем накладываем шаблон поверх
                result.paste(template, (paste_x, paste_y), template)
            else:
                # Сначала накладываем шаблон
                result.paste(template, (paste_x, paste_y), template)
                # Затем накладываем изображение поверх
                result.paste(image, (0, 0), image)
            
            log.info(f"  > Merged image size: {result.size}, mode: {result.mode}")
            return result
        
        # Изменяем размер изображения если нужно (кроме режима без масштабирования)
        if not no_scaling and (new_width != image_width or new_height != image_height):
            image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            log.info(f"  > Resized image to: {image.size}")
        
        # Определяем позицию для вставки с учетом режима без масштабирования
        paste_x = 0
        paste_y = 0
        
        if no_scaling:
            # В режиме без масштабирования центрируем изображение
            paste_x = (template_width - image_width) // 2
            paste_y = (template_height - image_height) // 2
            log.info(f"  > No scaling mode - Centered position: ({paste_x}, {paste_y})")
        else:
            # Стандартное позиционирование для других режимов
            if position == 'center':
                paste_x = (template_width - new_width) // 2
                paste_y = (template_height - new_height) // 2
            elif position == 'top':
                paste_x = (template_width - new_width) // 2
                paste_y = 0
            elif position == 'bottom':
                paste_x = (template_width - new_width) // 2
                paste_y = template_height - new_height
            elif position == 'left':
                paste_x = 0
                paste_y = (template_height - new_height) // 2
            elif position == 'right':
                paste_x = template_width - new_width
                paste_y = (template_height - new_height) // 2
            elif position == 'top-left':
                paste_x = 0
                paste_y = 0
            elif position == 'top-right':
                paste_x = template_width - new_width
                paste_y = 0
            elif position == 'bottom-left':
                paste_x = 0
                paste_y = template_height - new_height
            elif position == 'bottom-right':
                paste_x = template_width - new_width
                paste_y = template_height - new_height
        
        # Создаем новое изображение с фоном
        result = Image.new('RGBA', template.size, (255, 255, 255, 0))
        
        # Накладываем изображения в зависимости от порядка
        if template_on_top:
            # Сначала накладываем изображение
            result.paste(image, (paste_x, paste_y), image)
            # Затем накладываем шаблон поверх
            result = Image.alpha_composite(result, template)
        else:
            # Сначала накладываем шаблон
            result.paste(template, (0, 0), template)
            # Затем накладываем изображение поверх
            result.paste(image, (paste_x, paste_y), image)
        
        log.info(f"  > Merged image size: {result.size}, mode: {result.mode}")
        return result
        
    except Exception as e:
        log.error(f"  > Error merging with template: {str(e)}")
        return None

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

def _calculate_paste_position(img_size: Tuple[int, int], template_size: Tuple[int, int], position: str) -> Tuple[int, int]:
    """
    Calculate the paste position for the image on the template based on the specified position.
    
    Args:
        img_size: Size of the image to paste (width, height)
        template_size: Size of the template (width, height)
        position: Position string ('center', 'top', 'bottom', 'left', 'right', 'top-left', etc.)
        
    Returns:
        Tuple of (x, y) coordinates for pasting
    """
    img_width, img_height = img_size
    template_width, template_height = template_size
    
    # Calculate center position
    center_x = (template_width - img_width) // 2
    center_y = (template_height - img_height) // 2
    
    # Calculate positions based on the specified position
    if position == 'center':
        return (center_x, center_y)
    elif position == 'top':
        return (center_x, 0)
    elif position == 'bottom':
        return (center_x, template_height - img_height)
    elif position == 'left':
        return (0, center_y)
    elif position == 'right':
        return (template_width - img_width, center_y)
    elif position == 'top-left':
        return (0, 0)
    elif position == 'top-right':
        return (template_width - img_width, 0)
    elif position == 'bottom-left':
        return (0, template_height - img_height)
    elif position == 'bottom-right':
        return (template_width - img_width, template_height - img_height)
    else:
        # Default to center if position is not recognized
        return (center_x, center_y)

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
                         check_perimeter: bool = False, enable_crop: bool = True) -> Optional[Image.Image]:
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
        
    Returns:
        Processed image or None if processing fails
    """
    try:
        log.info("=== Processing background and crop ===")
        
        # Convert to RGBA if needed
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # Check perimeter if enabled
        should_remove_bg = True
        if check_perimeter:
            log.debug(f"Checking if image perimeter is white (tolerance: {perimeter_tolerance})")
            perimeter_is_white = image_utils.check_perimeter_is_white(img, perimeter_tolerance, 1)
            
            # Всегда используем 'if_white' режим
            if not perimeter_is_white:
                log.info("Background removal skipped: perimeter is NOT white")
                should_remove_bg = False
            else:
                log.info("Background will be removed: perimeter IS white")
        
        # Remove background if needed
        if should_remove_bg:
            img = image_utils.remove_white_background(img, white_tolerance)
            if not img:
                log.error("Failed to remove background")
                return None
        
        # Apply cropping only if enabled
        if enable_crop:
            log.info("Cropping is enabled, applying crop")
            img = image_utils.crop_image(img, force_axes_symmetry, force_absolute_symmetry)
            if not img:
                log.error("Failed to crop image")
                return None
        else:
            log.info("Cropping is disabled, keeping original canvas size")
        
        return img
        
    except Exception as e:
        log.error(f"Error in _apply_background_crop: {e}")
        log.exception("Error details")
        return None