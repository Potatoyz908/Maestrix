from maestri_core.agent_resume import resume_command


def test_known_agents_continue_their_last_conversation_with_fallback():
    assert resume_command("claude") == "claude --continue || claude"
    assert resume_command("agy --dangerously-skip-permissions") == (
        "agy --continue --dangerously-skip-permissions || agy --dangerously-skip-permissions")
    assert resume_command("codex") == "codex resume --last || codex"
    assert resume_command("/usr/bin/opencode") == "/usr/bin/opencode --continue || /usr/bin/opencode"


def test_unknown_commands_and_already_resuming_ones_are_untouched():
    for cmd in ("bash", "htop", "", "claude --resume abc", "claude -c", "codex resume", "claude; ls", "echo 'unterminated"):
        assert resume_command(cmd) == cmd
