"""
Arma el comando de llama-server a partir del perfil de config.py (fuente única de verdad): así el .bat no se
mantiene a mano ni queda descoordinado con lo que NINA manda en cada pedido. Sin dependencias de UI.
"""
import ntpath
import re
import subprocess
from typing import Optional
from urllib.parse import urlparse


class ErrorServidor(Exception):
    """El perfil no tiene lo necesario para lanzar un servidor (falta la sección `servidor`, etc.)."""


def puerto_de_url(url: str, defecto: int = 8080) -> int:
    try:
        return urlparse(url).port or defecto
    except ValueError:
        return defecto


def argumentos(perfil: dict) -> list:
    """Lista de argumentos (incluye el ejecutable). El puerto sale de la `url` del perfil; el muestreo, del perfil."""
    srv = perfil.get("servidor")
    if not srv:
        raise ErrorServidor("Este perfil no tiene sección `servidor` (¿es un perfil de Ollama?).")
    for clave in ("exe", "modelo_gguf"):
        if not srv.get(clave):
            raise ErrorServidor(f"Falta `servidor.{clave}` en la configuración.")

    m = perfil.get("muestreo") or {}
    a = [srv["exe"], "-m", srv["modelo_gguf"]]
    if srv.get("paralelo"):
        a += ["-np", str(srv["paralelo"])]
    if srv.get("flash_attn"):
        a += ["-fa", "on"]
    if srv.get("contexto"):
        a += ["-c", str(srv["contexto"])]
    if srv.get("cache_k"):
        a += ["-ctk", str(srv["cache_k"])]
    if srv.get("cache_v"):
        a += ["-ctv", str(srv["cache_v"])]
    if srv.get("jinja"):
        a += ["--jinja"]
    a += ["--port", str(puerto_de_url(perfil.get("url", "")))]
    for flag, clave in (("--temp", "temperature"), ("--top-p", "top_p"), ("--top-k", "top_k"),
                        ("--min-p", "min_p"), ("--repeat-penalty", "repeat_penalty"),
                        ("--presence-penalty", "presence_penalty")):
        if m.get(clave) is not None:
            a += [flag, str(m[clave])]
    if srv.get("reasoning_preserve") is False:
        a += ["--no-reasoning-preserve"]
    if srv.get("gpu_layers") is not None:
        a += ["--n-gpu-layers", str(srv["gpu_layers"])]
    if srv.get("n_cpu_moe") is not None:
        a += ["--n-cpu-moe", str(srv["n_cpu_moe"])]
    if srv.get("load_mode"):
        a += ["--load-mode", str(srv["load_mode"])]
    a += [str(x) for x in (srv.get("extra") or [])]
    return a


def _es_flag(x: str) -> bool:
    return bool(re.match(r"^--?[A-Za-z]", x))   # "-0.5" (valor negativo) no es un flag


def _comillas(x: str) -> str:
    return f'"{x}"' if (" " in x or "\\" in x or ":" in x) else x


def linea_comando(perfil: dict) -> str:
    """El comando en una sola línea, listo para pegar en una consola de Windows."""
    return " ".join(_comillas(x) for x in argumentos(perfil))


def generar_bat(perfil: dict, titulo: Optional[str] = None) -> str:
    """Contenido de un .bat equivalente (flags agrupados de a pares, uno por línea, con ^ de continuación)."""
    a = argumentos(perfil)
    exe, resto = a[0], a[1:]
    lineas, i = [], 0
    while i < len(resto):
        if _es_flag(resto[i]) and i + 1 < len(resto) and not _es_flag(resto[i + 1]):
            lineas.append(f"{resto[i]} {_comillas(resto[i + 1])}")
            i += 2
        else:
            lineas.append(_comillas(resto[i]))
            i += 1
    titulo = titulo or f"Llama Server - {perfil.get('modelo', '')}"
    carpeta = ntpath.dirname(exe)
    cuerpo = " ^\r\n  ".join([_comillas(exe)] + lineas)
    return (
        "@echo off\r\n"
        f"title {titulo}\r\n"
        "REM Generado por NINA (python frontend.py servidor --bat ...). No lo edites a mano: cambiá la config.\r\n"
        + (f'cd /d "{carpeta}"\r\n' if carpeta else "")
        + cuerpo + "\r\n\r\npause\r\n"
    )


def lanzar(perfil: dict) -> int:
    """Ejecuta llama-server en primer plano (como el .bat) y devuelve su código de salida."""
    return subprocess.call(argumentos(perfil))


def contexto_del_servidor(url: str, timeout: float = 3.0) -> Optional[int]:
    """Fase 5.2: n_ctx real con el que arrancó llama-server (GET /props), o None si no responde / no lo informa.
    `url` es la API_URL de config (…/v1/chat/completions); /props cuelga de la raíz del servidor."""
    import json
    import urllib.request
    base = url.split("/v1/")[0].rstrip("/")
    try:
        with urllib.request.urlopen(base + "/props", timeout=timeout) as r:
            datos = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    n = (datos.get("default_generation_settings") or {}).get("n_ctx") or datos.get("n_ctx")
    return int(n) if isinstance(n, (int, float)) and n > 0 else None
