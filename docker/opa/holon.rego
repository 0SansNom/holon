package holon.authz

import rego.v1

# ABAC layer — SAS v2 §8.1. Adapted directly from the SAS's own worked
# example: deny access to a confidential resource unless the connecting
# principal is in an allowed country. R8.1: ABAC only ever narrows what
# ReBAC already granted — PermissionClient.authorize() always calls this
# after a ReBAC grant, never in isolation.

default allow := true

allow := false if {
    input.resource.classification == "confidential"
    not input.principal.country in allowed_countries
}

allowed_countries := {"FR", "DE", "ES", "IT", "NL", "BE", "SE", "PL"}
