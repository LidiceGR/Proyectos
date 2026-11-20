"""Load Qwen locally and produce text without an external inference API.

Only import large machine-learning libraries when the model is actually used.
"""

from __future__ import annotations

import os

DEFAULT_MODEL_ID = "Qwen/Qwen3-0.6B"


class LocalQwen:
    """A minimal, reusable local Qwen text-generation adapter.

    The model is initialized on the first call, so preview and tests do not
    download weights or allocate model memory.
    """

    def __init__(self, model_id: str = DEFAULT_MODEL_ID, max_new_tokens: int = 384):
        if max_new_tokens < 1:
            raise ValueError("max_new_tokens must be greater than zero")
        self.model_id = model_id
        self.max_new_tokens = max_new_tokens
        self._tokenizer = None
        self._model = None
        self._device = None
        self._torch = None

    def _load(self) -> None:
        if self._model is not None:
            return

        # Corporate Windows networks may use a trusted enterprise TLS root.
        # Keep certificate validation enabled and use the OS certificate store.
        if os.name == "nt":
            try:
                import truststore
            except ImportError as exc:
                raise RuntimeError(
                    "The Windows environment requires truststore. "
                    "Install it with: python -m pip install truststore"
                ) from exc
            truststore.inject_into_ssl()

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        print(f"[model_loading] model={self.model_id} device={device}", flush=True)
        tokenizer = AutoTokenizer.from_pretrained(self.model_id)
        model = AutoModelForCausalLM.from_pretrained(
            self.model_id, dtype=dtype
        ).to(device)
        model.eval()
        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model
        self._device = device
        print("[model_ready]", flush=True)

    @property
    def device(self) -> str | None:
        """Return the current execution device, or None before loading."""
        return self._device

    def generate(self, prompt: str, system_prompt: str) -> str:
        """Return the model-generated text, excluding the input prompt."""
        self._load()
        assert self._tokenizer is not None and self._model is not None
        assert self._device is not None and self._torch is not None
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        rendered = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        inputs = self._tokenizer(rendered, return_tensors="pt").to(self._device)
        with self._torch.inference_mode():
            output = self._model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        generated = output[0][inputs["input_ids"].shape[1]:]
        return self._tokenizer.decode(generated, skip_special_tokens=True).strip()
