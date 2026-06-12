"""
ollama_client.py — Cliente centralizado para Ollama con seguridad integrada
=============================================================================
Capa 3 de defensa: TODA llamada al LLM pasa por este módulo.
Incluye:
  - Sanitización automática de inputs (anti-inyección)
  - Endurecimiento automático de system prompts
  - Fix de encoding UTF-8
  - Timeouts diferenciados por tipo de tarea
  - Descarga automática de modelos faltantes
  - Tracking de tokens y ahorro
"""

import json
import logging
import os
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

# Timeouts diferenciados por tipo de tarea
TIMEOUT_DEFAULT = int(os.environ.get("OLLAMA_TIMEOUT", "300"))
TIMEOUT_CLASSIFICATION = int(os.environ.get("OLLAMA_TIMEOUT_CLASSIFICATION", "120"))
TIMEOUT_EXTRACTION = int(os.environ.get("OLLAMA_TIMEOUT_EXTRACTION", "180"))
TIMEOUT_AUDIT = int(os.environ.get("OLLAMA_TIMEOUT_AUDIT", "180"))
TIMEOUT_CHAT = int(os.environ.get("OLLAMA_TIMEOUT_CHAT", "60"))

# Tracking global de uso (protegido por lock para thread safety)
_stats_lock = threading.Lock()
_usage_stats = {
    "total_calls": 0,
    "successful_calls": 0,
    "failed_calls": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_savings_usd": 0.0,
    "avg_latency_ms": 0,
    "injection_attempts_blocked": 0
}

# Estado de descarga de modelos (protegido por lock)
_pull_lock = threading.Lock()
_pull_status: Dict = {}


def get_usage_stats() -> Dict:
    """Retorna estadísticas de uso del LLM (thread-safe)."""
    with _stats_lock:
        return dict(_usage_stats)


def call_ollama(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.7,
    timeout: int = None,
    max_tokens: int = 1024,
    images: list = None,
    skip_sanitization: bool = False
) -> str:
    """
    Llamada centralizada a Ollama con seguridad integrada.
    
    Capa 3: Aplica sanitización de input y endurecimiento de prompt
    automáticamente en TODA llamada al LLM.
    
    Args:
        model: Nombre del modelo Ollama
        system_prompt: Prompt del sistema
        user_message: Mensaje del usuario (se sanitiza automáticamente)
        temperature: Temperatura de generación
        timeout: Timeout en segundos (None = default)
        max_tokens: Máximo de tokens a generar
        images: Lista de imágenes en base64 (para modelos multimodales)
        skip_sanitization: Solo para inputs internos del pipeline (no del usuario)
    
    Returns:
        Respuesta limpia del LLM
    """
    import requests
    import time
    from security import (
        sanitize_user_input, harden_system_prompt,
        fix_encoding, sanitize_llm_response,
        detect_injection_attempt, estimate_savings
    )

    if timeout is None:
        timeout = TIMEOUT_DEFAULT

    # Capa 2: Sanitizar input del usuario
    if not skip_sanitization:
        if detect_injection_attempt(user_message):
            with _stats_lock:
                _usage_stats["injection_attempts_blocked"] += 1
            logger.warning(f"Intento de inyección detectado y neutralizado")
        user_message = sanitize_user_input(user_message)

    # Capa 1: Endurecer system prompt
    hardened_prompt = harden_system_prompt(system_prompt)

    # Construir payload
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": hardened_prompt},
            {"role": "user", "content": user_message}
        ],
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens
        },
        "stream": False
    }

    if images:
        payload["messages"][-1]["images"] = images

    start_time = time.time()
    with _stats_lock:
        _usage_stats["total_calls"] += 1

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json=payload,
            timeout=timeout
        )
        resp.encoding = "utf-8"  # Forzar UTF-8 (evita latin-1 en Windows)

        if resp.status_code == 404:
            # Modelo no encontrado — iniciar descarga automática
            _start_pull_background(model)
            with _stats_lock:
                _usage_stats["failed_calls"] += 1
            raise Exception(f"Modelo {model} no disponible. Descarga iniciada automáticamente.")

        resp.raise_for_status()
        content = resp.json()["message"]["content"]

        # Sanitizar respuesta
        content = sanitize_llm_response(content)
        content = fix_encoding(content)

        # Tracking (thread-safe)
        elapsed_ms = int((time.time() - start_time) * 1000)
        with _stats_lock:
            _usage_stats["successful_calls"] += 1
            savings = estimate_savings(user_message, content)
            _usage_stats["total_input_tokens"] += savings["input_tokens"]
            _usage_stats["total_output_tokens"] += savings["output_tokens"]
            _usage_stats["total_savings_usd"] += savings["savings_usd"]

            # Promedio móvil de latencia
            n = _usage_stats["successful_calls"]
            _usage_stats["avg_latency_ms"] = int(
                (_usage_stats["avg_latency_ms"] * (n - 1) + elapsed_ms) / n
            )

        return content

    except Exception as e:
        with _stats_lock:
            _usage_stats["failed_calls"] += 1
        logger.error(f"Error en call_ollama: {e}")
        raise


def call_ollama_generate(
    model: str,
    prompt: str,
    system: str = None,
    temperature: float = 0.1,
    timeout: int = None,
    max_tokens: int = 1000,
    images: list = None,
    think: bool = False,
    skip_sanitization: bool = False
) -> Dict:
    """
    Llamada a /api/generate con seguridad integrada.
    Retorna dict con {response, thinking, raw}.
    """
    import requests
    import time
    from security import (
        sanitize_user_input, harden_system_prompt,
        fix_encoding, sanitize_llm_response,
        detect_injection_attempt
    )

    if timeout is None:
        timeout = TIMEOUT_DEFAULT

    # Sanitizar
    if not skip_sanitization:
        if detect_injection_attempt(prompt):
            with _stats_lock:
                _usage_stats["injection_attempts_blocked"] += 1
            logger.warning(f"Intento de inyección detectado en generate")
        prompt = sanitize_user_input(prompt)

    if system:
        system = harden_system_prompt(system)

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "top_p": 0.9
        }
    }

    if system:
        payload["system"] = system
    if images:
        payload["images"] = images
    if think:
        payload["think"] = True

    with _stats_lock:
        _usage_stats["total_calls"] += 1
    start_time = time.time()

    try:
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=timeout)
        resp.encoding = "utf-8"

        if resp.status_code == 404:
            _start_pull_background(model)
            with _stats_lock:
                _usage_stats["failed_calls"] += 1
            raise Exception(f"Modelo {model} no disponible. Descarga iniciada.")

        resp.raise_for_status()
        data = resp.json()

        raw_response = data.get("response", "").strip()
        thinking_text = data.get("thinking", "")
        clean_response = sanitize_llm_response(raw_response)
        clean_response = fix_encoding(clean_response)

        with _stats_lock:
            _usage_stats["successful_calls"] += 1
        return {
            "ok": True,
            "response": clean_response,
            "thinking": thinking_text,
            "raw": raw_response
        }

    except Exception as e:
        with _stats_lock:
            _usage_stats["failed_calls"] += 1
        logger.error(f"Error en call_ollama_generate: {e}")
        return {
            "ok": False,
            "error": str(e),
            "response": "",
            "thinking": "",
            "raw": ""
        }


# ============================================================
# Descarga automática de modelos
# ============================================================

def is_model_available(model: str) -> bool:
    """Verifica si un modelo está disponible en Ollama."""
    try:
        import requests
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        return model in models or any(m.startswith(model.split(":")[0]) for m in models)
    except Exception:
        return False


def _start_pull_background(model: str):
    """Inicia descarga de modelo en background (thread-safe)."""
    with _pull_lock:
        if model in _pull_status and _pull_status[model].get("status") in ("pulling", "queued"):
            return
        _pull_status[model] = {"status": "queued", "progress": 0, "error": None}
    threading.Thread(target=_do_pull, args=(model,), daemon=True).start()


def _do_pull(model: str):
    """Descarga un modelo de Ollama en background (thread-safe)."""
    import requests
    with _pull_lock:
        _pull_status[model]["status"] = "pulling"
    try:
        with requests.post(
            f"{OLLAMA_URL}/api/pull",
            json={"name": model, "stream": True},
            stream=True, timeout=3600
        ) as r:
            for line in r.iter_lines():
                if line:
                    data = json.loads(line)
                    if "completed" in data and "total" in data and data["total"] > 0:
                        with _pull_lock:
                            _pull_status[model]["progress"] = int(data["completed"] / data["total"] * 100)
        with _pull_lock:
            _pull_status[model] = {"status": "done", "progress": 100, "error": None}
    except Exception as e:
        with _pull_lock:
            _pull_status[model] = {"status": "error", "progress": 0, "error": str(e)}


def get_pull_status(model: str = None) -> Dict:
    """Retorna estado de descarga de modelos (thread-safe)."""
    with _pull_lock:
        if model:
            return _pull_status.get(model, {"status": "unknown", "progress": 0, "error": None})
        return dict(_pull_status)
