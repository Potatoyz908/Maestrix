"""Pure helpers behind the FileTree context menu, git badges and the drop-to-canvas preview rule (no display needed)."""
import os

import pytest

from maestri_canvas import file_ops
from maestri_canvas.file_ops import FileOpError, preview_frames, preview_spec
from maestri_canvas.git_badges import GitBadges, badge_for_xy, parse_branch, parse_porcelain


# ---- names / create / rename / move ---------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "   ", ".", "..", "a/b", "x\x00y", "tab\there", "a" * 256])
def test_validate_name_rejects(bad):
    with pytest.raises(FileOpError):
        file_ops.validate_name(bad)


def test_validate_name_strips_and_accepts_unicode():
    assert file_ops.validate_name("  relatório final.md ") == "relatório final.md"


def test_unique_child_keeps_extension(tmp_path):
    assert file_ops.unique_child(str(tmp_path), "untitled.txt") == "untitled.txt"
    (tmp_path / "untitled.txt").write_text("")
    (tmp_path / "untitled 2.txt").write_text("")
    assert file_ops.unique_child(str(tmp_path), "untitled.txt") == "untitled 3.txt"
    (tmp_path / ".env").write_text("")
    assert file_ops.unique_child(str(tmp_path), ".env") == ".env 2"


def test_create_never_overwrites(tmp_path):
    p = file_ops.create_file(str(tmp_path), "a.txt")
    assert os.path.isfile(p)
    with open(p, "w") as fh:
        fh.write("keep")
    with pytest.raises(FileOpError, match="already exists"):
        file_ops.create_file(str(tmp_path), "a.txt")
    with pytest.raises(FileOpError, match="already exists"):
        file_ops.create_folder(str(tmp_path), "a.txt")
    assert open(p).read() == "keep"
    assert os.path.isdir(file_ops.create_folder(str(tmp_path), "dir"))


def test_rename(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.txt").write_text("y")
    with pytest.raises(FileOpError):
        file_ops.rename(str(tmp_path / "a.txt"), "b.txt")   # never clobbers
    with pytest.raises(FileOpError):
        file_ops.rename(str(tmp_path / "a.txt"), "../escape.txt")
    assert file_ops.rename(str(tmp_path / "a.txt"), "a.txt") == str(tmp_path / "a.txt")   # no-op
    assert file_ops.rename(str(tmp_path / "a.txt"), "c.txt") == str(tmp_path / "c.txt")
    assert (tmp_path / "c.txt").read_text() == "x" and not (tmp_path / "a.txt").exists()


def test_check_move(tmp_path):
    (tmp_path / "d" / "sub").mkdir(parents=True)
    (tmp_path / "e").mkdir()
    (tmp_path / "f.txt").write_text("")
    (tmp_path / "e" / "f.txt").write_text("")
    d = str(tmp_path / "d")
    with pytest.raises(FileOpError, match="into itself"):
        file_ops.check_move(d, d)
    with pytest.raises(FileOpError, match="into itself"):
        file_ops.check_move(d, str(tmp_path / "d" / "sub"))
    with pytest.raises(FileOpError, match="Already"):
        file_ops.check_move(str(tmp_path / "f.txt"), str(tmp_path))
    with pytest.raises(FileOpError, match="already exists"):
        file_ops.check_move(str(tmp_path / "f.txt"), str(tmp_path / "e"))
    with pytest.raises(FileOpError, match="not a folder"):
        file_ops.check_move(str(tmp_path / "f.txt"), str(tmp_path / "nope"))
    assert file_ops.check_move(str(tmp_path / "f.txt"), d) == os.path.join(d, "f.txt")
    # "d2" is not inside "d" even though the string starts the same way
    (tmp_path / "d2").mkdir()
    assert file_ops.check_move(d, str(tmp_path / "d2")) == str(tmp_path / "d2" / "d")


# ---- drop-to-canvas preview rule --------------------------------------------------------------------------
def test_preview_spec(tmp_path):
    (tmp_path / "src").mkdir()
    for n in ("main.py", "README", "pic.png", "doc.pdf", "clip.mp4", "logo.svg"):
        (tmp_path / n).write_bytes(b"")
    kind = lambda n, ct=None: preview_spec(str(tmp_path / n), ct).kind
    assert kind("src") == "fileTree"
    assert kind("main.py") == "stickyNote" and kind("README") == "stickyNote"
    assert kind("pic.png", "image/png") == "portal" and kind("doc.pdf", "application/pdf") == "portal"
    assert kind("clip.mp4") == "portal" and kind("logo.svg", "image/svg+xml") == "portal"
    assert kind("main.py", "text/x-python") == "stickyNote"
    assert preview_spec(str(tmp_path / "main.py")).title == "main.py"


def test_preview_frames_stack_like_macos():
    assert preview_frames(2, 100, 50) == [(100, 50, 320, 240), (100, 310, 320, 240)]


# ---- git porcelain ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("xy,b", [(" M", "M"), ("M ", "M"), ("MM", "M"), ("A ", "A"), ("AM", "A"), (" D", "D"), ("D ", "D"),
                                  ("R ", "R"), ("RM", "R"), ("??", "?"), ("UU", "U"), ("AA", "U"), ("DD", "U"), ("!!", None),
                                  (" T", "M")])
def test_badge_for_xy(xy, b):
    assert badge_for_xy(xy) == b


def test_parse_porcelain_z_skips_rename_source_and_branch_header():
    out = "## main...origin/main [ahead 1]\0 M src/a.py\0R  new name.py\0old name.py\0?? build/\0A  lib/x.py\0"
    assert parse_porcelain(out) == {"src/a.py": "M", "new name.py": "R", "build/": "?", "lib/x.py": "A"}
    assert parse_branch(out) == "main"
    assert parse_branch("## HEAD (no branch)\0") == "HEAD (detached)"
    assert parse_branch("## No commits yet on trunk\0") == "trunk"
    assert parse_branch("## feat/x\0") == "feat/x"
    assert parse_branch(" M a\0") == ""


def test_git_badges_fold_onto_folders_and_untracked_dirs(tmp_path):
    top = str(tmp_path)
    b = GitBadges(top, {"src/pkg/a.py": "M", "src/b.py": "?", "gone.py": "D", "build/": "?"}, "main")
    j = lambda *p: os.path.join(top, *p)
    assert b.badge(j("src", "pkg", "a.py")) == "M"
    assert b.badge(j("src", "pkg"), is_dir=True) == "M"
    assert b.badge(j("src"), is_dir=True) == "M"            # M beats ?
    assert b.badge(top, is_dir=True) == "D"                 # D beats M
    assert b.badge(j("build", "deep", "x.o")) == "?"        # inside an untracked folder
    assert b.badge(j("build"), is_dir=True) == "?"
    assert b.badge(j("clean.py")) is None
    assert b.badge("/elsewhere/a.py") is None
    assert GitBadges().badge(j("src")) is None and not GitBadges()
    assert b.branch == "main" and len(b) == 4
