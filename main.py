"""Carousel Studio API v6.1
Правильная логика: Content посты + Ending слайды из шаблона
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

app = FastAPI(title="Carousel Studio", version="6.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("fonts", exist_ok=True)
os.makedirs("output", exist_ok=True)

CANVAS_W, CANVAS_H = 1080, 1350


class GenerateRequest(BaseModel):
    template_name: Optional[str] = None
    template_id: Optional[str] = None
    USERNAME: Optional[str] = None
    username: Optional[str] = None
    slides: Optional[List[Dict[str, Any]]] = None


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
        color = bg.get('color', '#ffffff')
        try:
            r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        except:
            r, g, b = 255, 255, 255

        canvas = Image.new('RGB', (CANVAS_W, CANVAS_H), (r, g, b))

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

        if el.get('type') == 'username':
            content = username_override or settings.get('username', '@username')
        elif el.get('type') == 'slidenum':
            content = f"{slide_num}/{total_slides}"

        font = self.get_font(font_family, font_size, font_weight)

        base_color = self.parse_color(color)
        hl_color = self.parse_color(highlight_color)

        if opacity < 1:
            base_color = tuple(int(c * opacity) for c in base_color)
            hl_color = tuple(int(c * opacity) for c in hl_color)

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
        "version": "6.1",
        "fonts": os.listdir("fonts") if os.path.exists("fonts") else []
    }


@app.get("/templates")
async def list_templates():
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
    safe = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', name)
    path = f"templates/{safe}.json"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


@app.post("/templates")
async def save_template(template: TemplateData):
    from datetime import datetime
    t = template.dict()
    t['createdAt'] = datetime.now().isoformat()
    safe = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', template.name)
    with open(f"templates/{safe}.json", 'w', encoding='utf-8') as f:
        json.dump(t, f, ensure_ascii=False, indent=2)
    return {"success": True, "name": template.name}


@app.delete("/templates/{name}")
async def delete_template(name: str):
    safe = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', name)
    path = f"templates/{safe}.json"
    if os.path.exists(path):
        os.remove(path)
        return {"success": True}
    raise HTTPException(status_code=404, detail="Not found")


@app.post("/render-slide")
async def render_slide(data: SlideData):
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
    ПРАВИЛЬНАЯ генерация:
    1. Рендерим посты из request.slides (с подстановкой данных)
    2. Добавляем ending слайды из шаблона (как есть)
    """
    template_name = request.template_id or request.template_name
    username = request.username or request.USERNAME or "@username"

    if not template_name:
        raise HTTPException(status_code=400, detail="template_name or template_id required")

    safe = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', template_name)
    path = f"templates/{safe}.json"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")

    with open(path, 'r', encoding='utf-8') as f:
        template = json.load(f)

    settings = template.get('settings', {})
    slides = template.get('slides', [])

    # Находим шаблоны
    content_slides = [s for s in slides if s.get('type') == 'content']
    ending_slides = [s for s in slides if s.get('type') == 'ending']

    if not content_slides:
        content_slides = [s for s in slides if s.get('type') != 'ending']

    content_template = content_slides[0] if content_slides else slides[0]

    result_slides = []

    if request.slides:
        # Считаем ПРАВИЛЬНО
        total_slides = len(request.slides) + len(ending_slides)

        # 1. Рендерим посты (с подстановкой данных)
        for i, slide_vars in enumerate(request.slides):
            slide = json.loads(json.dumps(content_template))

            # PHOTO для background
            if 'PHOTO' in slide_vars and slide.get('background', {}).get('type') == 'photo':
                slide['background']['photo'] = slide_vars['PHOTO']

            # varName + {VARNAME}_COLOR
            for el in slide.get('elements', []):
                var_name = el.get('varName', '')

                if not var_name:
                    continue

                value = None
                color_value = None

                for key, val in slide_vars.items():
                    if key.upper() == var_name.upper():
                        value = val
                    elif key.upper() == f"{var_name.upper()}_COLOR":
                        color_value = val

                if value is not None:
                    if el.get('type') == 'photo':
                        el['photo'] = value
                    else:
                        el['content'] = value

                if color_value:
                    el['highlightColor'] = color_value

            result_slides.append(slide)

        # 2. Добавляем ending слайды (КАК ЕСТЬ из шаблона)
        for ending_slide in ending_slides:
            result_slides.append(json.loads(json.dumps(ending_slide)))
    else:
        result_slides = slides
        total_slides = len(slides)

    # Рендерим все слайды
    rendered = []

    for i, slide in enumerate(result_slides):
        img = renderer.render_slide(slide, settings, i + 1, total_slides, username)

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

    return {
        "success": True,
        "slides": rendered
    }


@app.get("/output/{filename}")
async def get_output(filename: str):
    path = f"output/{filename}"
    if os.path.exists(path):
        return FileResponse(path, media_type="image/png")
    raise HTTPException(status_code=404, detail="Not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
