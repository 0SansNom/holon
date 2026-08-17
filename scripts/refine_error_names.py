#!/usr/bin/env python3
"""Refine generic HolonError names (InvalidRequest / NotFound) to stable specifics."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Exact substring replacements on raise lines (order matters: more specific first).
LITERALS: list[tuple[str, str]] = [
    (
        "HolonError.not_found('NotFound', f\"unknown dataset: {dataset_name}\")",
        "HolonError.not_found('DatasetNotFound', f\"unknown dataset: {dataset_name}\", dataset_name=dataset_name)",
    ),
    (
        "HolonError.not_found('NotFound', f\"unknown pipeline: {name}\")",
        "HolonError.not_found('PipelineNotFound', f\"unknown pipeline: {name}\", name=name)",
    ),
    (
        "HolonError.not_found('NotFound', f\"no connection registered as {name!r}\")",
        "HolonError.not_found('ConnectionNotFound', f\"no connection registered as {name!r}\", name=name)",
    ),
    (
        "HolonError.not_found('NotFound', f\"customer {customer_id} not found in source_erp\")",
        "HolonError.not_found('SourceCustomerNotFound', f\"customer {customer_id} not found in source_erp\", customer_id=customer_id)",
    ),
    (
        "HolonError.not_found('NotFound', f\"no execution adapter plugin registered as {name!r}\")",
        "HolonError.not_found('ExecutionAdapterPluginNotFound', f\"no execution adapter plugin registered as {name!r}\", name=name)",
    ),
    (
        "HolonError.not_found('NotFound', f\"no export format plugin registered as {name!r}\")",
        "HolonError.not_found('ExportFormatPluginNotFound', f\"no export format plugin registered as {name!r}\", name=name)",
    ),
    (
        "HolonError.not_found('NotFound', f\"no function plugin registered as {name!r}\")",
        "HolonError.not_found('FunctionPluginNotFound', f\"no function plugin registered as {name!r}\", name=name)",
    ),
    (
        "HolonError.not_found('NotFound', f\"no active function plugin registered for {name!r}\")",
        "HolonError.not_found('FunctionPluginNotFound', f\"no active function plugin registered for {name!r}\", name=name)",
    ),
    (
        "HolonError.not_found('NotFound', f\"approval {approval_id} not found\")",
        "HolonError.not_found('ApprovalNotFound', f\"approval {approval_id} not found\", approval_id=approval_id)",
    ),
    (
        "HolonError.not_found('NotFound', f\"no workflow execution found for approval {approval_id}\")",
        "HolonError.not_found('WorkflowExecutionNotFound', f\"no workflow execution found for approval {approval_id}\", approval_id=approval_id)",
    ),
    (
        "HolonError.invalid_argument('InvalidRequest', \"principal belongs to another tenant\")",
        "HolonError.invalid_argument('CrossTenantPrincipal', \"principal belongs to another tenant\")",
    ),
    (
        "HolonError.invalid_argument('InvalidRequest', f\"tenant {tenant_id!r} has no workspace\")",
        "HolonError.invalid_argument('TenantHasNoWorkspace', f\"tenant {tenant_id!r} has no workspace\", tenant_id=tenant_id)",
    ),
    (
        "HolonError.invalid_argument('InvalidRequest', f\"unknown export format {format!r}\")",
        "HolonError.invalid_argument('UnknownExportFormat', f\"unknown export format {format!r}\", format=format)",
    ),
    (
        'HolonError.invalid_argument(\'InvalidRequest\', "link write/unlink must be invoked from the FK-holding (source) ObjectType",)',
        'HolonError.invalid_argument(\'LinkWriteWrongEnd\', "link write/unlink must be invoked from the FK-holding (source) ObjectType")',
    ),
    (
        'HolonError.invalid_argument(\'InvalidRequest\', "target_id is required to link")',
        'HolonError.invalid_argument(\'LinkTargetRequired\', "target_id is required to link")',
    ),
    (
        'HolonError.invalid_argument(\'InvalidRequest\', "object type is not an end of this relation")',
        'HolonError.invalid_argument(\'ObjectTypeNotOnRelation\', "object type is not an end of this relation")',
    ),
    (
        'HolonError.invalid_argument(\'InvalidRequest\', f"artifact_base64 is not valid base64: {exc}") from exc',
        'HolonError.invalid_argument(\'InvalidBase64Artifact\', f"artifact_base64 is not valid base64: {exc}") from exc',
    ),
    (
        'HolonError.invalid_argument(\'InvalidRequest\', f"source returned {exc.response.status_code}: {exc.response.text[:300]}") from exc',
        'HolonError.invalid_argument(\'SourceHttpError\', f"source returned {exc.response.status_code}: {exc.response.text[:300]}") from exc',
    ),
    (
        'HolonError.invalid_argument(\'InvalidRequest\', f"could not reach the source: {exc}") from exc',
        'HolonError.invalid_argument(\'SourceUnreachable\', f"could not reach the source: {exc}") from exc',
    ),
    (
        'HolonError.invalid_argument(\'InvalidRequest\', f"pipeline run failed: {exc}") from exc',
        'HolonError.invalid_argument(\'PipelineRunFailed\', f"pipeline run failed: {exc}") from exc',
    ),
    (
        'HolonError.invalid_argument(\'InvalidRequest\', "object_backed RelationType is missing mid ObjectType / properties",)',
        'HolonError.invalid_argument(\'IncompleteObjectBackedRelation\', "object_backed RelationType is missing mid ObjectType / properties")',
    ),
]

# Regex-based refinements for f-strings / multi-line fragments.
REGEXES: list[tuple[str, str]] = [
    (
        r"HolonError\.invalid_argument\('InvalidRequest',\s*f\"unknown classification value\(s\)",
        "HolonError.invalid_argument('InvalidClassification', f\"unknown classification value(s)",
    ),
    (
        r"HolonError\.invalid_argument\('InvalidRequest',\s*f\"unknown resource_type:",
        "HolonError.invalid_argument('InvalidResourceType', f\"unknown resource_type:",
    ),
    (
        r"HolonError\.invalid_argument\('InvalidRequest',\s*f\"RelationType \{request\.relation_name!r\} does not originate",
        "HolonError.invalid_argument('RelationTypeMismatch', f\"RelationType {request.relation_name!r} does not originate",
    ),
    (
        r"HolonError\.invalid_argument\('InvalidRequest',\s*f\"\{request\.object_type\} has no primary key",
        "HolonError.invalid_argument('MissingPrimaryKey', f\"{request.object_type} has no primary key",
    ),
    (
        r"HolonError\.invalid_argument\('InvalidRequest',\s*f\"mid ObjectType is missing mapped properties",
        "HolonError.invalid_argument('IncompleteMidObjectType', f\"mid ObjectType is missing mapped properties",
    ),
    (
        r"HolonError\.invalid_argument\('InvalidRequest',\s*f\"invalid relation: \{relation!r\} \(must be one of \{sorted\(VALID_WORKSPACE_RELATIONS\)\}\)",
        "HolonError.invalid_argument('InvalidWorkspaceRelation', f\"invalid relation: {relation!r} (must be one of {sorted(VALID_WORKSPACE_RELATIONS)})",
    ),
    (
        r"HolonError\.invalid_argument\('InvalidRequest',\s*f\"invalid relation: \{relation!r\} \(must be one of \{sorted\(VALID_PROJECT_RELATIONS\)\}\)",
        "HolonError.invalid_argument('InvalidProjectRelation', f\"invalid relation: {relation!r} (must be one of {sorted(VALID_PROJECT_RELATIONS)})",
    ),
    (
        r"HolonError\.from_http\(exc\.response\.status_code,\s*exc\.response\.text,\s*error_name='InvalidRequest'\)",
        "HolonError.from_http(exc.response.status_code, exc.response.text, error_name='UpstreamError')",
    ),
    (
        r"HolonError\.from_http\(code,\s*detail,\s*error_name='InvalidRequest'\)",
        "HolonError.from_http(code, detail, error_name='ModelRegistryError')",
    ),
]

# Function-name prefix → error name for `str(exc) from exc` invalid_argument / not_found.
FN_INVALID: list[tuple[str, str]] = [
    (r"create_object_type|update_object_type|patch_object_type|propose_|publish_|object_type", "ObjectTypeValidationFailed"),
    (r"value_type|create_value|update_value|cast", "ValueTypeValidationFailed"),
    (r"shared_property|create_shared|update_shared", "SharedPropertyValidationFailed"),
    (r"action_type|create_action|update_action", "ActionTypeValidationFailed"),
    (r"interface|create_interface|update_interface", "InterfaceValidationFailed"),
    (r"relation_type|create_relation|update_relation", "RelationTypeValidationFailed"),
    (r"object_set|create_object_set|update_object_set|evaluate_object_set", "ObjectSetValidationFailed"),
    (r"branch|create_branch|update_branch|review_branch|merge_branch", "BranchValidationFailed"),
    (r"glossary", "GlossaryValidationFailed"),
    (r"marking", "MarkingValidationFailed"),
    (r"sync_dataset|dataset", "DatasetValidationFailed"),
    (r"paging|cursor|page_", "InvalidPageCursor"),
    (r"execute|plan|join", "ExecutionPlanInvalid"),
    (r"pipeline", "PipelineValidationFailed"),
    (r"plugin|register", "PluginValidationFailed"),
    (r"connection", "ConnectionValidationFailed"),
    (r"source", "SourceValidationFailed"),
    (r"session|turn|ask|agent", "AgentRequestInvalid"),
    (r"application|collection|tag|pin", "ExperienceValidationFailed"),
    (r"workspace|tenant|principal|grant", "IdentityValidationFailed"),
]

FN_NOT_FOUND: list[tuple[str, str]] = [
    (r"shared_property", "SharedPropertyTypeNotFound"),
    (r"object_set", "ObjectSetNotFound"),
    (r"action|approval", "ActionNotFound"),
    (r"generic|invoke|revert", "ActionInvocationNotFound"),
    (r"pipeline", "PipelineNotFound"),
    (r"plugin", "PluginNotFound"),
]


def enclosing_function(text: str, pos: int) -> str:
    before = text[:pos]
    matches = list(re.finditer(r"^(?:async\s+)?def\s+(\w+)\s*\(", before, re.MULTILINE))
    return matches[-1].group(1) if matches else ""


def refine_str_exc(text: str) -> str:
    def repl_invalid(m: re.Match[str]) -> str:
        fn = enclosing_function(text, m.start())
        name = "ValidationFailed"
        for pattern, candidate in FN_INVALID:
            if re.search(pattern, fn, re.I):
                name = candidate
                break
        return f"HolonError.invalid_argument('{name}', str(exc)) from exc"

    def repl_not_found(m: re.Match[str]) -> str:
        fn = enclosing_function(text, m.start())
        name = "ResourceNotFound"
        for pattern, candidate in FN_NOT_FOUND:
            if re.search(pattern, fn, re.I):
                name = candidate
                break
        return f"HolonError.not_found('{name}', str(exc)) from exc"

    text = re.sub(
        r"HolonError\.invalid_argument\(['\"]InvalidRequest['\"],\s*str\(exc\)\)\s+from\s+exc",
        repl_invalid,
        text,
    )
    text = re.sub(
        r"HolonError\.not_found\(['\"]NotFound['\"],\s*str\(exc\)\)\s+from\s+exc",
        repl_not_found,
        text,
    )
    # Double-quoted NotFound variants in generic.py
    text = re.sub(
        r'HolonError\.not_found\("NotFound",\s*str\(exc\)\)\s+from\s+exc',
        repl_not_found,
        text,
    )
    return text


def refine_initial_admin(text: str) -> str:
    text = text.replace(
        "HolonError.invalid_argument('InvalidRequest', \"initial_admin_urn is required when creating a workspace outside your tenant \"",
        "HolonError.invalid_argument('InitialAdminRequired', \"initial_admin_urn is required when creating a workspace outside your tenant \"",
    )
    text = text.replace(
        "HolonError.invalid_argument('InvalidRequest', \"initial_admin_urn must belong to the workspace's tenant\",)",
        "HolonError.invalid_argument('InitialAdminTenantMismatch', \"initial_admin_urn must belong to the workspace's tenant\")",
    )
    return text


def refine_from_http_status(text: str) -> str:
    # ontology_admin dynamic status from ValueError often 400/409
    def repl(m: re.Match[str]) -> str:
        fn = enclosing_function(text, m.start())
        name = "ValidationFailed"
        for pattern, candidate in FN_INVALID:
            if re.search(pattern, fn, re.I):
                name = candidate
                break
        return f"HolonError.from_http(status, detail, error_name='{name}') from exc"

    return re.sub(
        r"HolonError\.from_http\(status,\s*detail,\s*error_name='InvalidRequest'\)\s+from\s+exc",
        repl,
        text,
    )


def refine_experience_upstream(text: str) -> str:
    # Multi-line InvalidRequest around object-set evaluate
    return text.replace(
        "HolonError.invalid_argument('InvalidRequest', (",
        "HolonError.invalid_argument('ObjectSetEvaluateFailed', (",
    )


def refine_connectivity_multiline(text: str) -> str:
    # First InvalidRequest ( after sync source validation
    # Leave experience-style replace careful — connectivity may share pattern
    return text


def main() -> None:
    files = list((REPO / "services").rglob("*.py"))
    changed = 0
    for path in files:
        original = path.read_text()
        text = original
        for old, new in LITERALS:
            text = text.replace(old, new)
        for pattern, repl in REGEXES:
            text = re.sub(pattern, repl, text)
        text = refine_initial_admin(text)
        text = refine_str_exc(text)
        text = refine_from_http_status(text)
        if path.name == "main.py" and "experience" in str(path):
            text = refine_experience_upstream(text)
        if text != original:
            path.write_text(text)
            changed += 1
            print(f"refined {path.relative_to(REPO)}")
    print(f"changed {changed} files")


if __name__ == "__main__":
    main()
