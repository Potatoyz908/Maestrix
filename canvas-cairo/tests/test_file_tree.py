"""Native fileTree node (Gtk.DirectoryList + TreeListModel). Needs a display; skipped otherwise."""
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from maestri_canvas.file_tree_node import FileTreeView  # noqa: E402
from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.interaction import NodeContent  # noqa: E402
from maestri_canvas.scene import NodeItem  # noqa: E402
from maestri_canvas.viewport import Viewport  # noqa: E402
from maestri_canvas.widget import CanvasWidget  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display", allow_module_level=True)

F = "FFFFFFFF-0000-0000-0000-000000000006"


def pump_until(cond, timeout=5.0):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.005)
    return cond()


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text("x = 1\n")
    (tmp_path / "src" / "main.py").write_text("")
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# hi\n")
    (tmp_path / "b.txt").write_text("")
    (tmp_path / ".hidden").write_text("")
    return tmp_path


@pytest.fixture
def view(root):
    v = FileTreeView(root)
    win = Gtk.Window(default_width=400, default_height=600)
    win.set_child(v)
    win.present()
    assert pump_until(lambda: not v.is_loading() and v.rows())
    yield v
    win.destroy()
    pump_until(lambda: False, 0.05)


def settle(v):
    assert pump_until(lambda: not v.is_loading())


def test_lists_folders_first_sorted_and_hides_dotfiles(view):
    assert view.rows() == [(0, "docs"), (0, "src"), (0, "b.txt"), (0, "README.md")]


def test_expanding_a_folder_loads_its_children_lazily(view):
    src = view.row_for("src")
    assert src.is_expandable() and view.row_for("src/main.py") is None   # not read until expanded
    src.set_expanded(True); settle(view)
    assert view.rows() == [(0, "docs"), (0, "src"), (1, "pkg"), (1, "main.py"), (0, "b.txt"), (0, "README.md")]
    view.row_for("src/pkg").set_expanded(True); settle(view)
    assert (2, "mod.py") in view.rows()
    assert not view.row_for("README.md").is_expandable()
    src.set_expanded(False)
    assert view.rows() == [(0, "docs"), (0, "src"), (0, "b.txt"), (0, "README.md")]


def test_activate_toggles_folders_and_emits_files(view, root):
    got = []
    view.connect("file-activated", lambda _v, p: got.append(p))
    pos = [n for _, n in view.rows()].index("src")
    view.list_view.emit("activate", pos); settle(view)
    assert view.row_for("src").get_expanded()
    view.list_view.emit("activate", [n for _, n in view.rows()].index("README.md"))
    assert got == [str(root / "README.md")]


def test_rows_carry_themed_gicons(view):
    infos = [view.tree.get_item(i).get_item() for i in range(view.tree.get_n_items())]
    assert all(i.get_icon() is not None for i in infos)
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    folder = next(i for i in infos if i.get_name() == "docs").get_icon()
    assert any(theme.has_icon(n) for n in folder.get_names())


def test_show_hidden_refilters_the_whole_tree(view):
    view.row_for("src").set_expanded(True); settle(view)
    view.set_show_hidden(True)
    assert (0, ".hidden") in view.rows() and (1, "main.py") in view.rows()   # expansion survives the refilter
    view.set_show_hidden(False)
    assert (0, ".hidden") not in view.rows()


def test_live_updates_from_disk(view, root):
    (root / "new_dir").mkdir()
    assert pump_until(lambda: (0, "new_dir") in view.rows())
    (root / "b.txt").unlink()
    assert pump_until(lambda: (0, "b.txt") not in view.rows())


def test_navigation_up_back_forward(view, root):
    roots = []
    view.connect("root-changed", lambda _v, p: roots.append(p))
    view.set_root(root / "src"); settle(view)
    assert view.rows() == [(0, "pkg"), (0, "main.py")]
    view.go_up(); settle(view)
    assert view.root == str(root)
    view.go_back(); settle(view)
    assert view.root == str(root / "src")
    view.go_forward(); settle(view)
    assert view.root == str(root)
    assert roots == [str(root / "src"), str(root), str(root / "src"), str(root)]


def test_canvas_builds_file_tree_nodes_and_routes_input(root):
    c = CanvasWidget(viewport=Viewport(Point(0, 0), 1.0))
    c.add_node(NodeItem(F, Rect(50, 50, 320, 400), 1, "fileTree", "Files"), cwd=str(root))
    win = Gtk.Window(default_width=800, default_height=600)
    win.set_child(c)
    win.present()
    v = c.frames[F].content
    assert isinstance(v, FileTreeView) and v.root == str(root)
    assert pump_until(lambda: not v.is_loading() and v.rows())
    got = []
    c.connect("file-activated", lambda _c, nid, p: got.append((nid, p)))
    v.list_view.emit("activate", [n for _, n in v.rows()].index("b.txt"))
    assert got == [(F, str(root / "b.txt"))]
    pump_until(lambda: False, 0.3)   # let the canvas tick place the frame
    ok, r = v.list_view.compute_bounds(c)
    assert isinstance(c.target_at(r.get_x() + 20, r.get_y() + 10), NodeContent)   # clicks/scroll go to the list, not the canvas
    win.destroy()
