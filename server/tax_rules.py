"""Reglas operativas chilenas para orientar la revisión contable.

Estas reglas NO determinan impuestos ni reemplazan la revisión del contador o del SII.
Centralizan criterios que antes estaban dispersos y hacen explícita la diferencia
operativa entre ProPyme General y Régimen General.
"""
from __future__ import annotations

from typing import Any, Dict, List


REGIMEN_PROPYME = "ProPyme"
REGIMEN_GENERAL = "General"


def normalize_regimen(value: str | None) -> str:
    """Normaliza el régimen configurable a ProPyme o General.

    Se mantiene una superficie reducida de reglas para evitar que la interfaz
    simule una determinación tributaria completa.
    """
    normalized = (value or "").strip().lower()
    if "propyme" in normalized or "pro pyme" in normalized or "14 d" in normalized:
        return REGIMEN_PROPYME
    return REGIMEN_GENERAL


def regime_profile(value: str | None) -> Dict[str, Any]:
    """Devuelve contexto operacional visible para el usuario y el recomendador."""
    regimen = normalize_regimen(value)
    if regimen == REGIMEN_PROPYME:
        return {
            "regimen": REGIMEN_PROPYME,
            "metodo_base": "ingresos percibidos y gastos pagados",
            "contabilidad": "completa o simplificada, según opción aplicable",
            "tasa_idpc_referencia": 25.0,
            "requiere_revision_caja": True,
            "referencia": "Régimen ProPyme General (orientación operativa)",
        }
    return {
        "regimen": REGIMEN_GENERAL,
        "metodo_base": "reglas generales de renta líquida imponible",
        "contabilidad": "completa",
        "tasa_idpc_referencia": 27.0,
        "requiere_revision_caja": False,
        "referencia": "Régimen General / Semi Integrado (orientación operativa)",
    }


def evaluate_document_tax_context(document: Any, company: Any) -> Dict[str, Any]:
    """Evalúa señales de revisión de un gasto sin resolver su tratamiento tributario.

    `document` y `company` se aceptan como entidades SQLAlchemy o objetos de
    prueba. El resultado está diseñado para exposición en recomendaciones y
    auditorías, no para generar declaraciones ni cálculos de impuestos.
    """
    category = getattr(document, "categoria", None)
    regla_iva = (getattr(category, "regla_iva", None) or "Por revisar").strip()
    deducibilidad = (getattr(category, "deducibilidad", None) or "Por revisar").strip()
    regimen = normalize_regimen(getattr(company, "regimen_tributario", None))
    giro = (getattr(company, "giro", None) or "").strip()
    iva = float(getattr(document, "iva", 0) or 0)
    total = float(getattr(document, "monto_total", 0) or 0)
    tipo_documento = getattr(getattr(document, "tipo_documento", None), "value", None) or "Otro"

    signals: List[Dict[str, str]] = []
    if iva > 0 and regla_iva.lower() in {"no recuperable", "no_recuperable"}:
        signals.append({
            "codigo": "IVA_NO_RECUPERABLE",
            "severidad": "MEDIA",
            "mensaje": "La categoría indica IVA no recuperable; revisa el respaldo y el tratamiento contable.",
        })
    if iva == 0 and regla_iva.lower() in {"recuperable", "recuperable_si_factura"}:
        signals.append({
            "codigo": "IVA_POR_VALIDAR",
            "severidad": "BAJA",
            "mensaje": "La categoría permite revisar IVA, pero el documento no registra crédito fiscal extraído.",
        })
    if deducibilidad.lower() in {"no deducible", "no_deducible"}:
        signals.append({
            "codigo": "GASTO_NO_DEDUCIBLE",
            "severidad": "MEDIA",
            "mensaje": "La categoría está marcada como no deducible; valida la pertinencia con el giro y el respaldo.",
        })
    elif deducibilidad.lower() == "parcial":
        signals.append({
            "codigo": "DEDUCIBILIDAD_PARCIAL",
            "severidad": "BAJA",
            "mensaje": "La categoría tiene deducibilidad parcial; requiere validación humana antes de contabilizarla como gasto total.",
        })
    if regimen == REGIMEN_PROPYME and total > 0:
        signals.append({
            "codigo": "PROPYME_BASE_CAJA",
            "severidad": "INFO",
            "mensaje": "En ProPyme se destaca la revisión de pago/percibido para el control de caja y gasto pagado.",
        })

    return {
        "regimen": regimen,
        "perfil_regimen": regime_profile(regimen),
        "giro": giro,
        "tipo_documento": tipo_documento,
        "regla_iva": regla_iva,
        "deducibilidad": deducibilidad,
        "monto_total": round(total, 2),
        "senales": signals,
        "disclaimer": "Orientación operativa; confirma el tratamiento tributario con antecedentes y revisión profesional.",
    }
