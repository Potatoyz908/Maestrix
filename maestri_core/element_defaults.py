"""Apply saved creation defaults without changing restored or duplicated nodes."""
import math

TERMINAL_FIELDS = ('theme_id', 'font_family', 'font_size', 'icon', 'color')
NOTE_FIELDS = ('color', 'font_size')


def apply_element_defaults(node, preferences, *, size=True, appearance=True):
    defaults = getattr(preferences, '_extra', {}).get('newElementDefaults', {})
    values = defaults.get(node.node_type, {}) if isinstance(defaults, dict) else {}
    if not isinstance(values, dict):
        return
    dims = values.get('size')
    if size and isinstance(dims, list) and len(dims) == 2:
        try:
            w, h = map(float, dims)
            if math.isfinite(w) and math.isfinite(h) and w > 0 and h > 0:
                node.frame.width, node.frame.height = w, h
        except (TypeError, ValueError):
            pass
    look = values.get('appearance')
    if appearance and isinstance(look, dict):
        fields = TERMINAL_FIELDS if node.node_type == 'terminal' else NOTE_FIELDS if node.node_type == 'stickyNote' else ()
        for key in fields:
            if key in look:
                setattr(node.content, key, look[key])
