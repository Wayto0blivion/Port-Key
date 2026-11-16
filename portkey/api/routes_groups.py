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
    raise NotImplementedError("package 2")


@router.post("/api/groups", response_model=Group, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupCreate, services: ServicesDep) -> Group:
    raise NotImplementedError("package 2")


@router.patch("/api/groups/{group_id}", response_model=Group)
def update_group(group_id: str, body: GroupUpdate, services: ServicesDep) -> Group:
    raise NotImplementedError("package 2")


@router.delete("/api/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: str, services: ServicesDep) -> None:
    raise NotImplementedError("package 2")


@router.get("/api/tags", response_model=TagListResponse)
def list_tags(services: ServicesDep) -> TagListResponse:
    raise NotImplementedError("package 2")


@router.patch("/api/tags/{name}", response_model=TagInfo)
def rename_tag(name: str, body: TagRename, services: ServicesDep) -> TagInfo:
    raise NotImplementedError("package 2")


@router.delete("/api/tags/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(name: str, services: ServicesDep) -> None:
    raise NotImplementedError("package 2")
