"""Strategy CRUD, compilation and source retrieval."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, UserDB
from app.strategy_builder import codegen, compile_check
from app.strategy_builder import spec as S

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    spec: dict
    tags: list[str] = Field(default_factory=list)


class StrategyUpdate(BaseModel):
    spec: dict
    notes: str | None = None


def _validate(payload: dict) -> S.StrategySpec:
    try:
        return S.parse(payload)
    except Exception as exc:
        # Spec errors are the user's to fix, so they come back as 422 with the
        # validator's own wording rather than a generic message.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _compile(parsed: S.StrategySpec) -> tuple[str, compile_check.CompileResult]:
    source = codegen.generate(parsed)
    return source, compile_check.check(source, parsed.class_name)


@router.get("")
async def list_strategies(db: UserDB, include_archived: bool = False) -> dict:
    filters = {} if include_archived else {"is_archived": "eq.false"}
    rows = db.select(
        "strategy_specs",
        columns="id,name,class_name,description,source,tags,is_archived,is_public,"
                "current_version_id,created_at,updated_at",
        filters=filters,
        order="updated_at.desc",
        limit=200,
    )
    return {"strategies": rows}


@router.post("", status_code=201)
async def create_strategy(body: StrategyCreate, user: CurrentUser, db: UserDB) -> dict:
    parsed = _validate(body.spec)
    source, result = _compile(parsed)

    created = db.insert("strategy_specs", {
        "owner_id": user.profile_id,
        "name": body.name,
        "class_name": parsed.class_name,
        "description": body.description or parsed.description,
        "source": "builder",
        "tags": body.tags,
    })
    strategy = created[0]

    version = db.insert("strategy_versions", {
        "strategy_id": strategy["id"],
        "version": 1,
        "spec": parsed.model_dump(mode="json", by_alias=True, exclude_none=True),
        "generated_code": source,
        "code_sha256": codegen.code_sha256(source),
        "compiles": result.ok,
        "compile_error": result.error,
        "created_by": user.profile_id,
    })[0]

    db.update(
        "strategy_specs",
        {"current_version_id": version["id"]},
        filters={"id": f"eq.{strategy['id']}"},
    )

    return {"strategy": strategy, "version": _thin(version), "compile": result.as_dict()}


@router.get("/{strategy_id}")
async def get_strategy(strategy_id: str, db: UserDB) -> dict:
    strategy = db.select_one("strategy_specs", filters={"id": f"eq.{strategy_id}"})
    if not strategy:
        raise HTTPException(status_code=404, detail="no such strategy")
    versions = db.select(
        "strategy_versions",
        columns="id,version,compiles,compile_error,code_sha256,notes,created_at",
        filters={"strategy_id": f"eq.{strategy_id}"},
        order="version.desc",
    )
    return {"strategy": strategy, "versions": versions}


@router.post("/{strategy_id}/versions", status_code=201)
async def add_version(strategy_id: str, body: StrategyUpdate, user: CurrentUser, db: UserDB) -> dict:
    strategy = db.select_one("strategy_specs", filters={"id": f"eq.{strategy_id}"})
    if not strategy:
        raise HTTPException(status_code=404, detail="no such strategy")

    parsed = _validate(body.spec)
    if parsed.class_name != strategy["class_name"]:
        raise HTTPException(
            status_code=422,
            detail=(
                f"this strategy generates class {strategy['class_name']}; a new version "
                "cannot rename it, because past backtest results reference that name. "
                "Create a new strategy instead."
            ),
        )

    source, result = _compile(parsed)
    next_version = db.rpc("next_strategy_version", {"p_strategy_id": strategy_id})

    version = db.insert("strategy_versions", {
        "strategy_id": strategy_id,
        "version": next_version,
        "spec": parsed.model_dump(mode="json", by_alias=True, exclude_none=True),
        "generated_code": source,
        "code_sha256": codegen.code_sha256(source),
        "compiles": result.ok,
        "compile_error": result.error,
        "notes": body.notes,
        "created_by": user.profile_id,
    })[0]

    db.update(
        "strategy_specs",
        {"current_version_id": version["id"]},
        filters={"id": f"eq.{strategy_id}"},
    )
    return {"version": _thin(version), "compile": result.as_dict()}


@router.post("/compile")
async def compile_only(body: dict) -> dict:
    """Validate and compile a spec without saving it.

    This is what the builder calls on every edit, so the user sees whether their
    strategy works before committing a version.
    """
    parsed = _validate(body.get("spec", body))
    source, result = _compile(parsed)
    return {
        "class_name": parsed.class_name,
        "startup_candle_count": parsed.required_startup_candles(),
        "columns": sorted(parsed.available_columns()),
        "spec_sha256": codegen.spec_hash(parsed),
        "code": source,
        "compile": result.as_dict(),
    }


@router.get("/{strategy_id}/code")
async def get_code(strategy_id: str, db: UserDB, version: int | None = None) -> dict:
    filters = {"strategy_id": f"eq.{strategy_id}"}
    if version is not None:
        filters["version"] = f"eq.{version}"
    rows = db.select(
        "strategy_versions",
        columns="id,version,generated_code,code_sha256,compiles,compile_error",
        filters=filters,
        order="version.desc",
        limit=1,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="no such strategy version")
    return rows[0]


@router.delete("/{strategy_id}", status_code=204)
async def archive_strategy(strategy_id: str, db: UserDB) -> None:
    # Archive rather than delete: backtest runs reference this row, and losing
    # the strategy would orphan results people still want to read.
    updated = db.update(
        "strategy_specs", {"is_archived": True}, filters={"id": f"eq.{strategy_id}"}
    )
    if not updated:
        raise HTTPException(status_code=404, detail="no such strategy")


def _thin(version: dict) -> dict:
    """Version metadata without the full source, which callers fetch separately."""
    return {k: v for k, v in version.items() if k not in ("generated_code", "spec")}
