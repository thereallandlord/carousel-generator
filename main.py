"""
Carousel Studio API v4
======================
Полноценный генератор каруселей с визуальным редактором

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

app = FastAPI(title="Carousel Studio", version="4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directories
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("fonts", exist_ok=True)
os.makedirs("output", exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Canvas dimensions
CANVAS_W = 1080
CANVAS_H = 1350


# ==================== MODELS ====================

class SlideData(BaseModel):
    slide: Dict[str, Any]
    settings: Dict[str, Any]
    slideNumber: int


class GenerateRequest(BaseModel):
    template_name: str
    slides: Optional[List[Dict[str, Any]]] = None


class TemplateData(BaseModel):
    name: str
    settings: Dict[str, Any] = {}
    slides: List[Dict[str, Any]]
    createdAt: Optional[str] = None


# ==================== GENERATOR ====================

class SlideRenderer:
    def __init__(self):
        self.font_cache = {}
    
    def get_font(self, family: str, size: int, weight: str = '400') -> ImageFont.FreeTypeFont:
        """Get font with caching"""
        weight_map = {
            '300': 'Light', '400': 'Regular', '500': 'Medium',
            '600': 'SemiBold', '700': 'Bold', '800': 'ExtraBold', '900': 'Black',
        }
        weight_name = weight_map.get(str(weight), 'Regular')
        key = f"{family}_{size}_{weight_name}"
        
        if key not in self.font_cache:
            paths = [
                f"fonts/{family}-{weight_name}.ttf",
                f"fonts/{family.replace(' ', '')}-{weight_name}.ttf",
            ]
            
            # Fallback to system fonts
            if weight_name in ['Bold', 'ExtraBold', 'Black', 'SemiBold']:
                paths.append("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
            paths.append("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
            
            for path in paths:
                if os.path.exists(path):
                    try:
                        self.font_cache[key] = ImageFont.truetype(path, size)
                        break
                    except Exception:
                        continue
            
            if key not in self.font_cache:
                self.font_cache[key] = ImageFont.load_default()
        
        return self.font_cache[key]
    
    def load_image(self, source: str) -> Optional[Image.Image]:
        """Load image from URL or base64"""
        if not source:
            return None
        
        try:
            if source.startswith('data:'):
                header, data = source.split(',', 1)
                img_data = base64.b64decode(data)
                return Image.open(io.BytesIO(img_data))
            elif source.startswith('http'):
                response = requests.get(source, timeout=30)
                response.raise_for_status()
                return Image.open(io.BytesIO(response.content))
        except Exception as e:
            print(f"Error loading image: {e}")
        return None
    
    def create_background(self, bg: dict) -> Image.Image:
        """Create slide background"""
        canvas = Image.new('RGB', (CANVAS_W, CANVAS_H), bg.get('color', '#ffffff'))
        
        if bg.get('type') == 'photo' and bg.get('photo'):
            img = self.load_image(bg['photo'])
            if img:
                # Get position and zoom
                pos = bg.get('photoPosition', {'x': 50, 'y': 50})
                zoom = bg.get('photoZoom', 1)
                
                # Calculate dimensions
                img_ratio = img.width / img.height
                canvas_ratio = CANVAS_W / CANVAS_H
                
                if img_ratio > canvas_ratio:
                    new_height = int(CANVAS_H * zoom)
                    new_width = int(new_height * img_ratio)
                else:
                    new_width = int(CANVAS_W * zoom)
                    new_height = int(new_width / img_ratio)
                
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                # Position based on percentage
                max_offset_x = max(0, new_width - CANVAS_W)
                max_offset_y = max(0, new_height - CANVAS_H)
                
                offset_x = int(max_offset_x * pos['x'] / 100)
                offset_y = int(max_offset_y * pos['y'] / 100)
                
                img = img.crop((offset_x, offset_y, offset_x + CANVAS_W, offset_y + CANVAS_H))
                
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                canvas = img
                
                # Apply overlay
                overlay = bg.get('overlay', 0)
                if overlay > 0:
                    canvas = canvas.convert('RGBA')
                    overlay_type = bg.get('overlayType', 'full')
                    
                    if overlay_type == 'gradient':
                        gradient = Image.new('RGBA', (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
                        draw = ImageDraw.Draw(gradient)
                        
                        for y in range(CANVAS_H):
                            if y > CANVAS_H * 0.4:
                                progress = (y - CANVAS_H * 0.4) / (CANVAS_H * 0.6)
                                alpha = int(255 * overlay / 100 * progress)
                                draw.line([(0, y), (CANVAS_W, y)], fill=(0, 0, 0, alpha))
                        
                        canvas = Image.alpha_composite(canvas, gradient)
                    else:
                        overlay_layer = Image.new('RGBA', (CANVAS_W, CANVAS_H), (0, 0, 0, int(255 * overlay / 100)))
                        canvas = Image.alpha_composite(canvas, overlay_layer)
                    
                    canvas = canvas.convert('RGB')
        
        return canvas
    
    def parse_color(self, color: str) -> tuple:
        """Parse hex color to RGB tuple"""
        color = color.lstrip('#')
        return tuple(int(color[i:i+2], 16) for i in (0, 2, 4))
    
    def draw_text_element(self, img: Image.Image, el: dict, settings: dict, slide_num: int):
        """Draw text element with highlight support"""
        draw = ImageDraw.Draw(img)
        
        content = el.get('content', '')
        x = el.get('x', 0)
        y = el.get('y', 0)
        font_family = el.get('fontFamily', 'Inter')
        font_size = el.get('fontSize', 48)
        font_weight = el.get('fontWeight', '400')
        color = el.get('color', '#000000')
        highlight_color = el.get('highlightColor', '#c8ff00')
        opacity = el.get('opacity', 100) / 100
        line_height = el.get('lineHeight', 1.2)
        max_width = el.get('maxWidth')
        align = el.get('align', 'left')
        
        # Replace system values
        if el.get('type') == 'username':
            content = settings.get('username', '@username')
        elif el.get('type') == 'slidenum':
            content = f"{slide_num}/{settings.get('totalSlides', 10)}"
        
        font = self.get_font(font_family, font_size, font_weight)
        base_color = self.parse_color(color)
        hl_color = self.parse_color(highlight_color)
        
        # Apply opacity
        base_color = tuple(int(c * opacity) for c in base_color)
        hl_color = tuple(int(c * opacity) for c in hl_color)
        
        # Split content into segments (normal and highlighted)
        segments = []
        pattern = r'\*([^*]+)\*'
        last_end = 0
        
        for match in re.finditer(pattern, content):
            if match.start() > last_end:
                segments.append({'text': content[last_end:match.start()], 'highlight': False})
            segments.append({'text': match.group(1), 'highlight': True})
            last_end = match.end()
        
        if last_end < len(content):
            segments.append({'text': content[last_end:], 'highlight': False})
        
        if not segments:
            segments = [{'text': content, 'highlight': False}]
        
        # Word wrap and render
        lines = []
        current_line = []
        current_line_segments = []
        
        for seg in segments:
            words = seg['text'].replace('\n', ' \n ').split(' ')
            for word in words:
                if word == '\n':
                    lines.append(current_line_segments)
                    current_line = []
                    current_line_segments = []
                    continue
                
                if not word:
                    continue
                
                test_line = current_line + [word]
                test_text = ' '.join(test_line)
                bbox = draw.textbbox((0, 0), test_text, font=font)
                
                if max_width and bbox[2] > max_width and current_line:
                    lines.append(current_line_segments)
                    current_line = [word]
                    current_line_segments = [{'text': word, 'highlight': seg['highlight']}]
                else:
                    if current_line:
                        current_line_segments.append({'text': ' ', 'highlight': False})
                    current_line.append(word)
                    current_line_segments.append({'text': word, 'highlight': seg['highlight']})
        
        if current_line_segments:
            lines.append(current_line_segments)
        
        # Render lines
        current_y = y
        for line_segments in lines:
            # Calculate line width for alignment
            line_text = ''.join(s['text'] for s in line_segments)
            bbox = draw.textbbox((0, 0), line_text, font=font)
            line_width = bbox[2] - bbox[0]
            
            if align == 'right':
                current_x = x - line_width
            elif align == 'center':
                current_x = x - line_width // 2
            else:
                current_x = x
            
            # Draw each segment
            for seg in line_segments:
                text = seg['text']
                col = hl_color if seg['highlight'] else base_color
                draw.text((current_x, current_y), text, font=font, fill=col)
                
                bbox = draw.textbbox((0, 0), text, font=font)
                current_x += bbox[2] - bbox[0]
            
            current_y += int(font_size * line_height)
    
    def render_slide(self, slide: dict, settings: dict, slide_num: int) -> Image.Image:
        """Render a single slide"""
        # Create background
        canvas = self.create_background(slide.get('background', {}))
        
        # Draw elements
        for el in slide.get('elements', []):
            self.draw_text_element(canvas, el, settings, slide_num)
        
        return canvas


renderer = SlideRenderer()


# ==================== ROUTES ====================

@app.get("/")
async def index():
    """Serve web editor"""
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    return {"status": "healthy", "version": "4.0", "timestamp": datetime.now().isoformat()}


# Templates CRUD

@app.get("/templates")
async def list_templates():
    """List all templates"""
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
            except Exception:
                pass
    
    return {"templates": templates}


@app.get("/templates/{name}")
async def get_template(name: str):
    """Get template by name"""
    safe_name = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', name)
    path = f"templates/{safe_name}.json"
    
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Template not found")
    
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


@app.post("/templates")
async def save_template(template: TemplateData):
    """Save template"""
    template_dict = template.dict()
    template_dict['createdAt'] = datetime.now().isoformat()
    
    safe_name = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', template.name)
    path = f"templates/{safe_name}.json"
    
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(template_dict, f, ensure_ascii=False, indent=2)
    
    return {"success": True, "name": template.name}


@app.delete("/templates/{name}")
async def delete_template(name: str):
    """Delete template"""
    safe_name = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', name)
    path = f"templates/{safe_name}.json"
    
    if os.path.exists(path):
        os.remove(path)
        return {"success": True}
    
    raise HTTPException(status_code=404, detail="Template not found")


# Rendering

@app.post("/render-slide")
async def render_slide(data: SlideData):
    """Render a single slide to PNG"""
    try:
        img = renderer.render_slide(data.slide, data.settings, data.slideNumber)
        
        # Convert to base64
        buffer = io.BytesIO()
        img.save(buffer, format="PNG", quality=95)
        b64 = base64.b64encode(buffer.getvalue()).decode()
        
        return {
            "success": True,
            "base64": f"data:image/png;base64,{b64}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate")
async def generate_carousel(request: GenerateRequest):
    """
    Generate carousel from template
    
    Example request:
    {
        "template_name": "My Template",
        "slides": [
            {"TITLE": "Custom title", "DESCRIPTION": "Custom desc", "PHOTO": "https://..."},
            {"TITLE": "Slide 2 title"}
        ]
    }
    """
    # Load template
    safe_name = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', request.template_name)
    path = f"templates/{safe_name}.json"
    
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Template '{request.template_name}' not found")
    
    with open(path, 'r', encoding='utf-8') as f:
        template = json.load(f)
    
    settings = template.get('settings', {})
    slides = template.get('slides', [])
    
    # Apply variables from request
    if request.slides:
        for i, slide_vars in enumerate(request.slides):
            if i < len(slides):
                slide = slides[i]
                
                # Update photo if provided
                if 'PHOTO' in slide_vars:
                    slide['background']['photo'] = slide_vars['PHOTO']
                    slide['background']['type'] = 'photo'
                
                # Update elements
                for el in slide.get('elements', []):
                    var_name = el.get('varName', '')
                    if var_name and var_name in slide_vars:
                        el['content'] = slide_vars[var_name]
                    
                    # Check for highlight color
                    if f"{var_name}_COLOR" in slide_vars:
                        el['highlightColor'] = slide_vars[f"{var_name}_COLOR"]
    
    # Render all slides
    results = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    for i, slide in enumerate(slides):
        img = renderer.render_slide(slide, settings, i + 1)
        
        # Save file
        filename = f"carousel_{timestamp}_{i+1}.png"
        filepath = f"output/{filename}"
        img.save(filepath, "PNG", quality=95)
        
        # Convert to base64
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
        "message": f"Сгенерировано {len(results)} слайдов",
        "slides": results
    }


@app.get("/output/{filename}")
async def get_output(filename: str):
    """Get generated file"""
    path = f"output/{filename}"
    if os.path.exists(path):
        return FileResponse(path, media_type="image/png")
    raise HTTPException(status_code=404, detail="File not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
