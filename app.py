# app.py

import logging
import config_manager # Убедимся, что импортирован
import functools # Добавляем для более удобного связывания колбэков

# --- БЛОК ИНИЦИАЛИЗАЦИИ МНОГОПРОЦЕССОРНОЙ ОБРАБОТКИ ---
print("="*50)
print("--- Инициализация многопроцессорной обработки ---")
try:
    import multiprocessing
    import sys
    import os
    
    # Получаем метод запуска из переменной окружения, если задана
    mp_method = os.environ.get('PYTHONMULTIPROCESSINGMETHOD', 'spawn' if sys.platform == 'win32' else None)
    
    # Инициализируем многопроцессорную обработку как можно раньше
    if sys.platform == 'win32' or mp_method == 'spawn':
        try:
            # На Windows необходимо использовать 'spawn' метод
            multiprocessing.set_start_method('spawn', force=True)
            print(f"Установлен метод запуска процессов 'spawn' для {'Windows' if sys.platform == 'win32' else 'всех платформ'}")
        except RuntimeError as e:
            # Если метод уже был установлен
            print(f"Метод запуска процессов уже установлен: {e}")
    
    # Выводим информацию о доступных процессорах
    cpu_count = multiprocessing.cpu_count()
    print(f"Доступно CPU ядер: {cpu_count}")
    print(f"Операционная система: {sys.platform}")
    
    # Выводим значения переменных окружения, связанных с многопроцессорной обработкой
    for env_var in ['PYTHONPATH', 'PYTHONMULTIPROCESSING', 'PYTHONEXECUTABLE', 'PYTHONMULTIPROCESSINGMETHOD', 'PYTHONNOWINDOW']:
        value = os.environ.get(env_var, 'не задана')
        print(f"Переменная окружения {env_var}: {value}")
    
    # Импортируем наш модуль multiprocessing_utils
    import multiprocessing_utils
    multiprocessing_utils.enable_multiprocessing()
    print("Мультипроцессинг успешно инициализирован")
except Exception as e:
    print(f"[!] Ошибка при инициализации многопроцессорной обработки: {e}")
    import traceback
    traceback.print_exc()
    print("Приложение продолжит работу в однопроцессорном режиме")
print("="*50)

# --- БЛОК ПРОВЕРКИ И УСТАНОВКИ ЗАВИСИМОСТЕЙ ---
import sys
import subprocess
import importlib
import os
import time
import platform
print("="*50); print("--- Проверка и установка необходимых библиотек ---")

# Полный список обязательных библиотек с маппингом на имена модулей

# --- Инициализация installed_packages_info ---
installed_packages_info = []
for package_name in ["streamlit", "Pillow", "natsort", "psd-tools"]:
    module_map = { "streamlit": "streamlit", "Pillow": "PIL", "natsort": "natsort", "psd-tools": "psd_tools"}
    module_name = module_map[package_name]
    try: importlib.import_module(module_name); print(f"[OK] {package_name} found."); installed_packages_info.append(f"{package_name} (OK)")
    except ImportError: print(f"[!] {package_name} not found. Installing..."); # ... (код установки) ...; installed_packages_info.append(f"{package_name} (Installed/Error)")

print("="*50); print("--- Проверка зависимостей завершена ---"); print("Статус пакетов:", ", ".join(installed_packages_info)); print("="*50)
needs_restart = any("(Installed" in s for s in installed_packages_info) # Проверяем, была ли установка
if needs_restart: print("\n[ВАЖНО] Были установлены новые библиотеки...")
# === КОНЕЦ БЛОКА ПРОВЕРКИ ===

# === Импорт основных библиотек ===
print("Загрузка основных модулей приложения...")
try:
    import streamlit as st
    from PIL import Image
    from io import StringIO
    import logging
    from typing import Dict, Any, Optional, Tuple, List

    import config_manager
    import processing_workflows
    print("Модули успешно загружены.")
except ImportError as e: print(f"\n[!!! КРИТИЧЕСКАЯ ОШИБКА] Import Error: {e}"); sys.exit(1)
except Exception as e: print(f"\n[!!! КРИТИЧЕСКАЯ ОШИБКА] App Import Error: {e}"); import traceback; traceback.print_exc(); sys.exit(1)

# === ДОБАВЛЕНО: Настройка страницы ===
st.set_page_config(layout="wide", page_title="Обработчик Изображений")
# ====================================

# --- Настройка логирования ---
LOG_FILENAME = "app.log" # Имя файла для логов
log_stream = StringIO() # Буфер для UI
log_level = logging.INFO # logging.DEBUG для более подробных логов
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# --- Настройка корневого логгера --- 
# Удаляем стандартные обработчики, если они есть (на всякий случай)
root_logger = logging.getLogger()
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
root_logger.setLevel(log_level)

# 1. Обработчик для вывода в UI (через StringIO)
stream_handler = logging.StreamHandler(log_stream)
stream_handler.setFormatter(log_formatter)
stream_handler.setLevel(log_level) # Уровень для UI
root_logger.addHandler(stream_handler)

# 2. Обработчик для записи в файл
try:
    file_handler = logging.FileHandler(LOG_FILENAME, mode='a', encoding='utf-8') 
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.DEBUG) # В файл пишем ВСЕ сообщения от DEBUG и выше
    root_logger.addHandler(file_handler)
    print(f"Logging to file: {os.path.abspath(LOG_FILENAME)} (Level: DEBUG, Mode: Append)")
except Exception as e_fh:
    print(f"[!!! ОШИБКА] Не удалось настроить логирование в файл {LOG_FILENAME}: {e_fh}")

# Получаем логгер для текущего модуля
log = logging.getLogger(__name__)
log.info("--- App script started, logger configured (Stream + File) ---")
log.info(f"UI Log Level: {logging.getLevelName(log_level)}")
log.info(f"File Log Level: DEBUG")

def cleanup_unused_templates():
    """
    Проверяет папку 'templates' и удаляет файлы, на которые не ссылается
    ни один пресет настроек в папке 'settings_presets'.
    """
    log.info("--- Starting cleanup of unused templates --- ")
    presets_dir = config_manager.PRESETS_DIR
    templates_dir = "templates"
    
    if not os.path.isdir(presets_dir):
        log.warning(f"Presets directory '{presets_dir}' not found. Cannot check for unused templates.")
        return
        
    if not os.path.isdir(templates_dir):
        log.info(f"Templates directory '{templates_dir}' not found. Nothing to clean.")
        return
        
    # 1. Собрать все пути к шаблонам, используемые в пресетах
    valid_template_paths = set()
    try:
        preset_names = config_manager.get_available_presets()
        log.info(f"Checking {len(preset_names)} presets for template paths...")
        for name in preset_names:
            settings = config_manager.load_settings_preset(name)
            if settings:
                template_path_relative = settings.get('merge_settings', {}).get('template_path', '')
                # Проверяем, что путь не пустой и указывает на папку templates
                if template_path_relative and template_path_relative.startswith(templates_dir + os.path.sep):
                    # Нормализуем путь для корректного сравнения
                    try:
                        abs_template_path = os.path.abspath(template_path_relative)
                        normalized_path = os.path.normcase(os.path.normpath(abs_template_path))
                        valid_template_paths.add(normalized_path)
                        # log.debug(f"Preset '{name}' uses template: {normalized_path}")
                    except Exception as e_path:
                        log.error(f"Error processing template path '{template_path_relative}' from preset '{name}': {e_path}")
    except Exception as e:
        log.error(f"Error reading presets during template cleanup: {e}")
        return # Прерываем, если не можем надежно прочитать пресеты

    log.info(f"Found {len(valid_template_paths)} unique template paths used in presets.")
    # log.debug(f"Valid template paths: {valid_template_paths}")

    # 2. Получить все файлы в папке templates
    unused_deleted_count = 0
    try:
        files_in_templates_dir = [f for f in os.listdir(templates_dir) if os.path.isfile(os.path.join(templates_dir, f))]
        log.info(f"Found {len(files_in_templates_dir)} files in '{templates_dir}' directory. Checking usage...")

        # 3. Сравнить и удалить неиспользуемые
        for filename in files_in_templates_dir:
            file_abs_path = os.path.abspath(os.path.join(templates_dir, filename))
            normalized_file_path = os.path.normcase(os.path.normpath(file_abs_path))
            
            # log.debug(f"Checking file: {normalized_file_path}")
            if normalized_file_path not in valid_template_paths:
                log.warning(f"Template file '{filename}' seems unused by any preset. Attempting deletion...")
                try:
                    os.remove(file_abs_path)
                    log.info(f"Successfully deleted unused template: {filename}")
                    unused_deleted_count += 1
                except OSError as e_del:
                    log.error(f"Failed to delete unused template '{filename}': {e_del}")
            # else: 
                # log.debug(f"Template file '{filename}' is used.")

    except Exception as e:
        log.error(f"Error accessing or processing templates directory '{templates_dir}': {e}")

    log.info(f"--- Template cleanup finished. Deleted {unused_deleted_count} unused files. ---")

# === Конец определения функции ===




# === Основной код приложения Streamlit ===

# --- Загрузка/Инициализация Настроек ---
CONFIG_FILE = "settings.json" # Основной файл настроек (текущее состояние)


# --- Функция для получения папки загрузки пользователя (Перенесена сюда) ---
# ВАЖНО: Убедимся, что platform и os импортированы ранее
def get_downloads_folder():
    """Возвращает путь к папке Загрузки пользователя для разных ОС."""
    if platform.system() == "Windows":
        try:
            import winreg
            subkey = r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'
            downloads_guid = '{374DE290-123F-4565-9164-39C4925E467B}'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                location = winreg.QueryValueEx(key, downloads_guid)[0]
                log.debug(f"Downloads folder from registry: {location}")
                return location
        except ImportError:
            log.warning("winreg module not found, cannot get Downloads path from registry.")
        except FileNotFoundError: # Ключ или значение могут отсутствовать
             log.warning(f"Registry key/value for Downloads not found.")
        except Exception as e_reg:
            log.error(f"Error reading Downloads path from registry: {e_reg}")
        user_profile = os.environ.get('USERPROFILE')
        if user_profile:
            default_path = os.path.join(user_profile, 'Downloads')
            log.debug(f"Falling back to default Downloads path (Windows): {default_path}")
            return default_path
        else:
            log.error("Could not determine USERPROFILE for Downloads path.")
            return ""
    elif platform.system() == "Darwin": # macOS
        default_path = os.path.join(os.path.expanduser('~'), 'Downloads')
        log.debug(f"Default Downloads path (macOS): {default_path}")
        return default_path
    else: # Linux и другие
        default_path = os.path.join(os.path.expanduser('~'), 'Downloads')
        log.debug(f"Default Downloads path (Linux/Other): {default_path}")
        return default_path

config_manager.create_default_preset() # Проверка дефолтного пресета нужна

# Инициализация session_state (Единственный блок)
if 'initialized' not in st.session_state:
    log.info("--- Initializing Streamlit Session State ---")
    st.session_state.initialized = True
    st.session_state.settings_changed = False
    st.session_state.is_processing = False  # Добавляем флаг состояния обработки
    st.session_state.saved_logs = ""  # Добавляем переменную для сохранения логов
    
    # --- Очистка неиспользуемых шаблонов ПРИ ПЕРВОМ ЗАПУСКЕ сессии ---
    try:
        cleanup_unused_templates() # Вызов функции
    except Exception as e_cleanup:
        log.error(f"Error during template cleanup: {e_cleanup}")
    # ---------------------------------------------------------------------
    
    # Загружаем настройки из settings.json ОДИН РАЗ для определения активного пресета
    initial_main_settings = config_manager.load_settings(CONFIG_FILE)
    active_preset_name = initial_main_settings.get("active_preset", config_manager.DEFAULT_PRESET_NAME)
    
    loaded_preset_settings = config_manager.load_settings_preset(active_preset_name)
    if loaded_preset_settings:
        st.session_state.current_settings = loaded_preset_settings
        st.session_state.active_preset = active_preset_name
        log.info(f"Initialized session state with preset: '{active_preset_name}'")
    else:
        log.warning(f"Could not load preset '{active_preset_name}'. Falling back to default preset.")
        # Пытаемся загрузить дефолтный пресет
        default_settings = config_manager.load_settings_preset(config_manager.DEFAULT_PRESET_NAME)
        if default_settings:
             st.session_state.current_settings = default_settings
             st.session_state.active_preset = config_manager.DEFAULT_PRESET_NAME
             log.info("Initialized session state with default preset as fallback.")
        else:
             # Крайний случай: не удалось загрузить ни активный, ни дефолтный пресет
             log.error("CRITICAL: Could not load default preset either! Using hardcoded defaults.")
             st.session_state.current_settings = config_manager.get_default_settings() # Используем из кода
             st.session_state.active_preset = config_manager.DEFAULT_PRESET_NAME
             
    st.session_state.selected_processing_mode = st.session_state.current_settings.get('processing_mode_selector', "Обработка отдельных файлов")
    st.session_state.reset_profiles_confirmation_pending = False
    st.session_state.reset_settings_confirmation_pending = False
    # === НОВЫЙ ФЛАГ для подтверждения сброса пресета ===
    st.session_state.reset_active_preset_confirmation_pending = False
    # =================================================
    log.info("--- Session State Initialized ---")

# --- Вспомогательные функции для доступа к настройкам (Единственный блок) ---
# Убедимся, что они определены до первого использования
def get_setting(setting_path: str, default: Any = None) -> Any:
    """Получение значения настройки из текущего профиля"""
    try:
        # Разбиваем путь на части
        parts = setting_path.split('.')
        current = st.session_state.current_settings
        
        # Проходим по всем частям пути
        for part in parts:
            if part not in current:
                return default
            current = current[part]
            
        # Специальная обработка для width_ratio
        if setting_path == 'merge_settings.width_ratio':
            if not isinstance(current, (list, tuple)) or len(current) != 2:
                return [1.0, 1.0]
            # Проверяем минимальные значения
            return [max(0.1, float(current[0])), max(0.1, float(current[1]))]
            
        return current
    except Exception as e:
        log.error(f"Error getting setting {setting_path}: {str(e)}")
        return default

# === КОЛБЭК ДЛЯ ИЗМЕНЕНИЯ НАСТРОЕК ===
def setting_changed_callback(key_path: str):
    """Колбэк, вызываемый при изменении значения виджета настройки."""
    if key_path not in st.session_state:
        log.warning(f"Callback triggered for key '{key_path}' which is not in session_state. Skipping.")
        return
    new_value = st.session_state[key_path]
    set_setting(key_path, new_value)
# ====================================

def set_setting(key_path: str, value: Any):
    keys = key_path.split('.')
    d = st.session_state.current_settings
    current_value = get_setting(key_path) # Получаем текущее значение для сравнения
    is_different = str(current_value) != str(value) # Простое сравнение строк для базовых типов и списков/словарей
                                                   # Может быть не идеально для сложных объектов, но должно работать для JSON-сериализуемых
    if not is_different:
        # log.debug(f"Setting '{key_path}' value is the same ({value}). Skipping update.")
        return # Не меняем и не ставим флаг, если значение то же самое

    try:
        for key in keys[:-1]:
            if key not in d or not isinstance(d[key], dict): d[key] = {}
            d = d[key]
        new_value = value.copy() if isinstance(value, (list, dict)) else value
        d[keys[-1]] = new_value
        st.session_state.settings_changed = True
        log.debug(f"Set setting '{key_path}' to: {new_value}")
    except TypeError as e:
        log.error(f"Error setting '{key_path}': {e}")

# === НОВАЯ ФУНКЦИЯ для сравнения с пресетом ===
def check_settings_differ_from_preset(preset_name: str) -> bool:
    """Сравнивает текущие настройки в session_state с сохраненным пресетом."""
    if not preset_name:
        return True # Если имя пресета пустое, считаем, что отличия есть
        
    log.debug(f"Comparing current settings with saved preset '{preset_name}'...")
    saved_preset_settings = config_manager.load_settings_preset(preset_name)
    
    if not saved_preset_settings:
        log.warning(f"Could not load preset '{preset_name}' for comparison. Assuming settings differ.")
        return True # Если не удалось загрузить пресет, считаем, что отличия есть
        
    # Сравниваем текущие настройки из session_state с загруженными
    # Простое сравнение словарей должно работать
    are_different = saved_preset_settings != st.session_state.current_settings
    log.debug(f"Comparison result: differ = {are_different}")
    return are_different
# ==============================================

# === Вспомогательная функция для автосохранения ===
def autosave_active_preset_if_changed():
    active_preset = st.session_state.active_preset
    # === УБРАНО УСЛОВИЕ ЗАПРЕТА ДЛЯ DEFAULT_PRESET_NAME ===
    # if active_preset != config_manager.DEFAULT_PRESET_NAME and st.session_state.settings_changed:
    if st.session_state.settings_changed: # Теперь сохраняем ЛЮБОЙ активный пресет, если есть изменения
        log.info(f"Autosaving changes for preset '{active_preset}'...")
        settings_to_save = st.session_state.current_settings.copy()
        settings_to_save['processing_mode_selector'] = st.session_state.selected_processing_mode
        if config_manager.save_settings_preset(settings_to_save, active_preset):
            log.info(f"Autosave successful for '{active_preset}'.")
            st.session_state.settings_changed = False # Reset flag after successful save
            return True
        else:
            log.error(f"Autosave failed for preset '{active_preset}'.")
            st.toast(f"❌ Ошибка автосохранения набора '{active_preset}'!", icon="⚠️")
            return False
    # === УБРАН БЛОК ELIF ДЛЯ DEFAULT_PRESET_NAME ===
    # elif active_preset == config_manager.DEFAULT_PRESET_NAME:
    #    log.debug(f"Autosave skipped: Cannot autosave changes to default preset.")
    #    return True 
    else: # Not changed
        log.debug(f"Autosave skipped: No changes detected for preset '{active_preset}'.")
        return True # Consider it successful
# ====================================================

# === Функция для сохранения и выхода ===
# --- REMOVED FUNCTION save_state_and_exit() ---
# def save_state_and_exit():
#     ...
# ====================================

# === UI: Боковая Панель ===
with st.sidebar:
    # --- Кнопка Выхода (перемещена сюда) ---
    # --- REMOVED BUTTON ---
    # if st.button("🚪 Выход", key="exit_button_sidebar", help="Сохранить текущее состояние и подготовиться к выходу"):
    #     save_state_and_exit()
    # st.divider() # Добавим разделитель после кнопки
    # --------------------------------------
    
    # === Режим обработки ===
    st.header("💠 Режим обработки")
    selected_mode = st.selectbox("Выберите режим обработки:", 
                                ["Обработка отдельных файлов", "Создание коллажей"],
                                index=["Обработка отдельных файлов", "Создание коллажей"].index(st.session_state.selected_processing_mode),
                                key='processing_mode_selector',
                                help="Выберите режим обработки: обработка отдельных изображений или создание коллажа из нескольких изображений.")
    if selected_mode != st.session_state.selected_processing_mode:
        st.session_state.selected_processing_mode = selected_mode
        if autosave_active_preset_if_changed():
            st.toast(f"Режим изменен на '{selected_mode}'.", icon="🔄")
            st.rerun()
        else:
            log.warning(f"Mode change from '{st.session_state.selected_processing_mode}' to '{selected_mode}' possible after save.")

    # Функция для генерации уникальных имен пресетов
    def get_default_preset_name_ui():
        """Возвращает уникальное имя для нового пресета."""
        existing_presets = config_manager.get_available_presets()
        counter = 1
        while f"Набор {counter}" in existing_presets:
            counter += 1
        return f"Набор {counter}"

    # === Наборы настроек (объединенный блок) ===
    st.header("🗄️ Наборы настроек")
    with st.expander("Управление наборами настроек", expanded=False):
        # --- Выбор и управление наборами ---
        st.caption("⚡️ Выбор и управление наборами")
        all_presets = config_manager.get_available_presets()
        if not all_presets or len(all_presets) == 0:
            st.warning(f"Не найдено ни одного набора настроек, включая '{config_manager.DEFAULT_PRESET_NAME}'")
            if config_manager.create_default_preset():
                st.success(f"Создан стандартный набор '{config_manager.DEFAULT_PRESET_NAME}'")
                st.rerun()
            else:
                st.error("Не удалось создать стандартный набор! Проверьте права доступа.")
        else:
            preset_select_col, preset_delete_col = st.columns([4, 1])
            with preset_select_col:
                selected_preset_in_box = st.selectbox("Активный набор настроек:", all_presets, 
                                                    index=all_presets.index(st.session_state.active_preset) if st.session_state.active_preset in all_presets else 0, 
                                                    key="preset_selector", 
                                                    label_visibility="collapsed")
            with preset_delete_col:
                delete_disabled = selected_preset_in_box == config_manager.DEFAULT_PRESET_NAME
                if st.button("🗑️", key="delete_preset_button", disabled=delete_disabled, help="Удалить набор" if not delete_disabled else "Нельзя удалить стандартный набор"):
                    if config_manager.delete_settings_preset(selected_preset_in_box):
                        st.toast(f"Набор '{selected_preset_in_box}' удален", icon="🗑️")
                        if selected_preset_in_box == st.session_state.active_preset:
                            default_settings = config_manager.load_settings_preset(config_manager.DEFAULT_PRESET_NAME)
                            if default_settings:
                                st.session_state.current_settings = default_settings
                                st.session_state.active_preset = config_manager.DEFAULT_PRESET_NAME
                                st.toast(f"Возврат к набору по умолчанию '{config_manager.DEFAULT_PRESET_NAME}'", icon="↩️")
                            else:
                                st.error("Не удалось загрузить набор по умолчанию!")
                        st.rerun()
                    else: st.error("Ошибка удаления набора")
            st.caption(f"Активный: **{st.session_state.active_preset}**")

            if selected_preset_in_box != st.session_state.active_preset:
                if autosave_active_preset_if_changed(): 
                    log.info(f"Preset changed in selectbox from '{st.session_state.active_preset}' to '{selected_preset_in_box}'")
                    preset_settings = config_manager.load_settings_preset(selected_preset_in_box)
                    if preset_settings:
                        st.session_state.current_settings = preset_settings
                        st.session_state.active_preset = selected_preset_in_box
                        st.session_state.selected_processing_mode = st.session_state.current_settings.get('processing_mode_selector', "Обработка отдельных файлов")
                        st.session_state.settings_changed = False
                        st.toast(f"Загружен набор '{selected_preset_in_box}'", icon="🔄")
                        st.rerun()
                    else: st.error(f"Ошибка загрузки набора '{selected_preset_in_box}'")
                else:
                    log.warning(f"Preset switch aborted due to autosave failure.")

        # --- Переименование и создание наборов ---
        st.caption("⚡️ Переименование и создание наборов")
        rename_col1, rename_col2 = st.columns([4, 1])
        with rename_col1:
            rename_disabled = st.session_state.active_preset == config_manager.DEFAULT_PRESET_NAME
            new_name_input = st.text_input(
                "Новое имя для активного набора:", value=st.session_state.active_preset, key="rename_preset_input",
                disabled=rename_disabled, label_visibility="collapsed"
            )
        with rename_col2:
            if st.button("✏️", key="rename_preset_button", disabled=rename_disabled, help="Переименовать активный набор" if not rename_disabled else "Нельзя переименовать набор по умолчанию"):
                if autosave_active_preset_if_changed():
                    old_active_name = st.session_state.active_preset
                    if config_manager.rename_settings_preset(old_active_name, new_name_input):
                        st.session_state.active_preset = new_name_input
                        st.toast(f"Набор '{old_active_name}' переименован в '{new_name_input}'", icon="✏️")
                        st.rerun()
                    else: st.error(f"Ошибка переименования (возможно, имя '{new_name_input}' занято?)")
                else:
                    log.warning("Rename aborted due to autosave failure.")

        create_col1, create_col2 = st.columns([4, 1])
        with create_col1:
            default_new_name = get_default_preset_name_ui()
            new_preset_name_input_val = st.text_input(
                "Название нового набора:", key="new_preset_name_input", placeholder=default_new_name, label_visibility="collapsed"
            )
        with create_col2:
            if st.button("➕", key="create_preset_button", help="Создать новый набор со значениями по умолчанию"):
                if autosave_active_preset_if_changed():
                    preset_name_to_save = new_preset_name_input_val if new_preset_name_input_val else default_new_name
                    default_settings_for_new_preset = config_manager.get_default_settings()
                    if config_manager.save_settings_preset(default_settings_for_new_preset, preset_name_to_save):
                        st.session_state.active_preset = preset_name_to_save
                        st.session_state.current_settings = default_settings_for_new_preset.copy()
                        st.session_state.selected_processing_mode = st.session_state.current_settings.get('processing_mode_selector', "Обработка отдельных файлов")
                        st.session_state.settings_changed = False
                        st.toast(f"Создан и активирован новый набор '{preset_name_to_save}' (со значениями по умолчанию)", icon="✨")
                        st.rerun()
                    else: st.error(f"Ошибка создания набора '{preset_name_to_save}'")
                else:
                    log.warning("Create new preset aborted due to autosave failure.")

        # --- Сохранение и сброс настроек ---
        st.caption("⚡️ Сохранение и сброс настроек")
        settings_save_col, settings_reset_col_moved = st.columns(2)
        with settings_save_col:
            save_help_text = f"Сохранить текущие настройки UI в активный набор '{st.session_state.active_preset}'"
            if st.button("💾 Сохранить в набор", key="save_active_preset_button", help=save_help_text, use_container_width=True):
                active_preset_name_to_save = st.session_state.active_preset
                settings_to_save_in_preset = st.session_state.current_settings.copy()
                settings_to_save_in_preset['processing_mode_selector'] = st.session_state.selected_processing_mode
                save_preset_ok = config_manager.save_settings_preset(settings_to_save_in_preset, active_preset_name_to_save)
                if save_preset_ok:
                    log.info(f"Preset '{active_preset_name_to_save}' manually saved.")
                    st.toast(f"✅ Настройки сохранены в набор '{active_preset_name_to_save}'.", icon="💾")
                    st.session_state.settings_changed = False
                else:
                    log.error(f"Failed to manually save preset '{active_preset_name_to_save}'.")
                    st.toast(f"❌ Ошибка сохранения набора '{active_preset_name_to_save}'!", icon="⚠️")

        with settings_reset_col_moved:
            settings_differ = check_settings_differ_from_preset(st.session_state.active_preset)
            if st.button("🔄 Отменить изменения", key="confirm_reset_active_preset_button", help=f"Сбросить текущие настройки к значениям по умолчанию.", use_container_width=True):
                st.session_state.reset_active_preset_confirmation_pending = True
                st.session_state.reset_settings_confirmation_pending = False
                st.rerun()

        if st.button("💥 Сбросить все к заводским", key="reset_all_settings_button", disabled=st.session_state.reset_settings_confirmation_pending, help="Полностью сбросить все настройки к первоначальному состоянию программы.", use_container_width=True):
            st.session_state.reset_settings_confirmation_pending = True
            st.session_state.reset_active_preset_confirmation_pending = False
            st.rerun()

        # --- Диалоги подтверждения сброса ---
        if st.session_state.reset_active_preset_confirmation_pending:
            st.warning("⚠️ Вы уверены, что хотите сбросить текущий набор к значениям по умолчанию?")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("✅ Да, сбросить", key="confirm_reset_active_preset_yes", use_container_width=True):
                    # Загружаем дефолтные настройки
                    default_settings = config_manager.get_default_settings()
                    # Применяем их к текущему профилю
                    st.session_state.current_settings = default_settings
                    st.session_state.selected_processing_mode = default_settings.get('processing_mode_selector', "Обработка отдельных файлов")
                    st.session_state.settings_changed = True
                    st.session_state.reset_active_preset_confirmation_pending = False
                    st.toast("✅ Текущий набор сброшен к значениям по умолчанию", icon="🔄")
                    st.rerun()
            with col2:
                if st.button("❌ Нет, отмена", key="confirm_reset_active_preset_no", use_container_width=True):
                    st.session_state.reset_active_preset_confirmation_pending = False
                    st.rerun()

        if st.session_state.reset_settings_confirmation_pending:
            st.error("⚠️ ВНИМАНИЕ: Это действие удалит ВСЕ наборы настроек, включая первый! Первый набор будет пересоздан в режиме по умолчанию!")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("✅ Да, сбросить всё", key="confirm_reset_all_yes", use_container_width=True):
                    # Удаляем все кастомные пресеты
                    deleted_count = config_manager.delete_all_custom_presets()
                    
                    # Удаляем также дефолтный пресет, если он существует
                    default_preset_path = config_manager._get_preset_filepath(config_manager.DEFAULT_PRESET_NAME)
                    if os.path.exists(default_preset_path):
                        try:
                            os.remove(default_preset_path)
                            log.info(f"Deleted default preset file as part of factory reset")
                            deleted_count += 1
                        except Exception as e:
                            log.error(f"Error deleting default preset during factory reset: {e}")
                    
                    # Создаем новый дефолтный пресет с заводскими настройками
                    config_manager.create_default_preset()
                    
                    # Сбрасываем все настройки UI к дефолтным
                    default_settings = config_manager.get_default_settings()
                    
                    # Получаем папку загрузок пользователя
                    user_downloads = get_downloads_folder()
                    
                    # Устанавливаем пути по умолчанию, чтобы они были доступны сразу после сброса
                    default_settings['paths']['input_folder_path'] = user_downloads
                    default_settings['paths']['output_folder_path'] = os.path.join(user_downloads, "Processed")
                    default_settings['paths']['backup_folder_path'] = os.path.join(user_downloads, "Backups")
                    
                    # Сохраняем обновленные настройки с путями в пресет
                    config_manager.save_settings_preset(default_settings, config_manager.DEFAULT_PRESET_NAME)
                    
                    st.session_state.current_settings = default_settings
                    st.session_state.selected_processing_mode = default_settings.get('processing_mode_selector', "Обработка отдельных файлов")
                    st.session_state.active_preset = config_manager.DEFAULT_PRESET_NAME
                    st.session_state.settings_changed = False  # Изменили на False, так как настройки соответствуют сохраненному пресету
                    st.session_state.reset_settings_confirmation_pending = False
                    st.toast(f"✅ Все настройки сброшены к заводским значениям (удалено {deleted_count} профилей)", icon="💥")
                    st.rerun()
            with col2:
                if st.button("❌ Нет, отмена", key="confirm_reset_all_no", use_container_width=True):
                    st.session_state.reset_settings_confirmation_pending = False
                    st.rerun()

    # === Пути (объединенный блок) ===
    st.header("📂 Пути")
    user_downloads_folder = get_downloads_folder()
    log.debug(f"Resolved Downloads Folder: {user_downloads_folder}")

    with st.expander("Настройка путей и сохранения", expanded=True):
        current_mode_for_file_ops = st.session_state.selected_processing_mode
        if current_mode_for_file_ops == "Обработка отдельных файлов":
            # --- Переименование и удаление --- 
            st.caption("⚡️ Переименование и удаление")
            enable_rename_ind = st.checkbox("Переименовать файлы (по артикулу)",
                                            value=get_setting('individual_mode.enable_rename', False),
                                            key='individual_mode.enable_rename',
                                            help="Автоматически переименовывает обработанные файлы, используя указанный артикул. Первый файл будет назван как артикул, остальные - артикул_1, артикул_2 и т.д.",
                                            on_change=setting_changed_callback, args=('individual_mode.enable_rename',))
            # Убрали: set_setting('individual_mode.enable_rename', enable_rename_ind)
            if st.session_state.get('individual_mode.enable_rename', False):
                article_ind = st.text_input("Артикул для переименования",
                                            value=get_setting('individual_mode.article_name', ''),
                                            key='individual_mode.article_name',
                                            placeholder="Введите артикул...",
                                            help="Введите артикул или базовое имя для файлов. Это имя будет использоваться как основа для всех обработанных файлов.",
                                            on_change=setting_changed_callback, args=('individual_mode.article_name',))
                # Убрали: set_setting('individual_mode.article_name', article_ind)
                
                # Добавляем кнопку нормализации артикулей
                if st.button("🔄 Нормализовать артикули", help="Проанализировать имена файлов в папке и определить нормализованные артикулы"):
                    # Проверяем, указана ли папка с файлами
                    input_path = st.session_state.get('paths.input_folder_path', '')
                    if not input_path or not os.path.exists(input_path):
                        st.warning("Укажите существующую папку с изображениями!")
                    else:
                        with st.spinner("Анализ файлов..."):
                            try:
                                # Получаем маппинг артикулей
                                article_mapping = processing_workflows.normalize_articles_in_folder(input_path)
                                
                                if not article_mapping:
                                    st.warning("В указанной папке не найдено изображений для нормализации.")
                                else:
                                    # Находим основной артикул
                                    main_article = None
                                    for normalized in article_mapping.values():
                                        if '_' not in normalized:
                                            main_article = normalized
                                            break
                                            
                                    if not main_article and article_mapping:
                                        # Берем первый артикул и удаляем индекс, если он есть
                                        first_normalized = list(article_mapping.values())[0]
                                        main_article = first_normalized.split('_')[0]
                                    
                                    if main_article:
                                        # Применяем артикул к настройкам
                                        set_setting('individual_mode.article_name', main_article)
                                        st.success(f"Определен и применен артикул: {main_article}")
                                        
                                        # Удален неработающий элемент UI для переименования файлов
                                        
                            except Exception as e:
                                st.error(f"Ошибка при нормализации артикулей: {str(e)}")
                                log.exception("Error in normalize_articles")
                
                if st.session_state.get('individual_mode.article_name', ''): st.caption("Файлы будут вида: [Артикул]_1.jpg, ...")
                else: st.warning("Введите артикул для переименования.") # Валидация
            
            delete_orig_ind = st.checkbox("Удалять оригиналы после обработки?",
                                          value=get_setting('individual_mode.delete_originals', False),
                                          key='individual_mode.delete_originals',
                                          help="Если включено, исходные файлы будут удалены после успешной обработки. Это действие необратимо, поэтому используйте его с осторожностью. Рекомендуется включить бэкап перед использованием этой опции.",
                                          on_change=setting_changed_callback, args=('individual_mode.delete_originals',))
            # Убрали: set_setting('individual_mode.delete_originals', delete_orig_ind)
            if st.session_state.get('individual_mode.delete_originals', False): st.warning("ВНИМАНИЕ: Удаление необратимо!", icon="⚠️")

        # --- Input Path ---
        st.caption("Основной путь ввода")
        current_input_path = get_setting('paths.input_folder_path', '')
        input_path_default_value = current_input_path if current_input_path else user_downloads_folder
        input_path_val = st.text_input(
            "Папка с исходными файлами:",
            value=input_path_default_value,
            key='paths.input_folder_path',
            help="Укажите папку, в которой находятся исходные изображения для обработки. Поддерживаются форматы JPG, PNG, WEBP, TIFF, BMP, GIF. Папка должна существовать и быть доступной для чтения.",
            on_change=setting_changed_callback, args=('paths.input_folder_path',)
        )
        # Убрали: if input_path_val != current_input_path:
        #    set_setting('paths.input_folder_path', input_path_val)
        input_path_from_state = st.session_state.get('paths.input_folder_path', '')
        if input_path_from_state and os.path.isdir(input_path_from_state): 
            st.caption(f"✅ Папка найдена: {os.path.abspath(input_path_from_state)}")
        elif input_path_from_state: 
            st.caption(f"❌ Папка не найдена: {os.path.abspath(input_path_from_state)}")
        else: 
            st.caption("ℹ️ Путь не указан.")

        current_mode_local = st.session_state.selected_processing_mode
        if current_mode_local == "Обработка отдельных файлов":
            st.caption("Пути обработки файлов")
            # --- Output Path ---
            current_output_path = get_setting('paths.output_folder_path', '')
            output_path_default_value = current_output_path if current_output_path else os.path.join(user_downloads_folder, "Processed")
            output_path_val = st.text_input(
                "Папка для результатов:",
                value=output_path_default_value,
                key='paths.output_folder_path',
                help="Укажите папку, куда будут сохранены обработанные изображения. Папка будет создана автоматически, если она не существует. Убедитесь, что у вас есть права на запись в эту папку.",
                on_change=setting_changed_callback, args=('paths.output_folder_path',)
            )
            # Убрали: if output_path_val != current_output_path:
            #    set_setting('paths.output_folder_path', output_path_val)
            output_path_from_state = st.session_state.get('paths.output_folder_path', '')
            if output_path_from_state: st.caption(f"Сохранять в: {os.path.abspath(output_path_from_state)}")

            # --- Backup Path ---
            current_backup_path = get_setting('paths.backup_folder_path')
            backup_path_default_value = current_backup_path if current_backup_path else os.path.join(user_downloads_folder, "Backups")
            backup_path_val = st.text_input(
                "Папка для бэкапов:",
                value=backup_path_default_value,
                key='paths.backup_folder_path',
                placeholder="Оставьте пустым чтобы отключить",
                help="Укажите папку для резервного копирования оригинальных файлов перед обработкой. Оставьте пустым, чтобы отключить создание резервных копий. Рекомендуется использовать бэкап при включенной опции удаления оригиналов.",
                on_change=setting_changed_callback, args=('paths.backup_folder_path',)
            )
            # Убрали: if backup_path_val != current_backup_path:
            #    set_setting('paths.backup_folder_path', backup_path_val)
            backup_path_from_state = st.session_state.get('paths.backup_folder_path')
            if backup_path_from_state:
                 # Проверяем, был ли путь задан ИЛИ является ли он стандартным предложенным
                is_default_shown = not current_backup_path and backup_path_from_state == os.path.join(user_downloads_folder, "Backups")
                st.caption(f"Бэкап в: {os.path.abspath(backup_path_from_state)}" + (" (по умолчанию)" if is_default_shown else ""))
            else:
                st.caption(f"Бэкап отключен.")

        elif current_mode_local == "Создание коллажей":
            st.caption("Настройки коллажа")
            collage_filename_val = st.text_input(
                "Имя файла коллажа (без расш.):",
                value=get_setting('paths.output_filename', 'collage'),
                key='paths.output_filename',
                help="Введите базовое имя файла для коллажа (без расширения). Коллаж будет сохранен в папке с исходными файлами с указанным именем и расширением в соответствии с выбранным форматом (JPG или PNG).",
                on_change=setting_changed_callback, args=('paths.output_filename',)
            )
            # Убрали: set_setting('paths.output_filename', collage_filename_val)
            collage_filename_from_state = st.session_state.get('paths.output_filename', '')
            if collage_filename_from_state: st.caption(f"Имя файла: {collage_filename_from_state}.[расширение]")

    # --- Кнопка сброса путей в отдельном expander ---
    with st.expander("Сброс путей", expanded=False):
        if st.button("🔄 Сбросить пути по умолчанию", key="reset_paths_button",
                     help="Установить стандартные пути на основе папки Загрузки вашей системы. Это сбросит все пути к их значениям по умолчанию.",
                     use_container_width=True):
            # Получаем папку загрузок пользователя
            user_downloads = get_downloads_folder()
            
            # Устанавливаем пути на основе папки загрузок
            set_setting('paths.input_folder_path', user_downloads)
            set_setting('paths.output_folder_path', os.path.join(user_downloads, "Processed"))
            set_setting('paths.backup_folder_path', os.path.join(user_downloads, "Backups"))
            
            st.toast("Пути установлены на основе папки загрузок", icon="🔄")
            st.rerun()

    # === Остальные Настройки ===
    st.header("⚙️ Настройки обработки")
    st.caption(f"Настройки для режима: **{st.session_state.selected_processing_mode}**")
    # Проверяем, включено ли изменение размера
    should_expand_resize = get_setting('preprocessing.enable_preresize', False)
    
    with st.expander("1. Предварительное уменьшение размера (до обработки)", expanded=should_expand_resize):
        enable_preresize = st.checkbox("Включить предварительное уменьшение размера", 
                                     value=get_setting('preprocessing.enable_preresize', False),
                                     key='preprocessing.enable_preresize',
                                     help="Включить предварительное уменьшение размера изображений перед дальнейшей обработкой",
                                     on_change=setting_changed_callback, args=('preprocessing.enable_preresize',))
        # Убрали: set_setting('preprocessing.enable_preresize', enable_preresize)
        if st.session_state.get('preprocessing.enable_preresize', False):
            st.warning("⚠️ Уменьшение размера изображения может привести к снижению качества и потере мелких деталей. Используйте только если изображения очень большие или для ускорения обработки.", icon="⚠️")
            col_pre1, col_pre2 = st.columns(2)
            with col_pre1:
                 pr_w = st.number_input("Макс. Ширина (px)", 0, 10000, 
                                       value=get_setting('preprocessing.preresize_width', 2500), 
                                       step=10, key='preprocessing.preresize_width',
                                       help="Максимальная ширина изображения после ресайза. 0 означает отсутствие ограничения по ширине. Изображение будет пропорционально уменьшено, если его ширина превышает это значение.",
                                       on_change=setting_changed_callback, args=('preprocessing.preresize_width',))
                 # Убрали: set_setting('preprocessing.preresize_width', pr_w)
            with col_pre2:
                 pr_h = st.number_input("Макс. Высота (px)", 0, 10000, 
                                       value=get_setting('preprocessing.preresize_height', 2500), 
                                       step=10, key='preprocessing.preresize_height',
                                       help="Максимальная высота изображения после ресайза. 0 означает отсутствие ограничения по высоте. Изображение будет пропорционально уменьшено, если его высота превышает это значение.",
                                       on_change=setting_changed_callback, args=('preprocessing.preresize_height',))
                 # Убрали: set_setting('preprocessing.preresize_height', pr_h)

    # Проверяем, включено ли отбеливание
    should_expand_whitening = get_setting('whitening.enable_whitening', False)
    
    with st.expander("2. Отбеливание", expanded=should_expand_whitening):
        enable_whitening = st.checkbox("Включить отбеливание", 
                                     value=get_setting('whitening.enable_whitening', False),
                                     key='whitening.enable_whitening',
                                     help="Включить отбеливание изображений",
                                     on_change=setting_changed_callback, args=('whitening.enable_whitening',))
        # Убрали: set_setting('whitening.enable_whitening', enable_whitening)
        if st.session_state.get('whitening.enable_whitening', False):
            # === СЛАЙДЕР С ПРЕОБРАЗОВАНИЕМ ===
            # Для слайдеров, где нужно преобразование значения, используем временный ключ для UI и специальный колбэк
            current_adjusted_threshold = get_setting('whitening.whitening_cancel_threshold', 765) # 765 -> 0%
            default_slider_percent = max(0, min(100, round((765 - current_adjusted_threshold) / 7.65)))
            
            def whitening_slider_callback():
                """Специальный колбэк для слайдера отбеливания с преобразованием значения."""
                slider_percent = st.session_state.get('whitening_slider_percent_ui', 30)
                adjusted_threshold = int((100 - slider_percent) * 7.65) # Инвертируем
                set_setting('whitening.whitening_cancel_threshold', adjusted_threshold)
            
            wc_percent = st.slider("Максимальная темнота периметра", 0, 100, 
                                  value=default_slider_percent,
                                  step=1, 
                                  key='whitening_slider_percent_ui', # Временный ключ для UI
                                  format="%d%%",
                                  help="Определяет, насколько темным может быть периметр изображения для его отбеливания. 0% - отбеливать только чисто белый периметр, 100% - отбеливать любой периметр. Рекомендуемое значение - 30%.",
                                  on_change=whitening_slider_callback)
            # Убрали: прямое вычисление и set_setting здесь
            # adjusted_threshold = int((100 - wc_percent) * 7.65)
            # set_setting('whitening.whitening_cancel_threshold', adjusted_threshold)

    # Проверяем, включено ли удаление фона
    should_expand_bg_crop = get_setting('background_crop.enable_bg_crop', False)
    
    with st.expander("3. Удаление фона", expanded=should_expand_bg_crop):
        enable_bg_crop = st.checkbox("Включить удаление фона", 
                                   value=get_setting('background_crop.enable_bg_crop', False),
                                   key='enable_bg_crop',
                                   help="Включить удаление белого фона с изображений. Превращает непрозрачный фон в прозрачный, оставляя только основной объект. Особенно полезно для создания коллажей или размещения объектов на разных фонах.")
        set_setting('background_crop.enable_bg_crop', enable_bg_crop)
        if enable_bg_crop:
            bgc_tol = st.slider("Допуск белого фона", 0, 255, 
                              value=get_setting('background_crop.white_tolerance', 10), 
                              key='bgc_tol', 
                              help="Определяет, насколько цвет пикселя может отличаться от чисто белого (RGB 255,255,255), чтобы считаться фоном. Меньшие значения более строгие (только близкие к белому пиксели будут удалены), большие - более гибкие (захватит больше светлых оттенков). Рекомендуется не выше 20 для точных результатов.")
            set_setting('background_crop.white_tolerance', bgc_tol)
            
            # Добавляем выбор режима удаления фона
            bg_removal_modes = {
                "full": "Полное удаление фона",
                "edges": "Удаление фона по краям"
            }
            
            current_mode = get_setting('background_crop.removal_mode', 'full')
            bg_removal_mode = st.radio(
                "Режим удаления фона",
                options=list(bg_removal_modes.keys()),
                format_func=lambda x: bg_removal_modes[x],
                index=0 if current_mode == 'full' else 1,
                key='bg_removal_mode',
                help="Полное удаление фона - удаляет все белые пиксели. Удаление фона по краям - удаляет только белые пиксели от краев изображения, сохраняя белые элементы внутри темных контуров."
            )
            set_setting('background_crop.removal_mode', bg_removal_mode)
            
            # Добавляем опцию использования маски вместо прозрачности
            use_mask = st.checkbox("Не использовать прозрачность (решает проблему ореолов)", 
                                 value=get_setting('background_crop.use_mask_instead_of_transparency', False),
                                 key='use_mask_instead_of_transparency',
                                 help="Если включено, изображения будут обрабатываться без прозрачности и маска прозрачности будет применена только при сохранении PNG. Это решает проблему полупрозрачных краев (ореолов) при масштабировании PNG изображений.")
            set_setting('background_crop.use_mask_instead_of_transparency', use_mask)
            
            # Ползунок для устранения ореолов - доступен всегда
            halo_level = st.slider(
                "Уровень устранения ореолов", 
                min_value=0, 
                max_value=5, 
                value=get_setting('background_crop.halo_reduction_level', 0),
                step=1,
                key='halo_reduction_level',
                help="Уровень устранения ореолов по краям объектов с прозрачностью. 0 - отключено, 5 - максимальное устранение (может удалить часть объекта)."
            )
            set_setting('background_crop.halo_reduction_level', halo_level)
            
            if halo_level > 0:
                st.info(f"Уровень устранения ореолов: {halo_level}. Более высокие значения могут привести к потере деталей по краям.")
            
            # Добавляем отдельную опцию для обрезки
            enable_crop = st.checkbox("Обрезать изображение", 
                                    value=get_setting('background_crop.enable_crop', True), 
                                    key='bgc_crop', 
                                    help="Если включено, изображение будет обрезано по границам объекта после удаления фона, уменьшая размер холста. Если выключено, фон будет удален (станет прозрачным), но размер холста останется прежним. Включите для максимального устранения пустого пространства.")
            set_setting('background_crop.enable_crop', enable_crop)
            
            # Добавляем опции для симметричной обрезки
            if enable_crop:
                st.caption("Настройки симметричной обрезки:")
                
                crop_symmetric_axes = st.checkbox("Симметричная обрезка по осям X/Y", 
                                               value=get_setting('background_crop.crop_symmetric_axes', False), 
                                               key='bgc_sym_axes', 
                                               help="Если включено, обрезка будет выполнена симметрично по осям X и Y. Отступ слева будет равен отступу справа, а отступ сверху - отступу снизу. Полезно для создания симметричной композиции без смещения объекта по отдельным осям.")
                set_setting('background_crop.crop_symmetric_axes', crop_symmetric_axes)
                
                crop_symmetric_absolute = st.checkbox("Абсолютно симметричная обрезка", 
                                                   value=get_setting('background_crop.crop_symmetric_absolute', False), 
                                                   key='bgc_sym_abs', 
                                                   help="Если включено, обрезка будет выполнена с одинаковым отступом со всех сторон, сохраняя исходное центрирование объекта. Полезно для портретов или продуктовых изображений, где важно сохранить центрированное положение объекта относительно всего холста.")
                set_setting('background_crop.crop_symmetric_absolute', crop_symmetric_absolute)
                
                if crop_symmetric_absolute and crop_symmetric_axes:
                    st.warning("Включены обе опции симметричной обрезки. Будет применена абсолютно симметричная обрезка.", icon="⚠️")
                
                # Добавляем слайдер для дополнительной обрезки
                extra_crop_percent = st.slider(
                    "Дополнительная обрезка (%)", 
                    0.0, 100.0, 
                    value=get_setting('background_crop.extra_crop_percent', 0.0), 
                    step=0.5, 
                    key='bgc_extra_crop_percent', 
                    format="%.1f%%",
                    help="Дополнительно обрезать изображение на указанный процент после основной обрезки. Полезно для удаления лишних областей вокруг объекта или создания более компактной композиции. При 0% применяется только базовая обрезка по границам объекта. Значения до 100% позволяют создать более сильную обрезку."
                )
                set_setting('background_crop.extra_crop_percent', extra_crop_percent)
            
            bgc_per = st.checkbox("Проверять периметр", 
                                value=get_setting('background_crop.check_perimeter', True), 
                                key='bgc_perimeter', 
                                help="Включите, чтобы алгоритм анализировал периметр изображения перед обработкой. Обрезка будет выполняться только если периметр соответствует заданным условиям (белый или не белый). Помогает избежать нежелательной обработки изображений без белого фона или с объектами, касающимися края.")
            set_setting('background_crop.check_perimeter', bgc_per)
            
            if bgc_per:
                # Словарь для отображения режимов
                bgc_mode_options = {
                    "if_white": "Удалять если периметр белый",
                    "if_not_white": "Удалять если периметр не белый"
                }
                bgc_mode_keys = list(bgc_mode_options.keys())
                bgc_mode_values = list(bgc_mode_options.values())
                
                current_bgc_mode = get_setting('background_crop.perimeter_mode', 'if_white')
                try:
                    current_bgc_mode_index = bgc_mode_keys.index(current_bgc_mode)
                except ValueError:
                    current_bgc_mode_index = 0
                    set_setting('background_crop.perimeter_mode', 'if_white')
                
                selected_bgc_mode_value = st.radio(
                    "Режим проверки периметра:",
                    options=bgc_mode_values,
                    index=current_bgc_mode_index,
                    key='bgc_per_mode',
                    help="Выберите условие для обработки изображения. 'Удалять если периметр белый' - обрабатывает только изображения с белым периметром, подходит для стандартных предметных фото. 'Удалять если периметр не белый' - обрабатывает только изображения с цветным периметром, используется в специальных случаях."
                )
                selected_bgc_mode_key = bgc_mode_keys[bgc_mode_values.index(selected_bgc_mode_value)]
                set_setting('background_crop.perimeter_mode', selected_bgc_mode_key)
                
                # Добавляем отдельный допуск для периметра
                bgc_per_tol = st.slider("Допуск белого для периметра", 0, 255, 
                                     value=get_setting('background_crop.perimeter_tolerance', 10), 
                                     key='bgc_per_tol', 
                                     help="Определяет, насколько цвет пикселя периметра может отличаться от чисто белого (RGB 255,255,255), чтобы считаться белым при проверке периметра. Влияет только на решение о применении обработки, но не на саму обработку. Увеличьте для обработки изображений с сероватым или желтоватым фоном.")
                set_setting('background_crop.perimeter_tolerance', bgc_per_tol)

    # Проверяем, включено ли добавление отступов
    padding_mode = get_setting('padding.mode', 'never')
    should_expand_padding = padding_mode != 'never'
 
    
    # Устанавливаем значение по умолчанию для padding.mode, если оно не "Никогда"
    #if padding_mode == 'never' and get_setting('padding.padding_percent', 0.0) > 0:
        # Если padding_percent > 0, но mode = 'never', устанавливаем mode = 'always'
        #set_setting('padding.mode', 'always')
        #padding_mode = 'always'
        #should_expand_padding = True
    
    with st.expander("4. Добавление отступов", expanded=should_expand_padding):
        # Определяем режим padding
        padding_mode_options = {
            'never': 'Никогда',
            'always': 'Всегда',
            'if_white': 'Если белый',
            'if_not_white': 'Если не белый'
        }
        selected_padding_mode_key = st.radio(
            "Режим добавления отступов",
            options=list(padding_mode_options.keys()),
            format_func=lambda x: padding_mode_options[x],
            index=list(padding_mode_options.keys()).index(get_setting('padding.mode', 'never')),
            key='padding_mode',
            help="Выберите режим добавления прозрачных отступов вокруг изображения: 'Никогда' - отступы не добавляются; 'Всегда' - отступы добавляются во всех случаях; 'Если белый' - отступы добавляются только если периметр изображения белый; 'Если не белый' - отступы добавляются только если периметр не белый. Отступы создают дополнительное пространство вокруг объекта."
        )
        set_setting('padding.mode', selected_padding_mode_key)

        # === УСЛОВНЫЕ НАСТРОЙКИ ===
        check_perimeter_selected = selected_padding_mode_key in ['if_white', 'if_not_white']

        if check_perimeter_selected:
            st.caption("Настройки проверки периметра:")
            # Удалено: Толщина проверки периметра всегда равна 1px
            # Устанавливаем значение 1 для perimeter_margin
            set_setting('padding.perimeter_margin', 1)

            # === НОВЫЙ НЕЗАВИСИМЫЙ ДОПУСК БЕЛОГО ===
            pad_tol = st.slider("Допуск белого для проверки периметра", 0, 255, 
                                 value=get_setting('padding.perimeter_check_tolerance', 10), 
                                 key='pad_tolerance_conditional', 
                                 help="Определяет, насколько цвет пикселя периметра может отличаться от чисто белого (RGB 255,255,255), чтобы считаться белым при проверке. Влияет только на решение о добавлении отступов, но не на их размер. При низких значениях отступы добавляются только к изображениям с очень белым периметром, при высоких - к изображениям с сероватым или желтоватым фоном.")
            set_setting('padding.perimeter_check_tolerance', pad_tol)

        if selected_padding_mode_key != 'never':
            st.caption("Общие настройки полей:")
            pad_p = st.slider("Процент полей", -100.0, 200.0, 
                              value=get_setting('padding.padding_percent', 5.0), 
                              step=0.5, key='pad_perc_conditional', format="%.1f%%",
                              help="Размер добавляемых прозрачных полей в процентах от большей стороны изображения. Поля добавляются равномерно со всех четырёх сторон. Отрицательные значения позволяют обрезать изображение. Например, при значении 5% и изображении 1000x800 px будет добавлено 50 px прозрачного пространства с каждой стороны (5% от 1000). При значении -5% будет обрезано 50 px с каждой стороны.")
            set_setting('padding.padding_percent', pad_p)
            
            # Показываем предупреждение при отрицательных значениях
            if pad_p < 0:
                st.warning(f"Отрицательные поля ({pad_p}%) приведут к обрезанию изображения", icon="⚠️")

            pad_exp = st.checkbox("Разрешить полям расширять холст", 
                                  value=get_setting('padding.allow_expansion', True), 
                                  key='pad_expand_conditional', 
                                  help="Когда включено, поля могут увеличивать общий размер изображения (холста). Если выключено, поля будут добавлены только если они умещаются в исходный размер изображения - полезно, когда важно сохранить точные размеры исходного файла. Обычно следует оставить включенным для достижения наилучшего визуального результата.")
            set_setting('padding.allow_expansion', pad_exp)

    # Проверяем, включена ли настройка яркости и контраста
    should_expand_brightness = get_setting('brightness_contrast.enable_bc', False)
    
    with st.expander("5. Яркость и контраст", expanded=should_expand_brightness):
        enable_brightness_contrast = st.checkbox("Включить настройку яркости и контраста", 
                                               value=get_setting('brightness_contrast.enable_bc', False),
                                               key='enable_brightness_contrast',
                                               help="Включить регулировку яркости и контраста изображения. Позволяет осветлить темные изображения, затемнить перэкспонированные или усилить контрастность для большей детализации. Эти настройки применяются на финальном этапе обработки.")
        set_setting('brightness_contrast.enable_bc', enable_brightness_contrast)
        if enable_brightness_contrast:
            brightness_factor = st.slider("Яркость", 0.1, 3.0, 
                                          value=get_setting('brightness_contrast.brightness_factor', 1.0), 
                                          step=0.05, key='bc_brightness', format="%.2f",
                                          help="Коэффициент яркости: 1.0 - без изменений, меньше 1.0 - темнее, больше 1.0 - светлее. Например, 0.8 затемнит изображение на 20%, а 1.5 сделает его на 50% ярче. Используйте для коррекции слишком темных или светлых изображений.")
            set_setting('brightness_contrast.brightness_factor', brightness_factor)
            
            contrast_factor = st.slider("Контраст", 0.1, 3.0, 
                                        value=get_setting('brightness_contrast.contrast_factor', 1.0), 
                                        step=0.05, key='bc_contrast', format="%.2f",
                                        help="Коэффициент контраста: 1.0 - без изменений, меньше 1.0 - меньше контраста (более плоское изображение), больше 1.0 - больше контраста (более выраженная разница между светлыми и темными участками). Высокий контраст подчеркивает детали, низкий создает более мягкое изображение.")
            set_setting('brightness_contrast.contrast_factor', contrast_factor)
    # ========================

    # Проверяем, включена ли настройка слияния с шаблоном
    should_expand_merge = get_setting('merge_settings.enable_merge', False)
    
    # Показываем экспандер только если не выбран режим "Создание коллажей"
    if st.session_state.selected_processing_mode != "Создание коллажей":
        with st.expander("6. Слияние с шаблоном", expanded=should_expand_merge):
            enable_merge = st.checkbox(
                "Включить слияние с шаблоном", 
                value=get_setting('merge_settings.enable_merge', False),
                key='enable_merge',
                help="Включить слияние изображения с шаблоном"
            )
            set_setting('merge_settings.enable_merge', enable_merge)
            
            if enable_merge:
                # Настройки слияния
                st.subheader("Настройки слияния")
                
                # --- ДОБАВЛЕНО: Отображение текущего шаблона из настроек ---
                current_relative_path = get_setting('merge_settings.template_path', '')
                if current_relative_path:
                    current_absolute_path = os.path.abspath(current_relative_path)
                    if os.path.isfile(current_absolute_path):
                        st.caption(f"✅ Текущий шаблон: `{current_relative_path}` (Найден)")
                    else:
                        st.caption(f"❌ Текущий шаблон не найден: `{current_relative_path}`")
                else:
                    st.caption("ℹ️ Текущий шаблон не выбран в настройках.")
                # -----------------------------------------------------------

                # Путь к шаблону - только загрузка файла
                st.caption("Загрузить НОВЫЙ файл шаблона (заменит текущий)") # Изменена подпись
                uploaded_file = st.file_uploader(
                    "Перетащите файл шаблона сюда",
                    accept_multiple_files=False,
                    type=['jpg', 'jpeg', 'png', 'psd'],
                    key='template_uploader',
                    label_visibility="collapsed", # Скрываем стандартную метку
                    help="Загрузите изображение для использования в качестве шаблона. Поддерживаются форматы JPG, PNG и PSD"
                )
                
                if uploaded_file:
                    # Создаем папку для шаблонов, если её еще нет
                    templates_folder = os.path.join(os.getcwd(), "templates")
                    if not os.path.exists(templates_folder):
                        os.makedirs(templates_folder)
                    
                    # Сохраняем загруженный файл в папку templates
                    template_path = os.path.join(templates_folder, uploaded_file.name)
                    try:
                        with open(template_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())
                        
                        # Сохраняем относительный путь к шаблону
                        relative_template_path = os.path.join("templates", uploaded_file.name)
                        set_setting('merge_settings.template_path', relative_template_path)
                        st.success(f"Новый шаблон успешно загружен и сохранен: {relative_template_path}")
                        # --- УДАЛЕНО: Небольшая задержка и rerun, чтобы UI обновился и показал новый путь ---
                        # time.sleep(0.5) 
                        # st.rerun()
                    except Exception as e_save:
                         st.error(f"Ошибка сохранения шаблона '{template_path}': {e_save}")
                    
                    # Убраны старые проверки существования файла отсюда, т.к. они теперь выше

                # --- Убрана секция else: st.caption("ℹ️ Файл шаблона не выбран") --- 
                # Она больше не нужна, т.к. статус показывается выше до загрузчика

                # Чекбокс для включения/отключения соотношения размеров
                scaling_mode = st.radio(
                    "Режим масштабирования",
                    options=["Без масштабирования", "Вписать изображение в шаблон", "Вписать шаблон в изображение", "Использовать соотношение размеров"],
                    index=0,
                    key="scaling_mode",
                    help="Выберите способ масштабирования: 'Без масштабирования' - исходные размеры; 'Вписать изображение в шаблон' - изображение растягивается/уменьшается до размеров шаблона; 'Вписать шаблон в изображение' - шаблон растягивается/уменьшается до размеров изображения; 'Соотношение размеров' - применяется указанное соотношение"
                )
                
                # Сохраняем выбранный режим
                if scaling_mode == "Без масштабирования":
                    set_setting('merge_settings.enable_width_ratio', False)
                    set_setting('merge_settings.fit_image_to_template', False)
                    set_setting('merge_settings.fit_template_to_image', False)
                    set_setting('merge_settings.no_scaling', True)  # Добавляем настройку no_scaling
                elif scaling_mode == "Вписать изображение в шаблон":
                    set_setting('merge_settings.enable_width_ratio', False)
                    set_setting('merge_settings.fit_image_to_template', True)
                    set_setting('merge_settings.fit_template_to_image', False)
                    set_setting('merge_settings.no_scaling', False)  # Отключаем no_scaling
                elif scaling_mode == "Вписать шаблон в изображение":
                    set_setting('merge_settings.enable_width_ratio', False)
                    set_setting('merge_settings.fit_image_to_template', False)
                    set_setting('merge_settings.fit_template_to_image', True)
                    set_setting('merge_settings.no_scaling', False)  # Отключаем no_scaling
                else:  # "Использовать соотношение размеров"
                    set_setting('merge_settings.enable_width_ratio', True)
                    set_setting('merge_settings.fit_image_to_template', False)
                    set_setting('merge_settings.fit_template_to_image', False)
                    set_setting('merge_settings.no_scaling', False)  # Отключаем no_scaling
                
                # Настройки соотношения размеров
                if scaling_mode == "Использовать соотношение размеров":
                    st.caption("Соотношение размеров изображения к шаблону")
                    col1, col2 = st.columns(2)
                    with col1:
                        width_ratio_w = st.number_input("Шаблон", min_value=0.1, max_value=10.0, value=get_setting('merge_settings.width_ratio', [1.0, 1.0])[0], step=0.1, key="merge_width_ratio_w", help="Соотношение ширины шаблона к ширине изображения. Например: 2.0 означает, что шаблон будет в 2 раза шире изображения")
                    with col2:
                        width_ratio_h = st.number_input("Изображение", min_value=0.1, max_value=10.0, value=get_setting('merge_settings.width_ratio', [1.0, 1.0])[1], step=0.1, key="merge_width_ratio_h", help="Соотношение высоты изображения к высоте шаблона. Например: 2.0 означает, что изображение будет в 2 раза выше шаблона")
                    
                    # Обновляем пояснение
                    st.caption("Например: 1.0, 1.0 - изображение будет такого же размера как шаблон; 2.0, 2.0 - изображение будет в 2 раза больше шаблона; 0.5, 0.5 - изображение будет в 2 раза меньше шаблона")
                    
                    # Сохраняем настройки соотношения размеров только если значения валидны
                    if width_ratio_w > 0 and width_ratio_h > 0:
                        set_setting('merge_settings.width_ratio', [width_ratio_w, width_ratio_h])
                        log.info(f"Width ratio settings updated - w: {width_ratio_w}, h: {width_ratio_h}")
                    else:
                        st.warning("Значения соотношения размеров должны быть больше 0")
                
                # Порядок слоев
                st.caption("Порядок слоев")
                template_on_top = st.checkbox("Шаблон поверх изображения", value=get_setting('merge_settings.template_on_top', True), key="merge_template_on_top", help="Если включено, шаблон будет размещен поверх изображения; если выключено - изображение будет наверху")
                set_setting('merge_settings.template_on_top', template_on_top)
                
                # Позиция изображения
                st.caption("Позиция изображения")
                position = st.selectbox(
                    "Позиция изображения",
                    options=['center', 'top', 'bottom', 'left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right'],
                    index=['center', 'top', 'bottom', 'left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right'].index(get_setting('merge_settings.position', 'center')),
                    format_func=lambda x: {
                        'center': 'Центр',
                        'top': 'Верх',
                        'bottom': 'Низ',
                        'left': 'Лево',
                        'right': 'Право',
                        'top-left': 'Верх-лево',
                        'top-right': 'Верх-право',
                        'bottom-left': 'Низ-лево',
                        'bottom-right': 'Низ-право'
                    }[x],
                    help="Укажите положение изображения на холсте. Это определяет, где будет размещено изображение относительно шаблона"
                )
                set_setting('merge_settings.position', position)
                
                # Позиция шаблона
                st.caption("Позиция шаблона")
                template_position = st.selectbox(
                    "Позиция шаблона",
                    options=['center', 'top', 'bottom', 'left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right'],
                    index=['center', 'top', 'bottom', 'left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right'].index(get_setting('merge_settings.template_position', 'center')),
                    format_func=lambda x: {
                        'center': 'Центр',
                        'top': 'Верх',
                        'bottom': 'Низ',
                        'left': 'Лево',
                        'right': 'Право',
                        'top-left': 'Верх-лево',
                        'top-right': 'Верх-право',
                        'bottom-left': 'Низ-лево',
                        'bottom-right': 'Низ-право'
                    }[x],
                    help="Укажите положение шаблона на холсте. Это определяет, где будет размещен шаблон относительно изображения"
                )
                set_setting('merge_settings.template_position', template_position)
                
                # Обработка шаблона
                process_template = st.checkbox("Обрабатывать шаблон", value=get_setting('merge_settings.process_template', False), key="merge_process_template", help="Если включено, к шаблону будут применены те же операции обработки, что и к основному изображению (отбеливание, удаление фона и т.д.)")
                set_setting('merge_settings.process_template', process_template)
    # ==============================================

    # Настройки, зависящие от режима
    current_mode_local_for_settings = st.session_state.selected_processing_mode

    if current_mode_local_for_settings == "Обработка отдельных файлов":
        # === ВОЗВРАЩАЕМ HEADER ===
        # === ЭКСПАНДЕР 1 (теперь не вложенный) ===
        with st.expander("Финальный размер и формат", expanded=False):
            # --- Соотношение сторон --- 
            enable_ratio_ind = st.checkbox("Принудительное соотношение сторон", 
                                           value=get_setting('individual_mode.enable_force_aspect_ratio', False),
                                           key='ind_enable_ratio',
                                           help="Если включено, изображение будет приведено к указанному соотношению сторон путем добавления прозрачных или цветных полей")
            set_setting('individual_mode.enable_force_aspect_ratio', enable_ratio_ind)
            if enable_ratio_ind:
                st.caption("Соотношение (W:H)")
                col_r1, col_r2 = st.columns(2)
                # Получаем сохраненное значение
                current_ratio_ind_val = get_setting('individual_mode.force_aspect_ratio') # Убрали дефолт отсюда
                
                # === ДОБАВЛЕНА ПРОВЕРКА ===
                if isinstance(current_ratio_ind_val, (list, tuple)) and len(current_ratio_ind_val) == 2:
                    default_w_ind = float(current_ratio_ind_val[0])
                    default_h_ind = float(current_ratio_ind_val[1])
                else:
                    log.warning(f"Invalid value for force_aspect_ratio found: {current_ratio_ind_val}. Using default 1:1")
                    default_w_ind = 1.0
                    default_h_ind = 1.0
                    # Установим дефолтное значение в настройки, если оно некорректно
                    if current_ratio_ind_val is not None: # Не перезаписываем, если изначально было None и флаг выключен
                       set_setting('individual_mode.force_aspect_ratio', [default_w_ind, default_h_ind])
                # ==========================

                with col_r1: 
                    ratio_w_ind = st.number_input("W", 0.1, 100.0, value=default_w_ind, step=0.1, 
                                                key='ind_ratio_w', format="%.1f", 
                                                label_visibility="collapsed", 
                                                help="Ширина в соотношении сторон. Например, для соотношения 16:9 введите 16")
                with col_r2: 
                    ratio_h_ind = st.number_input("H", 0.1, 100.0, value=default_h_ind, step=0.1, 
                                                key='ind_ratio_h', format="%.1f", 
                                                label_visibility="collapsed", 
                                                help="Высота в соотношении сторон. Например, для соотношения 16:9 введите 9")
                # Сохраняем, только если оба > 0
                if ratio_w_ind > 0 and ratio_h_ind > 0:
                     # Сохраняем только если значение изменилось 
                     if [ratio_w_ind, ratio_h_ind] != get_setting('individual_mode.force_aspect_ratio'):
                          set_setting('individual_mode.force_aspect_ratio', [ratio_w_ind, ratio_h_ind])
                else: st.warning("Соотношение должно быть больше 0") # Валидация
            
            # --- Максимальный размер --- 
            enable_max_dim_ind = st.checkbox("Максимальный размер",
                                             value=get_setting('individual_mode.enable_max_dimensions', False),
                                             key='ind_enable_maxdim',
                                             help="Если включено, изображение будет уменьшено, если его размеры превышают указанные максимальные значения. Пропорции изображения сохраняются.")
            set_setting('individual_mode.enable_max_dimensions', enable_max_dim_ind)
            if enable_max_dim_ind:
                st.caption("Макс. размер (ШxВ, px)")
                col_m1, col_m2 = st.columns(2)
                with col_m1: 
                    max_w_ind = st.number_input("Ш", 1, 10000, 
                                              value=get_setting('individual_mode.max_output_width', 1500), 
                                              step=50, key='ind_max_w', 
                                              label_visibility="collapsed", 
                                              help="Максимальная ширина в пикселях")
                    set_setting('individual_mode.max_output_width', max_w_ind)
                with col_m2: 
                    max_h_ind = st.number_input("В", 1, 10000, 
                                              value=get_setting('individual_mode.max_output_height', 1500), 
                                              step=50, key='ind_max_h', 
                                              label_visibility="collapsed", 
                                              help="Максимальная высота в пикселях")
                    set_setting('individual_mode.max_output_height', max_h_ind)

            # --- Точный холст --- 
            enable_exact_ind = st.checkbox("Точный холст", 
                                           value=get_setting('individual_mode.enable_exact_canvas', False),
                                           key='ind_enable_exact',
                                           help="Если включено, изображение будет размещено на холсте точного размера. Изображение будет отцентрировано и масштабировано для сохранения пропорций.")
            set_setting('individual_mode.enable_exact_canvas', enable_exact_ind)

            if enable_exact_ind:
                st.caption("Размер холста (ШxВ, px)")
                col_e1, col_e2 = st.columns(2)
                with col_e1:
                    exact_w_ind = st.number_input("Ш", 1, 10000, 
                                                value=get_setting('individual_mode.final_exact_width', 1), 
                                                step=10, key='ind_exact_w', 
                                                label_visibility="collapsed",
                                                help="Точная ширина холста в пикселях")
                    set_setting('individual_mode.final_exact_width', exact_w_ind)
                with col_e2:
                    exact_h_ind = st.number_input("В", 1, 10000, 
                                                value=get_setting('individual_mode.final_exact_height', 1), 
                                                step=10, key='ind_exact_h', 
                                                label_visibility="collapsed",
                                                help="Точная высота холста в пикселях")
                    set_setting('individual_mode.final_exact_height', exact_h_ind)

            # --- Специальная обработка первого файла ---
            special_first_file = st.checkbox("Особенно обрабатывать первый файл",
                                           value=get_setting('individual_mode.special_first_file', False),
                                           key='special_first_file',
                                           help="Если включено, первый файл будет обработан с особыми настройками.")
            set_setting('individual_mode.special_first_file', special_first_file)

            if special_first_file:
                st.subheader("Настройки для первого файла")
                
                # --- Принудительное соотношение сторон ---
                enable_force_first = st.checkbox("Принудительное соотношение сторон", 
                                               value=get_setting('individual_mode.first_file_settings.enable_force_aspect_ratio', False),
                                               key='first_force_aspect',
                                               help="Если включено, изображение будет приведено к заданному соотношению сторон.")
                set_setting('individual_mode.first_file_settings.enable_force_aspect_ratio', enable_force_first)
                
                if enable_force_first:
                    st.caption("Соотношение сторон (Ш:В)")
                    col_f1, col_f2 = st.columns(2)
                    with col_f1:
                        force_w_first = st.number_input("Ш", 1, 100, 
                                                      value=get_setting('individual_mode.first_file_settings.force_aspect_ratio', [1, 1])[0], 
                                                      step=1, key='first_force_w', 
                                                      label_visibility="collapsed")
                    with col_f2:
                        force_h_first = st.number_input("В", 1, 100, 
                                                      value=get_setting('individual_mode.first_file_settings.force_aspect_ratio', [1, 1])[1], 
                                                      step=1, key='first_force_h', 
                                                      label_visibility="collapsed")
                    set_setting('individual_mode.first_file_settings.force_aspect_ratio', [force_w_first, force_h_first])
                
                # --- Максимальные размеры ---
                enable_max_first = st.checkbox("Максимальные размеры", 
                                             value=get_setting('individual_mode.first_file_settings.enable_max_dimensions', False),
                                             key='first_max_dim',
                                             help="Если включено, изображение будет уменьшено, если его размеры превышают заданные максимальные значения.")
                set_setting('individual_mode.first_file_settings.enable_max_dimensions', enable_max_first)
                
                if enable_max_first:
                    st.caption("Макс. размер (ШxВ, px)")
                    col_m1, col_m2 = st.columns(2)
                    with col_m1:
                        max_w_first = st.number_input("Ш", 1, 10000, 
                                                    value=get_setting('individual_mode.first_file_settings.max_output_width', 1500), 
                                                    step=50, key='first_max_w', 
                                                    label_visibility="collapsed")
                    with col_m2:
                        max_h_first = st.number_input("В", 1, 10000, 
                                                    value=get_setting('individual_mode.first_file_settings.max_output_height', 1500), 
                                                    step=50, key='first_max_h', 
                                                    label_visibility="collapsed")
                    set_setting('individual_mode.first_file_settings.max_output_width', max_w_first)
                    set_setting('individual_mode.first_file_settings.max_output_height', max_h_first)
                
                # --- Точный холст ---
                enable_exact_first = st.checkbox("Точный холст", 
                                               value=get_setting('individual_mode.first_file_settings.enable_exact_canvas', False),
                                               key='first_exact_canvas',
                                               help="Если включено, изображение будет размещено на холсте точного размера.")
                set_setting('individual_mode.first_file_settings.enable_exact_canvas', enable_exact_first)
                
                if enable_exact_first:
                    st.caption("Размер холста (ШxВ, px)")
                    col_e1, col_e2 = st.columns(2)
                    with col_e1:
                        exact_w_first = st.number_input("Ш", 1, 10000, 
                                                      value=get_setting('individual_mode.first_file_settings.final_exact_width', 1), 
                                                      step=10, key='first_exact_w', 
                                                      label_visibility="collapsed")
                    with col_e2:
                        exact_h_first = st.number_input("В", 1, 10000, 
                                                      value=get_setting('individual_mode.first_file_settings.final_exact_height', 1), 
                                                      step=10, key='first_exact_h', 
                                                      label_visibility="collapsed")
                    set_setting('individual_mode.first_file_settings.final_exact_width', exact_w_first)
                    set_setting('individual_mode.first_file_settings.final_exact_height', exact_h_first)

            # --- Параметры вывода (без изменений) ---
            st.caption("Параметры вывода")
            fmt_col, q_col, bg_col = st.columns([1,1,2])
            with fmt_col:
                 output_format_ind = st.selectbox("Формат", ["jpg", "png"], 
                                            index=["jpg", "png"].index(get_setting('individual_mode.output_format', 'jpg')), 
                                            key='ind_format',
                                            help="JPG - меньше размер файла, нет прозрачности. PNG - больше размер, сохраняет прозрачность.")
                 set_setting('individual_mode.output_format', output_format_ind)
                 
            with q_col:
                 if output_format_ind == 'jpg': 
                     q_ind = st.number_input("Кач-во", 1, 100, 
                                       value=get_setting('individual_mode.jpeg_quality', 95), 
                                       key='ind_quality',
                                       help="Качество сжатия JPG (1-100). Выше значение - лучше качество, но больше размер файла.")
                     set_setting('individual_mode.jpeg_quality', q_ind)
                 else:
                     transparent_bg = st.checkbox("Прозрачный", 
                                               value=get_setting('individual_mode.png_transparent_background', True),
                                               key='ind_png_transparent',
                                               help="Если включено, фон будет прозрачным")
                     set_setting('individual_mode.png_transparent_background', transparent_bg)
            with bg_col:
                 if output_format_ind == 'jpg':
                     bg_color_str_ind = ",".join(map(str, get_setting('individual_mode.jpg_background_color', [255,255,255])))
                     new_bg_color_str_ind = st.text_input("Фон (R,G,B)", 
                                                    value=bg_color_str_ind, 
                                                    key='ind_bg',
                                                    help="Цвет фона для JPG в формате R,G,B (значения 0-255). Для белого: 255,255,255, для черного: 0,0,0")
                     try:
                         new_bg_color_ind = list(map(int, new_bg_color_str_ind.split(',')))
                         if len(new_bg_color_ind) == 3 and all(0 <= c <= 255 for c in new_bg_color_ind):
                             if new_bg_color_ind != get_setting('individual_mode.jpg_background_color', [255,255,255]): 
                                 set_setting('individual_mode.jpg_background_color', new_bg_color_ind)
                                 # Синхронизируем цвет фона для PNG с цветом фона для JPG
                                 set_setting('individual_mode.png_background_color', new_bg_color_ind)
                         else: st.caption("❌ R,G,B 0-255")
                     except ValueError: st.caption("❌ R,G,B 0-255")
                 elif not get_setting('individual_mode.png_transparent_background', True):
                     # Используем тот же интерфейс для цвета фона PNG, что и для JPG
                     bg_color_str_ind = ",".join(map(str, get_setting('individual_mode.jpg_background_color', [255,255,255])))
                     new_bg_color_str_ind = st.text_input("Фон (R,G,B)", 
                                                    value=bg_color_str_ind, 
                                                    key='ind_bg_png',
                                                    help="Цвет фона для PNG в формате R,G,B (значения 0-255). Для белого: 255,255,255, для черного: 0,0,0")
                     try:
                         new_bg_color_ind = list(map(int, new_bg_color_str_ind.split(',')))
                         if len(new_bg_color_ind) == 3 and all(0 <= c <= 255 for c in new_bg_color_ind):
                             if new_bg_color_ind != get_setting('individual_mode.jpg_background_color', [255,255,255]): 
                                 # Устанавливаем тот же цвет для обоих форматов
                                 set_setting('individual_mode.jpg_background_color', new_bg_color_ind)
                                 set_setting('individual_mode.png_background_color', new_bg_color_ind)
                         else: st.caption("❌ R,G,B 0-255")
                     except ValueError: st.caption("❌ R,G,B 0-255")
                 else:
                     st.caption("-")

            # Добавляем чекбокс для удаления метаданных на всю ширину
            remove_metadata = st.checkbox("Удалить метаданные и установить единую дату", 
                                       value=get_setting('individual_mode.remove_metadata', False),
                                       key='ind_remove_metadata',
                                       help="Удалить все метаданные из файла и установить единую дату создания/изменения для всех файлов с интервалом в 2 секунды между ними")
            set_setting('individual_mode.remove_metadata', remove_metadata)
            
            # Добавляем чекбокс для обратного порядка
            if remove_metadata:
                reverse_order = st.checkbox("Обратный порядок дат", 
                                         value=get_setting('individual_mode.reverse_date_order', False),
                                         key='ind_reverse_date_order',
                                         help="Если включено, даты будут установлены в обратном порядке (от новых к старым)")
                set_setting('individual_mode.reverse_date_order', reverse_order)
        # === КОНЕЦ ЭКСПАНДЕРА 1 ===
        
        # === ЭКСПАНДЕР 2 УДАЛЕН, ПЕРЕМЕЩЕН ВЫШЕ ===
        # with st.expander("Переименование и удаление", expanded=False):
        #     # --- Переименование --- 
        #     enable_rename_ind = st.checkbox("Переименовать файлы (по артикулу)",
        #                                     value=get_setting('individual_mode.enable_rename', False),
        #                                     key='ind_enable_rename')
        #     set_setting('individual_mode.enable_rename', enable_rename_ind)
        #     if enable_rename_ind:
        #         article_ind = st.text_input("Артикул для переименования",
        #                                     value=get_setting('individual_mode.article_name', ''),
        #                                     key='ind_article',
        #                                     placeholder="Введите артикул...")
        #         set_setting('individual_mode.article_name', article_ind)
        #         if article_ind: st.caption("Файлы будут вида: [Артикул]_1.jpg, ...")
        #         else: st.warning("Введите артикул для переименования.") # Валидация
        #     
        #     # --- Удаление (без изменений) ---
        #     delete_orig_ind = st.checkbox("Удалять оригиналы после обработки?",
        #                                  value=get_setting('individual_mode.delete_originals', False),
        #                                  key='ind_delete_orig')
        #     set_setting('individual_mode.delete_originals', delete_orig_ind)
        #     if delete_orig_ind: st.warning("ВНИМАНИЕ: Удаление необратимо!", icon="⚠️")
        # === КОНЕЦ ЭКСПАНДЕРА 2 ===
        # === КОНЕЦ УДАЛЕННОГО ОБЩЕГО ЭКСПАНДЕРА ===

    elif current_mode_local_for_settings == "Создание коллажей":
        with st.expander("Параметры сетки коллажа", expanded=False):
            # --- Кол-во столбцов ---
            enable_cols_coll = st.checkbox("Задать кол-во столбцов",
                                             value=get_setting('collage_mode.enable_forced_cols', False),
                                             key='coll_enable_cols',
                                             help="Если включено, позволяет задать фиксированное количество столбцов в коллаже. Иначе количество определяется автоматически")
            set_setting('collage_mode.enable_forced_cols', enable_cols_coll)
            if enable_cols_coll:
                 cols_coll = st.number_input("Столбцов", 1, 20, value=get_setting('collage_mode.forced_cols', 3), step=1, key='coll_cols', help="Фиксированное количество столбцов в коллаже. Изображения будут распределены по этим столбцам")
                 set_setting('collage_mode.forced_cols', cols_coll)
            else: 
                 st.caption("Кол-во столбцов: Авто")
            
            # --- Отступ (с поддержкой отрицательных значений) ---
            enable_spacing = st.checkbox("Задать отступ между фото", value=get_setting('collage_mode.enable_spacing', True), 
                                         key='coll_enable_spacing', help="Включает настройку отступов между фотографиями")
            set_setting('collage_mode.enable_spacing', enable_spacing)
            
            if enable_spacing:
                spc_coll = st.slider("Отступ между фото (%)", -100.0, 300.0, value=get_setting('collage_mode.spacing_percent', 0.0), 
                                     step=0.5, key='coll_spacing', format="%.1f%%", 
                                     help="Размер пространства между фотографиями в коллаже. Указывается в процентах от размера изображений. Отрицательные значения позволяют накладывать изображения друг на друга.")
                set_setting('collage_mode.spacing_percent', spc_coll)
                
                # Показываем предупреждение при отрицательных значениях
                if spc_coll < 0:
                    st.warning(f"Отрицательный отступ ({spc_coll}%) приведёт к наложению изображений друг на друга", icon="⚠️")
            
            # --- Внешние поля коллажа ---
            enable_outer_margins = st.checkbox("Задать внешние поля коллажа", value=get_setting('collage_mode.enable_outer_margins', True), 
                                                key='coll_enable_outer_margins', help="Включает настройку внешних полей коллажа")
            set_setting('collage_mode.enable_outer_margins', enable_outer_margins)
            
            if enable_outer_margins:
                outer_margins = st.slider("Внешние поля (%)", -100.0, 100.0, value=get_setting('collage_mode.outer_margins_percent', 0.0), 
                                         step=0.5, key='coll_outer_margins', format="%.1f%%", 
                                         help="Размер внешних полей вокруг коллажа. Указывается в процентах от размера изображений. Отрицательные значения позволяют обрезать края изображений.")
                set_setting('collage_mode.outer_margins_percent', outer_margins)
                
                # Показываем предупреждение при отрицательных значениях
                if outer_margins < 0:
                    st.warning(f"Отрицательные внешние поля ({outer_margins}%) приведут к обрезанию краёв изображений в коллаже", icon="⚠️")

        with st.expander("Пропорциональное размещение", expanded=False):
            prop_enabled = st.checkbox("Включить пропорциональное размещение", 
                                        value=get_setting('collage_mode.proportional_placement', False),
                                        key='coll_prop_enable',
                                        help="Масштабировать изображения относительно друг друга перед размещением.")
            set_setting('collage_mode.proportional_placement', prop_enabled)
            if prop_enabled:
                ratios_str = ",".join(map(str, get_setting('collage_mode.placement_ratios', [1.0])))
                new_ratios_str = st.text_input("Соотношения размеров (через запятую)", 
                                                value=ratios_str, 
                                                key='coll_ratios',
                                                help="Напр.: 1,0.8,0.8 - второе и третье фото будут 80% от размера первого.")
                try:
                    new_ratios = [float(x.strip()) for x in new_ratios_str.split(',') if x.strip()]
                    if new_ratios and all(r > 0 for r in new_ratios): set_setting('collage_mode.placement_ratios', new_ratios)
                    else: st.caption("❌ Введите положительные числа")
                except ValueError:
                    st.caption("❌ Неверный формат чисел")

        with st.expander("Размер и формат коллажа", expanded=False):
            # --- Соотношение сторон --- 
            enable_ratio_coll = st.checkbox("Принудительное соотношение сторон коллажа", 
                                              value=get_setting('collage_mode.enable_force_aspect_ratio', False),
                                              key='coll_enable_ratio',
                                              help="Если включено, коллаж будет приведен к указанному соотношению сторон путем добавления полей")
            set_setting('collage_mode.enable_force_aspect_ratio', enable_ratio_coll)
            if enable_ratio_coll:
                st.caption("Соотношение (W:H)")
                col_r1_coll, col_r2_coll = st.columns(2)
                current_ratio_coll_val = get_setting('collage_mode.force_collage_aspect_ratio', [16.0, 9.0])
                default_w_coll = float(current_ratio_coll_val[0])
                default_h_coll = float(current_ratio_coll_val[1])
                with col_r1_coll: 
                    ratio_w_coll = st.number_input("W", 0.1, 100.0, 
                                                 value=default_w_coll, step=0.1, 
                                                 key='coll_ratio_w', format="%.1f", 
                                                 label_visibility="collapsed",
                                                 help="Ширина в соотношении сторон. Например, для соотношения 16:9 введите 16")
                with col_r2_coll: 
                    ratio_h_coll = st.number_input("H", 0.1, 100.0, 
                                                 value=default_h_coll, step=0.1, 
                                                 key='coll_ratio_h', format="%.1f", 
                                                 label_visibility="collapsed",
                                                 help="Высота в соотношении сторон. Например, для соотношения 16:9 введите 9")
                if ratio_w_coll > 0 and ratio_h_coll > 0: set_setting('collage_mode.force_collage_aspect_ratio', [ratio_w_coll, ratio_h_coll])
                else: st.warning("Соотношение должно быть больше 0")

            # --- Максимальный размер --- 
            enable_max_dim_coll = st.checkbox("Максимальный размер коллажа",
                                                value=get_setting('collage_mode.enable_max_dimensions', False),
                                                key='coll_enable_maxdim',
                                                help="Если включено, коллаж будет уменьшен, если его размеры превышают указанные максимальные значения")
            set_setting('collage_mode.enable_max_dimensions', enable_max_dim_coll)
            if enable_max_dim_coll:
                st.caption("Макс. размер (ШxВ, px)")
                col_m1_coll, col_m2_coll = st.columns(2)
                with col_m1_coll: 
                    max_w_coll = st.number_input("Ш", 1, 10000, 
                                               value=get_setting('collage_mode.max_collage_width', 1500), 
                                               step=50, key='coll_max_w', 
                                               label_visibility="collapsed",
                                               help="Максимальная ширина коллажа в пикселях")
                    set_setting('collage_mode.max_collage_width', max_w_coll)
                with col_m2_coll: 
                    max_h_coll = st.number_input("В", 1, 10000, 
                                               value=get_setting('collage_mode.max_collage_height', 1500), 
                                               step=50, key='coll_max_h', 
                                               label_visibility="collapsed",
                                               help="Максимальная высота коллажа в пикселях")
                    set_setting('collage_mode.max_collage_height', max_h_coll)

            # --- Точный холст --- 
            enable_exact_coll = st.checkbox("Точный холст коллажа", 
                                              value=get_setting('collage_mode.enable_exact_canvas', False),
                                              key='coll_enable_exact',
                                              help="Если включено, коллаж будет размещен на холсте точного размера с сохранением пропорций")
            set_setting('collage_mode.enable_exact_canvas', enable_exact_coll)
            if enable_exact_coll:
                st.caption("Точный холст (ШxВ, px)")
                col_e1_coll, col_e2_coll = st.columns(2)
                with col_e1_coll: 
                    exact_w_coll = st.number_input("Ш", 1, 10000, 
                                                 value=get_setting('collage_mode.final_collage_exact_width', 1920), 
                                                 step=50, key='coll_exact_w', 
                                                 label_visibility="collapsed",
                                                 help="Точная ширина холста коллажа в пикселях")
                    set_setting('collage_mode.final_collage_exact_width', exact_w_coll)
                with col_e2_coll: 
                    exact_h_coll = st.number_input("В", 1, 10000, 
                                                 value=get_setting('collage_mode.final_collage_exact_height', 1080), 
                                                 step=50, key='coll_exact_h', 
                                                 label_visibility="collapsed",
                                                 help="Точная высота холста коллажа в пикселях")
                    set_setting('collage_mode.final_collage_exact_height', exact_h_coll)

            # --- Параметры вывода (без изменений) ---
            st.caption("Параметры вывода коллажа")
            fmt_col_coll, q_col_coll, bg_col_coll = st.columns([1,1,2])
            with fmt_col_coll:
                 output_format_coll = st.selectbox("Формат", ["jpg", "png"], 
                                                index=["jpg", "png"].index(get_setting('collage_mode.output_format', 'jpg')), 
                                                key='coll_format',
                                                help="JPG - меньше размер файла, нет прозрачности. PNG - больше размер файла, сохраняет прозрачность")
                 set_setting('collage_mode.output_format', output_format_coll)
            with q_col_coll:
                 if output_format_coll == 'jpg': 
                     q_coll = st.number_input("Кач-во", 1, 100, 
                                            value=get_setting('collage_mode.jpeg_quality', 95), 
                                            key='coll_quality', 
                                            help="Качество сжатия JPG (1-100). Выше значение - лучше качество, но больше размер файла.")
                     set_setting('collage_mode.jpeg_quality', q_coll)
                 else:
                     transparent_bg = st.checkbox("Прозрачный", 
                                               value=get_setting('collage_mode.png_transparent_background', True),
                                               key='coll_png_transparent',
                                               help="Если включено, фон будет прозрачным")
                     set_setting('collage_mode.png_transparent_background', transparent_bg)
                     
                     # Добавляем настройку для использования маски вместо прозрачности
                     if not transparent_bg:  # Показываем только когда прозрачность выключена
                         use_mask = st.checkbox("Маска вместо прозрачности", 
                                               value=get_setting('collage_mode.use_mask_instead_of_transparency', True),
                                               key='coll_use_mask',
                                               help="Использовать маску для избежания эффекта ореола при масштабировании")
                         set_setting('collage_mode.use_mask_instead_of_transparency', use_mask)
            with bg_col_coll:
                 if output_format_coll == 'jpg':
                     bg_color_str_coll = ",".join(map(str, get_setting('collage_mode.jpg_background_color', [255,255,255])))
                     new_bg_color_str_coll = st.text_input("Фон (R,G,B)", 
                                                         value=bg_color_str_coll, 
                                                         key='coll_bg', 
                                                         help="Цвет фона для JPG в формате R,G,B (значения 0-255). Для белого: 255,255,255, для черного: 0,0,0")
                     try:
                         new_bg_color_coll = list(map(int, new_bg_color_str_coll.split(',')))
                         if len(new_bg_color_coll) == 3 and all(0 <= c <= 255 for c in new_bg_color_coll):
                            if new_bg_color_coll != get_setting('collage_mode.jpg_background_color', [255,255,255]): 
                                set_setting('collage_mode.jpg_background_color', new_bg_color_coll)
                                # Синхронизируем цвет фона для PNG с цветом фона для JPG
                                set_setting('collage_mode.png_background_color', new_bg_color_coll)
                         else: st.caption("❌ R,G,B 0-255")
                     except ValueError: st.caption("❌ R,G,B 0-255")
                 elif not get_setting('collage_mode.png_transparent_background', True):
                     # Используем тот же интерфейс для цвета фона PNG, что и для JPG
                     bg_color_str_coll = ",".join(map(str, get_setting('collage_mode.jpg_background_color', [255,255,255])))
                     new_bg_color_str_coll = st.text_input("Фон (R,G,B)", 
                                                         value=bg_color_str_coll, 
                                                         key='coll_bg_png',
                                                         help="Цвет фона для PNG в формате R,G,B (значения 0-255). Для белого: 255,255,255, для черного: 0,0,0")
                     try:
                         new_bg_color_coll = list(map(int, new_bg_color_str_coll.split(',')))
                         if len(new_bg_color_coll) == 3 and all(0 <= c <= 255 for c in new_bg_color_coll):
                            if new_bg_color_coll != get_setting('collage_mode.jpg_background_color', [255,255,255]): 
                                # Устанавливаем тот же цвет для обоих форматов
                                set_setting('collage_mode.jpg_background_color', new_bg_color_coll)
                                set_setting('collage_mode.png_background_color', new_bg_color_coll)
                         else: st.caption("❌ R,G,B 0-255")
                     except ValueError: st.caption("❌ R,G,B 0-255")
                 else:
                     st.caption("-")

    # Добавляем секцию настроек производительности
    with st.expander("Настройки производительности", expanded=False):
        st.info("Эти настройки позволяют ускорить обработку за счет использования многопроцессорной обработки.")
        
        # Получаем текущие настройки производительности
        performance_settings = st.session_state.current_settings.get('performance', {})
        
        # Создаем колонки для более компактного отображения
        perf_col1, perf_col2 = st.columns(2)
        
        with perf_col1:
            enable_multiprocessing = st.checkbox(
                "Включить многопроцессорную обработку", 
                value=performance_settings.get('enable_multiprocessing', False),
                key='enable_multiprocessing',
                help="Позволяет использовать все ядра процессора для ускорения обработки изображений."
            )
            
            set_setting('performance.enable_multiprocessing', enable_multiprocessing)
        
        with perf_col2:
            max_workers = st.number_input(
                "Количество процессов (0 = автоматически)", 
                min_value=0, 
                max_value=32,
                value=performance_settings.get('max_workers', 0),
                key='max_workers',
                help="Максимальное количество процессов для параллельной обработки. 0 = автоматически (по числу ядер)."
            )
            
            set_setting('performance.max_workers', int(max_workers))
        
        if enable_multiprocessing:
            num_cores = multiprocessing.cpu_count()
            st.success(f"✅ Многопроцессорная обработка включена. Доступно {num_cores} ядер CPU.")
            
            if max_workers > 0:
                st.info(f"Будет использовано {max_workers} процессов.")
            else:
                st.info(f"Будет использовано количество процессов, равное количеству ядер ({num_cores}).")
        else:
            st.warning("⚠️ Многопроцессорная обработка отключена. Обработка будет выполняться в один поток.")

# === Конец блока with st.sidebar ===

# === ОСНОВНАЯ ОБЛАСТЬ ===

# --- Заголовок ---
st.title("🖼️ Инструмент Обработки Изображений")


st.subheader(f"**Режим:** {st.session_state.selected_processing_mode} | **Активный набор:** {st.session_state.active_preset}")

# --- Секция статуса ---
# --- Стилизация кнопки ---
st.markdown("""
    <style>
        div.stButton > button:first-child {
            height: 120px;
            font-size: 24px;
        }
    </style>
""", unsafe_allow_html=True)

# --- Инициализация переменной для кнопки ---
start_button_pressed_this_run = False

# --- Кнопка Запуска ---
if st.button(f"🚀 Запустить: {st.session_state.selected_processing_mode}", 
             type="primary", 
             key="run_processing_button", 
             use_container_width=True,
             disabled=st.session_state.is_processing):  # Делаем кнопку неактивной во время обработки
    start_button_pressed_this_run = True
    st.session_state.is_processing = True  # Устанавливаем флаг обработки
    log.info(f"--- Button '{st.session_state.selected_processing_mode}' CLICKED! Processing will start below. ---")
    log_stream.seek(0)
    log_stream.truncate(0) # Очищаем лог для нового запуска
    st.session_state.saved_logs = ""  # Очищаем сохраненные логи
    log.info(f"--- Log cleared. Validating paths for mode '{st.session_state.selected_processing_mode}' ---")

# --- Логика Запуска ---
if start_button_pressed_this_run:
    log.info(f"--- Start button was pressed this run. Starting validation... ---")
    paths_ok = True
    validation_errors = []
    
    # Проверка входной папки
    input_path = get_setting('paths.input_folder_path', '')
    abs_input_path = os.path.abspath(input_path) if input_path else ''
    if not input_path or not os.path.isdir(abs_input_path):
        validation_errors.append(f"Папка с исходными файлами не найдена или не указана: '{input_path}'")
        paths_ok = False

    # Проверка пути шаблона, если включено слияние
    if get_setting('merge_settings.enable_merge', False):
        template_path = get_setting('merge_settings.template_path', '')
        if template_path:
            clean_path = template_path.strip('"\'')
            if not os.path.isfile(clean_path):
                validation_errors.append(f"❌ Файл шаблона не найден: {os.path.abspath(clean_path)}")
                paths_ok = False
        else:
            validation_errors.append("❌ Не указан путь к файлу шаблона")
            paths_ok = False

    current_mode = st.session_state.selected_processing_mode
    if current_mode == "Обработка отдельных файлов":
        output_path_ind = get_setting('paths.output_folder_path', '')
        if not output_path_ind:
            validation_errors.append("Не указана папка для результатов!")
            paths_ok = False
        if paths_ok and get_setting('individual_mode.delete_originals') and input_path and output_path_ind:
            if os.path.normcase(os.path.abspath(input_path)) == os.path.normcase(os.path.abspath(output_path_ind)):
                # Удаляем предупреждение, так как наша логика теперь обрабатывает эту ситуацию безопасно
                log.info("Input and output folders are the same with delete_originals enabled. Safe processing will be used.")
    elif current_mode == "Создание коллажей":
        output_filename_coll = get_setting('paths.output_filename', '')
        if not output_filename_coll:
            validation_errors.append("Не указано имя файла для сохранения коллажа!")
            paths_ok = False
        elif input_path and paths_ok:
            output_format_coll = get_setting('collage_mode.output_format', 'jpg').lower()
            base_name, _ = os.path.splitext(output_filename_coll)
            coll_filename_with_ext = f"{base_name}.{output_format_coll}"
            full_coll_path_with_ext = os.path.abspath(os.path.join(abs_input_path, coll_filename_with_ext))
            if os.path.isdir(full_coll_path_with_ext):
                validation_errors.append(f"Имя файла коллажа '{coll_filename_with_ext}' указывает на папку!")
                paths_ok = False

    if not paths_ok:
        log.warning("--- Path validation FAILED. Processing aborted. ---")
        for error_msg in validation_errors: 
            st.error(error_msg, icon="❌")
            log.error(f"Validation Error: {error_msg}")
        st.warning("Обработка не запущена из-за ошибок в настройках путей.", icon="⚠️")
    else:
        log.info(f"--- Path validation successful. Starting processing workflow '{current_mode}'... ---")
        st.info(f"Запускаем обработку в режиме '{current_mode}'...")
        progress_placeholder = st.empty()
        workflow_success = False # Инициализируем флаг успеха
        with st.spinner(f"Выполняется обработка... Пожалуйста, подождите."):
            try:
                current_run_settings = st.session_state.current_settings.copy()
                log.debug(f"Passing settings to workflow: {current_run_settings}")
                # Используем значение из session_state напрямую для сравнения
                mode_from_state = st.session_state.selected_processing_mode
                log.debug(f"---> Checking workflow for mode (from state): '{mode_from_state}'")
                
                # Run the appropriate processing workflow
                if mode_from_state == "Обработка отдельных файлов":
                    log.info("Condition matched: 'Обработка отдельных файлов'")
                    success = processing_workflows.run_individual_processing(**current_run_settings)
                    if not success:
                        st.error("❌ Произошла ошибка при обработке одного или нескольких файлов.", icon="⚠️")
                        log.error("Processing failed with errors")
                    else:
                        st.session_state.settings_changed = True
                        autosave_active_preset_if_changed()
                    log.info(f"Finished run_individual_processing call (result: {success})")
                    workflow_success = success
                elif mode_from_state == "Создание коллажей":
                    log.info("Condition matched: 'Создание коллажей'")
                    success = processing_workflows.run_collage_processing(**current_run_settings)
                    if not success:
                        st.error("❌ Ошибка при выполнении операции 'Создание коллажа'!")
                        log.error("Processing failed with errors")
                    else:
                        st.session_state.settings_changed = True
                        autosave_active_preset_if_changed()
                    log.info(f"Finished run_collage_processing call (result: {success})")
                    workflow_success = success
                elif mode_from_state == "Слияние изображений":
                    log.info("Condition matched: 'Слияние изображений'")
                    success = processing_workflows.run_merge_processing(**current_run_settings)
                    if not success:
                        st.error("❌ Ошибка при выполнении операции 'Слияние изображений'!")
                        log.error("Processing failed with errors")
                    log.info(f"Finished run_merge_processing call (result: {success})")
                    workflow_success = success
                else:
                    log.error(f"!!! Unknown mode_from_state encountered in processing block: '{mode_from_state}'")
                    workflow_success = False 
            except Exception as e:
                log.critical(f"!!! WORKFLOW EXECUTION FAILED with EXCEPTION: {e}", exc_info=True)
                st.error(f"Произошла критическая ошибка во время обработки: {e}", icon="🔥")
                workflow_success = False

        progress_placeholder.empty() # Очищаем спиннер
        
        # --- Вывод сообщения по результату --- 
        # Создаем placeholder для сообщения об успехе/ошибке и сохраняем в session_state
        result_message_placeholder = st.empty()
        st.session_state.result_message_placeholder = result_message_placeholder
        
        if workflow_success:
            # Проверяем наличие файлов в папке ввода
            found_files = False
            try:
                input_folder = get_setting('paths.input_folder_path', '')
                if input_folder and os.path.isdir(input_folder):
                    # Проверяем, есть ли подходящие файлы в папке
                    image_extensions = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff', '.webp', '.tif')
                    for file in os.listdir(input_folder):
                        if file.lower().endswith(image_extensions) and os.path.isfile(os.path.join(input_folder, file)):
                            found_files = True
                            break
            except Exception as e:
                log.warning(f"Не удалось проверить содержимое папки: {e}")
                found_files = True  # В случае ошибки предполагаем, что файлы есть
            
            # Если папка пуста, выводим предупреждение вместо сообщения об успехе
            if not found_files:
                # Используем mode_from_state для сообщения
                result_message_placeholder.warning("⚠️ Папка не содержит файлов для обработки!", icon="⚠️")
                st.session_state.result_message = {"type": "warning", "text": "⚠️ Папка не содержит файлов для обработки!"}
            else:
                # Используем mode_from_state для сообщения
                if mode_from_state == "Обработка отдельных файлов":
                    result_message_placeholder.success("✅ Обработка изображений завершена успешно!")
                    st.session_state.result_message = {"type": "success", "text": "✅ Обработка изображений завершена успешно!"}
                elif mode_from_state == "Создание коллажей":
                    result_message_placeholder.success("✅ Создание коллажа завершено успешно!")
                    st.session_state.result_message = {"type": "success", "text": "✅ Создание коллажа завершено успешно!"}
                elif mode_from_state == "Слияние изображений":
                    result_message_placeholder.success("✅ Слияние изображений завершено успешно!")
                    st.session_state.result_message = {"type": "success", "text": "✅ Слияние изображений завершено успешно!"}
                else:
                    # Неизвестный режим
                    result_message_placeholder.success(f"✅ Операция '{mode_from_state}' выполнена успешно!")
                    st.session_state.result_message = {"type": "success", "text": f"✅ Операция '{mode_from_state}' выполнена успешно!"}
        else:
            result_message_placeholder.error("❌ Произошла ошибка во время выполнения операции!", icon="🔥")
            st.session_state.result_message = {"type": "error", "text": "❌ Произошла ошибка во время выполнения операции!"}
        
        # В конце, перед st.rerun(), добавим:
        # Сохраняем текущие логи в session_state, чтобы они не потерялись при rerun
        st.session_state.saved_logs = log_stream.getvalue()
        log.info("--- Сохраняем логи перед rerun() ---")
        
        # Автосохранение настроек после завершения обработки (успешной или нет)
        log.info("--- Автосохранение настроек после обработки ---")
        autosave_active_preset_if_changed()
        
        st.session_state.is_processing = False  # Сбрасываем флаг обработки после завершения
        st.rerun()  # Перезагружаем страницу, чтобы обновить состояние кнопки

# --- Область для Логов ---
st.subheader("Логи и предпросмотр:")

# Отображаем сохраненное сообщение о результате выполнения, если оно есть
if 'result_message' in st.session_state:
    if st.session_state.result_message["type"] == "success":
        st.success(st.session_state.result_message["text"])
    elif st.session_state.result_message["type"] == "error":
        st.error(st.session_state.result_message["text"], icon="🔥")
    elif st.session_state.result_message["type"] == "warning":
        st.warning(st.session_state.result_message["text"], icon="⚠️")
    # Очищаем сообщение после отображения, чтобы оно не появлялось после каждого взаимодействия с UI
    if not st.session_state.is_processing:
        st.session_state.pop('result_message', None)

# Блок лога - объединяем сохраненные логи и текущие
with st.expander("📋 Журнал работы приложения", expanded=False):
    # Объединяем сохраненные логи (из прошлого запуска) с текущими
    combined_logs = st.session_state.saved_logs + log_stream.getvalue() 
    st.text_area("Лог:", value=combined_logs, height=300, 
               key='log_output_display_area', disabled=True, 
               label_visibility="collapsed")

# --- Настройки отображения результатов ---
with st.expander("⚙️ Настройки отображения результатов", expanded=False):
    # Общая галочка для всех режимов
    show_results = st.checkbox("Отображать результаты обработки", 
                              value=get_setting('ui_display.show_results', True),
                              key='show_results_checkbox',
                              help="Включите для отображения результатов обработки")
    set_setting('ui_display.show_results', show_results)
    
    current_display_mode = st.session_state.selected_processing_mode
    
    if current_display_mode == "Обработка отдельных файлов":
        # Опции только для режима пакетной обработки
        show_all_results = st.checkbox("Показывать все изображения", 
                                     value=get_setting('ui_display.show_all_images', False),
                                     key='show_all_images_checkbox',
                                     help="Показывать все обработанные изображения. Может замедлить работу при большом количестве файлов.")
        set_setting('ui_display.show_all_images', show_all_results)
        
        # Количество столбцов (доступно и при show_all)
        images_columns = st.number_input("Количество столбцов", 
                                       min_value=1, max_value=6, value=get_setting('ui_display.columns_count', 3),
                                       step=1, key='columns_count',
                                       help="Количество столбцов для отображения изображений")
        set_setting('ui_display.columns_count', images_columns)
        
        if not show_all_results:
            # Количество изображений (доступно только если не show_all)
            images_limit = st.number_input("Количество изображений", 
                                        min_value=1, value=get_setting('ui_display.images_limit', 18),
                                        step=3, key='images_limit',
                                        help="Максимальное количество изображений для отображения")
            set_setting('ui_display.images_limit', images_limit)
    
    elif current_display_mode == "Создание коллажей":
        st.info("Для режима коллажа будет отображен результирующий коллаж")

# --- Опциональное отображение коллажа ---
if show_results and st.session_state.selected_processing_mode == "Создание коллажей":
    coll_input_path = get_setting('paths.input_folder_path','')
    # Получаем БАЗОВОЕ имя файла из настроек
    coll_filename_base = get_setting('paths.output_filename','')
    if coll_input_path and coll_filename_base and os.path.isdir(coll_input_path):
        # Получаем ФОРМАТ из настроек коллажа
        coll_format = get_setting('collage_mode.output_format', 'jpg').lower()
        # Формируем ПОЛНОЕ имя файла с расширением
        base_name, _ = os.path.splitext(coll_filename_base)
        coll_filename_with_ext = f"{base_name}.{coll_format}"
        # Используем ПОЛНОЕ имя для проверки и отображения
        coll_full_path = os.path.abspath(os.path.join(coll_input_path, coll_filename_with_ext))
        log.debug(f"Checking for collage preview at: {coll_full_path}") # Добавим лог
        if os.path.isfile(coll_full_path):
            with st.expander("🖼️ Предпросмотр коллажа", expanded=True):
                try:
                    # Уникальный ключ не нужен для st.image в данном случае
                    st.image(coll_full_path, use_container_width=True)
                    log.debug(f"Displaying collage preview: {coll_full_path}")
                except Exception as img_e:
                    st.warning(f"Не удалось отобразить превью коллажа: {img_e}")
                    log.warning(f"Failed to display collage preview {coll_full_path}: {img_e}")
        else: log.debug(f"Collage file for preview not found: {coll_full_path}")
    else: log.debug("Input path or collage filename not set for preview.")

# --- Добавляем предпросмотр обработанных фотографий ---
elif show_results and st.session_state.selected_processing_mode == "Обработка отдельных файлов":
    output_path = get_setting('paths.output_folder_path','')
    if output_path and os.path.isdir(output_path):
        import glob
        # Получаем список файлов изображений
        image_files = glob.glob(os.path.join(output_path, "*.jpg")) + glob.glob(os.path.join(output_path, "*.jpeg")) + glob.glob(os.path.join(output_path, "*.png"))
        # Сортируем файлы по времени изменения (сначала новые)
        image_files.sort(key=os.path.getmtime, reverse=True)
        
        if image_files:
            with st.expander("🖼️ Предпросмотр обработанных фотографий", expanded=True):
                # Получаем настройки отображения
                columns_count = get_setting('ui_display.columns_count', 3)
                show_all_images = get_setting('ui_display.show_all_images', False)
                max_images = len(image_files) if show_all_images else get_setting('ui_display.images_limit', 18)
                
                # Ограничиваем количество изображений, если не show_all
                preview_images = image_files[:max_images]
                
                # Создаем строки по columns_count изображений
                for i in range(0, len(preview_images), columns_count):
                    row_images = preview_images[i:i+columns_count]
                    cols = st.columns(len(row_images))
                    
                    for j, img_path in enumerate(row_images):
                        try:
                            # Показываем имя файла и изображение
                            cols[j].caption(os.path.basename(img_path))
                            cols[j].image(img_path)
                        except Exception as img_e:
                            cols[j].warning(f"Не удалось отобразить {os.path.basename(img_path)}: {img_e}")
                            log.warning(f"Failed to display image preview {img_path}: {img_e}")
                
                if not show_all_images and len(image_files) > max_images:
                    st.caption(f"Показано {min(max_images, len(image_files))} из {len(image_files)} изображений")
        else:
            log.debug(f"No image files found in output directory: {output_path}")
    else: 
        log.debug("Output path not set or not found for individual files preview.")

log.info("--- End of app script render cycle ---")


# ... остальной код app.py ...

# === Функции для нормализации артикулей === 
def normalize_articles_ui():
    """
    UI для нормализации артикулей в интерфейсе Streamlit
    """
    st.subheader("Нормализация артикулей")
    
    # Получаем путь к исходной папке
    source_folder = get_setting('paths.source_folder', get_downloads_folder())
    
    # Проверяем существование папки
    if not os.path.exists(source_folder):
        st.warning(f"Папка источник не существует: {source_folder}")
        return
    
    # Опция переименования файлов
    rename_files = st.checkbox("Переименовать файлы", value=False, 
                             help="Если отмечено, файлы будут переименованы согласно нормализованным артикулям")
    
    if st.button("Нормализовать артикули"):
        with st.spinner("Анализируем файлы..."):
            try:
                # Получаем маппинг артикулей
                article_mapping = processing_workflows.normalize_articles_in_folder(source_folder)
                
                if not article_mapping:
                    st.warning("В указанной папке не найдено изображений для нормализации.")
                    return
                
                # Показываем предпросмотр результатов
                st.subheader("Результаты нормализации:")
                
                # Создаем таблицу результатов
                results_data = []
                for file_path, normalized_article in article_mapping.items():
                    file_name = os.path.basename(file_path)
                    results_data.append({"Исходное имя": file_name, "Нормализованный артикул": normalized_article})
                
                # Ограничиваем количество строк в таблице для производительности
                max_rows = 30
                if len(results_data) > max_rows:
                    st.info(f"Показаны первые {max_rows} из {len(results_data)} файлов")
                    results_data = results_data[:max_rows]
                
                # Отображаем таблицу
                st.table(results_data)
                
                # Находим основной артикул для обновления в настройках
                main_article = None
                for normalized in article_mapping.values():
                    if '_' not in normalized:
                        main_article = normalized
                        break
                        
                if not main_article and article_mapping:
                    # Берем первый артикул и удаляем индекс, если он есть
                    first_normalized = list(article_mapping.values())[0]
                    main_article = first_normalized.split('_')[0]
                
                if main_article:
                    st.success(f"Определен основной артикул: {main_article}")
                    
                    # Даем возможность применить его к настройкам
                    apply_to_settings = st.checkbox("Применить артикул в настройках", value=True)
                    
                    if apply_to_settings:
                        # Обновляем артикул в настройках
                        set_setting('individual_mode.article_name', main_article)
                        st.success(f"Артикул '{main_article}' успешно установлен в настройках")
                
                # Если выбрано переименование файлов
                if rename_files:
                    rename_confirm = st.checkbox("Подтверждаю переименование файлов", value=False)
                    
                    if rename_confirm:
                        with st.spinner("Переименовываем файлы..."):
                            success = processing_workflows.apply_normalized_articles(
                                source_folder, 
                                article_mapping, 
                                rename_files=True
                            )
                            
                            if success:
                                st.success("Файлы успешно переименованы!")
                            else:
                                st.error("Произошла ошибка при переименовании файлов.")
            
            except Exception as e:
                st.error(f"Ошибка при нормализации артикулей: {str(e)}")
                log.exception("Error in normalize_articles_ui")

# === Основной код приложения Streamlit ===