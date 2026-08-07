"""All `/objects/*` reads — export, the six typed list/get pairs,
relation traversal, and instance-graph (all in `seeded.py`), plus the
generic self-serve list/get/action-invoke routes (`generic.py`).

Router ordering constraint, preserved exactly across the split: `seeded`
must combine into this package's `router` *before* `generic` — Starlette
matches routes in registration order, and `generic.py`'s bare
`/objects/{object_type}` / `/objects/{object_type}/{instance_id}` routes
are general enough to shadow every specific route in `seeded.py`
(including `/objects/Customer` itself) if registered first. Within
`seeded.py`, `export_objects` must itself be first among the seeded
routes for the same reason (`/objects/Customer/export` vs.
`/objects/Customer/{customer_id}`) — that ordering lives inside
`seeded.py`, since `include_router` here only controls the ordering
*between* the two files, not within either one.

Separately, `main.py` registers `routers/actions.py`'s router *before*
this package's, for the same reason again: this package's generic
`POST /objects/{object_type}/{instance_id}/actions/{action_name}` route
(in `generic.py`) would otherwise shadow `routers/actions.py`'s specific
`/objects/Customer/{customer_id}/actions/{putOnCreditHold|closeAccount}`
routes.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import generic, seeded

router = APIRouter()
router.include_router(seeded.router)
router.include_router(generic.router)
