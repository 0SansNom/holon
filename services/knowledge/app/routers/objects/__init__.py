"""All `/objects/*` reads — export, relation traversal, and instance-graph
(all in `seeded.py`, despite the name — every route in it is generic over
any ObjectType via `core._type_handle`), plus the generic self-serve
list/get/action-invoke routes (`generic.py`).

Router ordering constraint: `seeded` must combine into this package's
`router` *before* `generic` — Starlette matches routes in registration
order, and `export_objects`'s `/objects/{object_type}/export` would
otherwise be shadowed by `generic.py`'s `/objects/{object_type}/{instance_id}`
(matching `"export"` as `instance_id`) if that registered first.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import generic, seeded

router = APIRouter()
router.include_router(seeded.router)
router.include_router(generic.router)
