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
                st.session_state.reset_profiles_confirmation_pending = False
                st.rerun()

        if st.button("💥 Сбросить все к заводским", key="reset_all_settings_button", disabled=st.session_state.reset_settings_confirmation_pending, help="Полностью сбросить все настройки к первоначальному состоянию программы.", use_container_width=True):
            st.session_state.reset_settings_confirmation_pending = True
            st.session_state.reset_active_preset_confirmation_pending = False
            st.rerun()

    # === Пути (объединенный блок) ===
    st.header("📂 Пути")
    user_downloads_folder = get_downloads_folder()
    log.debug(f"Resolved Downloads Folder: {user_downloads_folder}")

    with st.expander("Настройка путей и сохранения", expanded=False):
        current_mode_for_file_ops = st.session_state.selected_processing_mode
        if current_mode_for_file_ops == "Обработка отдельных файлов":
            # --- Переименование и удаление --- 
            st.caption("⚡️ Переименование и удаление")
            enable_rename_ind = st.checkbox("Переименовать файлы (по артикулу)",
                                            value=get_setting('individual_mode.enable_rename', False),
                                            key='ind_enable_rename',
                                            help="Автоматически переименовывает обработанные файлы, используя указанный артикул. Первый файл будет назван как артикул, остальные - артикул_1, артикул_2 и т.д.")
            set_setting('individual_mode.enable_rename', enable_rename_ind)
            if enable_rename_ind:
                article_ind = st.text_input("Артикул для переименования",
                                            value=get_setting('individual_mode.article_name', ''),
                                            key='ind_article',
                                            placeholder="Введите артикул...",
                                            help="Введите артикул или базовое имя для файлов. Это имя будет использоваться как основа для всех обработанных файлов.")
                set_setting('individual_mode.article_name', article_ind)
                if article_ind: st.caption("Файлы будут вида: [Артикул]_1.jpg, ...")
                else: st.warning("Введите артикул для переименования.") # Валидация
            
            delete_orig_ind = st.checkbox("Удалять оригиналы после обработки?",
                                          value=get_setting('individual_mode.delete_originals', False),
                                          key='ind_delete_orig',
                                          help="Если включено, исходные файлы будут удалены после успешной обработки. Это действие необратимо, поэтому используйте его с осторожностью. Рекомендуется включить бэкап перед использованием этой опции.")
            set_setting('individual_mode.delete_originals', delete_orig_ind)
            if delete_orig_ind: st.warning("ВНИМАНИЕ: Удаление необратимо!", icon="⚠️")

        # --- Input Path ---
        st.caption("Основной путь ввода")
        current_input_path = get_setting('paths.input_folder_path')
        input_path_default_value = current_input_path if current_input_path else user_downloads_folder
        input_path_val = st.text_input(
            "Папка с исходными файлами:",
            value=input_path_default_value,
            key='path_input_sidebar',
            help="Укажите папку, в которой находятся исходные изображения для обработки. Поддерживаются форматы JPG, PNG, WEBP, TIFF, BMP, GIF. Папка должна существовать и быть доступной для чтения."
        )
        if input_path_val != current_input_path:
            set_setting('paths.input_folder_path', input_path_val)
        if input_path_val and os.path.isdir(input_path_val): st.caption(f"✅ Папка найдена: {os.path.abspath(input_path_val)}")
        elif input_path_val: st.caption(f"❌ Папка не найдена: {os.path.abspath(input_path_val)}")
        else: st.caption("ℹ️ Путь не указан.")

        current_mode_local = st.session_state.selected_processing_mode
        if current_mode_local == "Обработка отдельных файлов":
            st.caption("Пути обработки файлов")
            # --- Output Path ---
            current_output_path = get_setting('paths.output_folder_path', '')
            output_path_default_value = current_output_path if current_output_path else os.path.join(user_downloads_folder, "Processed")
            output_path_val = st.text_input(
                "Папка для результатов:",
                value=output_path_default_value,
                key='path_output_ind_sidebar',
                help="Укажите папку, куда будут сохранены обработанные изображения. Папка будет создана автоматически, если она не существует. Убедитесь, что у вас есть права на запись в эту папку."
            )
            if output_path_val != current_output_path:
                set_setting('paths.output_folder_path', output_path_val)
            if output_path_val: st.caption(f"Сохранять в: {os.path.abspath(output_path_val)}")

            # --- Backup Path ---
            current_backup_path = get_setting('paths.backup_folder_path')
            backup_path_default_value = current_backup_path if current_backup_path else os.path.join(user_downloads_folder, "Backups")
            backup_path_val = st.text_input(
                "Папка для бэкапов:",
                value=backup_path_default_value,
                key='path_backup_ind_sidebar',
                placeholder="Оставьте пустым чтобы отключить",
                help="Укажите папку для резервного копирования оригинальных файлов перед обработкой. Оставьте пустым, чтобы отключить создание резервных копий. Рекомендуется использовать бэкап при включенной опции удаления оригиналов."
            )
            if backup_path_val != current_backup_path:
                set_setting('paths.backup_folder_path', backup_path_val)
            if backup_path_val:
                is_default_shown = not current_backup_path and backup_path_val == os.path.join(user_downloads_folder, "Backups")
                st.caption(f"Бэкап в: {os.path.abspath(backup_path_val)}" + (" (по умолчанию)" if is_default_shown else ""))
            else:
                st.caption(f"Бэкап отключен.")

        elif current_mode_local == "Создание коллажей":
            st.caption("Настройки коллажа")
            collage_filename_val = st.text_input(
                "Имя файла коллажа (без расш.):",
                value=get_setting('paths.output_filename', 'collage'),
                key='path_output_coll_sidebar',
                help="Введите базовое имя файла для коллажа (без расширения). Коллаж будет сохранен в папке с исходными файлами с указанным именем и расширением в соответствии с выбранным форматом (JPG или PNG)."
            )
            set_setting('paths.output_filename', collage_filename_val)
            if collage_filename_val: st.caption(f"Имя файла: {collage_filename_val}.[расширение]")

    # --- Кнопка сброса путей в отдельном expander ---
    with st.expander("Сброс путей", expanded=False):
        if st.button("🔄 Сбросить пути по умолчанию", key="reset_paths_button",
                     help="Установить стандартные пути на основе папки Загрузки вашей системы. Это сбросит все пути к их значениям по умолчанию.",
                     use_container_width=True):
            set_setting('paths.input_folder_path', '')
            set_setting('paths.output_folder_path', '')
            set_setting('paths.backup_folder_path', '')
            st.toast("Пути сброшены к значениям по умолчанию", icon="🔄")
            st.rerun()

    # === Остальные Настройки ===
    st.header("⚙️ Настройки обработки")
    st.caption(f"Настройки для режима: **{st.session_state.selected_processing_mode}**")
    with st.expander("1. Предварительный ресайз", expanded=False):
        enable_preresize = st.checkbox("Включить", value=get_setting('preprocessing.enable_preresize', False), key='pre_enable',
                                     help="Уменьшает размер изображения перед дальнейшей обработкой для экономии памяти и ускорения работы. Полезно при обработке больших изображений.")
        set_setting('preprocessing.enable_preresize', enable_preresize)
        if enable_preresize:
            col_pre1, col_pre2 = st.columns(2)
            with col_pre1:
                 pr_w = st.number_input("Макс. Ширина (px)", 0, 10000, 
                                       value=get_setting('preprocessing.preresize_width', 2500), 
                                       step=10, key='pre_w',
                                       help="Максимальная ширина изображения после ресайза. 0 означает отсутствие ограничения по ширине. Изображение будет пропорционально уменьшено, если его ширина превышает это значение.")
                 set_setting('preprocessing.preresize_width', pr_w)
            with col_pre2:
                 pr_h = st.number_input("Макс. Высота (px)", 0, 10000, 
                                       value=get_setting('preprocessing.preresize_height', 2500), 
                                       step=10, key='pre_h',
                                       help="Максимальная высота изображения после ресайза. 0 означает отсутствие ограничения по высоте. Изображение будет пропорционально уменьшено, если его высота превышает это значение.")
                 set_setting('preprocessing.preresize_height', pr_h)

    with st.expander("2. Отбеливание", expanded=False):
        # === ЧЕКБОКС ВКЛЮЧЕНИЯ ===
        enable_whitening = st.checkbox("Включить ", value=get_setting('whitening.enable_whitening', False), 
                                     key='white_enable',
                                     help="Конвертирует светлый фон по периметру в чисто белый цвет. Полезно для улучшения качества изображений с сероватым или не совсем белым фоном.")
        set_setting('whitening.enable_whitening', enable_whitening)
        if enable_whitening:
            # === ПРОСТОЙ ПРОЦЕНТНЫЙ СЛАЙДЕР ===
            # Порог светлости периметра для отбеливания
            # При пороге 50% отбеливаются изображения с периметром светлее 50% (серый)
            wc_percent = st.slider("Максимальная темнота периметра", 0, 100, 
                                  value=30, # По умолчанию 30%
                                  step=1, 
                                  key='white_thr', 
                                  format="%d%%",
                                  help="Определяет, насколько темным может быть периметр изображения для его отбеливания. 0% - отбеливать только чисто белый периметр, 100% - отбеливать любой периметр. Рекомендуемое значение - 30%.")
            
            # Преобразуем проценты в абсолютный порог для функции отбеливания
            adjusted_threshold = int((100 - wc_percent) * 7.65)  # Инвертируем для логического соответствия
            set_setting('whitening.cancel_threshold_sum', adjusted_threshold)

    with st.expander("3. Удаление фона и обрезка", expanded=False):
        enable_bg_crop = st.checkbox("Включить ", value=get_setting('background_crop.enable_bg_crop', False), 
                                   key='bgc_enable',
                                   help="Удаляет белый фон вокруг объекта и обрезает изображение по границам объекта. Полезно для подготовки изображений к публикации или для создания коллажей.")
        set_setting('background_crop.enable_bg_crop', enable_bg_crop)
        if enable_bg_crop:
            bgc_tol = st.slider("Допуск белого фона", 0, 255, 
                              value=get_setting('background_crop.white_tolerance', 10), 
                              key='bgc_tol', 
                              help="Определяет, насколько цвет пикселя может отличаться от чисто белого (RGB 255,255,255), чтобы считаться фоном. Меньшие значения более строгие, большие - более гибкие. Рекомендуется не выше 20.")
            set_setting('background_crop.white_tolerance', bgc_tol)
            bgc_per = st.checkbox("Проверять периметр", 
                                value=get_setting('background_crop.check_perimeter', True), 
                                key='bgc_perimeter', 
                                help="Включите, чтобы обрезка выполнялась только если периметр изображения белый. Предотвращает обрезку изображений без белого фона или с объектами, касающимися края.")
            set_setting('background_crop.check_perimeter', bgc_per)
            bgc_abs = st.checkbox("Абсолютно симм. обрезка", 
                                value=get_setting('background_crop.crop_symmetric_absolute', False), 
                                key='bgc_abs',
                                help="Обрезка будет одинаковой со всех сторон (минимальная из обнаруженных). Полезно для создания квадратных изображений или сохранения симметрии.")
            set_setting('background_crop.crop_symmetric_absolute', bgc_abs)
            if not bgc_abs:
                bgc_axes = st.checkbox("Симм. обрезка по осям", 
                                     value=get_setting('background_crop.crop_symmetric_axes', False), 
                                     key='bgc_axes',
                                     help="Обрезка будет симметричной по каждой оси (слева/справа одинаково, сверху/снизу одинаково). Полезно для сохранения баланса изображения.")
                set_setting('background_crop.crop_symmetric_axes', bgc_axes)

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
            help="Определяет условия, при которых будут добавлены поля вокруг изображения. Выберите подходящий режим в зависимости от типа обрабатываемых изображений."
        )
        selected_padding_mode_key = padding_mode_keys[padding_mode_values.index(selected_padding_mode_value)]
        
        # Сохраняем выбранный режим
        if selected_padding_mode_key != current_padding_mode:
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
                                 help="Определяет, насколько цвет пикселя периметра может отличаться от чисто белого (RGB 255,255,255), чтобы считаться белым при проверке. Влияет только на решение о добавлении полей, но не на их размер.")
            set_setting('padding.perimeter_check_tolerance', pad_tol)

        if selected_padding_mode_key != 'never':
            st.caption("Общие настройки полей:")
            pad_p = st.slider("Процент полей", 0.0, 50.0, 
                              value=get_setting('padding.padding_percent', 5.0), 
                              step=0.5, key='pad_perc_conditional', format="%.1f%%",
                              help="Размер добавляемых полей в процентах от большей стороны изображения. Поля будут одинаковыми со всех сторон. Большие значения создают больше пространства вокруг объекта.")
            set_setting('padding.padding_percent', pad_p)

            pad_exp = st.checkbox("Разрешить полям расширять холст", 
                                  value=get_setting('padding.allow_expansion', True), 
                                  key='pad_expand_conditional', 
                                  help="Когда включено, поля могут увеличивать общий размер изображения. Если выключено, поля будут добавлены только если они не приведут к увеличению исходного размера изображения.")
            set_setting('padding.allow_expansion', pad_exp)

    # === НОВЫЙ ЭКСПАНДЕР ===
    with st.expander("5. Яркость и Контраст", expanded=False):
        enable_bc = st.checkbox("Включить", 
                              value=get_setting('brightness_contrast.enable_bc', False), 
                              key='bc_enable',
                              help="Активирует регулировку яркости и контраста изображения. Полезно для улучшения видимости деталей и общего качества изображения.")
        set_setting('brightness_contrast.enable_bc', enable_bc)
        if enable_bc:
            brightness_factor = st.slider("Яркость", 0.1, 3.0, 
                                          value=get_setting('brightness_contrast.brightness_factor', 1.0), 
                                          step=0.05, key='bc_brightness', format="%.2f",
                                          help="Коэффициент яркости: 1.0 - без изменений, меньше 1.0 - темнее, больше 1.0 - светлее. Используйте для коррекции слишком темных или светлых изображений.")
            set_setting('brightness_contrast.brightness_factor', brightness_factor)
            
            contrast_factor = st.slider("Контраст", 0.1, 3.0, 
                                        value=get_setting('brightness_contrast.contrast_factor', 1.0), 
                                        step=0.05, key='bc_contrast', format="%.2f",
                                        help="Коэффициент контраста: 1.0 - без изменений, меньше 1.0 - меньше контраста (более плоское изображение), больше 1.0 - больше контраста (более выраженная разница между светлыми и темными участками).")
            set_setting('brightness_contrast.contrast_factor', contrast_factor)
    # ========================

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
                st.caption("Точный холст (ШxВ, px)")
                col_e1, col_e2 = st.columns(2)
                with col_e1: 
                    exact_w_ind = st.number_input("Ш", 1, 10000, 
                                                value=get_setting('individual_mode.final_exact_width', 1000), 
                                                step=50, key='ind_exact_w', 
                                                label_visibility="collapsed",
                                                help="Точная ширина холста в пикселях")
                    set_setting('individual_mode.final_exact_width', exact_w_ind)
                with col_e2: 
                    exact_h_ind = st.number_input("В", 1, 10000, 
                                                value=get_setting('individual_mode.final_exact_height', 1000), 
                                                step=50, key='ind_exact_h', 
                                                label_visibility="collapsed",
                                                help="Точная высота холста в пикселях")
                    set_setting('individual_mode.final_exact_height', exact_h_ind)

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
                 else: st.caption("-")
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
                             if new_bg_color_ind != get_setting('individual_mode.jpg_background_color', [255,255,255]): set_setting('individual_mode.jpg_background_color', new_bg_color_ind)
                         else: st.caption("❌ R,G,B 0-255")
                     except ValueError: st.caption("❌ R,G,B 0-255")
                 else: st.caption("-")
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

# === Конец блока with st.sidebar ===

# === ОСНОВНАЯ ОБЛАСТЬ ===

# --- Заголовок ---
st.title("🖼️ Инструмент Обработки Изображений")
st.subheader(f"**Режим:** {st.session_state.selected_processing_mode} | **Активный набор:** {st.session_state.active_preset}")

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
        if not output_path_ind: 
            validation_errors.append("Не указана папка для результатов!")
            paths_ok = False
        if paths_ok and get_setting('individual_mode.delete_originals') and input_path and output_path_ind:
            if os.path.normcase(os.path.abspath(input_path)) == os.path.normcase(os.path.abspath(output_path_ind)):
                st.warning("Удаление оригиналов не будет выполнено (папка ввода и вывода совпадают).", icon="⚠️")
                log.warning("Original deletion will be skipped (paths are same).")
    elif current_mode == "Создание коллажей":
        output_filename_coll = get_setting('paths.output_filename', '')
        if not output_filename_coll: 
            validation_errors.append("Не указано имя файла для сохранения коллажа!")
            paths_ok = False
        elif input_path and paths_ok:
            # Проверяем ПОЛНОЕ имя файла с расширением
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
        
        with st.expander("📋 Журнал ошибок валидации", expanded=True):
            st.text_area("Лог:", value=log_stream.getvalue(), height=200, 
                       key='log_output_validation_error', disabled=True, 
                       label_visibility="collapsed")
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
                
                if mode_from_state == "Обработка отдельных файлов": 
                    log.info("Condition matched: 'Обработка отдельных файлов'")
                    processing_workflows.run_individual_processing(**current_run_settings)
                    workflow_success = True 
                    log.info("Finished run_individual_processing call (assumed success).")
                elif mode_from_state == "Создание коллажей": 
                    log.info("Condition matched: 'Создание коллажей'")
                    collage_created_ok = processing_workflows.run_collage_processing(**current_run_settings)
                    workflow_success = collage_created_ok 
                    log.info(f"Finished run_collage_processing call. Result: {workflow_success}")
                else:
                    log.error(f"!!! Unknown mode_from_state encountered in processing block: '{mode_from_state}'")
                    workflow_success = False 
            except Exception as e:
                log.critical(f"!!! WORKFLOW EXECUTION FAILED with EXCEPTION: {e}", exc_info=True)
                st.error(f"Произошла критическая ошибка во время обработки: {e}", icon="🔥")
                workflow_success = False

        progress_placeholder.empty() # Очищаем спиннер
        
        # --- Вывод сообщения по результату --- 
        if workflow_success:
            # Используем mode_from_state для сообщения
            if mode_from_state == "Обработка отдельных файлов":
                st.success("✅ Обработка изображений завершена успешно!")
            elif mode_from_state == "Создание коллажей":
                st.success("✅ Создание коллажа завершено успешно!")
            else:
                # Неизвестный режим
                st.success(f"✅ Операция '{mode_from_state}' выполнена успешно!")

            with st.expander("📋 Журнал выполнения", expanded=False):
                st.text_area("Лог:", value=log_stream.getvalue(), height=250, 
                          key='log_output_success', disabled=True, 
                          label_visibility="collapsed")
        else:
            # В случае ошибки:
            st.error(f"❌ Ошибка при выполнении операции '{mode_from_state}'!")

            # --- LOGS IN EXPANDER ---
            with st.expander("📋 Журнал выполнения (ошибки)", expanded=True):
                log_content = log_stream.getvalue()
                st.text_area("Лог с ошибками:", value=log_content, height=300, 
                           key='log_output_error', disabled=True, 
                           label_visibility="collapsed")
            # ----------------------

# --- Область для Логов ---
# Этот блок должен быть ПОСЛЕ блока if start_button_pressed_this_run
st.subheader("Логи и предпросмотр:")

# Блок лога
with st.expander("📋 Журнал работы приложения", expanded=False):
    st.text_area("Лог:", value=log_stream.getvalue(), height=250, 
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
            with st.expander("🖼️ Предпросмотр коллажа", expanded=False):
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
            with st.expander("🖼️ Предпросмотр обработанных фотографий", expanded=False):
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