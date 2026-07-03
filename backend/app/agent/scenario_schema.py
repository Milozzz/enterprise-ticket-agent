"""JSON-schema style contract for scenario config v2 runtime."""

RUNTIME_V2_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "enterprise-ticket-agent.scenario-runtime.v2",
    "title": "Scenario Runtime v2",
    "type": "object",
    "required": ["schema_version", "slot_extraction", "tool", "policy", "reply_template", "ui"],
    "properties": {
        "schema_version": {"const": "2"},
        "approval_type": {"type": "string"},
        "current_step": {"type": "string"},
        "slot_extraction": {
            "type": "object",
            "required": ["fields"],
            "properties": {
                "fields": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "required": ["type"],
                        "properties": {
                            "type": {
                                "enum": ["keyword_map", "keyword_enum", "amount", "message_excerpt"]
                            },
                            "default": {},
                            "regex": {"type": "string"},
                            "fallback_regex": {"type": "string"},
                            "patterns": {"type": "array"},
                            "options": {"type": "array"},
                            "max_length": {"type": "integer"},
                        },
                    },
                }
            },
        },
        "tool": {
            "type": "object",
            "required": ["name", "args"],
            "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
        },
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "args"],
                "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
            },
        },
        "policy": {
            "type": "object",
            "required": ["name", "args"],
            "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
        },
        "policies": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "args"],
                "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
            },
        },
        "reply_template": {"type": "string"},
        "state_outputs": {"type": "object"},
        "ui": {"type": "object"},
    },
}
