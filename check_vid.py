import subprocess
import json

path = r"C:\Users\User\.gemini\antigravity\scratch\dark-channel-factory\videos_finais\10-05-2026\US\video_longo_c2609dc1-b50f-4603-ba75-12ddbda985c1.mp4"
cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", path]
res = subprocess.run(cmd, capture_output=True, text=True)
print(res.stdout)
