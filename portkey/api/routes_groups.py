"""Folder and tag routes (package 2): /api/groups*, /api/tags*. Contract: docs/api.md
"Folders" and "Tags". Filtering and search are client-side; these only manage the sets."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from portkey.api.deps import ServicesDep, require_auth
from portkey.api.schemas import (
    Group,
    GroupCreate,
    GroupList,
    GroupUpdate,
    TagInfo,
    TagListResponse,
    TagRename,
)

router = APIRouter(tags=["groups"], dependencies=[Depends(require_auth)])


@router.get("/api/groups", response_model=GroupList)
def list_groups(services: ServicesDep) -> GroupList:
    groups = services.groups.list_all()
    return GroupList(groups=[Group.model_validate(g, from_attributes=True) for g in groups])


@router.post("/api/groups", response_model=Group, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupCreate, services: ServicesDep) -> Group:
    record = services.groups.create(body.name, body.parent_id)
    return Group.model_validate(record, from_attributes=True)


@router.patch("/api/groups/{group_id}", response_model=Group)
def update_group(group_id: str, body: GroupUpdate, services: ServicesDep) -> Group:
    record = services.groups.update(group_id, body.model_dump(exclude_unset=True))
    return Group.model_validate(record, from_attributes=True)


@router.delete("/api/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: str, services: ServicesDep) -> None:
    services.groups.delete(group_id)


@router.get("/api/tags", response_model=TagListResponse)
def list_tags(services: ServicesDep) -> TagListResponse:
    tags = services.tags.list_all()
    return TagListResponse(tags=[TagInfo(name=t.name, count=t.count) for t in tags])


@router.patch("/api/tags/{name}", response_model=TagInfo)
def rename_tag(name: str, body: TagRename, services: ServicesDep) -> TagInfo:
    result = services.tags.rename(name, body.name)
    return TagInfo(name=result.name, count=result.count)


@router.delete("/api/tags/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(name: str, services: ServicesDep) -> None:
    services.tags.delete(name)
