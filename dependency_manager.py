"""
╔══════════════════════════════════════════════════════════════╗
║   Dark Channel Factory — Bootstrapper v1.0                  ║
║   Módulo: dependency_manager.py                             ║
║                                                              ║
║   Responsabilidade:                                          ║
║     • Verificar e baixar FFmpeg (gyan.dev) na 1ª execução   ║
║     • Configurar caminho de download do modelo Whisper       ║
║     • Injetar app_data/bin no PATH em tempo de execução      ║
║     • Exibir progresso detalhado no terminal                 ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import zipfile
import shutil
import tempfile

try:
    import requests
except ImportError:
    raise RuntimeError("[Bootstrapper] 'requests' não encontrado. Execute: pip install requests")

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # fallback para prints iterativos sem tqdm

# ──────────────────────────────────────────────────────────────
# CONSTANTES DE CAMINHO
# ──────────────────────────────────────────────────────────────

# Resolve o diretório raiz do executável (compatível com PyInstaller)
def _get_base_dir() -> str:
    """Retorna o diretório base: pasta do .exe (PyInstaller) ou do .py."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.dirname(__file__))


BASE_DIR       = _get_base_dir()
APP_DATA_DIR   = os.path.join(BASE_DIR, "app_data")
BIN_DIR        = os.path.join(APP_DATA_DIR, "bin")
MODELS_DIR     = os.path.join(APP_DATA_DIR, "models")

FFMPEG_EXE     = os.path.join(BIN_DIR, "ffmpeg.exe")
FFPROBE_EXE    = os.path.join(BIN_DIR, "ffprobe.exe")

# URL da build estática gyan.dev — ffmpeg-release-essentials (menor, ~80 MB)
FFMPEG_ZIP_URL = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)

# Modelo Whisper padrão (pode ser sobrescrito via env WHISPER_MODEL)
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "base")


# ──────────────────────────────────────────────────────────────
# HELPERS DE DOWNLOAD
# ──────────────────────────────────────────────────────────────

def _download_with_progress(url: str, dest_path: str, label: str) -> None:
    """
    Baixa `url` para `dest_path` exibindo barra de progresso.
    Usa tqdm se disponível; caso contrário, usa prints percentuais.
    """
    print(f"\n[Bootstrapper] ⬇  {label}")
    response = requests.get(url, stream=True, timeout=120)
    response.raise_for_status()

    total = int(response.headers.get("content-length", 0))
    chunk_size = 1024 * 256  # 256 KB por chunk

    if tqdm and total > 0:
        progress = tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=f"  [Bootstrapper] {label[:45]}",
            ncols=80,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{rate_fmt}]",
        )
    else:
        progress = None

    downloaded = 0
    last_pct = -1

    with open(dest_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)

            if progress:
                progress.update(len(chunk))
            elif total > 0:
                pct = int(downloaded / total * 100)
                if pct != last_pct and pct % 5 == 0:
                    print(f"  [Bootstrapper] {label[:40]}... {pct}%")
                    last_pct = pct

    if progress:
        progress.close()

    print(f"  [Bootstrapper] ✅ Download concluído: {os.path.basename(dest_path)}")


# ──────────────────────────────────────────────────────────────
# LÓGICA FFmpeg
# ──────────────────────────────────────────────────────────────

def _install_ffmpeg() -> None:
    """
    Baixa o ZIP do FFmpeg (gyan.dev), extrai ffmpeg.exe e ffprobe.exe
    para BIN_DIR e apaga o arquivo temporário.
    """
    os.makedirs(BIN_DIR, exist_ok=True)

    # Arquivo temporário dentro do app_data para evitar problemas de permissão
    tmp_zip = os.path.join(APP_DATA_DIR, "_ffmpeg_tmp.zip")

    try:
        _download_with_progress(
            url=FFMPEG_ZIP_URL,
            dest_path=tmp_zip,
            label="Baixando motor de renderização de vídeo (FFmpeg)...",
        )

        print("[Bootstrapper] 📦 Extraindo ffmpeg.exe e ffprobe.exe...")
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            # O ZIP do gyan.dev tem estrutura: ffmpeg-X.Y-essentials_build/bin/
            extracted_count = 0
            for member in zf.namelist():
                basename = os.path.basename(member)
                if basename in ("ffmpeg.exe", "ffprobe.exe"):
                    # Extrai apenas o arquivo, sem criar sub-pastas
                    source = zf.open(member)
                    target = os.path.join(BIN_DIR, basename)
                    with open(target, "wb") as out:
                        shutil.copyfileobj(source, out)
                    print(f"  [Bootstrapper] ✅ Extraído: {basename}")
                    extracted_count += 1

        if extracted_count < 2:
            raise RuntimeError(
                f"[Bootstrapper] ❌ ffmpeg.exe ou ffprobe.exe não encontrados no ZIP. "
                f"Extraídos: {extracted_count}/2"
            )

    finally:
        if os.path.exists(tmp_zip):
            os.remove(tmp_zip)
            print("[Bootstrapper] 🗑  Arquivo temporário removido.")


def _ensure_ffmpeg() -> None:
    """Garante que ffmpeg.exe e ffprobe.exe estão em BIN_DIR."""
    if os.path.exists(FFMPEG_EXE) and os.path.exists(FFPROBE_EXE):
        print(f"[Bootstrapper] ✅ FFmpeg já instalado em: {BIN_DIR}")
        return

    print("[Bootstrapper] 🔍 FFmpeg não encontrado. Iniciando instalação automática...")
    _install_ffmpeg()


# ──────────────────────────────────────────────────────────────
# LÓGICA Whisper
# ──────────────────────────────────────────────────────────────

def _ensure_whisper_model() -> str:
    """
    Garante que o modelo Whisper está em MODELS_DIR.
    Retorna o caminho do diretório de modelos para ser passado
    ao `whisper.load_model(download_root=...)`.
    """
    os.makedirs(MODELS_DIR, exist_ok=True)

    # O Whisper usa o nome no formato '<model>.pt'
    model_file = os.path.join(MODELS_DIR, f"{WHISPER_MODEL_NAME}.pt")

    if os.path.exists(model_file):
        print(f"[Bootstrapper] ✅ Modelo Whisper '{WHISPER_MODEL_NAME}' já em cache.")
    else:
        print(
            f"[Bootstrapper] 🧠 Modelo Whisper '{WHISPER_MODEL_NAME}' ausente.\n"
            f"  O download será iniciado automaticamente pelo Whisper em:\n"
            f"  {MODELS_DIR}"
        )
        # O Whisper faz o download automaticamente quando load_model é chamado.
        # Configuramos o download_root aqui para garantir o caminho correto.
        # O download real acontece dentro de `load_whisper_model()`.

    return MODELS_DIR


# ──────────────────────────────────────────────────────────────
# INJEÇÃO DE PATH
# ──────────────────────────────────────────────────────────────

def _inject_bin_path() -> None:
    """Injeta BIN_DIR no PATH do processo atual."""
    abs_bin = os.path.abspath(BIN_DIR)
    paths = os.environ.get("PATH", "").split(os.pathsep)
    if abs_bin not in paths:
        os.environ["PATH"] = abs_bin + os.pathsep + os.environ.get("PATH", "")
        print(f"[Bootstrapper] 🛣  PATH atualizado com: {abs_bin}")
    else:
        print(f"[Bootstrapper] ✅ PATH já contém: {abs_bin}")


# ──────────────────────────────────────────────────────────────
# API PÚBLICA
# ──────────────────────────────────────────────────────────────

def check_and_download_dependencies() -> dict:
    """
    Ponto de entrada principal do Bootstrapper.

    Executa (em ordem):
      1. Cria diretórios app_data/bin e app_data/models
      2. Verifica / instala FFmpeg
      3. Verifica / prepara caminho para modelo Whisper
      4. Injeta app_data/bin no PATH do processo

    Retorna:
      dict com:
        - bin_dir     : caminho absoluto da pasta bin
        - models_dir  : caminho absoluto da pasta models
        - whisper_root: download_root a passar para whisper.load_model()
    """
    print()
    print("=" * 62)
    print("  [Bootstrapper] 🚀 Verificando dependências locais...")
    print("=" * 62)

    # 1. Cria estrutura de diretórios
    os.makedirs(BIN_DIR, exist_ok=True)
    os.makedirs(MODELS_DIR, exist_ok=True)

    # 2. FFmpeg
    _ensure_ffmpeg()

    # 3. Whisper
    whisper_root = _ensure_whisper_model()

    # 3.5 TTS e PyTorch (Voz Premium XTTSv2)
    def _ensure_tts_dependencies():
        # [NEW] Check for legacy venv bridge to avoid installing broken TTS on Python 3.13
        venv_bridge = os.path.join("venv_clonagem", "Scripts", "python.exe")
        if os.path.exists(venv_bridge):
            print("[Bootstrapper] 🌉 Ponte 'venv_clonagem' detectada. O XTTSv2 será executado isoladamente.")
            return

        try:
            import TTS
            print("[Bootstrapper] ✅ Dependências do XTTSv2 (TTS) já instaladas.")
        except ImportError:
            print("[Bootstrapper] 🧠 Instalando bibliotecas premium do XTTSv2 (Isso pode demorar)...")
            import subprocess
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "TTS", "torch", "torchvision", "torchaudio", "numpy<2.0.0"])
                print("[Bootstrapper] ✅ XTTSv2 instalado com sucesso!")
            except Exception as e:
                print(f"[Bootstrapper] ⚠️ Erro ao instalar TTS: O seu Python atual pode não suportar a biblioteca TTS oficial.")
                print("[Bootstrapper] ℹ️ A clonagem premium tentará usar Edge-TTS ou ElevenLabs.")
                
    _ensure_tts_dependencies()

    # 4. PATH
    _inject_bin_path()

    print("=" * 62)
    print("  [Bootstrapper] ✅ Todas as dependências verificadas!")
    print("=" * 62)
    print()

    return {
        "bin_dir": BIN_DIR,
        "models_dir": MODELS_DIR,
        "whisper_root": whisper_root,
        "ffmpeg_exe": FFMPEG_EXE,
        "ffprobe_exe": FFPROBE_EXE,
    }


def load_whisper_model(model_name: str = None):
    """
    Carrega o modelo Whisper com o download_root configurado para MODELS_DIR.
    Deve ser chamado APÓS check_and_download_dependencies().

    Args:
        model_name: nome do modelo (default: valor de WHISPER_MODEL_NAME)

    Returns:
        modelo whisper carregado
    """
    import whisper  # import tardio para não falhar se whisper não instalado ainda

    name = model_name or WHISPER_MODEL_NAME
    print(f"[Bootstrapper] 🧠 Carregando modelo Whisper '{name}' (cache: {MODELS_DIR})...")
    model = whisper.load_model(name, download_root=MODELS_DIR)
    print(f"[Bootstrapper] ✅ Modelo Whisper '{name}' carregado com sucesso.")
    return model


# ──────────────────────────────────────────────────────────────
# SIMULAÇÃO DE PRIMEIRA EXECUÇÃO (validação)
# ──────────────────────────────────────────────────────────────

def simulate_first_run_structure() -> None:
    """
    Simula a estrutura de pastas gerada após a primeira execução
    sem baixar nada (modo dry-run). Útil para validação em CI/CD.
    """
    import datetime

    print("\n" + "=" * 62)
    print("  [Bootstrapper] 🔬 SIMULAÇÃO DE ESTRUTURA — PRIMEIRA EXECUÇÃO")
    print("=" * 62)

    structure = {
        "app_data/": {
            "bin/": {
                "ffmpeg.exe":  "~90 MB  — Motor de renderização (gyan.dev)",
                "ffprobe.exe": "~85 MB  — Analisador de mídia (gyan.dev)",
            },
            "models/": {
                f"{WHISPER_MODEL_NAME}.pt": "~145 MB — Modelo ASR (openai/whisper)",
            },
        }
    }

    def _print_tree(d: dict, indent: int = 0) -> None:
        for key, val in d.items():
            pad = "  " * indent
            if isinstance(val, dict):
                print(f"{pad}📁 {key}")
                _print_tree(val, indent + 1)
            else:
                print(f"{pad}📄 {key:<30} {val}")

    _print_tree(structure)

    print()
    print(f"  Base DIR  : {BASE_DIR}")
    print(f"  BIN  DIR  : {BIN_DIR}")
    print(f"  MODELS DIR: {MODELS_DIR}")
    print(f"  Timestamp : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 62 + "\n")


# ──────────────────────────────────────────────────────────────
# EXECUÇÃO DIRETA — Teste e Validação
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Dark Channel Factory — Bootstrapper de Dependências"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas simula a estrutura de pastas sem baixar nada.",
    )
    args = parser.parse_args()

    if args.dry_run:
        simulate_first_run_structure()
    else:
        deps = check_and_download_dependencies()
        simulate_first_run_structure()
        print("Caminhos resolvidos:")
        for k, v in deps.items():
            print(f"  {k:<15}: {v}")
