"""Detección local y explicable de anomalías de gasto.

El detector usa Isolation Forest cuando hay suficientes documentos y recurre a
mediana/MAD cuando la muestra es pequeña. No depende de servicios cloud ni de
valores predeterminados por proveedor.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from math import log1p
from typing import Any, Dict, Iterable, List

import numpy as np

try:
    from sklearn.ensemble import IsolationForest
except ImportError:  # La aplicación sigue operativa si una instalación antigua aún no tiene sklearn.
    IsolationForest = None


MIN_ML_SAMPLES = 6


def _document_date(document: Any) -> datetime:
    return getattr(document, "fecha_emision", None) or getattr(document, "fecha_creacion", None) or datetime.utcnow()


def _category_name(document: Any) -> str:
    category = getattr(document, "categoria", None)
    return (getattr(category, "nombre", None) or getattr(document, "categoria_sugerida", None) or "Sin categoría").strip()


def _amount(document: Any) -> float:
    return max(float(getattr(document, "monto_total", 0) or 0), 0.0)


def _robust_score(value: float, values: List[float]) -> float:
    """Devuelve una distancia robusta a la mediana usando MAD."""
    if len(values) < 3:
        return 0.0
    median = float(np.median(values))
    mad = float(np.median(np.abs(np.array(values) - median)))
    if mad <= 0:
        return 0.0 if value == median else 4.0
    return abs(0.6745 * (value - median) / mad)


def detect_expense_anomalies(documents: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    """Analiza documentos y retorna una evaluación explicable por ID.

    El modelo observa monto transformado, relación IVA/total, frecuencia del
    proveedor y día del mes. Los resultados sólo se generan cuando existe una
    señal estadística; nunca se inventan anomalías para todos los documentos.
    """
    docs = list(documents)
    if not docs:
        return {}

    providers = [str(getattr(d, "proveedor", "") or "Desconocido").strip().lower() for d in docs]
    provider_counts = Counter(providers)
    categories = [_category_name(d) for d in docs]
    category_counts = Counter(categories)
    amounts = [_amount(d) for d in docs]
    features = []
    for doc, provider, category, amount in zip(docs, providers, categories, amounts):
        date = _document_date(doc)
        iva = max(float(getattr(doc, "iva", 0) or 0), 0.0)
        iva_ratio = iva / amount if amount else 0.0
        features.append([
            log1p(amount),
            iva_ratio,
            min(provider_counts[provider], 10),
            min(category_counts[category], 10),
            date.day / 31.0,
            date.weekday() / 6.0,
        ])

    results: Dict[str, Dict[str, Any]] = {}
    if IsolationForest is not None and len(docs) >= MIN_ML_SAMPLES:
        model = IsolationForest(
            n_estimators=120,
            contamination="auto",
            random_state=42,
            n_jobs=1,
        )
        matrix = np.array(features, dtype=float)
        predictions = model.fit_predict(matrix)
        raw_scores = -model.decision_function(matrix)
        min_score = float(np.min(raw_scores))
        max_score = float(np.max(raw_scores))
        for doc, prediction, raw_score, amount, provider in zip(docs, predictions, raw_scores, amounts, providers):
            normalized = 0.0 if max_score == min_score else (float(raw_score) - min_score) / (max_score - min_score)
            is_anomaly = prediction == -1
            reasons = []
            provider_amounts = [_amount(d) for d, p in zip(docs, providers) if p == provider]
            z = _robust_score(amount, provider_amounts if len(provider_amounts) >= 3 else amounts)
            if z >= 3.5:
                reasons.append("Monto atípico respecto de su historial comparable")
            if provider_counts[provider] == 1:
                reasons.append("Proveedor sin historial previo en el período")
            if category_counts[_category_name(doc)] == 1:
                reasons.append("Categoría poco frecuente en el período")
            if is_anomaly and not reasons:
                reasons.append("Combinación inusual de monto, IVA, proveedor, categoría y fecha")
            if is_anomaly:
                results[str(getattr(doc, "id"))] = {
                    "is_anomaly": True,
                    "score": round(max(normalized, 0.5), 3),
                    "method": "isolation_forest_local",
                    "reasons": reasons,
                }
        return results

    # Fallback determinista y robusto: conserva el comportamiento offline incluso en equipos limitados.
    for doc, amount, provider in zip(docs, amounts, providers):
        comparable = [_amount(d) for d, p in zip(docs, providers) if p == provider]
        if len(comparable) < 3:
            comparable = amounts
        z = _robust_score(amount, comparable)
        if z >= 3.5:
            results[str(getattr(doc, "id"))] = {
                "is_anomaly": True,
                "score": round(min(z / 8.0, 1.0), 3),
                "method": "robust_mad_fallback",
                "reasons": ["Monto atípico según mediana y desviación absoluta mediana"],
            }
    return results
