"""Connected wallets, and verification against them."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, UserDB
from app.providers import credentials as creds
from app.providers import registry
from app.providers.base import ProviderError
from app.validation import engine

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["wallets"])


class AccountCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=60)
    ccxt_id: str | None = None
    provider_kind: str = "exchange"
    is_sandbox: bool = False
    # Names of environment variables, never the secrets themselves. The database
    # has a check constraint enforcing that shape.
    api_key_env_var: str | None = None
    api_secret_env_var: str | None = None
    api_password_env_var: str | None = None


class VerifyRequest(BaseModel):
    kind: str = Field(default="preflight", pattern="^(connectivity|preflight|balance)$")
    pairs: list[str] = Field(default_factory=list, max_length=50)
    stake_currency: str = "USDT"
    stake_amount: float = Field(default=10.0, gt=0)
    max_open_trades: int = Field(default=1, ge=1, le=50)


@router.get("")
async def list_accounts(db: UserDB) -> dict:
    return {
        "accounts": db.select(
            "exchange_accounts",
            columns="id,label,provider,provider_kind,ccxt_id,is_sandbox,is_active,"
                    "api_key_env_var,api_key_fingerprint,permissions,last_verified_at,"
                    "last_verification,verification_notes,created_at",
            order="created_at.desc",
        )
    }


@router.post("", status_code=201)
async def create_account(body: AccountCreate, user: CurrentUser, db: UserDB) -> dict:
    payload = body.model_dump(exclude_none=True)
    payload["owner_id"] = user.profile_id
    if body.provider_kind == "exchange" and not payload.get("ccxt_id"):
        payload["ccxt_id"] = body.provider

    # Fingerprint whatever key the named variable currently holds, so the UI can
    # later say whether the key changed without ever storing it.
    resolved = creds.resolve(payload)
    fingerprint = creds.fingerprint(resolved)
    if fingerprint:
        payload["api_key_fingerprint"] = fingerprint

    created = db.insert("exchange_accounts", payload)
    return {"account": created[0]}


@router.post("/{account_id}/verify")
async def verify_account(account_id: str, body: VerifyRequest, db: UserDB) -> dict:
    """Run a verification suite against this wallet, using its own credentials."""
    account = db.select_one("exchange_accounts", filters={"id": f"eq.{account_id}"})
    if not account:
        raise HTTPException(status_code=404, detail="no such account")

    try:
        outcome = engine.verify_account(
            db, account,
            kind=body.kind,
            pairs=body.pairs,
            stake_currency=body.stake_currency,
            stake_amount=body.stake_amount,
            max_open_trades=body.max_open_trades,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return outcome.as_dict()


@router.get("/{account_id}/balances")
async def balances(account_id: str, db: UserDB) -> dict:
    account = db.select_one("exchange_accounts", filters={"id": f"eq.{account_id}"})
    if not account:
        raise HTTPException(status_code=404, detail="no such account")

    provider = registry.build(account)
    try:
        found = provider.fetch_balances()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        provider.close()

    return {
        "balances": [
            {"currency": b.currency, "free": b.free, "used": b.used, "total": b.total}
            for b in found
        ]
    }


@router.get("/{account_id}/validations")
async def validations(account_id: str, db: UserDB, limit: int = 20) -> dict:
    runs = db.select(
        "validation_runs",
        columns="id,kind,status,summary,egress_ip,egress_region,checks_total,"
                "checks_passed,checks_warning,checks_failed,duration_ms,created_at",
        filters={"account_id": f"eq.{account_id}"},
        order="created_at.desc",
        limit=min(limit, 100),
    )
    return {"runs": runs}


@router.get("/validations/{run_id}")
async def validation_detail(run_id: str, db: UserDB) -> dict:
    run = db.select_one("validation_runs", filters={"id": f"eq.{run_id}"})
    if not run:
        raise HTTPException(status_code=404, detail="no such validation run")
    return {
        "run": run,
        "checks": db.select(
            "validation_checks", filters={"run_id": f"eq.{run_id}"}, order="id.asc"
        ),
    }


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: str, db: UserDB) -> None:
    deleted = db.delete("exchange_accounts", filters={"id": f"eq.{account_id}"})
    if not deleted:
        raise HTTPException(status_code=404, detail="no such account")
