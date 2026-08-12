"""
Módulo de analítica para pyme-ledger-ai.
Genera KPIs, vistas mensuales, rankings y análisis de gastos.
"""
import json
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import func

from models import Documento, CategoriaContable, CentroCosto, DuplicateEvent
from .anomaly_detector import detect_expense_anomalies


class AnalyticsEngine:
    """Motor de análisis de gastos y generación de KPIs."""
    
    def __init__(self, db: Session, empresa_id: str):
        """
        Inicializa el motor de analítica.
        
        Args:
            db: Sesión de base de datos
            empresa_id: ID de la empresa
        """
        self.db = db
        self.empresa_id = empresa_id
    
    def get_dashboard_summary(self, period_days: int = 30) -> Dict:
        """
        Genera resumen para dashboard principal.
        
        Args:
            period_days: Período en días a analizar
            
        Returns:
            Dict con KPIs principales
        """
        start_date = datetime.utcnow() - timedelta(days=period_days)
        
        # Documentos en el período
        docs = self.db.query(Documento).filter(
            Documento.empresa_id == self.empresa_id,
            Documento.fecha_creacion >= start_date
        ).all()
        
        duplicate_attempts = self.db.query(DuplicateEvent).filter(
            DuplicateEvent.empresa_id == self.empresa_id,
            DuplicateEvent.fecha_creacion >= start_date
        ).count()
        if not docs:
            empty = self._get_empty_summary()
            empty["kpis"]["documentos_duplicados"] = duplicate_attempts
            empty["kpis"]["intentos_duplicados_bloqueados"] = duplicate_attempts
            return empty

        duplicate_docs = [d for d in docs if self._is_duplicate_document(d)]
        valid_docs = [d for d in docs if not self._is_duplicate_document(d)]
        # Solo los documentos válidos afectan los montos contables y promedios.
        total_gasto = sum(d.monto_total or 0 for d in valid_docs)
        total_iva = sum(d.iva or 0 for d in valid_docs)
        total_neto = sum(d.monto_neto or 0 for d in valid_docs)

        pending_review = len([d for d in valid_docs if self._status_value(d) == "pendiente"])
        reviewed = len([d for d in valid_docs if self._status_value(d) == "revisado"])
        duplicates = len(duplicate_docs) + duplicate_attempts

        # Promedio basado en gastos válidos, sin duplicados.
        avg_gasto = total_gasto / len(valid_docs) if valid_docs else 0
        
        # Proveedor con mayor gasto
        top_provider = self._get_top_provider(docs)
        
        # Categoría con mayor gasto
        top_category = self._get_top_category(docs)
        
        return {
            "periodo_dias": period_days,
            "fecha_generacion": datetime.utcnow().isoformat(),
            "kpis": {
                "total_gasto": round(total_gasto, 2),
                "total_iva": round(total_iva, 2),
                "total_neto": round(total_neto, 2),
                "promedio_documento": round(avg_gasto, 2),
                "cantidad_documentos": len(valid_docs),
                "documentos_pendiente_revision": pending_review,
                "documentos_revisados": reviewed,
                "documentos_duplicados": duplicates,
                "intentos_duplicados_bloqueados": duplicate_attempts
            },
            "top_provider": self._get_top_provider(valid_docs),
            "top_category": self._get_top_category(valid_docs),
            "tendencia": self._calculate_trend(valid_docs)
        }
    
    def get_expenses_by_category(self, period_days: int = 30) -> Dict:
        """
        Análisis de gastos por categoría contable.
        
        Args:
            period_days: Período en días
            
        Returns:
            Dict con gastos agrupados por categoría
        """
        start_date = datetime.utcnow() - timedelta(days=period_days)
        
        docs = self.db.query(Documento).filter(
            Documento.empresa_id == self.empresa_id,
            Documento.fecha_creacion >= start_date
        ).all()
        
        categories_data = defaultdict(lambda: {
            "total": 0.0,
            "iva": 0.0,
            "neto": 0.0,
            "cantidad": 0,
            "documentos": []
        })
        
        for doc in docs:
            if self._is_duplicate_document(doc):
                continue
            cat_name = doc.categoria.nombre if doc.categoria else "Sin categoría"
            categories_data[cat_name]["total"] += doc.monto_total or 0
            categories_data[cat_name]["iva"] += doc.iva or 0
            categories_data[cat_name]["neto"] += doc.monto_neto or 0
            categories_data[cat_name]["cantidad"] += 1
            categories_data[cat_name]["documentos"].append({
                "id": doc.id,
                "proveedor": doc.proveedor,
                "monto": doc.monto_total,
                "fecha": doc.fecha_emision.isoformat() if doc.fecha_emision else None
            })
        
        # Convertir a lista y ordenar por total
        result = [
            {
                "categoria": cat,
                "total": round(data["total"], 2),
                "iva": round(data["iva"], 2),
                "neto": round(data["neto"], 2),
                "cantidad": data["cantidad"],
                "promedio": round(data["total"] / data["cantidad"], 2) if data["cantidad"] > 0 else 0,
                "porcentaje": 0  # Se calcula después
            }
            for cat, data in categories_data.items()
        ]
        
        # Calcular porcentajes
        total_general = sum(r["total"] for r in result)
        for r in result:
            r["porcentaje"] = round((r["total"] / total_general * 100), 1) if total_general > 0 else 0
        
        # Ordenar por total descendente
        result.sort(key=lambda x: x["total"], reverse=True)
        
        return {
            "periodo_dias": period_days,
            "total_general": round(total_general, 2),
            "categorias": result
        }
    
    def get_expenses_by_provider(self, period_days: int = 30, limit: int = 20) -> Dict:
        """
        Análisis de gastos por proveedor.
        
        Args:
            period_days: Período en días
            limit: Número máximo de proveedores a retornar
            
        Returns:
            Dict con gastos agrupados por proveedor
        """
        start_date = datetime.utcnow() - timedelta(days=period_days)
        
        docs = self.db.query(Documento).filter(
            Documento.empresa_id == self.empresa_id,
            Documento.fecha_creacion >= start_date
        ).all()
        
        providers_data = defaultdict(lambda: {
            "total": 0.0,
            "cantidad": 0,
            "rut": None,
            "categorias": defaultdict(int),
            "fechas": []
        })
        
        for doc in docs:
            if self._is_duplicate_document(doc):
                continue
            prov_name = doc.proveedor or "Desconocido"
            providers_data[prov_name]["total"] += doc.monto_total or 0
            providers_data[prov_name]["cantidad"] += 1
            providers_data[prov_name]["rut"] = doc.rut_proveedor
            if doc.categoria:
                providers_data[prov_name]["categorias"][doc.categoria.nombre] += 1
            if doc.fecha_emision:
                providers_data[prov_name]["fechas"].append(doc.fecha_emision.isoformat())
        
        # Convertir a lista y ordenar
        result = [
            {
                "proveedor": prov,
                "rut": data["rut"],
                "total": round(data["total"], 2),
                "cantidad": data["cantidad"],
                "promedio": round(data["total"] / data["cantidad"], 2) if data["cantidad"] > 0 else 0,
                "categorias": dict(data["categorias"]),
                "frecuencia": self._detect_frequency(data["fechas"])
            }
            for prov, data in providers_data.items()
        ]
        
        # Ordenar por total descendente y limitar
        result.sort(key=lambda x: x["total"], reverse=True)
        result = result[:limit]
        
        return {
            "periodo_dias": period_days,
            "total_proveedores": len(providers_data),
            "top_proveedores": result
        }
    
    def get_expenses_by_cost_center(self, period_days: int = 30) -> Dict:
        """
        Análisis de gastos por centro de costo.
        
        Args:
            period_days: Período en días
            
        Returns:
            Dict con gastos agrupados por centro de costo
        """
        start_date = datetime.utcnow() - timedelta(days=period_days)
        
        docs = self.db.query(Documento).filter(
            Documento.empresa_id == self.empresa_id,
            Documento.fecha_creacion >= start_date
        ).all()
        
        cost_centers_data = defaultdict(lambda: {
            "total": 0.0,
            "cantidad": 0,
            "categorias": defaultdict(float)
        })
        
        for doc in docs:
            if self._is_duplicate_document(doc):
                continue
            cc_name = doc.centro_costo.nombre if doc.centro_costo else "Sin asignar"
            cost_centers_data[cc_name]["total"] += doc.monto_total or 0
            cost_centers_data[cc_name]["cantidad"] += 1
            if doc.categoria:
                cost_centers_data[cc_name]["categorias"][doc.categoria.nombre] += doc.monto_total or 0
        
        # Convertir a lista
        result = [
            {
                "centro_costo": cc,
                "total": round(data["total"], 2),
                "cantidad": data["cantidad"],
                "promedio": round(data["total"] / data["cantidad"], 2) if data["cantidad"] > 0 else 0,
                "categorias": {k: round(v, 2) for k, v in data["categorias"].items()}
            }
            for cc, data in cost_centers_data.items()
        ]
        
        # Ordenar por total descendente
        result.sort(key=lambda x: x["total"], reverse=True)
        
        return {
            "periodo_dias": period_days,
            "centros_costo": result
        }
    
    def get_monthly_trend(self, months: int = 12) -> Dict:
        """
        Análisis de tendencia mensual de gastos.
        
        Args:
            months: Número de meses a analizar
            
        Returns:
            Dict con datos mensuales
        """
        start_date = datetime.utcnow() - timedelta(days=30*months)
        
        docs = self.db.query(Documento).filter(
            Documento.empresa_id == self.empresa_id,
            Documento.fecha_creacion >= start_date
        ).all()
        
        monthly_data = defaultdict(lambda: {
            "total": 0.0,
            "neto": 0.0,
            "iva": 0.0,
            "cantidad": 0
        })
        
        for doc in docs:
            if self._is_duplicate_document(doc):
                continue
            if doc.fecha_emision:
                month_key = doc.fecha_emision.strftime("%Y-%m")
            else:
                month_key = doc.fecha_creacion.strftime("%Y-%m")
            
            monthly_data[month_key]["total"] += doc.monto_total or 0
            monthly_data[month_key]["neto"] += doc.monto_neto or 0
            monthly_data[month_key]["iva"] += doc.iva or 0
            monthly_data[month_key]["cantidad"] += 1
        
        # Convertir a lista ordenada
        result = [
            {
                "mes": month,
                "total": round(data["total"], 2),
                "neto": round(data["neto"], 2),
                "iva": round(data["iva"], 2),
                "cantidad": data["cantidad"]
            }
            for month, data in sorted(monthly_data.items())
        ]
        
        return {
            "meses": months,
            "datos": result,
            "promedio_mensual": round(sum(d["total"] for d in result) / len(result), 2) if result else 0
        }
    
    def get_anomalies_and_alerts(self, period_days: int = 30) -> Dict:
        """
        Detecta anomalías y genera alertas.
        
        Args:
            period_days: Período en días
            
        Returns:
            Dict con anomalías detectadas
        """
        start_date = datetime.utcnow() - timedelta(days=period_days)
        
        docs = self.db.query(Documento).filter(
            Documento.empresa_id == self.empresa_id,
            Documento.fecha_creacion >= start_date
        ).all()
        
        alerts = []
        duplicate_docs = [d for d in docs if self._is_duplicate_document(d)]
        valid_docs = [d for d in docs if not self._is_duplicate_document(d)]
        duplicate_attempts = self.db.query(DuplicateEvent).filter(
            DuplicateEvent.empresa_id == self.empresa_id,
            DuplicateEvent.fecha_creacion >= start_date
        ).count()

        # Alertas de calidad de datos; no se presentan como anomalías de IA.
        unclassified = [d for d in valid_docs if not d.categoria_id]
        if unclassified:
            alerts.append({
                "tipo": "SIN_CLASIFICACIÓN",
                "severidad": "MEDIA",
                "cantidad": len(unclassified),
                "mensaje": f"{len(unclassified)} documentos sin categoría asignada"
            })
        
        # Alerta 2: Documentos sin centro de costo
        no_cost_center = [d for d in valid_docs if not d.centro_costo_id]
        if no_cost_center:
            alerts.append({
                "tipo": "SIN_CENTRO_COSTO",
                "severidad": "MEDIA",
                "cantidad": len(no_cost_center),
                "mensaje": f"{len(no_cost_center)} documentos sin centro de costo"
            })
        
        # Alerta 3: Documentos con baja confianza
        low_confidence = [d for d in valid_docs if (d.confianza_clasificacion or 0) < 0.6]
        if low_confidence:
            alerts.append({
                "tipo": "BAJA_CONFIANZA",
                "severidad": "MEDIA",
                "cantidad": len(low_confidence),
                "mensaje": f"{len(low_confidence)} documentos con confianza < 60%"
            })
        
        # Duplicados persistidos e intentos bloqueados en carga.
        duplicate_count = len(duplicate_docs) + duplicate_attempts
        if duplicate_count:
            alerts.append({
                "tipo": "DUPLICADOS",
                "severidad": "ALTA",
                "cantidad": duplicate_count,
                "mensaje": f"{duplicate_count} duplicados detectados o bloqueados antes de contabilizar",
                "metodo": "hash_sha256"
            })

        # Detección local de anomalías: Isolation Forest o fallback MAD para muestras pequeñas.
        anomaly_map = detect_expense_anomalies(valid_docs)
        ml_anomalies = []
        for doc in valid_docs:
            found = anomaly_map.get(str(doc.id))
            if found:
                ml_anomalies.append({
                    "documento_id": doc.id,
                    "proveedor": doc.proveedor or "Desconocido",
                    "monto_total": round(float(doc.monto_total or 0), 2),
                    **found,
                })
        if ml_anomalies:
            alerts.append({
                "tipo": "GASTOS_ANOMALOS_IA_LOCAL",
                "severidad": "ALTA" if any(a["score"] >= 0.75 for a in ml_anomalies) else "MEDIA",
                "cantidad": len(ml_anomalies),
                "mensaje": f"{len(ml_anomalies)} gastos atípicos detectados por análisis local",
                "metodo": ", ".join(sorted({a["method"] for a in ml_anomalies}))
            })

        return {
            "periodo_dias": period_days,
            "total_alertas": len(alerts),
            "alertas": alerts,
            "anomalias": ml_anomalies,
            "metodos_analisis": sorted({a["method"] for a in ml_anomalies})
        }
    
    # Métodos auxiliares

    @staticmethod
    def _status_value(document: Documento) -> str:
        status = getattr(document, "estado_revision", None)
        return str(getattr(status, "value", status) or "pendiente").lower()

    def _is_duplicate_document(self, document: Documento) -> bool:
        if self._status_value(document) == "duplicado":
            return True
        try:
            exceptions = json.loads(document.excepciones or "[]")
        except (TypeError, json.JSONDecodeError):
            return False
        return any(isinstance(item, dict) and item.get("tipo") == "DUPLICADO" for item in exceptions)
    
    def _get_empty_summary(self) -> Dict:
        """Retorna resumen vacío."""
        return {
            "periodo_dias": 30,
            "fecha_generacion": datetime.utcnow().isoformat(),
            "kpis": {
                "total_gasto": 0,
                "total_iva": 0,
                "total_neto": 0,
                "promedio_documento": 0,
                "cantidad_documentos": 0,
                "documentos_pendiente_revision": 0,
                "documentos_revisados": 0,
                "documentos_duplicados": 0,
                "intentos_duplicados_bloqueados": 0
            },
            "top_provider": None,
            "top_category": None,
            "tendencia": "Sin datos"
        }
    
    def _get_top_provider(self, docs: list) -> Optional[Dict]:
        """Obtiene el proveedor con mayor gasto."""
        if not docs:
            return None
        
        providers = defaultdict(float)
        for doc in docs:
            if doc.proveedor:
                providers[doc.proveedor] += doc.monto_total or 0
        
        if not providers:
            return None
        
        top = max(providers.items(), key=lambda x: x[1])
        return {"proveedor": top[0], "total": round(top[1], 2)}
    
    def _get_top_category(self, docs: list) -> Optional[Dict]:
        """Obtiene la categoría con mayor gasto."""
        if not docs:
            return None
        
        categories = defaultdict(float)
        for doc in docs:
            if self._is_duplicate_document(doc):
                continue
            cat_name = doc.categoria.nombre if doc.categoria else "Sin categoría"
            categories[cat_name] += doc.monto_total or 0
        
        if not categories:
            return None
        
        top = max(categories.items(), key=lambda x: x[1])
        return {"categoria": top[0], "total": round(top[1], 2)}
    
    def _calculate_trend(self, docs: list) -> str:
        """Calcula tendencia de gastos."""
        if len(docs) < 2:
            return "Sin datos"
        
        # Dividir en dos períodos
        mid = len(docs) // 2
        first_half = sum(d.monto_total or 0 for d in docs[:mid])
        second_half = sum(d.monto_total or 0 for d in docs[mid:])
        
        if first_half == 0:
            return "Sin datos"
        
        change = ((second_half - first_half) / first_half) * 100
        
        if change > 10:
            return f"Alza ({change:.1f}%)"
        elif change < -10:
            return f"Baja ({change:.1f}%)"
        else:
            return "Estable"
    
    def _detect_frequency(self, dates: list) -> str:
        """Detecta frecuencia de pagos."""
        if len(dates) < 2:
            return "Único"
        
        dates_sorted = sorted(dates)
        intervals = []
        
        for i in range(len(dates_sorted) - 1):
            d1 = datetime.fromisoformat(dates_sorted[i])
            d2 = datetime.fromisoformat(dates_sorted[i+1])
            interval = (d2 - d1).days
            intervals.append(interval)
        
        if not intervals:
            return "Único"
        
        avg_interval = sum(intervals) / len(intervals)
        
        if avg_interval < 5:
            return "Diario"
        elif avg_interval < 10:
            return "Semanal"
        elif avg_interval < 35:
            return "Mensual"
        elif avg_interval < 100:
            return "Trimestral"
        else:
            return "Anual"
