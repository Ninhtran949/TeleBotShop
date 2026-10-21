import pytest

from telebotshop.bot.keyboards import catalog as navigation


def test_navigation_is_actor_bound_expires_and_stores_only_browse_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(navigation, "monotonic", lambda: 0)
    store = navigation.NavigationStore()
    view = navigation.CatalogView("products", query="Bản quyền")
    token = store.remember(101, view)
    assert len(token) == 16
    assert store.resolve(101, token) == view
    assert store.resolve(102, token) is None
    monkeypatch.setattr(navigation, "monotonic", lambda: 600)
    assert store.resolve(101, token) is None


def test_navigation_capacity_evicts_old_views_without_caching_products() -> None:
    store = navigation.NavigationStore()
    first = store.remember(101, navigation.CatalogView("categories"))
    for _ in range(4096):
        store.remember(101, navigation.CatalogView("products"))
    assert store.resolve(101, first) is None
    assert len(store.entries) == 4096
