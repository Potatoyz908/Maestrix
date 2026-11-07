"""Ported from Sources/Shared/Constants.swift and CanvasNodesView.swift (keep in sync)."""

CANVAS_INITIAL_ORIGIN = (9800.0, 8500.0)
CANVAS_MIN_ZOOM = 0.1
CANVAS_MAX_ZOOM = 3.0
CANVAS_ZOOM_STEP = 0.25
CANVAS_GRID_SPACING = 16.0

MIN_NODE_WIDTH = 160.0
MIN_NODE_HEIGHT = 80.0

ROPE_CONTROL_POINT_COUNT = 21
ROPE_BEND_RATIO_MIN = 1.08
ROPE_BEND_RATIO_MAX = 1.15

SNAP_THRESHOLD = 12.0
SNAP_PROXIMITY_RADIUS = 600.0

# Viewport culling margin (canvas units): nodes this far outside the view still render, so they don't pop in.
CULL_MARGIN = 200.0

# Pointer must travel this far (screen px) before a press on a node becomes a drag.
DRAG_THRESHOLD_PX = 3.0
