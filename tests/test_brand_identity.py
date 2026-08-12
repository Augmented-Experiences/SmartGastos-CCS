"""Pruebas de regresión de identidad para SmartGastos.

Garantizan que el nombre anterior no reaparezca en superficies visibles y que
la paleta e iconografía corporativas existentes no cambien durante renombrados.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app import app
import database

ROOT = Path(__file__).resolve().parents[1]
VISIBLE_BRAND_FILES = [
    ROOT / "README.md",
    ROOT / "pinokio.js",
    ROOT / "install.json",
    ROOT / "start.json",
    ROOT / "stop.json",
    ROOT / "app" / "index.html",
    ROOT / "server" / "app.py",
    ROOT / "launcher.py",
    ROOT / "setup.py",
    ROOT / "requirements.txt",
]
LEGACY_BRAND = re.compile(r"Pyme[ _-]?Ledger|Ledger[ _-]?AI", re.IGNORECASE)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", VISIBLE_BRAND_FILES, ids=lambda path: path.name)
def test_visible_brand_surfaces_use_smartgastos_without_legacy_name(path: Path):
    text = read_text(path)
    assert "SmartGastos" in text, f"{path.relative_to(ROOT)} debe presentar la marca SmartGastos"
    assert not LEGACY_BRAND.search(text), f"{path.relative_to(ROOT)} aún conserva el nombre anterior"


def test_database_uses_smartgastos_name_and_keeps_safe_legacy_migration():
    """La nueva marca alcanza la base local sin abandonar los datos existentes."""
    database_source = read_text(ROOT / "server" / "database.py")
    summary_script = read_text(ROOT / "scripts" / "summarize_validation_data.py")
    assert 'DB_PATH = DATA_DIR / "smartgastos.db"' in database_source
    assert 'DATABASE = ROOT / "validation_data" / "smartgastos.db"' in summary_script
    assert "def _migrate_database_filename()" in database_source
    assert "source.backup(destination)" in database_source


def test_database_filename_migration_preserves_existing_records(tmp_path, monkeypatch):
    """Una instalación anterior se copia de forma segura al archivo SmartGastos."""
    legacy_name = "_".join(("pyme", "ledger.db"))
    legacy = tmp_path / legacy_name
    target = tmp_path / "smartgastos.db"
    with sqlite3.connect(legacy) as source:
        source.execute("CREATE TABLE comprobacion_migracion (id INTEGER PRIMARY KEY, valor TEXT)")
        source.execute("INSERT INTO comprobacion_migracion (valor) VALUES ('gasto existente')")
        source.commit()
    monkeypatch.setattr(database, "DB_PATH", target)
    monkeypatch.setattr(database, "_LEGACY_DB_PATH", legacy)
    database._migrate_database_filename()
    assert target.exists()
    assert not legacy.exists()
    with sqlite3.connect(target) as migrated:
        row = migrated.execute("SELECT valor FROM comprobacion_migracion").fetchone()
    assert row == ("gasto existente",)


def test_pinokio_metadata_and_server_title_are_smartgastos():
    pinokio = read_text(ROOT / "pinokio.js")
    app_source = read_text(ROOT / "server" / "app.py")
    assert 'title: "CCS — SmartGastos"' in pinokio
    assert 'FastAPI(title="SmartGastos"' in app_source
    assert 'getLogger("smartgastos")' in app_source


def test_user_interface_brand_and_assistant_are_smartgastos():
    ui = read_text(ROOT / "app" / "index.html")
    for expected in [
        "CCS — SmartGastos | Clasificación de Gastos",
        '<div class="logo-text">SmartGastos</div>',
        '<div class="asst-name">SmartGastos</div>',
        "Iniciando SmartGastos...",
        "Bienvenido a SmartGastos",
    ]:
        assert expected in ui
    assert "Acciones de SmartGastos" in ui


def test_corporate_visual_identity_tokens_and_icon_are_preserved():
    """El renombrado debe ser textual: colores, clases y el icono no cambian."""
    ui = read_text(ROOT / "app" / "index.html")
    pinokio = read_text(ROOT / "pinokio.js")
    for token in ["#0D3DA6", "#3DAE2B", "--ccs-azul-oscuro", "--ccs-verde", "CCS Theme"]:
        assert token in ui
    assert 'icon: "icon.png"' in pinokio
    assert (ROOT / "icon.png").is_file()


def test_pinokio_lifecycle_files_remain_valid_json_and_untouched_paths():
    for filename in ["install.json", "start.json", "stop.json"]:
        payload = json.loads(read_text(ROOT / filename))
        assert "run" in payload
    start = json.loads(read_text(ROOT / "start.json"))
    serialized_start = json.dumps(start)
    assert "server/app.py" in serialized_start
    assert "/ui/index.html" in serialized_start


def test_api_contract_still_reports_brand_and_health():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload.get("status") == "ok"
    assert "SmartGastos" in read_text(ROOT / "server" / "app.py")
