"""
Prueba de una consulta basica de uso de LLM con un promt de pruebas  basico 

EJECUTAR EN TERMINAL:

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python llm_test\primer_llm_test.py

"""

import truststore
truststore.inject_into_ssl()
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

# Modelo que descargaremos de Hugging Face
MODEL_ID = "Qwen/Qwen3-0.6B"

# Utilizar GPU cuando este disponible
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

# Primera prueba orientada a nuestro Capstone
prompt =  """Analiza el siguiente SQL:

UPDATE Operaciones
SET Estado = 'APROBADA'
WHERE Estado = 'PENDIENTE'
AND Monto > 0;

Devuelve exclusivamente un JSON valido con:
- tabla: nombre de la tabla
- condiciones: lista de condiciones del WHERE
- efecto: descripcion breve del cambio

No agregues explicaciones fuera del JSON."""

"""
Analiza el siguiente SQL:

UPDATE Operaciones
SET Estado = 'APROBADA'
WHERE Estado = 'PENDIENTE'
AND Monto > 0;

Explica brevemente que condiciones deben cumplirse
y que efecto tiene la operacion.
No inventes condiciones adicionales.
 */"""

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
