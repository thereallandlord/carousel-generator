"""
Carousel Generator API v2
=========================
Генератор Instagram каруселей для интеграции с N8N

Запуск:
  pip install -r requirements.txt
  python main.py

Эндпоинты:
  POST /generate - Генерация карусели
  GET /health - Проверка работы
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from PIL import Image, ImageDraw, ImageFont
import requests
import base64
import os
import io
from datetime import datetime

app = FastAPI(title="Carousel Generator API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Директории
os.makedirs("fonts", exist_ok=True)
os.makedirs("output", exist_ok=True)

# Размер Instagram карусели
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1350


# ============== МОДЕЛИ ==============

class SlideContent(BaseModel):
    """Контент одного слайда"""
    heading: str = ""
    text: str = ""
    highlight: Optional[str] = None  # Текст с жёлтым фоном


class CarouselRequest(BaseModel):
    """Запрос на генерацию"""
    # Фото фон - URL или base64
    background_image: Optional[str] = None
    background_color: str = "#000000"
    overlay_opacity: int = 40  # Затемнение 0-100
    
    # Брендинг
    username: str = "@kamilgazizovv"
    
    # Контент слайдов
    slides: List[SlideContent]
    
    # Стили (опционально)
    text_color: str = "#ffffff"
    highlight_color: str = "#c8ff00"
    highlight_text_color: str = "#000000"
    font_size_heading: int = 64
    font_size_text: int = 48


class CarouselResponse(BaseModel):
    """Ответ с результатами"""
    success: bool
    message: str
    slides: List[dict]  # [{slide_number, base64, filename}]


# ============== ГЕНЕРАТОР ==============

class CarouselGenerator:
    
    def __init__(self):
        self.font_cache = {}
    
    def get_font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        """Получить шрифт Inter"""
        weight = "Bold" if bold else "Regular"
        key = f"{size}_{weight}"
        
        if key not in self.font_cache:
            # Пробуем разные пути к шрифтам
            font_paths = [
                f"fonts/Inter-{weight}.ttf",
                f"fonts/Inter_{weight}.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
            
            for path in font_paths:
                if os.path.exists(path):
                    try:
                        self.font_cache[key] = ImageFont.truetype(path, size)
                        break
                    except:
                        continue
            
            if key not in self.font_cache:
                # Fallback на дефолтный
                try:
                    self.font_cache[key] = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
                except:
                    self.font_cache[key] = ImageFont.load_default()
        
        return self.font_cache[key]
    
    def load_background_image(self, source: str) -> Optional[Image.Image]:
        """Загрузить фото из URL или base64"""
        try:
            if source.startswith('data:'):
                # Base64
                header, data = source.split(',', 1)
                img_data = base64.b64decode(data)
                return Image.open(io.BytesIO(img_data))
            elif source.startswith('http'):
                # URL
                response = requests.get(source, timeout=30)
                response.raise_for_status()
                return Image.open(io.BytesIO(response.content))
            else:
                # Локальный файл
                if os.path.exists(source):
                    return Image.open(source)
        except Exception as e:
            print(f"Ошибка загрузки фото: {e}")
        return None
    
    def create_background(self, bg_image: Optional[Image.Image], bg_color: str, overlay: int) -> Image.Image:
        """Создать фон с затемнением"""
        # Базовый цвет
        canvas = Image.new('RGB', (CANVAS_WIDTH, CANVAS_HEIGHT), bg_color)
        
        if bg_image:
            # Ресайзим фото чтобы покрыло весь canvas (cover)
            img_ratio = bg_image.width / bg_image.height
            canvas_ratio = CANVAS_WIDTH / CANVAS_HEIGHT
            
            if img_ratio > canvas_ratio:
                # Фото шире - подгоняем по высоте
                new_height = CANVAS_HEIGHT
                new_width = int(new_height * img_ratio)
            else:
                # Фото выше - подгоняем по ширине
                new_width = CANVAS_WIDTH
                new_height = int(new_width / img_ratio)
            
            bg_image = bg_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Центрируем и обрезаем
            left = (new_width - CANVAS_WIDTH) // 2
            top = (new_height - CANVAS_HEIGHT) // 2
            bg_image = bg_image.crop((left, top, left + CANVAS_WIDTH, top + CANVAS_HEIGHT))
            
            # Конвертируем в RGB если нужно
            if bg_image.mode != 'RGB':
                bg_image = bg_image.convert('RGB')
            
            canvas = bg_image
        
        # Накладываем затемнение
        if overlay > 0:
            overlay_layer = Image.new('RGBA', (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, int(255 * overlay / 100)))
            canvas = canvas.convert('RGBA')
            canvas = Image.alpha_composite(canvas, overlay_layer)
            canvas = canvas.convert('RGB')
        
        return canvas
    
    def draw_text_with_wrap(self, draw: ImageDraw.Draw, text: str, x: int, y: int, 
                            font: ImageFont.FreeTypeFont, color: str, max_width: int,
                            line_height: float = 1.1) -> int:
        """Отрисовать текст с переносом строк, вернуть Y после текста"""
        lines = []
        
        # Разбиваем по явным переносам
        paragraphs = text.split('\n')
        
        for para in paragraphs:
            words = para.split()
            current_line = []
            
            for word in words:
                current_line.append(word)
                test_text = ' '.join(current_line)
                bbox = draw.textbbox((0, 0), test_text, font=font)
                
                if bbox[2] > max_width and len(current_line) > 1:
                    current_line.pop()
                    lines.append(' '.join(current_line))
                    current_line = [word]
            
            if current_line:
                lines.append(' '.join(current_line))
        
        # Отрисовываем строки
        current_y = y
        font_size = font.size if hasattr(font, 'size') else 48
        
        for line in lines:
            draw.text((x, current_y), line, font=font, fill=color)
            current_y += int(font_size * line_height)
        
        return current_y
    
    def draw_highlight_text(self, img: Image.Image, text: str, x: int, y: int,
                           font: ImageFont.FreeTypeFont, text_color: str, 
                           bg_color: str, padding_x: int = 16, padding_y: int = 4):
        """Отрисовать текст с цветным фоном (выделение)"""
        draw = ImageDraw.Draw(img)
        
        # Размер текста
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # Рисуем фон
        bg_rect = [
            x - padding_x,
            y - padding_y,
            x + text_width + padding_x,
            y + text_height + padding_y
        ]
        draw.rectangle(bg_rect, fill=bg_color)
        
        # Рисуем текст
        draw.text((x, y), text, font=font, fill=text_color)
    
    def generate_slide(self, slide_num: int, total_slides: int, 
                       content: SlideContent, request: CarouselRequest,
                       bg_image: Optional[Image.Image]) -> Image.Image:
        """Генерация одного слайда"""
        
        # Создаём фон
        canvas = self.create_background(bg_image, request.background_color, request.overlay_opacity)
        draw = ImageDraw.Draw(canvas)
        
        # Шрифты
        font_small = self.get_font(26)
        font_heading = self.get_font(request.font_size_heading, bold=True)
        font_text = self.get_font(request.font_size_text)
        font_highlight = self.get_font(request.font_size_heading, bold=True)
        
        # === Никнейм слева сверху ===
        draw.text((48, 48), request.username, font=font_small, fill=request.text_color)
        
        # === Нумерация справа сверху ===
        slide_text = f"{slide_num}/{total_slides}"
        bbox = draw.textbbox((0, 0), slide_text, font=font_small)
        text_width = bbox[2] - bbox[0]
        draw.text((CANVAS_WIDTH - 48 - text_width, 48), slide_text, font=font_small, fill=request.text_color)
        
        # === Основной контент ===
        content_y = 820  # Начало контента (нижняя треть)
        
        # Заголовок
        if content.heading:
            content_y = self.draw_text_with_wrap(
                draw, content.heading, 
                x=48, y=content_y,
                font=font_heading, color=request.text_color,
                max_width=CANVAS_WIDTH - 96,
                line_height=1.05
            )
            content_y += 20  # Отступ после заголовка
        
        # Обычный текст
        if content.text:
            content_y = self.draw_text_with_wrap(
                draw, content.text,
                x=48, y=content_y,
                font=font_text, color=request.text_color,
                max_width=CANVAS_WIDTH - 96,
                line_height=1.2
            )
            content_y += 20
        
        # Выделенный текст (жёлтый фон)
        if content.highlight:
            self.draw_highlight_text(
                canvas, content.highlight,
                x=48, y=content_y,
                font=font_highlight,
                text_color=request.highlight_text_color,
                bg_color=request.highlight_color,
                padding_x=16, padding_y=6
            )
        
        return canvas
    
    def generate_carousel(self, request: CarouselRequest) -> List[Image.Image]:
        """Генерация всей карусели"""
        images = []
        
        # Загружаем фото один раз
        bg_image = None
        if request.background_image:
            bg_image = self.load_background_image(request.background_image)
        
        total_slides = len(request.slides)
        
        for i, slide_content in enumerate(request.slides):
            img = self.generate_slide(
                slide_num=i + 1,
                total_slides=total_slides,
                content=slide_content,
                request=request,
                bg_image=bg_image
            )
            images.append(img)
        
        return images


generator = CarouselGenerator()


# ============== API ENDPOINTS ==============

@app.get("/")
async def root():
    return {"status": "ok", "service": "Carousel Generator API v2"}


@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.post("/generate", response_model=CarouselResponse)
async def generate_carousel(request: CarouselRequest):
    """
    Генерация карусели
    
    Пример запроса:
    {
        "background_image": "https://example.com/photo.jpg",
        "overlay_opacity": 40,
        "username": "@kamilgazizovv",
        "slides": [
            {
                "heading": "Теперь в\\nСаудовскую\\nАравию можно",
                "highlight": "без виз?"
            },
            {
                "heading": "Что произошло?",
                "text": "27 ноября правительство РФ одобрило проект соглашения"
            }
        ]
    }
    """
    try:
        images = generator.generate_carousel(request)
        
        results = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        for i, img in enumerate(images):
            # Сохраняем файл
            filename = f"carousel_{timestamp}_{i+1}.png"
            filepath = f"output/{filename}"
            img.save(filepath, "PNG", quality=95)
            
            # Конвертируем в base64
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            b64 = base64.b64encode(buffer.getvalue()).decode()
            
            results.append({
                "slide_number": i + 1,
                "filename": filename,
                "base64": f"data:image/png;base64,{b64}"
            })
        
        return CarouselResponse(
            success=True,
            message=f"Создано {len(images)} слайдов",
            slides=results
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-simple")
async def generate_simple(
    photo_url: str = "",
    photo_base64: str = "",
    username: str = "@kamilgazizovv",
    overlay: int = 40,
    slide1_heading: str = "",
    slide1_highlight: str = "",
    slide2_heading: str = "",
    slide2_text: str = "",
    slide3_heading: str = "",
    slide3_text: str = "",
    slide4_heading: str = "",
    slide4_text: str = "",
    slide5_heading: str = "",
    slide5_highlight: str = "",
):
    """
    Упрощённый эндпоинт для N8N (form-data)
    """
    slides = []
    
    slide_data = [
        (slide1_heading, "", slide1_highlight),
        (slide2_heading, slide2_text, ""),
        (slide3_heading, slide3_text, ""),
        (slide4_heading, slide4_text, ""),
        (slide5_heading, "", slide5_highlight),
    ]
    
    for heading, text, highlight in slide_data:
        if heading or text or highlight:
            slides.append(SlideContent(heading=heading, text=text, highlight=highlight or None))
    
    if not slides:
        raise HTTPException(status_code=400, detail="Нужен хотя бы один слайд")
    
    request = CarouselRequest(
        background_image=photo_url or photo_base64 or None,
        overlay_opacity=overlay,
        username=username,
        slides=slides
    )
    
    return await generate_carousel(request)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
