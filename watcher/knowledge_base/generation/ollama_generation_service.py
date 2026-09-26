import os

import requests


class GenerationServiceError(Exception):
    """Raised when Ollama cannot generate a response."""


class OllamaGenerationService:
    DEFAULT_BASE_URL = "http://127.0.0.1:11434"
    DEFAULT_MODEL = "llama3.1:8b"

    def __init__(
        self,
        *,
        base_url=None,
        model_name=None,
        session=None,
    ):
        self.base_url = (
            base_url
            or os.getenv("OLLAMA_BASE_URL")
            or self.DEFAULT_BASE_URL
        ).rstrip("/")

        self.model_name = (
            model_name
            or os.getenv("OLLAMA_GENERATION_MODEL")
            or self.DEFAULT_MODEL
        )

        self.session = session or requests.Session()

    def generate(
        self,
        prompt: str,
        *,
        temperature=0.0,
        max_tokens=512,
        seed=None,
        top_p=None,
        num_ctx=None,
        json_mode=False,
    ) -> str:
        """
        seed / top_p / num_ctx / json_mode were added for the Interpreter
        (guide 5.3: determinism). They default to "not sent", so every
        existing caller produces exactly the same request as before.
        """

        prompt = str(prompt or "").strip()

        if not prompt:
            raise GenerationServiceError(
                "Generation prompt cannot be empty."
            )

        options = {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": 16384 if num_ctx is None else num_ctx,
        }
        if seed is not None:
            options["seed"] = seed
        if top_p is not None:
            options["top_p"] = top_p

        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,

            # Qwen3 should answer directly rather than
            # spending time on an internal thinking pass.
            "think": False,

            "options": options,
        }
        if json_mode:
            payload["format"] = "json"

        try:
            response = self.session.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=(5, 300),
            )

            response.raise_for_status()

        except requests.RequestException as exc:
            raise GenerationServiceError(
                f"Ollama generation request failed: {exc}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise GenerationServiceError(
                "Ollama returned invalid JSON."
            ) from exc

        answer = payload.get("response")

        if not isinstance(answer, str):
            raise GenerationServiceError(
                "Ollama response contains no generated text."
            )

        answer = answer.strip()

        if not answer:
            raise GenerationServiceError(
                "Ollama returned an empty response."
            )

        return answer

    def model_digest(self) -> str:
        """
        Guide 5.3: pin the model by digest, not tag. Returns the digest of
        self.model_name from Ollama's /api/tags, or "" if unavailable.
        Never raises; a missing digest must not stop classification.
        """
        try:
            response = self.session.get(
                f"{self.base_url}/api/tags",
                timeout=(5, 30),
            )
            response.raise_for_status()
            models = response.json().get("models") or []
        except Exception:
            return ""

        wanted = self.model_name
        if ":" not in wanted:
            wanted = f"{wanted}:latest"

        for model in models:
            if model.get("name") == wanted or model.get("model") == wanted:
                return str(model.get("digest") or "")

        return ""
