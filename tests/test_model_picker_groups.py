"""Grouped conversation-picker payload tests (#1441).

``picker_model_groups`` splits the flat ``picker_models`` list into
provider groups plus a final ``Custom`` group, with no key lost or
duplicated. The picker renders these groups directly.
"""

from decafclaw.config_types import ModelConfig, ProviderConfig
from decafclaw.model_catalog import (
    CatalogModel,
    CatalogProvider,
    ModelCatalog,
    apply_catalog,
    picker_model_groups,
    picker_models,
    save_catalog,
)


def _catalog(config, providers):
    config.providers = {name: ProviderConfig(type="openai-compat", url=f"http://{name}/v1") for name in providers}
    catalog = ModelCatalog(
        providers={
            name: CatalogProvider(models=[CatalogModel(id=model_id, enabled=enabled) for model_id, enabled in models])
            for name, models in providers.items()
        }
    )
    save_catalog(config, catalog)
    apply_catalog(config, catalog)
    return catalog


def test_groups_contain_every_picker_model_exactly_once(config):
    _catalog(
        config,
        {
            "local": [("on", True), ("off", False)],
            "cloud": [("fast", True), ("slow", True)],
        },
    )
    config.model_configs["hand-alias"] = ModelConfig(provider="local", model="hand")

    groups = picker_model_groups(config)
    grouped = [model for group in groups for model in group["models"]]

    assert grouped.count("local/off") == 0
    assert sorted(grouped) == picker_models(config)
    assert len(grouped) == len(set(grouped))


def test_groups_follow_catalog_order_and_sort_within_group(config):
    _catalog(
        config,
        {
            "zeta": [("m2", True), ("m1", True)],
            "alpha": [("b", True), ("a", True)],
        },
    )
    config.model_configs["zz-alias"] = ModelConfig(provider="alpha", model="a")
    config.model_configs["aa-alias"] = ModelConfig(provider="zeta", model="m1")

    groups = picker_model_groups(config)

    assert groups == [
        {"name": "zeta", "models": ["zeta/m1", "zeta/m2"]},
        {"name": "alpha", "models": ["alpha/a", "alpha/b"]},
        {"name": "Custom", "models": ["aa-alias", "zz-alias"]},
    ]


def test_disabled_models_absent_and_empty_providers_omitted(config):
    _catalog(
        config,
        {
            "local": [("on", True), ("off", False)],
            "empty-provider": [("also-off", False)],
        },
    )

    groups = picker_model_groups(config)

    assert groups == [{"name": "local", "models": ["local/on"]}]


def test_hand_authored_collision_stays_in_provider_group_only(config):
    _catalog(config, {"local": [("on", True)]})
    # A hand-authored entry under the same key is never catalog-owned, so it
    # counts as hand-authored but must not also appear under Custom.
    config.model_configs["local/on"] = ModelConfig(provider="local", model="on", timeout=99)

    groups = picker_model_groups(config)

    assert groups == [{"name": "local", "models": ["local/on"]}]
    assert [group["name"] for group in groups].count("Custom") == 0


def test_hand_authored_aliases_land_in_custom_last(config):
    _catalog(config, {"local": [("on", True)]})
    config.model_configs["legacy-model"] = ModelConfig(provider="local", model="legacy")

    groups = picker_model_groups(config)

    assert groups[-1] == {"name": "Custom", "models": ["legacy-model"]}
    assert groups[0] == {"name": "local", "models": ["local/on"]}
