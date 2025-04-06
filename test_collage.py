import os
import time
import logging
from PIL import Image

# Настраиваем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
log = logging.getLogger(__name__)

# Импортируем функцию создания коллажа
from processing_workflows import run_collage_processing

def create_test_images(test_dir, count=5):
    """Создаем тестовые изображения для коллажа"""
    log.info(f"Creating {count} test images in {test_dir}")
    
    # Проверяем/создаем директорию
    if not os.path.exists(test_dir):
        os.makedirs(test_dir)
        log.info(f"Created directory: {test_dir}")
    
    # Создаем несколько тестовых изображений разных размеров
    image_files = []
    for i in range(count):
        width = 100 + i * 50
        height = 100 + i * 30
        img = Image.new('RGB', (width, height), color=(50 + i * 30, 100, 150))
        
        filename = os.path.join(test_dir, f"test_image_{i}.jpg")
        img.save(filename, "JPEG", quality=95)
        log.info(f"Created test image: {filename}, size: {width}x{height}")
        image_files.append(filename)
    
    return image_files

def test_direct_collage_creation():
    """Тестирует создание коллажа напрямую"""
    # Путь для тестов - используем текущий каталог
    test_dir = os.path.join(os.getcwd(), "test_images")
    collage_output = os.path.join(test_dir, "test_collage_output.jpg")
    
    log.info(f"Test directory: {test_dir}")
    log.info(f"Collage output path: {collage_output}")
    
    # Создаем тестовые изображения
    create_test_images(test_dir)
    
    # Проверяем, что директория содержит изображения
    image_files = [f for f in os.listdir(test_dir) if f.endswith('.jpg') and f != "test_collage_output.jpg"]
    log.info(f"Directory contains {len(image_files)} image files")
    
    if not image_files:
        log.error("No test images created/found")
        return False
    
    # Создаем настройки для функции коллажа
    settings = {
        'paths': {
            'input_folder_path': test_dir,
            'output_filename': "test_collage_output.jpg",
        },
        'collage_mode': {
            'output_format': 'jpg',
            'jpeg_quality': 95,
            'forced_cols': 2,
            'spacing_percent': 2.0,
        }
    }
    
    log.info("Calling run_collage_processing function")
    try:
        # Вызываем функцию создания коллажа
        run_collage_processing(**settings)
        log.info("run_collage_processing completed successfully")
        
        # Проверяем, был ли создан файл коллажа
        if os.path.exists(collage_output):
            file_size = os.path.getsize(collage_output)
            log.info(f"Collage created successfully! File size: {file_size} bytes")
            return True
        else:
            log.error("Collage file was not created")
            return False
    except Exception as e:
        log.error(f"Error during collage creation: {e}", exc_info=True)
        return False

if __name__ == "__main__":
    print("=== Testing direct collage creation ===")
    success = test_direct_collage_creation()
    print(f"Test result: {'Success' if success else 'Failed'}") 