import os
import logging
from typing import List, Tuple, Optional, Dict, Any
from PIL import Image, ImageEnhance
import numpy as np

log = logging.getLogger(__name__)

class MergeProcessor:
    """Класс для обработки слияния изображений с шаблоном."""
    
    def __init__(self, template: Image.Image, settings: Dict[str, Any]):
        """
        Инициализация процессора слияния изображений.
        
        Args:
            template: Шаблон для слияния
            settings: Словарь настроек слияния
        """
        self.template = template
        self.settings = settings
        
    def merge_images(self, images: List[Image.Image]) -> Optional[Image.Image]:
        """
        Слияние списка изображений с шаблоном.
        
        Args:
            images: Список изображений для слияния
            
        Returns:
            Результат слияния или None в случае ошибки
        """
        try:
            if not images:
                log.error("No images provided for merging")
                return None
                
            # Получаем настройки
            size_ratio = self.settings.get('size_ratio', 1.0)
            position = self.settings.get('position', 'Центр')
            opacity = self.settings.get('opacity', 1.0)
            rotation = self.settings.get('rotation', 0)
            use_mask = self.settings.get('use_mask', False)
            overlay_order = self.settings.get('overlay_order', 'photo_over_template')
            
            # Создаем копию шаблона
            result = self.template.copy()
            
            for img in images:
                # Изменяем размер изображения
                new_size = self._calculate_new_size(img.size, size_ratio)
                img_resized = img.resize(new_size, Image.Resampling.LANCZOS)
                
                # Поворачиваем изображение
                if rotation != 0:
                    img_resized = img_resized.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
                
                # Настраиваем прозрачность
                if opacity < 1.0:
                    if img_resized.mode != 'RGBA':
                        img_resized = img_resized.convert('RGBA')
                    alpha = ImageEnhance.Brightness(img_resized.split()[3]).enhance(opacity)
                    img_resized.putalpha(alpha)
                
                # Определяем позицию для вставки
                paste_position = self._calculate_paste_position(img_resized.size, position)
                
                # Создаем маску если нужно
                mask = img_resized.split()[3] if use_mask and img_resized.mode == 'RGBA' else None
                
                # Выполняем слияние в зависимости от порядка
                if overlay_order == 'photo_over_template':
                    # Фото поверх шаблона
                    result.paste(img_resized, paste_position, mask)
                else:
                    # Шаблон поверх фото
                    # Создаем новое изображение размером с шаблон
                    temp = Image.new('RGBA', self.template.size, (0, 0, 0, 0))
                    # Вставляем фото
                    temp.paste(img_resized, paste_position, mask)
                    # Вставляем шаблон поверх
                    temp.paste(self.template, (0, 0), self.template.split()[3] if self.template.mode == 'RGBA' else None)
                    result = temp
            
            return result
            
        except Exception as e:
            log.exception("Error in merge_images")
            return None
            
    def _calculate_new_size(self, original_size: Tuple[int, int], ratio: float) -> Tuple[int, int]:
        """Вычисление нового размера изображения."""
        return (int(original_size[0] * ratio), int(original_size[1] * ratio))
        
    def _calculate_paste_position(self, img_size: Tuple[int, int], position: str) -> Tuple[int, int]:
        """Вычисление позиции для вставки изображения."""
        template_w, template_h = self.template.size
        img_w, img_h = img_size
        
        if position == "Центр":
            x = (template_w - img_w) // 2
            y = (template_h - img_h) // 2
        elif position == "Сверху":
            x = (template_w - img_w) // 2
            y = 0
        elif position == "Снизу":
            x = (template_w - img_w) // 2
            y = template_h - img_h
        elif position == "Слева":
            x = 0
            y = (template_h - img_h) // 2
        elif position == "Справа":
            x = template_w - img_w
            y = (template_h - img_h) // 2
        else:
            x = (template_w - img_w) // 2
            y = (template_h - img_h) // 2
            
        return (x, y)

    def process_psd(self, psd_path: str) -> Optional[Image.Image]:
        """Обработка PSD файла."""
        try:
            # Здесь будет код для обработки PSD
            # Пока просто возвращаем None, чтобы продолжить с обычным объединением
            return None
        except Exception as e:
            log.error(f"Error processing PSD: {e}")
            return None
            
    def merge_images(self, images: List[Image.Image]) -> Image.Image:
        """Объединение изображений с базовым."""
        try:
            result = self.base_image.copy()
            
            for img in images:
                # Применяем настройки к изображению
                if self.size_ratio != 1.0:
                    new_size = (int(img.width * self.size_ratio), 
                              int(img.height * self.size_ratio))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                
                if self.rotation != 0:
                    img = img.rotate(self.rotation, expand=True)
                
                if self.opacity < 1.0:
                    img = ImageEnhance.Brightness(img).enhance(self.opacity)
                
                # Определяем позицию для вставки
                x = (result.width - img.width) // 2
                y = (result.height - img.height) // 2
                
                if self.position == 'Сверху':
                    y = 0
                elif self.position == 'Снизу':
                    y = result.height - img.height
                elif self.position == 'Слева':
                    x = 0
                elif self.position == 'Справа':
                    x = result.width - img.width
                
                # Вставляем изображение
                if self.use_mask:
                    mask = img.split()[3] if img.mode == 'RGBA' else None
                    result.paste(img, (x, y), mask)
                else:
                    result.paste(img, (x, y))
            
            return result
            
        except Exception as e:
            log.error(f"Error merging images: {e}")
            return self.base_image
            
    def _apply_settings(self, image: Image.Image) -> Image.Image:
        """Применение настроек к изображению."""
        try:
            # Изменение размера
            if self.size_ratio != 1.0:
                new_size = (int(image.size[0] * self.size_ratio),
                          int(image.size[1] * self.size_ratio))
                image = image.resize(new_size, Image.Resampling.LANCZOS)
                
            # Поворот
            if self.rotation != 0:
                image = image.rotate(self.rotation, expand=True)
                
            # Прозрачность
            if self.opacity != 1.0:
                if image.mode != 'RGBA':
                    image = image.convert('RGBA')
                alpha = image.split()[3]
                alpha = Image.fromarray(np.array(alpha) * self.opacity)
                image.putalpha(alpha)
                
            return image
            
        except Exception as e:
            self.log.error(f"Error applying settings to image: {e}")
            return image
            
    def _calculate_position(self, base_size: Tuple[int, int], 
                          overlay_size: Tuple[int, int]) -> Tuple[int, int]:
        """Расчет позиции для наложения изображения."""
        position_mode = self.position
        
        if position_mode == "Центр":
            return ((base_size[0] - overlay_size[0]) // 2,
                   (base_size[1] - overlay_size[1]) // 2)
                   
        elif position_mode == "Случайное":
            return (np.random.randint(0, base_size[0] - overlay_size[0]),
                   np.random.randint(0, base_size[1] - overlay_size[1]))
                   
        elif position_mode == "Углы":
            # TODO: Реализовать позиционирование по углам
            return (0, 0)
            
        elif position_mode == "Произвольное":
            # TODO: Реализовать произвольное позиционирование
            return (0, 0)
            
        return (0, 0)  # По умолчанию в левый верхний угол 