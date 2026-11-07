"""FileTree Sprint 4: Grid mode + thumbnails, context menu operations, git badges, Quick Look (Space), drop on the canvas.
Needs a display; skipped otherwise. Git tests need the `git` binary."""
import os
import shutil
import subprocess
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk  # noqa: E402

from maestri_canvas.file_tree_node import FileTreeView  # noqa: E402
from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.quick_look import QuickLookWindow, preview_mode  # noqa: E402
from maestri_canvas.scene import NodeItem  # noqa: E402
from maestri_canvas.viewport import Viewport  # noqa: E402
from maestri_canvas.widget import CanvasWidget  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display", allow_module_level=True)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
F = "FFFFFFFF-0000-0000-0000-000000000044"


def pump_until(cond, timeout=5.0):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def make_png(path, w=40, h=20):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, w, h)
    pb.fill(0xFF0000FF)
    pb.savev(str(path), "png", [], [])


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n")
    (tmp_path / "README.md").write_text("# hello\n")
    make_png(tmp_path / "pic.png")
    return tmp_path


@pytest.fixture
def win():
    w = Gtk.Window(default_width=500, default_height=600)
    yield w
    w.destroy()
    pump_until(lambda: False, 0.05)


def show(win, view):
    win.set_child(view)
    win.present()
    assert pump_until(lambda: not view.is_loading() and view.rows())
    return view


# ---- grid mode ------------------------------------------------------------------------------------------------
def test_grid_mode_lists_root_sorted_and_thumbnails_images(root, win):
    v = show(win, FileTreeView(root, git=False))
    modes = []
    v.connect("view-mode-changed", lambda _v, m: modes.append(m))
    v.btn_grid.set_active(True)   # the toolbar toggle
    assert v.view_mode == "grid" and v.stack.get_visible_child_name() == "grid" and modes == ["grid"]
    assert pump_until(lambda: v.cells() == ["src", "pic.png", "README.md"])
    # the image cell swaps its icon for a thumbnail texture once decoded off-thread
    def pic_cell():
        w = v.grid_view.get_first_child()
        while w is not None:
            cell = w.get_first_child()
            if cell is not None and getattr(cell, "maestri_path", None) == str(root / "pic.png"):
                return cell
            w = w.get_next_sibling()
    assert pump_until(lambda: pic_cell() is not None)
    thumb = pic_cell().get_first_child().get_child()
    assert pump_until(lambda: thumb.get_visible_child_name() == "thumb")
    tex = thumb.get_child_by_name("thumb").get_paintable()
    assert isinstance(tex, Gdk.Texture) and max(tex.get_width(), tex.get_height()) <= 72
    # activating a folder in the grid enters it; a file emits file-activated
    got = []
    v.connect("file-activated", lambda _v, p: got.append(p))
    v.grid_view.emit("activate", v.cells().index("README.md"))
    assert got == [str(root / "README.md")]
    v.grid_view.emit("activate", v.cells().index("src"))
    assert v.root == str(root / "src") and pump_until(lambda: v.cells() == ["main.py"])
    v.btn_list.set_active(True)
    assert v.view_mode == "list" and modes == ["grid", "list"]


def test_view_mode_from_persisted_content(root, win):
    v = show(win, FileTreeView(root, view_mode="grid", git=False))
    assert v.view_mode == "grid" and v.btn_grid.get_active()
    assert pump_until(lambda: len(v.cells()) == 3)


# ---- context menu + operations -------------------------------------------------------------------------------
def test_menu_models(root, win):
    v = show(win, FileTreeView(root, git=False))
    labels = lambda m: [m.get_item_link(i, "section").get_item_attribute_value(j, "label").unpack()
                        for i in range(m.get_n_items()) for j in range(m.get_item_link(i, "section").get_n_items())]
    assert labels(v.menu_model(str(root / "README.md"))) == ["Open", "Quick Look", "New File…", "New Folder…", "Rename…",
                                                             "Move to…", "Copy Path", "Move to Trash…"]
    assert "Quick Look" not in labels(v.menu_model(str(root / "src")))
    assert labels(v.menu_model(None)) == ["New File…", "New Folder…"]   # background of the folder
    assert all(v.action_group.has_action(a) for a in FileTreeView.MENU_ACTIONS)


def test_right_click_on_row_opens_item_menu(root, win):
    v = show(win, FileTreeView(root, git=False))
    pump_until(lambda: False, 0.2)
    row = v.list_view.get_first_child()
    while row is not None and row.get_first_child().get_tooltip_text() != str(root / "README.md"):
        row = row.get_next_sibling()
    expander = row.get_first_child()
    click = next(c for c in (expander.observe_controllers().get_item(i) for i in range(expander.observe_controllers().get_n_items()))
                 if isinstance(c, Gtk.GestureClick) and c.get_button() == Gdk.BUTTON_SECONDARY)
    click.emit("pressed", 1, 5.0, 5.0)
    assert v.selected_path() == str(root / "README.md")
    assert v.context_menu.get_visible()
    v.context_menu.popdown()


def test_new_file_and_folder_via_prompt(root, win):
    v = show(win, FileTreeView(root, git=False))
    pop = v.prompt_new(str(root / "src" / "main.py"), folder=False)   # near a file -> its folder
    assert pop.entry.get_text() == "untitled.txt"
    pop.entry.set_text("../escape.txt"); pop.commit()
    assert pop.error.get_visible() and v.name_prompt is pop and not (root / "escape.txt").exists()
    pop.entry.set_text("new.py"); pop.commit()
    assert (root / "src" / "new.py").is_file() and pump_until(lambda: v.name_prompt is None)
    pop = v.prompt_new(None, folder=True)   # background -> root
    pop.entry.set_text("docs"); pop.commit()
    assert (root / "docs").is_dir()
    assert pump_until(lambda: (0, "docs") in v.rows())
    pop = v.prompt_new(None, folder=True)
    pop.entry.set_text("docs"); pop.commit()   # already exists: stays open with the error
    assert "already exists" in pop.error.get_label()
    pop.popdown()


def test_rename_selects_the_result_and_refuses_clobber(root, win):
    v = show(win, FileTreeView(root, git=False))
    pop = v.prompt_rename(str(root / "README.md"))
    assert pop.entry.get_text() == "README.md"
    assert tuple(pop.entry.get_selection_bounds())[-2:] == (0, 6)   # the stem, like Nautilus
    pop.entry.set_text("pic.png"); pop.commit()
    assert "already exists" in pop.error.get_label() and (root / "README.md").exists()
    pop.entry.set_text("NOTES.md"); pop.commit()
    assert (root / "NOTES.md").exists() and not (root / "README.md").exists()
    assert pump_until(lambda: v.selected_path() == str(root / "NOTES.md"))


def test_move_async(root, win):
    v = show(win, FileTreeView(root, git=False))
    done = {}
    v.move(str(root / "README.md"), str(root / "src"), lambda d: done.setdefault("d", d))
    assert pump_until(lambda: "d" in done) and done["d"] == str(root / "src" / "README.md")
    assert (root / "src" / "README.md").exists() and not (root / "README.md").exists()
    done.clear()
    v.move(str(root / "src"), str(root / "src"), lambda d: done.setdefault("d", d))   # refused synchronously
    assert done == {"d": None} and "into itself" in v.last_error


def test_delete_asks_then_trashes(root, win, tmp_path_factory, monkeypatch):
    v = show(win, FileTreeView(root, git=False))
    trashed = []
    monkeypatch.setattr(v, "trash", lambda p, cb=None: trashed.append(p))   # the real Trash is exercised below in isolation
    answers = iter([0, 1])

    def fake_choose(dlg, parent, cancellable, callback):
        assert dlg.get_buttons() == ["Cancel", "Move to Trash"] and dlg.get_cancel_button() == 0 and dlg.get_default_button() == 0
        idx = next(answers)
        dlg.choose_finish = lambda _r: idx
        callback(dlg, None)
    monkeypatch.setattr(Gtk.AlertDialog, "choose", fake_choose)
    v.request_delete(str(root / "README.md"))
    assert trashed == []                           # Cancel: nothing happens
    v.request_delete(str(root / "README.md"))
    assert trashed == [str(root / "README.md")]


def test_trash_really_moves_to_the_trash(tmp_path, win, monkeypatch):
    # XDG_DATA_HOME points the home Trash into tmp_path, so nothing lands in the user's real Trash
    data = tmp_path / "xdg-data"
    data.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "junk.txt").write_text("bye")
    v = show(win, FileTreeView(tmp_path / "work", git=False))
    res = {}
    v.trash(str(tmp_path / "work" / "junk.txt"), lambda ok: res.setdefault("ok", ok))
    assert pump_until(lambda: "ok" in res)
    if not res["ok"] and "no Trash" in (v.last_error or ""):
        pytest.skip("this file system has no trash")
    assert res["ok"] and not (tmp_path / "work" / "junk.txt").exists()


# ---- git badges ------------------------------------------------------------------------------------------------
@needs_git
def test_git_badges_on_rows_and_branch(root, win):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, env=env)
    run("init", "-q", "-b", "trunk")
    run("add", "src/main.py", "README.md")
    run("commit", "-qm", "init")
    (root / "src" / "main.py").write_text("print('changed')\n")
    (root / "new.txt").write_text("")
    v = show(win, FileTreeView(root))
    assert pump_until(lambda: v.badges.branch == "trunk", 10)
    assert v.git_button is not None and v.git_button.get_parent() is not None   # the branch menu is mounted in the toolbar
    assert pump_until(lambda: v.git_button.get_visible(), 10)
    # "Show Changes" in the branch menu reveals the diff pane with the modified file
    v.git_button.emit("diff-requested")
    assert v.diff_view.get_visible()
    assert pump_until(lambda: not v.diff_view.is_loading() and "changed" in v.diff_view.buffer.get_property("text"), 10)
    assert v.badge_for(str(root / "src"), True) == "M"
    assert v.badge_for(str(root / "new.txt")) == "?" and v.badge_for(str(root / "README.md")) is None

    def badge_text(name):
        row = v.list_view.get_first_child()
        while row is not None:
            exp = row.get_first_child()
            if exp is not None and exp.get_tooltip_text() == str(root / name):
                name = exp.get_child().get_last_child().name_label   # macOS: no letter, the NAME is coloured
                from maestri_canvas.git_badges import CSS_CLASS
                return next((k for k, cls in CSS_CLASS.items() if name.has_css_class(cls)), None)
            row = row.get_next_sibling()
    assert pump_until(lambda: badge_text("src") == "M" and badge_text("new.txt") == "?" and badge_text("README.md") is None)
    # a file created on disk refreshes the badges (debounced) without any click
    (root / "another.txt").write_text("")
    assert pump_until(lambda: v.badge_for(str(root / "another.txt")) == "?", 10)


def test_no_repo_means_no_badges(root, win):
    v = show(win, FileTreeView(root))
    fired = []
    v.connect("git-badges-changed", lambda *_: fired.append(1))
    pump_until(lambda: fired, 3)
    assert not v.badges and not v.badges.branch
    assert pump_until(lambda: not v.git_button.get_visible(), 5)   # outside a repo the branch menu hides itself


# ---- Quick Look --------------------------------------------------------------------------------------------------
def test_preview_modes(root):
    assert preview_mode(str(root / "pic.png"), "image/png") == "image"
    assert preview_mode(str(root / "README.md"), "text/markdown") == "text"
    assert preview_mode(str(root / "x.mp4"), "video/mp4") == "media"
    assert preview_mode(str(root / "x.pdf"), "application/pdf", webkit=False) == "info"
    assert preview_mode(str(root / "x.pdf"), "application/pdf", webkit=True) == "web"
    assert preview_mode(str(root / "src"), "inode/directory") == "info"


def press(widget, keyval):
    """Run the capture-phase key controllers of `widget` like GTK would for a key press."""
    ctrls = widget.observe_controllers()
    for c in (ctrls.get_item(i) for i in range(ctrls.get_n_items())):
        if isinstance(c, Gtk.EventControllerKey):
            return c.emit("key-pressed", keyval, 0, Gdk.ModifierType(0))
    return False


def test_space_toggles_quick_look_and_arrows_walk_files(root, win):
    v = show(win, FileTreeView(root, git=False))
    assert not press(v, Gdk.KEY_space)                     # nothing selected: Space is left alone
    v.select_path(str(root / "README.md"))
    assert press(v, Gdk.KEY_space)
    ql = v.quick_look_window
    assert isinstance(ql, QuickLookWindow) and ql.mode == "text" and ql.get_title() == "README.md"
    assert "# hello" in ql.text_view.get_buffer().get_property("text")
    assert press(ql, Gdk.KEY_Up)                           # previous file in the list
    assert ql.mode == "image" and v.selected_path() == str(root / "pic.png")
    assert press(ql, Gdk.KEY_Up) and v.selected_path() == str(root / "pic.png")   # folders are skipped: stays
    assert press(ql, Gdk.KEY_space)                        # Space closes it again
    assert pump_until(lambda: v.quick_look_window is None)


def test_quick_look_info_card_for_unknown_types(root, win):
    (root / "blob.bin").write_bytes(bytes(range(256)) * 10)
    v = show(win, FileTreeView(root, git=False))
    ql = v.quick_look(str(root / "blob.bin"))
    assert ql.mode == "info" and "2.6 KB" in ql.info_label.get_label()
    ql.close()
    assert pump_until(lambda: v.quick_look_window is None)


def test_rename_prompt_owns_space(root, win):
    v = show(win, FileTreeView(root, git=False))
    v.select_path(str(root / "README.md"))
    v.prompt_rename(str(root / "README.md"))
    assert not press(v, Gdk.KEY_space) and v.quick_look_window is None   # typing "my file" must not open Quick Look
    v.name_prompt.popdown()


# ---- drop on the canvas -------------------------------------------------------------------------------------------
def test_canvas_drop_on_background_emits_files_dropped_in_canvas_coords(root):
    c = CanvasWidget(viewport=Viewport(Point(1000, 2000), 2.0))
    c.add_node(NodeItem(F, Rect(1000, 2000, 200, 200), 1, "fileTree", "Files"), cwd=str(root), file_tree={"viewMode": "grid"})
    w = Gtk.Window(default_width=900, default_height=700); w.set_child(c); w.present()
    pump_until(lambda: False, 0.3)
    assert c.frames[F].content.view_mode == "grid"
    got = []
    c.connect("files-dropped", lambda _c, paths, x, y: got.append((paths, x, y)))
    files = Gdk.FileList.new_from_list([Gio.File.new_for_path(str(root / "README.md")), Gio.File.new_for_path(str(root / "pic.png"))])
    t = c.file_drop_target
    assert t.get_gtypes()[0] == Gdk.FileList.__gtype__
    # over the file tree node (screen 0..400 at zoom 2): not a canvas drop
    assert t.emit("motion", 50.0, 50.0) == 0
    assert not t.emit("drop", GObject.Value(Gdk.FileList, files), 50.0, 50.0)
    # on the empty background: canvas point = origin + screen / zoom
    assert t.emit("motion", 600.0, 500.0) == Gdk.DragAction.COPY and c.has_css_class("maestri-canvas-drop-hover")
    assert t.emit("drop", GObject.Value(Gdk.FileList, files), 600.0, 500.0)
    assert got == [([str(root / "README.md"), str(root / "pic.png")], 1300.0, 2250.0)]
    assert not c.has_css_class("maestri-canvas-drop-hover")
    tree_changes = []
    c.connect("file-tree-changed", lambda _c, nid, d: tree_changes.append((nid, d)))
    c.frames[F].content.set_view_mode("list")
    assert tree_changes == [(F, {"rootPath": str(root), "viewMode": "list"})]
    w.destroy()
