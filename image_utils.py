# image_utils.py

import os
import math
import logging
import traceback
from typing import Optional, Tuple, List
from PIL import Image, ImageChops, UnidentifiedImageError, ImageFile, ImageEnhance

# --- Настройка Логгера ---
# Используем стандартный модуль logging
# Настраивать сам логгер (куда писать, уровень и т.д.) будем в app.py
log = logging.getLogger(__name__)

# --- Обработка Опциональной Библиотеки natsort ---
try:
    from natsort import natsorted
    log.debug("natsort library found and imported.")
except ImportError:
    log.warning("natsort library not found. Standard list sorting will be used.")
    # Заменяем natsorted на стандартную функцию sorted
    natsorted = sorted

# --- Конфигурация Pillow ---
# Проверка Pillow здесь не нужна, т.к. она будет в app.py при старте
# Если Pillow не будет найдена там, приложение не запустится до импорта этого модуля.
ImageFile.LOAD_TRUNCATED_IMAGES = True
log.debug("ImageFile.LOAD_TRUNCATED_IMAGES set to True.")

SUPPORTED_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff', '.webp', '.tif', '.psd')

# === Функции Обработки Изображений ===

def safe_close(img_obj):
    """Safely closes a PIL Image object, ignoring errors."""
    if img_obj and isinstance(img_obj, Image.Image):
        try:
            img_obj.close()
        except Exception:
            pass # Ignore close errors

def whiten_image_by_darkest_perimeter(img, cancel_threshold_sum):
    """
    Whitens an image using the darkest perimeter pixel (1px border)
    as the white reference. Checks the threshold before whitening.
    Works on a copy. Returns a new image object or the original if cancelled/error.
    
    Args:
        img: PIL Image object
        cancel_threshold_sum: Value from 0 to 765
                              0 = only whiten if perimeter is pure white (lightness 100%)
                              765 = always whiten regardless of darkness (even black perimeter)
    """
    log.debug(f"Attempting whitening (perimeter pixel, threshold: {cancel_threshold_sum})...")
    img_copy = None; img_rgb = None; alpha_channel = None; img_whitened_rgb = None
    # Bands for LUT application
    r_ch, g_ch, b_ch = None, None, None
    out_r, out_g, out_b = None, None, None
    final_image = img # Return original by default

    try:
        # Ensure we work with a copy and prepare RGB / Alpha parts
        img_copy = img.copy()
        original_mode = img_copy.mode
        has_alpha = 'A' in img_copy.getbands()

        if original_mode == 'RGBA' and has_alpha:
            split_bands = img_copy.split()
            if len(split_bands) == 4:
                img_rgb = Image.merge('RGB', split_bands[:3])
                alpha_channel = split_bands[3]
                # Close intermediate bands from split
                for band in split_bands[:3]: safe_close(band)
            else:
                # This case should ideally not happen for valid RGBA
                log.warning(f"Expected 4 bands in RGBA, got {len(split_bands)}. Converting to RGB.")
                img_rgb = img_copy.convert('RGB')
                has_alpha = False # Treat as no alpha
        elif original_mode != 'RGB':
            img_rgb = img_copy.convert('RGB')
        else:
            # If already RGB, make sure we have a separate copy for processing
            img_rgb = img_copy.copy()

        width, height = img_rgb.size
        if width <= 1 or height <= 1:
            log.warning("Image too small for perimeter analysis. Whitening cancelled.")
            final_image = img_copy # Return original copy
            img_copy = None # Avoid closing in finally
            safe_close(img_rgb); safe_close(alpha_channel)
            return final_image

        # Find the darkest pixel on the perimeter
        darkest_pixel_rgb = None
        min_sum = float('inf')
        pixels = img_rgb.load()
        perimeter_pixels = []
        # Build perimeter coordinates safely
        if height > 0: perimeter_pixels.extend([(x, 0) for x in range(width)]) # Top
        if height > 1: perimeter_pixels.extend([(x, height - 1) for x in range(width)]) # Bottom
        if width > 0: perimeter_pixels.extend([(0, y) for y in range(1, height - 1)]) # Left (excl corners)
        if width > 1: perimeter_pixels.extend([(width - 1, y) for y in range(1, height - 1)]) # Right (excl corners)

        for x, y in perimeter_pixels:
            try:
                pixel = pixels[x, y]
                # Handle RGB/L modes
                if isinstance(pixel, (tuple, list)) and len(pixel) >= 3:
                    r, g, b = pixel[:3]
                    # Ensure integer values for sum
                    if all(isinstance(val, int) for val in (r, g, b)):
                        current_sum = r + g + b
                        if current_sum < min_sum:
                             min_sum = current_sum
                             darkest_pixel_rgb = (r, g, b)
                elif isinstance(pixel, int): # Grayscale ('L')
                    current_sum = pixel * 3
                    if current_sum < min_sum:
                         min_sum = current_sum
                         darkest_pixel_rgb = (pixel, pixel, pixel)
            except (IndexError, TypeError, Exception):
                # Ignore pixels causing errors
                continue

        if darkest_pixel_rgb is None:
            log.warning("Could not find valid perimeter pixels. Whitening cancelled.")
            final_image = img_copy; img_copy = None
            safe_close(img_rgb); safe_close(alpha_channel)
            return final_image

        ref_r, ref_g, ref_b = darkest_pixel_rgb
        current_pixel_sum = ref_r + ref_g + ref_b
        
        # Рассчитываем "светлость" периметра как процент от максимума
        # 0 (черный) = 0%, 765 (белый) = 100%
        perimeter_lightness_percent = (current_pixel_sum / 765.0) * 100
        
        # Преобразуем пороговое значение в проценты
        threshold_percent = (cancel_threshold_sum / 765.0) * 100
        
        log.debug(f"Darkest perimeter pixel: RGB=({ref_r},{ref_g},{ref_b}), Lightness={perimeter_lightness_percent:.1f}%")
        log.debug(f"Whitening threshold: {threshold_percent:.1f}%")

        # Проверяем, подходит ли периметр для отбеливания
        log.info(f"=== ПРОВЕРКА ОТБЕЛИВАНИЯ ===")
        log.info(f"Самый темный пиксель периметра: RGB={darkest_pixel_rgb}")
        log.info(f"Сумма RGB темного пикселя: {current_pixel_sum}")
        log.info(f"Пороговое значение: {cancel_threshold_sum}")
        

        if current_pixel_sum >= cancel_threshold_sum:
            log.info(f"Периметр подходит для отбеливания (сумма {current_pixel_sum} > порог {cancel_threshold_sum} или порог = 765)")
            log.info("=== ПРИМЕНЕНИЕ ОТБЕЛИВАНИЯ ===")
            
            # Рассчитываем коэффициенты
            r, g, b = darkest_pixel_rgb
            scale_r = 255.0 / max(1.0, float(r))
            scale_g = 255.0 / max(1.0, float(g))
            scale_b = 255.0 / max(1.0, float(b))
            
            log.info(f"Коэффициенты масштабирования: R*={scale_r:.2f}, G*={scale_g:.2f}, B*={scale_b:.2f}")
            
            # Создаем и применяем LUT
            log.info("Создаем и применяем LUT...")
            r_ch, g_ch, b_ch = img_rgb.split()
            
            lut_r = bytes([min(255, round(i * scale_r)) for i in range(256)])
            lut_g = bytes([min(255, round(i * scale_g)) for i in range(256)])
            lut_b = bytes([min(255, round(i * scale_b)) for i in range(256)])
            
            log.info("Применяем LUT к каналам...")
            r_ch = r_ch.point(lut_r)
            g_ch = g_ch.point(lut_g)
            b_ch = b_ch.point(lut_b)
            
            log.info("Объединяем каналы...")
            img_whitened_rgb = Image.merge('RGB', (r_ch, g_ch, b_ch))
            log.info("Отбеливание завершено")

            # Восстанавливаем альфа-канал, если он был
            if alpha_channel:
                log.debug("Restoring alpha channel...")
                if img_whitened_rgb.size == alpha_channel.size:
                     img_whitened_rgb.putalpha(alpha_channel)
                     final_image = img_whitened_rgb # Result is whitened RGBA
                     img_whitened_rgb = None # Prevent closing in finally
                     log.debug("Whitening with alpha channel completed.")
                else:
                     log.error(f"Size mismatch when adding alpha ({img_whitened_rgb.size} vs {alpha_channel.size}). Returning whitened RGB only.")
                     final_image = img_whitened_rgb # Return RGB only
                     img_whitened_rgb = None
            else:
                final_image = img_whitened_rgb # Result is whitened RGB
                img_whitened_rgb = None
                log.debug("Whitening (without alpha channel) completed.")
        else:
            log.info(f"Отбеливание отменено: периметр слишком светлый (сумма {current_pixel_sum} <= порог {cancel_threshold_sum})")
            final_image = img_copy
            img_copy = None

    except Exception as e:
        log.error(f"Error during whitening: {e}. Returning original copy.", exc_info=True)
        # Try to return the initial copy if available, otherwise the original input
        final_image = img_copy if img_copy else img
        img_copy = None # Avoid closing in finally if it becomes the result
    finally:
        # Close intermediate objects safely
        safe_close(r_ch); safe_close(g_ch); safe_close(b_ch)
        safe_close(out_r); safe_close(out_g); safe_close(out_b)
        safe_close(alpha_channel)
        # Close img_rgb only if it's not the initial copy (img_copy)
        if img_rgb and img_rgb is not img_copy:
             safe_close(img_rgb)
        # Close img_whitened_rgb if it wasn't assigned to final_image
        if img_whitened_rgb and img_whitened_rgb is not final_image:
            safe_close(img_whitened_rgb)
        # Close img_copy if it wasn't assigned to final_image
        if img_copy and img_copy is not final_image:
            safe_close(img_copy)

    return final_image


# Используем улучшенную версию из предыдущего шага
def remove_white_background(img, tolerance, mode="full"):
    """
    Turns white/near-white pixels transparent based on selected mode.
    
    Args:
        img (PIL.Image.Image): Input image.
        tolerance (int): Tolerance for white (0=only 255, 255=all).
                         If None or < 0, the function does nothing but ensures RGBA.
        mode (str): Removal mode - "full" (removes all white pixels) or 
                   "edges" (removes only white pixels from the edges inward).
    
    Returns:
        PIL.Image.Image: Processed image in RGBA mode,
                         or the original image if critical conversion error occurs.
    """
    if tolerance is None or tolerance < 0:
        log.debug("remove_white_background tolerance is None or negative, skipping removal logic but ensuring RGBA.")
        # Still ensure RGBA for consistency downstream
        if img.mode == 'RGBA':
            return img.copy() # Return a copy
        else:
            try:
                rgba_copy = img.convert('RGBA')
                log.debug(f"Converted {img.mode} -> RGBA (removal skipped)")
                return rgba_copy
            except Exception as e:
                log.error(f"Failed to convert image to RGBA (removal skipped): {e}", exc_info=True)
                return img # Return original on error

    log.debug(f"Attempting remove_white_background (tolerance: {tolerance}, mode: {mode}) on image mode {img.mode}")
    
    # Choose the appropriate background removal method based on mode
    if mode == "edges":
        return remove_white_background_edges(img, tolerance)
    else:  # Default to full background removal
        return remove_white_background_full(img, tolerance)

def remove_white_background_full(img, tolerance):
    """
    Turns all white/near-white pixels transparent throughout the image.
    
    Args:
        img (PIL.Image.Image): Input image.
        tolerance (int): Tolerance for white (0=only 255, 255=all).
    
    Returns:
        PIL.Image.Image: Processed image in RGBA mode.
    """
    img_rgba = None
    final_image = img # Default to original if critical error
    original_mode = img.mode

    try:
        # --- 1. Ensure RGBA ---
        if original_mode != 'RGBA':
            try:
                img_rgba = img.convert('RGBA')
                log.debug(f"Converted {original_mode} -> RGBA")
            except Exception as e:
                log.error(f"Failed to convert image to RGBA: {e}", exc_info=True)
                return img # Critical error, return as is
        else:
            img_rgba = img.copy()
            log.debug("Created RGBA copy (original was RGBA)")

        # --- 2. Get and check data ---
        try:
            datas = list(img_rgba.getdata())
        except Exception as e:
             log.error(f"Failed to get image data: {e}", exc_info=True)
             safe_close(img_rgba)
             return img # Return original

        if not datas:
            log.warning("Image data is empty. Skipping background removal.")
            final_image = img_rgba
            img_rgba = None
            return final_image

        if not isinstance(datas[0], (tuple, list)) or len(datas[0]) != 4:
            log.error(f"Unexpected pixel data format (first element: {datas[0]}). Skipping background removal.")
            final_image = img_rgba
            img_rgba = None
            return final_image

        # --- 3. Process pixels ---
        newData = []
        cutoff = 255 - tolerance
        pixels_changed = 0
        for r, g, b, a in datas:
            if a > 0 and r >= cutoff and g >= cutoff and b >= cutoff:
                newData.append((r, g, b, 0))
                pixels_changed += 1
            else:
                newData.append((r, g, b, a))

        del datas # Free memory

        # --- 4. Apply changes (if any) ---
        if pixels_changed > 0:
            log.info(f"Pixels made transparent: {pixels_changed}")
            expected_len = img_rgba.width * img_rgba.height
            if len(newData) == expected_len:
                try:
                    img_rgba.putdata(newData)
                    log.debug("Pixel data updated successfully.")
                    final_image = img_rgba
                    img_rgba = None
                except Exception as e:
                    log.error(f"Error using putdata: {e}", exc_info=True)
                    final_image = img_rgba # Return RGBA before failed putdata
                    img_rgba = None
            else:
                log.error(f"Pixel data length mismatch (expected {expected_len}, got {len(newData)}). Skipping update.")
                final_image = img_rgba # Return RGBA before failed putdata
                img_rgba = None
        else:
            log.debug("No white pixels found to make transparent.")
            final_image = img_rgba # Return the RGBA copy/conversion
            img_rgba = None

    except Exception as e:
        log.error(f"General error in remove_white_background_full: {e}", exc_info=True)
        final_image = img_rgba if img_rgba else img
        img_rgba = None
    finally:
        if img_rgba and img_rgba is not final_image:
            safe_close(img_rgba)

    log.debug(f"remove_white_background_full returning image mode: {final_image.mode if final_image else 'None'}")
    return final_image

def remove_white_background_edges(img, tolerance):
    """
    Turns white/near-white pixels transparent, but only from the edges inward 
    until a non-white pixel boundary is reached.
    
    Args:
        img (PIL.Image.Image): Input image.
        tolerance (int): Tolerance for white (0=only 255, 255=all).
    
    Returns:
        PIL.Image.Image: Processed image in RGBA mode.
    """
    img_rgba = None
    final_image = img  # Default to original if critical error
    original_mode = img.mode

    try:
        # --- 1. Ensure RGBA ---
        if original_mode != 'RGBA':
            try:
                img_rgba = img.convert('RGBA')
                log.debug(f"Converted {original_mode} -> RGBA for edge removal")
            except Exception as e:
                log.error(f"Failed to convert image to RGBA for edge removal: {e}", exc_info=True)
                return img  # Critical error, return as is
        else:
            img_rgba = img.copy()
            log.debug("Created RGBA copy for edge removal (original was RGBA)")

        width, height = img_rgba.size
        cutoff = 255 - tolerance

        # --- 2. Create a mask to track processed pixels ---
        # 0 = not processed, 1 = white (to be transparent), 2 = non-white (preserve)
        from numpy import zeros
        mask = zeros((height, width), dtype=int)
        
        # --- 3. Get pixel data as numpy array for faster processing ---
        import numpy as np
        data = np.array(img_rgba)
        
        # --- 4. Apply flood fill from the edges ---
        # Queue of pixels to check (starting with all edge pixels)
        pixels_to_check = []
        
        # Add all edge pixels to the queue
        for y in range(height):
            pixels_to_check.append((0, y))  # Left edge
            pixels_to_check.append((width-1, y))  # Right edge
            mask[y, 0] = 1  # Mark as white initially
            mask[y, width-1] = 1  # Mark as white initially
            
        for x in range(width):
            pixels_to_check.append((x, 0))  # Top edge
            pixels_to_check.append((x, height-1))  # Bottom edge
            mask[0, x] = 1  # Mark as white initially
            mask[height-1, x] = 1  # Mark as white initially
        
        # Process the queue
        pixels_changed = 0
        while pixels_to_check:
            x, y = pixels_to_check.pop(0)
            
            # Skip if already marked as non-white
            if mask[y, x] == 2:
                continue
                
            # Get the pixel value
            r, g, b, a = data[y, x]
            
            # Check if white (according to tolerance)
            is_white = a > 0 and r >= cutoff and g >= cutoff and b >= cutoff
            
            if is_white:
                # Mark as white and add neighbors to check
                mask[y, x] = 1
                pixels_changed += 1
                
                # Add the 4 neighboring pixels to the queue
                neighbors = []
                if x > 0 and mask[y, x-1] == 0:
                    neighbors.append((x-1, y))
                    mask[y, x-1] = 1  # Mark as tentatively white
                if x < width-1 and mask[y, x+1] == 0:
                    neighbors.append((x+1, y))
                    mask[y, x+1] = 1  # Mark as tentatively white
                if y > 0 and mask[y-1, x] == 0:
                    neighbors.append((x, y-1))
                    mask[y-1, x] = 1  # Mark as tentatively white
                if y < height-1 and mask[y+1, x] == 0:
                    neighbors.append((x, y+1))
                    mask[y+1, x] = 1  # Mark as tentatively white
                    
                pixels_to_check.extend(neighbors)
            else:
                # Mark as non-white boundary
                mask[y, x] = 2
        
        # --- 5. Apply the mask to the image data ---
        for y in range(height):
            for x in range(width):
                if mask[y, x] == 1:  # White pixel to make transparent
                    data[y, x, 3] = 0  # Set alpha to 0
        
        # --- 6. Create a new image from the modified data ---
        from PIL import Image
        result_img = Image.fromarray(data)
        
        log.info(f"Edge-only removal: {pixels_changed} pixels made transparent")
        final_image = result_img
        img_rgba = None

    except Exception as e:
        log.error(f"General error in remove_white_background_edges: {e}", exc_info=True)
        final_image = img_rgba if img_rgba else img
        img_rgba = None
    finally:
        if img_rgba and img_rgba is not final_image:
            safe_close(img_rgba)

    log.debug(f"remove_white_background_edges returning image mode: {final_image.mode if final_image else 'None'}")
    return final_image


def crop_image(img, symmetric_axes=False, symmetric_absolute=False, extra_crop_percent=0.0):
    """
    Crops transparent borders from an image (assuming RGBA).
    No longer adds a 1px padding around the non-transparent area.
    Includes options for symmetrical cropping.
    
    Args:
        img: Input image (RGBA)
        symmetric_axes: Whether to apply symmetrical cropping along each axis independently
        symmetric_absolute: Whether to apply absolute symmetrical cropping
        extra_crop_percent: Additional percentage to crop after the main crop (0.0 to 100.0)
    
    Returns:
        Cropped image
    """
    crop_mode = "Standard"
    if symmetric_absolute: crop_mode = "Absolute Symmetric"
    elif symmetric_axes: crop_mode = "Axes Symmetric"
    log.debug(f"Attempting crop (Mode: {crop_mode}, Extra crop: {extra_crop_percent}%). Expecting RGBA input.")

    img_rgba = None; cropped_img = None
    final_image = img # Return original by default

    try:
        # Ensure RGBA and work on a copy
        if img.mode != 'RGBA':
            log.warning("Input image for crop is not RGBA. Converting.")
            try:
                img_rgba = img.convert('RGBA')
            except Exception as e:
                log.error(f"Failed to convert to RGBA for cropping: {e}. Cropping cancelled.", exc_info=True)
                return img # Return original if conversion fails
        else:
            img_rgba = img.copy()

        # Get bounding box of non-transparent pixels
        bbox = img_rgba.getbbox()

        if not bbox:
            log.info("No non-transparent pixels found (bbox is None). Cropping skipped.")
            final_image = img_rgba # Return the RGBA copy/conversion
            img_rgba = None
            return final_image

        original_width, original_height = img_rgba.size
        left, upper, right, lower = bbox

        # Validate bbox
        if left >= right or upper >= lower:
            log.error(f"Invalid bounding box found: {bbox}. Cropping cancelled.")
            final_image = img_rgba; img_rgba = None
            return final_image

        log.debug(f"Found bbox of non-transparent pixels: L={left}, T={upper}, R={right}, B={lower}")

        # Determine crop box based on symmetry settings
        crop_l, crop_u, crop_r, crop_b = left, upper, right, lower # Start with standard bbox

        if symmetric_absolute:
            log.debug("Calculating absolute symmetric crop box...")
            dist_left = left
            dist_top = upper
            dist_right = original_width - right
            dist_bottom = original_height - lower
            min_dist = min(dist_left, dist_top, dist_right, dist_bottom)
            log.debug(f"Distances: L={dist_left}, T={dist_top}, R={dist_right}, B={dist_bottom} -> Min Dist: {min_dist}")
            new_left = min_dist
            new_upper = min_dist
            new_right = original_width - min_dist
            new_lower = original_height - min_dist
            if new_left < new_right and new_upper < new_lower:
                crop_l, crop_u, crop_r, crop_b = new_left, new_upper, new_right, new_lower
                log.debug(f"Using absolute symmetric box: ({crop_l}, {crop_u}, {crop_r}, {crop_b})")
            else:
                log.warning("Calculated absolute symmetric box is invalid. Using standard bbox.")

        elif symmetric_axes:
            log.debug("Calculating axes symmetric crop box...")
            # Calculate distances from content to each edge
            dist_left = left
            dist_top = upper
            dist_right = original_width - right
            dist_bottom = original_height - lower
            
            log.debug(f"Distances: L={dist_left}, T={dist_top}, R={dist_right}, B={dist_bottom}")
            
            # Calculate minimum distances for each axis separately
            min_x_dist = min(dist_left, dist_right)  # Minimum of left and right
            min_y_dist = min(dist_top, dist_bottom)  # Minimum of top and bottom
            
            log.debug(f"Min distances: X-axis={min_x_dist}, Y-axis={min_y_dist}")
            
            # Apply symmetric cropping for X-axis
            new_left = min_x_dist
            new_right = original_width - min_x_dist
            
            # Apply symmetric cropping for Y-axis
            new_upper = min_y_dist
            new_lower = original_height - min_y_dist
            
            if new_left < new_right and new_upper < new_lower:
                crop_l, crop_u, crop_r, crop_b = new_left, new_upper, new_right, new_lower
                log.debug(f"Using axes symmetric box: ({crop_l}, {crop_u}, {crop_r}, {crop_b})")
            else:
                log.warning("Calculated axes symmetric box is invalid. Using standard bbox.")
                crop_l, crop_u, crop_r, crop_b = left, upper, right, lower

        # Use exact crop box without adding 1px padding
        final_crop_box = (crop_l, crop_u, crop_r, crop_b)

        # Check if cropping is actually needed
        if final_crop_box == (0, 0, original_width, original_height):
            log.debug("Final crop box matches image size. Cropping not needed.")
            final_image = img_rgba # Return the RGBA copy
            img_rgba = None
        else:
            log.debug(f"Final crop box (exact, no padding): {final_crop_box}")
            try:
                cropped_img = img_rgba.crop(final_crop_box)
                log.info(f"Cropped image size: {cropped_img.size}")
                
                # Apply additional crop based on percentage if specified
                if extra_crop_percent > 0:
                    crop_img_width, crop_img_height = cropped_img.size
                    
                    # Calculate extra pixels to crop from each side
                    extra_crop_pixels_x = int(round(crop_img_width * (extra_crop_percent / 100.0) / 2))
                    extra_crop_pixels_y = int(round(crop_img_height * (extra_crop_percent / 100.0) / 2))
                    
                    # Ensure we don't crop the entire image
                    if extra_crop_pixels_x * 2 >= crop_img_width or extra_crop_pixels_y * 2 >= crop_img_height:
                        log.warning(f"Extra crop of {extra_crop_percent}% would remove entire image. Skipping extra crop.")
                    else:
                        # Calculate the new crop box
                        extra_crop_box = (
                            extra_crop_pixels_x,
                            extra_crop_pixels_y,
                            crop_img_width - extra_crop_pixels_x,
                            crop_img_height - extra_crop_pixels_y
                        )
                        log.debug(f"Applying extra crop of {extra_crop_percent}%, box: {extra_crop_box}")
                        
                        # Apply the extra crop
                        extra_cropped_img = cropped_img.crop(extra_crop_box)
                        safe_close(cropped_img)
                        cropped_img = extra_cropped_img
                        log.info(f"Final size after extra crop: {cropped_img.size}")
                
                final_image = cropped_img
                cropped_img = None # Prevent closing in finally
            except Exception as e:
                log.error(f"Error during cropping operation: {e}. Cropping cancelled.", exc_info=True)
                final_image = img_rgba # Return RGBA copy before failed crop
                img_rgba = None

    except Exception as general_error:
        log.error(f"General error in crop_image: {general_error}", exc_info=True)
        final_image = img # Fallback to original input on severe error
    finally:
        # Close intermediate objects if they weren't the final result
        if img_rgba and img_rgba is not final_image:
            safe_close(img_rgba)
        if cropped_img and cropped_img is not final_image:
            safe_close(cropped_img)

    return final_image


def add_padding(img, percent, allow_expansion=True):
    """Adds transparent padding around the image (expects RGBA). Negative values will crop the image."""
    if img is None:
        return img

    if img.mode != 'RGBA':
        log.warning("Input image for add_padding is not RGBA. Converting.")
        try:
             img = img.convert("RGBA")
        except Exception as e:
            log.error(f"Failed to convert to RGBA for padding: {e}. Padding cancelled.", exc_info=True)
            return img # Return original on conversion error

    w, h = img.size
    if w == 0 or h == 0:
        log.warning("add_padding warning: Input image has zero size.")
        return img

    # Calculate padding/cropping pixels based on the larger dimension
    padding_pixels = int(round(max(w, h) * (percent / 100.0)))
    
    if padding_pixels == 0:
        log.debug("Padding/cropping skipped (calculated value is zero).")
        return img

    if padding_pixels > 0:
        # Adding padding
        new_width = w + 2 * padding_pixels
        new_height = h + 2 * padding_pixels

        # Check if expansion is allowed
        if not allow_expansion and (new_width > w or new_height > h):
            log.debug("Padding skipped (expansion not allowed and new size would exceed original).")
            return img

        log.info(f"Adding padding: {percent}% ({padding_pixels}px). New size: {new_width}x{new_height}")

        padded_img = None
        try:
            # Create a new transparent canvas
            padded_img = Image.new('RGBA', (new_width, new_height), (0, 0, 0, 0))
            # Paste the original image onto the canvas, centered
            paste_pos = (padding_pixels, padding_pixels)
            padded_img.paste(img, paste_pos, mask=img) # Use img as mask since it's RGBA
            log.debug("Pasted image onto new padded canvas.")
            # Close the original image passed to the function
            safe_close(img)
            return padded_img # Return the new padded image
        except Exception as e:
            log.error(f"Error during paste or other operation in add_padding: {e}", exc_info=True)
            safe_close(padded_img) # Close the canvas if created but failed
            return img # Return the original image on error
    else:
        # Cropping the image
        crop_pixels = abs(padding_pixels)
        new_width = max(1, w - 2 * crop_pixels)
        new_height = max(1, h - 2 * crop_pixels)
        
        if new_width <= 0 or new_height <= 0:
            log.warning("Cropping would result in zero or negative dimensions. Skipping.")
            return img
            
        log.info(f"Cropping image: {percent}% ({crop_pixels}px). New size: {new_width}x{new_height}")
        
        try:
            # Calculate the crop box
            left = crop_pixels
            top = crop_pixels
            right = w - crop_pixels
            bottom = h - crop_pixels
            
            cropped_img = img.crop((left, top, right, bottom))
            safe_close(img)
            return cropped_img
        except Exception as e:
            log.error(f"Error during cropping in add_padding: {e}", exc_info=True)
            return img # Return the original image on error

def check_perimeter_is_white(img, tolerance, margin):
    """
    Checks if the perimeter of the image is white (using tolerance).
    Handles transparency by checking against a white background simulation.
    Returns:
        bool: True if the perimeter is considered white, False otherwise.
    """
    if img is None or margin <= 0:
        if margin <= 0 : log.debug("Perimeter check skipped (margin is zero or negative).")
        return False

    log.debug(f"Checking perimeter white (tolerance: {tolerance}, margin: {margin}px)...")
    log.debug(f"DIAGNOSTICS: Image mode: {img.mode}, Size: {img.size}, ID: {id(img)}")
    
    img_to_check = None
    created_new_object = False
    mask = None
    is_white = False # Default to False

    try:
        # Prepare an RGB version, simulating a white background if alpha is present
        if img.mode == 'RGBA' or 'A' in img.getbands():
            try:
                log.debug(f"DIAGNOSTICS: Creating white RGB canvas for alpha image: {id(img)}")
                img_to_check = Image.new("RGB", img.size, (255, 255, 255))
                created_new_object = True
                log.debug(f"DIAGNOSTICS: Created white RGB canvas: {id(img_to_check)}")
                
                # Get alpha mask
                if img.mode == 'RGBA':
                    mask = img.getchannel('A')
                    log.debug(f"DIAGNOSTICS: Extracted alpha channel from RGBA: {id(mask)}")
                else: # Handle modes like 'LA', 'PA'
                    with img.convert('RGBA') as temp_rgba:
                        log.debug(f"DIAGNOSTICS: Converted to RGBA temporarily: {id(temp_rgba)}")
                        mask = temp_rgba.getchannel('A')
                        log.debug(f"DIAGNOSTICS: Extracted alpha channel from converted: {id(mask)}")
                
                # Paste using the mask
                log.debug(f"DIAGNOSTICS: About to paste image {id(img)} onto canvas {id(img_to_check)}")
                img_to_check.paste(img, mask=mask)
                log.debug(f"DIAGNOSTICS: Paste operation completed")
                
            except Exception as paste_err:
                log.error(f"Failed to create or paste onto white background for perimeter check: {paste_err}", exc_info=True)
                # Fallback or return False? Let's return False as we can't check.
                log.debug(f"DIAGNOSTICS: Error during paste, closing resources: mask={id(mask) if mask else 'None'}, img_to_check={id(img_to_check) if img_to_check and created_new_object else 'None'}")
                safe_close(mask)
                if created_new_object: safe_close(img_to_check)
                return False
        elif img.mode != 'RGB':
             # If no alpha but not RGB, convert to RGB
             try:
                 log.debug(f"DIAGNOSTICS: Converting {img.mode} image {id(img)} to RGB")
                 img_to_check = img.convert('RGB')
                 created_new_object = True
                 log.debug(f"DIAGNOSTICS: Converted to RGB: {id(img_to_check)}")
             except Exception as conv_e:
                 log.error(f"Failed to convert {img.mode} -> RGB for perimeter check: {conv_e}", exc_info=True)
                 return False # Cannot check
        else:
             # If already RGB, use it directly (no copy needed for reading pixels)
             img_to_check = img
             created_new_object = False
             log.debug(f"DIAGNOSTICS: Using original RGB image directly: {id(img_to_check)}")

        width, height = img_to_check.size
        if width <= 0 or height <= 0:
            log.warning(f"Image for perimeter check has zero size ({width}x{height}).")
            # Clean up resources if created
            log.debug(f"DIAGNOSTICS: Zero size image, closing resources: mask={id(mask) if mask else 'None'}, img_to_check={id(img_to_check) if img_to_check and created_new_object else 'None'}")
            safe_close(mask)
            if created_new_object: safe_close(img_to_check)
            return False

        # Calculate effective margin, ensuring it's not > half the dimension
        # and at least 1px if margin > 0 and dimension allows
        margin_h = min(margin, height // 2 if height > 0 else 0)
        margin_w = min(margin, width // 2 if width > 0 else 0)
        # Ensure at least 1px if requested and possible
        if margin_h == 0 and height > 0 and margin > 0: margin_h = 1
        if margin_w == 0 and width > 0 and margin > 0: margin_w = 1

        if margin_h == 0 or margin_w == 0:
            log.warning(f"Cannot check perimeter with margin {margin}px on image {width}x{height}. Effective margins are W={margin_w}, H={margin_h}.")
            log.debug(f"DIAGNOSTICS: Invalid margins, closing resources: mask={id(mask) if mask else 'None'}, img_to_check={id(img_to_check) if img_to_check and created_new_object else 'None'}")
            safe_close(mask)
            if created_new_object: safe_close(img_to_check)
            return False

        # --- Check pixels ---
        log.debug(f"DIAGNOSTICS: About to load pixels from {id(img_to_check)}")
        try:
            pixels = img_to_check.load()
            log.debug(f"DIAGNOSTICS: Pixels loaded successfully")
        except Exception as px_err:
            log.error(f"Error loading pixels: {px_err}", exc_info=True)
            log.debug(f"DIAGNOSTICS: Error loading pixels, closing resources")
            safe_close(mask)
            if created_new_object: safe_close(img_to_check)
            return False
            
        cutoff = 255 - tolerance
        is_perimeter_white = True

        # Iterate over perimeter pixels efficiently
        log.debug(f"DIAGNOSTICS: Starting perimeter check: width={width}, height={height}, margin_w={margin_w}, margin_h={margin_h}, cutoff={cutoff}")
        
        # Top margin_h rows
        for y in range(margin_h):
            for x in range(width):
                try: r, g, b = pixels[x, y][:3]
                except (IndexError, TypeError): 
                    log.debug(f"DIAGNOSTICS: Pixel access error at ({x},{y})")
                    is_perimeter_white = False; break
                if not (r >= cutoff and g >= cutoff and b >= cutoff): 
                    log.debug(f"DIAGNOSTICS: Non-white pixel found at ({x},{y}): RGB=({r},{g},{b})")
                    is_perimeter_white = False; break
            if not is_perimeter_white: break
        if not is_perimeter_white: log.debug("Non-white pixel found in top margin.");

        # Bottom margin_h rows (if not already failed)
        if is_perimeter_white:
            for y in range(height - margin_h, height):
                for x in range(width):
                    try: r, g, b = pixels[x, y][:3]
                    except (IndexError, TypeError): 
                        log.debug(f"DIAGNOSTICS: Pixel access error at ({x},{y})")
                        is_perimeter_white = False; break
                    if not (r >= cutoff and g >= cutoff and b >= cutoff): 
                        log.debug(f"DIAGNOSTICS: Non-white pixel found at ({x},{y}): RGB=({r},{g},{b})")
                        is_perimeter_white = False; break
                if not is_perimeter_white: break
            if not is_perimeter_white: log.debug("Non-white pixel found in bottom margin.");

        # Left margin_w columns (excluding top/bottom margins already checked)
        if is_perimeter_white:
            for x in range(margin_w):
                for y in range(margin_h, height - margin_h):
                     try: r, g, b = pixels[x, y][:3]
                     except (IndexError, TypeError): 
                         log.debug(f"DIAGNOSTICS: Pixel access error at ({x},{y})")
                         is_perimeter_white = False; break
                     if not (r >= cutoff and g >= cutoff and b >= cutoff): 
                         log.debug(f"DIAGNOSTICS: Non-white pixel found at ({x},{y}): RGB=({r},{g},{b})")
                         is_perimeter_white = False; break
                if not is_perimeter_white: break
            if not is_perimeter_white: log.debug("Non-white pixel found in left margin.");

        # Right margin_w columns (excluding top/bottom margins already checked)
        if is_perimeter_white:
            for x in range(width - margin_w, width):
                 for y in range(margin_h, height - margin_h):
                     try: r, g, b = pixels[x, y][:3]
                     except (IndexError, TypeError):
                         log.debug(f"DIAGNOSTICS: Pixel access error at ({x},{y})")
                         is_perimeter_white = False;
                         break
                     if not (r >= cutoff and g >= cutoff and b >= cutoff):
                         log.debug(f"DIAGNOSTICS: Non-white pixel found at ({x},{y}): RGB=({r},{g},{b})")
                         is_perimeter_white = False;
                         break
                 if not is_perimeter_white:
                    break
            if not is_perimeter_white: log.debug("Non-white pixel found in right margin.");

        is_white = is_perimeter_white # Store the final result
        log.info(f"Perimeter check result: {'White' if is_white else 'NOT White'}")

    except Exception as e:
        log.error(f"General error in check_perimeter_is_white: {e}", exc_info=True)
        is_white = False # Return False on error
    finally:
         log.debug(f"DIAGNOSTICS: Cleanup in finally block: mask={id(mask) if mask else 'None'}, img_to_check={id(img_to_check) if img_to_check else 'None'}, created_new_object={created_new_object}")
         safe_close(mask) # Close alpha mask if extracted
         # Close the checked image only if we created a new object
         if created_new_object and img_to_check:
             log.debug(f"DIAGNOSTICS: Closing img_to_check={id(img_to_check)}")
             safe_close(img_to_check)
         log.debug(f"DIAGNOSTICS: Finished perimeter check, is_white={is_white}")

    return is_white

# === НОВАЯ ФУНКЦИЯ (ИСПРАВЛЕННАЯ + ДОП. ЛОГИ) ===
def apply_brightness_contrast(img: Optional[Image.Image], brightness_factor: float = 1.0, contrast_factor: float = 1.0) -> Optional[Image.Image]:
    """Применяет яркость и контраст к изображению, сохраняя альфа-канал."""
    # Убираем проверку на маленькую разницу для отладки
    # if not img or (abs(brightness_factor - 1.0) < 0.01 and abs(contrast_factor - 1.0) < 0.01):
    if not img or (brightness_factor == 1.0 and contrast_factor == 1.0):
        log.debug("  Brightness/Contrast adjustment skipped (factors are 1.0 or no image).")
        return img

    img_copy = None; img_rgb = None; alpha_channel = None; adjusted_rgb = None; final_image = img
    try:
        log.info(f"--> Applying Brightness/Contrast (B:{brightness_factor:.2f}, C:{contrast_factor:.2f}) to mode {img.mode}") # Используем INFO для заметности
        img_copy = img.copy()
        original_mode = img_copy.mode
        has_alpha = 'A' in img_copy.getbands()

        # Извлекаем RGB и Alpha
        if has_alpha:
            split_bands = img_copy.split()
            if len(split_bands) == 4:
                img_rgb = Image.merge('RGB', split_bands[:3])
                alpha_channel = split_bands[3]
                for band in split_bands[:3]: safe_close(band)
                log.debug("    Separated RGB and Alpha channels.")
            else: 
                log.warning(f"    Unexpected bands ({len(split_bands)}) with Alpha. Converting base to RGB.")
                img_rgb = img_copy.convert('RGB')
                has_alpha = False 
        elif original_mode != 'RGB':
            img_rgb = img_copy.convert('RGB')
            log.debug(f"    Converted {original_mode} to RGB for adjustments.")
        else:
            img_rgb = img_copy # Уже RGB, используем копию
            log.debug("    Using original RGB image for adjustments.")
        
        adjusted_rgb = img_rgb # Начинаем с RGB
        img_rgb = None 

        # Применяем ЯРКОСТЬ
        if brightness_factor != 1.0:
            log.info(f"    Adjusting brightness: factor={brightness_factor:.2f}")
            log.debug(f"      Before Brightness: {repr(adjusted_rgb)}")
            enhancer_b = ImageEnhance.Brightness(adjusted_rgb)
            temp_img_b = enhancer_b.enhance(brightness_factor)
            log.debug(f"      After Brightness Enhance: {repr(temp_img_b)}")
            if temp_img_b is not adjusted_rgb: safe_close(adjusted_rgb)
            adjusted_rgb = temp_img_b
        else:
            log.debug("    Brightness factor is 1.0, skipping adjustment.")

        # Применяем КОНТРАСТ
        if contrast_factor != 1.0:
            log.info(f"    Adjusting contrast: factor={contrast_factor:.2f}")
            log.debug(f"      Before Contrast: {repr(adjusted_rgb)}")
            enhancer_c = ImageEnhance.Contrast(adjusted_rgb)
            temp_img_c = enhancer_c.enhance(contrast_factor)
            log.debug(f"      After Contrast Enhance: {repr(temp_img_c)}")
            if temp_img_c is not adjusted_rgb: safe_close(adjusted_rgb)
            adjusted_rgb = temp_img_c
        else:
            log.debug("    Contrast factor is 1.0, skipping adjustment.")
        
        # Восстанавливаем альфа-канал
        if alpha_channel:
            log.debug("    Restoring original alpha channel...")
            if adjusted_rgb and adjusted_rgb.size == alpha_channel.size:
                 adjusted_rgb.putalpha(alpha_channel)
                 final_image = adjusted_rgb 
                 adjusted_rgb = None
                 log.info(f"--> Brightness/Contrast applied. Final mode: {final_image.mode}")
            else:
                 log.error(f"    ! Size mismatch adding alpha or adjusted_rgb is None. Returning RGB.")
                 final_image = adjusted_rgb # Возвращаем RGB
                 adjusted_rgb = None
                 log.info(f"--> Brightness/Contrast partially applied (RGB only due to alpha error). Final mode: {final_image.mode if final_image else 'None'}")
        else:
            final_image = adjusted_rgb # Результат - RGB
            adjusted_rgb = None
            log.info(f"--> Brightness/Contrast applied. Final mode: {final_image.mode if final_image else 'None'}")

        # Важно: Возвращаем именно final_image
        return final_image

    except Exception as e:
        log.error(f"  ! Error applying brightness/contrast: {e}", exc_info=True)
        return img # Возвращаем оригинал в случае ошибки
    finally:
        safe_close(img_copy)
        safe_close(img_rgb) 
        safe_close(alpha_channel)
        # Закрываем adjusted_rgb только если он не стал final_image
        if adjusted_rgb and adjusted_rgb is not final_image: safe_close(adjusted_rgb)
# ======================

# === Конец Файла image_utils.py ===
# Добавляю новые функции для анализа границ и последующей обрезки

def find_crop_boundaries(img, white_tolerance, symmetric_absolute=False, symmetric_axes=False, extra_crop_percent=0.0):
    """
    Анализирует изображение и находит границы прозрачной области,
    но не выполняет фактическую обрезку.
    
    Args:
        img: Входное изображение (RGBA)
        white_tolerance: Порог определения белого цвета
        symmetric_absolute: Применять ли абсолютно симметричную обрезку
        symmetric_axes: Применять ли симметричную обрезку по осям независимо
        extra_crop_percent: Дополнительный процент обрезки (0.0 - 100.0)
    
    Returns:
        dict: Границы обрезки (top, bottom, left, right) или None в случае ошибки
    """
    if img is None or img.width <= 0 or img.height <= 0:
        return None
    
    try:
        # Конвертируем в RGBA для анализа
        img_rgba = None
        if img.mode != 'RGBA':
            try:
                img_rgba = img.convert('RGBA')
            except Exception as e:
                log.error(f"Failed to convert to RGBA for boundary analysis: {e}")
                return None
        else:
            img_rgba = img
        
        # Находим непрозрачную область
        bbox = img_rgba.getbbox()
        if not bbox:
            return None
            
        original_width, original_height = img_rgba.size
        left, upper, right, lower = bbox
        
        # Проверяем корректность bbox
        if left >= right or upper >= lower:
            return None
            
        # Определяем границы обрезки в зависимости от настроек симметрии
        crop_l, crop_u, crop_r, crop_b = left, upper, right, lower
        
        if symmetric_absolute:
            # Абсолютно симметричная обрезка (одинаковое расстояние со всех сторон)
            dist_left = left
            dist_top = upper
            dist_right = original_width - right
            dist_bottom = original_height - lower
            min_dist = min(dist_left, dist_top, dist_right, dist_bottom)
            
            crop_l = min_dist
            crop_u = min_dist
            crop_r = original_width - min_dist
            crop_b = original_height - min_dist
            
            # Проверка валидности границ
            if crop_l >= crop_r or crop_u >= crop_b:
                # Используем стандартные границы, если симметричные некорректны
                crop_l, crop_u, crop_r, crop_b = left, upper, right, lower
                
        elif symmetric_axes:
            # Симметричная обрезка по осям независимо
            dist_left = left
            dist_top = upper
            dist_right = original_width - right
            dist_bottom = original_height - lower
            
            min_x_dist = min(dist_left, dist_right)
            min_y_dist = min(dist_top, dist_bottom)
            
            crop_l = min_x_dist
            crop_u = min_y_dist
            crop_r = original_width - min_x_dist
            crop_b = original_height - min_y_dist
            
            # Проверка валидности границ
            if crop_l >= crop_r or crop_u >= crop_b:
                # Используем стандартные границы, если симметричные некорректны
                crop_l, crop_u, crop_r, crop_b = left, upper, right, lower
        
        # Применяем дополнительную обрезку, если указана
        if extra_crop_percent > 0:
            crop_width = crop_r - crop_l
            crop_height = crop_b - crop_u
            
            extra_crop_x = int(round(crop_width * (extra_crop_percent / 100.0) / 2))
            extra_crop_y = int(round(crop_height * (extra_crop_percent / 100.0) / 2))
            
            # Проверяем, чтобы не обрезать всё изображение
            if (extra_crop_x * 2 < crop_width) and (extra_crop_y * 2 < crop_height):
                crop_l += extra_crop_x
                crop_r -= extra_crop_x
                crop_u += extra_crop_y
                crop_b -= extra_crop_y
        
        # Возвращаем финальные границы
        return {
            'left': crop_l,
            'top': crop_u,
            'right': crop_r,
            'bottom': crop_b
        }
        
    except Exception as e:
        log.error(f"Error analyzing crop boundaries: {e}")
        return None

def apply_crop_with_boundaries(img, boundaries):
    """
    Применяет заранее вычисленные границы обрезки к изображению.
    
    Args:
        img: Входное изображение
        boundaries: Словарь с границами обрезки (left, top, right, bottom)
    
    Returns:
        Image: Обрезанное изображение или None в случае ошибки
    """
    if img is None or boundaries is None:
        return img
        
    try:
        # Извлекаем границы
        left = boundaries.get('left', 0)
        top = boundaries.get('top', 0)
        right = boundaries.get('right', img.width)
        bottom = boundaries.get('bottom', img.height)
        
        # Проверяем корректность границ
        if left < 0: left = 0
        if top < 0: top = 0
        if right > img.width: right = img.width
        if bottom > img.height: bottom = img.height
        
        if left >= right or top >= bottom:
            log.error(f"Invalid crop boundaries: {boundaries}")
            return img
            
        # Применяем обрезку
        crop_box = (left, top, right, bottom)
        cropped_img = img.crop(crop_box)
        
        return cropped_img
        
    except Exception as e:
        log.error(f"Error applying crop with boundaries: {e}")
        return img