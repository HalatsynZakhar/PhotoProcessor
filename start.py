# start.py
import subprocess
import sys
import os
import time # Для небольшой паузы
import traceback
import multiprocessing

def update_pip():
    """
    Обновляет pip до последней версии.
    Возвращает True, если обновление прошло успешно, иначе False.
    """
    print("=" * 50)
    print("Обновление pip до последней версии...")
    
    try:
        update_command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pip"
        ]
        
        print(f"Выполнение команды: {' '.join(update_command)}")
        process = subprocess.run(update_command, capture_output=True, text=True)
        
        if process.returncode != 0:
            print(f"\n[!!! ОШИБКА ОБНОВЛЕНИЯ PIP !!!]")
            print(f"Код ошибки: {process.returncode}")
            print(f"Сообщение ошибки:\n{process.stderr}")
            return False
        
        print("\n[ИНФОРМАЦИЯ] Pip успешно обновлен до последней версии.")
        print(f"Подробности:\n{process.stdout}")
        return True
        
    except Exception as e:
        print(f"\n[!!! ОШИБКА !!!]")
        print(f"Не удалось обновить pip: {e}")
        return False

def install_requirements():
    """
    Устанавливает необходимые зависимости из файла requirements.txt.
    Возвращает True, если установка прошла успешно, иначе False.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    requirements_path = os.path.join(script_dir, "requirements.txt")
    
    if not os.path.isfile(requirements_path):
        print(f"\n[!!! ОШИБКА !!!]")
        print(f"Не найден файл зависимостей: {requirements_path}")
        print("Убедитесь, что 'requirements.txt' находится в той же папке, что и 'start.py'.")
        return False
    
    print("=" * 50)
    print("Проверка и установка необходимых библиотек...")
    print(f"Используется файл: {requirements_path}")
    
    try:
        # Используем pip для установки требуемых пакетов
        install_command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            requirements_path
        ]
        
        print(f"Выполнение команды: {' '.join(install_command)}")
        process = subprocess.run(install_command, capture_output=True, text=True)
        
        if process.returncode != 0:
            print(f"\n[!!! ОШИБКА УСТАНОВКИ !!!]")
            print(f"Код ошибки: {process.returncode}")
            print(f"Сообщение ошибки:\n{process.stderr}")
            return False
        
        # Проверяем, были ли установлены новые пакеты
        # Если в выводе есть "Successfully installed", значит что-то установилось
        if "Successfully installed" in process.stdout:
            print("\n[ИНФОРМАЦИЯ] Были установлены новые библиотеки.")
            print(f"Подробности:\n{process.stdout}")
        else:
            print("\n[ИНФОРМАЦИЯ] Все необходимые библиотеки уже установлены.")
        
        return True
    
    except Exception as e:
        print(f"\n[!!! КРИТИЧЕСКАЯ ОШИБКА !!!]")
        print(f"Ошибка при установке зависимостей: {e}")
        return False

def initialize_multiprocessing():
    """
    Инициализирует мультипроцессинг перед запуском приложения.
    Устанавливает правильный метод запуска процессов и выполняет другие необходимые настройки.
    """
    try:
        print("=" * 50)
        print("Настройка мультипроцессинга...")
        
        # Устанавливаем критичные переменные окружения для Windows
        if sys.platform == 'win32':
            # Устанавливаем необходимые переменные окружения для поддержки многопроцессорности
            os.environ['PYTHONMULTIPROCESSING'] = '1'
            os.environ['PYTHONMULTIPROCESSINGMETHOD'] = 'spawn'
            os.environ['PYTHONNOWINDOW'] = '1'
            os.environ['PYTHONLEGACYWINDOWSSUBPROCESS'] = '1'
            os.environ['PYTHONSUBPROCESSNOSESSION'] = '1'
            print("Установлены переменные окружения для Windows")
            
            # Устанавливаем метод запуска 'spawn' напрямую, до импорта модулей
            if hasattr(multiprocessing, 'set_start_method'):
                try:
                    multiprocessing.set_start_method('spawn', force=True)
                    print("Установлен метод запуска процессов 'spawn'")
                except RuntimeError as e:
                    print(f"Метод запуска уже установлен: {e}")
            
        # Импортируем модуль мультипроцессинга
        script_dir = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, script_dir)
        
        # Импортируем наш модуль multiprocessing_utils
        try:
            import multiprocessing_utils
            
            # Вызываем функцию инициализации
            multiprocessing_utils.enable_multiprocessing()
        except AttributeError as e:
            if '_subprocess' in str(e):
                print(f"Обнаружена ошибка с subprocess._subprocess. Используем переменные окружения для решения.")
                # Продолжаем выполнение, т.к. переменные окружения уже установлены выше
            else:
                raise e
        
        # Выводим информацию о доступных ядрах
        cpu_count = multiprocessing.cpu_count() if 'multiprocessing' in sys.modules else "Unknown"
        print(f"Доступно CPU ядер: {cpu_count}")
        print(f"Текущая платформа: {sys.platform}")
        
        # Для Windows выводим дополнительную информацию
        if sys.platform == 'win32':
            try:
                method = multiprocessing.get_start_method()
                print(f"Используется метод запуска процессов '{method}' для Windows")
            except Exception as e:
                print(f"Не удалось получить текущий метод запуска: {e}")
            
        print("Мультипроцессинг успешно настроен")
        return True
        
    except ImportError as e:
        print(f"\n[!!! ОШИБКА !!!]")
        print(f"Не удалось импортировать модуль multiprocessing_utils: {e}")
        return False
    except Exception as e:
        print(f"\n[!!! ОШИБКА !!!]")
        print(f"Произошла ошибка при настройке мультипроцессинга: {e}")
        print(f"Детали: {traceback.format_exc()}")
        return False

def main():
    """
    Запускает Streamlit-приложение app.py с помощью команды 'streamlit run'.
    Предполагается, что start.py и app.py находятся в одной директории.
    """
    # Определяем директорию, где находится сам start.py
    # Это важно, чтобы правильно найти app.py, даже если скрипт
    # запускается из другого места.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    app_script_path = os.path.join(script_dir, "app.py")

    print("=" * 50)
    print("Запуск Streamlit приложения...")
    print(f"Директория скрипта: {script_dir}")
    print(f"Путь к app.py: {app_script_path}")

    # Проверяем, существует ли app.py
    if not os.path.isfile(app_script_path):
        print(f"\n[!!! ОШИБКА !!!]")
        print(f"Не найден основной файл приложения: {app_script_path}")
        print("Убедитесь, что 'app.py' находится в той же папке, что и 'start.py'.")
        print("=" * 50)
        # Даем пользователю время прочитать ошибку перед закрытием консоли
        time.sleep(10)
        sys.exit(1) # Выход с кодом ошибки
    
    # Обновляем pip перед установкой зависимостей
    print("Обновление pip...")
    update_pip()
    
    # Устанавливаем зависимости перед запуском
    print("Установка необходимых зависимостей...")
    if not install_requirements():
        print("Не удалось установить необходимые зависимости. Приложение может работать некорректно.")
        print("=" * 50)
        time.sleep(5)
        # Продолжаем выполнение, даже если установка не удалась
    
    # Инициализируем мультипроцессинг перед запуском приложения
    print("Настройка системы мультипроцессорной обработки...")
    initialize_multiprocessing()

    # Формируем команду для запуска
    # Используем sys.executable, чтобы гарантировать использование
    # того же интерпретатора Python, под которым запущен start.py.
    # Это помогает найти правильную установку streamlit, особенно в venv.
    command = [
        sys.executable,    # Путь к текущему интерпретатору python.exe
        "-m",              # Флаг для запуска модуля как скрипта
        "streamlit",       # Имя модуля streamlit
        "run",             # Команда streamlit для запуска
        app_script_path    # Путь к вашему основному файлу приложения
    ]

    print(f"\nВыполнение команды:")
    print(f"> {' '.join(command)}") # Показываем команду пользователю
    print("=" * 50)
    print("\nStreamlit должен запуститься в вашем браузере.")
    print("Окно консоли можно свернуть, но НЕ ЗАКРЫВАТЬ, пока работает приложение.")
    print("Для остановки приложения закройте это окно консоли или нажмите Ctrl+C.")

    try:
        # Подготавливаем переменные окружения для дочернего процесса
        env = os.environ.copy()
        
        # Устанавливаем переменные окружения для мультипроцессинга
        # Форсируем использование 'spawn' метода на Windows
        if sys.platform == 'win32':
            env['PYTHONPATH'] = script_dir
            env['PYTHONMULTIPROCESSING'] = '1'
            # Устанавливаем переменную для метода запуска
            env['PYTHONEXECUTABLE'] = sys.executable
            # Форсируем использование 'spawn' метода для многопроцессорной обработки
            env['PYTHONMULTIPROCESSINGMETHOD'] = 'spawn'
            # Устанавливаем переменную для контроля за подпроцессами - предотвращает создание окон консоли
            env['PYTHONNOWINDOW'] = '1'
            # Устанавливаем переменную для обработки ошибки subprocess._subprocess
            env['PYTHONLEGACYWINDOWSSUBPROCESS'] = '1'
            # Добавим дополнительную переменную для совместимости с разными версиями Python
            env['PYTHONSUBPROCESSNOSESSION'] = '1'
            
            # Уставливаем переменные напрямую в текущем процессе, чтобы модули могли их подхватить
            os.environ['PYTHONMULTIPROCESSING'] = '1'
            os.environ['PYTHONMULTIPROCESSINGMETHOD'] = 'spawn'
            os.environ['PYTHONNOWINDOW'] = '1'
            os.environ['PYTHONLEGACYWINDOWSSUBPROCESS'] = '1'
            os.environ['PYTHONSUBPROCESSNOSESSION'] = '1'
            
            print(f"Установлены переменные окружения для мультипроцессинга и контроля за подпроцессами")
        
        # Запускаем streamlit run app.py как дочерний процесс
        # stdout и stderr будут выводиться в эту же консоль
        # Передаем настроенные переменные окружения
        process = subprocess.run(command, check=False, env=env) # check=False, т.к. код возврата streamlit может быть разным

        print("\n" + "=" * 50)
        print("Процесс Streamlit завершился.")
        if process.returncode != 0:
             print(f"Код завершения: {process.returncode} (Возможно, была ошибка в приложении)")
        else:
             print("Код завершения: 0 (Нормальное завершение)")

    except FileNotFoundError:
        print("\n[!!! КРИТИЧЕСКАЯ ОШИБКА !!!]")
        print(f"Не удалось найти '{sys.executable}' или модуль 'streamlit'.")
        print("Убедитесь, что Python установлен корректно и что streamlit установлен")
        print("в этом окружении Python (возможно, через 'pip install streamlit').")
    except Exception as e:
        print("\n[!!! КРИТИЧЕСКАЯ ОШИБКА !!!]")
        print(f"Произошла ошибка при попытке запуска Streamlit: {e}")

    print("=" * 50)
    print("Скрипт start.py завершил свою работу.")
    # Пауза перед автоматическим закрытием окна консоли (если оно запускалось двойным кликом)
    time.sleep(5)


if __name__ == "__main__":
    main()