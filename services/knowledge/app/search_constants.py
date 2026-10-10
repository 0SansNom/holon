"""Shared OpenSearch index constants for Knowledge unified search."""
from __future__ import annotations

INDEX_NAME = "holon-search"
POLICY_VERSION = 2

_INDEX_MAPPING = {
    "mappings": {
        "dynamic_templates": [
            {
                "props_as_keyword": {
                    "path_match": "props.*",
                    "mapping": {"type": "keyword"},
                }
            },
            {
                "confidential_props_as_keyword": {
                    "path_match": "confidential_props.*",
                    "mapping": {"type": "keyword"},
                }
            },
        ],
        "properties": {
            "urn": {"type": "keyword"},
            "object_type": {"type": "keyword"},
            "tenant_id": {"type": "keyword"},
            "classification": {"type": "keyword"},
            "entitlement_tokens": {"type": "keyword"},
            "policy_version": {"type": "integer"},
            "required_markings": {"type": "keyword"},
            "required_marking_count": {"type": "integer"},
            "text": {"type": "text"},
            "confidential_text": {"type": "text"},
            "props": {"type": "object", "dynamic": True},
            "confidential_props": {"type": "object", "dynamic": True},
            "index_generation": {"type": "long"},
            "indexed_at": {"type": "double"},
        },
    }
}

