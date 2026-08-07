"""Classification and lineage propagation.

`Classification` and `most_restrictive()` compute every ObjectType's
classification level from its sources. `union_markings()` is the Markings
(Phase B.2) equivalent for a genuinely different kind of value: Markings
are independent, composable labels (PII, Export-Controlled), not points
on one severity scale, so a derived resource's markings are the *union*
of its sources' — every marking any parent carried — rather than a
single worst-case pick.
"""

from __future__ import annotations

from enum import Enum


class Classification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


_CLASSIFICATION_RANK = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}


def most_restrictive(*values: "Classification | str") -> Classification:
    """A derived resource inherits the most restrictive classification
    of its lineage parents.
    """
    classifications = [Classification(v) for v in values]
    if not classifications:
        return Classification.INTERNAL
    return max(classifications, key=lambda c: _CLASSIFICATION_RANK[c])


def union_markings(*marking_sets: "list[str]") -> list[str]:
    """A derived resource carries every marking any of its lineage
    parents carried.

    Scope note: unlike `most_restrictive()`, this is *not* yet wired into
    `catalog.py`'s connector sync. `object_type.markings` today is a
    governance-declared, versioned field (set via ontology propose/publish/
    branch+review, validated against the `marking` registry at publish
    time — an admin decision, the plan's "attachable to an ObjectType"
    case) — auto-unioning a catalog-computed contribution into that same
    field would create two sources of truth for one value, silently
    overwriting or double-counting whatever governance explicitly
    declared. `classification` avoids this by *not* being versioned at
    all (catalog.py owns it outright, unconditionally). Lineage propagation for
    markings applies across multi-hop lineage graphs — this function provides
    the reusable primitive for combining marking sets.
    """
    result: set[str] = set()
    for markings in marking_sets:
        result.update(markings)
    return sorted(result)
