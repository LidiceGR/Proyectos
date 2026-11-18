# Capstone CMU — Preparación del entorno para un LLM local (Windows)

**Registro de instalación y primera ejecución exitosa — 17 de noviembre de 2026**

Este archivo conserva los **comandos y cambios realmente utilizados con éxito** para preparar el entorno `cmu-llm`, resolver la conexión HTTPS y ejecutar `Qwen3-0.6B` localmente. Sirve como guía para repetir el proceso en otra computadora Windows con Anaconda/Conda. Las salidas de terminal se incluyen como referencia, no como garantía de versiones futuras.

Instalación de 
Componente	Versión
Python	3.11.18
PyTorch	2.15.0+cpu
Transformers	4.57.6
Accelerate	1.16.0

## 1. Requisitos de partida

- Windows y PowerShell.
- Anaconda/Conda instalado.
- Acceso a Internet para instalar paquetes.
- Ejecución **en CPU** (no se detectó GPU NVIDIA accesible en el entorno original).

**En la computadora original:**

- Conda: `D:\devenv\Anaconda32024.02\Scripts\conda.exe`
- Proyecto: `D:\Proyectos\CMUAgent\proyectos`
- Nombre del entorno: `cmu-llm`

> **En otra computadora:** sustituye la ruta a `conda.exe` por la correspondiente a esa instalación. La ruta y el nombre de usuario del entorno resultante pueden cambiar. No es necesario tener previamente Python 3.11 fuera de Conda: el primer comando lo instala dentro del nuevo entorno.

## 2. Crear el entorno aislado con Python 3.11

Abrir **PowerShell** y ejecutar:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" create -n cmu-llm python=3.11 -y
```

Verificar la versión:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run -n cmu-llm python --version
```

**Resultado observado:** `Python 3.11.18`.

## 3. Aislar el entorno de los paquetes del usuario

Este paso fue **necesario**: inicialmente, `pip` encontraba paquetes en el directorio personal de Python, fuera de `cmu-llm`.

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" env config vars set -n cmu-llm PYTHONNOUSERSITE=1
```

El comando puede terminar **sin mostrar texto**; eso no implica un error.

Comprobar que la variable está guardada:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" env config vars list -n cmu-llm
```

**Resultado observado:** `PYTHONNOUSERSITE = 1`.

Comprobar el aislamiento:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run -n cmu-llm python -c "import site; print('User site habilitado:', site.ENABLE_USER_SITE)"
```

**Resultado observado:** `User site habilitado: False`.

## 4. Instalar PyTorch para CPU

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
```

`--no-capture-output` permite ver el progreso y los mensajes de instalación mientras se ejecuta.

Verificar:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -c "import torch; print('PyTorch:', torch.__version__); print('CUDA:', torch.cuda.is_available())"
```

**Resultados observados:**

```text
PyTorch: 2.15.0+cpu
CUDA: False
```

## 5. Instalar Transformers y Accelerate

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m pip install "transformers>=4.51.0,<5" accelerate
```

La instalación terminó con `Successfully installed`.

## 6. Verificación final de las tres bibliotecas

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -c "import torch, transformers, accelerate; print('PyTorch:', torch.__version__); print('Transformers:', transformers.__version__); print('Accelerate:', accelerate.__version__)"
```

**Resultado real obtenido:**

```text
PyTorch: 2.15.0+cpu
Transformers: 4.57.6
Accelerate: 1.16.0
```

✅ **Estado conseguido:** entorno Python aislado, con PyTorch CPU, Transformers y Accelerate importándose correctamente.


## 7. Primer programa: `primer_llm.py`

Guardar el siguiente código como `primer_llm.py` dentro de la carpeta del proyecto. Este es el programa probado, **con las dos líneas de `truststore` que fueron necesarias para descargar el modelo desde la red usada**.

> Primero se intentó ejecutarlo sin `truststore`, pero falló al verificar el certificado HTTPS. El código reproducido aquí corresponde a la versión que sí llegó a generar una respuesta.

```python
import truststore
truststore.inject_into_ssl()

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

# Modelo descargado desde Hugging Face
MODEL_ID = "Qwen/Qwen3-0.6B"

# Usar GPU CUDA si está disponible; en el equipo probado se utilizó CPU
device = "cuda" if torch.cuda.is_available() else "cpu"
dtype = torch.float16 if device == "cuda" else torch.float32

print("Dispositivo:", device)
print("Cargando modelo...")

tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)

model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID,
    torch_dtype=dtype
).to(device)

model.eval()

prompt = """
Analiza el siguiente SQL:

UPDATE Operaciones
SET Estado = 'APROBADA'
WHERE Estado = 'PENDIENTE'
AND Monto > 0;

Explica brevemente que condiciones deben cumplirse
y que efecto tiene la operacion.
No inventes condiciones adicionales.
"""

messages = [{"role": "user", "content": prompt}]

texto = tokenizer.apply_chat_template(
    messages,
    tokenize=False,
    add_generation_prompt=True,
    enable_thinking=False
)

inputs = tokenizer(texto, return_tensors="pt").to(device)

print("Generando respuesta...")

with torch.inference_mode():
    outputs = model.generate(
        **inputs,
        max_new_tokens=120,
        do_sample=False,
        pad_token_id=tokenizer.eos_token_id
    )

respuesta = tokenizer.decode(
    outputs[0][inputs["input_ids"].shape[1]:],
    skip_special_tokens=True
)

print("\nRESPUESTA DEL LLM:")
print(respuesta)
```

**Nota:** el código probado usa `torch_dtype=dtype` y mostró la advertencia de que está obsoleto; cambiarlo a `dtype=dtype` es una mejora pendiente, **no un cambio validado en esta sesión**.

## 8. Primera ejecución: error de certificado SSL

Se ejecutó desde PowerShell, situado en `D:\Proyectos\CMUAgent\proyectos`:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python primer_llm.py
```

La primera ejecución (sin `truststore` en el script) llegó a imprimir `Dispositivo: cpu` y `Cargando modelo...`, pero terminó con:

```text
SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED]
certificate verify failed: self-signed certificate in certificate chain
```

La conexión podía estar pasando por infraestructura corporativa con certificados internos; el error **no demostraba un fallo de PyTorch ni del LLM**.

### 8.1 Confirmar acceso HTTPS desde Windows

Comando ejecutado correctamente:

```powershell
try {
    (Invoke-WebRequest -Uri "https://huggingface.co/Qwen/Qwen3-0.6B/resolve/main/tokenizer_config.json" -Method Head -UseBasicParsing).StatusCode
} catch {
    $_.Exception.Message
}
```

**Resultado observado:** `200`. Windows sí podía validar la conexión HTTPS.

### 8.2 Instalar `truststore` dentro del entorno

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m pip install truststore
```

**Resultado observado:** `Successfully installed truststore-0.10.5`.

### 8.3 Modificar el programa para usar los certificados de Windows

Se añadieron **al inicio** de `primer_llm.py`, antes de importar `torch` o `transformers`:

```python
import truststore
truststore.inject_into_ssl()
```

Esto permite a Python utilizar el almacén de certificados de confianza del sistema. **No se desactivó la verificación SSL**.

## 9. Ejecutar Qwen3-0.6B correctamente

Con el programa ya corregido, se volvió a ejecutar el mismo comando:

```powershell
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python primer_llm.py
```

Se descargaron los archivos del tokenizador, configuración y el archivo `model.safetensors` (aproximadamente **1.50 GB**). Después apareció `Generando respuesta...` y se obtuvo esta respuesta real:

```text
Dispositivo: cpu
Cargando modelo...
Generando respuesta...

RESPUESTA DEL LLM:
La condición **Estado = 'PENDIENTE'** debe cumplirse para que la operación se ejecute. La condición **Monto > 0** también debe cumplirse para que el valor de la columna **Monto** sea positivo, lo cual es necesario para que el estado final sea 'APROBADA'. La operación **UPDATE** actualiza la columna **Estado** de las filas donde ambos condiciones se cumplen.
```

**Resultado validado:** el LLM ejecutó inferencia local en CPU y explicó correctamente las dos condiciones y el cambio de estado del SQL. La respuesta fue útil como primera prueba, aunque no indicó que si ninguna fila cumple el `WHERE`, no se actualiza ningún registro.

## 10. Advertencias observadas (no impidieron ejecutar)

- **Caché de Hugging Face y `symlinks`:** Windows no permitía enlaces simbólicos en esa carpeta. La caché funcionó igualmente, aunque puede ocupar más espacio. No fue necesario activar Developer Mode.
- **`hf_xet` no instalado:** la descarga utilizó HTTP convencional; esto no impidió descargar el modelo. No se instaló `hf_xet`.
- **`torch_dtype` obsoleto:** el programa funcionó con el argumento utilizado; se podrá actualizar a `dtype` posteriormente.
- **`temperature`, `top_p`, `top_k` pueden ignorarse:** apareció una advertencia de configuración de generación, pero el modelo produjo la respuesta esperada.

## 11. Ubicación de archivos y caché en el equipo original

| Contenido | Ubicación observada |
|---|---|
| Código fuente | `D:\Proyectos\CMUAgent\proyectos\primer_llm.py` |
| Entorno Conda y bibliotecas | `C:\Users\F13990\.conda\envs\cmu-llm` |
| Caché del modelo Qwen | `C:\Users\F13990\.cache\huggingface\hub\models--Qwen--Qwen3-0.6B` |

En el equipo original se decidió conservar la caché en **C:** por disponer de espacio suficiente y mantener el código en **D:**. Las rutas con nombre de usuario cambian según la computadora.

## 12. Estado alcanzado y reproducción en otra computadora

- **Probado:** creación del entorno, aislamiento de paquetes del usuario, instalación e importación de PyTorch/Transformers/Accelerate, instalación de `truststore`, descarga del modelo y generación de la primera respuesta SQL.
- **Versiones observadas:** Python `3.11.18`, PyTorch `2.15.0+cpu`, Transformers `4.57.6`, Accelerate `1.16.0`, truststore `0.10.5`.
- **Importante:** se usaron rangos de versiones para instalar parte de las dependencias. Repetir los comandos más adelante **no garantiza las mismas versiones exactas**. Este README registra la ejecución que funcionó; no es todavía un archivo de bloqueo de dependencias (`lockfile`).
- **CPU:** la receta corresponde a un equipo sin GPU NVIDIA accesible. Una máquina con GPU CUDA requiere comprobar compatibilidad y quizá instalar otra variante de PyTorch.
- **Red corporativa:** la solución con `truststore` funcionó en este entorno específico. En otra red puede no ser necesaria, o la organización puede requerir certificados o permisos propios.
- **Google Colab:** pendiente de probar con este modelo y este programa. **No** se considera validado todavía.
- **Siguiente experimento:** cambiar el prompt para solicitar salida JSON; **aún no se había ejecutado esa prueba** al actualizar este README.

**Nota de uso:** se empleó `conda run --no-capture-output -n cmu-llm` para mostrar el progreso sin activar el entorno en PowerShell. Todos los comandos de esta guía están escritos para ese flujo que sí funcionó.
