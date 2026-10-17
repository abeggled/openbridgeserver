from __future__ import annotations

from obs.logic.nodes.string.comment import NODE_TYPE


def test_comment_node_has_no_ports():
    assert NODE_TYPE.inputs == []
    assert NODE_TYPE.outputs == []
    assert NODE_TYPE.config_schema["text"]["default"] == ""
    assert NODE_TYPE.config_schema["width"]["default"] == 220
    assert NODE_TYPE.config_schema["height"]["default"] == 140


def test_comment_node_stays_visible_in_the_palette():
    assert NODE_TYPE.hidden_from_palette is False
    assert NODE_TYPE.legacy is False
