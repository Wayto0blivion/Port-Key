"""Connections, folders (groups) and tags: /api/connections, /api/groups, /api/tags."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from portkey.api.schemas.common import (
    AuthMethod,
    Color,
    ConnName,
    Fingerprint,
    FolderName,
    Hostname,
    Notes,
    Port,
    Request,
    Response,
    Tag,
    TagNames,
    Username,
    Uuid,
)

# --- connections --------------------------------------------------------------------------------


class Connection(Response):
    id: str
    name: str
    host: str
    port: int
    username: str
    auth_method: AuthMethod
    key_fp: str | None  # null = the default key
    jump_id: str | None
    group_id: str | None  # null = ungrouped
    tags: list[str]
    color: Color
    pinned: bool
    notes: str
    sort_order: int
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    use_count: int


class ConnectionList(Response):
    connections: list[Connection]


class ConnectionCreate(Request):
    name: ConnName
    host: Hostname
    port: Port = 22
    username: Username
    auth_method: AuthMethod
    key_fp: Fingerprint | None = None
    jump_id: Uuid | None = None
    group_id: Uuid | None = None
    tags: TagNames = Field(default_factory=list)
    color: Color = "grey"
    pinned: bool = False
    notes: Notes = ""


class ConnectionUpdate(Request):
    """Any subset. Send ``null`` to clear key_fp, jump_id or group_id; ``tags`` replaces the set.
    Routes must use ``model_dump(exclude_unset=True)`` so absent fields stay unchanged."""

    name: ConnName | None = None
    host: Hostname | None = None
    port: Port | None = None
    username: Username | None = None
    auth_method: AuthMethod | None = None
    key_fp: Fingerprint | None = None
    jump_id: Uuid | None = None
    group_id: Uuid | None = None
    tags: TagNames | None = None
    color: Color | None = None
    pinned: bool | None = None
    notes: Notes | None = None
    sort_order: int | None = None


class BulkRequest(Request):
    """Atomic: either every connection changes or none does. Omit ``set_group_id`` to leave
    folders alone; send ``null`` to move to Ungrouped."""

    ids: Annotated[list[Uuid], Field(min_length=1, max_length=1000)]
    set_group_id: Uuid | None = None
    add_tags: TagNames = Field(default_factory=list)
    remove_tags: TagNames = Field(default_factory=list)


class ReachabilityRequest(Request):
    ids: Annotated[list[Uuid], Field(max_length=500)]


class ReachabilityResult(Response):
    id: str
    state: Literal["up", "down", "skipped"]
    ms: int | None = None  # set when up


class ReachabilityResponse(Response):
    results: list[ReachabilityResult]


# --- folders ------------------------------------------------------------------------------------


class Group(Response):
    id: str
    name: str
    parent_id: str | None
    sort_order: int
    connection_count: int  # includes descendants


class GroupList(Response):
    groups: list[Group]


class GroupCreate(Request):
    name: FolderName
    parent_id: Uuid | None = None


class GroupUpdate(Request):
    """Any subset; ``parent_id: null`` moves the folder to the root."""

    name: FolderName | None = None
    parent_id: Uuid | None = None
    sort_order: int | None = None


# --- tags ---------------------------------------------------------------------------------------


class TagInfo(Response):
    name: str
    count: int


class TagListResponse(Response):
    tags: list[TagInfo]


class TagRename(Request):
    name: Tag
