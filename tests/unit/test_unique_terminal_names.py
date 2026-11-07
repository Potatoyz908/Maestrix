from types import SimpleNamespace as NS

from maestri_core.unique_names import dedupe_terminal_names, unique_name


def _t(name):
    return NS(node_type="terminal", content=NS(name=name))


def test_free_name_is_kept_and_taken_name_gets_a_number():
    assert unique_name("Claude", ["Codex"]) == "Claude"
    assert unique_name("Claude", ["claude"]) == "Claude #2"
    assert unique_name("Claude", ["Claude", "Claude #2"]) == "Claude #3"


def test_saved_duplicates_become_numbered_from_one():
    nodes = [_t("Agent"), _t("Agent"), _t("Other"), _t("agent")]
    assert dedupe_terminal_names(nodes) is True
    assert [n.content.name for n in nodes] == ["Agent #1", "Agent #2", "Other", "Agent #3"]
    assert dedupe_terminal_names(nodes) is False


def test_non_terminal_nodes_are_ignored():
    note = NS(node_type="stickyNote", content=NS(name="Agent"))
    assert dedupe_terminal_names([_t("Agent"), note]) is False
