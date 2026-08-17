"""Conceptual ontology — self-serve ObjectTypes and RelationTypes.

Split into this package, one submodule per sub-domain (`urns`,
`object_types`, `interfaces`, `markings`, `publishing`, `branching`,
`relation_types`, `authz_seed`). This `__init__.py` re-exports the entire
public surface so every `ontology.X(...)`-style call (module-qualified,
not `from .ontology import X`) elsewhere in this service works
byte-for-byte regardless of which submodule actually defines it.

**Ontology lifecycle** (versioning/publication) governs every change:
`propose_object_type_version` creates a `draft` in `object_type_version`
(its own append-only history, one row per version);
`publish_object_type_version` is the only thing that ever updates the
live `object_type` row everything else reads (`resolver.py`/
`serving_store.py`/`search.py`/every endpoint) — same draft-then-promote
discipline as Application Builder's own versioning, and the same
workspace-`approve` governance gate `create_relation_type` already uses.
"""

from __future__ import annotations

from .urns import (
    object_type_urn,
    relation_type_urn,
    workspace_urn,
)
from .object_types import (
    DDL,
    INITIAL_CLASSIFICATION,
    VALID_VISIBILITIES,
    create_object_type,
    delete_object_type,
    ensure_schema,
    get_object_type,
    get_object_type_by_dataset,
    get_object_type_version,
    get_property_classifications,
    list_object_type_versions,
    list_object_types,
    title_of,
    upsert_property_classification,
    validate_ot_metadata,
)
from .lifecycle import VALID_LIFECYCLE_STATUSES
from .interfaces import (
    create_interface_type,
    delete_interface_type,
    effective_interface_contract,
    expand_implements,
    get_interface_type,
    list_interface_types,
    object_type_names_for_interface,
    update_interface_type,
)
from .markings import (
    category_groups_satisfied,
    create_marking,
    create_marking_category,
    ensure_default_category,
    get_instance_markings_bulk,
    get_marking,
    get_marking_category,
    list_marking_categories,
    list_markings,
    marking_authz_meta,
    set_instance_markings,
)
from .value_types import (
    create_value_type,
    delete_value_type,
    deprecate_value_type,
    get_value_type,
    list_value_type_revisions,
    list_value_types,
    update_value_type,
    validate_value,
    value_type_urn,
)
from .shared_property_types import (
    create_shared_property_type,
    delete_shared_property_type,
    get_shared_property_type,
    list_shared_property_type_usage,
    list_shared_property_types,
    shared_property_type_urn,
    update_shared_property_type,
)
from .type_classes import (
    KNOWN_TYPE_CLASSES,
    find_property_with_type_class,
    has_type_class,
    normalize_type_classes,
    parse_type_class,
)
from .typed_values import (
    TypeCache,
    partition_rows_by_property_types,
    validate_object_row,
    validate_typed_property_value,
    validate_value_type_casts,
)
from .action_types import create_action_type, get_action_type, list_action_types
from .publishing import propose_object_type_version, publish_object_type_version
from .branching import create_branch, get_branch, list_branch_reviews, list_branches, review_branch, update_branch_draft
from .relation_types import (
    VALID_CARDINALITIES,
    VALID_STORAGE_KINDS,
    create_relation_type,
    delete_relation_type,
    get_relation_type,
    list_relation_types,
    update_relation_type,
)
from .object_type_groups import (
    create_object_type_group,
    delete_object_type_group,
    get_object_type_group,
    list_object_type_groups,
    update_object_type_group,
)
from .object_sets import (
    create_object_set,
    get_object_set,
    list_object_sets,
    matches_predicates,
    object_set_urn,
    update_object_set,
)
from .authz_seed import ensure_authz_seeded
from .resource_branching import (
    ALLOWED_RESOURCE_TYPES,
    create_resource_branch,
    get_resource_branch,
    list_resource_branch_reviews,
    list_resource_branches,
    review_resource_branch,
    update_resource_branch_draft,
)

__all__ = [
    "INITIAL_CLASSIFICATION",
    "DDL",
    "VALID_CARDINALITIES",
    "VALID_STORAGE_KINDS",
    "VALID_LIFECYCLE_STATUSES",
    "VALID_VISIBILITIES",
    "title_of",
    "validate_ot_metadata",
    "object_type_urn",
    "relation_type_urn",
    "workspace_urn",
    "ensure_schema",
    "create_object_type",
    "delete_object_type",
    "get_object_type_by_dataset",
    "get_object_type",
    "list_object_type_versions",
    "get_object_type_version",
    "create_interface_type",
    "get_interface_type",
    "list_interface_types",
    "update_interface_type",
    "delete_interface_type",
    "expand_implements",
    "effective_interface_contract",
    "object_type_names_for_interface",
    "create_marking",
    "create_marking_category",
    "ensure_default_category",
    "get_marking",
    "get_marking_category",
    "list_markings",
    "list_marking_categories",
    "marking_authz_meta",
    "category_groups_satisfied",
    "set_instance_markings",
    "get_instance_markings_bulk",
    "create_value_type",
    "delete_value_type",
    "deprecate_value_type",
    "get_value_type",
    "list_value_type_revisions",
    "list_value_types",
    "update_value_type",
    "validate_value",
    "value_type_urn",
    "validate_typed_property_value",
    "validate_object_row",
    "validate_value_type_casts",
    "partition_rows_by_property_types",
    "TypeCache",
    "KNOWN_TYPE_CLASSES",
    "find_property_with_type_class",
    "has_type_class",
    "normalize_type_classes",
    "parse_type_class",
    "create_shared_property_type",
    "delete_shared_property_type",
    "get_shared_property_type",
    "list_shared_property_type_usage",
    "list_shared_property_types",
    "shared_property_type_urn",
    "update_shared_property_type",
    "create_action_type",
    "get_action_type",
    "list_action_types",
    "propose_object_type_version",
    "publish_object_type_version",
    "get_branch",
    "list_branches",
    "create_branch",
    "update_branch_draft",
    "list_branch_reviews",
    "review_branch",
    "upsert_property_classification",
    "get_property_classifications",
    "get_relation_type",
    "list_object_types",
    "list_relation_types",
    "create_relation_type",
    "update_relation_type",
    "delete_relation_type",
    "create_object_type_group",
    "get_object_type_group",
    "list_object_type_groups",
    "update_object_type_group",
    "delete_object_type_group",
    "create_object_set",
    "get_object_set",
    "list_object_sets",
    "update_object_set",
    "object_set_urn",
    "matches_predicates",
    "ensure_authz_seeded",
    "ALLOWED_RESOURCE_TYPES",
    "create_resource_branch",
    "get_resource_branch",
    "list_resource_branches",
    "update_resource_branch_draft",
    "review_resource_branch",
    "list_resource_branch_reviews",
]
