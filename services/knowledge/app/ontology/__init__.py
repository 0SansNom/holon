"""Conceptual ontology — seeded ObjectTypes and RelationTypes.

Split from a single 1075-line `ontology.py` into this package, one
submodule per sub-domain (`urns`, `object_types`, `interfaces`,
`markings`, `publishing`, `branching`, `relation_types`, `authz_seed`).
This `__init__.py` re-exports the *entire* previous public surface so
every existing `ontology.X(...)`-style call (module-qualified, not
`from .ontology import X`) elsewhere in this service keeps working
byte-for-byte — no call site needed to change.

See the original module's docstring, preserved here: ObjectTypes are
seeded at startup from code, but no longer *only* editable that way —
**ontology lifecycle** (versioning/publication) is real.
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
    customer_object_type_urn,
    inventory_level_object_type_urn,
    object_type_urn,
    order_object_type_urn,
    product_review_object_type_urn,
    relation_type_urn,
    support_ticket_object_type_urn,
    supplier_object_type_urn,
    workspace_urn,
)
from .object_types import (
    CUSTOMER_PROPERTY_MAPPING,
    DDL,
    INITIAL_CLASSIFICATION,
    INVENTORY_LEVEL_PROPERTY_MAPPING,
    ORDER_PROPERTY_MAPPING,
    PRODUCT_REVIEW_PROPERTY_MAPPING,
    RELATION_TYPES,
    SUPPLIER_PROPERTY_MAPPING,
    SUPPORT_TICKET_PROPERTY_MAPPING,
    create_object_type,
    delete_object_type,
    ensure_schema,
    ensure_seeded,
    get_object_type,
    get_object_type_by_dataset,
    get_object_type_version,
    get_property_classifications,
    list_object_type_versions,
    list_object_types,
    upsert_property_classification,
)
from .interfaces import create_interface_type, get_interface_type, list_interface_types, update_interface_type
from .markings import create_marking, get_instance_markings_bulk, get_marking, list_markings, set_instance_markings
from .value_types import create_value_type, get_value_type, list_value_types, update_value_type, validate_value
from .shared_property_types import (
    create_shared_property_type,
    get_shared_property_type,
    list_shared_property_types,
    update_shared_property_type,
)
from .action_types import create_action_type, get_action_type, list_action_types
from .publishing import propose_object_type_version, publish_object_type_version
from .branching import create_branch, get_branch, list_branch_reviews, list_branches, review_branch, update_branch_draft
from .relation_types import (
    VALID_CARDINALITIES,
    create_relation_type,
    get_relation_type,
    list_relation_types,
    update_relation_type,
)
from .object_type_groups import create_object_type_group, get_object_type_group, list_object_type_groups
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
    "CUSTOMER_PROPERTY_MAPPING",
    "ORDER_PROPERTY_MAPPING",
    "SUPPORT_TICKET_PROPERTY_MAPPING",
    "PRODUCT_REVIEW_PROPERTY_MAPPING",
    "SUPPLIER_PROPERTY_MAPPING",
    "INVENTORY_LEVEL_PROPERTY_MAPPING",
    "RELATION_TYPES",
    "INITIAL_CLASSIFICATION",
    "DDL",
    "VALID_CARDINALITIES",
    "object_type_urn",
    "relation_type_urn",
    "customer_object_type_urn",
    "order_object_type_urn",
    "support_ticket_object_type_urn",
    "product_review_object_type_urn",
    "supplier_object_type_urn",
    "inventory_level_object_type_urn",
    "workspace_urn",
    "ensure_schema",
    "ensure_seeded",
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
    "create_marking",
    "get_marking",
    "list_markings",
    "set_instance_markings",
    "get_instance_markings_bulk",
    "create_value_type",
    "get_value_type",
    "list_value_types",
    "update_value_type",
    "validate_value",
    "create_shared_property_type",
    "get_shared_property_type",
    "list_shared_property_types",
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
    "create_object_type_group",
    "get_object_type_group",
    "list_object_type_groups",
    "ensure_authz_seeded",
    "ALLOWED_RESOURCE_TYPES",
    "create_resource_branch",
    "get_resource_branch",
    "list_resource_branches",
    "update_resource_branch_draft",
    "review_resource_branch",
    "list_resource_branch_reviews",
]
