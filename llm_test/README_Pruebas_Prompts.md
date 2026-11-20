# Capstone CMU — Primera batería de prompts con Qwen local

## Archivos

- `primer_llm.py`: conserva una prueba SQL individual y ofrece funciones reutilizables para cargar el modelo **una sola vez** y generar respuestas.
- `primer_llm_testpromts.py`: lee varios casos, envía prompts a Qwen y guarda las respuestas reales.
- `prompts_reglas_negocio.json`: cinco casos SQL ficticios con una pregunta, fuentes, contexto opcional y resultado esperado **solo para comparación**.
- `resultados_llm.json`: **se crea automáticamente al ejecutar** `primer_llm_testpromts.py`. Las salidas del modelo no se pueden conocer antes de ejecutarlo.

## Qué significa "RAG simulado"

Este ejercicio todavía **no implementa un índice vectorial ni embeddings**. En los casos C03 y C04, el programa agrega manualmente fragmentos de un catálogo al prompt para simular qué pasaría **después de una recuperación RAG**. Más adelante sustituiremos esta selección manual por un recuperador real.

Casos:

| ID | Tipo | Qué comprobar |
|---|---|---|
| C01 | Sin RAG | Redacción natural de una regla desde un `UPDATE` |
| C02 | Sin RAG | Código de catálogo desconocido (`TipoOperacion=2`) |
| C03 | RAG simulado | Mismo SQL, pero con el catálogo que define `2 = Ajuste interno` |
| C04 | RAG simulado | Procedimiento + trigger + catálogo; verificar la excepción por monto |
| C05 | Sin RAG | Clasificar el registro de una traza como lógica técnica |

## Entorno probado

Windows, Python 3.11 (Conda `cmu-llm`), PyTorch CPU, `transformers` 4.57.6, `accelerate`, `truststore`. El script mantiene la solución de certificados para Windows. Reutiliza la caché ya descargada de `Qwen/Qwen3-0.6B`.

En Google Colab, `truststore` no se requiere; el código utiliza GPU si PyTorch detecta CUDA. Deberás instalar allí las bibliotecas por separado.

## Un paso a la vez

**Paso 1.** Copia los tres archivos (`primer_llm.py`, `primer_llm_testpromts.py`, `prompts_reglas_negocio.json`) a la misma carpeta de tu proyecto. Antes de cargar el modelo, comprueba que el programa reconoce los ejemplos:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python primer_llm_testpromts.py --listar
```

**Paso 2.** Si la lista es correcta, ejecuta el primer caso (solo C01):

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python primer_llm_testpromts.py
```

La respuesta aparecerá en terminal y en `resultados_llm.json`. Por CPU, generar respuestas de 320 tokens puede tardar.

**Paso 3.** Compara C02 y C03 ejecutándolos de manera independiente:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python primer_llm_testpromts.py --id C02 --salida resultado_C02.json
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python primer_llm_testpromts.py --id C03 --salida resultado_C03.json
```

**Paso 4.** Para ejecutar todos los casos en una sola carga del modelo:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python primer_llm_testpromts.py --todos
```

> Nota: cada ejecución sobrescribe **su propio archivo de salida**. Usa `--salida` con nombres distintos si quieres conservar resultados de pruebas anteriores. El programa guarda el resultado después de **cada** caso para evitar perder todo si falla uno posterior.

## Cómo revisar `resultados_llm.json`

Cada caso registra:

- `prompt_enviado`: texto completo con fragmentos y líneas numeradas.
- `respuesta_texto`: respuesta original del LLM, incluso si no es JSON válido.
- `respuesta_json`: objeto extraído, o `null` si el modelo no produjo JSON válido.
- `resultado_esperado_para_comparar`: referencia escrita por nosotros; **no se envía al modelo**.
- `error_json`, `estado`, `segundos`: información diagnóstica.

**Importante:** Qwen3-0.6B es un modelo pequeño. Podría producir JSON incorrecto, respuestas incompletas o atribuciones sin fundamento. Eso forma parte de lo que queremos medir; no corregimos silenciosamente los resultados.

Todos los ejemplos son ficticios. No enviar SQL interno de trabajo a servicios externos ni subirlo a repositorios públicos sin autorización.
