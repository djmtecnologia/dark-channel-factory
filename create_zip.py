import json
import os
import zipfile

SENSITIVE_JSON_KEYS = {
    'client_secret',
    'refresh_token',
    'access_token',
    'private_key',
    'private_key_id',
    'token_uri',
    'credentials',
    'service_account',
}


def _contains_sensitive_key(node) -> bool:
    """Recursively check if a parsed JSON structure contains any sensitive key."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in SENSITIVE_JSON_KEYS:
                return True
            if _contains_sensitive_key(value):
                return True
    elif isinstance(node, list):
        for item in node:
            if _contains_sensitive_key(item):
                return True
    return False


def _has_sensitive_json_content(file_path: str) -> bool:
    """Inspect JSON file content for sensitive credential fields without logging values."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return True
    return _contains_sensitive_key(data)

def create_project_zip(source_dir=".", output_filename="dark-channel-factory-source.zip"):
    # Diretorios a ignorar completamente (comparacao case-insensitive)
    exclude_dir_names = {
        '.git', '.cursor', '.vscode', '.idea', '.pytest_cache', '.vercel',
        '__pycache__', 'assets_cache', 'bot_profile', 'browser_profiles',
        'build', 'chrome_profiles', 'dist', 'node_modules', 'output',
        'sessions', 'temp', 'tmp', 'user_data', 'videos_finais', 'venv',
        'venv_clonagem', 'app_data', 'bin', 'credentials', 'test_assets',
        'scratch', '.cache'
    }

    # Extensoes que nao sao codigo fonte (midia, logs, binarios, bancos locais, chaves/certificados, caches)
    exclude_extensions = {
        '.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp4', '.mp3', '.wav',
        '.pyc', '.pyo', '.pyd', '.log', '.bak', '.zip', '.exe', '.tmp',
        '.temp', '.swp', '.swo', '.cache',
        '.db', '.sqlite', '.sqlite3', '.db-shm', '.db-wal', '.sqlite-shm', '.sqlite-wal',
        '.pem', '.key', '.p12', '.pfx', '.crt', '.cer', '.der', '.jks', '.keystore',
        '.session', '.session-journal'
    }

    # Arquivos sensiveis ou temporarios especificos
    exclude_files = {
        '.env', '.env.local', 'quota_tracker.json', 'youtube_token.json',
        os.path.basename(output_filename).lower()
    }

    output_abs = os.path.abspath(output_filename)

    def is_source_file(file_path, file):
        if os.path.abspath(file_path) == output_abs:
            return False

        lower_name = file.lower()
        if lower_name in exclude_files:
            return False

        # Ignora qualquer .env exceto .env.example
        if (lower_name.startswith('.env') or lower_name.endswith('.env')) and lower_name != '.env.example':
            return False

        # Ignora extensoes nao-codigo (midia, compilacao, logs, bancos, chaves, certificados, sessoes)
        if any(lower_name.endswith(ext) for ext in exclude_extensions):
            return False

        # Ignora tokens OAuth, client secrets e arquivos de credenciais em JSON
        if lower_name.endswith('.json'):
            sensitive_json_markers = (
                'client_secret', 'oauth', 'credential', 'service_account'
            )
            if (
                lower_name.startswith('token')
                or '_token' in lower_name
                or any(marker in lower_name for marker in sensitive_json_markers)
                or _has_sensitive_json_content(file_path)
            ):
                return False

        # Ignora copias residuais do Windows (" - Copia")
        if ' - copia' in lower_name:
            return False
        return True

    included_files = []
    print("Compactando codigo fonte do projeto...")
    
    with zipfile.ZipFile(output_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(source_dir):
            # Filtra subdiretorios in-place para que o os.walk nao os processe
            dirs[:] = [
                d for d in dirs 
                if d.lower() not in exclude_dir_names 
                and not d.startswith('.') 
                and 'venv' not in d.lower()
            ]
            
            for file in sorted(files):
                file_path = os.path.join(root, file)
                if is_source_file(file_path, file):
                    arcname = os.path.relpath(file_path, source_dir)
                    
                    zipf.write(file_path, arcname)
                    included_files.append(arcname)

    size_kb = os.path.getsize(output_filename) / 1024
    print(f"\n[SUCESSO] Arquivo ZIP gerado: {output_filename}")
    print(f"Total de arquivos incluidos: {len(included_files)}")
    print(f"Tamanho final: {size_kb:.2f} KB\n")

if __name__ == "__main__":
    create_project_zip()
