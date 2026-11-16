import json
import zipfile
from pathlib import Path

from create_zip import create_project_zip


def _populate_sample_tree(base: Path) -> None:
    """Create dummy sensitive and legitimate files without real credentials."""
    sensitive_files = [
        "private_key.pem",
        "signing.key",
        "cert_bundle.p12",
        "identity.pfx",
        ".env",
        ".env.production",
        "client_secret.json",
        "client_secret_12345.apps.googleusercontent.com.json",
        "token.json",
        "youtube_token.json",
        "oauth_data.json",
        "my_credentials.json",
        "service_account_dummy.json",
        "local_state.db",
        "cache.sqlite3",
        "app.log",
        "scratch.tmp",
        "browser.session",
    ]

    for name in sensitive_files:
        (base / name).write_text("dummy-placeholder", encoding="utf-8")

    (base / ".env.example").write_text("APP_ENV=example\n", encoding="utf-8")
    (base / "main.py").write_text("print('ok')\n", encoding="utf-8")


def test_create_project_zip_excludes_sensitive_files_root_and_subdirs(tmp_path: Path):
    project_dir = tmp_path / "project"
    sub_dir = project_dir / "nested" / "module"
    sub_dir.mkdir(parents=True)

    _populate_sample_tree(project_dir)
    _populate_sample_tree(sub_dir)

    # Browser profile and cache directories
    chrome_dir = project_dir / "chrome_profiles" / "Default"
    chrome_dir.mkdir(parents=True)
    (chrome_dir / "Cookies").write_text("dummy-cookie", encoding="utf-8")

    pycache_dir = sub_dir / "__pycache__"
    pycache_dir.mkdir(parents=True)
    (pycache_dir / "main.cpython-311.pyc").write_bytes(b"\x00\x01")

    output_zip = tmp_path / "out.zip"
    create_project_zip(source_dir=str(project_dir), output_filename=str(output_zip))

    assert output_zip.exists()

    with zipfile.ZipFile(output_zip, "r") as zf:
        archived = {name.replace("\\", "/") for name in zf.namelist()}

    expected_included = {
        ".env.example",
        "main.py",
        "nested/module/.env.example",
        "nested/module/main.py",
    }
    assert archived == expected_included


def test_create_project_zip_inspects_json_content_and_excludes_sensitive_keys(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)

    # Unconventional JSON filenames with dummy sensitive fields (nested & case-insensitive)
    (project_dir / "secrets.json").write_text(
        json.dumps({"Refresh_Token": "dummy-refresh-token", "CLIENT_SECRET": "dummy-secret"}),
        encoding="utf-8",
    )
    (project_dir / "config.json").write_text(
        json.dumps({"auth": {"nested": [{"access_token": "dummy-access-token"}]}}),
        encoding="utf-8",
    )
    (project_dir / "data.json").write_text(
        json.dumps({
            "service_account": {
                "Private_Key": "dummy-private-key",
                "private_key_id": "dummy-key-id",
                "token_uri": "https://example.invalid/token",
                "credentials": "dummy-creds",
            }
        }),
        encoding="utf-8",
    )

    # Legitimate JSON without sensitive fields
    (project_dir / "settings.json").write_text(
        json.dumps({"app_name": "dark-channel-factory", "version": "1.0.0", "features": ["a", "b"]}),
        encoding="utf-8",
    )

    output_zip = tmp_path / "out.zip"
    create_project_zip(source_dir=str(project_dir), output_filename=str(output_zip))

    assert output_zip.exists()

    with zipfile.ZipFile(output_zip, "r") as zf:
        archived = {name.replace("\\", "/") for name in zf.namelist()}

    assert archived == {"settings.json"}


def test_create_project_zip_fail_closed_on_malformed_and_invalid_encoding_json(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)

    # 1. Malformed JSON named config.json -> excluded
    (project_dir / "config.json").write_text("{invalid-json", encoding="utf-8")

    # 2. Invalid UTF-8 encoding in JSON file -> excluded
    (project_dir / "broken_encoding.json").write_bytes(b"\xff\xfe\x00\x00{\"ok\": true}")

    # 3. Valid JSON without sensitive fields -> included
    (project_dir / "valid_settings.json").write_text(
        json.dumps({"app": "dark-channel-factory", "retries": 3}),
        encoding="utf-8",
    )

    # 4. Valid JSON with nested credentials -> excluded
    (project_dir / "nested_creds.json").write_text(
        json.dumps({"service": {"auth": [{"client_secret": "dummy-secret"}]}}),
        encoding="utf-8",
    )

    output_zip = tmp_path / "out.zip"
    create_project_zip(source_dir=str(project_dir), output_filename=str(output_zip))

    assert output_zip.exists()

    with zipfile.ZipFile(output_zip, "r") as zf:
        archived = {name.replace("\\", "/") for name in zf.namelist()}

    assert archived == {"valid_settings.json"}
