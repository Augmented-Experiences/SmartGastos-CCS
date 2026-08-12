"""
Módulo de recomendaciones para SmartGastos.
Genera sugerencias de optimización y ahorro de gastos.
"""
import json
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy.orm import Session

from models import Documento, Empresa
from tax_rules import evaluate_document_tax_context, regime_profile


class RecommendationEngine:
    """Motor de generación de recomendaciones de ahorro."""
    
    def __init__(self, db: Session, empresa_id: str):
        """
        Inicializa el motor de recomendaciones.
        
        Args:
            db: Sesión de base de datos
            empresa_id: ID de la empresa
        """
        self.db = db
        self.empresa_id = empresa_id
    
    def get_recommendations(self, period_days: int = 90) -> Dict:
        """
        Genera recomendaciones de optimización de gastos.
        
        Args:
            period_days: Período en días para análisis
            
        Returns:
            Dict con recomendaciones
        """
        start_date = datetime.utcnow() - timedelta(days=period_days)
        
        docs = self.db.query(Documento).filter(
            Documento.empresa_id == self.empresa_id,
            Documento.fecha_creacion >= start_date
        ).all()
        company = self.db.query(Empresa).filter(Empresa.id == self.empresa_id).first()
        valid_docs = [d for d in docs if not self._is_duplicate_document(d)]

        recommendations = []
        
        # Recomendación 1: Consolidación de proveedores
        consolidation_recs = self._recommend_provider_consolidation(valid_docs)
        recommendations.extend(consolidation_recs)
        
        # Recomendación 2: Gastos duplicados
        duplicate_recs = self._recommend_duplicate_detection(valid_docs)
        recommendations.extend(duplicate_recs)
        
        # Recomendación 3: Suscripciones recurrentes
        subscription_recs = self._recommend_subscription_review(valid_docs)
        recommendations.extend(subscription_recs)
        
        # Recomendación 4: Categorías con mayor gasto
        category_recs = self._recommend_category_optimization(valid_docs)
        recommendations.extend(category_recs)
        
        # Recomendación 5: Proveedores con alza anómala
        anomaly_recs = self._recommend_anomaly_investigation(valid_docs)
        recommendations.extend(anomaly_recs)

        # Reglas tributarias operativas: régimen, giro, deducibilidad y regla IVA.
        recommendations.extend(self._recommend_tax_context(valid_docs, company))

        # Ordenar por impacto potencial
        recommendations.sort(key=lambda x: x.get("impacto_potencial", 0), reverse=True)
        
        return {
            "periodo_dias": period_days,
            "fecha_generacion": datetime.utcnow().isoformat(),
            "total_recomendaciones": len(recommendations),
            "ahorro_potencial_total": sum(r.get("ahorro_potencial", 0) for r in recommendations),
            "contexto_empresa": {
                "regimen": regime_profile(getattr(company, "regimen_tributario", None)) if company else regime_profile(None),
                "giro": getattr(company, "giro", None) if company else None,
                "moneda": "CLP",
                "aviso": "Las recomendaciones tributarias son orientativas y requieren revisión humana con los respaldos.",
            },
            "recomendaciones": recommendations
        }
    
    @staticmethod
    def _is_duplicate_document(doc: Documento) -> bool:
        status = str(getattr(getattr(doc, "estado_revision", None), "value", getattr(doc, "estado_revision", "")) or "").lower()
        if status == "duplicado":
            return True
        try:
            exceptions = json.loads(doc.excepciones or "[]")
        except (TypeError, json.JSONDecodeError):
            return False
        return any(isinstance(item, dict) and item.get("tipo") == "DUPLICADO" for item in exceptions)

    def _recommend_tax_context(self, docs: list, company: Empresa | None) -> list:
        """Agrupa señales de revisión generadas desde los campos contables reales."""
        if not company:
            return []
        grouped = defaultdict(lambda: {"cantidad": 0, "monto": 0.0, "mensaje": "", "severidad": "BAJA"})
        for doc in docs:
            context = evaluate_document_tax_context(doc, company)
            for signal in context["senales"]:
                bucket = grouped[signal["codigo"]]
                bucket["cantidad"] += 1
                bucket["monto"] += float(doc.monto_total or 0)
                bucket["mensaje"] = signal["mensaje"]
                bucket["severidad"] = signal["severidad"]

        recommendations = []
        for code, data in grouped.items():
            recommendations.append({
                "tipo": code,
                "severidad": data["severidad"],
                "cantidad_documentos": data["cantidad"],
                "monto_involucrado": round(data["monto"], 2),
                "ahorro_potencial": 0.0,
                "impacto_potencial": 0.0,
                "mensaje": data["mensaje"],
                "accion": "Revisar respaldos, giro y tratamiento con la persona responsable de contabilidad.",
                "regimen": context["regimen"],
                "giro": context["giro"],
                "disclaimer": context["disclaimer"],
            })
        return recommendations

    def _recommend_provider_consolidation(self, docs: list) -> list:
        """Recomienda consolidación de proveedores similares."""
        recommendations = []
        
        # Agrupar por tipo de servicio
        providers_by_category = defaultdict(list)
        
        for doc in docs:
            if doc.categoria:
                cat_name = doc.categoria.nombre
                providers_by_category[cat_name].append({
                    "proveedor": doc.proveedor,
                    "monto": doc.monto_total or 0,
                    "fecha": doc.fecha_emision
                })
        
        # Buscar categorías con múltiples proveedores
        for category, providers_list in providers_by_category.items():
            providers_unique = set(p["proveedor"] for p in providers_list if p["proveedor"])
            
            if len(providers_unique) > 2:
                total_category = sum(p["monto"] for p in providers_list)
                
                recommendations.append({
                    "tipo": "CONSOLIDACIÓN_PROVEEDORES",
                    "severidad": "MEDIA",
                    "categoria": category,
                    "cantidad_proveedores": len(providers_unique),
                    "gasto_total": round(total_category, 2),
                    "ahorro_potencial": round(total_category * 0.05, 2),  # 5% de ahorro estimado
                    "impacto_potencial": round(total_category * 0.05, 2),
                    "mensaje": f"Consolidar {len(providers_unique)} proveedores de {category} podría ahorrar ~{round(total_category * 0.05, 2)}",
                    "accion": f"Negociar con 1-2 proveedores principales para {category}"
                })
        
        return recommendations
    
    def _recommend_duplicate_detection(self, docs: list) -> list:
        """Recomienda revisión de posibles duplicados."""
        recommendations = []
        
        # Buscar documentos similares (mismo proveedor, monto similar, fechas cercanas)
        potential_duplicates = defaultdict(list)
        
        for i, doc1 in enumerate(docs):
            for doc2 in docs[i+1:]:
                if (doc1.proveedor and doc2.proveedor and
                    doc1.proveedor.lower() == doc2.proveedor.lower() and
                    abs((doc1.monto_total or 0) - (doc2.monto_total or 0)) < 1 and
                    doc1.fecha_emision and doc2.fecha_emision and
                    abs((doc1.fecha_emision - doc2.fecha_emision).days) < 7):
                    
                    key = f"{doc1.proveedor}_{doc1.monto_total}"
                    potential_duplicates[key].append((doc1, doc2))
        
        if potential_duplicates:
            total_duplicate_amount = 0
            
            for key, pairs in potential_duplicates.items():
                for doc1, doc2 in pairs:
                    total_duplicate_amount += doc1.monto_total or 0
            
            recommendations.append({
                "tipo": "DUPLICADOS_POTENCIALES",
                "severidad": "ALTA",
                "cantidad_pares": len(potential_duplicates),
                "monto_potencial_duplicado": round(total_duplicate_amount, 2),
                "ahorro_potencial": round(total_duplicate_amount, 2),
                "impacto_potencial": round(total_duplicate_amount, 2),
                "mensaje": f"Se detectaron {len(potential_duplicates)} pares de documentos potencialmente duplicados",
                "accion": "Revisar y eliminar documentos duplicados"
            })
        
        return recommendations
    
    def _recommend_subscription_review(self, docs: list) -> list:
        """Recomienda revisión de suscripciones recurrentes."""
        recommendations = []
        
        # Detectar pagos recurrentes
        recurring_providers = defaultdict(list)
        
        for doc in docs:
            if doc.proveedor and doc.fecha_emision:
                recurring_providers[doc.proveedor].append(doc.fecha_emision)
        
        for provider, dates in recurring_providers.items():
            if len(dates) >= 3:
                dates_sorted = sorted(dates)
                intervals = []
                
                for i in range(len(dates_sorted) - 1):
                    interval = (dates_sorted[i+1] - dates_sorted[i]).days
                    intervals.append(interval)
                
                # Si los intervalos son regulares, es probablemente una suscripción
                if intervals and all(abs(iv - intervals[0]) <= 3 for iv in intervals):
                    avg_interval = sum(intervals) / len(intervals)
                    total_annual = 0
                    
                    # Calcular gasto anual
                    for doc in docs:
                        if doc.proveedor == provider:
                            total_annual += doc.monto_total or 0
                    
                    # Proyectar anual si es necesario
                    if avg_interval > 0:
                        annual_projection = total_annual * (365 / avg_interval)
                    else:
                        annual_projection = total_annual
                    
                    recommendations.append({
                        "tipo": "SUSCRIPCIÓN_RECURRENTE",
                        "severidad": "BAJA",
                        "proveedor": provider,
                        "frecuencia_dias": round(avg_interval),
                        "gasto_registrado": round(total_annual, 2),
                        "proyeccion_anual": round(annual_projection, 2),
                        "ahorro_potencial": round(annual_projection * 0.10, 2),  # 10% de ahorro estimado
                        "impacto_potencial": round(annual_projection * 0.10, 2),
                        "mensaje": f"{provider} es un cobro recurrente cada ~{round(avg_interval)} días",
                        "accion": f"Revisar contrato y negociar descuentos anuales o cambiar proveedor"
                    })
        
        return recommendations
    
    def _recommend_category_optimization(self, docs: list) -> list:
        """Recomienda optimización por categoría de gasto."""
        recommendations = []
        
        # Agrupar por categoría
        categories_data = defaultdict(float)
        
        for doc in docs:
            cat_name = doc.categoria.nombre if doc.categoria else "Sin categoría"
            categories_data[cat_name] += doc.monto_total or 0
        
        # Identificar categorías con alto gasto
        total_gasto = sum(categories_data.values())
        
        for category, amount in categories_data.items():
            percentage = (amount / total_gasto * 100) if total_gasto > 0 else 0
            
            # Si una categoría representa > 20% del gasto, recomendar revisión
            if percentage > 20:
                recommendations.append({
                    "tipo": "CATEGORÍA_ALTO_GASTO",
                    "severidad": "MEDIA",
                    "categoria": category,
                    "gasto_total": round(amount, 2),
                    "porcentaje_total": round(percentage, 1),
                    "ahorro_potencial": round(amount * 0.15, 2),  # 15% de ahorro estimado
                    "impacto_potencial": round(amount * 0.15, 2),
                    "mensaje": f"{category} representa {percentage:.1f}% del gasto total",
                    "accion": f"Revisar gastos en {category} e identificar oportunidades de reducción"
                })
        
        return recommendations
    
    def _recommend_anomaly_investigation(self, docs: list) -> list:
        """Recomienda investigación de anomalías de gasto."""
        recommendations = []
        
        # Agrupar por proveedor
        providers_data = defaultdict(list)
        
        for doc in docs:
            if doc.proveedor:
                providers_data[doc.proveedor].append(doc.monto_total or 0)
        
        for provider, montos in providers_data.items():
            if len(montos) >= 2:
                avg_monto = sum(montos) / len(montos)
                max_monto = max(montos)
                
                # Si hay un monto significativamente mayor, investigar
                if max_monto > avg_monto * 2.5:
                    recommendations.append({
                        "tipo": "ALZA_ANÓMALA_PROVEEDOR",
                        "severidad": "MEDIA",
                        "proveedor": provider,
                        "promedio_histórico": round(avg_monto, 2),
                        "monto_máximo": round(max_monto, 2),
                        "incremento_porcentaje": round(((max_monto - avg_monto) / avg_monto * 100), 1),
                        "ahorro_potencial": round((max_monto - avg_monto), 2),
                        "impacto_potencial": round((max_monto - avg_monto), 2),
                        "mensaje": f"{provider} tuvo un incremento de {round(((max_monto - avg_monto) / avg_monto * 100), 1)}% en último gasto",
                        "accion": f"Contactar a {provider} para aclarar el incremento de precio"
                    })
        
        return recommendations


# Instancia global
recommender_engine = None


def get_recommender_engine(db: Session, empresa_id: str) -> RecommendationEngine:
    """Obtiene una instancia del motor de recomendaciones."""
    return RecommendationEngine(db, empresa_id)
