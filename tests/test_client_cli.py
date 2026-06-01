"""Unit tests for decafclaw client CLI argument parsing."""

import pytest

from decafclaw.client.cli import parse_args


def test_send_minimal(monkeypatch):
    monkeypatch.delenv("DECAFCLAW_TOKEN", raising=False)
    monkeypatch.delenv("DECAFCLAW_HOST", raising=False)
    args = parse_args(["send", "--token", "dfc_x", "--prompt", "hello"])
    assert args.action == "send"
    assert args.token == "dfc_x"
    assert args.host == "http://localhost:8088"
    assert args.prompts == ["hello"]
    assert args.conv is None
    assert args.timeout == 180.0
    assert args.fmt == "summary"


def test_token_and_host_from_env(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_env")
    monkeypatch.setenv("DECAFCLAW_HOST", "https://example.com")
    args = parse_args(["send", "--prompt", "hi"])
    assert args.token == "dfc_env"
    assert args.host == "https://example.com"


def test_explicit_token_overrides_env(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_env")
    args = parse_args(["send", "--token", "dfc_flag", "--prompt", "hi"])
    assert args.token == "dfc_flag"


def test_multiple_prompts_preserve_order(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    args = parse_args(["send", "--prompt", "one", "--prompt", "two"])
    assert args.prompts == ["one", "two"]


def test_script_file_lines_become_prompts(tmp_path, monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    script = tmp_path / "s.txt"
    script.write_text("first line\n\nsecond line\n")  # blank lines skipped
    args = parse_args(["send", "--script", str(script)])
    assert args.prompts == ["first line", "second line"]


def test_missing_token_errors(monkeypatch):
    monkeypatch.delenv("DECAFCLAW_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        parse_args(["send", "--prompt", "hi"])


def test_send_requires_a_prompt(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    with pytest.raises(SystemExit):
        parse_args(["send"])


def test_respond_defaults_to_approve(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    args = parse_args(["respond", "--conv", "web-1", "--confirmation-id", "c1"])
    assert args.action == "respond"
    assert args.conv == "web-1"
    assert args.confirmation_id == "c1"
    assert args.approved is True


def test_respond_deny(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    args = parse_args(["respond", "--conv", "web-1", "--confirmation-id", "c1",
                       "--deny"])
    assert args.approved is False


def test_respond_requires_conv(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    with pytest.raises(SystemExit):
        parse_args(["respond", "--confirmation-id", "c1"])


def test_respond_requires_confirmation_id(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    with pytest.raises(SystemExit):
        parse_args(["respond", "--conv", "web-1"])


def test_format_jsonl(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    args = parse_args(["send", "--prompt", "hi", "--format", "jsonl"])
    assert args.fmt == "jsonl"
