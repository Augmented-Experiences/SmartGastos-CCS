"""
Agente OCR Multi-Estrategia para SmartGastos.

Estrategias en orden de prioridad:
  1. Para PDFs: PyPDF2 (texto digital) → pdf2image + Tesseract/EasyOCR
  2. Para imágenes: Tesseract multi-PSM + preprocesamiento → EasyOCR → VLLM Ollama

Modelos de visión soportados (via Ollama):
  - moondream        (1.8B — muy ligero, bueno para texto impreso)
  - llama3.2-vision  (11B — excelente para facturas y recibos)
  - llava:7b         (7B  — bueno para documentos generales)
  - granite3.2-vision (IBM — excelente para documentos empresariales)
"""

import base64
import hashlib
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

# Modelos de visión preferidos en orden (del más ligero al más pesado)
VISION_MODELS_PREFERENCE = [
    "moondream",
    "llama3.2-vision:11b",
    "llama3.2-vision",
    "llava:7b",
    "llava",
    "granite3.2-vision",
    "minicpm-v",
]

# Umbral mínimo de palabras para considerar OCR exitoso
MIN_WORDS = 10

# ── Imports opcionales ────────────────────────────────────────────────────────
try:
    import PyPDF2
    _PYPDF2_OK = True
except ImportError:
    _PYPDF2_OK = False

try:
    from PIL import Image, ImageFilter, ImageEnhance
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

# EasyOCR: lazy load para no bloquear el arranque del servidor
_easyocr_reader = None
_easyocr_tried = False

# Tesseract: verificar disponibilidad una sola vez
_tesseract_ok: Optional[bool] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _word_count(text: str) -> int:
    """Cuenta palabras alfanuméricas de más de 2 caracteres."""
    if not text:
        return 0
    return len([w for w in text.split() if len(w) > 2 and any(c.isalnum() for c in w)])


def _tesseract_available() -> bool:
    global _tesseract_ok
    if _tesseract_ok is None:
        try:
            r = subprocess.run(['tesseract', '--version'],
                               capture_output=True, timeout=5)
            _tesseract_ok = (r.returncode == 0)
        except Exception:
            _tesseract_ok = False
    return _tesseract_ok


def _get_easyocr_reader():
    global _easyocr_reader, _easyocr_tried
    if _easyocr_tried:
        return _easyocr_reader
    _easyocr_tried = True
    try:
        import easyocr
        _easyocr_reader = easyocr.Reader(["es", "en"], gpu=False, verbose=False)
        logger.info("EasyOCR cargado correctamente")
    except Exception as e:
        logger.info(f"EasyOCR no disponible: {e}")
        _easyocr_reader = None
    return _easyocr_reader


def _get_vision_model() -> Optional[str]:
    """Detecta qué modelo de visión está disponible en Ollama."""
    try:
        import requests
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if resp.status_code != 200:
            return None
        models_raw = resp.json().get('models', [])
        available_names = {m['name'] for m in models_raw}
        available_bases = {m['name'].split(':')[0] for m in models_raw}
        for preferred in VISION_MODELS_PREFERENCE:
            base = preferred.split(':')[0]
            if preferred in available_names or base in available_bases:
                return preferred
        return None
    except Exception:
        return None


# ── Preprocesamiento ──────────────────────────────────────────────────────────

def _preprocess(img_path: str, mode: str = "standard") -> str:
    """
    Preprocesa imagen para mejorar OCR.
    Retorna ruta al archivo temporal preprocesado.
    """
    if not _PIL_OK:
        return img_path
    try:
        img = Image.open(img_path)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')

        # Escalar si es muy pequeña
        w, h = img.size
        if w < 1000:
            scale = max(1000 / w, 1.5)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        gray = img.convert('L')

        if mode == "standard":
            gray = ImageEnhance.Contrast(gray).enhance(2.0)
            gray = gray.filter(ImageFilter.SHARPEN)
        elif mode == "aggressive":
            gray = ImageEnhance.Contrast(gray).enhance(3.0)
            gray = gray.point(lambda x: 0 if x < 128 else 255, '1').convert('L')
            gray = gray.filter(ImageFilter.MedianFilter(size=3))

        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        gray.save(tmp.name, 'PNG', dpi=(300, 300))
        return tmp.name
    except Exception as e:
        logger.debug(f"Preprocesamiento falló: {e}")
        return img_path


# ── Estrategia 1: Tesseract ───────────────────────────────────────────────────

def _run_tesseract(img_path: str, psm: int, lang: str = "spa+eng") -> str:
    try:
        result = subprocess.run(
            ['tesseract', img_path, 'stdout', '-l', lang,
             '--psm', str(psm), '--oem', '3'],
            capture_output=True, text=True, timeout=60
        )
        return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Tesseract PSM={psm} error: {e}")
        return ""


def ocr_tesseract(img_path: str) -> str:
    """Tesseract con múltiples PSM y preprocesamiento. Retorna el mejor resultado."""
    if not _tesseract_available():
        return ""

    best_text, best_count = "", 0
    tmp_files = []

    try:
        # Variantes de preprocesamiento
        variants = [(img_path, "raw")]
        for mode in ("standard", "aggressive"):
            p = _preprocess(img_path, mode)
            if p != img_path:
                variants.append((p, mode))
                tmp_files.append(p)

        # PSM modes a probar
        psm_modes = [6, 11, 3, 4]

        for img_v, mode_name in variants:
            for psm in psm_modes:
                text = _run_tesseract(img_v, psm)
                count = _word_count(text)
                if count > best_count:
                    best_count = count
                    best_text = text
                    logger.debug(f"Tesseract mejor: mode={mode_name} psm={psm} words={count}")
    finally:
        for f in tmp_files:
            try:
                os.unlink(f)
            except Exception:
                pass

    logger.info(f"Tesseract resultado: {best_count} palabras")
    return best_text


# ── Estrategia 2: EasyOCR ────────────────────────────────────────────────────

def ocr_easyocr(img_path: str) -> str:
    """EasyOCR — mejor para texto manuscrito y fuentes no estándar."""
    try:
        import numpy as np
        reader = _get_easyocr_reader()
        if reader is None:
            return ""

        img = Image.open(img_path)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img_array = np.array(img)

        results = reader.readtext(img_array, detail=1, paragraph=False)
        # Ordenar top-to-bottom
        results_sorted = sorted(results, key=lambda x: x[0][0][1])

        lines, current_line, prev_y = [], [], -1
        for bbox, text, conf in results_sorted:
            if conf < 0.1:
                continue
            y_center = (bbox[0][1] + bbox[2][1]) / 2
            if prev_y >= 0 and (y_center - prev_y) > 20:
                if current_line:
                    lines.append(' '.join(current_line))
                    current_line = []
            current_line.append(text)
            prev_y = y_center
        if current_line:
            lines.append(' '.join(current_line))

        text = '\n'.join(lines)
        logger.info(f"EasyOCR resultado: {_word_count(text)} palabras")
        return text
    except Exception as e:
        logger.warning(f"EasyOCR falló: {e}")
        return ""


# ── Estrategia 3: VLLM via Ollama ────────────────────────────────────────────

def ocr_vllm_ollama(img_path: str, model: Optional[str] = None) -> str:
    """
    OCR usando modelo de visión (VLLM) via Ollama.
    Ideal para documentos complejos, manuscritos o con layout irregular.
    """
    try:
        import requests

        if model is None:
            model = _get_vision_model()
        if model is None:
            logger.debug("No hay modelo de visión disponible en Ollama")
            return ""

        # Cargar imagen y redimensionar si es muy grande
        with open(img_path, 'rb') as f:
            img_data = f.read()

        if _PIL_OK:
            try:
                import io
                img = Image.open(img_path)
                w, h = img.size
                if w > 1920 or h > 1920:
                    img.thumbnail((1920, 1920), Image.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format='JPEG', quality=85)
                    img_data = buf.getvalue()
            except Exception:
                pass

        img_b64 = base64.b64encode(img_data).decode()

        prompt = (
            "Eres un experto en lectura de documentos contables latinoamericanos. "
            "Extrae TODO el texto visible en esta imagen de documento (factura, boleta, recibo, etc.).\n\n"
            "INSTRUCCIONES:\n"
            "- Transcribe el texto EXACTAMENTE como aparece, incluyendo números, fechas y símbolos\n"
            "- Preserva la estructura del documento (encabezado, tabla de items, totales)\n"
            "- Incluye: RUT/RUC, número de documento, fecha, nombre del emisor, items, montos\n"
            "- Para texto manuscrito: transcribe lo que puedas leer, marca con [?] lo ilegible\n"
            "- NO agregues interpretaciones ni comentarios, solo el texto del documento\n\n"
            "TEXTO DEL DOCUMENTO:"
        )

        payload = {
            "model": model,
            "prompt": prompt,
            "images": [img_b64],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 1500}
        }

        resp = requests.post(f"{OLLAMA_URL}/api/generate",
                             json=payload, timeout=180)
        if resp.status_code == 200:
            text = resp.json().get('response', '').strip()
            logger.info(f"VLLM ({model}): {_word_count(text)} palabras")
            return text
        else:
            logger.warning(f"Ollama VLLM HTTP {resp.status_code}")
            return ""
    except Exception as e:
        logger.debug(f"VLLM Ollama falló: {e}")
        return ""


# ── Estrategia 4: PyPDF2 para PDFs digitales ─────────────────────────────────

def ocr_pdf_digital(pdf_path: str) -> str:
    if not _PYPDF2_OK:
        return ""
    try:
        parts = []
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                t = page.extract_text() or ""
                if t.strip():
                    parts.append(t)
        combined = '\n'.join(parts)
        if _word_count(combined) > MIN_WORDS:
            logger.info(f"PyPDF2: {_word_count(combined)} palabras")
            return combined
        return ""
    except Exception as e:
        logger.debug(f"PyPDF2 falló: {e}")
        return ""


def ocr_pdf_scanned(pdf_path: str) -> str:
    """Convierte PDF escaneado a imágenes y aplica OCR."""
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(pdf_path, dpi=200, first_page=1, last_page=3)
        all_text = []
        for img in images:
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                img.save(tmp.name, 'PNG')
                t = ocr_tesseract(tmp.name)
                if _word_count(t) < MIN_WORDS:
                    t = ocr_easyocr(tmp.name)
                if t:
                    all_text.append(t)
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass
        return '\n\n--- PÁGINA SIGUIENTE ---\n\n'.join(all_text)
    except Exception as e:
        logger.debug(f"PDF escaneado OCR falló: {e}")
        return ""


# ── Clase principal ───────────────────────────────────────────────────────────

class OCRAgent:
    """
    Agente OCR multi-estrategia para documentos contables.

    Orden de estrategias para imágenes:
      1. Tesseract (multi-PSM + preprocesamiento)
      2. EasyOCR (si Tesseract da poco texto)
      3. VLLM via Ollama (si las anteriores fallan)

    Orden de estrategias para PDFs:
      1. PyPDF2 texto digital
      2. pdf2image + Tesseract/EasyOCR
    """

    def __init__(self, hardware_profile: str = "cpu"):
        self.hardware_profile = hardware_profile
        self._vision_model: Optional[str] = None
        self._vision_model_checked = False
        logger.info(
            f"OCRAgent inicializado. "
            f"Tesseract: {'✅' if _tesseract_available() else '❌'} | "
            f"PIL: {'✅' if _PIL_OK else '❌'} | "
            f"PyPDF2: {'✅' if _PYPDF2_OK else '❌'}"
        )

    def _get_vision_model_cached(self) -> Optional[str]:
        if not self._vision_model_checked:
            self._vision_model = _get_vision_model()
            self._vision_model_checked = True
        return self._vision_model

    def extract_from_file(self, file_path: str) -> Dict:
        """
        Extrae texto de un archivo. Nunca lanza excepciones.
        Retorna dict con: text, method, word_count, confidence, hash, file.
        """
        path = Path(file_path)
        ext = path.suffix.lower()

        if not path.exists():
            return {"text": "", "method": "error",
                    "error": f"Archivo no encontrado: {file_path}",
                    "word_count": 0, "confidence": 0.0}

        try:
            file_hash = self._compute_hash(file_path)
        except Exception:
            file_hash = ""

        # ── Procesar según tipo ───────────────────────────────────────────────
        if ext == ".pdf":
            result = self._process_pdf(file_path)
        elif ext in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"):
            result = self._process_image(file_path)
        else:
            result = {"text": "", "method": "unsupported",
                      "error": f"Extensión no soportada: {ext}"}

        # Agregar metadatos
        result["hash"] = file_hash
        result["file"] = str(file_path)
        if "text" not in result:
            result["text"] = ""

        wc = _word_count(result.get("text", ""))
        result["word_count"] = wc
        result["confidence"] = self._estimate_confidence(wc)

        logger.info(
            f"OCR completado — método: {result.get('method', '?')}, "
            f"palabras: {wc}, confianza: {result['confidence']:.1f}"
        )
        return result

    def _process_pdf(self, file_path: str) -> Dict:
        """Pipeline OCR para PDFs."""
        # Paso 1: texto digital
        text = ocr_pdf_digital(file_path)
        if _word_count(text) >= MIN_WORDS:
            return {"text": text, "method": "pdf_digital"}

        # Paso 2: PDF escaneado → imagen → OCR
        text = ocr_pdf_scanned(file_path)
        if _word_count(text) >= MIN_WORDS:
            return {"text": text, "method": "pdf_scanned_ocr"}

        # Paso 3: VLLM si hay modelo disponible
        vm = self._get_vision_model_cached()
        if vm:
            # Convertir primera página a imagen para el VLLM
            try:
                from pdf2image import convert_from_path
                images = convert_from_path(file_path, dpi=150, first_page=1, last_page=1)
                if images:
                    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                        images[0].save(tmp.name, 'JPEG', quality=85)
                        text = ocr_vllm_ollama(tmp.name, vm)
                        try:
                            os.unlink(tmp.name)
                        except Exception:
                            pass
                    if _word_count(text) >= MIN_WORDS:
                        return {"text": text, "method": f"pdf_vllm_{vm}"}
            except Exception:
                pass

        return {"text": text or "", "method": "pdf_partial"}

    def _process_image(self, file_path: str) -> Dict:
        """Pipeline OCR para imágenes con múltiples estrategias."""
        strategies_tried = {}

        # Estrategia 1: Tesseract multi-PSM
        text = ocr_tesseract(file_path)
        wc = _word_count(text)
        strategies_tried['tesseract'] = wc
        if wc >= MIN_WORDS * 3:  # Resultado muy bueno → usar directamente
            return {"text": text, "method": "tesseract",
                    "strategies": strategies_tried}

        # Estrategia 2: EasyOCR
        text_easy = ocr_easyocr(file_path)
        wc_easy = _word_count(text_easy)
        strategies_tried['easyocr'] = wc_easy

        # Elegir el mejor entre Tesseract y EasyOCR
        if wc_easy > wc:
            best_text, best_method = text_easy, "easyocr"
            best_wc = wc_easy
        else:
            best_text, best_method = text, "tesseract"
            best_wc = wc

        if best_wc >= MIN_WORDS * 2:
            return {"text": best_text, "method": best_method,
                    "strategies": strategies_tried}

        # Estrategia 3: VLLM via Ollama (para documentos difíciles)
        vm = self._get_vision_model_cached()
        if vm:
            text_vllm = ocr_vllm_ollama(file_path, vm)
            wc_vllm = _word_count(text_vllm)
            strategies_tried[f'vllm_{vm}'] = wc_vllm
            if wc_vllm > best_wc:
                best_text, best_method = text_vllm, f"vllm_{vm}"
                best_wc = wc_vllm

        return {"text": best_text, "method": best_method,
                "strategies": strategies_tried}

    def _estimate_confidence(self, word_count: int) -> float:
        """Estima confianza basada en palabras extraídas."""
        if word_count >= 50:
            return 0.9
        elif word_count >= 20:
            return 0.7
        elif word_count >= MIN_WORDS:
            return 0.5
        elif word_count > 0:
            return 0.3
        return 0.0

    def _compute_hash(self, file_path: str) -> str:
        """SHA256 del archivo para detección de duplicados."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()


# ── Singleton ─────────────────────────────────────────────────────────────────
_ocr_agent_instance: Optional[OCRAgent] = None


def get_ocr_agent(hardware_profile: str = "cpu") -> OCRAgent:
    """Obtiene o crea la instancia singleton del agente OCR."""
    global _ocr_agent_instance
    if _ocr_agent_instance is None:
        _ocr_agent_instance = OCRAgent(hardware_profile)
    return _ocr_agent_instance


# ── Test directo ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO,
                        format='%(levelname)s: %(message)s')

    test_files = sys.argv[1:] if len(sys.argv) > 1 else [
        '/tmp/boleta_test.jpeg',
        '/tmp/boleta_manuscrita.jpg',
    ]

    agent = OCRAgent()
    for f in test_files:
        if os.path.exists(f):
            print(f"\n{'='*60}")
            print(f"Procesando: {f}")
            print('='*60)
            result = agent.extract_from_file(f)
            print(f"Método:    {result['method']}")
            print(f"Palabras:  {result['word_count']}")
            print(f"Confianza: {result['confidence']}")
            if 'strategies' in result:
                print(f"Estrategias: {result['strategies']}")
            print(f"\nTexto extraído:\n{result['text'][:800]}")
        else:
            print(f"Archivo no encontrado: {f}")
