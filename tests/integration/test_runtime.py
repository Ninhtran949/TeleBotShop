import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop import bootstrap
from telebotshop.infrastructure.config import Settings


@pytest.mark.asyncio
async def test_http_lifespan_disposes_real_postgres_pool(
    database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: database)
    settings = Settings(
        app_env="test", database_url=database.url.render_as_string(hide_password=False)
    )
    pool = database.pool
    app = bootstrap.create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://local-test"
        ) as client:
            assert (await client.get("/health/live")).status_code == 200
            response = await client.get("/health/ready")
            assert response.status_code == 503
            assert response.json() == {"status": "unavailable"}
        assert client.is_closed
        assert pool.checkedin() > 0
    assert pool.checkedin() == 0
    assert pool.checkedout() == 0
