# start.py
import subprocess
import sys
import os
import time # Для небольшой паузы

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
        # Запускаем streamlit run app.py как дочерний процесс
        # stdout и stderr будут выводиться в эту же консоль
        process = subprocess.run(command, check=False) # check=False, т.к. код возврата streamlit может быть разным

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