"""Carga Qwen3-0.6B y permite reutilizarlo en distintas pruebas del Capstone.

Ejecutable directamente con un SQL sencillo, o importable desde
primer_llm_testpromts.py para procesar varios casos con una sola carga.
"""

import os

# En el equipo Windows del proyecto, truststore resolvió un problema de
# certificados SSL. En Google Colab (Linux) no hace falta instalarlo.
if os.name == "nt":
    try:
        import truststore
    except ImportError as exc:
        raise RuntimeError(
            "Falta truststore. Instálalo en cmu-llm: python -m pip install truststore"
        ) from exc
    truststore.inject_into_ssl()

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_ID = "Qwen/Qwen3-0.6B"


def cargar_modelo(model_id=MODEL_ID):
    """Carga una sola vez tokenizador y modelo; retorna (tokenizador, modelo, dispositivo)."""
    dispositivo = "cuda" if torch.cuda.is_available() else "cpu"
    tipo_dato = torch.float16 if dispositivo == "cuda" else torch.float32
    print(f"Dispositivo: {dispositivo}", flush=True)
    print(f"Cargando modelo: {model_id}", flush=True)

    tokenizador = AutoTokenizer.from_pretrained(model_id)
    modelo = AutoModelForCausalLM.from_pretrained(
        model_id,
        dtype=tipo_dato,  # En Transformers 4.57 reemplaza a torch_dtype
    ).to(dispositivo)
    modelo.eval()
    print("Modelo listo.", flush=True)
    return tokenizador, modelo, dispositivo


def generar_respuesta(
    pregunta,
    tokenizador,
    modelo,
    dispositivo,
    instruccion_sistema=None,
    max_tokens=300,
):
    """Envía un prompt al modelo y retorna ÚNICAMENTE el texto generado."""
    mensajes = []
    if instruccion_sistema:
        mensajes.append({"role": "system", "content": instruccion_sistema})
    mensajes.append({"role": "user", "content": pregunta})

    texto_prompt = tokenizador.apply_chat_template(
        mensajes,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    entradas = tokenizador(texto_prompt, return_tensors="pt").to(dispositivo)

    with torch.inference_mode():
        salida = modelo.generate(
            **entradas,
            max_new_tokens=max_tokens,
            do_sample=False,
            pad_token_id=tokenizador.eos_token_id,
        )

    tokens_generados = salida[0][entradas["input_ids"].shape[1]:]
    return tokenizador.decode(tokens_generados, skip_special_tokens=True).strip()


def main():
    prompt = """Analiza este SQL y explica en español las condiciones y el efecto.
No inventes condiciones adicionales.

UPDATE Operaciones
SET Estado = 'APROBADA'
WHERE Estado = 'PENDIENTE'
  AND Monto > 0;
"""
    tokenizador, modelo, dispositivo = cargar_modelo()
    print("Generando respuesta...", flush=True)
    respuesta = generar_respuesta(
        prompt, tokenizador, modelo, dispositivo, max_tokens=150
    )
    print("\nRESPUESTA DEL LLM:\n" + respuesta)


if __name__ == "__main__":
    main()
