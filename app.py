# app.py

# --- БЛОК ПРОВЕРКИ И УСТАНОВКИ ЗАВИСИМОСТЕЙ ---
import sys
import subprocess
import importlib
import os
import time
import platform
print("="*50); print("--- Проверка и установка необходимых библиотек ---"); #... (весь блок)
# --- Инициализация installed_packages_info ---
installed_packages_info = []
for package_name in ["streamlit", "Pillow", "natsort"]:
    module_map = { "streamlit": "streamlit", "Pillow": "PIL", "natsort": "natsort" }
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

# === Основной код приложения Streamlit ===

# --- Загрузка/Инициализация Настроек ---
CONFIG_FILE = "settings.json" # Основной файл настроек (текущее состояние)

# === НАЧАЛО ЕДИНСТВЕННОГО БЛОКА ИНИЦИАЛИЗАЦИИ ===
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
def get_setting(key_path: str, default: Any = None) -> Any:
    keys = key_path.split('.')
    value = st.session_state.current_settings
    try:
        for key in keys: value = value[key]
        if isinstance(value, (list, dict)): return value.copy()
        return value
    except (KeyError, TypeError):
        # log.debug(f"Setting '{key_path}' not found, returning default: {default}") # Отключим для чистоты лога
        if isinstance(default, (list, dict)): return default.copy()
        return default

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

# === UI: Боковая Панель ===
with st.sidebar:
    st.header("🎯 Режим работы")
    processing_mode_options = ["Обработка отдельных файлов", "Создание коллажей"]
    current_mode_index = processing_mode_options.index(st.session_state.selected_processing_mode) \
                         if st.session_state.selected_processing_mode in processing_mode_options else 0
    processing_mode_selected = st.selectbox(
        "Выберите режим обработки:", processing_mode_options, index=current_mode_index, key="processing_mode_selector_widget"
    )
    if processing_mode_selected != st.session_state.selected_processing_mode:
         st.session_state.selected_processing_mode = processing_mode_selected
         set_setting('processing_mode_selector', processing_mode_selected)
         log.info(f"Processing mode changed to: {processing_mode_selected}")
         st.rerun()
    st.caption("Режим обработки")
    st.divider()

    # === Управление наборами ===
    st.header("📦 Управление наборами")
    available_presets = config_manager.get_available_presets()
    def get_default_preset_name_ui(): # Переименовали функцию
        existing_presets = config_manager.get_available_presets(); counter = 1
        while f"Набор {counter}" in existing_presets: counter += 1
        return f"Набор {counter}"
    preset_col1, preset_col2 = st.columns([4, 1])
    with preset_col1:
        selected_preset_in_box = st.selectbox( # Переименовали переменную
            "Активный набор:", available_presets,
            index=available_presets.index(st.session_state.active_preset) if st.session_state.active_preset in available_presets else 0,
            key="preset_selector", label_visibility="collapsed"
        )
    with preset_col2:
        can_delete = selected_preset_in_box != config_manager.DEFAULT_PRESET_NAME
        if st.button("🗑️", key="delete_preset_button", help=f"Удалить набор '{selected_preset_in_box}'" if can_delete else "Нельзя удалить набор по умолчанию", disabled=not can_delete):
            if config_manager.delete_settings_preset(selected_preset_in_box):
                st.toast(f"Набор '{selected_preset_in_box}' удален", icon="✅")
                if st.session_state.active_preset == selected_preset_in_box:
                    st.session_state.active_preset = config_manager.DEFAULT_PRESET_NAME
                    default_settings = config_manager.load_settings_preset(config_manager.DEFAULT_PRESET_NAME)
                    if default_settings:
                        st.session_state.current_settings = default_settings
                        st.session_state.selected_processing_mode = st.session_state.current_settings.get('processing_mode_selector', "Обработка отдельных файлов")
                        st.session_state.settings_changed = True
                st.rerun()
            else: st.error("Ошибка удаления набора")
    st.caption(f"Активный: **{st.session_state.active_preset}**")

    if selected_preset_in_box != st.session_state.active_preset:
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

    rename_col1, rename_col2 = st.columns([4, 1])
    with rename_col1:
        rename_disabled = st.session_state.active_preset == config_manager.DEFAULT_PRESET_NAME
        new_name_input = st.text_input( # Переименовали переменную
            "Новое имя для активного набора:", value=st.session_state.active_preset, key="rename_preset_input",
            disabled=rename_disabled, label_visibility="collapsed"
        )
    with rename_col2:
        if st.button("✏️", key="rename_preset_button", disabled=rename_disabled, help="Переименовать активный набор" if not rename_disabled else "Нельзя переименовать набор по умолчанию"):
            old_active_name = st.session_state.active_preset
            if config_manager.rename_settings_preset(old_active_name, new_name_input):
                st.session_state.active_preset = new_name_input
                st.toast(f"Набор '{old_active_name}' переименован в '{new_name_input}'", icon="✏️")
                st.rerun()
            else: st.error(f"Ошибка переименования (возможно, имя '{new_name_input}' занято?)")
    st.caption("Переименовать активный набор")

    create_col1, create_col2 = st.columns([4, 1])
    with create_col1:
        default_new_name = get_default_preset_name_ui()
        new_preset_name_input_val = st.text_input( # Переименовали переменную
            "Название нового набора:", key="new_preset_name_input", placeholder=default_new_name, label_visibility="collapsed"
        )
    with create_col2:
        if st.button("➕", key="create_preset_button", help="Создать новый набор с текущими настройками"):
            preset_name_to_save = new_preset_name_input_val if new_preset_name_input_val else default_new_name
            if config_manager.save_settings_preset(st.session_state.current_settings, preset_name_to_save):
                st.session_state.active_preset = preset_name_to_save
                st.toast(f"Создан и активирован новый набор '{preset_name_to_save}'", icon="✨")
                st.rerun()
            else: st.error(f"Ошибка создания набора '{preset_name_to_save}'")
    st.caption("Создать новый набор (из текущих настроек)")
    st.divider()

    # === Блок: Сохранить тек. настройки / Отменить изменения в наборе ===
    # (Перемещен из раздела "Управление настройками")
    settings_save_col, settings_reset_col_moved = st.columns(2) 
    with settings_save_col:
        if st.button("💾 Сохранить тек. настройки", key="save_main_settings_button", # Используем оригинальный ключ
                      help="Сохраняет текущие настройки интерфейса и имя активного пресета в главный файл settings.json.", 
                      use_container_width=True):
            # Логика сохранения 
            current_settings_to_save = st.session_state.current_settings.copy()
            current_settings_to_save["active_preset"] = st.session_state.active_preset
            current_settings_to_save["processing_mode_selector"] = st.session_state.selected_processing_mode
            save_main_ok = config_manager.save_settings(current_settings_to_save, CONFIG_FILE)
            if save_main_ok:
                log.info(f"Main settings manually saved to {CONFIG_FILE}.")
                st.toast("✅ Основные настройки сохранены.")
                st.session_state.settings_changed = False
            else:
                log.error(f"Failed to manually save main settings to {CONFIG_FILE}.")
                st.toast("❌ Ошибка сохранения основных настроек!")

    with settings_reset_col_moved:
        # === Используем новую функцию для disabled ===
        settings_differ = check_settings_differ_from_preset(st.session_state.active_preset)
        # Используем название "Отменить изменения" и логику сброса к значениям по умолчанию
        if st.button("🔄 Отменить изменения", key="confirm_reset_active_preset_button", # Этот ключ инициирует подтверждение ниже
                      help=f"Сбросить текущие настройки к значениям по умолчанию.", 
                      use_container_width=True, 
                      disabled=not settings_differ): # Отключаем, если настройки НЕ отличаются от пресета? Или всегда вкл? Оставим так.
        # ============================================
             # Инициируем подтверждение сброса к значениям ПО УМОЛЧАНИЮ
             st.session_state.reset_active_preset_confirmation_pending = True 
             st.session_state.reset_settings_confirmation_pending = False 
             st.session_state.reset_profiles_confirmation_pending = False 
             st.rerun()
    # =====================================================

    # === СУЩЕСТВУЮЩИЙ БЛОК КНОПОК СБРОСА ПРОФИЛЕЙ/К ЗАВОДСКИМ ===
    reset_profiles_col, reset_settings_col = st.columns(2)
    with reset_profiles_col:
        if st.button("🗑️ Сбросить профили", key="reset_all_profiles_button", disabled=st.session_state.reset_profiles_confirmation_pending, help="Удалить все пользовательские профили"):
            st.session_state.reset_profiles_confirmation_pending = True; st.session_state.reset_settings_confirmation_pending = False; st.rerun()

    with reset_settings_col:
        if st.button("💥 Сбросить все к заводским", key="reset_all_settings_button", 
                      disabled=st.session_state.reset_settings_confirmation_pending, 
                      help="Полностью сбросить все настройки к первоначальному состоянию программы."):
            st.session_state.reset_settings_confirmation_pending = True; st.session_state.reset_profiles_confirmation_pending = False; st.rerun()
    st.divider()

    # --- Логика подтверждений (остается на своем месте) ---
    # === Логика подтверждения для ОТМЕНЫ ИЗМЕНЕНИЙ (СБРОС К УМОЛЧАНИЯМ) ===
    if st.session_state.reset_active_preset_confirmation_pending:
        # Меняем текст предупреждения
        st.warning(f"Сбросить текущие настройки к значениям по умолчанию (независимо от набора '{st.session_state.active_preset}')?", icon="🔄")
        active_preset_confirm_col1, active_preset_confirm_col2 = st.columns(2)
        with active_preset_confirm_col1:
            # Ключ остается прежним, чтобы логика флага работала
            if st.button("Да, сбросить к умолчаниям", key="confirm_reset_active_preset", type="primary"):
                # Загружаем ДЕФОЛТНЫЕ настройки
                default_settings = config_manager.get_default_settings()
                # Применяем их
                st.session_state.current_settings = default_settings.copy()
                # Режим обработки тоже берем из дефолтных
                st.session_state.selected_processing_mode = st.session_state.current_settings.get('processing_mode_selector', "Обработка отдельных файлов")
                st.session_state.settings_changed = False # Считаем, что отличий от дефолта нет
                # Меняем сообщение
                st.toast(f"Настройки сброшены к значениям по умолчанию.", icon="🔄")
                st.session_state.reset_active_preset_confirmation_pending = False
                st.rerun()
        with active_preset_confirm_col2:
            # Кнопка отмены остается прежней
            if st.button("Отмена", key="cancel_reset_active_preset"): 
                st.session_state.reset_active_preset_confirmation_pending = False
                st.rerun()
    # =========================================================
    # === Логика подтверждения для СБРОСА ПРОФИЛЕЙ ===
    if st.session_state.reset_profiles_confirmation_pending:
        st.warning("Удалить ВСЕ пользовательские профили?", icon="⚠️")
        prof_confirm_col1, prof_confirm_col2 = st.columns(2)
        with prof_confirm_col1:
            if st.button("Да, удалить профили", key="confirm_reset_profiles", type="primary"):
                deleted_count = config_manager.delete_all_custom_presets()
                if deleted_count is not None:
                     st.toast(f"Удалено профилей: {deleted_count}.", icon="🗑️")
                     if st.session_state.active_preset != config_manager.DEFAULT_PRESET_NAME:
                         st.session_state.active_preset = config_manager.DEFAULT_PRESET_NAME
                         default_settings = config_manager.load_settings_preset(config_manager.DEFAULT_PRESET_NAME)
                         if default_settings:
                             st.session_state.current_settings = default_settings
                             st.session_state.selected_processing_mode = st.session_state.current_settings.get('processing_mode_selector', "Обработка отдельных файлов")
                             st.session_state.settings_changed = True
                else: st.error("Ошибка при удалении профилей.")
                st.session_state.reset_profiles_confirmation_pending = False; st.rerun()
        with prof_confirm_col2:
             if st.button("Отмена ", key="cancel_reset_profiles"): # Добавил пробел для уникальности ключа
                st.session_state.reset_profiles_confirmation_pending = False; st.rerun()
    # =================================================
    # === Логика подтверждения для СБРОСА К ЗАВОДСКИМ ===
    if st.session_state.reset_settings_confirmation_pending:
        st.warning("Вы уверены, что хотите сбросить ВСЕ настройки к заводским? Это действие необратимо!", icon="💥")
        settings_confirm_col1, settings_confirm_col2 = st.columns(2)
        with settings_confirm_col1:
            # Текст кнопки подтверждения
            if st.button("Да, сбросить к заводским", key="confirm_reset_settings", type="primary"):
                # === Загружаем САМЫЕ ДЕФОЛТНЫЕ настройки ===
                hard_default_settings = config_manager.get_default_settings()
                st.session_state.current_settings = hard_default_settings.copy() # Копируем для безопасности
                st.session_state.active_preset = config_manager.DEFAULT_PRESET_NAME # Активным делаем "По умолчанию"
                st.session_state.selected_processing_mode = st.session_state.current_settings.get('processing_mode_selector', "Обработка отдельных файлов")
                st.session_state.settings_changed = True # Отмечаем изменения для UI
                # === ДОБАВЛЕНО: Удаление всех кастомных пресетов ===
                log.info("Reset to factory: Attempting to delete all custom presets...")
                deleted_count = config_manager.delete_all_custom_presets()
                if deleted_count is not None:
                    log.info(f"Reset to factory: Deleted {deleted_count} custom preset(s).")
                    st.toast(f"Настройки сброшены! Удалено кастомных профилей: {deleted_count}.", icon="💥")
                else:
                    log.error("Reset to factory: Error occurred while deleting custom presets.")
                    st.toast("Настройки сброшены, но произошла ошибка при удалении профилей!", icon="⚠️")
                # =================================================
                st.session_state.reset_settings_confirmation_pending = False; st.rerun()
        with settings_confirm_col2:
            if st.button("Отмена ", key="cancel_reset_settings"): # Ключ может остаться
                st.session_state.reset_settings_confirmation_pending = False; st.rerun()
    st.divider() # Конец последнего блока логики подтверждения

    # === Пути ===
    st.header("📂 Пути")
    # --- Получаем путь к Загрузкам ОДИН РАЗ --- 
    user_downloads_folder = get_downloads_folder()
    log.debug(f"Resolved Downloads Folder: {user_downloads_folder}")

    # --- Input Path --- 
    current_input_path = get_setting('paths.input_folder_path')
    # Подставляем Загрузки, если путь пустой
    input_path_default_value = current_input_path if current_input_path else user_downloads_folder 
    input_path_val = st.text_input(
        "Папка с исходными файлами:", 
        value=input_path_default_value,
        key='path_input_sidebar',
        help="Укажите папку, где лежат изображения для обработки."
    )
    # Сохраняем новое значение, только если оно отличается от того, что было (или было пустым)
    if input_path_val != current_input_path:
        set_setting('paths.input_folder_path', input_path_val)
    
    # Отображение статуса папки
    if input_path_val and os.path.isdir(input_path_val): st.caption(f"✅ Папка найдена: {os.path.abspath(input_path_val)}")
    elif input_path_val: st.caption(f"❌ Папка не найдена: {os.path.abspath(input_path_val)}")
    else: st.caption("ℹ️ Путь не указан.")

    current_mode_local = st.session_state.selected_processing_mode
    if current_mode_local == "Обработка отдельных файлов":
        st.subheader("Пути (Обработка файлов)")
        # --- Output Path --- 
        current_output_path = get_setting('paths.output_folder_path')
        # Подставляем Загрузки/Processed, если путь пустой
        output_path_default_value = current_output_path if current_output_path else os.path.join(user_downloads_folder, "Processed")
        output_path_val = st.text_input(
            "Папка для результатов:", 
            value=output_path_default_value,
            key='path_output_ind_sidebar', 
            help="Куда сохранять обработанные файлы."
        )
        if output_path_val != current_output_path:
            set_setting('paths.output_folder_path', output_path_val)
        if output_path_val: st.caption(f"Сохранять в: {os.path.abspath(output_path_val)}")
        
        # --- Backup Path (ИЗМЕНЕНО ПОВЕДЕНИЕ) ---
        current_backup_path = get_setting('paths.backup_folder_path')
        # Подставляем Загрузки/Backups, если путь пустой
        backup_path_default_value = current_backup_path if current_backup_path else os.path.join(user_downloads_folder, "Backups")
        backup_path_val = st.text_input(
            "Папка для бэкапов:", 
            # === ТЕПЕРЬ ВСЕГДА ПОКАЗЫВАЕМ ЗНАЧЕНИЕ (ТЕКУЩЕЕ ИЛИ ПО УМОЛЧАНИЮ) ===
            value=backup_path_default_value, 
            key='path_backup_ind_sidebar',
            placeholder="Оставьте пустым чтобы отключить", # Placeholder теперь проще
            help="Куда копировать оригиналы перед обработкой. Если оставить пустым, бэкап будет отключен."
        )
        # Сохраняем новое значение (может быть пустым, если пользователь стер)
        if backup_path_val != current_backup_path:
             set_setting('paths.backup_folder_path', backup_path_val)
        # Отображаем статус
        if backup_path_val:
            # Проверяем, отличается ли от стандартного умолчания, чтобы не писать лишнее
            is_default_shown = not current_backup_path and backup_path_val == os.path.join(user_downloads_folder, "Backups")
            st.caption(f"Бэкап в: {os.path.abspath(backup_path_val)}" + (" (по умолчанию)" if is_default_shown else ""))
        else: 
            st.caption(f"Бэкап отключен.")
        
    elif current_mode_local == "Создание коллажей":
        st.subheader("Пути (Создание коллажа)")
        # --- Имя файла коллажа (остается как было, по умолчанию 'collage') ---
        collage_filename_val = st.text_input(
            "Имя файла коллажа (без расш.):", 
            value=get_setting('paths.output_filename', 'collage'), 
            key='path_output_coll_sidebar',
            help="Базовое имя файла (без .jpg/.png), который будет создан в папке с исходными файлами."
        )
        set_setting('paths.output_filename', collage_filename_val)
        if collage_filename_val: st.caption(f"Имя файла: {collage_filename_val}.[расширение]")

    # --- Кнопка сброса путей --- 
    if st.button("🔄 Сбросить пути по умолчанию", key="reset_paths_button", help="Установить стандартные пути (Загрузки)"):
        # При сбросе устанавливаем пустые строки, чтобы при следующем рендере подставились Загрузки
        set_setting('paths.input_folder_path', "")
        set_setting('paths.output_folder_path', "")
        set_setting('paths.backup_folder_path', "")
        set_setting('paths.output_filename', "collage") # Сбрасываем имя коллажа
        st.toast("Пути сброшены! При следующем обновлении подставятся Загрузки.", icon="🔄"); 
        st.rerun()
    st.divider()

    # === Остальные Настройки ===
    st.header("⚙️ Общие настройки обработки")
    with st.expander("1. Предварительный ресайз", expanded=False):
        enable_preresize = st.checkbox("Включить", value=get_setting('preprocessing.enable_preresize', False), key='pre_enable')
        set_setting('preprocessing.enable_preresize', enable_preresize)
        if enable_preresize:
            col_pre1, col_pre2 = st.columns(2)
            with col_pre1:
                 pr_w = st.number_input("Макс. Ширина (px)", 0, 10000, value=get_setting('preprocessing.preresize_width', 2500), step=10, key='pre_w')
                 set_setting('preprocessing.preresize_width', pr_w)
            with col_pre2:
                 pr_h = st.number_input("Макс. Высота (px)", 0, 10000, value=get_setting('preprocessing.preresize_height', 2500), step=10, key='pre_h')
                 set_setting('preprocessing.preresize_height', pr_h)

    with st.expander("2. Отбеливание периметра", expanded=False):
        # === РАСКОММЕНТИРОВАНО ===
        enable_whitening = st.checkbox("Включить ", value=get_setting('whitening.enable_whitening', False), key='white_enable') # Пробел в лейбле для уникальности
        set_setting('whitening.enable_whitening', enable_whitening)
        if enable_whitening:
             # === ИСПРАВЛЕН ДИАПАЗОН ===
             wc_thr = st.slider("Порог отмены (сумма RGB)", 0, 765, 
                                value=get_setting('whitening.cancel_threshold_sum', 5), 
                                step=5, # Увеличим шаг для удобства
                                key='white_thr', 
                                help="Отбеливание не будет применяться, если самый темный пиксель на границе светлее этого значения (0-765).")
             # ==========================
             set_setting('whitening.cancel_threshold_sum', wc_thr)
        # =========================

    with st.expander("3. Удаление фона и обрезка", expanded=False):
        # === РАСКОММЕНТИРОВАНО ===
        enable_bg_crop = st.checkbox("Включить ", value=get_setting('background_crop.enable_bg_crop', False), key='bgc_enable') # Пробел в лейбле
        set_setting('background_crop.enable_bg_crop', enable_bg_crop)
        if enable_bg_crop:
            bgc_tol = st.slider("Допуск белого фона", 0, 255, value=get_setting('background_crop.white_tolerance', 10), key='bgc_tol', help="Насколько цвет может отличаться от чисто белого, чтобы считаться фоном.")
            set_setting('background_crop.white_tolerance', bgc_tol)
            bgc_per = st.checkbox("Проверять периметр", value=get_setting('background_crop.check_perimeter', True), key='bgc_perimeter', help="Обрезать только если фон доходит до краев изображения.")
            set_setting('background_crop.check_perimeter', bgc_per)
            bgc_abs = st.checkbox("Абсолютно симм. обрезка", value=get_setting('background_crop.crop_symmetric_absolute', False), key='bgc_abs')
            set_setting('background_crop.crop_symmetric_absolute', bgc_abs)
            if not bgc_abs:
                bgc_axes = st.checkbox("Симм. обрезка по осям", value=get_setting('background_crop.crop_symmetric_axes', False), key='bgc_axes')
                set_setting('background_crop.crop_symmetric_axes', bgc_axes)
            else:
                 if get_setting('background_crop.crop_symmetric_axes', False):
                     set_setting('background_crop.crop_symmetric_axes', False)
        # =========================

    with st.expander("4. Добавление полей", expanded=False):
        # === НОВЫЕ РЕЖИМЫ ===
        padding_mode_options = {
            "never": "Никогда не добавлять поля",
            "always": "Добавлять поля всегда",
            "if_white": "Добавлять поля, если периметр белый",
            "if_not_white": "Добавлять поля, если периметр НЕ белый"
        }
        padding_mode_keys = list(padding_mode_options.keys())
        padding_mode_values = list(padding_mode_options.values())
        
        current_padding_mode = get_setting('padding.mode', 'never')
        try:
            current_padding_mode_index = padding_mode_keys.index(current_padding_mode)
        except ValueError:
            current_padding_mode_index = 0 # Fallback to 'never'
            set_setting('padding.mode', 'never') # Correct invalid setting

        selected_padding_mode_value = st.radio(
            "Когда добавлять поля:",
            options=padding_mode_values,
            index=current_padding_mode_index,
            key='pad_mode_radio',
            horizontal=False, # Вертикальное расположение для читаемости
        )
        selected_padding_mode_key = padding_mode_keys[padding_mode_values.index(selected_padding_mode_value)]
        
        # Сохраняем выбранный режим
        if selected_padding_mode_key != current_padding_mode:
            set_setting('padding.mode', selected_padding_mode_key)
            # st.rerun() # Может не потребоваться, т.к. виджеты ниже зависят от current_padding_mode

        # === УСЛОВНЫЕ НАСТРОЙКИ ===
        check_perimeter_selected = selected_padding_mode_key in ['if_white', 'if_not_white']
        add_padding_selected = selected_padding_mode_key != 'never'

        if check_perimeter_selected:
            st.caption("Настройки проверки периметра:")
            pad_m = st.number_input("Толщина проверки периметра (px)", 1, 100, # Минимальное значение 1, если проверяем
                                   value=get_setting('padding.perimeter_margin', 5), 
                                   step=1, key='pad_margin_conditional',
                                   help="Ширина зоны у края изображения для проверки цвета (мин. 1).")
            set_setting('padding.perimeter_margin', pad_m)

            # === НОВЫЙ НЕЗАВИСИМЫЙ ДОПУСК БЕЛОГО ===
            pad_tol = st.slider("Допуск белого для проверки периметра", 0, 255, 
                                 value=get_setting('padding.perimeter_check_tolerance', 10), 
                                 key='pad_tolerance_conditional', 
                                 help="Насколько цвет пикселя на периметре может отличаться от белого (RGB 255,255,255), чтобы считаться белым для этой проверки.")
            set_setting('padding.perimeter_check_tolerance', pad_tol)
            # =======================================
        else:
             # Сбрасываем значения, когда проверка не нужна? Или оставляем? Оставим пока.
             # if get_setting('padding.perimeter_margin', 0) != 0: set_setting('padding.perimeter_margin', 0)
             pass

        if add_padding_selected:
            st.caption("Общие настройки полей:")
            pad_p = st.slider("Процент полей", 0.0, 50.0, 
                              value=get_setting('padding.padding_percent', 5.0), 
                              step=0.5, key='pad_perc_conditional', format="%.1f%%",
                              help="Размер добавляемых полей относительно большей стороны изображения.")
            set_setting('padding.padding_percent', pad_p)

            pad_exp = st.checkbox("Разрешить полям расширять холст", 
                                  value=get_setting('padding.allow_expansion', True), 
                                  key='pad_expand_conditional', 
                                  help="Если отключено, поля будут добавлены только если финальный размер изображения позволяет.")
            set_setting('padding.allow_expansion', pad_exp)

        # Удаляем старые ненужные виджеты (enable_padding и старый perimeter_margin)
        # Они заменены на radio и условные number_input/slider

    # === НОВЫЙ ЭКСПАНДЕР ===
    with st.expander("5. Яркость и Контраст", expanded=False):
        enable_bc = st.checkbox("Включить", value=get_setting('brightness_contrast.enable_bc', False), key='bc_enable')
        set_setting('brightness_contrast.enable_bc', enable_bc)
        if enable_bc:
            brightness_factor = st.slider("Яркость", 0.1, 3.0, 
                                          value=get_setting('brightness_contrast.brightness_factor', 1.0), 
                                          step=0.05, key='bc_brightness', format="%.2f",
                                          help="Меньше 1.0 - темнее, больше 1.0 - светлее.")
            set_setting('brightness_contrast.brightness_factor', brightness_factor)
            
            contrast_factor = st.slider("Контраст", 0.1, 3.0, 
                                        value=get_setting('brightness_contrast.contrast_factor', 1.0), 
                                        step=0.05, key='bc_contrast', format="%.2f",
                                        help="Меньше 1.0 - меньше контраста, больше 1.0 - больше контраста.")
            set_setting('brightness_contrast.contrast_factor', contrast_factor)
    # ========================

    # Настройки, зависящие от режима
    st.divider()
    current_mode_local_for_settings = st.session_state.selected_processing_mode

    if current_mode_local_for_settings == "Обработка отдельных файлов":
        # === ВОЗВРАЩАЕМ HEADER ===
        st.header("⚙️ Настройки обработки файлов") 
        # === ЭКСПАНДЕР 1 (теперь не вложенный) ===
        with st.expander("Финальный размер и формат", expanded=True):
            # --- Соотношение сторон --- 
            enable_ratio_ind = st.checkbox("Принудительное соотношение сторон", 
                                           value=get_setting('individual_mode.enable_force_aspect_ratio', False),
                                           key='ind_enable_ratio')
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

                with col_r1: ratio_w_ind = st.number_input("W", 0.1, 100.0, value=default_w_ind, step=0.1, key='ind_ratio_w', format="%.1f", label_visibility="collapsed")
                with col_r2: ratio_h_ind = st.number_input("H", 0.1, 100.0, value=default_h_ind, step=0.1, key='ind_ratio_h', format="%.1f", label_visibility="collapsed")
                # Сохраняем, только если оба > 0
                if ratio_w_ind > 0 and ratio_h_ind > 0:
                     # Сохраняем только если значение изменилось 
                     if [ratio_w_ind, ratio_h_ind] != get_setting('individual_mode.force_aspect_ratio'):
                          set_setting('individual_mode.force_aspect_ratio', [ratio_w_ind, ratio_h_ind])
                else: st.warning("Соотношение должно быть больше 0") # Валидация
            
            # --- Максимальный размер --- 
            enable_max_dim_ind = st.checkbox("Максимальный размер",
                                             value=get_setting('individual_mode.enable_max_dimensions', False),
                                             key='ind_enable_maxdim')
            set_setting('individual_mode.enable_max_dimensions', enable_max_dim_ind)
            if enable_max_dim_ind:
                st.caption("Макс. размер (ШxВ, px)")
                col_m1, col_m2 = st.columns(2)
                with col_m1: max_w_ind = st.number_input("Ш", 1, 10000, value=get_setting('individual_mode.max_output_width', 1500), step=50, key='ind_max_w', label_visibility="collapsed"); set_setting('individual_mode.max_output_width', max_w_ind)
                with col_m2: max_h_ind = st.number_input("В", 1, 10000, value=get_setting('individual_mode.max_output_height', 1500), step=50, key='ind_max_h', label_visibility="collapsed"); set_setting('individual_mode.max_output_height', max_h_ind)

            # --- Точный холст --- 
            enable_exact_ind = st.checkbox("Точный холст", 
                                           value=get_setting('individual_mode.enable_exact_canvas', False),
                                           key='ind_enable_exact')
            set_setting('individual_mode.enable_exact_canvas', enable_exact_ind)
            if enable_exact_ind:
                st.caption("Точный холст (ШxВ, px)")
                col_e1, col_e2 = st.columns(2)
                with col_e1: exact_w_ind = st.number_input("Ш", 1, 10000, value=get_setting('individual_mode.final_exact_width', 1000), step=50, key='ind_exact_w', label_visibility="collapsed"); set_setting('individual_mode.final_exact_width', exact_w_ind)
                with col_e2: exact_h_ind = st.number_input("В", 1, 10000, value=get_setting('individual_mode.final_exact_height', 1000), step=50, key='ind_exact_h', label_visibility="collapsed"); set_setting('individual_mode.final_exact_height', exact_h_ind)

            # --- Параметры вывода (без изменений) ---
            st.caption("Параметры вывода")
            fmt_col, q_col, bg_col = st.columns([1,1,2])
            with fmt_col:
                 output_format_ind = st.selectbox("Формат", ["jpg", "png"], index=["jpg", "png"].index(get_setting('individual_mode.output_format', 'jpg')), key='ind_format')
                 set_setting('individual_mode.output_format', output_format_ind)
            with q_col:
                 if output_format_ind == 'jpg': q_ind = st.number_input("Кач-во", 1, 100, value=get_setting('individual_mode.jpeg_quality', 95), key='ind_quality'); set_setting('individual_mode.jpeg_quality', q_ind)
                 else: st.caption("-")
            with bg_col:
                 if output_format_ind == 'jpg':
                     bg_color_str_ind = ",".join(map(str, get_setting('individual_mode.jpg_background_color', [255,255,255])))
                     new_bg_color_str_ind = st.text_input("Фон (R,G,B)", value=bg_color_str_ind, key='ind_bg')
                     try:
                         new_bg_color_ind = list(map(int, new_bg_color_str_ind.split(',')))
                         if len(new_bg_color_ind) == 3 and all(0 <= c <= 255 for c in new_bg_color_ind):
                             if new_bg_color_ind != get_setting('individual_mode.jpg_background_color', [255,255,255]): set_setting('individual_mode.jpg_background_color', new_bg_color_ind)
                         else: st.caption("❌ R,G,B 0-255")
                     except ValueError: st.caption("❌ R,G,B 0-255")
                 else: st.caption("-")
        # === КОНЕЦ ЭКСПАНДЕРА 1 ===
        
        # === ЭКСПАНДЕР 2 (теперь не вложенный) ===
        with st.expander("Переименование и удаление", expanded=False):
            # --- Переименование --- 
            enable_rename_ind = st.checkbox("Переименовать файлы (по артикулу)",
                                            value=get_setting('individual_mode.enable_rename', False),
                                            key='ind_enable_rename')
            set_setting('individual_mode.enable_rename', enable_rename_ind)
            if enable_rename_ind:
                article_ind = st.text_input("Артикул для переименования",
                                            value=get_setting('individual_mode.article_name', ''),
                                            key='ind_article',
                                            placeholder="Введите артикул...")
                set_setting('individual_mode.article_name', article_ind)
                if article_ind: st.caption("Файлы будут вида: [Артикул]_1.jpg, ...")
                else: st.warning("Введите артикул для переименования.") # Валидация
            
            # --- Удаление (без изменений) ---
            delete_orig_ind = st.checkbox("Удалять оригиналы после обработки?",
                                          value=get_setting('individual_mode.delete_originals', False),
                                          key='ind_delete_orig')
            set_setting('individual_mode.delete_originals', delete_orig_ind)
            if delete_orig_ind: st.warning("ВНИМАНИЕ: Удаление необратимо!", icon="⚠️")
        # === КОНЕЦ ЭКСПАНДЕРА 2 ===
        # === КОНЕЦ УДАЛЕННОГО ОБЩЕГО ЭКСПАНДЕРА ===

    elif current_mode_local_for_settings == "Создание коллажей":
        st.header("🖼️ Настройки коллажа")
        with st.expander("Размер и формат коллажа", expanded=True):
            # --- Соотношение сторон --- 
            enable_ratio_coll = st.checkbox("Принудительное соотношение сторон коллажа", 
                                              value=get_setting('collage_mode.enable_force_aspect_ratio', False),
                                              key='coll_enable_ratio')
            set_setting('collage_mode.enable_force_aspect_ratio', enable_ratio_coll)
            if enable_ratio_coll:
                st.caption("Соотношение (W:H)")
                col_r1_coll, col_r2_coll = st.columns(2)
                current_ratio_coll_val = get_setting('collage_mode.force_collage_aspect_ratio', [16.0, 9.0])
                default_w_coll = float(current_ratio_coll_val[0])
                default_h_coll = float(current_ratio_coll_val[1])
                with col_r1_coll: ratio_w_coll = st.number_input("W", 0.1, 100.0, value=default_w_coll, step=0.1, key='coll_ratio_w', format="%.1f", label_visibility="collapsed")
                with col_r2_coll: ratio_h_coll = st.number_input("H", 0.1, 100.0, value=default_h_coll, step=0.1, key='coll_ratio_h', format="%.1f", label_visibility="collapsed")
                if ratio_w_coll > 0 and ratio_h_coll > 0: set_setting('collage_mode.force_collage_aspect_ratio', [ratio_w_coll, ratio_h_coll])
                else: st.warning("Соотношение должно быть больше 0")

            # --- Максимальный размер --- 
            enable_max_dim_coll = st.checkbox("Максимальный размер коллажа",
                                                value=get_setting('collage_mode.enable_max_dimensions', False),
                                                key='coll_enable_maxdim')
            set_setting('collage_mode.enable_max_dimensions', enable_max_dim_coll)
            if enable_max_dim_coll:
                st.caption("Макс. размер (ШxВ, px)")
                col_m1_coll, col_m2_coll = st.columns(2)
                with col_m1_coll: max_w_coll = st.number_input("Ш", 1, 10000, value=get_setting('collage_mode.max_collage_width', 1500), step=50, key='coll_max_w', label_visibility="collapsed"); set_setting('collage_mode.max_collage_width', max_w_coll)
                with col_m2_coll: max_h_coll = st.number_input("В", 1, 10000, value=get_setting('collage_mode.max_collage_height', 1500), step=50, key='coll_max_h', label_visibility="collapsed"); set_setting('collage_mode.max_collage_height', max_h_coll)

            # --- Точный холст --- 
            enable_exact_coll = st.checkbox("Точный холст коллажа", 
                                              value=get_setting('collage_mode.enable_exact_canvas', False),
                                              key='coll_enable_exact')
            set_setting('collage_mode.enable_exact_canvas', enable_exact_coll)
            if enable_exact_coll:
                st.caption("Точный холст (ШxВ, px)")
                col_e1_coll, col_e2_coll = st.columns(2)
                with col_e1_coll: exact_w_coll = st.number_input("Ш", 1, 10000, value=get_setting('collage_mode.final_collage_exact_width', 1920), step=50, key='coll_exact_w', label_visibility="collapsed"); set_setting('collage_mode.final_collage_exact_width', exact_w_coll)
                with col_e2_coll: exact_h_coll = st.number_input("В", 1, 10000, value=get_setting('collage_mode.final_collage_exact_height', 1080), step=50, key='coll_exact_h', label_visibility="collapsed"); set_setting('collage_mode.final_collage_exact_height', exact_h_coll)

            # --- Параметры вывода (без изменений) ---
            st.caption("Параметры вывода коллажа")
            fmt_col_coll, q_col_coll, bg_col_coll = st.columns([1,1,2])
            with fmt_col_coll:
                 output_format_coll = st.selectbox("Формат", ["jpg", "png"], index=["jpg", "png"].index(get_setting('collage_mode.output_format', 'jpg')), key='coll_format')
                 set_setting('collage_mode.output_format', output_format_coll)
            with q_col_coll:
                 if output_format_coll == 'jpg': q_coll = st.number_input("Кач-во", 1, 100, value=get_setting('collage_mode.jpeg_quality', 95), key='coll_quality'); set_setting('collage_mode.jpeg_quality', q_coll)
                 else: st.caption("-")
            with bg_col_coll:
                 if output_format_coll == 'jpg':
                     bg_color_str_coll = ",".join(map(str, get_setting('collage_mode.jpg_background_color', [255,255,255])))
                     new_bg_color_str_coll = st.text_input("Фон (R,G,B)", value=bg_color_str_coll, key='coll_bg')
                     try:
                         new_bg_color_coll = list(map(int, new_bg_color_str_coll.split(',')))
                         if len(new_bg_color_coll) == 3 and all(0 <= c <= 255 for c in new_bg_color_coll):
                            if new_bg_color_coll != get_setting('collage_mode.jpg_background_color', [255,255,255]): set_setting('collage_mode.jpg_background_color', new_bg_color_coll)
                         else: st.caption("❌ R,G,B 0-255")
                     except ValueError: st.caption("❌ R,G,B 0-255")
                 else: st.caption("-")

        with st.expander("Параметры сетки коллажа", expanded=False):
            # --- Кол-во столбцов ---
            enable_cols_coll = st.checkbox("Задать кол-во столбцов",
                                             value=get_setting('collage_mode.enable_forced_cols', False),
                                             key='coll_enable_cols')
            set_setting('collage_mode.enable_forced_cols', enable_cols_coll)
            if enable_cols_coll:
                 cols_coll = st.number_input("Столбцов", 1, 20, value=get_setting('collage_mode.forced_cols', 3), step=1, key='coll_cols')
                 set_setting('collage_mode.forced_cols', cols_coll)
            else: 
                 st.caption("Кол-во столбцов: Авто")
                 # Сбросим значение, если галка снята?
                 # if get_setting('collage_mode.forced_cols', 3) != 0:
                 #     set_setting('collage_mode.forced_cols', 0) 
            
            # --- Отступ (без изменений) ---
            spc_coll = st.slider("Отступ между фото (%)", 0.0, 20.0, value=get_setting('collage_mode.spacing_percent', 2.0), step=0.5, key='coll_spacing', format="%.1f%%")
            set_setting('collage_mode.spacing_percent', spc_coll)

        # === ВОССТАНОВЛЕННЫЕ НАСТРОЙКИ ПРОПОРЦИОНАЛЬНОСТИ ===
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
        # ====================================================

    # === ЗАКОММЕНТИРОВАН ДУБЛИРУЮЩИЙ БЛОК УПРАВЛЕНИЯ НАСТРОЙКАМИ ===
    # st.subheader("💾 Управление настройками") 
    # settings_save_col_dup, settings_reset_col_dup = st.columns(2)
    # with settings_save_col_dup:
    #     if st.button("💾 Сохранить текущие настройки", key="save_main_settings_button_dup", # <-- Использовал бы другой ключ, если бы не был закомментирован
    #                   help="Сохраняет текущие настройки интерфейса и имя активного пресета в главный файл settings.json.", 
    #                   use_container_width=True):
    #         current_settings_to_save = st.session_state.current_settings.copy()
    #         current_settings_to_save["active_preset"] = st.session_state.active_preset
    #         current_settings_to_save["processing_mode_selector"] = st.session_state.selected_processing_mode
    #         save_main_ok = config_manager.save_settings(current_settings_to_save, CONFIG_FILE)
    #         if save_main_ok:
    #             log.info(f"Main settings manually saved to {CONFIG_FILE}.")
    #             st.toast("✅ Основные настройки сохранены.")
    #             st.session_state.settings_changed = False
    #         else:
    #             log.error(f"Failed to manually save main settings to {CONFIG_FILE}.")
    #             st.toast("❌ Ошибка сохранения основных настроек!")
    # with settings_reset_col_dup:
    #     if st.button("🔄 Сбросить текущие настройки", key="confirm_reset_settings_button_dup", # <-- Использовал бы другой ключ
    #                   help="Восстанавливает настройки до значений активного пресета.", 
    #                   use_container_width=True):
    #          st.session_state.reset_active_preset_confirmation_pending = True 
    #          st.session_state.reset_settings_confirmation_pending = False 
    #          st.session_state.reset_profiles_confirmation_pending = False 
    #          st.rerun()
    # =============================================================

    # === ЗАКОММЕНТИРОВАН ДУБЛИРУЮЩИЙ БЛОК ЛОГИКИ ПОДТВЕРЖДЕНИЯ ===
    # st.subheader("📊 Пресеты настроек") # Этот заголовок, возможно, тоже лишний здесь
    # if st.session_state.reset_active_preset_confirmation_pending:
    #     # ... (вся логика подтверждения)...
    # =========================================================

# === Конец блока with st.sidebar ===

# === ОСНОВНАЯ ОБЛАСТЬ ===
st.title(f"🖼️ Инструмент Обработки Изображений")
st.markdown(f"**Режим:** {st.session_state.selected_processing_mode} | **Активный набор:** {st.session_state.active_preset}")
st.divider()

# --- Кнопка Запуска ---
col_run_main, col_spacer_main = st.columns([3, 1])
start_button_pressed_this_run = False

with col_run_main:
    if st.button(f"🚀 Запустить: {st.session_state.selected_processing_mode}", type="primary", key="run_processing_button", use_container_width=True):
        start_button_pressed_this_run = True
        log.info(f"--- Button '{st.session_state.selected_processing_mode}' CLICKED! Processing will start below. ---")
        log_stream.seek(0); log_stream.truncate(0) # Очищаем лог для нового запуска
        log.info(f"--- Log cleared. Validating paths for mode '{st.session_state.selected_processing_mode}' ---")

# --- Логика Запуска ---
if start_button_pressed_this_run:
    log.info(f"--- Start button was pressed this run. Starting validation... ---")
    paths_ok = True
    validation_errors = []
    input_path = get_setting('paths.input_folder_path', '')
    abs_input_path = os.path.abspath(input_path) if input_path else ''
    if not input_path or not os.path.isdir(abs_input_path):
        validation_errors.append(f"Папка с исходными файлами не найдена или не указана: '{input_path}'")
        paths_ok = False

    current_mode = st.session_state.selected_processing_mode # Используем из state
    if current_mode == "Обработка отдельных файлов":
        output_path_ind = get_setting('paths.output_folder_path', '')
        if not output_path_ind: validation_errors.append("Не указана папка для результатов!"); paths_ok = False
        if paths_ok and get_setting('individual_mode.delete_originals') and input_path and output_path_ind:
             if os.path.normcase(os.path.abspath(input_path)) == os.path.normcase(os.path.abspath(output_path_ind)):
                 st.warning("Удаление оригиналов не будет выполнено (папка ввода и вывода совпадают).", icon="⚠️")
                 log.warning("Original deletion will be skipped (paths are same).")
    elif current_mode == "Создание коллажей":
        output_filename_coll = get_setting('paths.output_filename', '')
        if not output_filename_coll: validation_errors.append("Не указано имя файла для сохранения коллажа!"); paths_ok = False
        elif input_path and paths_ok:
             # Проверяем ПОЛНОЕ имя файла с расширением
             output_format_coll = get_setting('collage_mode.output_format', 'jpg').lower()
             base_name, _ = os.path.splitext(output_filename_coll)
             coll_filename_with_ext = f"{base_name}.{output_format_coll}"
             full_coll_path_with_ext = os.path.abspath(os.path.join(abs_input_path, coll_filename_with_ext))
             if os.path.isdir(full_coll_path_with_ext): validation_errors.append(f"Имя файла коллажа '{coll_filename_with_ext}' указывает на папку!"); paths_ok = False

    if not paths_ok:
        log.warning("--- Path validation FAILED. Processing aborted. ---")
        for error_msg in validation_errors: st.error(error_msg, icon="❌"); log.error(f"Validation Error: {error_msg}")
        st.warning("Обработка не запущена из-за ошибок в настройках путей.", icon="⚠️")
        st.subheader("Логи выполнения (Ошибки валидации):")
        st.text_area("Лог:", value=log_stream.getvalue(), height=200, key='log_output_validation_error', disabled=True, label_visibility="collapsed")
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
                 
                 # === ИСПРАВЛЕНО СРАВНЕНИЕ ===
                 if mode_from_state == "Обработка отдельных файлов": 
                     log.info("Condition matched: 'Обработка отдельных файлов'")
                     # TODO: Позже можно доработать run_individual_processing по аналогии.
                     processing_workflows.run_individual_processing(**current_run_settings)
                     workflow_success = True 
                     log.info("Finished run_individual_processing call (assumed success).")
                 elif mode_from_state == "Создание коллажей": 
                     log.info("Condition matched: 'Создание коллажей'")
                     collage_created_ok = processing_workflows.run_collage_processing(**current_run_settings)
                     workflow_success = collage_created_ok 
                     log.info(f"Finished run_collage_processing call. Result: {workflow_success}")
                 else:
                     # Этот блок теперь не должен выполняться, но оставим на всякий случай
                     log.error(f"!!! Unknown mode_from_state encountered in processing block: '{mode_from_state}'")
                     workflow_success = False 
             except Exception as e:
                 log.critical(f"!!! WORKFLOW EXECUTION FAILED with EXCEPTION: {e}", exc_info=True)
                 st.error(f"Произошла критическая ошибка во время обработки: {e}", icon="🔥")
                 workflow_success = False
                 # progress_placeholder.empty() # Убрали, сообщение ниже
                 # st.text_area("Детали ошибки:", value=traceback.format_exc(), height=200, key="error_traceback_area")

        progress_placeholder.empty() # Очищаем спиннер
        # --- Вывод сообщения по результату --- 
        if workflow_success:
            # Используем mode_from_state для сообщения
            st.success(f"Обработка ('{mode_from_state}') успешно завершена!", icon="✅")
            log.info(f"--- Workflow '{mode_from_state}' completed successfully. --- ")
            
            # === АВТОСОХРАНЕНИЕ ПОСЛЕ УСПЕХА ===
            log.debug("Workflow successful, attempting to auto-save main settings...")
            try:
                settings_to_save_after_success = st.session_state.current_settings.copy()
                settings_to_save_after_success["active_preset"] = st.session_state.active_preset
                settings_to_save_after_success["processing_mode_selector"] = st.session_state.selected_processing_mode
                save_after_success_ok = config_manager.save_settings(settings_to_save_after_success, CONFIG_FILE)
                if save_after_success_ok:
                    log.info(f"Main settings auto-saved successfully to {CONFIG_FILE} after workflow completion.")
                    st.session_state.settings_changed = False # Сбрасываем флаг, если он был
                else:
                    log.error(f"Failed to auto-save main settings after workflow completion.")
                    st.toast("❌ Не удалось авто-сохранить настройки после обработки.")
            except Exception as save_ex:
                 log.error(f"Exception during auto-save after workflow completion: {save_ex}", exc_info=True)
                 st.toast("❌ Ошибка при авто-сохранении настроек после обработки.")
            # ====================================
            
        else:
            st.warning(f"Обработка ('{mode_from_state}') завершена, но результат НЕ достигнут (см. лог).", icon="⚠️")
            log.warning(f"--- Workflow '{mode_from_state}' finished, but reported failure or encountered an exception. --- ")

# --- Область для Логов ---
# Этот блок должен быть ПОСЛЕ блока if start_button_pressed_this_run
st.divider()
st.subheader("Логи выполнения:")
# Кнопка обновления лога теперь не так нужна, т.к. лог очищается при запуске
# if st.button("🔄 Обновить лог", key="refresh_log_button"):
#     st.rerun()
with st.expander("Показать/скрыть лог", expanded=True):
    st.text_area("Лог:", value=log_stream.getvalue(), height=300, key='log_output_display_area', disabled=True, label_visibility="collapsed")

# --- Опциональное отображение коллажа ---
if st.session_state.selected_processing_mode == "Создание коллажей":
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
            st.divider(); st.subheader("Предпросмотр коллажа:")
            try:
                # Используем полный путь для ключа и отображения
                preview_key = f"collage_preview_{int(os.path.getmtime(coll_full_path))}_{coll_filename_with_ext}" # Добавим имя файла в ключ
                st.image(coll_full_path, use_container_width=True, key=preview_key)
                log.debug(f"Displaying collage preview: {coll_full_path}")
            except Exception as img_e:
                st.warning(f"Не удалось отобразить превью коллажа: {img_e}")
                log.warning(f"Failed to display collage preview {coll_full_path}: {img_e}")
        else: log.debug(f"Collage file for preview not found: {coll_full_path}")
    else: log.debug("Input path or collage filename not set for preview.")

log.info("--- End of app script render cycle ---")