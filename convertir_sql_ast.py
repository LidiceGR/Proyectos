"""Analiza scripts T-SQL con SQLFluff, sin PowerShell ni ScriptDOM.

Produce un arbol de segmentos de parseo (estructura tipo AST), mas errores
por cada archivo. No es el AST semantico de Microsoft ScriptDOM.

Probado para sintaxis de Python, pendiente de validar con SQLFluff instalado
contra los procedimientos reales.

Dependencia Python 3.8: python -m pip install "sqlfluff==3.3.1"
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def leer_sql(ruta: Path) -> str:
    """Maneja UTF-8/UTF-16 con BOM y codificaciones Windows frecuentes."""
    contenido = ruta.read_bytes()
    if contenido.startswith((b"\xff\xfe", b"\xfe\xff")):
        return contenido.decode("utf-16")
    if contenido.startswith(b"\xef\xbb\xbf"):
        return contenido.decode("utf-8-sig")
    for codificacion in ("utf-8", "cp1252"):
        try:
            return contenido.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return contenido.decode("latin-1")


def pos_nodo(nodo: Any) -> Dict[str, int]:
    posicion = getattr(nodo, "pos_marker", None)
    if posicion is None:
        return {}
    try:
        linea, columna = posicion.source_position()
        return {"linea": linea, "columna": columna}
    except (AttributeError, TypeError, ValueError):
        return {}


def extraer_arbol(nodo: Any, inconsistencias: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Serializa el arbol de segmentos. No altera ni ejecuta el SQL."""
    tipo = str(getattr(nodo, "type", type(nodo).__name__))
    resultado: Dict[str, Any] = {"tipo": tipo}
    resultado.update(pos_nodo(nodo))
    hijos = getattr(nodo, "segments", ())
    if tipo in {"unparsable", "unlexable"}:
        inconsistencias.append({"tipo": tipo, **pos_nodo(nodo), "fragmento": nodo.raw[:250]})
    if hijos:
        resultado["hijos"] = [extraer_arbol(hijo, inconsistencias) for hijo in hijos]
    else:
        resultado["texto"] = str(getattr(nodo, "raw", ""))
    return resultado


def error_sqlfluff(error: Any) -> Dict[str, Any]:
    try:
        codigo = str(error.rule_code())
    except (AttributeError, TypeError):
        codigo = type(error).__name__
    try:
        mensaje = str(error.desc())
    except (AttributeError, TypeError):
        mensaje = str(error)
    return {
        "codigo": codigo,
        "linea": getattr(error, "line_no", None),
        "columna": getattr(error, "line_pos", None),
        "mensaje": mensaje,
    }


def convertir_archivo(origen: Path, relativo: Path, analizador: Any) -> Dict[str, Any]:
    resultado: Dict[str, Any] = {
        "archivo_origen": relativo.as_posix(),
        "parser": "SQLFluff (dialecto tsql)",
        "tipo_arbol": "arbol_de_segmentos_sintacticos",
        "estado": "error",
        "errores": [],
        "zonas_no_analizadas": [],
        "arbol": None,
    }
    try:
        sql = leer_sql(origen)
        analisis = analizador.parse_string(sql, fname=str(origen))
        if analisis.tree is None:
            resultado["errores"] = [error_sqlfluff(e) for e in analisis.violations]
            if not resultado["errores"]:
                resultado["errores"] = [{"mensaje": "No se produjo ningun arbol de parseo"}]
            return resultado
        zonas: List[Dict[str, Any]] = []
        resultado["arbol"] = extraer_arbol(analisis.tree, zonas)
        resultado["zonas_no_analizadas"] = zonas
        # Ignorar avisos sobre formato/estilo: interesan solo errores lexicos/sintacticos.
        errores = [error_sqlfluff(e) for e in analisis.violations]
        resultado["errores"] = [e for e in errores if e["codigo"] in {"PRS", "LXR", "SQLParseError", "SQLLexError"}]
        resultado["estado"] = "parcial" if zonas or resultado["errores"] else "ok"
    except Exception as exc:
        resultado["errores"] = [{"codigo": type(exc).__name__, "mensaje": str(exc)}]
    return resultado


def main() -> int:
    ap = argparse.ArgumentParser(description="Convertir archivos .sql T-SQL en arboles JSON con SQLFluff")
    ap.add_argument("--origen", required=True, type=Path)
    ap.add_argument("--destino", required=True, type=Path)
    ap.add_argument("--max-archivos", type=int, default=0, help="0 = todos; por defecto 0")
    opciones = ap.parse_args()

    try:
        from sqlfluff.core import FluffConfig, Linter
        import sqlfluff
    except ImportError:
        print('Falta SQLFluff. Instala con: python -m pip install "sqlfluff==3.3.1"', file=sys.stderr)
        return 2

    origen = opciones.origen.expanduser().resolve()
    destino = opciones.destino.expanduser().resolve()
    if not origen.is_dir():
        ap.error("La carpeta de origen no existe: " + str(origen))
    if origen == destino:
        ap.error("La carpeta origen y la de destino no pueden ser iguales")
    if opciones.max_archivos < 0:
        ap.error("--max-archivos debe ser >= 0")

    analizador = Linter(config=FluffConfig(overrides={"dialect": "tsql", "templater": "raw"}))
    archivos = [p for p in origen.rglob("*") if p.is_file() and p.suffix.lower() == ".sql"
                and destino not in p.parents]
    archivos.sort(key=lambda p: p.as_posix().casefold())
    if opciones.max_archivos:
        archivos = archivos[:opciones.max_archivos]
    destino.mkdir(parents=True, exist_ok=True)

    conteos: Counter = Counter()
    detalles: List[Dict[str, Any]] = []
    for numero, ruta in enumerate(archivos, 1):
        relativo = ruta.relative_to(origen)
        ruta_salida = destino / relativo.parent / (relativo.name + ".ast.json")
        ruta_salida.parent.mkdir(parents=True, exist_ok=True)
        resultado = convertir_archivo(ruta, relativo, analizador)
        try:
            ruta_salida.write_text(json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            resultado["estado"] = "error"
            resultado["errores"].append({"codigo": type(exc).__name__, "mensaje": str(exc)})
        estado = resultado["estado"]
        conteos[estado] += 1
        detalles.append({"archivo": relativo.as_posix(), "estado": estado,
                         "salida": str(ruta_salida), "errores": len(resultado["errores"]),
                         "zonas_no_analizadas": len(resultado["zonas_no_analizadas"])})
        print("[{}/{}] {} -> {}".format(numero, len(archivos), relativo.as_posix(), estado))

    resumen = {
        "fecha_utc": datetime.now(timezone.utc).isoformat(),
        "parser": "SQLFluff {}".format(sqlfluff.__version__),
        "origen": str(origen), "destino": str(destino),
        "archivos_procesados": len(archivos),
        "correctos": conteos["ok"], "parciales": conteos["parcial"], "con_error": conteos["error"],
        "resultados": detalles,
    }
    (destino / "_resumen.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nTerminado: {} archivos, {} correctos, {} parciales, {} con error.".format(
        len(archivos), conteos["ok"], conteos["parcial"], conteos["error"]))
    return 0 if not conteos["error"] else 1


if __name__ == "__main__":
    sys.exit(main())
