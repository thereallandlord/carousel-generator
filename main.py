"""Carousel Studio API v6 - Complete Edition
Сохраняет ВСЕ функции оригинала + добавляет MP4 генерацию
"""
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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
import uuid
import shutil
from datetime import datetime, timedelta
import subprocess

app = FastAPI(title="Carousel Studio", version="6.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# Directories (+ videos для MP4)
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("fonts", exist_ok=True)
os.makedirs("output", exist_ok=True)
os.makedirs("videos", exist_ok=True)  # НОВОЕ

CANVAS_W, CANVAS_H = 1080, 1350


class GenerateRequest(BaseModel):
    template_name: Optional[str] = None  # Старый формат
    template_id: Optional[str] = None    # Новый формат (алиас)
    USERNAME: Optional[str] = None       # Старый формат
    username: Optional[str] = None       # Новый формат (алиас)
    slides: Optional[List[Dict[str, Any]]] = None
    return_format: Optional[str] = "video"  # НОВОЕ: "base64" или "video"


class SlideData(BaseModel):
    slide: Dict[str, Any]
    settings: Dict[str, Any]
    slideNumber: int


class TemplateData(BaseModel):
    name: str
    settings: Dict[str, Any] = {}
    slides: List[Dict[str, Any]]
    createdAt: Optional[str] = None


class SlideRenderer:
    def __init__(self):
        self.font_cache = {}

    def get_font(self, family: str, size: int, weight: str = '400'):
        # ОРИГИНАЛ: Полная weight_map
        weight_map = {
            '300': 'Light', '400': 'Regular', '500': 'Medium',
            '600': 'SemiBold', '700': 'Bold', '800': 'ExtraBold', '900': 'Black'
        }
        weight_name = weight_map.get(str(weight), 'Regular')
        font_name = f"Inter-{weight_name}" if family == 'Inter' and weight_name != 'Regular' else family
        key = f"{font_name}_{size}"

        if key not in self.font_cache:
            paths = [
                f"fonts/{font_name}.otf",
                f"fonts/{font_name}.ttf",
                "fonts/Inter.otf",
                "fonts/Inter-Regular.otf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if weight_name in ['Bold', 'ExtraBold', 'Black', 'SemiBold'] else None,
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            ]
            for path in paths:
                if path and os.path.exists(path):
                    try:
                        self.font_cache[key] = ImageFont.truetype(path, size)
                        break
                    except:
                        continue

            if key not in self.font_cache:
                self.font_cache[key] = ImageFont.load_default()

        return self.font_cache[key]

    def load_image(self, source: str):
        # ОРИГИНАЛ: data: и http поддержка
        if not source:
            return None
        try:
            if source.startswith('data:'):
                _, data = source.split(',', 1)
                return Image.open(io.BytesIO(base64.b64decode(data)))
            elif source.startswith('http'):
                r = requests.get(source, timeout=30)
                r.raise_for_status()
                return Image.open(io.BytesIO(r.content))
        except Exception as e:
            print(f"Error loading image: {e}")
            return None

    def create_background(self, bg: dict) -> Image.Image:
        # ОРИГИНАЛ: Полная логика с фото, цветом, overlay, gradient
        color = bg.get('color', '#ffffff')
        try:
            r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        except:
            r, g, b = 255, 255, 255

        canvas = Image.new('RGB', (CANVAS_W, CANVAS_H), (r, g, b))

        # Фото фон
        if bg.get('type') == 'photo' and bg.get('photo'):
            img = self.load_image(bg['photo'])
            if img:
                pos = bg.get('photoPosition', {'x': 50, 'y': 50})
                zoom = max(1, bg.get('photoZoom', 1))

                img_ratio = img.width / img.height
                canvas_ratio = CANVAS_W / CANVAS_H

                if img_ratio > canvas_ratio:
                    base_height, base_width = CANVAS_H, int(CANVAS_H * img_ratio)
                else:
                    base_width, base_height = CANVAS_W, int(CANVAS_W / img_ratio)

                new_width, new_height = int(base_width * zoom), int(base_height * zoom)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                offset_x = int(max(0, new_width - CANVAS_W) * pos.get('x', 50) / 100)
                offset_y = int(max(0, new_height - CANVAS_H) * pos.get('y', 50) / 100)
                img = img.crop((offset_x, offset_y, offset_x + CANVAS_W, offset_y + CANVAS_H))

                if img.mode != 'RGB':
                    img = img.convert('RGB')
                canvas = img

        # Overlay (градиент или полный)
        overlay = bg.get('overlay', 0)
        if overlay > 0:
            canvas = canvas.convert('RGBA')
            if bg.get('overlayType') == 'gradient':
                gradient = Image.new('RGBA', (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
                draw = ImageDraw.Draw(gradient)
                for y in range(CANVAS_H):
                    if y / CANVAS_H > 0.4:
                        alpha = int(255 * overlay / 100 * ((y / CANVAS_H - 0.4) / 0.6))
                        draw.line([(0, y), (CANVAS_W, y)], fill=(0, 0, 0, alpha))
                canvas = Image.alpha_composite(canvas, gradient)
            else:
                canvas = Image.alpha_composite(canvas, Image.new('RGBA', (CANVAS_W, CANVAS_H), (0, 0, 0, int(255 * overlay / 100))))
            canvas = canvas.convert('RGB')

        return canvas

    def parse_color(self, color: str):
        if not color:
            return (0, 0, 0)
        color = color.lstrip('#')
        try:
            return tuple(int(color[i:i+2], 16) for i in (0, 2, 4))
        except:
            return (0, 0, 0)

    def draw_photo_element(self, canvas: Image.Image, el: dict):
        # ОРИГИНАЛ: Фото элементы с border-radius
        if not el.get('photo'):
            return

        img = self.load_image(el['photo'])
        if not img:
            return

        x, y = int(el.get('x', 0)), int(el.get('y', 0))
        w, h = int(el.get('width', 300)), int(el.get('height', 300))
        border_radius = el.get('borderRadius', 0)

        img_ratio = img.width / img.height
        el_ratio = w / h

        if img_ratio > el_ratio:
            new_h = h
            new_w = int(h * img_ratio)
        else:
            new_w = w
            new_h = int(w / img_ratio)

        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        left = (new_w - w) // 2
        top = (new_h - h) // 2
        img = img.crop((left, top, left + w, top + h))

        if border_radius > 0:
            img = img.convert('RGBA')
            mask = Image.new('L', (w, h), 0)
            draw = ImageDraw.Draw(mask)
            radius = int(min(w, h) * border_radius / 100)
            draw.rounded_rectangle([0, 0, w, h], radius=radius, fill=255)
            img.putalpha(mask)
            canvas.paste(img, (x, y), img)
        else:
            if img.mode == 'RGBA':
                canvas.paste(img, (x, y), img)
            else:
                canvas.paste(img, (x, y))

    def draw_text_element(self, canvas: Image.Image, el: dict, settings: dict, slide_num: int, total_slides: int, username_override: str = None):
        # ОРИГИНАЛ: Полная логика текста с подсветкой, переносом, выравниванием
        draw = ImageDraw.Draw(canvas)
        content = el.get('content', '')

        x, y = int(el.get('x', 0)), int(el.get('y', 0))
        font_family = el.get('fontFamily', 'Inter')
        font_size = int(el.get('fontSize', 48))
        font_weight = str(el.get('fontWeight', '400'))
        color = el.get('color', '#000000')
        highlight_color = el.get('highlightColor', '#c8ff00')
        opacity = float(el.get('opacity', 100)) / 100
        line_height = float(el.get('lineHeight', 1.2))
        max_width = int(el.get('maxWidth')) if el.get('maxWidth') else None
        align = el.get('align', 'left')

        # USERNAME и SLIDENUM (ОРИГИНАЛ)
        if el.get('type') == 'username':
            content = username_override or settings.get('username', '@username')
        elif el.get('type') == 'slidenum':
            # НОВОЕ: автоподсчёт вместо фиксированного
            content = f"{slide_num}/{total_slides}"

        font = self.get_font(font_family, font_size, font_weight)

        base_color = self.parse_color(color)
        hl_color = self.parse_color(highlight_color)

        if opacity < 1:
            base_color = tuple(int(c * opacity) for c in base_color)
            hl_color = tuple(int(c * opacity) for c in hl_color)

        # Парсинг *подсветки* (ОРИГИНАЛ)
        segments = []
        pattern = r'\*([^*]+)\*'
        last_end = 0
        for match in re.finditer(pattern, content):
            if match.start() > last_end:
                segments.append({'text': content[last_end:match.start()], 'hl': False})
            segments.append({'text': match.group(1), 'hl': True})
            last_end = match.end()
        if last_end < len(content):
            segments.append({'text': content[last_end:], 'hl': False})

        if not segments:
            segments = [{'text': content, 'hl': False}]

        # Word wrap (ОРИГИНАЛ)
        lines = []
        current_words, current_segs = [], []
        for seg in segments:
            for pi, part in enumerate(seg['text'].split('\n')):
                if pi > 0:
                    lines.append(current_segs)
                    current_words, current_segs = [], []
                for word in part.split(' '):
                    if not word:
                        continue
                    test = ' '.join(current_words + [word])
                    bbox = draw.textbbox((0, 0), test, font=font)
                    if max_width and bbox[2] - bbox[0] > max_width and current_words:
                        lines.append(current_segs)
                        current_words, current_segs = [word], [{'text': word, 'hl': seg['hl']}]
                    else:
                        if current_words:
                            current_segs.append({'text': ' ', 'hl': False})
                        current_words.append(word)
                        current_segs.append({'text': word, 'hl': seg['hl']})
        if current_segs:
            lines.append(current_segs)

        # Рендеринг с выравниванием (ОРИГИНАЛ)
        curr_y = y
        for line_segs in lines:
            if not line_segs:
                curr_y += int(font_size * line_height)
                continue

            line_text = ''.join(s['text'] for s in line_segs)
            bbox = draw.textbbox((0, 0), line_text, font=font)
            line_w = bbox[2] - bbox[0]

            curr_x = x - line_w if align == 'right' else (x - line_w // 2 if align == 'center' else x)

            for seg in line_segs:
                col = hl_color if seg['hl'] else base_color
                draw.text((curr_x, curr_y), seg['text'], font=font, fill=col)
                bbox = draw.textbbox((0, 0), seg['text'], font=font)
                curr_x += bbox[2] - bbox[0]

            curr_y += int(font_size * line_height)

    def render_slide(self, slide: dict, settings: dict, slide_num: int, total_slides: int, username_override: str = None) -> Image.Image:
        canvas = self.create_background(slide.get('background', {}))

        for el in slide.get('elements', []):
            if el.get('type') == 'photo':
                self.draw_photo_element(canvas, el)
            else:
                self.draw_text_element(canvas, el, settings, slide_num, total_slides, username_override)

        return canvas


renderer = SlideRenderer()

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "6.0",
        "fonts": os.listdir("fonts") if os.path.exists("fonts") else []
    }


# НОВОЕ: cleanup old videos
def cleanup_old_videos():
    """Удаляет видео старше 24 часов"""
    now = datetime.now()
    if not os.path.exists("videos"):
        return
    for filename in os.listdir("videos"):
        filepath = os.path.join("videos", filename)
        if os.path.isfile(filepath):
            file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
            if now - file_time > timedelta(hours=24):
                try:
                    os.remove(filepath)
                    print(f"Deleted old video: {filename}")
                except Exception as e:
                    print(f"Error deleting {filename}: {e}")


@app.get("/templates")
async def list_templates():
    # ОРИГИНАЛ
    templates = []
    for f in os.listdir("templates"):
        if f.endswith('.json'):
            try:
                with open(f"templates/{f}", 'r', encoding='utf-8') as file:
                    d = json.load(file)
                    templates.append({
                        "name": d.get('name', f.replace('.json', '')),
                        "createdAt": d.get('createdAt', ''),
                        "slidesCount": len(d.get('slides', []))
                    })
            except:
                pass
    return {"templates": templates}


@app.get("/templates/{name}")
async def get_template(name: str):
    # ОРИГИНАЛ
    safe = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', name)
    path = f"templates/{safe}.json"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


@app.post("/templates")
async def save_template(template: TemplateData):
    # ОРИГИНАЛ
    t = template.dict()
    t['createdAt'] = datetime.now().isoformat()
    safe = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', template.name)
    with open(f"templates/{safe}.json", 'w', encoding='utf-8') as f:
        json.dump(t, f, ensure_ascii=False, indent=2)
    return {"success": True, "name": template.name}


@app.delete("/templates/{name}")
async def delete_template(name: str):
    # ОРИГИНАЛ
    safe = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', name)
    path = f"templates/{safe}.json"
    if os.path.exists(path):
        os.remove(path)
        return {"success": True}
    raise HTTPException(status_code=404, detail="Not found")


@app.post("/render-slide")
async def render_slide(data: SlideData):
    # ОРИГИНАЛ
    try:
        img = renderer.render_slide(data.slide, data.settings, data.slideNumber, 10)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return {
            "success": True,
            "base64": f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate")
async def generate_carousel(request: GenerateRequest):
    """
    ПОЛНАЯ генерация с поддержкой:
    - Старый формат (template_name, USERNAME, base64)
    - Новый формат (template_id, username, video URL)
    - {VARNAME}_COLOR
    - PHOTO для background
    - Intro/Content/Ending
    """
    cleanup_old_videos()

    # Поддержка старого и нового API
    template_name = request.template_id or request.template_name
    username = request.username or request.USERNAME or "@username"

    if not template_name:
        raise HTTPException(status_code=400, detail="template_name or template_id required")

    # Загружаем шаблон
    safe = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', template_name)
    path = f"templates/{safe}.json"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")

    with open(path, 'r', encoding='utf-8') as f:
        template = json.load(f)

    settings = template.get('settings', {})
    slides = template.get('slides', [])

    result_slides = []

    if request.slides:
        # ОРИГИНАЛЬНАЯ ЛОГИКА (полностью сохранена!)
        intro_slides = [s for s in slides if s.get('type') == 'intro']
        content_slides = [s for s in slides if s.get('type') == 'content']
        ending_slides = [s for s in slides if s.get('type') == 'ending']

        if not content_slides:
            content_slides = [s for s in slides if s.get('type') != 'ending']

        total_slides = len(request.slides) + len(intro_slides) + len(ending_slides)

        for i, slide_vars in enumerate(request.slides):
            # Выбираем тип слайда (ОРИГИНАЛ)
            if i == 0 and intro_slides:
                base_slide = intro_slides[0]
            elif ending_slides and i >= len(request.slides) - len(ending_slides):
                idx = i - (len(request.slides) - len(ending_slides))
                base_slide = ending_slides[idx] if idx < len(ending_slides) else content_slides[0]
            else:
                base_slide = content_slides[0]

            # Deep copy (ОРИГИНАЛ)
            slide = json.loads(json.dumps(base_slide))

            # ОРИГИНАЛ: PHOTO для background (КРИТИЧНО!)
            if 'PHOTO' in slide_vars and slide.get('background', {}).get('type') == 'photo':
                slide['background']['photo'] = slide_vars['PHOTO']

            # Применяем varName (ОРИГИНАЛ + улучшения)
            for el in slide.get('elements', []):
                var_name = el.get('varName', '')

                if not var_name:
                    continue

                # Поиск значения (case-insensitive)
                value = None
                color_value = None

                for key, val in slide_vars.items():
                    if key.upper() == var_name.upper():
                        value = val
                    # ОРИГИНАЛ: {VARNAME}_COLOR (КРИТИЧНО!)
                    elif key.upper() == f"{var_name.upper()}_COLOR":
                        color_value = val

                if value is not None:
                    if el.get('type') == 'photo':
                        el['photo'] = value
                    else:
                        el['content'] = value

                # Применяем кастомный цвет подсветки
                if color_value:
                    el['highlightColor'] = color_value

            result_slides.append(slide)
    else:
        # Без переменных
        result_slides = slides
        total_slides = len(slides)

    # Рендерим слайды
    rendered = []
    temp_dir = None

    for i, slide in enumerate(result_slides):
        img = renderer.render_slide(slide, settings, i + 1, total_slides, username)

        # Формат вывода
        if request.return_format == "video":
            # НОВОЕ: Генерация MP4
            if temp_dir is None:
                video_id = str(uuid.uuid4())[:8]
                temp_dir = f"output/{video_id}"
                os.makedirs(temp_dir, exist_ok=True)

            img.save(f"{temp_dir}/slide_{i:03d}.png", "PNG")
        else:
            # ОРИГИНАЛ: base64 + filename
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()

            filename = f"slide_{i+1}_{uuid.uuid4().hex[:8]}.png"
            with open(f"output/{filename}", 'wb') as f:
                f.write(buf.getvalue())

            rendered.append({
                "slide_number": i + 1,
                "base64": f"data:image/png;base64,{b64}",
                "filename": filename
            })

    # НОВОЕ: Генерация MP4
    if request.return_format == "video" and temp_dir:
        video_path = f"videos/{video_id}.mp4"

        try:
            cmd = [
                "ffmpeg",
                "-framerate", "1/3",
                "-i", f"{temp_dir}/slide_%03d.png",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-y",
                video_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                raise Exception(f"FFmpeg error: {result.stderr}")

        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="FFmpeg not installed")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Video generation failed: {str(e)}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        base_url = os.getenv("RAILWAY_PUBLIC_DOMAIN", "localhost:8000")
        if not base_url.startswith("http"):
            base_url = f"https://{base_url}"

        return {
            "success": True,
            "url": f"{base_url}/video/{video_id}",
            "slides_count": total_slides,
            "expires_in": "24h"
        }
    else:
        # ОРИГИНАЛ: Возврат base64
        return {
            "success": True,
            "slides": rendered
        }


@app.get("/video/{video_id}")
async def get_video(video_id: str):
    """НОВОЕ: Отдаёт видео по ID"""
    safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '', video_id)
    video_path = f"videos/{safe_id}.mp4"

    if not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="Video not found or expired")

    return FileResponse(
        video_path,
        media_type="video/mp4",
        headers={"Content-Disposition": f"inline; filename=carousel_{safe_id}.mp4"}
    )


@app.get("/output/{filename}")
async def get_output(filename: str):
    # ОРИГИНАЛ
    path = f"output/{filename}"
    if os.path.exists(path):
        return FileResponse(path, media_type="image/png")
    raise HTTPException(status_code=404, detail="Not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
