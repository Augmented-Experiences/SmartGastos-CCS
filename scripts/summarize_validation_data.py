"""Resume la calidad de los documentos persistidos en la validación de ZIP reales."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "validation_data" / "smartgastos.db"
OUTPUT = ROOT / "VALIDATION_SUPPLIED_ZIPS_SUMMARY.json"


def main() -> None:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    query = """
        SELECT e.razon_social AS empresa,
               COUNT(d.id) AS documentos,
               SUM(CASE WHEN d.monto_total IS NOT NULL THEN 1 ELSE 0 END) AS con_monto,
               SUM(CASE WHEN d.proveedor IS NOT NULL AND d.proveedor <> '' THEN 1 ELSE 0 END) AS con_proveedor,
               SUM(CASE WHEN d.categoria_id IS NOT NULL THEN 1 ELSE 0 END) AS categorizados,
               SUM(CASE WHEN lower(d.estado_revision) = 'pendiente' THEN 1 ELSE 0 END) AS pendientes,
               SUM(CASE WHEN lower(d.estado_revision) = 'revisado' THEN 1 ELSE 0 END) AS aprobados,
               SUM(CASE WHEN lower(d.estado_revision) = 'rechazado' THEN 1 ELSE 0 END) AS rechazados
          FROM empresas e
          LEFT JOIN documentos d ON d.empresa_id = e.id
         GROUP BY e.id, e.razon_social
         ORDER BY e.razon_social
    """
    rows = [dict(row) for row in connection.execute(query).fetchall()]
    details = [dict(row) for row in connection.execute(
        "SELECT proveedor, monto_total, moneda, estado_revision, categoria_sugerida, excepciones FROM documentos ORDER BY fecha_creacion LIMIT 100"
    ).fetchall()]
    connection.close()
    report = {"resumen_por_empresa": rows, "muestra_documentos": details}
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
