from maestri_canvas.replay import normalize_replay


def test_snapshot_gets_crlf_and_loses_trailing_blank_rows():
    snap = b"echo hi\nhi\nroot@h:/# " + b"\n" * 17
    assert normalize_replay(snap) == b"echo hi\r\nhi\r\nroot@h:/# "      # prompt keeps its trailing space: the cursor stays after it


def test_empty_screen_snapshot_becomes_nothing():
    assert normalize_replay(b"\n" * 24) == b""


def test_live_output_is_never_touched():
    for live in (b"echo hi\r\n", b"\x1b[?2004hroot@h:/# ", b"plain no newline", b"a\r\nb\nc", b"\x1b[31mred\x1b[0m\n"):
        assert normalize_replay(live) == live
