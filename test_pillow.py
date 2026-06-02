import os
import random
from PIL import Image, ImageDraw, ImageFont

def generate_code_image_test(output_path, is_short=False):
    w, h = (720, 1280) if is_short else (1280, 720)
    # Criar fundo escuro cyberpunk
    img = Image.new("RGBA", (w, h), color=(10, 8, 19, 255))
    draw = ImageDraw.Draw(img)
    
    # Adicionar grade sutil ao fundo
    grid_color = (25, 20, 45, 50)
    grid_size = 40
    for x in range(0, w, grid_size):
        draw.line([(x, 0), (x, h)], fill=grid_color, width=1)
    for y in range(0, h, grid_size):
        draw.line([(0, y), (w, y)], fill=grid_color, width=1)
        
    # Desenhar barra superior estilo editor de código (macOS style)
    bar_h = 40
    draw.rectangle([(0, 0), (w, bar_h)], fill=(16, 12, 28, 255))
    
    # 3 botões coloridos
    draw.ellipse([(15, 13), (27, 25)], fill=(255, 95, 87, 255))
    draw.ellipse([(35, 13), (47, 25)], fill=(254, 188, 46, 255))
    draw.ellipse([(55, 13), (67, 25)], fill=(40, 200, 64, 255))
    
    # Título do arquivo
    font_title = None
    for path in ["C:\\Windows\\Fonts\\consolab.ttf", "C:\\Windows\\Fonts\\consola.ttf", "arial.ttf"]:
        if os.path.exists(path):
            try:
                font_title = ImageFont.truetype(path, 14)
                break
            except: pass
    if not font_title:
        font_title = ImageFont.load_default()
        
    draw.text((85, 11), "f1_telemetry_pipeline.py", fill=(150, 140, 180, 255), font=font_title)
    
    # Código Python a ser exibido
    code_lines = [
        "import fastf1",
        "import pandas as pd",
        "from sklearn.ensemble import RandomForestClassifier",
        "",
        "# 1. Initialize F1 telemetry API",
        "session = fastf1.get_session(2026, 'Monaco', 'Q')",
        "session.load()",
        "",
        "# 2. Feature Engineering: Speed Delta",
        "telemetry = session.laps.pick_fastest().get_telemetry()",
        "telemetry['accel'] = telemetry['Speed'].diff()",
        "",
        "# 3. Predict Acceleration using RandomForest",
        "model = RandomForestClassifier(n_estimators=100)",
        "X = telemetry[['Speed', 'Throttle', 'Brake']]",
        "y = telemetry['accel'] > 0",
        "model.fit(X, y)",
        "",
        "print('F1 Telemetry Integration: SUCCESS')"
    ]
    
    # Fontes do código
    font_code = None
    for path in ["C:\\Windows\\Fonts\\consolab.ttf", "C:\\Windows\\Fonts\\consola.ttf", "arial.ttf"]:
        if os.path.exists(path):
            try:
                font_code = ImageFont.truetype(path, 20 if not is_short else 15)
                break
            except: pass
    if not font_code:
        font_code = ImageFont.load_default()
        
    # Cores de syntax highlighting estilo Dracula/One Dark Pro
    colors = {
        "keyword": (255, 121, 198, 255),  # Rosa
        "comment": (98, 114, 164, 255),  # Roxo acinzentado
        "string": (241, 250, 140, 255),   # Amarelo
        "import": (139, 233, 253, 255),   # Ciano
        "default": (248, 248, 242, 255),  # Branco
        "number": (189, 147, 249, 255)   # Roxo
    }
    
    start_y = 60
    for idx, line in enumerate(code_lines):
        line_y = start_y + idx * (28 if not is_short else 22)
        # Identificar palavras chave para syntax highlighting simples
        words = line.split(" ")
        draw_x = 30
        
        if line.strip().startswith("#"):
            draw.text((draw_x, line_y), line, fill=colors["comment"], font=font_code)
            continue
            
        # Parse simples de linha para demonstração
        for word in words:
            # Mantém espaços vazios
            if not word:
                draw_x += 10
                continue
                
            clean_word = word.replace("(", "").replace(")", "").replace("[", "").replace("]", "").replace("'", "").replace('"', '').replace(",", "")
            
            w_color = colors["default"]
            if clean_word in ["import", "from", "as", "class", "def", "return", "if", "for", "in", "and", "or", "not"]:
                w_color = colors["keyword"]
            elif clean_word.isdigit():
                w_color = colors["number"]
            elif "'" in word or '"' in word:
                w_color = colors["string"]
            elif clean_word in ["fastf1", "pd", "RandomForestClassifier", "print", "telemetry", "model", "session", "X", "y"]:
                w_color = colors["import"]
                
            draw.text((draw_x, line_y), word + " ", fill=w_color, font=font_code)
            # Avança o cursor X
            try:
                word_w = draw.textlength(word + " ", font=font_code)
            except AttributeError:
                # Fallback se textlength não existir em versões antigas do Pillow
                word_w = len(word + " ") * (12 if not is_short else 9)
            draw_x += word_w
            
    img.save(output_path, "PNG")
    print(f"✅ Imagem de código salva em: {output_path}")

def generate_flowchart_image_test(output_path, is_short=False):
    w, h = (720, 1280) if is_short else (1280, 720)
    # Criar fundo escuro cyberpunk
    img = Image.new("RGBA", (w, h), color=(10, 8, 19, 255))
    draw = ImageDraw.Draw(img)
    
    # Grade de fundo
    grid_color = (25, 20, 45, 50)
    grid_size = 40
    for x in range(0, w, grid_size):
        draw.line([(x, 0), (x, h)], fill=grid_color, width=1)
    for y in range(0, h, grid_size):
        draw.line([(0, y), (w, y)], fill=grid_color, width=1)
        
    # Barra superior
    bar_h = 40
    draw.rectangle([(0, 0), (w, bar_h)], fill=(16, 12, 28, 255))
    draw.ellipse([(15, 13), (27, 25)], fill=(255, 95, 87, 255))
    draw.ellipse([(35, 13), (47, 25)], fill=(254, 188, 46, 255))
    draw.ellipse([(55, 13), (67, 25)], fill=(40, 200, 64, 255))
    
    font_title = None
    for path in ["C:\\Windows\\Fonts\\consolab.ttf", "arial.ttf"]:
        if os.path.exists(path):
            try:
                font_title = ImageFont.truetype(path, 14)
                break
            except: pass
    if not font_title:
        font_title = ImageFont.load_default()
        
    draw.text((85, 11), "f1_realtime_flowchart.drawio", fill=(150, 140, 180, 255), font=font_title)
    
    # Desenhando caixas do fluxograma
    steps = [
        {"title": "F1 LIVE TELEMETRY API", "desc": "Ingests real-time speed & RPM", "color": (139, 92, 246, 255)}, # Roxo
        {"title": "DATA PREPROCESSING ENGINE", "desc": "Pre-processes telemetry delta", "color": (59, 130, 246, 255)}, # Azul
        {"title": "AI PREDICTIVE MODEL", "desc": "Predicts live lap acceleration", "color": (236, 72, 153, 255)}, # Rosa
        {"title": "AUTOMATED TRIGGER QUEUE", "desc": "Triggers execution events", "color": (16, 185, 129, 255)}  # Verde
    ]
    
    font_step_title = None
    font_step_desc = None
    for path in ["C:\\Windows\\Fonts\\consolab.ttf", "arial.ttf"]:
        if os.path.exists(path):
            try:
                font_step_title = ImageFont.truetype(path, 18 if not is_short else 14)
                font_step_desc = ImageFont.truetype(path, 13 if not is_short else 10)
                break
            except: pass
    if not font_step_title:
        font_step_title = ImageFont.load_default()
        font_step_desc = ImageFont.load_default()
        
    box_w = 380 if not is_short else 280
    box_h = 80 if not is_short else 65
    
    start_y = 80
    gap = 75 if not is_short else 130
    
    for idx, step in enumerate(steps):
        # Centralizar caixas no eixo X
        box_x = (w - box_w) // 2
        box_y = start_y + idx * (box_h + gap)
        
        # Desenhar caixa com borda brilhante e fundo semi-transparente
        draw.rectangle([(box_x, box_y), (box_x + box_w, box_y + box_h)], fill=(20, 16, 36, 220), outline=step["color"], width=3)
        
        # Glow sutil (borda externa mais escura)
        draw.rectangle([(box_x - 1, box_y - 1), (box_x + box_w + 1, box_y + box_h + 1)], fill=None, outline=(*step["color"][:3], 100), width=1)
        
        # Textos da caixa
        draw.text((box_x + 15, box_y + 15), step["title"], fill=(255, 255, 255, 255), font=font_step_title)
        draw.text((box_x + 15, box_y + (45 if not is_short else 38)), step["desc"], fill=(180, 180, 210, 255), font=font_step_desc)
        
        # Desenhar seta apontando para baixo para o próximo passo
        if idx < len(steps) - 1:
            arrow_x = w // 2
            arrow_y_start = box_y + box_h
            arrow_y_end = arrow_y_start + gap
            # Desenha a linha da seta
            draw.line([(arrow_x, arrow_y_start), (arrow_x, arrow_y_end)], fill=(120, 110, 150, 255), width=3)
            # Ponta da seta
            draw.polygon([
                (arrow_x - 8, arrow_y_end - 12),
                (arrow_x + 8, arrow_y_end - 12),
                (arrow_x, arrow_y_end)
            ], fill=(120, 110, 150, 255))
            
    img.save(output_path, "PNG")
    print(f"✅ Fluxograma salvo em: {output_path}")

# Executar testes
os.makedirs("test_assets", exist_ok=True)
generate_code_image_test("test_assets/code_test.png", is_short=False)
generate_flowchart_image_test("test_assets/flowchart_test.png", is_short=False)
generate_code_image_test("test_assets/code_test_short.png", is_short=True)
generate_flowchart_image_test("test_assets/flowchart_test_short.png", is_short=True)
