import os
import logging
from logging.handlers import RotatingFileHandler
from io import StringIO

def setup_logging():
    """Настраивает логирование для приложения."""
    # Создаем папку для логов если её нет
    LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    LOG_FILENAME = os.path.join(LOG_DIR, "app.log")
    LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
    LOG_BACKUP_COUNT = 5  # Keep 5 backup files
    
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # Настраиваем логирование
    log_stream = StringIO() # Буфер для UI
    log_level = logging.INFO # logging.DEBUG для более подробных логов
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    
    # --- Настройка корневого логгера --- 
    # Удаляем стандартные обработчики, если они есть
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.setLevel(log_level)
    
    # 1. Обработчик для вывода в UI (через StringIO)
    stream_handler = logging.StreamHandler(log_stream)
    stream_handler.setFormatter(log_formatter)
    stream_handler.setLevel(log_level)
    root_logger.addHandler(stream_handler)
    
    # 2. Обработчик для записи в файл с ротацией
    try:
        file_handler = RotatingFileHandler(
            LOG_FILENAME,
            mode='a',
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding='utf-8'
        )
        file_handler.setFormatter(log_formatter)
        file_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(file_handler)
        print(f"Logging to file: {os.path.abspath(LOG_FILENAME)} (Level: DEBUG, Mode: Append, Max Size: {LOG_MAX_BYTES/1024/1024}MB)")
    except Exception as e_fh:
        print(f"[!!! ОШИБКА] Не удалось настроить логирование в файл {LOG_FILENAME}: {e_fh}")
    
    # Получаем логгер для текущего модуля
    log = logging.getLogger(__name__)
    log.info("--- Logger configured (Stream + File) ---")
    log.info(f"UI Log Level: {logging.getLevelName(log_level)}")
    log.info(f"File Log Level: DEBUG")
    
    return log_stream, log 