#!/usr/bin/env python3
"""Extrae inventario y relaciones de AST JSON de SQLFluff (T-SQL).

No consulta SQL Server, no ejecuta scripts y no infiere reglas de negocio.
Compatible con Python 3.8+, usa solo la biblioteca estandar.

Ejemplo:
  python generar_inventario_relaciones.py --origen "D:/Proyectos/agente/AST" --destino "D:/Proyectos/agente/metadatos"
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.1.0"
TIPOS_DML = {
    "insert_statement": "INSERTA",
    "update_statement": "ACTUALIZA",
    "delete_statement": "ELIMINA",
    "merge_statement": "COMBINA",
}
TIPOS_EJECUCION = {
    "execute_statement", "execute_script_segment", "execute_procedure_statement",
    "exec_statement", "execute_segment", "execute_script_statement", "execute_command",
}


def recorrer(nodo):
    """Recorre todos los nodos en profundidad."""
    yield nodo
    for hijo in nodo.get("hijos", []):
        yield from recorrer(hijo)


def terminales(nodo):
    for n in recorrer(nodo):
        if "hijos" not in n:
            yield n


def texto(nodo):
    return "".join(n.get("texto", "") for n in terminales(nodo))


def compacto(nodo, max_largo=0):
    s = " ".join(texto(nodo).split())
    return s[:max_largo] if max_largo else s


def hijos_tipo(nodo, tipo):
    return [h for h in nodo.get("hijos", []) if h.get("tipo") == tipo]


def descendientes_tipo(nodo, tipo):
    return [n for n in recorrer(nodo) if n.get("tipo") == tipo]


def nombre_objeto(nodo):
    # Las referencias pueden tener partes de esquema, puntos y corchetes.
    return re.sub(r"\s+", "", texto(nodo)).strip()


def nombre_destino(nodo):
    # No normalizamos a dbo: si el esquema no esta en el SQL, no lo inventamos.
    return nombre_objeto(nodo)


def parametros_de(procedimiento):
    lista = next((h for h in procedimiento.get("hijos", [])
                  if h.get("tipo") == "procedure_parameter_list"), None)
    if not lista:
        return []
    resultado = []
    actual = None
    for h in lista.get("hijos", []):
        tipo, value = h.get("tipo"), h.get("texto", "")
        if tipo == "raw" and value.startswith("@"):
            if actual:
                resultado.append(actual)
            actual = {"nombre": value, "tipo_dato": None, "linea": h.get("linea")}
        elif tipo == "data_type" and actual and not actual["tipo_dato"]:
            actual["tipo_dato"] = compacto(h)
    if actual:
        resultado.append(actual)
    return resultado


def nombre_procedimiento(procedimiento):
    for h in procedimiento.get("hijos", []):
        if h.get("tipo") in ("object_reference", "procedure_reference"):
            return nombre_objeto(h)
    return None


def referencias_tabla(nodo):
    tablas = []
    for ref in descendientes_tipo(nodo, "table_reference"):
        nombre = nombre_destino(ref)
        if nombre:
            tablas.append({"nombre": nombre, "linea": ref.get("linea")})
    return tablas


def retornos_rama(if_nodo):
    """Retornos encontrados en la rama del IF (incluye IF anidados)."""
    resultado = []
    for h in if_nodo.get("hijos", []):
        if h.get("tipo") == "if_clause":
            continue
        for n in descendientes_tipo(h, "return_segment"):
            resultado.append({"linea": n.get("linea"), "sentencia": compacto(n)})
    return resultado


def ejecutar_candidato(nodo):
    tipo = nodo.get("tipo", "")
    if tipo in TIPOS_EJECUCION or re.match(r"^exec(?:ute)?(?:_|$)", tipo):
        return True
    # Algunas gramaticas de SQLFluff etiquetan EXEC como statement generico.
    return False


def analizar_exec(nodo):
    """Extrae el nombre estatico; marca sin resolver los EXEC dinamicos."""
    sql = compacto(nodo)
    # Caso habitual: EXEC SP, EXEC dbo.SP, EXEC @rc = dbo.SP, EXEC [dbo].[SP].
    m = re.match(r"(?is)^exec(?:ute)?\s+(.+)$", sql)
    if not m:
        return None, sql, "no_reconocido"
    cola = m.group(1).strip()
    # EXECUTE AS USER no es llamada a un procedimiento.
    if re.match(r"(?i)^as\s+(?:user|login)\b", cola):
        return None, sql, "no_es_llamada"
    # Variable de retorno opcional.
    cola = re.sub(r"(?is)^@\w+\s*=\s*", "", cola)
    if not cola or cola[0] in "(@":
        return None, sql, "dinamico_o_variable"
    # Captura partes de nombres entre corchetes, dobles comillas o literales simples.
    identificador = r'(?:\[[^\]]+\]|"[^"]+"|[\w#]+)'
    nombre = re.match(r"^({}(?:\s*\.\s*{})*)".format(identificador, identificador), cola)
    if not nombre:
        return None, sql, "no_reconocido"
    return re.sub(r"\s+", "", nombre.group(1)), sql, "estatico"


def analizar_procedimiento(proc, archivo_sql, archivo_ast, estado):
    nombre = nombre_procedimiento(proc)
    if not nombre:
        return None, [], ["No se identifico el nombre de procedimiento en {}".format(archivo_ast)]

    inv = {
        "tipo": "procedimiento",
        "nombre": nombre,
        "archivo_sql": archivo_sql,
        "archivo_ast": archivo_ast,
        "estado_parseo": estado,
        "linea_definicion": proc.get("linea"),
        "parametros": parametros_de(proc),
        "tablas": [],
        "llamadas": [],
        "condiciones": [],
        "operaciones": [],
    }
    relaciones = []
    advertencias = []
    tablas_por_nombre = defaultdict(lambda: {"operaciones": set(), "lineas": set()})
    llamadas = []
    condiciones = []
    operaciones = []
    ocurrencias = set()

    def registrar_tabla(ref, accion, contexto, linea_sentencia):
        tabla = ref["nombre"]
        linea = ref.get("linea") or linea_sentencia
        clave = (tabla.casefold(), accion, linea, tuple((x["linea"] for x in contexto)))
        if clave in ocurrencias:
            return
        ocurrencias.add(clave)
        tablas_por_nombre[tabla]["operaciones"].add(accion)
        tablas_por_nombre[tabla]["lineas"].add(linea)
        relaciones.append({
            "origen": {"tipo": "procedimiento", "nombre": nombre},
            "destino": {"tipo": "tabla", "nombre": tabla},
            "tipo": accion,
            "archivo_sql": archivo_sql,
            "linea": linea,
            "condiciones_contexto": list(contexto),
        })

    def procesar(nodo, contexto):
        tipo = nodo.get("tipo", "")
        linea = nodo.get("linea")

        if tipo == "if_then_statement":
            clausulas = hijos_tipo(nodo, "if_clause")
            if clausulas:
                clausula = clausulas[0]
                condicion = compacto(clausula)
                condicion_sola = re.sub(r"(?i)^if\s+", "", condicion).strip()
                refs = referencias_tabla(clausula)
                condiciones.append({
                    "linea": linea,
                    "expresion": condicion_sola,
                    "tablas_consultadas": sorted({r["nombre"] for r in refs}, key=str.casefold),
                    "retornos_en_rama": retornos_rama(nodo),
                    "clasificacion": "no_clasificada",
                    "evidencia": {"archivo_sql": archivo_sql, "linea": linea},
                })
                # En la condicion se leen tablas; el IF mismo aun no se cumple.
                for h in clausulas:
                    procesar(h, contexto)
                nuevo_contexto = list(contexto) + [{"linea": linea, "expresion": condicion_sola}]
                for h in nodo.get("hijos", []):
                    if h.get("tipo") not in ("if_clause", "elif_clause", "else_clause"):
                        procesar(h, nuevo_contexto)
                # ELSE/ELIF no heredan la condicion verdadera. Registramos su rama aparte.
                for h in nodo.get("hijos", []):
                    if h.get("tipo") in ("elif_clause", "else_clause"):
                        rama_alternativa = list(contexto) + [{
                            "linea": h.get("linea", linea),
                            "expresion": "RAMA_ALTERNATIVA_DEL_IF_EN_LINEA_{}".format(linea),
                        }]
                        procesar(h, rama_alternativa)
                return

        if tipo == "select_statement":
            refs = referencias_tabla(nodo)
            operaciones.append({"tipo": "SELECT", "linea": linea,
                                "tablas": sorted({r["nombre"] for r in refs}, key=str.casefold),
                                "condiciones_contexto": list(contexto)})
            for ref in refs:
                registrar_tabla(ref, "LEE", contexto, linea)
            # Evitar recorrer y registrar otra vez la misma SELECT.
            return

        if tipo in TIPOS_DML:
            accion = TIPOS_DML[tipo]
            refs_directas = [h for h in nodo.get("hijos", []) if h.get("tipo") == "table_reference"]
            tabla_objetivo = nombre_destino(refs_directas[0]) if refs_directas else None
            refs = referencias_tabla(nodo)
            operaciones.append({"tipo": accion, "linea": linea, "tabla_objetivo": tabla_objetivo,
                                "tablas_referenciadas": sorted({r["nombre"] for r in refs}, key=str.casefold),
                                "condiciones_contexto": list(contexto)})
            if refs_directas:
                registrar_tabla({"nombre": tabla_objetivo, "linea": refs_directas[0].get("linea")},
                                accion, contexto, linea)
            else:
                advertencias.append("{} en linea {}: no se identifico la tabla objetivo".format(accion, linea))
            for r in refs:
                if r["nombre"] != tabla_objetivo or (r.get("linea") != refs_directas[0].get("linea") if refs_directas else True):
                    registrar_tabla(r, "LEE", contexto, linea)
            return

        if ejecutar_candidato(nodo) or (
            tipo == "statement"
            and re.match(r"(?i)^exec(?:ute)?\s+", compacto(nodo))
            and not any(ejecutar_candidato(h) for h in recorrer(nodo) if h is not nodo)
        ):
            destino, sentencia, metodo = analizar_exec(nodo)
            llamadas.append({"nombre": destino, "linea": linea, "sentencia": sentencia,
                             "tipo_resolucion": metodo, "condiciones_contexto": list(contexto)})
            if destino:
                relaciones.append({
                    "origen": {"tipo": "procedimiento", "nombre": nombre},
                    "destino": {"tipo": "procedimiento", "nombre": destino},
                    "tipo": "EJECUTA", "archivo_sql": archivo_sql,
                    "linea": linea, "condiciones_contexto": list(contexto),
                })
            return

        for h in nodo.get("hijos", []):
            procesar(h, contexto)

    # Analizar solo el cuerpo; no confundir referencias en la declaracion.
    for child in proc.get("hijos", []):
        if child.get("tipo") == "procedure_statement":
            procesar(child, [])

    inv["tablas"] = [
        {"nombre": k, "operaciones": sorted(v["operaciones"]), "lineas": sorted(v["lineas"])}
        for k, v in sorted(tablas_por_nombre.items(), key=lambda x: x[0].casefold())
    ]
    inv["llamadas"] = llamadas
    inv["condiciones"] = sorted(condiciones, key=lambda x: x["linea"] or 0)
    inv["operaciones"] = sorted(operaciones, key=lambda x: x["linea"] or 0)
    return inv, relaciones, advertencias


def obtener_archivos(origen, destino):
    archivos = []
    for f in origen.rglob("*.ast.json"):
        if f.is_file() and destino not in f.parents:
            archivos.append(f)
    return sorted(archivos, key=lambda p: p.as_posix().casefold())


def grabar_json(ruta, obj):
    ruta.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Generar inventario.json y relaciones.json desde AST de SQLFluff")
    ap.add_argument("--origen", required=True, type=Path, help="Carpeta que contiene los *.ast.json")
    ap.add_argument("--destino", required=True, type=Path, help="Carpeta para inventario.json y relaciones.json")
    ap.add_argument("--max-archivos", type=int, default=0, help="0 = todos los AST encontrados")
    args = ap.parse_args()
    origen, destino = args.origen.expanduser().resolve(), args.destino.expanduser().resolve()
    if not origen.is_dir():
        ap.error("No existe la carpeta de AST: {}".format(origen))
    if args.max_archivos < 0:
        ap.error("--max-archivos debe ser >= 0")
    archivos = obtener_archivos(origen, destino)
    if args.max_archivos:
        archivos = archivos[:args.max_archivos]
    if not archivos:
        print("No se encontraron archivos *.ast.json en {}".format(origen), file=sys.stderr)
        return 2
    destino.mkdir(parents=True, exist_ok=True)

    procedimientos = []
    relaciones = []
    incidencias = []
    archivos_usados = []
    for i, f in enumerate(archivos, 1):
        relativo = f.relative_to(origen).as_posix()
        try:
            doc = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            incidencias.append({"archivo_ast": relativo, "problema": "JSON ilegible: {}".format(exc)})
            print("[{}/{}] {} -> ERROR de lectura".format(i, len(archivos), relativo))
            continue
        estado = doc.get("estado", "desconocido")
        arbol = doc.get("arbol")
        if not isinstance(arbol, dict):
            incidencias.append({"archivo_ast": relativo, "problema": "No hay arbol valido", "estado": estado})
            print("[{}/{}] {} -> SIN ARBOL".format(i, len(archivos), relativo))
            continue
        if doc.get("parser", "").lower().find("sqlfluff") < 0:
            incidencias.append({"archivo_ast": relativo,
                                "problema": "Formato distinto de SQLFluff. Intento de lectura no garantizado."})
        if estado != "ok" or doc.get("errores") or doc.get("zonas_no_analizadas"):
            incidencias.append({"archivo_ast": relativo, "problema": "Analisis SQL parcial o con errores",
                                "estado": estado, "errores": doc.get("errores", []),
                                "zonas_no_analizadas": doc.get("zonas_no_analizadas", [])})
        definiciones = descendientes_tipo(arbol, "create_procedure_statement")
        n = 0
        for proc in definiciones:
            invent, rels, avisos = analizar_procedimiento(proc, doc.get("archivo_origen", ""), relativo, estado)
            if invent:
                procedimientos.append(invent)
                relaciones.extend(rels)
                n += 1
            for av in avisos:
                incidencias.append({"archivo_ast": relativo, "problema": av})
        if not definiciones:
            incidencias.append({"archivo_ast": relativo, "problema": "Sin CREATE PROCEDURE; otros objetos aun no soportados"})
        archivos_usados.append(relativo)
        print("[{}/{}] {} -> {} procedimiento(s)".format(i, len(archivos), relativo, n))

    fecha = datetime.now(timezone.utc).isoformat()
    inventario = {
        "version_esquema": "1.0",
        "fecha_utc": fecha,
        "origen_ast": str(origen),
        "nota": "Extraccion sintactica; las condiciones NO son reglas de negocio confirmadas.",
        "resumen": {"archivos_ast_leidos": len(archivos_usados), "procedimientos": len(procedimientos),
                    "tablas_referenciadas": len({(r["destino"]["nombre"].casefold()) for r in relaciones
                                                 if r["destino"]["tipo"] == "tabla"}),
                    "incidencias": len(incidencias)},
        "procedimientos": sorted(procedimientos, key=lambda x: (x["nombre"].casefold(), x["archivo_sql"])),
        "incidencias": incidencias,
    }
    # Relacion individual con evidencia. Las ausencias no demuestran inexistencia.
    relaciones.sort(key=lambda r: (r["archivo_sql"], r["linea"] or 0,
                                   r["tipo"], r["destino"]["nombre"].casefold()))
    graph = {
        "version_esquema": "1.0",
        "fecha_utc": fecha,
        "nota": "Se conserva una relacion por ubicacion/operacion. Es un grafo parcial del codigo analizado.",
        "resumen_por_tipo": dict(sorted(Counter(r["tipo"] for r in relaciones).items())),
        "relaciones": relaciones,
    }
    grabar_json(destino / "inventario.json", inventario)
    grabar_json(destino / "relaciones.json", graph)
    print("\nGenerados:")
    print("  {}".format(destino / "inventario.json"))
    print("  {}".format(destino / "relaciones.json"))
    print("Resumen: {} SP, {} relaciones, {} incidencias".format(len(procedimientos), len(relaciones), len(incidencias)))
    return 0 if not incidencias else 1


if __name__ == "__main__":
    sys.exit(main())
