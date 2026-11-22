"""Settings → 连接: paste keys, link banks / brokerages in the browser, sync now.

Secrets go straight to the Keychain and never come back out of this API: GET reports only which
fields are filled in."""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection

from pf.api.deps import get_conn
from pf.connectors import CONNECTORS, Secrets
from pf.connectors.base import ConnectorError, load_state, save_state
from pf.connectors.plaid import finish_link, start_link
from pf.connectors.snaptrade import personal, portal_url, register

router = APIRouter(prefix="/api/connections", tags=["connections"])
Conn = Annotated[Connection, Depends(get_conn)]


class FieldOut(BaseModel):
    name: str
    label: str
    secret: bool
    filled: bool


class ConnectionOut(BaseModel):
    source: str
    label: str
    connected: bool
    fields: list[FieldOut]
    status: str | None = None  # ok | partial | error | reauth
    message: str | None = None
    last_sync: str | None = None
    accounts: int = 0
    linked: list[str] = []  # Plaid: banks signed in to


class KeysIn(BaseModel):
    values: dict[str, str]


class SyncOut(BaseModel):
    source: str
    ok: bool
    accounts: int
    new: int
    message: str | None = None
    reauth: bool = False
    partial: bool = False


class UrlOut(BaseModel):
    url: str
    link_token: str | None = None


class LinkIn(BaseModel):
    link_token: str


class LinkedOut(BaseModel):
    added: list[str]


def _client() -> httpx.Client:
    return httpx.Client(timeout=60, headers={"User-Agent": "Keel/1.0"})


def _connector(source: str) -> Any:
    if source not in CONNECTORS:
        raise HTTPException(404, f"unknown connection {source}")
    return CONNECTORS[source]


def _secrets(source: str) -> dict[str, Any]:
    got = Secrets.get(source)
    if not got:
        raise HTTPException(400, "paste the keys first")
    return got


@router.get("", response_model=list[ConnectionOut])
def list_connections(conn: Conn) -> list[ConnectionOut]:
    state = load_state(conn)
    out = []
    for source, c in CONNECTORS.items():
        secrets = Secrets.get(source) or {}
        st = state.get(source, {})
        out.append(
            ConnectionOut(
                source=source,
                label=c.label,
                connected=bool(secrets),
                fields=[
                    FieldOut(
                        name=f.name,
                        label=f.label,
                        secret=f.secret,
                        filled=bool(secrets.get(f.name)),
                    )
                    for f in c.fields
                ],
                status=st.get("status"),
                message=st.get("message"),
                last_sync=st.get("last_sync"),
                accounts=st.get("accounts", 0),
                linked=[i.get("institution", "") for i in secrets.get("items", [])]
                if source == "plaid"
                else [],
            )
        )
    return out


@router.put("/{source}", response_model=ConnectionOut)
def save_keys(source: str, body: KeysIn, conn: Conn) -> ConnectionOut:
    c = _connector(source)
    names = {f.name for f in c.fields}
    values = {k: v.strip() for k, v in body.values.items() if k in names and v.strip()}
    current = Secrets.get(source) or {}
    merged = {**current, **values}  # keep linked banks / the registered SnapTrade user
    optional = {"region", "host"}
    missing = [f.name for f in c.fields if f.name not in optional and not merged.get(f.name)]
    if missing:
        raise HTTPException(400, f"missing: {', '.join(missing)}")
    needs_user = not personal(merged) and (
        "user_secret" not in merged or values.get("consumer_key")
    )
    if source == "snaptrade" and needs_user:
        try:
            with _client() as client:
                merged = register(client, merged["client_id"], merged["consumer_key"])
        except (ConnectorError, httpx.HTTPError) as exc:
            raise HTTPException(400, f"SnapTrade: {exc}") from exc
    Secrets.set(source, merged)
    return next(x for x in list_connections(conn) if x.source == source)


@router.delete("/{source}", response_model=ConnectionOut)
def disconnect(source: str, conn: Conn) -> ConnectionOut:
    _connector(source)
    Secrets.delete(source)
    state = load_state(conn)
    state.pop(source, None)
    save_state(conn, state)
    return next(x for x in list_connections(conn) if x.source == source)


@router.post("/{source}/sync", response_model=SyncOut)
def sync_one(source: str) -> SyncOut:
    from pf.connectors import sync_now

    _connector(source)  # 404 for an unknown source
    return SyncOut(**sync_now(source)[0].__dict__)


@router.post("/sync", response_model=list[SyncOut])
def sync_everything() -> list[SyncOut]:
    from pf.connectors import sync_now

    return [SyncOut(**r.__dict__) for r in sync_now()]


@router.post("/plaid/link", response_model=UrlOut)
def plaid_link() -> UrlOut:
    try:
        with _client() as client:
            got = start_link(client, _secrets("plaid"))
    except ConnectorError as exc:
        raise HTTPException(400, str(exc)) from exc
    return UrlOut(url=got["url"], link_token=got["link_token"])


@router.post("/plaid/link/finish", response_model=LinkedOut)
def plaid_link_finish(body: LinkIn) -> LinkedOut:
    try:
        with _client() as client:
            return LinkedOut(added=finish_link(client, _secrets("plaid"), body.link_token))
    except ConnectorError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/snaptrade/portal", response_model=UrlOut)
def snaptrade_portal() -> UrlOut:
    try:
        with _client() as client:
            return UrlOut(url=portal_url(client, _secrets("snaptrade")))
    except ConnectorError as exc:
        raise HTTPException(400, str(exc)) from exc
