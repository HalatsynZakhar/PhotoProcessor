# config_manager.py

import json
import os
import logging
from typing import Dict, Any, Optional, Tuple, List # Для type hints
import shutil # Добавим для rename

log = logging.getLogger(__name__)

# Директория для хранения пресетов
PRESETS_DIR = "settings_presets"
DEFAULT_PRESET_NAME = "Набор 1"

# =============================
# === НАСТРОЙКИ ПО УМОЛЧАНИЮ ===
# =============================
DEFAULT_SETTINGS = {
    "active_preset": "Набор 1", 
    "processing_mode_selector": "Обработка отдельных файлов",
    "paths": {
        "input_folder_path": "", 
        "output_folder_path": "", 
        "backup_folder_path": os.path.join(os.path.expanduser('~'), "Downloads", "Backups"), # Бэкап по умолчанию в папке Downloads/Backups
        "output_filename": "collage"
    },
    "preprocessing": {
        "enable_preresize": False,
        "preresize_width": 0,
        "preresize_height": 0
    },
    "whitening": {
        "enable_whitening": False,
        "whitening_cancel_threshold": 765
    },
    "background_crop": {
        "enable_bg_crop": False,
        "white_tolerance": 10,
        "perimeter_tolerance": 10,
        "crop_symmetric_absolute": False,
        "crop_symmetric_axes": False,
        "check_perimeter": True,
        "enable_crop": True,
        "perimeter_mode": "if_white",
        "removal_mode": "full",
        "extra_crop_percent": 0.0,
        "use_mask_instead_of_transparency": False,
        "halo_reduction_level": 0
    },
    "padding": {
        "mode": "never",
        "padding_percent": 0.0,
        "allow_expansion": False,
        "perimeter_check_tolerance": 10,
        "perimeter_margin": 1
    },
    "brightness_contrast": {
        "enable_bc": False,
        "brightness_factor": 1.0,
        "contrast_factor": 1.0
    },
    "merge_settings": {
        "enable_merge": False,
        "template_path": "",
        "template_position": "center",  # Позиция шаблона по умолчанию
        "position": "center",  # Позиция изображения по умолчанию
        "template_on_top": True,
        "process_template": False,
        "width_ratio": [1.0, 1.0],
        "enable_width_ratio": False,
        "fit_image_to_template": False,
        "fit_template_to_image": False,
        "no_scaling": False
    },
    "ui_display": {
        "show_results": True,           # Отображать результаты обработки
        "show_all_images": False,       # Показывать все обработанные изображения
        "columns_count": 3,             # Количество столбцов в сетке изображений
        "images_limit": 18              # Лимит количества отображаемых изображений
    },
    "single": {
        "enabled_steps": { # Все шаги по умолчанию выключены
            "resize": False,
            "border": False,
            "logo": False
        },
        "save_formats": { # Все форматы сохранения по умолчанию выключены
            "png": False,
            "jpg": False,
            "webp": False
        },
        "resize": {
            "mode": "По ширине",
            "width": 1920,
            "height": 1080
        },
        "padding": {
            "mode": "never",
            "padding_percent": 0.0,
            "allow_expansion": False,
            "perimeter_check_tolerance": 10,
            "perimeter_margin": 1
        },
        "border": {
            "width_px": 10,
            "color": "#000000"
        },
        "logo": {
            "path": "",
            "position": "bottom-right",
            "scale": 0.1,
            "opacity": 0.8,
            "padding_percent": 5
        },
        "save_options": {
            "jpg_quality": 95,
            "webp_quality": 90,
            "webp_lossless": False
        }
    },
    "collage": {
        "enabled_steps": { # Все шаги по умолчанию выключены
            "margins": False,
            "square": False,
            "borders": False
        },
        "save_formats": { # Все форматы сохранения по умолчанию выключены
            "png": False,
            "jpg": False,
            "webp": False
        },
        "layout": {
            "rows": 0,
            "cols": 0,
            "direction": "auto"
        },
        "margins": {
            "size_px": 50,
            "color": "#FFFFFF"
        },
        "square": {
            "fill_color": "#FFFFFF"
        },
        "borders": {
            "width_px": 5,
            "color": "#000000"
        },
        "save_options": {
            "jpg_quality": 95,
            "webp_quality": 90,
            "webp_lossless": False,
            "remove_metadata": False
        },
        "output_format": "jpg",
        "jpeg_quality": 95,
        "jpg_background_color": [255, 255, 255],
        "png_transparent_background": True,
        "png_background_color": [255, 255, 255],
        "remove_metadata": False,
        "fixed_modification_date": False
    },
    "individual_mode": {
        "enable_rename": False,
        "article_name": "",
        "delete_originals": False,
        "output_format": "jpg",
        "jpg_background_color": [255, 255, 255],
        "jpeg_quality": 95,
        "png_transparent_background": True,
        "png_background_color": [255, 255, 255],
        "enable_force_aspect_ratio": False,
        "force_aspect_ratio": [1, 1],
        "enable_max_dimensions": False,
        "max_output_width": 1500,
        "max_output_height": 1500,
        "enable_exact_canvas": True,
        "final_exact_width": 1500,
        "final_exact_height": 1500,
        "remove_metadata": False,
        "reverse_date_order": False,
        "special_first_file": False,
        "first_file_settings": {
            "enable_force_aspect_ratio": False,
            "force_aspect_ratio": [1, 1],
            "enable_max_dimensions": False,
            "max_output_width": 1500,
            "max_output_height": 1500,
            "enable_exact_canvas": True,
            "final_exact_width": 1500,
            "final_exact_height": 1500
        }
    },
    "performance": {
        "enable_multiprocessing": False,
        "max_workers": 0,  # 0 или None означает автоматический выбор (по количеству ядер CPU)
        "memory_limit": 0  # 0 означает без ограничений
    }
}

def get_default_settings(ensure_all_keys=True):
    """
    Возвращает настройки по умолчанию.
    
    Args:
        ensure_all_keys: Если True, проверяет наличие всех необходимых ключей
        
    Returns:
        Словарь с настройками по умолчанию
    """
    settings = DEFAULT_SETTINGS.copy()
    
    if ensure_all_keys:
        settings = ensure_performance_settings(settings)
        # Другие проверки...
    
    return settings

def _ensure_presets_dir_exists():
    """Убеждается, что директория для пресетов существует."""
    if not os.path.isdir(PRESETS_DIR):
        try:
            os.makedirs(PRESETS_DIR)
            log.info(f"Created presets directory: {PRESETS_DIR}")
        except OSError as e:
            log.error(f"Could not create presets directory {PRESETS_DIR}: {e}")
            # В случае ошибки создания директории, функции работы с пресетами могут не работать

def _get_preset_filepath(preset_name: str) -> str:
    """Возвращает полный путь к файлу пресета."""
    # Убираем недопустимые символы из имени файла
    safe_filename = "".join(c for c in preset_name if c.isalnum() or c in (' ', '_', '-')).rstrip()
    if not safe_filename: # Если имя стало пустым
        safe_filename = "_invalid_preset_name_"
    return os.path.join(PRESETS_DIR, f"{safe_filename}.json")

def load_settings(filepath: str) -> Dict[str, Any]:
    """
    Загружает настройки из JSON-файла.
    Если файл не найден или поврежден, возвращает настройки по умолчанию.
    Объединяет загруженные настройки с дефолтными, чтобы гарантировать наличие всех ключей.
    """
    defaults = get_default_settings()
    if not os.path.exists(filepath):
        log.warning(f"Settings file not found: '{filepath}'. Using default settings.")
        return defaults

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            loaded_settings = json.load(f)
            log.info(f"Settings loaded successfully from: '{filepath}'")

            # --- Объединение с дефолтными ---
            # Это гарантирует, что все ключи из defaults будут присутствовать,
            # даже если их нет в файле (например, после обновления).
            # Значения из loaded_settings перезапишут значения из defaults.
            # Используем рекурсивное обновление для вложенных словарей.
            def update_recursive(d, u):
                for k, v in u.items():
                    if isinstance(v, dict):
                        d[k] = update_recursive(d.get(k, {}), v)
                    else:
                        # Преобразуем списки обратно в кортежи для цветов/соотношений, если нужно
                        # (Хотя, возможно, удобнее работать со списками везде)
                        # if k in ["jpg_background_color", "force_aspect_ratio", "force_collage_aspect_ratio"] and isinstance(v, list):
                        #    d[k] = tuple(v)
                        # else:
                        #    d[k] = v
                        d[k] = v # Оставляем как загрузилось (скорее всего списки)
                return d

            merged_settings = update_recursive(defaults.copy(), loaded_settings)
            return merged_settings

    except json.JSONDecodeError as e:
        log.error(f"Error decoding JSON from settings file '{filepath}': {e}. Using default settings.", exc_info=True)
        return defaults
    except Exception as e:
        log.error(f"Failed to load settings from '{filepath}': {e}. Using default settings.", exc_info=True)
        return defaults

def save_settings(settings_dict: Dict[str, Any], filepath: str) -> bool:
    """
    Сохраняет переданный словарь настроек в JSON-файл.
    Возвращает True при успехе, False при ошибке.
    """
    try:
        # Создаем директорию, если она не существует
        dirpath = os.path.dirname(filepath)
        if dirpath and not os.path.exists(dirpath):
            os.makedirs(dirpath)
            log.info(f"Created directory for settings file: '{dirpath}'")

        with open(filepath, 'w', encoding='utf-8') as f:
            # Используем indent для читаемости файла
            json.dump(settings_dict, f, indent=4, ensure_ascii=False)
        log.info(f"Settings saved successfully to: '{filepath}'")
        return True
    except Exception as e:
        log.error(f"Failed to save settings to '{filepath}': {e}", exc_info=True)
        return False

# === Функции для управления пресетами ===

def create_default_preset():
    """Создает файл пресета по умолчанию, если он не существует."""
    _ensure_presets_dir_exists()
    default_preset_path = _get_preset_filepath(DEFAULT_PRESET_NAME)
    if not os.path.exists(default_preset_path):
        log.info(f"Default preset '{DEFAULT_PRESET_NAME}' not found. Creating...")
        if save_settings_preset(get_default_settings(), DEFAULT_PRESET_NAME):
            log.info(f"Successfully created default preset: {default_preset_path}")
            return True
        else:
            log.error(f"Failed to create default preset: {default_preset_path}")
            return False
    return True

def get_available_presets() -> List[str]:
    """Возвращает список имен доступных пресетов."""
    _ensure_presets_dir_exists()
    presets = []
    try:
        for filename in os.listdir(PRESETS_DIR):
            if filename.lower().endswith('.json'):
                preset_name = os.path.splitext(filename)[0]
                # Пытаемся восстановить оригинальное имя, если оно было "безопасным"
                # Это простая эвристика, может не всегда работать идеально
                # Если мы хотим точного восстановления, нужно хранить оригинальное имя внутри JSON
                # Пока оставим так для простоты
                presets.append(preset_name) 
    except FileNotFoundError:
        log.warning(f"Presets directory not found: {PRESETS_DIR}. Returning empty list.")
        return []
    except Exception as e:
        log.error(f"Error reading presets directory {PRESETS_DIR}: {e}")
        return []
    
    # Убедимся, что дефолтный пресет всегда есть в списке и он первый
    if DEFAULT_PRESET_NAME not in presets:
        # Попробуем создать его на лету
        create_default_preset()
        # Перечитаем список
        presets = get_available_presets() # Рекурсивный вызов, но безопасный
    
    if DEFAULT_PRESET_NAME in presets:
        presets.remove(DEFAULT_PRESET_NAME)
        presets.insert(0, DEFAULT_PRESET_NAME)
        
    return presets

def save_settings_preset(settings: Dict[str, Any], preset_name: str) -> bool:
    """Сохраняет словарь настроек как пресет."""
    if not preset_name:
        log.warning("Attempted to save preset with empty name. Aborting.")
        return False
        
    _ensure_presets_dir_exists()
    preset_path = _get_preset_filepath(preset_name)
    log.info(f"Saving settings to preset: '{preset_name}' ({preset_path})")
    return save_settings(settings, preset_path) # Используем общую функцию сохранения

def load_settings_preset(preset_name: str) -> Optional[Dict[str, Any]]:
    """Загружает настройки из указанного пресета."""
    if not preset_name:
        log.warning("Attempted to load preset with empty name.")
        return None
        
    _ensure_presets_dir_exists() # На всякий случай
    preset_path = _get_preset_filepath(preset_name)
    log.info(f"Loading settings from preset: '{preset_name}' ({preset_path})")
    
    if not os.path.exists(preset_path):
        log.error(f"Preset file not found: {preset_path}")
        # Если не найден дефолтный, пытаемся создать и загрузить его
        if preset_name == DEFAULT_PRESET_NAME:
             log.warning("Default preset file missing. Attempting to recreate and load.")
             if create_default_preset():
                 # Повторная попытка загрузки
                 try:
                     with open(preset_path, 'r', encoding='utf-8') as f:
                         loaded_settings = json.load(f)
                     # Важно: Мержим с дефолтными, чтобы добавить новые ключи, если пресет старый
                     default_settings = get_default_settings()
                     default_settings.update(loaded_settings)
                     return default_settings
                 except Exception as e:
                     log.error(f"Failed to load recreated default preset {preset_path}: {e}")
                     return get_default_settings() # Возвращаем дефолт по коду
             else:
                 log.error("Failed to recreate default preset. Returning defaults from code.")
                 return get_default_settings() # Возвращаем дефолт по коду
        else:
            return None # Обычный пресет не найден
            
    try:
        with open(preset_path, 'r', encoding='utf-8') as f:
            loaded_settings = json.load(f)
        # Мержим с дефолтными настройками, чтобы гарантировать наличие всех ключей
        default_settings = get_default_settings()
        default_settings.update(loaded_settings)
        return default_settings
    except json.JSONDecodeError as e:
        log.error(f"Error decoding JSON from {preset_path}: {e}")
        # Можно вернуть дефолтные или None
        return None 
    except Exception as e:
        log.error(f"Error reading preset file {preset_path}: {e}")
        return None

def delete_settings_preset(preset_name: str) -> bool:
    """Удаляет файл пресета."""
    if preset_name == DEFAULT_PRESET_NAME:
        log.warning("Attempted to delete the default preset. Operation aborted.")
        return False
    if not preset_name:
        log.warning("Attempted to delete preset with empty name.")
        return False
        
    preset_path = _get_preset_filepath(preset_name)
    if os.path.exists(preset_path):
        try:
            os.remove(preset_path)
            log.info(f"Deleted preset: '{preset_name}' ({preset_path})")
            return True
        except OSError as e:
            log.error(f"Error deleting preset file {preset_path}: {e}")
            return False
    else:
        log.warning(f"Preset file not found, cannot delete: {preset_path}")
        return False # Или True, т.к. его и так нет?

def rename_settings_preset(old_name: str, new_name: str) -> bool:
    """Переименовывает пресет."""
    if old_name == DEFAULT_PRESET_NAME:
        log.warning("Cannot rename the default preset.")
        return False
    if new_name == DEFAULT_PRESET_NAME:
        log.warning("Cannot rename a preset to the default preset name.")
        return False
    if not old_name or not new_name:
        log.warning("Attempted to rename preset with empty name.")
        return False
    if old_name == new_name:
        return True # Ничего не делаем

    old_path = _get_preset_filepath(old_name)
    new_path = _get_preset_filepath(new_name)

    if not os.path.exists(old_path):
        log.warning(f"Preset to rename not found: {old_path}")
        return False
    if os.path.exists(new_path):
        log.warning(f"Preset with the new name already exists: {new_path}. Aborting rename.")
        return False
        
    try:
        # Используем shutil.move для переименования (работает и между дисками)
        shutil.move(old_path, new_path)
        log.info(f"Renamed preset '{old_name}' to '{new_name}' ({old_path} -> {new_path})")
        return True
    except Exception as e:
        log.error(f"Error renaming preset from {old_path} to {new_path}: {e}")
        return False

def delete_all_custom_presets() -> Optional[int]:
    """Удаляет все пресеты, кроме дефолтного. Возвращает количество удаленных."""
    _ensure_presets_dir_exists()
    deleted_count = 0
    try:
        for filename in os.listdir(PRESETS_DIR):
            preset_name = os.path.splitext(filename)[0]
            if filename.lower().endswith('.json') and preset_name != DEFAULT_PRESET_NAME:
                preset_path = os.path.join(PRESETS_DIR, filename)
                try:
                    os.remove(preset_path)
                    log.info(f"Deleted custom preset: {preset_name}")
                    deleted_count += 1
                except OSError as e:
                    log.error(f"Failed to delete custom preset {preset_path}: {e}")
                    # Продолжаем удалять другие
        return deleted_count
    except FileNotFoundError:
        log.warning(f"Presets directory not found: {PRESETS_DIR}. Cannot delete custom presets.")
        return 0 # Нет папки - нет пресетов
    except Exception as e:
        log.error(f"Error deleting custom presets from {PRESETS_DIR}: {e}")
        return None # Возвращаем None при общей ошибке

# Проверим наличие секции performance в DEFAULT_SETTINGS, если ее нет, добавим
def ensure_performance_settings(settings):
    """
    Обеспечивает наличие секции performance в настройках.
    
    Args:
        settings: Словарь с настройками
        
    Returns:
        Обновленный словарь с настройками
    """
    if 'performance' not in settings:
        settings['performance'] = {
            'enable_multiprocessing': False,
            'max_workers': 0  # 0 означает автоматическое определение (по количеству ядер)
        }
    elif 'enable_multiprocessing' not in settings['performance']:
        settings['performance']['enable_multiprocessing'] = False
        settings['performance']['max_workers'] = 0
    
    return settings

# Пример использования (если файл запускается напрямую)
if __name__ == "__main__":
    # Настройка базового логирования для теста
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    log.info("Testing config_manager...")
    settings_file = "test_settings.json"

    # 1. Получить дефолтные
    defaults = get_default_settings()
    print("\n--- Default Settings ---")
    # print(json.dumps(defaults, indent=4))

    # 2. Попробовать загрузить (файла еще нет)
    print(f"\n--- Loading from non-existent file ({settings_file}) ---")
    loaded1 = load_settings(settings_file)
    # print(json.dumps(loaded1, indent=4))
    assert loaded1 == defaults # Должны вернуться дефолтные

    # 3. Изменить что-то и сохранить
    print(f"\n--- Modifying and saving to {settings_file} ---")
    loaded1["paths"]["input_folder_path"] = "/new/input/path"
    loaded1["whitening"]["enable_whitening"] = False
    loaded1["collage_mode"]["jpeg_quality"] = 85
    save_successful = save_settings(loaded1, settings_file)
    assert save_successful

    # 4. Загрузить из сохраненного файла
    print(f"\n--- Loading from existing file ({settings_file}) ---")
    loaded2 = load_settings(settings_file)
    # print(json.dumps(loaded2, indent=4))
    assert loaded2["paths"]["input_folder_path"] == "/new/input/path"
    assert loaded2["whitening"]["enable_whitening"] is False
    assert loaded2["collage_mode"]["jpeg_quality"] == 85
    assert "individual_mode" in loaded2 # Проверка, что остальные ключи тоже есть

    # 5. Проверка сброса (получение дефолтных)
    print("\n--- Getting defaults again (for reset simulation) ---")
    reset_settings = get_default_settings()
    assert reset_settings["whitening"]["enable_whitening"] is True # Убедимся, что вернулись к дефолту

    # 6. Тест поврежденного файла (если возможно создать вручную)
    # try:
    #     with open("corrupted_settings.json", "w") as f: f.write("{invalid json")
    #     print("\n--- Loading from corrupted file ---")
    #     loaded3 = load_settings("corrupted_settings.json")
    #     assert loaded3 == defaults
    # finally:
    #     if os.path.exists("corrupted_settings.json"): os.remove("corrupted_settings.json")

    # Очистка тестового файла
    if os.path.exists(settings_file):
        os.remove(settings_file)
        log.info(f"Cleaned up test file: {settings_file}")

    log.info("Testing preset functions...")
    create_default_preset()
    avail = get_available_presets()
    print(f"Available presets: {avail}")
    
    test_settings = {"paths": {"input_folder_path": "/test/input"}, "collage_mode": {"output_format": "png"}}
    save_ok = save_settings_preset(test_settings, "My Test Preset")
    print(f"Save 'My Test Preset' ok: {save_ok}")
    
    avail = get_available_presets()
    print(f"Available presets: {avail}")
    
    loaded = load_settings_preset("My Test Preset")
    if loaded:
        print("Loaded 'My Test Preset':", loaded.get("paths"), loaded.get("collage_mode"))
    else:
        print("Failed to load 'My Test Preset'")
        
    rename_ok = rename_settings_preset("My Test Preset", "My Renamed Preset")
    print(f"Rename ok: {rename_ok}")

    avail = get_available_presets()
    print(f"Available presets: {avail}")

    delete_ok = delete_settings_preset("My Renamed Preset")
    print(f"Delete ok: {delete_ok}")
    
    avail = get_available_presets()
    print(f"Available presets: {avail}")

    # Тест загрузки несуществующего
    loaded_nonexist = load_settings_preset("NonExistentPreset")
    print(f"Load non-existent preset: {loaded_nonexist}")

    # Тест загрузки дефолтного (даже если удален)
    delete_settings_preset(DEFAULT_PRESET_NAME) # Попробуем удалить (не должно сработать)
    load_default = load_settings_preset(DEFAULT_PRESET_NAME)
    print(f"Load default preset (exists: {os.path.exists(_get_preset_filepath(DEFAULT_PRESET_NAME))}):", load_default is not None)

    # Тест удаления всех кастомных
    save_settings_preset({}, "Custom1")
    save_settings_preset({}, "Custom2")
    print(f"Presets before delete all: {get_available_presets()}")
    deleted = delete_all_custom_presets()
    print(f"Deleted custom presets: {deleted}")
    print(f"Presets after delete all: {get_available_presets()}")

    log.info("config_manager tests finished.")