"""
Carousel Generator API v3
=========================
Полноценный генератор с визуальным редактором и шаблонами

Запуск:
  pip install -r requirements.txt
  python main.py

Веб-редактор: http://localhost:8000/
API: http://localhost:8000/docs
"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from PIL import Image, ImageDraw, ImageFont
import requests
import base64
import json
import os
import io
import re
from datetime import datetime

app = FastAPI(title="Carousel Generator", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Директории
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("fonts", exist_ok=True)
os.makedirs("output", exist_ok=True)

# Монтируем статику
app.mount("/static", StaticFiles(directory="static"), name="static")

# Размеры
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1350


# ============== MODELS ==============

class GenerateRequest(BaseModel):
    """Запрос на генерацию карусели"""
    template_name: str
    variables: Dict[str, str] = {}
    # Например: {"PHOTO": "https://...", "TITLE": "Заголовок", "CONTENT": "Текст"}


class TemplateData(BaseModel):
    """Данные шаблона"""
    name: str
    username: str = "@kamilgazizovv"
    totalSlides: int = 10
    slides: List[Dict[str, Any]]
    createdAt: Optional[str] = None


# ============== GENERATOR ==============

class CarouselGenerator:
    def __init__(self):
        self.font_cache = {}
        
    def get_font(self, family: str, size: int, weight: str = "400") -> ImageFont.FreeTypeFont:
        """Получить шрифт"""
        # Маппинг весов на файлы
        weight_map = {
            "300": "Light",
            "400": "Regular",
            "500": "Medium",
            "600": "SemiBold",
            "700": "Bold",
            "800": "ExtraBold",
            "900": "Black",
        }
        
        weight_name = weight_map.get(str(weight), "Regular")
        key = f"{family}_{size}_{weight_name}"
        
        if key not in self.font_cache:
            # Пробуем разные пути
            paths = [
                f"fonts/{family}-{weight_name}.ttf",
                f"fonts/{family}_{weight_name}.ttf",
                f"fonts/{family.replace(' ', '')}-{weight_name}.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ]
            
            if weight_name in ["Bold", "ExtraBold", "Black", "SemiBold"]:
                paths.insert(0, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
            
            for path in paths:
                if os.path.exists(path):
                    try:
                        self.font_cache[key] = ImageFont.truetype(path, size)
                        break
                    except:
                        continue
            
            if key not in self.font_cache:
                self.font_cache[key] = ImageFont.load_default()
        
        return self.font_cache[key]
    
    def load_image(self, source: str) -> Optional[Image.Image]:
        """Загрузить изображение из URL или base64"""
        if not source:
            return None
            
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
        except Exception as e:
            print(f"Error loading image: {e}")
        return None
    
    def create_background(self, slide_bg: dict, variables: dict) -> Image.Image:
        """Создать фон слайда"""
        bg_type = slide_bg.get('type', 'color')
        bg_color = slide_bg.get('color', '#ffffff')
        bg_image = slide_bg.get('image', '')
        overlay = slide_bg.get('overlay', 0)
        overlay_type = slide_bg.get('overlayType', 'full')
        
        # Заменяем переменные
        if bg_image.startswith('{') and bg_image.endswith('}'):
            var_name = bg_image[1:-1]
            bg_image = variables.get(var_name, '')
        
        # Создаём canvas
        canvas = Image.new('RGB', (CANVAS_WIDTH, CANVAS_HEIGHT), bg_color)
        
        # Если есть изображение
        if bg_type == 'image' and bg_image:
            img = self.load_image(bg_image)
            if img:
                # Cover - масштабируем и обрезаем
                img_ratio = img.width / img.height
                canvas_ratio = CANVAS_WIDTH / CANVAS_HEIGHT
                
                if img_ratio > canvas_ratio:
                    new_height = CANVAS_HEIGHT
                    new_width = int(new_height * img_ratio)
                else:
                    new_width = CANVAS_WIDTH
                    new_height = int(new_width / img_ratio)
                
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                # Центрируем
                left = (new_width - CANVAS_WIDTH) // 2
                top = (new_height - CANVAS_HEIGHT) // 2
                img = img.crop((left, top, left + CANVAS_WIDTH, top + CANVAS_HEIGHT))
                
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                canvas = img
                
                # Затемнение
                if overlay > 0:
                    canvas = canvas.convert('RGBA')
                    
                    if overlay_type == 'gradient':
                        # Градиент снизу вверх
                        gradient = Image.new('RGBA', (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
                        draw = ImageDraw.Draw(gradient)
                        
                        for y in range(CANVAS_HEIGHT):
                            # Градиент от 60% высоты до низа
                            if y > CANVAS_HEIGHT * 0.4:
                                progress = (y - CANVAS_HEIGHT * 0.4) / (CANVAS_HEIGHT * 0.6)
                                alpha = int(255 * overlay / 100 * progress)
                                draw.line([(0, y), (CANVAS_WIDTH, y)], fill=(0, 0, 0, alpha))
                        
                        canvas = Image.alpha_composite(canvas, gradient)
                    else:
                        # Полное затемнение
                        overlay_layer = Image.new('RGBA', (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, int(255 * overlay / 100)))
                        canvas = Image.alpha_composite(canvas, overlay_layer)
                    
                    canvas = canvas.convert('RGB')
        
        return canvas
    
    def draw_text_element(self, canvas: Image.Image, element: dict, variables: dict, 
                         slide_num: int, total_slides: int, username: str):
        """Отрисовать текстовый элемент"""
        draw = ImageDraw.Draw(canvas)
        
        content = element.get('content', '')
        x = element.get('x', 0)
        y = element.get('y', 0)
        font_family = element.get('fontFamily', 'Inter')
        font_size = element.get('fontSize', 48)
        font_weight = element.get('fontWeight', '400')
        color = element.get('color', '#000000')
        text_align = element.get('textAlign', 'left')
        line_height = element.get('lineHeight', 1.2)
        max_width = element.get('maxWidth')
        
        # Заменяем переменные
        content = content.replace('{USERNAME}', username)
        content = content.replace('{SLIDE_NUM}', f"{slide_num}/{total_slides}")
        
        # Заменяем кастомные переменные
        for var_name, var_value in variables.items():
            content = content.replace(f'{{{var_name}}}', str(var_value))
        
        # Если после замены остались переменные в {} — пропускаем элемент
        if re.search(r'\{[^}]+\}', content):
            return
        
        font = self.get_font(font_family, font_size, font_weight)
        
        # Перенос текста
        lines = []
        for paragraph in content.split('\n'):
            if max_width:
                words = paragraph.split()
                current_line = []
                
                for word in words:
                    current_line.append(word)
                    test = ' '.join(current_line)
                    bbox = draw.textbbox((0, 0), test, font=font)
                    
                    if bbox[2] > max_width and len(current_line) > 1:
                        current_line.pop()
                        lines.append(' '.join(current_line))
                        current_line = [word]
                
                if current_line:
                    lines.append(' '.join(current_line))
            else:
                lines.append(paragraph)
        
        # Отрисовка
        current_y = y
        for line in lines:
            line_x = x
            
            if text_align in ['center', 'right']:
                bbox = draw.textbbox((0, 0), line, font=font)
                line_width = bbox[2] - bbox[0]
                
                if text_align == 'right':
                    line_x = x - line_width
                elif text_align == 'center':
                    line_x = x - line_width // 2
            
            draw.text((line_x, current_y), line, font=font, fill=color)
            current_y += int(font_size * line_height)
    
    def draw_highlight_element(self, canvas: Image.Image, element: dict, variables: dict,
                               slide_num: int, total_slides: int, username: str):
        """Отрисовать выделенный текст с фоном"""
        draw = ImageDraw.Draw(canvas)
        
        content = element.get('content', '')
        x = element.get('x', 0)
        y = element.get('y', 0)
        font_family = element.get('fontFamily', 'Inter')
        font_size = element.get('fontSize', 64)
        font_weight = element.get('fontWeight', '700')
        color = element.get('color', '#000000')
        bg_color = element.get('bgColor', '#c8ff00')
        padding_x = element.get('paddingX', 16)
        padding_y = element.get('paddingY', 6)
        
        # Заменяем переменные
        content = content.replace('{USERNAME}', username)
        content = content.replace('{SLIDE_NUM}', f"{slide_num}/{total_slides}")
        
        for var_name, var_value in variables.items():
            content = content.replace(f'{{{var_name}}}', str(var_value))
        
        if re.search(r'\{[^}]+\}', content):
            return
        
        font = self.get_font(font_family, font_size, font_weight)
        
        # Размер текста
        bbox = draw.textbbox((0, 0), content, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # Фон
        draw.rectangle([
            x - padding_x,
            y - padding_y,
            x + text_width + padding_x,
            y + text_height + padding_y
        ], fill=bg_color)
        
        # Текст
        draw.text((x, y), content, font=font, fill=color)
    
    def generate_slide(self, slide: dict, variables: dict, slide_num: int, 
                       total_slides: int, username: str) -> Image.Image:
        """Генерация одного слайда"""
        # Фон
        canvas = self.create_background(slide.get('background', {}), variables)
        
        # Элементы
        for element in slide.get('elements', []):
            el_type = element.get('type', 'text')
            
            if el_type == 'highlight':
                self.draw_highlight_element(canvas, element, variables, slide_num, total_slides, username)
            else:
                self.draw_text_element(canvas, element, variables, slide_num, total_slides, username)
        
        return canvas
    
    def generate(self, template: dict, variables: dict) -> List[Image.Image]:
        """Генерация всей карусели"""
        images = []
        
        username = template.get('username', '@kamilgazizovv')
        total_slides = template.get('totalSlides', len(template.get('slides', [])))
        
        for i, slide in enumerate(template.get('slides', [])):
            img = self.generate_slide(slide, variables, i + 1, total_slides, username)
            images.append(img)
        
        return images


generator = CarouselGenerator()


# ============== ROUTES ==============

@app.get("/")
async def index():
    """Веб-редактор"""
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    return {"status": "healthy", "version": "3.0", "timestamp": datetime.now().isoformat()}


# Templates CRUD

@app.get("/templates")
async def list_templates():
    """Список шаблонов"""
    templates = []
    
    for filename in os.listdir("templates"):
        if filename.endswith('.json'):
            try:
                with open(f"templates/{filename}", 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    templates.append({
                        "name": data.get('name', filename.replace('.json', '')),
                        "createdAt": data.get('createdAt', ''),
                        "slidesCount": len(data.get('slides', [])),
                    })
            except:
                pass
    
    return {"templates": templates}


@app.get("/templates/{name}")
async def get_template(name: str):
    """Получить шаблон"""
    path = f"templates/{name}.json"
    
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Template not found")
    
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


@app.post("/templates")
async def save_template(template: TemplateData):
    """Сохранить шаблон"""
    template_dict = template.dict()
    template_dict['createdAt'] = datetime.now().isoformat()
    
    # Безопасное имя файла
    safe_name = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', template.name)
    path = f"templates/{safe_name}.json"
    
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(template_dict, f, ensure_ascii=False, indent=2)
    
    return {"success": True, "name": template.name}


@app.delete("/templates/{name}")
async def delete_template(name: str):
    """Удалить шаблон"""
    path = f"templates/{name}.json"
    
    if os.path.exists(path):
        os.remove(path)
        return {"success": True}
    
    raise HTTPException(status_code=404, detail="Template not found")


# Generation

@app.post("/generate")
async def generate_carousel(request: GenerateRequest):
    """
    Генерация карусели из шаблона
    
    Пример:
    {
        "template_name": "Мой шаблон",
        "variables": {
            "PHOTO": "https://example.com/photo.jpg",
            "TITLE": "Заголовок",
            "CONTENT": "Текст контента",
            "HIGHLIGHT": "Выделение"
        }
    }
    """
    # Загружаем шаблон
    safe_name = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', request.template_name)
    path = f"templates/{safe_name}.json"
    
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Template '{request.template_name}' not found")
    
    with open(path, 'r', encoding='utf-8') as f:
        template = json.load(f)
    
    # Генерируем
    try:
        images = generator.generate(template, request.variables)
        
        results = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        for i, img in enumerate(images):
            filename = f"carousel_{timestamp}_{i+1}.png"
            filepath = f"output/{filename}"
            img.save(filepath, "PNG", quality=95)
            
            # Base64
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            b64 = base64.b64encode(buffer.getvalue()).decode()
            
            results.append({
                "slide_number": i + 1,
                "filename": filename,
                "url": f"/output/{filename}",
                "base64": f"data:image/png;base64,{b64}"
            })
        
        return {
            "success": True,
            "message": f"Создано {len(images)} слайдов",
            "slides": results
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/output/{filename}")
async def get_output(filename: str):
    """Получить сгенерированный файл"""
    path = f"output/{filename}"
    if os.path.exists(path):
        return FileResponse(path, media_type="image/png")
    raise HTTPException(status_code=404, detail="File not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
