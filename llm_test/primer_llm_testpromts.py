"""Ejecuta casos de prompts SQL contra Qwen y guarda sus respuestas en JSON.

Por defecto ejecuta SOLO el primer caso para evitar una prueba muy larga en CPU.

Ejemplos (con el entorno cmu-llm activo):
    python primer_llm_testpromts.py --listar
    python primer_llm_testpromts.py
    python primer_llm_testpromts.py --id C02
    python primer_llm_testpromts.py --id C03
    python primer_llm_testpromts.py --todos

IMPORTANTE: 'rag_simulado' significa que el contexto se lee de un JSON local;
este programa NO realiza embeddings ni búsqueda en una base vectorial.

EJECURTAR EN TERMINAL:

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python llm_test\primer_llm_testpromts.py --id C03

"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import time

BASE = Path(__file__).resolve().parent
ARCHIVO_CASOS = BASE / "prompts_reglas_negocio.json"
ARCHIVO_SALIDA = BASE / "resultados_llm.json"

INSTRUCCION_SISTEMA = (
    "Eres analista de software legado especializado en T-SQL. "
    "Propones posibles reglas de negocio usando EXCLUSIVAMENTE la evidencia aportada. "
    "No inventes nombres de catálogos, permisos, políticas o validaciones. "
    "Una regla candidata no equivale a una regla confirmada por un experto humano."
)


def numerar_lineas(texto):
    """Numera las líneas para que el modelo pueda citar fuentes verificables."""
    return "\n".join(
        f"L{numero}: {linea}"
        for numero, linea in enumerate(texto.splitlines(), start=1)
    )


def construir_prompt(caso):
    bloques = [
        f"CASO: {caso['id']} - {caso['titulo']}",
        f"PREGUNTA: {caso['pregunta']}",
        "FUENTES DEL SISTEMA (SQL u otros artefactos):",
    ]
    for fuente in caso["fuentes"]:
        bloques.append(
            f"Archivo: {fuente['archivo']}\n{numerar_lineas(fuente['contenido'])}"
        )

    contexto = caso.get("contexto_rag", [])
    if contexto:
        bloques.append(
            "CONTEXTO ADICIONAL RECUPERADO (RAG SIMULADO; "
            "fragmentos suministrados manualmente, NO búsqueda real):"
        )
        for fuente in contexto:
            bloques.append(
                f"Archivo: {fuente['archivo']}\n{numerar_lineas(fuente['contenido'])}"
            )
    else:
        bloques.append(
            "CONTEXTO ADICIONAL: ninguno. Si falta un catálogo, "
            "NO interpretes los códigos numéricos de negocio."
        )

    bloques.append(
        """INSTRUCCIONES:
- Distingue una regla de negocio candidata de lógica técnica y casos ambiguos.
- Si desconoces el significado de un catálogo, NO adivines; deja un pendiente.
- Describe las condiciones y el efecto concretos del código, sin generalizar a todo el sistema.
- En 'evidencias', cita el nombre del archivo y las líneas Lx-Ly aportadas.
- Usa el contexto recuperado solo si fue proporcionado.
- Responde ÚNICAMENTE un objeto JSON válido, sin bloques Markdown, con esta forma:
{
  "clasificacion": "regla_candidata | logica_tecnica | ambiguo",
  "regla": "texto en lenguaje natural, o null si no se puede proponer",
  "condiciones": ["condición observable"],
  "efecto": "efecto observable, o null",
  "evidencias": [{"archivo": "ruta", "lineas": "L1-L2"}],
  "pendientes": ["información no encontrada"],
  "uso_contexto_rag": false
}
Recuerda: 'uso_contexto_rag' solo es true si realmente utilizaste un fragmento adicional."
"""
    )
    return "\n\n".join(bloques)


def extraer_json(respuesta):
    """Intenta leer el primer objeto JSON de la respuesta; conserva siempre el texto original."""
    texto = re.sub(r"<think>.*?</think>", "", respuesta, flags=re.DOTALL).strip()
    texto = re.sub(r"^```(?:json)?\s*", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\s*```$", "", texto)

    try:
        return json.loads(texto), None
    except (ValueError, TypeError):
        pass

    decodificador = json.JSONDecoder()
    for posicion, caracter in enumerate(texto):
        if caracter == "{":
            try:
                objeto, _ = decodificador.raw_decode(texto[posicion:])
                return objeto, None
            except json.JSONDecodeError:
                continue
    return None, "La respuesta no contiene un JSON válido; revisa respuesta_texto."


def guardar_salida(ruta, contenido):
    """Escritura atómica: evita perder resultados anteriores por un cierre inesperado."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + ".tmp")
    temporal.write_text(json.dumps(contenido, indent=2, ensure_ascii=False), encoding="utf-8")
    temporal.replace(ruta)


def main():
    analizador = argparse.ArgumentParser(description="Pruebas de reglas de negocio con Qwen")
    analizador.add_argument("--listar", action="store_true", help="Lista los casos sin cargar el LLM")
    seleccion = analizador.add_mutually_exclusive_group()
    seleccion.add_argument("--todos", action="store_true", help="Ejecuta todos los casos")
    seleccion.add_argument("--id", help="Ejecuta un caso por ID, p. ej. C02")
    analizador.add_argument("--max-tokens", type=int, default=320)
    analizador.add_argument("--salida", type=Path, default=ARCHIVO_SALIDA)
    args = analizador.parse_args()

    datos = json.loads(ARCHIVO_CASOS.read_text(encoding="utf-8"))
    casos = datos["casos"]
    if args.listar:
        print("Casos disponibles (no se carga el modelo):")
        for caso in casos:
            print(f"  {caso['id']}  [{caso['fase']}]  {caso['titulo']}")
        return

    if args.id:
        elegidos = [c for c in casos if c["id"].upper() == args.id.upper()]
        if not elegidos:
            raise SystemExit(f"No existe el caso {args.id}. Usa --listar.")
    elif args.todos:
        elegidos = casos
    else:
        elegidos = casos[:1]  # Paso a paso: primero una sola prueba

    # Importar el módulo solo cuando realmente vamos a ejecutar el modelo.
    from primer_llm_cargamodelo import MODEL_ID, cargar_modelo, generar_respuesta

    tokenizador, modelo, dispositivo = cargar_modelo()
    salida = {
        "fecha": datetime.now().astimezone().isoformat(timespec="seconds"),
        "modelo": MODEL_ID,
        "dispositivo": dispositivo,
        "advertencia": "Las respuestas son salidas reales del LLM, no reglas validadas. RAG simulado: contexto precargado, sin recuperación vectorial.",
        "casos_ejecutados": [c["id"] for c in elegidos],
        "resultados": [],
    }

    ruta_salida = args.salida if args.salida.is_absolute() else BASE / args.salida
    for numero, caso in enumerate(elegidos, start=1):
        print(f"\n[{numero}/{len(elegidos)}] {caso['id']} - {caso['titulo']}", flush=True)
        prompt = construir_prompt(caso)
        inicio = time.perf_counter()
        resultado = {
            "id": caso["id"],
            "titulo": caso["titulo"],
            "fase": caso["fase"],
            "contexto_rag_proporcionado": bool(caso.get("contexto_rag")),
            "prompt_enviado": prompt,
            "resultado_esperado_para_comparar": caso["resultado_esperado"],
        }
        try:
            respuesta = generar_respuesta(
                prompt,
                tokenizador,
                modelo,
                dispositivo,
                instruccion_sistema=INSTRUCCION_SISTEMA,
                max_tokens=args.max_tokens,
            )
            objeto, error_json = extraer_json(respuesta)
            resultado.update(
                respuesta_texto=respuesta,
                respuesta_json=objeto,
                error_json=error_json,
                estado="generado" if objeto is not None else "respuesta_no_json",
            )
            print("Respuesta:", respuesta, sep="\n", flush=True)
            if error_json:
                print("AVISO:", error_json, flush=True)
        except Exception as exc:
            resultado.update(
                respuesta_texto=None,
                respuesta_json=None,
                error_json=None,
                estado="error_ejecucion",
                error=str(exc),
            )
            print(f"ERROR en {caso['id']}: {exc}", flush=True)

        resultado["segundos"] = round(time.perf_counter() - inicio, 2)
        salida["resultados"].append(resultado)
        guardar_salida(ruta_salida, salida)
        print(f"Guardado: {ruta_salida}", flush=True)

    print("\nPrueba terminada. Resultados guardados en:", ruta_salida, flush=True)


if __name__ == "__main__":
    main()
