import os
import zipfile
import fnmatch

def create_project_zip(source_dir, output_filename):
    # Patterns to exclude
    exclude_dirs = {
        '.git', '.pytest_cache', '.vercel', '__pycache__', 'assets_cache', 
        'bot_profile', 'build', 'chrome_profiles', 'dist', 'output', 'temp', 
        'videos_finais', 'venv', '.cursor'
    }
    exclude_files = {
        '.env', '.env.local', 'factory_console.log', 'factory_errors.log',
        'quota_tracker.json', 'youtube_token.json'
    }
    exclude_extensions = {'.png', '.jpg', '.jpeg', '.mp4', '.bak', '.spec'}

    with zipfile.ZipFile(output_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(source_dir):
            # Modificar dirs in-place para que o os.walk não entre neles
            dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]
            
            for file in files:
                # Filtrar arquivos secretos/tokens JSON
                if file.endswith('.json') and (file.startswith('client_secret') or file.startswith('token_')):
                    continue
                
                # Filtrar extensões de mídia/debug
                if any(file.lower().endswith(ext) for ext in exclude_extensions):
                    continue
                
                # Filtrar arquivos específicos
                if file in exclude_files:
                    continue
                
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, source_dir)
                
                # Não incluir o próprio script de zip
                if arcname == output_filename or arcname == 'create_zip.py':
                    continue
                
                zipf.write(file_path, arcname)
                print(f"Added: {arcname}")

if __name__ == "__main__":
    source = "."
    output = "dark-channel-factory-source.zip"
    create_project_zip(source, output)
    print(f"\nSuccessfully created {output}")
