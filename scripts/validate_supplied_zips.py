"""Valida el importador ZIP con los documentos reales entregados para Pyme Ledger AI."""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DATA = ROOT / "validation_data"
# La limpieza debe suceder antes de importar la aplicación; así el motor SQLite
# crea un esquema nuevo y el informe no confunde reejecuciones con duplicados.
if VALIDATION_DATA.exists():
    shutil.rmtree(VALIDATION_DATA)
VALIDATION_DATA.mkdir(parents=True, exist_ok=True)
os.environ["DATA_DIR"] = str(VALIDATION_DATA)
os.environ["OLLAMA_URL"] = "http://127.0.0.1:11434"
sys.path.insert(0, str(ROOT / "server"))

from fastapi.testclient import TestClient
from app import app

ZIP_FILES = [
    Path("/home/ubuntu/upload/LlamaTalksTecWeek.zip"),
    Path("/home/ubuntu/upload/LLaMATechBuenosAires-ARSAT.zip"),
    Path("/home/ubuntu/upload/Montevideo2025.zip"),
]


def create_company(client: TestClient, index: int) -> str:
    response = client.post("/api/empresas", json={
        "razon_social": f"Validación ZIP {index}",
        "pais": "Chile",
        "moneda_base": "CLP",
        "regimen_tributario": "ProPyme",
        "giro": "Servicios tecnológicos",
    })
    if response.status_code != 200:
        raise RuntimeError(f"No se pudo crear empresa de validación: {response.status_code} {response.text}")
    return response.json()["id"]


def run_validation() -> dict:
    client = TestClient(app)
    results = []
    for index, zip_path in enumerate(ZIP_FILES, start=1):
        if not zip_path.exists():
            raise FileNotFoundError(zip_path)
        company_id = create_company(client, index)
        with zip_path.open("rb") as stream:
            response = client.post(
                f"/api/empresas/{company_id}/documentos/import-zip",
                files={"file": (zip_path.name, stream.read(), "application/zip")},
            )
        if response.status_code != 202:
            results.append({"zip": zip_path.name, "ok": False, "http_status": response.status_code, "detail": response.text})
            continue
        job = response.json()
        status = client.get(f"/api/empresas/{company_id}/documentos/import-zip/{job['id']}")
        status.raise_for_status()
        final = status.json()
        processed = [item for item in final["resultados"] if item["estado"] == "procesado"]
        duplicates = [item for item in final["resultados"] if item["estado"] == "duplicado"]
        errors = [item for item in final["resultados"] if item["estado"] == "error"]
        results.append({
            "zip": zip_path.name,
            "ok": final["status"] == "completed" and not errors and len(processed) + len(duplicates) == final["total"],
            "empresa_id": company_id,
            "estado": final["status"],
            "archivos_admitidos": final["total"],
            "procesados": len(processed),
            "duplicados": len(duplicates),
            "errores": errors,
            "ignorados": final["ignorados"],
            "resultados": final["resultados"],
        })
    report = {
        "generado_en": datetime.utcnow().isoformat() + "Z",
        "validaciones": results,
        "exito_total": all(item["ok"] for item in results),
    }
    output = ROOT / "VALIDATION_SUPPLIED_ZIPS.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    outcome = run_validation()
    raise SystemExit(0 if outcome["exito_total"] else 1)
