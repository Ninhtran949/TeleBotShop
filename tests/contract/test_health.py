from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from telebotshop.api.health import create_api


@asynccontextmanager
async def no_resources(_app: FastAPI) -> AsyncIterator[None]:
    yield


def test_liveness_does_not_require_database() -> None:
    ready = AsyncMock(side_effect=AssertionError("liveness must not query DB"))
    with TestClient(create_api(ready, lifespan=no_resources)) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"
    ready.assert_not_awaited()


@pytest.mark.parametrize(("available", "status"), [(True, 200), (False, 503)])
def test_readiness_reflects_injected_probe(available: bool, status: int) -> None:
    ready = AsyncMock(return_value=available)
    with TestClient(create_api(ready, lifespan=no_resources)) as client:
        response = client.get("/health/ready")
    assert response.status_code == status
    assert response.json() == {"status": "ok" if available else "unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    ready.assert_awaited_once()


def test_http_lifespan_releases_resources() -> None:
    events = []

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        events.append("opened")
        try:
            yield
        finally:
            events.append("closed")

    with TestClient(create_api(AsyncMock(return_value=True), lifespan=lifespan)) as client:
        assert client.get("/health/live").status_code == 200
        assert events == ["opened"]
    assert events == ["opened", "closed"]
