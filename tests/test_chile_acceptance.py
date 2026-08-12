"""Pruebas de aceptación para la evolución chilena de Pyme Ledger AI."""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import uuid
import zipfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ledger_chile_acceptance_"))

from fastapi.testclient import TestClient
from app import app
from database import SessionLocal
from models import CategoriaContable, Documento, Empresa, EstadoRevision, TipoDocumento
from analytics.anomaly_detector import detect_expense_anomalies
from tax_rules import evaluate_document_tax_context
from pipeline_agent import DocumentPipelineAgent


class ChileAcceptanceBase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def create_company(self, name=None, rut=None, regimen="ProPyme"):
        response = self.client.post("/api/empresas", json={
            "razon_social": name or f"Empresa {uuid.uuid4().hex[:8]}",
            "rut": rut,
            "pais": "Chile",
            "moneda_base": "CLP",
            "regimen_tributario": regimen,
            "giro": "Servicios de tecnología",
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class TestCompaniesAndCurrency(ChileAcceptanceBase):
    def test_companies_without_rut_do_not_overwrite_each_other(self):
        first = self.create_company(name="Sin RUT Uno", rut=None)
        second = self.create_company(name="Sin RUT Dos", rut=None)
        self.assertNotEqual(first["id"], second["id"])
        self.assertTrue(first["created"])
        self.assertTrue(second["created"])
        listed = self.client.get("/api/empresas").json()["empresas"]
        ids = {item["id"] for item in listed}
        self.assertIn(first["id"], ids)
        self.assertIn(second["id"], ids)

    def test_only_chile_and_clp_are_accepted(self):
        foreign = self.client.post("/api/empresas", json={
            "razon_social": "Empresa Extranjera", "pais": "Argentina", "moneda_base": "ARS"
        })
        self.assertEqual(foreign.status_code, 422)
        currency = self.client.post("/api/empresas", json={
            "razon_social": "Empresa Moneda Incorrecta", "pais": "Chile", "moneda_base": "USD"
        })
        self.assertEqual(currency.status_code, 422)


class TestLedgerActionsAndDuplicateControl(ChileAcceptanceBase):
    def test_chat_can_create_category_and_sum_valid_expenses(self):
        company = self.create_company()
        category = self.client.post("/api/chat", json={
            "empresa_id": company["id"], "action": "create_category", "message": "",
            "payload": {"confirm": True, "nombre": "Viajes", "regla_iva": "No recuperable"},
        })
        self.assertEqual(category.status_code, 200, category.text)
        self.assertEqual(category.json()["response"]["action"], "categoria_creada")

        db = SessionLocal()
        try:
            doc = Documento(
                id=str(uuid.uuid4()), empresa_id=company["id"], proveedor="Uber",
                monto_total=25000, moneda="CLP", tipo_documento=TipoDocumento.BOLETA,
                estado_revision=EstadoRevision.REVISADO, hash_documento=uuid.uuid4().hex,
                fecha_creacion=datetime.utcnow(),
            )
            db.add(doc); db.commit()
        finally:
            db.close()
        summary = self.client.post("/api/chat", json={
            "empresa_id": company["id"], "action": "sum_expenses", "message": "",
            "payload": {"proveedor": "Uber"},
        })
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["response"]["data"]["total_clp"], 25000)

    def test_exact_duplicate_is_blocked_and_counted_in_dashboard(self):
        company = self.create_company()
        payload = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"
        digest = hashlib.sha256(payload).hexdigest()
        db = SessionLocal()
        try:
            db.add(Documento(
                id=str(uuid.uuid4()), empresa_id=company["id"], proveedor="Proveedor",
                monto_total=10000, moneda="CLP", tipo_documento=TipoDocumento.FACTURA,
                estado_revision=EstadoRevision.REVISADO, hash_documento=digest,
                fecha_creacion=datetime.utcnow(),
            ))
            db.commit()
        finally:
            db.close()
        response = self.client.post(
            f"/api/empresas/{company['id']}/documentos/upload",
            files={"file": ("factura.pdf", payload, "application/pdf")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["duplicate"])
        dashboard = self.client.get(f"/api/empresas/{company['id']}/analytics/dashboard").json()
        self.assertGreaterEqual(dashboard["kpis"]["documentos_duplicados"], 1)
        self.assertEqual(dashboard["kpis"]["total_gasto"], 10000)
    def test_zip_endpoint_accepts_safe_batch_without_processing_untrusted_paths(self):
        company = self.create_company()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("soportes/factura.pdf", b"%PDF-1.4\n%%EOF")
            zip_file.writestr("../../fuera.pdf", b"%PDF-1.4\n%%EOF")
            zip_file.writestr("planilla.xlsx", b"no compatible")
        with patch("app._run_batch_import"):
            response = self.client.post(
                f"/api/empresas/{company['id']}/documentos/import-zip",
                files={"file": ("lote.zip", archive.getvalue(), "application/zip")},
            )
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.assertEqual(job["total"], 1)
        self.assertEqual(len(job["ignorados"]), 2)


class TestLocalIntelligence(unittest.TestCase):
    def test_local_anomaly_detector_identifies_extreme_expense(self):
        category = SimpleNamespace(nombre="Operaciones")
        docs = [SimpleNamespace(
            id=str(index), proveedor="Proveedor estable", monto_total=10000 + index * 100,
            iva=1900, categoria=category, categoria_sugerida="Operaciones",
            fecha_emision=datetime(2026, 1, index + 1), fecha_creacion=datetime(2026, 1, index + 1),
        ) for index in range(6)]
        docs.append(SimpleNamespace(
            id="outlier", proveedor="Proveedor estable", monto_total=900000,
            iva=171000, categoria=category, categoria_sugerida="Operaciones",
            fecha_emision=datetime(2026, 1, 8), fecha_creacion=datetime(2026, 1, 8),
        ))
        found = detect_expense_anomalies(docs)
        self.assertIn("outlier", found)
        self.assertEqual(found["outlier"]["method"], "isolation_forest_local")
        self.assertGreaterEqual(found["outlier"]["score"], 0.5)

    def test_tax_context_uses_regimen_and_iva_rule(self):
        company = SimpleNamespace(regimen_tributario="ProPyme", giro="Servicios tecnológicos")
        category = SimpleNamespace(regla_iva="No recuperable", deducibilidad="Parcial")
        document = SimpleNamespace(categoria=category, iva=19000, monto_total=119000, tipo_documento=SimpleNamespace(value="Factura"))
        context = evaluate_document_tax_context(document, company)
        self.assertEqual(context["regimen"], "ProPyme")
        codes = {item["codigo"] for item in context["senales"]}
        self.assertIn("IVA_NO_RECUPERABLE", codes)
        self.assertIn("PROPYME_BASE_CAJA", codes)


class TestPipelineDataIntegrity(unittest.TestCase):
    def test_pipeline_marks_infinite_amount_as_pending_without_persisting_it(self):
        db = MagicMock()
        agent = DocumentPipelineAgent(db, "empresa-prueba", "/tmp")
        document = agent._save_document("documento-prueba", "/tmp/factura.pdf", "factura.pdf", {
            "fields": {"tipo_documento": "FACTURA", "monto_total": "Infinity", "moneda": "CLP"},
            "classification": {"categoria_id": None, "categoria_sugerida": "Operaciones", "confianza": 0.8},
            "audit": {"requiere_revision": False, "hash": "a" * 64, "excepciones": []},
            "combined_text": "factura de prueba",
            "agent_results": {},
        })
        self.assertIsNone(document.monto_total)
        self.assertEqual(document.estado_revision, EstadoRevision.PENDIENTE)
        exceptions = json.loads(document.excepciones)
        self.assertIn("MONTO_INVALIDO", {item["tipo"] for item in exceptions})


class TestUserInterfaceContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "..", "app", "index.html")
        with open(path, encoding="utf-8") as html_file:
            cls.html = html_file.read()

    def test_ui_exposes_separate_company_view_and_status_guide(self):
        self.assertIn("showSection('empresa-activa')", self.html)
        self.assertIn("renderEmpresaActiva", self.html)
        self.assertIn("Estados de revisión:", self.html)
        self.assertIn("Duplicado", self.html)

    def test_ui_exposes_ledger_actions_and_clp_only(self):
        for action in ["create_company", "create_category", "lookup_expense", "sum_expenses"]:
            self.assertIn(action, self.html)
        self.assertIn("CLP (Peso Chileno)", self.html)
        self.assertNotIn('<option value="PEN">', self.html)
        self.assertNotIn('<option value="USD">USD — Dólar</option>', self.html)


if __name__ == "__main__":
    unittest.main()
