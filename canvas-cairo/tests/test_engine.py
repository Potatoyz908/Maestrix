import math

import pytest

from maestri_canvas.constants import MIN_NODE_HEIGHT, MIN_NODE_WIDTH
from maestri_canvas.geometry import Point, Rect
from maestri_canvas.interaction import (Background, CanvasController, ConnectRequested, DeleteRequested, DrawRequested, DuplicateRequested,
                                        Mods, NodeContent, NodeEdge, NodeHeader, NodePin, NodeResized, NodesMoved, SelectionChanged,
                                        ViewportChanged)
from maestri_canvas.resize import Edge, resize_frame
from maestri_canvas.rope import RopeSimulation, static_catenary
from maestri_canvas.scene import ConnItem, NodeItem, Scene, facing_sides, pin_point
from maestri_canvas.snapping import snap
from maestri_canvas.viewport import Viewport

N = Mods.NONE


# ---- geometry / viewport ----------------------------------------------------------------------------
def test_frame_roundtrip_and_cgrect_intersects():
    assert Rect.from_frame([[1, 2], [3, 4]]).to_frame() == [[1, 2], [3, 4]]
    assert not Rect(0, 0, 10, 10).intersects(Rect(10, 0, 5, 5))      # touching edges do not intersect (CGRect semantics)
    assert Rect(0, 0, 10, 10).intersects(Rect(9, 9, 5, 5))


def test_viewport_roundtrip_and_zoom_anchor():
    vp = Viewport(Point(9800, 8500), 1.0)
    p = Point(321, 123)
    back = vp.to_screen(vp.to_canvas(p))
    assert (back.x, back.y) == pytest.approx((p.x, p.y))
    c0 = vp.to_canvas(p)
    vp.zoom_at(p, 1.7)
    c1 = vp.to_canvas(p)
    assert (c1.x, c1.y) == pytest.approx((c0.x, c0.y))


def test_zoom_clamped_and_step_additive():
    vp = Viewport(Point(0, 0), 1.0)
    vp.zoom_at(Point(0, 0), 1000); assert vp.zoom == 3.0
    vp.zoom_at(Point(0, 0), 1e-6); assert vp.zoom == 0.1
    vp.zoom = 1.0; vp.zoom_step(Point(0, 0), +1); assert vp.zoom == 1.25


def test_pan_moves_content_with_the_pointer():
    vp = Viewport(Point(0, 0), 2.0)
    vp.pan_by_screen(40, 0)               # drag right by 40px at zoom 2 => origin moves 20 canvas units left
    assert vp.origin.x == -20


def test_focus_on_centers_and_fits():
    vp = Viewport(Point(0, 0), 1.0)
    vp.focus_on(Rect(1000, 1000, 400, 200), 800, 600)
    c = vp.to_screen(Rect(1000, 1000, 400, 200).center)
    assert (c.x, c.y) == pytest.approx((400, 300))


# ---- snapping / resize --------------------------------------------------------------------------------
def test_snap_to_neighbour_edge_within_threshold_only():
    other = Rect(100, 100, 200, 100)
    p, g = snap(Rect(305, 400, 50, 50), [other])
    assert p.x == 300 and any(x.axis == "vertical" and x.position == 300 for x in g)
    assert snap(Rect(330, 500, 50, 50), [other])[0] == Point(330, 500)


def test_resize_grid_fixed_opposite_edge_and_minimum():
    f = Rect(100, 100, 400, 300)
    assert resize_frame(f, Edge.BOTTOM_RIGHT, 21, 5) == Rect(100, 100, 428, 300)   # active EDGE snaps to the 16 grid
    r = resize_frame(f, Edge.LEFT, -30, 0, grid=False)
    assert r.max_x == 500 and r.w == 430
    r = resize_frame(f, Edge.TOP_LEFT, 999, 999)
    assert (r.w, r.h) == (MIN_NODE_WIDTH, MIN_NODE_HEIGHT) and (r.max_x, r.max_y) == (500, 400)


def test_grid_rounding_matches_swift_on_exact_ties():
    from maestri_canvas.geometry import round_half_away
    assert [round_half_away(v) for v in (0.5, 1.5, 2.5, -0.5, -1.5, 12.5)] == [1, 2, 3, -1, -2, 13]   # Python round() gives 0, 2, 2, 0, -2, 12
    # right edge at 200 is the tie 200/16 = 12.5: Swift snaps it to 13 (208); banker's rounding would give 192
    assert resize_frame(Rect(0, 0, 200, 100), Edge.RIGHT, 0, 0).w == 208


# ---- ropes -------------------------------------------------------------------------------------------
def test_static_catenary_sags_down_with_fixed_ends():
    pts = static_catenary(Point(0, 0), Point(200, 0))
    assert len(pts) == 21 and pts[0] == Point(0, 0) and pts[-1] == Point(200, 0) and pts[10].y > 0


def test_rope_settles_sleeps_and_keeps_anchors():
    sim = RopeSimulation()
    slept = []
    sim.on_sleep = lambda pts: slept.append(pts)
    sim.add("r", Point(0, 0), Point(300, 0))
    n = 0
    while sim.tick() and n < 5000:
        n += 1
    assert sim.is_sleeping and slept and n < 1000
    p = sim.points("r")
    assert p[0] == Point(0, 0) and p[-1] == Point(300, 0) and p[10].y > 0


def test_rope_wakes_only_on_real_anchor_movement():
    sim = RopeSimulation()
    sim.add("r", Point(0, 0), Point(300, 0))
    while sim.tick(): pass
    sim.update_anchors("r", Point(0.1, 0), Point(300, 0))
    assert sim.is_sleeping
    sim.update_anchors("r", Point(50, 0), Point(300, 0))
    assert not sim.is_sleeping


def test_restored_rope_points_do_not_wake():
    sim = RopeSimulation()
    sim.add("r", Point(0, 0), Point(300, 0), existing=static_catenary(Point(0, 0), Point(300, 0)))
    assert sim.is_sleeping and not sim.tick()


# ---- scene ------------------------------------------------------------------------------------------
def make_scene():
    sc = Scene()
    sc.add_node(NodeItem("aa", Rect(0, 0, 200, 100), 1, "terminal", "A"))
    sc.add_node(NodeItem("bb", Rect(100, 50, 200, 100), 2, "stickyNote", "B"))   # overlaps A, on top
    sc.add_node(NodeItem("cc", Rect(600, 0, 200, 100), 3, "portal", "C"))
    return sc


def test_ids_are_case_insensitive_and_hit_test_honours_z():
    sc = make_scene()
    assert sc.get("AA") is sc.get("aa")
    assert sc.hit_node(Point(150, 75)).id == "BB"      # overlap: higher z wins
    assert sc.hit_node(Point(10, 10)).id == "AA"
    assert sc.hit_node(Point(400, 400)) is None


def test_z_caches_rebuild_only_on_z_change():
    sc = make_scene()
    asc, desc = sc.ascending(), sc.descending()
    sc.set_frame("aa", Rect(5, 5, 200, 100))
    assert sc.ascending() is asc and sc.descending() is desc          # moving a node must not re-sort (drag hot path)
    sc.raise_to_front("aa")
    assert sc.ascending() is not asc and sc.hit_node(Point(150, 75)).id == "AA"


def test_connections_rules_and_anchors():
    sc = make_scene()
    assert not sc.can_connect("aa", "AA") and sc.can_connect("aa", "bb")
    sc.add_connection(ConnItem("c1", "aa", "bb"))
    assert not sc.can_connect("bb", "aa")                              # undirected duplicate
    assert facing_sides(sc.get("aa").frame, sc.get("cc").frame) == ("right", "left")
    a, b = sc.anchors(sc.connections["C1"] if "C1" in sc.connections else sc.connections["c1"])
    assert a == pin_point(sc.get("aa").frame, "right") and b == pin_point(sc.get("bb").frame, "left")


def test_only_widget_kinds_can_be_connected():
    sc = make_scene()
    sc.add_node(NodeItem("sh", Rect(0, 300, 100, 100), 4, "shape"))
    assert not sc.can_connect("aa", "sh") and sc.can_connect("aa", "cc")
    p = pin_point(sc.get("sh").frame, "left")
    assert sc.hit_pin(p, 1.0) is None


def test_remove_node_drops_its_connections_and_selection():
    sc = make_scene()
    sc.add_connection(ConnItem("c1", "aa", "bb"))
    sc.selection = {"AA"}
    sc.remove_node("aa")
    assert not sc.connections and not sc.selection


def test_pin_hit_radius_is_constant_in_screen_pixels():
    sc = make_scene()
    p = pin_point(sc.get("cc").frame, "left")
    assert sc.hit_pin(Point(p.x - 8, p.y), 1.0)[1] == "left"
    assert sc.hit_pin(Point(p.x - 8, p.y), 3.0) is None                # zoomed in: 8 canvas units = 24px away
    assert sc.hit_pin(Point(p.x - 8, p.y), 1.0, exclude="cc") is None


# ---- controller ----------------------------------------------------------------------------------------
def ctl_with_events():
    sc = make_scene()
    ctl = CanvasController(sc, Viewport(Point(0, 0), 1.0))
    ctl.size = (1000, 600)
    ev = []
    ctl.on_event = ev.append
    return ctl, sc, ev


def test_header_drag_moves_with_threshold_and_emits_final():
    ctl, sc, ev = ctl_with_events()
    assert ctl.press(Point(20, 10), 1, N, NodeHeader("aa"))
    ctl.move(Point(21, 10), N)                                          # under the 3px threshold: no drag yet
    assert sc.get("aa").frame.x == 0
    ctl.move(Point(70, 40), N)
    assert sc.get("aa").frame == Rect(50, 30, 200, 100)
    ctl.release(Point(70, 40), N)
    moved = [e for e in ev if isinstance(e, NodesMoved)]
    assert not moved[0].final and moved[-1].final and moved[-1].frames["AA"] == Rect(50, 30, 200, 100)


def test_drag_accounts_for_zoom():
    ctl, sc, _ = ctl_with_events()
    ctl.vp.zoom = 2.0
    ctl.press(Point(20, 10), 1, N, NodeHeader("aa"))
    ctl.move(Point(120, 10), N)
    assert sc.get("aa").frame.x == 50                                    # 100 screen px at 2x = 50 canvas units


def test_multi_selection_drags_as_a_group_and_shift_toggles():
    ctl, sc, ev = ctl_with_events()
    ctl.press(Point(1, 1), 1, N, NodeHeader("aa")); ctl.release(Point(1, 1), N)
    ctl.press(Point(1, 1), 1, Mods.SHIFT, NodeHeader("cc")); ctl.release(Point(1, 1), Mods.SHIFT)
    assert sc.selection == {"AA", "CC"}
    ctl.press(Point(1, 1), 1, N, NodeHeader("aa"))                      # pressing a selected node keeps the group
    ctl.move(Point(101, 1), N)
    assert sc.get("aa").frame.x == 100 and sc.get("cc").frame.x == 700
    ctl.release(Point(101, 1), N)
    ctl.press(Point(1, 1), 1, Mods.SHIFT, NodeHeader("cc")); ctl.release(Point(1, 1), Mods.SHIFT)   # Shift+click on a selected node deselects it
    assert sc.selection == {"AA"}


def test_ctrl_drag_snaps_and_draws_guides():
    ctl, sc, _ = ctl_with_events()
    ctl.press(Point(20, 10), 1, N, NodeHeader("cc"))
    ctl.move(Point(20 - 295, 10), Mods.CTRL)                             # cc.min_x -> 305, just right of bb.max_x (300)
    assert sc.get("cc").frame.x == 300 and ctl.overlay.guides
    ctl.release(Point(20 - 295, 10), Mods.CTRL)
    assert not ctl.overlay.guides                                         # overlay cleared on release
    ctl.press(Point(20, 10), 1, N, NodeHeader("cc"))
    ctl.move(Point(20 + 5, 10), N)                                        # no Ctrl => no snapping
    assert sc.get("cc").frame.x == 305


def test_alt_drag_requests_duplicate_and_leaves_original():
    ctl, sc, ev = ctl_with_events()
    ctl.press(Point(20, 10), 1, Mods.ALT, NodeHeader("aa"))
    ctl.move(Point(220, 10), Mods.ALT)
    assert sc.get("aa").frame.x == 0 and ctl.overlay.ghosts == [Rect(200, 0, 200, 100)]
    ctl.release(Point(220, 10), Mods.ALT)
    dup = [e for e in ev if isinstance(e, DuplicateRequested)]
    assert dup and dup[0].frames["AA"] == Rect(200, 0, 200, 100)


def test_locked_nodes_do_not_move_or_resize():
    ctl, sc, _ = ctl_with_events()
    sc.get("aa").locked = True
    ctl.press(Point(20, 10), 1, N, NodeHeader("aa")); ctl.move(Point(200, 10), N)
    assert sc.get("aa").frame.x == 0
    ctl.release(Point(200, 10), N)
    ctl.press(Point(200, 100), 1, N, NodeEdge("aa", Edge.RIGHT)); ctl.move(Point(400, 100), N)
    assert sc.get("aa").frame.w == 200


def test_resize_reanchors_ropes_and_esc_restores():
    ctl, sc, ev = ctl_with_events()
    ctl.add_connection(ConnItem("c1", "aa", "bb"))
    ctl.press(Point(200, 50), 1, N, NodeEdge("aa", Edge.RIGHT)); ctl.move(Point(270, 50), N)
    assert sc.get("aa").frame.w == 272
    assert ctl.ropes.get("c1").ax == 272                                  # rope end followed the right pin
    assert ctl.cancel()
    assert sc.get("aa").frame.w == 200 and ctl.ropes.get("c1").ax == 200
    assert isinstance([e for e in ev if isinstance(e, NodeResized)][-1], NodeResized)


def test_marquee_selects_intersecting_and_click_on_empty_deselects():
    ctl, sc, ev = ctl_with_events()
    ctl.press(Point(-10, -10), 1, N, Background()); ctl.move(Point(310, 160), N)
    assert sc.selection == {"AA", "BB"} and ctl.overlay.marquee is not None
    ctl.release(Point(310, 160), N)
    assert sc.selection == {"AA", "BB"} and ctl.overlay.marquee is None
    ctl.press(Point(900, 500), 1, N, Background()); ctl.release(Point(900, 500), N)
    assert sc.selection == set()


def test_pan_middle_button_and_space():
    ctl, _, ev = ctl_with_events()
    ctl.press(Point(100, 100), 2, N, Background()); ctl.move(Point(130, 90), N)
    assert ctl.vp.origin == Point(-30, 10) and any(isinstance(e, ViewportChanged) for e in ev)
    ctl.release(Point(130, 90), N)
    assert ctl.key("space", N, True) and ctl.space_held
    ctl.press(Point(0, 0), 1, N, Background()); ctl.move(Point(10, 0), N)
    assert ctl.vp.origin.x == -40                                          # Space+left drag pans instead of marquee
    ctl.release(Point(10, 0), N); ctl.key_release("space")
    assert not ctl.space_held


def test_scroll_pan_zoom_and_content_passthrough():
    ctl, _, _ = ctl_with_events()
    ctl.scroll(Point(0, 0), 0, 40, N)
    assert ctl.vp.origin.y == 40                                           # plain scroll pans
    ctl.scroll(Point(0, 0), 40, 0, Mods.SHIFT)
    assert ctl.vp.origin.y == 80 and ctl.vp.origin.x == 0                  # Shift swaps the axis
    z = ctl.vp.zoom
    ctl.scroll(Point(500, 300), 0, -40, Mods.CTRL)
    assert ctl.vp.zoom == pytest.approx(z * math.exp(0.1))
    assert ctl.press(Point(5, 5), 1, N, NodeContent("aa")) is False       # content: engine selects but does NOT claim


def test_connect_via_pins_validates_targets():
    ctl, sc, ev = ctl_with_events()
    ctl.press(Point(200, 50), 1, N, NodePin("aa", "right"))
    tgt = pin_point(sc.get("cc").frame, "left")
    ctl.move(Point(tgt.x + 3, tgt.y), N)
    assert ctl.overlay.connecting[2] == ("CC", "left")                     # hovered pin highlighted
    ctl.release(Point(tgt.x + 3, tgt.y), N)
    assert [e for e in ev if isinstance(e, ConnectRequested)] == [ConnectRequested("AA", "right", "CC", "left")]
    # releasing over empty space, or on the source node, requests nothing
    n = len(ev)
    ctl.press(Point(200, 50), 1, N, NodePin("aa", "right")); ctl.release(Point(900, 500), N)
    assert len(ev) == n


def test_draw_tool_creates_grid_snapped_frame_then_returns_to_select():
    ctl, _, ev = ctl_with_events()
    ctl.key("p", N, True)
    assert ctl.tool == "portal"
    ctl.press(Point(1003, 1005), 1, N, Background()); ctl.move(Point(1403, 1305), N)
    ctl.release(Point(1403, 1305), N)
    d = [e for e in ev if isinstance(e, DrawRequested)]
    assert d and d[0].kind == "portal" and d[0].frame.x % 16 == 0 and d[0].frame.w >= MIN_NODE_WIDTH
    assert ctl.tool == "select"
    ctl.key("p", N, True); ctl.press(Point(0, 0), 1, N, Background()); ctl.release(Point(2, 2), N)
    assert not [e for e in ev if isinstance(e, DrawRequested)][1:]         # a stray click draws nothing


def test_keyboard_shortcuts_and_focus_routing():
    ctl, sc, ev = ctl_with_events()
    sc.selection = {"AA"}
    assert ctl.key("equal", Mods.CTRL, canvas_focused=False) and ctl.vp.zoom == 1.25     # Ctrl chords work even inside a node
    assert not ctl.key("delete", N, canvas_focused=False)                                 # plain keys belong to the focused widget
    assert ctl.key("delete", N, canvas_focused=True)
    assert [e for e in ev if isinstance(e, DeleteRequested)][-1].ids == ("AA",)
    n_del = len([e for e in ev if isinstance(e, DeleteRequested)])
    assert not ctl.key("w", Mods.CTRL, canvas_focused=False)                              # readline word-rubout inside a terminal: never delete the node
    assert len([e for e in ev if isinstance(e, DeleteRequested)]) == n_del
    assert ctl.key("w", Mods.CTRL | Mods.SHIFT, canvas_focused=False)                      # explicit chord from inside a node
    assert ctl.key("w", Mods.CTRL, canvas_focused=True)                                    # from the canvas, as on macOS
    assert ctl.key("right", N, True) and sc.get("aa").frame.x == 16
    assert ctl.key("down", Mods.SHIFT, True) and sc.get("aa").frame.y == 160
    assert ctl.key("a", Mods.CTRL, True) and sc.selection == {"AA", "BB", "CC"}
    assert ctl.key("0", Mods.CTRL, True) and ctl.vp.zoom == 1.0
    assert ctl.key("escape", N, True) and sc.selection == set()
    assert not ctl.key("escape", N, True)                                                 # nothing left to cancel


def test_backslash_focuses_selection():
    ctl, sc, _ = ctl_with_events()
    sc.selection = {"CC"}
    ctl.key("backslash", N, True)
    c = ctl.vp.to_screen(sc.get("cc").frame.center)
    assert (c.x, c.y) == pytest.approx((500, 300))


def test_selection_changed_emitted_once_per_change():
    ctl, sc, ev = ctl_with_events()
    ctl.press(Point(1, 1), 1, N, NodeHeader("aa")); ctl.release(Point(1, 1), N)
    ctl.press(Point(1, 1), 1, N, NodeHeader("aa")); ctl.release(Point(1, 1), N)
    assert len([e for e in ev if isinstance(e, SelectionChanged)]) == 1
