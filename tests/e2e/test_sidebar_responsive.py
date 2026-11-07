"""Exercise actual GTK layout and sidebar reopening under Xvfb."""
from tests.e2e.test_gtk4_window_visual import window, maestri_app, pump_events
from gi.repository import Gtk


def test_sidebar_animation_and_reopening(window):
    assert not window.paned.get_resize_start_child()
    assert not window.paned.get_shrink_start_child()
    assert window.btn_sidebar.get_child().get_icon_name() == 'maestri-sidebar-symbolic'
    window._on_toggle_sidebar(None)
    pump_events(.3)
    assert not window.sidebar.get_visible()
    assert window.btn_floating_sidebar.get_visible()
    window.btn_floating_sidebar.emit('clicked')
    pump_events(.3)
    assert window.sidebar.get_visible()
    assert not window.btn_floating_sidebar.get_visible()
    assert window.paned.get_position() == 260
    window.set_default_size(1500, 850)
    pump_events(.3)
    assert window.paned.get_position() == 260


def test_rapid_toggle_and_injected_controls(window):
    window._on_toggle_sidebar(None)
    pump_events(.04)
    window._on_toggle_sidebar(None)
    pump_events(.3)
    assert window.sidebar.get_visible()
    assert window.paned.get_position() == 260
    assert not window._header.get_show_title_buttons()
    injected = Gtk.Button(label='ghost')
    injected.add_css_class('titlebutton')
    window._header.pack_end(injected)
    pump_events(.25)
    assert not injected.get_visible()
    assert window.traffic_box.get_visible()
    assert window.traffic_box.get_first_child().get_visible()


def test_sidebar_minimum_width_and_search_text(window):
    widths = []
    for width in (1500, 1000, 800, 1280):
        window.set_default_size(width, 800)
        pump_events(.25)
        sidebar = window.sidebar
        minimum, _natural, _mb, _nb = sidebar.measure(Gtk.Orientation.HORIZONTAL, -1)
        assert window.paned.get_position() >= minimum
        widths.append(sidebar.get_width())
        search = sidebar.search_entry
        layout = search.create_pango_layout(search.get_placeholder_text())
        text_width, _ = layout.get_pixel_size()
        assert search.get_width() >= text_width + 32
    assert len(set(widths)) == 1


def test_native_controls_and_nested_injection(window):
    box = Gtk.Box()
    controls = Gtk.WindowControls(side=Gtk.PackType.END)
    ghost = Gtk.Button(label='injected close')
    ghost.add_css_class('titlebutton')
    box.append(controls)
    box.append(ghost)
    window._header.pack_end(box)
    pump_events(.25)
    assert not controls.get_visible()
    assert not ghost.get_visible()
    assert not ghost.get_can_target()
    child = window.traffic_box.get_first_child()
    count = 0
    while child:
        assert child.get_visible() and child.get_sensitive()
        count += 1
        child = child.get_next_sibling()
    assert count == 3
