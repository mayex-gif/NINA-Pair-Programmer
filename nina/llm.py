"""
Cliente del servidor del modelo (API compatible con OpenAI, por streaming). Sin dependencias de UI.
"""
import json
import time
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

# --- DRIVERS POR SERVIDOR (Fase 0.4) ---
class DriverBase:
    def preparar_payload(self, mensajes, modelo):
        return {
            "model": modelo,
            "messages": mensajes,
            "stream": True
        }

class OllamaDriver(DriverBase):
    def preparar_payload(self, mensajes, modelo):
        payload = super().preparar_payload(mensajes, modelo)
        # Ollama necesita num_ctx explícito para no recortar contexto grande en silencio
        ctx_size = gestor_config.perfil_actual.get("max_tokens_prompt", 12000) + 4000
        payload["options"] = {"num_ctx": ctx_size}
        return payload

class LlamaCppDriver(DriverBase):
    def preparar_payload(self, mensajes, modelo):
        payload = super().preparar_payload(mensajes, modelo)
        payload["cache_prompt"] = True 
        payload["stream_options"] = {"include_usage": True}
        return payload

def obtener_driver(tipo: str) -> DriverBase:
    if tipo == "ollama":
        return OllamaDriver()
    elif tipo == "llama.cpp":
        return LlamaCppDriver()
    return DriverBase()
# ---------------------------------------

def detectar_bucle(texto, tamaño_ventana=50, max_repeticiones=3):
    """
    Busca si los últimos 'tamaño_ventana' caracteres se repiten
    consecutivamente demasiadas veces al final del texto.
    """
    if len(texto) < tamaño_ventana * max_repeticiones:
        return False
        
    fragmento_final = texto[-tamaño_ventana:]
    
    repeticiones = 1
    for i in range(1, max_repeticiones):
        inicio = -(tamaño_ventana * (i + 1))
        fin = -(tamaño_ventana * i)
        if texto[inicio:fin] == fragmento_final:
            repeticiones += 1
        else:
            break
            
    return repeticiones >= max_repeticiones

def stream_llm(
    mensajes: list,
    on_fragmento: Optional[Callable[[str], None]] = None,
    on_pensamiento: Optional[Callable[[str], None]] = None,
    url: Optional[str] = None,
    modelo: Optional[str] = None,
    on_estadisticas: Optional[Callable[[dict], None]] = None,
) -> str:
    """Llama al modelo por streaming usando el driver adecuado según la configuración."""
    perfil = gestor_config.perfil_actual
    destino = url or perfil["url"]
    modelo_final = modelo or perfil["modelo"]
    tipo = perfil.get("tipo", "ollama")
    api_key = perfil.get("api_key", "") # <-- Nueva lectura de API Key

    driver = obtener_driver(tipo)
    payload = driver.preparar_payload(mensajes, modelo_final)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None # <-- Header

    t0, t_primero, usage, timings = time.monotonic(), None, {}, {}
    razonamiento, contenido, finish = "", "", None
    ult_pens, ult_resp = "", ""
    try:
        # Añadimos headers=headers al Client
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

                    # Verificamos si el modelo se trabó repitiendo su propio razonamiento
                    if razonamiento and len(razonamiento) % 300 < 10: 
                        if detectar_bucle(razonamiento):
                            raise ErrorLLM("🛑 Bucle degenerativo detectado en el razonamiento. Abortando y forzando reintento.")

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
            "Revisá la configuración de max_tokens_prompt."
        )
    if on_estadisticas:
        t_fin = time.monotonic()
        salida = usage.get("completion_tokens") or timings.get("predicted_n")

        tks_pensamiento = len(ult_pens) // 3 if ult_pens else 0
        tks_codigo = (salida - tks_pensamiento) if salida and salida > tks_pensamiento else (len(ult_resp) // 3)

        tps = timings.get("predicted_per_second")
        if not tps and salida and t_primero and t_fin > t_primero:
            tps = salida / (t_fin - t_primero)

        on_estadisticas({
            "tokens_entrada": usage.get("prompt_tokens"),
            "tokens_salida": salida,
            "tokens_pens": tks_pensamiento,
            "tokens_cod": tks_codigo,
            "tps": tps,
            "seg_total": t_fin - t0,
            "seg_primer_token": (t_primero - t0) if t_primero else None,
        })
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