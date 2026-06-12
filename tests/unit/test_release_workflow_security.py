from __future__ import annotations

from pathlib import Path


def _python_heredoc_body(workflow_text: str) -> str:
    start_marker = "python3 <<'EOF'"
    start = workflow_text.find(start_marker)
    assert start != -1, "release workflow must use single-quoted heredoc delimiter"
    start = workflow_text.find("\n", start)
    assert start != -1
    end = workflow_text.find("\n          EOF", start)
    assert end != -1, "release workflow Python block terminator missing"
    return workflow_text[start:end]


def test_release_workflow_uses_env_variables_for_python_block():
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    body = _python_heredoc_body(workflow)

    assert "os.environ['TAG_NAME']" in body
    assert "os.environ['REPO']" in body
    assert "os.environ['PROJECT']" in body
    assert "${TAG_NAME}" not in body
    assert "${REPO}" not in body
    assert "${PROJECT}" not in body
