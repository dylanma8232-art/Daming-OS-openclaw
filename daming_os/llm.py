import json
from typing import Optional, Dict, Any, List

class LLMProvider:
    def __init__(self, model_name: str = "gpt-4o"):
        self.model_name = model_name

    def complete(self, messages: List[Dict[str, str]], temperature: float = 0.3) -> str:
        try:
            import litellm
        except ImportError as exc:
            raise RuntimeError("LLM features require: pip install 'daming-os[llm]'") from exc
        response = litellm.completion(
            model=self.model_name,
            messages=messages,
            temperature=temperature
        )
        return response.choices[0].message.content.strip()

    def generate_json(self, messages: List[Dict[str, str]], temperature: float = 0.3) -> Dict[str, Any]:
        content = self.complete(messages, temperature)
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            parts = content.split("```")
            if len(parts) >= 3:
                content = parts[1].strip()
                if content.startswith(("json", "python", "yaml")):
                    content = "\n".join(content.split("\n")[1:]).strip()
        return json.loads(content)
