extends RefCounted

const TEXT := Color("#edf5f6")
const MUTED := Color("#b3c7cc")
const ACCENT := Color("#88deca")

static func surface(fill: Color, border: Color, radius: int, pad_x: int, pad_y: int) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = fill
	box.border_color = border
	box.set_border_width_all(1)
	box.set_corner_radius_all(radius)
	box.content_margin_left = pad_x
	box.content_margin_right = pad_x
	box.content_margin_top = pad_y
	box.content_margin_bottom = pad_y
	return box

static func make_theme() -> Theme:
	var result := Theme.new()
	result.default_font_size = 15
	result.set_color("font_color", "Label", TEXT)
	for control in ["Button", "OptionButton", "CheckButton", "CheckBox", "TabContainer"]:
		result.set_color("font_color", control, TEXT)
		result.set_color("font_hover_color", control, Color.WHITE)
		result.set_color("font_disabled_color", control, Color("#84959d"))
		var focus := surface(Color.TRANSPARENT, ACCENT, 5, 0, 0)
		focus.set_border_width_all(2)
		result.set_stylebox("focus", control, focus)
	for control in ["Button", "OptionButton"]:
		result.set_stylebox("normal", control, surface(Color("#243645"), Color("#456071"), 5, 12, 6))
		result.set_stylebox("hover", control, surface(Color("#304757"), Color("#81acb4"), 5, 12, 6))
		result.set_stylebox("pressed", control, surface(Color("#27534f"), ACCENT, 5, 12, 6))
		result.set_stylebox("disabled", control, surface(Color("#192833"), Color("#30414e"), 5, 12, 6))
	result.set_stylebox("panel", "TabContainer", surface(Color("#101a23"), Color("#344854"), 6, 1, 1))
	result.set_stylebox("tab_selected", "TabContainer", surface(Color("#1d383b"), ACCENT, 5, 21, 8))
	result.set_stylebox("tab_unselected", "TabContainer", surface(Color("#182632"), Color("#344854"), 5, 21, 8))
	result.set_stylebox("tab_hovered", "TabContainer", surface(Color("#263b47"), Color("#7996a1"), 5, 21, 8))
	result.set_color("font_selected_color", "TabContainer", ACCENT)
	result.set_color("font_unselected_color", "TabContainer", MUTED)
	return result
