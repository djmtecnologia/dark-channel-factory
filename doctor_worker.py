import os
import sys
import subprocess
import requests
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Cores para terminal
class Colors:
    OK = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def check_step(name, success, info=""):
    status = f"{Colors.OK}✅ PASS{Colors.ENDC}" if success else f"{Colors.FAIL}❌ FAIL{Colors.ENDC}"
    print(f"{name:<40} {status} {info}")
    return success

async def run_diagnostics():
    print(f"\n{Colors.BOLD}🩺 YouTube Dark Factory — Worker Diagnostic{Colors.ENDC}\n")
    load_dotenv()
    
    overall_success = True
    
    # 1. Check Python Modules
    try:
        import fastapi, sqlmodel, playwright, moviepy, edge_tts
        check_step("Python Core Modules", True)
    except ImportError as e:
        check_step("Python Core Modules", False, f"Missing: {e}")
        overall_success = False

    # 2. Check FFmpeg
    try:
        res = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        check_step("FFmpeg Installation", res.returncode == 0, "Found in PATH")
    except:
        check_step("FFmpeg Installation", False, "NOT FOUND - Please install FFmpeg")
        overall_success = False

    # 3. Check ENV Variables
    hub_url = os.getenv("CLOUD_HUB_URL")
    check_step("CLOUD_HUB_URL in .env", hub_url is not None, hub_url or "")
    
    # 4. Check Connection to Vercel
    if hub_url:
        try:
            resp = requests.get(f"{hub_url}/api/health", timeout=5)
            check_step("Cloud Hub Connectivity", resp.status_code == 200, f"HTTP {resp.status_code}")
        except Exception as e:
            check_step("Cloud Hub Connectivity", False, str(e))
            overall_success = False

    # 5. Check Chrome Profiles
    from channel_setup.chrome_profile_mapper import get_chrome_profiles
    profiles = get_chrome_profiles()
    check_step("Chrome Profiles Detected", len(profiles) > 0, f"Found {len(profiles)} profiles")

    # 6. Check Directories
    Path("output").mkdir(exist_ok=True)
    Path("temp").mkdir(exist_ok=True)
    check_step("Working Directories (output/temp)", True)

    print("\n" + "="*50)
    if overall_success:
        print(f"{Colors.OK}{Colors.BOLD}🚀 TUDO PRONTO! Seu Worker está configurado corretamente.{Colors.ENDC}")
        print("Execute 'python -m desktop_worker.main' para iniciar a produção.")
    else:
        print(f"{Colors.WARNING}⚠️  Alguns itens precisam de atenção antes de rodar o Worker.{Colors.ENDC}")
    print("="*50 + "\n")

if __name__ == "__main__":
    asyncio.run(run_diagnostics())
