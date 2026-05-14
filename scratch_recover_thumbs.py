import os
import subprocess
from PIL import Image, ImageDraw, ImageFont

def generate(vid_path, output_path, text):
    # Procura fonte Arial Bold
    font_path = "C:/Windows/Fonts/arialbd.ttf"
    if not os.path.exists(font_path):
        font_path = "arial.ttf" # Fallback
        
    # Caminho para o FFmpeg local do projeto
    ffmpeg_exe = os.path.join(os.path.abspath("."), "bin", "ffmpeg.exe")
    
    # Extrai frame aos 5 segundos
    vf = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,eq=contrast=1.2:saturation=1.6"
    cmd = f'"{ffmpeg_exe}" -ss 00:00:05 -i "{vid_path}" -vframes 1 -vf "{vf}" -q:v 2 "temp_frame.jpg" -y'
    subprocess.run(cmd, shell=True)
    
    if os.path.exists("temp_frame.jpg"):
        img = Image.open("temp_frame.jpg").convert("RGBA")
        
        # Tarja de destaque
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)
        draw_overlay.rectangle([(0, 360-100), (1280, 360+100)], fill=(0, 0, 0, 190))
        img = Image.alpha_composite(img, overlay)
        
        # Texto Viral
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(font_path, 95)
        except:
            font = ImageFont.load_default()
            
        draw.text((640, 360), text.upper(), font=font, fill="yellow", anchor="mm", stroke_width=5, stroke_fill="black")
        
        img.convert("RGB").save(output_path, "JPEG", quality=95)
        print(f"✅ Thumbnail salva em: {output_path}")
        if os.path.exists("temp_frame.jpg"):
            os.remove("temp_frame.jpg")

base_dir = r"c:\Users\User\.gemini\antigravity\scratch\dark-channel-factory\videos_finais"
generate(os.path.join(base_dir, "final_c72c64d5-a47f-4dd2-a050-a521a19a745d.mp4"), os.path.join(base_dir, "THUMB_STOLE_JOB.jpg"), "AI STOLE YOUR JOB?")
generate(os.path.join(base_dir, "final_c408c29c-b150-4fe6-bfe5-d40188324602.mp4"), os.path.join(base_dir, "THUMB_MILLIONAIRES.jpg"), "NEW AI MILLIONAIRES")
