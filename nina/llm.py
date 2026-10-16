"""
Cliente del servidor del modelo (API compatible con OpenAI, por streaming). Sin dependencias de UI.
"""
import json
import time
from collections import Counter
from typing import Callable, Optional
import httpx

from .config import gestor_config


class ErrorLLM(Exception):
    """Fallo de conexión o respuesta inválida/cortada del servidor del modelo."""

def separar_pensamiento(contenido: str):
    """Algunos servidores mezclan el razonamiento dentro de `content` con etiquetas <think>."""
    if "<think>" in contenido:
        antes, resto = contenido.split("<think>", 1)
        if "</think>" in resto:
            pensado, despues = resto.split("</think>", 1)
            return pensado.strip(), (antes + despues).strip()
        return resto.strip(), antes.strip()  
    if "</think>" in contenido:  
        pensado, despues = contenido.split("</think>", 1)
        return pensado.strip(), despues.strip()
    return "", contenido

class CorteLLM(ErrorLLM):
    """
    Un intento se cortó antes de terminar: bucle de razonamiento, razonamiento demasiado largo o tope de tokens.
    A diferencia de un ErrorLLM de conexión, es REINTENTABLE: `generar_cambio` vuelve a pedir, esta vez sin razonamiento.
    Lleva lo que alcanzó a llegar (`pensamiento`, `respuesta`) y las estadísticas (`stats`, parte estimadas).
    """
    def __init__(self, mensaje: str, motivo: str, pensamiento: str = "", respuesta: str = "", stats: Optional[dict] = None):
        super().__init__(mensaje)
        self.motivo, self.pensamiento, self.respuesta, self.stats = motivo, pensamiento, respuesta, stats or {}


def estimar_tokens_texto(texto: str) -> int:
    return len(texto) // 3


def detectar_bucle(texto: str, min_linea: int = 30, repeticiones: int = 10, ventana_lineas: int = 80) -> Optional[str]:
    """
    Devuelve un fragmento del texto que se está repitiendo (señal de bucle) o None.
    1) la misma línea (de al menos `min_linea` caracteres) `repeticiones` veces en las últimas `ventana_lineas`.
       (Umbrales altos a propósito: al redactar un SEARCH/REPLACE el modelo cita varias veces las mismas líneas de código
       dentro de su razonamiento, y eso NO es un bucle; un bucle real las repite decenas de veces.)
    2) la cola de 200 caracteres aparece 3 o más veces (bucles sin saltos de línea).
    """
    lineas = [l.strip() for l in texto.splitlines()[-ventana_lineas:] if len(l.strip()) >= min_linea]
    for linea, n in Counter(lineas).items():
        if n >= repeticiones:
            return linea[:80]
    if len(texto) > 1200:
        cola = texto[-200:]
        if texto.count(cola) >= 3:
            return cola[:80].strip()
    return None


# --- DRIVERS POR SERVIDOR (Fase 0.4) ---
# Todo el muestreo y los límites salen del perfil (config.py): NINA los manda en cada pedido, así lo que decide
# config.py manda sobre los valores por defecto con los que se haya lanzado el servidor.
class DriverBase:
    def preparar_payload(self, mensajes, modelo, perfil, razonar=None):
        m = perfil.get("muestreo") or {}
        limites = perfil.get("limites") or {}
        payload = {"model": modelo, "messages": mensajes, "stream": True}
        for clave in ("temperature", "top_p", "presence_penalty"):
            if m.get(clave) is not None:
                payload[clave] = m[clave]
        if limites.get("max_tokens_salida"):
            payload["max_tokens"] = int(limites["max_tokens_salida"])
        return payload


class OllamaDriver(DriverBase):
    def preparar_payload(self, mensajes, modelo, perfil, razonar=None):
        payload = super().preparar_payload(mensajes, modelo, perfil, razonar)
        m = perfil.get("muestreo") or {}
        # Ollama necesita num_ctx explícito para no recortar contexto grande en silencio
        opciones = {"num_ctx": perfil.get("max_tokens_prompt", 12000) + 4000}
        for clave in ("top_k", "min_p", "repeat_penalty"):
            if m.get(clave) is not None:
                opciones[clave] = m[clave]
        payload["options"] = opciones
        if razonar is False:
            payload["think"] = False      # parámetro nativo de Ollama; su endpoint /v1 podría ignorarlo
        return payload


class LlamaCppDriver(DriverBase):
    def preparar_payload(self, mensajes, modelo, perfil, razonar=None):
        payload = super().preparar_payload(mensajes, modelo, perfil, razonar)
        m = perfil.get("muestreo") or {}
        for clave in ("top_k", "min_p", "repeat_penalty"):
            if m.get(clave) is not None:
                payload[clave] = m[clave]
        payload["cache_prompt"] = True
        payload["stream_options"] = {"include_usage": True}
        if razonar is False:              # lo respetan las plantillas de chat que lo soportan (Qwen3, con --jinja)
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload


def obtener_driver(tipo: str) -> DriverBase:
    if tipo == "ollama":
        return OllamaDriver()
    elif tipo == "llama.cpp":
        return LlamaCppDriver()
    return DriverBase()
# ---------------------------------------


def _estadisticas(usage, timings, t0, t_primero, t_fin, pensamiento: str, respuesta: str) -> dict:
    salida = usage.get("completion_tokens") or timings.get("predicted_n")
    tps = timings.get("predicted_per_second")
    if not tps and salida and t_primero and t_fin > t_primero:
        tps = salida / (t_fin - t_primero)
    # Separar pensamiento y código: exacto si el servidor informa reasoning_tokens; si no, se reparte `salida`
    # en proporción a los caracteres de cada parte (aproximado).
    t_pens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    exactos = t_pens is not None
    if salida is not None and t_pens is None:
        rc, cc = len(pensamiento), len(respuesta)
        t_pens = round(salida * rc / (rc + cc)) if rc + cc else 0
    return {
        "tokens_entrada": usage.get("prompt_tokens"),
        "tokens_salida": salida,
        "tokens_pensamiento": t_pens,
        "tokens_codigo": (salida - t_pens) if (salida is not None and t_pens is not None) else None,
        "pens_exactos": exactos,
        "tps": tps,
        "seg_total": t_fin - t0,
        "seg_primer_token": (t_primero - t0) if t_primero else None,
    }


def stream_llm(
    mensajes: list,
    on_fragmento: Optional[Callable[[str], None]] = None,
    on_pensamiento: Optional[Callable[[str], None]] = None,
    url: Optional[str] = None,
    modelo: Optional[str] = None,
    on_estadisticas: Optional[Callable[[dict], None]] = None,
    razonar: Optional[bool] = None,
) -> str:
    """
    Llama al modelo por streaming usando el driver adecuado según la configuración del perfil activo.
    `razonar=False` pide al servidor que no razone (se usa en los reintentos tras un corte).
    Lanza CorteLLM si el intento se corta por bucle / límite de razonamiento / tope de tokens, y ErrorLLM si falla la conexión.
    """
    perfil = gestor_config.perfil_actual
    destino = url or perfil["url"]
    modelo_final = modelo or perfil["modelo"]
    tipo = perfil.get("tipo", "ollama")
    api_key = perfil.get("api_key", "")
    limites = perfil.get("limites") or {}
    tope_pens = int(limites.get("max_tokens_pensamiento") or 0)
    ver_bucles = bool(limites.get("detectar_bucles", True))

    driver = obtener_driver(tipo)
    payload = driver.preparar_payload(mensajes, modelo_final, perfil, razonar)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None

    t0, t_primero, usage, timings = time.monotonic(), None, {}, {}
    razonamiento, contenido, finish = "", "", None
    ult_pens, ult_resp = "", ""
    ult_chequeo, corte = 0, None
    try:
        with httpx.Client(timeout=httpx.Timeout(None, connect=10.0), headers=headers) as client:
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
                    # Guardas anti-bucle (solo mientras sigue razonando y todavía no hay respuesta)
                    if not respuesta.strip() and len(pensamiento) - ult_chequeo >= 500:
                        ult_chequeo = len(pensamiento)
                        if tope_pens and estimar_tokens_texto(pensamiento) > tope_pens:
                            corte = ("limite_pensamiento",
                                     f"El razonamiento superó ~{tope_pens} tokens sin llegar a una respuesta "
                                     f"(límite `max_tokens_pensamiento` del perfil).")
                        elif ver_bucles:
                            repetido = detectar_bucle(pensamiento)
                            if repetido:
                                corte = ("bucle", f"Bucle de razonamiento detectado: se repite «{repetido}».")
                        if corte:
                            break      # al salir del `with` se cierra la conexión y el servidor deja de generar
    except httpx.ConnectError:
        raise ErrorLLM(f"No pude conectar con {destino}. ¿Está corriendo el servidor del modelo?")
    except httpx.HTTPError as e:
        raise ErrorLLM(f"Error de red hablando con el modelo: {e}")

    if corte is None and finish == "length":
        tope = limites.get("max_tokens_salida")
        corte = ("longitud",
                 "El modelo se quedó sin tokens antes de terminar (finish_reason=length): "
                 + (f"tope `max_tokens_salida`={tope} del perfil o " if tope else "")
                 + "contexto del servidor agotado.")
    t_fin = time.monotonic()
    stats = _estadisticas(usage, timings, t0, t_primero, t_fin, ult_pens, ult_resp)
    if corte:
        if stats["tokens_salida"] is None:      # al cortar no llega `usage`: se estima por caracteres
            stats["tokens_pensamiento"] = estimar_tokens_texto(ult_pens)
            stats["tokens_codigo"] = estimar_tokens_texto(ult_resp)
            stats["tokens_salida"] = stats["tokens_pensamiento"] + stats["tokens_codigo"]
            stats["pens_exactos"] = False
        if on_estadisticas:
            on_estadisticas(stats)
        raise CorteLLM(corte[1], corte[0], ult_pens, ult_resp, stats)
    if on_estadisticas:
        on_estadisticas(stats)
    return ult_resp.strip()


def listar_modelos(url: Optional[str] = None) -> list:
    """Consulta GET /v1/models (llama.cpp, Ollama y LM Studio lo soportan)."""
    perfil = gestor_config.perfil_actual
    base = (url or perfil["url"]).split("/chat/completions")[0].rstrip("/")
    api_key = perfil.get("api_key", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    try:
        r = httpx.get(base + "/models", timeout=5.0, headers=headers)
        r.raise_for_status()
        datos = r.json()
    except httpx.ConnectError:
        raise ErrorLLM(f"No pude conectar con {base}. ¿Está corriendo el servidor?")
    except (httpx.HTTPError, ValueError) as e:
        raise ErrorLLM(f"{base}/models no respondió como se esperaba: {e}")
    items = datos.get("data") or datos.get("models") or []
    ids = [(m.get("id") or m.get("name") or m.get("model")) for m in items if isinstance(m, dict)]
    return [i for i in ids if i]