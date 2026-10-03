"""
Cliente del servidor del modelo (API compatible con OpenAI, por streaming). Sin dependencias de UI.
"""
import json
import time
from typing import Callable, Optional

import httpx

from .config import API_URL, MODELO


class ErrorLLM(Exception):
    """Fallo de conexión o respuesta inválida/cortada del servidor del modelo."""


def separar_pensamiento(contenido: str):
    """Algunos servidores mezclan el razonamiento dentro de `content` con etiquetas <think>."""
    if "<think>" in contenido:
        antes, resto = contenido.split("<think>", 1)
        if "</think>" in resto:
            pensado, despues = resto.split("</think>", 1)
            return pensado.strip(), (antes + despues).strip()
        return resto.strip(), antes.strip()  # todavía pensando
    if "</think>" in contenido:  # la plantilla del modelo ya abrió <think> por nosotros
        pensado, despues = contenido.split("</think>", 1)
        return pensado.strip(), despues.strip()
    return "", contenido


def stream_llm(
    mensajes: list,
    on_fragmento: Optional[Callable[[str], None]] = None,
    on_pensamiento: Optional[Callable[[str], None]] = None,
    url: Optional[str] = None,
    modelo: Optional[str] = None,
    on_estadisticas: Optional[Callable[[dict], None]] = None,
) -> str:
    """
    Llama al modelo por streaming. Sin dependencias de UI: sirve para el CLI y para la web.
    - on_fragmento(respuesta_acumulada): la respuesta visible (sin razonamiento).
    - on_pensamiento(razonamiento_acumulado): el razonamiento, venga como `reasoning_content` (llama.cpp),
      `reasoning`/`thinking` (Ollama) o como <think>…</think> dentro del contenido.
    - on_estadisticas(dict): al terminar. Usa los números reales del servidor (usage/timings) si los manda;
      si no, solo tiempos. Claves: tokens_entrada, tokens_salida, tps, seg_total, seg_primer_token.
    Devuelve solo la respuesta. Si el servidor corta por límite de tokens/contexto, lanza ErrorLLM
    (una respuesta truncada aplicaría solo parte de los bloques).
    """
    payload = {
        "model": modelo or MODELO,
        "messages": mensajes,
        "temperature": 0.1,
        "stream": True,
        "cache_prompt": True,  # llama.cpp reutiliza el prefijo; otros servidores lo ignoran
        "stream_options": {"include_usage": True},  # pide el conteo real de tokens al final
    }
    destino = url or API_URL
    t0, t_primero, usage, timings = time.monotonic(), None, {}, {}
    razonamiento, contenido, finish = "", "", None
    ult_pens, ult_resp = "", ""
    try:
        with httpx.Client(timeout=httpx.Timeout(None, connect=10.0)) as client:
            with client.stream("POST", destino, json=payload) as r:
                if r.status_code != 200:
                    r.read()
                    raise ErrorLLM(f"El servidor respondió {r.status_code}: {r.text[:500]}")
                for linea in r.iter_lines():
                    if not linea.startswith("data:"):
                        continue
                    dato = linea[5:].strip()
                    if dato == "[DONE]":
                        break
                    try:
                        data = json.loads(dato)
                    except json.JSONDecodeError:
                        continue
                    usage.update(data.get("usage") or {})
                    timings = data.get("timings") or timings
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    finish = choices[0].get("finish_reason") or finish
                    delta = choices[0].get("delta") or {}
                    r_frag = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking") or ""
                    c_frag = delta.get("content") or ""
                    if (r_frag or c_frag) and t_primero is None:
                        t_primero = time.monotonic()
                    razonamiento += r_frag
                    contenido += c_frag
                    inline, respuesta = separar_pensamiento(contenido)
                    pensamiento = (razonamiento + "\n" + inline).strip() if inline else razonamiento
                    if pensamiento != ult_pens:
                        ult_pens = pensamiento
                        if on_pensamiento:
                            on_pensamiento(pensamiento)
                    if respuesta != ult_resp:
                        ult_resp = respuesta
                        if on_fragmento:
                            on_fragmento(respuesta)
    except httpx.ConnectError:
        raise ErrorLLM(f"No pude conectar con {destino}. ¿Está corriendo el servidor del modelo?")
    except httpx.HTTPError as e:
        raise ErrorLLM(f"Error de red hablando con el modelo: {e}")
    if finish == "length":
        raise ErrorLLM(
            "El modelo se quedó sin tokens o contexto antes de terminar (finish_reason=length). "
            "Los modelos que razonan gastan mucho contexto pensando: subí el contexto del servidor "
            "(llama-server: -c, Ollama: num_ctx) o probá con un archivo/mapa más chico."
        )
    if on_estadisticas:
        t_fin = time.monotonic()
        salida = usage.get("completion_tokens") or timings.get("predicted_n")
        tps = timings.get("predicted_per_second")
        if not tps and salida and t_primero and t_fin > t_primero:
            tps = salida / (t_fin - t_primero)
        on_estadisticas({
            "tokens_entrada": usage.get("prompt_tokens"),
            "tokens_salida": salida,
            "tps": tps,
            "seg_total": t_fin - t0,
            "seg_primer_token": (t_primero - t0) if t_primero else None,
        })
    return ult_resp.strip()


def listar_modelos(url: Optional[str] = None) -> list:
    """Consulta GET /v1/models (llama.cpp, Ollama y LM Studio lo soportan). Sirve para probar la conexión."""
    base = (url or API_URL).split("/chat/completions")[0].rstrip("/")
    try:
        r = httpx.get(base + "/models", timeout=5.0)
        r.raise_for_status()
        datos = r.json()
    except httpx.ConnectError:
        raise ErrorLLM(f"No pude conectar con {base}. ¿Está corriendo el servidor?")
    except (httpx.HTTPError, ValueError) as e:
        raise ErrorLLM(f"{base}/models no respondió como se esperaba: {e}")
    items = datos.get("data") or datos.get("models") or []
    ids = [(m.get("id") or m.get("name") or m.get("model")) for m in items if isinstance(m, dict)]
    return [i for i in ids if i]
