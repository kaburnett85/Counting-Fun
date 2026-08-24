"""The prompt and response schema for the optional Claude assist."""

from __future__ import annotations

SYSTEM = """You are helping someone split their computer time between two jobs.

You will be given a list of windows they had open that the tracker could not
categorise. For each one, say which job it most likely belongs to, or say
"unclear" if there is genuinely not enough to go on.

Prefer "unclear" over a guess. A wrong label silently lands on a timesheet and
is worse than an honest gap the person can resolve themselves.

Where the window clearly identifies a whole website or program that will always
belong to one job, suggest a rule so the tracker stops asking. Do not suggest a
rule for something incidental, like a generic document title or a search engine.
"""


def build_system_prompt(categories: list[dict], example_rules: list[dict]) -> str:
    lines = [SYSTEM, "\nThe two jobs:"]
    for cat in categories:
        description = (cat.get("description") or "").strip()
        lines.append(
            f'- "{cat["key"]}" — {cat["display_name"]}'
            + (f": {description}" if description else "")
        )
    lines.append('- "unclear" — not enough information')

    if example_rules:
        lines.append("\nRules this person already set, as a guide to their work:")
        for rule in example_rules[:15]:
            lines.append(
                f"- {rule['kind'].replace('_', ' ')} \"{rule['pattern']}\""
                f" → {rule['category_key']}"
            )
    return "\n".join(lines)


def build_user_message(items: list[dict]) -> str:
    lines = ["Categorise each of these windows:\n"]
    for item in items:
        parts = [f"id={item['id']}", f"app={item['app']}"]
        if item.get("domain"):
            parts.append(f"website={item['domain']}")
        if item.get("url"):
            parts.append(f"address={item['url']}")
        parts.append(f'title="{item["title"]}"')
        lines.append("- " + "  ".join(parts))
    return "\n".join(lines)


def response_schema(category_keys: list[str]) -> dict:
    """Structured output, so the reply parses with the standard library."""
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "category": {"type": "string", "enum": [*category_keys, "unclear"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "suggested_rule": {
                            "type": ["object", "null"],
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["domain", "domain_suffix", "exe", "title_contains"],
                                },
                                "pattern": {"type": "string"},
                            },
                            "required": ["kind", "pattern"],
                            "additionalProperties": False,
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "category", "confidence", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    }
