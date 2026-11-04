"""Tests for the secret resolver (#1032)."""

import pytest

from decafclaw.secrets import EnvSecretResolver, SecretResolutionError, is_secret_ref, resolve_secret


class TestIsSecretRef:
    def test_detects_var_ref(self):
        assert is_secret_ref("${OPENAI_API_KEY}") is True

    def test_rejects_plain_literal(self):
        assert is_secret_ref("sk-live-abc123") is False

    def test_rejects_empty(self):
        assert is_secret_ref("") is False


class TestResolveSecret:
    def test_resolves_set_var(self):
        resolver = EnvSecretResolver(environ={"MY_KEY": "s3cret-value"})
        assert resolve_secret("${MY_KEY}", resolver=resolver) == "s3cret-value"

    def test_missing_var_raises(self):
        resolver = EnvSecretResolver(environ={})
        with pytest.raises(SecretResolutionError):
            resolve_secret("${MISSING_KEY}", resolver=resolver)

    def test_empty_var_raises(self):
        resolver = EnvSecretResolver(environ={"EMPTY_KEY": ""})
        with pytest.raises(SecretResolutionError):
            resolve_secret("${EMPTY_KEY}", resolver=resolver)

    def test_literal_passthrough(self):
        resolver = EnvSecretResolver(environ={})
        assert resolve_secret("sk-live-abc123", resolver=resolver) == "sk-live-abc123"

    def test_empty_ref_returns_empty(self):
        resolver = EnvSecretResolver(environ={})
        assert resolve_secret("", resolver=resolver) == ""
