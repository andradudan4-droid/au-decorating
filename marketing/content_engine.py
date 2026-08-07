import os
from pathlib import Path

from anthropic import Anthropic

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
PROMPTS_DIR = Path(__file__).parent / "prompts"
DEFAULT_MODEL = os.environ.get("CONTENT_ENGINE_MODEL", "claude-haiku-4-5-20251001")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _load_knowledge():
    parts = [
        (KNOWLEDGE_DIR / name).read_text()
        for name in ("brand.md", "services.md", "service_area.md")
    ]
    return "\n\n".join(parts)


def _load_prompt(content_type):
    path = PROMPTS_DIR / f"{content_type}.md"
    if not path.exists():
        raise ValueError(f"Unknown content type: {content_type}")
    return path.read_text()


def generate(content_type, context):
    """Generate marketing copy of `content_type`, grounded in the AU Decorating
    knowledge base, interpolating `context` into that type's prompt template."""
    knowledge = _load_knowledge()
    template = _load_prompt(content_type)
    instructions = template.format(**context)
    system = f"You are the marketing copywriter for AU Decorating Ltd.\n\n{knowledge}"

    response = _get_client().messages.create(
        model=DEFAULT_MODEL,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": instructions}],
    )
    return response.content[0].text.strip()
