"""Pydantic request bodies for Connectivity HTTP routes.

Kept separate from `ingest.py` so the worker/scheduler module stays focused
on sync execution rather than API schema.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class QuiesceRequest(BaseModel):
    quiesced: bool = True


class TransformStep(BaseModel):
    step_name: str
    input_dataset: str
    function_name: str
    output_dataset: str
    # Optional value type casts mapping column names to target types
    value_type_casts: Optional[dict[str, str]] = None


class CreatePipelineRequest(BaseModel):
    steps: list[TransformStep]
    workspace_id: Optional[str] = None


class SetPipelineScheduleRequest(BaseModel):
    schedule_interval_minutes: Optional[int] = None


class RegisterKafkaStreamRequest(BaseModel):
    name: str
    topic: str
    key_field: str
    dataset_name: str
    batch_interval_seconds: float = 5.0


class RegisterPluginRequest(BaseModel):
    entry_point: str


class SetPluginScheduleRequest(BaseModel):
    schedule_interval_minutes: Optional[int] = None


class RegisterConnectionRequest(BaseModel):
    name: str
    auth_type: str = "header"
    auth_header_name: Optional[str] = None
    # Optional; if omitted on edit, existing secret is retained
    auth_header_value: Optional[str] = None
    oauth2_token_url: Optional[str] = None
    oauth2_client_id: Optional[str] = None
    # Optional; if omitted on edit, existing secret is retained
    oauth2_client_secret: Optional[str] = None
    oauth2_scope: Optional[str] = None
    secret_ref: Optional[str] = None
    # Origin (scheme://host[:port]) sources using this connection must target.
    # Required on create; omitted on edit keeps the stored one.
    allowed_origin: Optional[str] = None


class RegisterSourceRequest(BaseModel):
    name: str
    base_url: str
    workspace_id: Optional[str] = None
    auth_header_name: Optional[str] = None
    auth_header_value: Optional[str] = None
    record_path: Optional[str] = None
    next_page_path: Optional[str] = None
    connection_name: Optional[str] = None
    schedule_interval_minutes: Optional[int] = None
    cursor_property: Optional[str] = None
    incremental_param: Optional[str] = None


class RegisterSqlConnectionRequest(BaseModel):
    name: str
    host: str
    dialect: str = "postgres"
    # Optional; defaults to the dialect's standard port when omitted
    port: Optional[int] = None
    database: str
    username: str
    # Snowflake compute warehouse (ignored for other dialects)
    warehouse: Optional[str] = None
    # Optional; if omitted on edit, existing secret is retained
    password: Optional[str] = None
    secret_ref: Optional[str] = None


class RegisterSqlSourceRequest(BaseModel):
    name: str
    connection_name: str
    workspace_id: Optional[str] = None
    table_name: Optional[str] = None
    query: Optional[str] = None
    schedule_interval_minutes: Optional[int] = None
    cursor_property: Optional[str] = None


class RegisterObjectConnectionRequest(BaseModel):
    name: str
    access_key_id: str
    kind: str = "s3"
    endpoint: Optional[str] = None
    region: str = "us-east-1"
    path_style: bool = True
    # Optional; if omitted on edit, existing secret is retained
    secret_access_key: Optional[str] = None
    secret_ref: Optional[str] = None


class RegisterObjectSourceRequest(BaseModel):
    name: str
    connection_name: str
    bucket: str
    format: str
    workspace_id: Optional[str] = None
    object_key: Optional[str] = None
    key_prefix: Optional[str] = None
    incremental: bool = False
    schedule_interval_minutes: Optional[int] = None


class RegisterSftpConnectionRequest(BaseModel):
    name: str
    host: str
    port: int = 22
    username: str
    # Optional; if omitted on edit, existing secret is retained
    password: Optional[str] = None
    secret_ref: Optional[str] = None


class RegisterSftpSourceRequest(BaseModel):
    name: str
    connection_name: str
    format: str
    workspace_id: Optional[str] = None
    remote_path: Optional[str] = None
    remote_prefix: Optional[str] = None
    incremental: bool = False
    schedule_interval_minutes: Optional[int] = None


class RegisterSalesforceConnectionRequest(BaseModel):
    name: str
    client_id: str
    login_url: Optional[str] = None
    # Optional; if omitted on edit, existing secret is retained
    client_secret: Optional[str] = None
    secret_ref: Optional[str] = None


class RegisterSalesforceSourceRequest(BaseModel):
    name: str
    connection_name: str
    soql: str
    workspace_id: Optional[str] = None
    api_version: str = "v59.0"
    cursor_property: Optional[str] = None
    schedule_interval_minutes: Optional[int] = None


class CloseAccountRequest(BaseModel):
    reason: str


class RegisterWriteTargetRequest(BaseModel):
    dataset_name: str
    table_name: str
    id_column: str
    allowed_properties: dict[str, str]


class WriteSourceRequest(BaseModel):
    edits: dict[str, object]
