"""FileTreeView -> terminal drag and drop. A real pointer drag cannot be injected into a Wayland compositor, so the two halves are
driven separately: the source's content provider (including its text/uri-list wire format, which is what a cross-process drop sees)
and the terminals' DropTarget `drop` handler, ending in a real shell that must receive each path as ONE argument."""
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from maestri_canvas.file_dnd import dropped_text, file_content_provider, shell_quote_paths  # noqa: E402
from maestri_canvas.gi_env import vte_available  # noqa: E402


def pump_until(cond, timeout=6.0):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.004)
    return cond()


NASTY = ["plain.txt", "with space.txt", "it's.md", 'dq"x.txt', "$HOME;rm -rf ~.sh", "tab\tand\nnewline", "ação ✓.txt", "-dash.txt"]


# ---- quoting (pure) -------------------------------------------------------------------------------------
def test_shell_quote_paths():
    assert shell_quote_paths(["/a/plain.txt"]) == "/a/plain.txt "
    assert shell_quote_paths(["/a/with space.txt", "/b/c"]) == "'/a/with space.txt' /b/c "
    assert shell_quote_paths(["/x/it's"]) == "'/x/it'\"'\"'s' "
    assert shell_quote_paths(["/x/$HOME;rm"]) == "'/x/$HOME;rm' "
    assert shell_quote_paths([]) == ""
    # control characters never reach the line editor raw
    assert shell_quote_paths(["/x/tab\tand\nnl"]) == "$'/x/tab\\tand\\nnl' "
    assert shell_quote_paths(["/x/q'\x1b"]) == "$'/x/q\\'\\x1b' "


def test_remote_files_fall_back_to_the_uri():
    f = Gio.File.new_for_uri("sftp://host/srv/a b")
    assert dropped_text(f) == shlex_q(f.get_path() or f.get_uri())


def shlex_q(s):
    import shlex
    return shlex.quote(s) + " "


if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display", allow_module_level=True)

from maestri_canvas.file_tree_node import FileTreeView  # noqa: E402


@pytest.fixture
def files(tmp_path):
    out = []
    for n in NASTY:
        p = tmp_path / n
        p.write_text("")
        out.append(p)
    return out


# ---- source -------------------------------------------------------------------------------------------
def read_text(prov) -> str:
    out = Gio.MemoryOutputStream.new_resizable()
    done = {}
    prov.write_mime_type_async("text/plain;charset=utf-8", out, GLib.PRIORITY_DEFAULT, None,
                               lambda p, r: done.setdefault("ok", p.write_mime_type_finish(r)))
    assert pump_until(lambda: "ok" in done)
    out.close()
    return bytes(out.steal_as_bytes().get_data()).decode()


def test_provider_offers_filelist_then_file_then_text(files):
    prov = file_content_provider([Gio.File.new_for_path(str(files[1]))])
    fmts = prov.ref_formats()
    assert list(fmts.get_gtypes()) == [Gdk.FileList.__gtype__, Gio.File.__gtype__]   # preference order
    assert fmts.contain_mime_type("text/plain;charset=utf-8")
    # GDK serializes the FileList as text/uri-list (and portal file-transfer) for other processes
    assert fmts.union_serialize_mime_types().contain_mime_type("text/uri-list")
    assert read_text(prov) == str(files[1])
    multi = file_content_provider([Gio.File.new_for_path(str(p)) for p in files[:2]])
    assert list(multi.ref_formats().get_gtypes()) == [Gdk.FileList.__gtype__]   # a single Gio.File only for one file


def test_uri_list_round_trip(files):
    """The cross-process path: FileList -> text/uri-list bytes -> FileList, nasty names intact."""
    gfiles = [Gio.File.new_for_path(str(p)) for p in files]
    out = Gio.MemoryOutputStream.new_resizable()
    done = {}
    Gdk.content_serialize_async(out, "text/uri-list", GObject.Value(Gdk.FileList, Gdk.FileList.new_from_list(gfiles)),
                                GLib.PRIORITY_DEFAULT, None, lambda o, r: done.setdefault("w", Gdk.content_serialize_finish(r)))
    assert pump_until(lambda: "w" in done)
    out.close()
    stream = Gio.MemoryInputStream.new_from_bytes(out.steal_as_bytes())
    Gdk.content_deserialize_async(stream, "text/uri-list", Gdk.FileList, GLib.PRIORITY_DEFAULT, None,
                                  lambda s_, r: done.setdefault("v", Gdk.content_deserialize_finish(r)))
    assert pump_until(lambda: "v" in done)
    ok, value = done["v"]
    fl = value.get_boxed() if isinstance(value, GObject.Value) else value
    assert dropped_text(fl) == shell_quote_paths([str(p) for p in files])


def test_file_tree_rows_are_drag_sources(tmp_path, files):
    v = FileTreeView(tmp_path)
    win = Gtk.Window(default_width=400, default_height=500); win.set_child(v); win.present()
    assert pump_until(lambda: not v.is_loading() and v.rows())

    class LI:   # the drag handlers only need ListItem.get_item()
        def __init__(self, row): self.row = row
        def get_item(self): return self.row

    row = v.row_for("with space.txt")
    assert [f.get_path() for f in v.dragged_files(LI(row))] == [str(tmp_path / "with space.txt")]
    assert read_text(v._drag_prepare(LI(row))) == str(tmp_path / "with space.txt")
    assert v._drag_prepare(LI(None)) is None
    # every row widget carries a DragSource
    sources = []
    def walk(w):
        while w is not None:
            ctrls = w.observe_controllers()
            sources.extend(c for c in (ctrls.get_item(i) for i in range(ctrls.get_n_items())) if isinstance(c, Gtk.DragSource))
            walk(w.get_first_child()); w = w.get_next_sibling()
    walk(v.list_view.get_first_child())
    assert len(sources) >= len(v.rows())
    win.destroy()


# ---- drop targets ---------------------------------------------------------------------------------------
needs_vte = pytest.mark.skipif(not vte_available(), reason="Vte 3.91 not available")


def drop(target: Gtk.DropTarget, gfiles):
    return target.emit("drop", GObject.Value(Gdk.FileList, Gdk.FileList.new_from_list(gfiles)), 10.0, 10.0)


@needs_vte
def test_vte_terminal_view_types_quoted_paths_into_the_transport(files):
    from maestri_canvas.terminal_node import VteTerminalView

    class T:
        on_data = on_status = None
        def __init__(self): self.written = []
        def write(self, d): self.written.append(d)
        def resize(self, *a): pass
        def close(self): pass

    t = T(); view = VteTerminalView(t)
    win = Gtk.Window(default_width=600, default_height=300); win.set_child(view); win.present()
    pump_until(lambda: False, 0.2)
    gtypes = view.drop_target.get_gtypes()
    assert gtypes[0] == Gdk.FileList.__gtype__
    assert drop(view.drop_target, [Gio.File.new_for_path(str(files[0])), Gio.File.new_for_path(str(files[1]))])
    assert b"".join(w if isinstance(w, bytes) else w.encode() for w in t.written) == f"{files[0]} '{files[1]}' ".encode()   # no Enter
    assert win.get_focus() is view.term
    win.destroy()


@needs_vte
def test_local_shell_receives_each_dropped_path_as_one_argument(tmp_path, files):
    """End to end through a real shell: a counting printf proves no path was split, globbed or expanded."""
    from maestri_canvas.widget import create_terminal
    term = create_terminal(cwd=str(tmp_path), argv=["/bin/bash", "--norc", "--noprofile"], env={"PS1": "$ "})
    win = Gtk.Window(default_width=900, default_height=400); win.set_child(term); win.present()
    assert pump_until(lambda: term.maestri_pid > 0)
    out = tmp_path / "args.out"
    term.feed_child(b"printf '<%s>\\n' ")
    assert drop(term.drop_target, [Gio.File.new_for_path(str(p)) for p in files])
    term.feed_child(f"> {out}\n".encode())
    assert pump_until(lambda: out.exists() and out.read_text().count("<") == len(files))
    expected = "".join(f"<{p}>\n" for p in files)
    assert out.read_text() == expected
    win.destroy()
