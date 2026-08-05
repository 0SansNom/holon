"""URN identifier scheme.

    hl:{tenant}:{workspace}:{type}:{id}[@{version}]

- The URN is immutable (renaming changes display_name, never the URN).
- APIs, events and lineage reference URNs.
- Omitting @version means "latest published version".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_URN_RE = re.compile(
    r"^hl:(?P<tenant>[a-z0-9][a-z0-9\-]*):(?P<workspace>[a-z0-9][a-z0-9\-]*):"
    r"(?P<type>[a-zA-Z0-9][a-zA-Z0-9\-]*):(?P<id>[^@]+?)(?:@(?P<version>\d+))?$"
)


class InvalidURNError(ValueError):
    pass


@dataclass(frozen=True)
class URN:
    tenant: str
    workspace: str
    type: str
    id: str
    version: Optional[int] = None

    def __str__(self) -> str:
        base = f"hl:{self.tenant}:{self.workspace}:{self.type}:{self.id}"
        return f"{base}@{self.version}" if self.version is not None else base

    def unversioned(self) -> str:
        return f"hl:{self.tenant}:{self.workspace}:{self.type}:{self.id}"


def build(tenant: str, workspace: str, type_: str, id_: str, version: Optional[int] = None) -> str:
    return str(URN(tenant=tenant, workspace=workspace, type=type_, id=id_, version=version))


def parse(urn: str) -> URN:
    match = _URN_RE.match(urn)
    if not match:
        raise InvalidURNError(f"invalid URN (expected hl:{{tenant}}:{{workspace}}:{{type}}:{{id}}[@version]): {urn!r}")
    version = match.group("version")
    return URN(
        tenant=match.group("tenant"),
        workspace=match.group("workspace"),
        type=match.group("type"),
        id=match.group("id"),
        version=int(version) if version is not None else None,
    )
