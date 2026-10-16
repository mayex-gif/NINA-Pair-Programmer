"""
Fase 4.6 — guardas anti-bucle, tokens de pensamiento vs. código, reintentos baratos, config como fuente única
y generación del comando/.bat de llama-server.

`stream_llm` se prueba contra un servidor SSE falso real (http.server en un hilo), con la config aislada: ningún
test lee ni escribe ~/.nina/config.json.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nina import config as cfg  # noqa: E402
from nina import contexto as cx  # noqa: E402
from nina import llm  # noqa: E402
from nina import servidor as srv  # noqa: E402

BLOQUE = "<<<<<<< SEARCH\nb = 2\n=======\nb = 20\n>>>>>>> REPLACE"
LINEA_REPETIDA = "Wait, let me double check the same thing again, maybe the user means something else.\n"


# ------------------------------------------------------------------ servidor SSE falso
class _Gestor:
    """Sustituto de gestor_config: un perfil fijo, sin tocar el disco."""
    def __init__(self, perfil):
        self.perfil_actual = perfil


def _perfil(tipo="llama.cpp", **limites):
    return {
        "tipo": tipo, "url": "http://localhost:1/v1/chat/completions", "modelo": "m", "max_tokens_prompt": 12000,
        "muestreo": {"temperature": 0.3, "top_p": 0.95, "top_k": 20, "min_p": 0.05, "repeat_penalty": 1.05,
                     "presence_penalty": 0.0},
        "limites": {"max_tokens_salida": 1000, "max_tokens_pensamiento": 0, "detectar_bucles": True,
                    "reenviar_respuesta_hasta_tokens": 64000, **limites},
    }


@pytest.fixture
def servidor_sse(monkeypatch):
    """Devuelve `levantar(eventos)`: un servidor que emite los eventos SSE (dicts o callables que los generan)."""
    abiertos = []

    def levantar(eventos, perfil=None):
        recibido = {}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                recibido["payload"] = json.loads(self.rfile.read(n))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for ev in (eventos() if callable(eventos) else eventos):
                        self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    recibido["cortado_por_el_cliente"] = True

        s = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        abiertos.append(s)
        p = perfil or _perfil()
        p["url"] = f"http://127.0.0.1:{s.server_port}/v1/chat/completions"
        monkeypatch.setattr(llm, "gestor_config", _Gestor(p))
        return recibido

    yield levantar
    for s in abiertos:
        s.shutdown()
        s.server_close()


def _d(razon="", contenido="", fin=None, **extra):
    delta = {}
    if razon:
        delta["reasoning_content"] = razon
    if contenido:
        delta["content"] = contenido
    return {"choices": [{"delta": delta, "finish_reason": fin}], **extra}


# ------------------------------------------------------------------ detectar_bucle
def test_detectar_bucle_linea_repetida():
    assert llm.detectar_bucle(LINEA_REPETIDA * 12)


def test_detectar_bucle_no_confunde_codigo_citado_con_un_bucle():
    """Caso real (probar_reintento.py, Qwen3.6): al redactar el bloque el modelo cita 5+ veces `def promedio(items):`."""
    texto = ("Original:\ndef promedio(items):\n    return total(items) / len(items)\n" * 1 +
             "SEARCH block:\ndef promedio(items):\n    return total(items) / len(items)\n" +
             "REPLACE block:\ndef promedio(items):\n    if len(items) == 0:\n        return 0\n    return total(items) / len(items)\n" +
             "Check: def promedio(items):\nFinal: def promedio(items):\n    return total(items) / len(items)\n")
    assert llm.detectar_bucle(texto) is None


def test_detectar_bucle_no_da_falsos_positivos_con_texto_variado():
    texto = "".join(f"Paso {i}: reviso el componente número {i} y su estado interno completo\n" for i in range(60))
    assert llm.detectar_bucle(texto) is None
    assert llm.detectar_bucle(LINEA_REPETIDA * 2) is None             # repetir un poco es normal


def test_detectar_bucle_cola_repetida_sin_saltos_de_linea():
    unidad = "y entonces vuelvo a revisar lo mismo una vez más, " * 4
    assert llm.detectar_bucle("introducción distinta " * 20 + unidad * 8)


# ------------------------------------------------------------------ stream_llm contra un servidor falso
def test_stream_corta_un_bucle_de_razonamiento(servidor_sse):
    rec = servidor_sse(lambda: (_d(razon=LINEA_REPETIDA) for _ in range(400)))
    pens = []
    with pytest.raises(llm.CorteLLM) as e:
        llm.stream_llm([{"role": "user", "content": "x"}], on_pensamiento=pens.append)
    assert e.value.motivo == "bucle" and "Bucle de razonamiento" in str(e.value)
    assert e.value.pensamiento and e.value.respuesta == ""
    assert e.value.stats["tokens_salida"] and e.value.stats["pens_exactos"] is False     # estimado: al cortar no llega usage
    assert len(e.value.pensamiento) < 400 * len(LINEA_REPETIDA)                          # no se leyó todo el stream


def test_stream_corta_si_el_razonamiento_supera_el_tope(servidor_sse):
    servidor_sse(lambda: (_d(razon=f"Analizo el punto número {i} con mucho detalle y cuidado\n") for i in range(2000)),
                 _perfil(max_tokens_pensamiento=300, detectar_bucles=False))
    with pytest.raises(llm.CorteLLM) as e:
        llm.stream_llm([{"role": "user", "content": "x"}])
    assert e.value.motivo == "limite_pensamiento" and "max_tokens_pensamiento" in str(e.value)


def test_stream_no_corta_si_ya_hay_respuesta(servidor_sse):
    """Las guardas miran solo el razonamiento: una respuesta larga y repetitiva (código) no es un bucle."""
    codigo = "".join(f"    campo_{i % 2} = valor_largo_repetido_para_probar_el_detector\n" for i in range(40))
    servidor_sse([_d(razon="Plan: agregar los campos."), *[_d(contenido=codigo[k:k + 200]) for k in range(0, len(codigo), 200)],
                  _d(fin="stop")], _perfil(max_tokens_pensamiento=50))
    assert llm.stream_llm([{"role": "user", "content": "x"}]).startswith("campo_0")


def test_stream_finish_length_es_un_corte_por_longitud(servidor_sse):
    servidor_sse([_d(razon="pienso"), _d(contenido="<<<<<<< SEARCH\nb"), _d(fin="length")])
    with pytest.raises(llm.CorteLLM) as e:
        llm.stream_llm([{"role": "user", "content": "x"}])
    assert e.value.motivo == "longitud" and "max_tokens_salida" in str(e.value)
    assert e.value.respuesta.startswith("<<<<<<< SEARCH")


def test_stream_normal_separa_pensamiento_y_codigo_con_usage_exacto(servidor_sse):
    servidor_sse([
        _d(razon="pienso un poco"), _d(contenido=BLOQUE), _d(fin="stop"),
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                                  "completion_tokens_details": {"reasoning_tokens": 30}}},
    ])
    stats = {}
    r = llm.stream_llm([{"role": "user", "content": "x"}], on_estadisticas=stats.update)
    assert r == BLOQUE
    assert (stats["tokens_entrada"], stats["tokens_salida"]) == (100, 50)
    assert (stats["tokens_pensamiento"], stats["tokens_codigo"], stats["pens_exactos"]) == (30, 20, True)


def test_stream_sin_reasoning_tokens_reparte_en_proporcion(servidor_sse):
    servidor_sse([_d(razon="a" * 300), _d(contenido="b" * 100), _d(fin="stop"),
                  {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 40}}])
    stats = {}
    llm.stream_llm([{"role": "user", "content": "x"}], on_estadisticas=stats.update)
    assert (stats["tokens_pensamiento"], stats["tokens_codigo"], stats["pens_exactos"]) == (30, 10, False)


def test_el_payload_lleva_el_muestreo_del_perfil_y_razonar_false(servidor_sse):
    rec = servidor_sse([_d(contenido=BLOQUE), _d(fin="stop")])
    llm.stream_llm([{"role": "user", "content": "x"}], razonar=False)
    p = rec["payload"]
    assert p["temperature"] == 0.3 and p["top_k"] == 20 and p["min_p"] == 0.05 and p["repeat_penalty"] == 1.05
    assert p["max_tokens"] == 1000 and p["chat_template_kwargs"] == {"enable_thinking": False}
    rec = servidor_sse([_d(contenido=BLOQUE), _d(fin="stop")], _perfil("ollama"))
    llm.stream_llm([{"role": "user", "content": "x"}], razonar=False)
    assert rec["payload"]["think"] is False and rec["payload"]["options"]["top_k"] == 20
    rec = servidor_sse([_d(contenido=BLOQUE), _d(fin="stop")])
    llm.stream_llm([{"role": "user", "content": "x"}])
    assert "chat_template_kwargs" not in rec["payload"]


# ------------------------------------------------------------------ generar_cambio tras un corte
def _ctx():
    return cx.Contexto(ruta=None, clave="a.py", original="a = 1\nb = 2\nc = 3\n", eol="\n",
                       mensajes=[{"role": "system", "content": "S"}, {"role": "user", "content": "U"}],
                       tokens=1, mapa_usados=0, mapa_total=0, hay_convenciones=False)


def test_tras_un_corte_se_reintenta_sin_razonar_y_sin_reenviar_la_respuesta():
    llamadas = []

    def llamar(mensajes, **opciones):
        llamadas.append((list(mensajes), opciones))
        if len(llamadas) == 1:
            raise llm.CorteLLM("Bucle de razonamiento detectado", "bucle", "pensando…", "", {"tokens_salida": 5})
        return BLOQUE

    avisos = []
    res = cx.generar_cambio(_ctx(), None, llamar, lambda n, errs: avisos.append((n, errs)))
    assert res.es_valida and res.intentos == 2
    assert llamadas[0][1] == {} and llamadas[1][1] == {"razonar": False}
    msgs2 = llamadas[1][0]
    assert [m["role"] for m in msgs2] == ["system", "user"]                    # roles alternan, sin eco del assistant
    assert "AVISO: el intento anterior se cortó (bucle)" in msgs2[-1]["content"] and msgs2[-1]["content"].startswith("U")
    assert avisos and "Bucle" in avisos[0][1]["a.py"][0]


def test_si_todos_los_intentos_se_cortan_queda_un_error_claro(monkeypatch):
    monkeypatch.setattr(cx, "MAX_INTENTOS", 2)

    def llamar(mensajes, **o):
        raise llm.CorteLLM("tope", "longitud", "", "parcial")

    res = cx.generar_cambio(_ctx(), None, llamar)
    assert not res.es_valida and res.intentos == 2 and res.respuesta == "parcial"
    assert "longitud" in res.cambios[0].errores[0]


# ------------------------------------------------------------------ debe_reenviar
def _p(**kw):
    return {"limites": {"reenviar_respuesta_hasta_tokens": 1000, "max_tokens_salida": 500},
            "servidor": {"contexto": kw.get("contexto", 65536)}}


def test_debe_reenviar_solo_si_pesa_menos_del_tope():
    msgs = [{"role": "user", "content": "x" * 300}]
    assert cx.debe_reenviar("y" * 2999, msgs, _p())                      # ~999 tokens
    assert not cx.debe_reenviar("y" * 3300, msgs, _p())                  # ~1100 tokens > 1000


def test_debe_reenviar_respeta_el_contexto_del_servidor():
    msgs = [{"role": "user", "content": "x" * 3000}]                      # ~1000 tokens de prompt
    assert cx.debe_reenviar("y" * 300, msgs, _p(contexto=65536))
    assert not cx.debe_reenviar("y" * 300, msgs, _p(contexto=2000))       # prompt + respuesta + reserva no entran


def test_en_el_reintento_una_respuesta_gigante_se_reemplaza_por_un_aviso(monkeypatch):
    monkeypatch.setattr(cx, "debe_reenviar", lambda *a, **k: False)
    llamadas = []

    def llamar(mensajes, **o):
        llamadas.append(list(mensajes))
        return "<<<<<<< SEARCH\nzzz\n=======\nq\n>>>>>>> REPLACE" if len(llamadas) == 1 else BLOQUE

    res = cx.generar_cambio(_ctx(), None, llamar)
    assert res.es_valida
    assert llamadas[1][2] == {"role": "assistant", "content": "(respuesta anterior omitida por su tamaño)"}


# ------------------------------------------------------------------ config: fuente única
def test_completar_config_migra_una_config_vieja():
    vieja = {"perfil_activo": "Llama Server", "perfiles": {
        "Llama Server": {"tipo": "llama.cpp", "url": "http://localhost:8099/v1/chat/completions", "modelo": "mio",
                         "temperatura": 0.1, "muestreo": {"top_k": 64}},
        "Otro": {"tipo": "openai", "url": "http://x/v1/chat/completions", "modelo": "z"},
    }}
    c = cfg.completar_config(vieja)
    ls = c["perfiles"]["Llama Server"]
    assert "temperatura" not in ls                                        # la clave vieja ya no manda
    assert ls["muestreo"]["top_k"] == 64 and ls["muestreo"]["temperature"] == cfg.DEFAULT_CONFIG["perfiles"]["Llama Server"]["muestreo"]["temperature"]     # lo guardado manda; lo faltante se completa
    assert ls["limites"]["reenviar_respuesta_hasta_tokens"] == cfg.LIMITES_DEFECTO["reenviar_respuesta_hasta_tokens"] and ls["limites"]["profundidad_mapa"] == cfg.LIMITES_DEFECTO["profundidad_mapa"]
    assert ls["servidor"]["contexto"] == 65536 and ls["url"].endswith(":8099/v1/chat/completions")
    assert c["perfiles"]["Otro"]["muestreo"]["top_p"] == 0.95 and "servidor" not in c["perfiles"]["Otro"]
    assert "Ollama Local" in c["perfiles"]                                  # perfiles por defecto presentes
    assert "perfiles" in cfg.DEFAULT_CONFIG and "temperatura" not in json.dumps(cfg.DEFAULT_CONFIG)


def test_completar_config_no_modifica_el_original():
    original = {"perfil_activo": "X", "perfiles": {"X": {"url": "u", "modelo": "m"}}}
    cfg.completar_config(original)
    assert original == {"perfil_activo": "X", "perfiles": {"X": {"url": "u", "modelo": "m"}}}


# ------------------------------------------------------------------ servidor / .bat
def _perfil_llama():
    return json.loads(json.dumps(cfg.DEFAULT_CONFIG["perfiles"]["Llama Server"]))


def test_argumentos_salen_del_perfil():
    p = _perfil_llama()
    p["url"] = "http://localhost:8099/v1/chat/completions"
    a = srv.argumentos(p)
    assert a[0].endswith("llama-server.exe") and a[1:3] == ["-m", p["servidor"]["modelo_gguf"]]
    par = {a[i]: a[i + 1] for i in range(1, len(a) - 1) if a[i].startswith("-")}
    assert par["--port"] == "8099" and par["-c"] == str(p["servidor"]["contexto"]) and par["--temp"] == str(p["muestreo"]["temperature"]) and par["--top-k"] == str(p["muestreo"]["top_k"])
    assert par["-ctk"] == "q8_0" and par["--n-cpu-moe"] == str(p["servidor"]["n_cpu_moe"]) and par["--load-mode"] == "none"
    assert "--jinja" in a and "--no-reasoning-preserve" in a and ("-fa", "on") in zip(a, a[1:])


def test_el_muestreo_del_bat_y_el_que_manda_nina_son_el_mismo_dato():
    p = _perfil_llama()
    p["muestreo"]["temperature"] = 0.7
    assert srv.argumentos(p)[srv.argumentos(p).index("--temp") + 1] == "0.7"
    assert llm.DriverBase().preparar_payload([], "m", p)["temperature"] == 0.7


def test_flags_opcionales_se_omiten_y_extra_se_agrega():
    p = _perfil_llama()
    p["servidor"].update(n_cpu_moe=None, load_mode=None, flash_attn=False, reasoning_preserve=True, extra=["--cache-reuse", "256"])
    a = srv.argumentos(p)
    assert "--n-cpu-moe" not in a and "--load-mode" not in a and "-fa" not in a and "--no-reasoning-preserve" not in a
    assert a[-2:] == ["--cache-reuse", "256"]


def test_valor_negativo_no_se_confunde_con_un_flag_en_el_bat():
    p = _perfil_llama()
    p["muestreo"]["presence_penalty"] = -0.5
    bat = srv.generar_bat(p)
    assert "--presence-penalty -0.5 ^" in bat


def test_generar_bat_es_crlf_con_continuaciones_y_cd():
    bat = srv.generar_bat(_perfil_llama())
    assert bat.startswith("@echo off\r\n") and "\n" not in bat.replace("\r\n", "")
    assert 'cd /d "C:\\llama-server"' in bat and bat.rstrip().endswith("pause")
    assert " ^\r\n  -m " in bat and "--port 8081" in bat


def test_perfil_sin_servidor_da_un_error_claro():
    with pytest.raises(srv.ErrorServidor):
        srv.argumentos(cfg.DEFAULT_CONFIG["perfiles"]["Ollama Local"])
    p = _perfil_llama()
    p["servidor"]["modelo_gguf"] = ""
    with pytest.raises(srv.ErrorServidor):
        srv.argumentos(p)


def test_puerto_de_url():
    assert srv.puerto_de_url("http://localhost:8081/v1/chat/completions") == 8081
    assert srv.puerto_de_url("http://localhost/v1") == 8080 and srv.puerto_de_url("basura::") == 8080
