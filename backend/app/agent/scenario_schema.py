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
                            "required": {"type": "boolean"},
                            "prompt": {"type": "string"},
                            "label": {"type": "string"},
                            "description": {"type": "string"},
                            "missing_values": {"type": "array"},
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
        "planner": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "mode": {"enum": ["react", "plan_execute"]},
                "max_steps": {"type": "integer", "minimum": 1, "maximum": 12},
                "deadline_ms": {"type": "integer", "minimum": 100},
                "max_consecutive_failures": {"type": "integer", "minimum": 1},
                "allowed_side_effects": {
                    "type": "array",
                    "items": {"enum": ["read", "decision", "write", "external"]},
                },
            },
        },
    },
}


RUNTIME_V3_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "enterprise-ticket-agent.scenario-runtime.v3",
    "title": "Scenario Runtime v3 Declarative LangGraph",
    "type": "object",
    "required": [
        "schema_version",
        "engine",
        "entry_node",
        "nodes",
        "edges",
        "conditional_edges",
    ],
    "properties": {
        "schema_version": {"const": "3"},
        "engine": {"const": "langgraph"},
        "entry_node": {"type": "string", "minLength": 1},
        "nodes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "handler"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "handler": {"type": "string", "minLength": 1},
                    "plan_step": {"type": "string", "minLength": 1},
                    "plan_steps": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
                "additionalProperties": False,
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["from", "to"],
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "conditional_edges": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["from", "router", "routes"],
                "properties": {
                    "from": {"type": "string"},
                    "router": {"type": "string"},
                    "routes": {"type": "object", "minProperties": 1},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

# Config-driven v3 graphs may compose the generic runtime building blocks.
# Reuse the validated slot/tool/policy/UI contract instead of introducing a
# second incompatible configuration shape.
for _name, _schema in RUNTIME_V2_SCHEMA["properties"].items():
    if _name != "schema_version":
        RUNTIME_V3_SCHEMA["properties"].setdefault(_name, _schema)


RUNTIME_SCHEMAS = {
    "2": RUNTIME_V2_SCHEMA,
    "3": RUNTIME_V3_SCHEMA,
}
