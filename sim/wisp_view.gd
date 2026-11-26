extends Control

const AQUA := Color("#9ce8dd")
const AMBER := Color("#ffc778")
const INK := Color("#172b37")
const PALE := Color("#c7d6dc")

var shown_cue := -1
var shown_action := -1

func show_choice(cue: int, action: int) -> void:
	shown_cue = cue
	shown_action = action
	queue_redraw()

func _draw() -> void:
	var middle := size.x * 0.5
	var floor_y := size.y * 0.73
	var body := Vector2(middle, floor_y - 47)
	draw_rect(Rect2(Vector2.ZERO, size), Color("#14232f"))
	draw_line(Vector2(18, floor_y), Vector2(size.x - 18, floor_y), Color("#34505f"), 2)
	var symbol := "Cue: none"
	if shown_cue == 0:
		symbol = "Cue: ring"
	elif shown_cue == 1:
		symbol = "Cue: diamond"
	draw_string(ThemeDB.fallback_font, Vector2(20, 27), symbol, HORIZONTAL_ALIGNMENT_LEFT, -1, 15, PALE)
	# The cue indicators are overhead and centered, independent of the two gaze targets.
	for i in range(2):
		var at := Vector2(middle + (i - 0.5) * 42, 33)
		var color := AMBER if shown_cue == i else Color("#536675")
		if i == 0:
			draw_arc(at, 9, 0, TAU, 36, color, 3, true)
		else:
			var diamond := PackedVector2Array([at + Vector2(0, -10), at + Vector2(10, 0), at + Vector2(0, 10), at + Vector2(-10, 0), at + Vector2(0, -10)])
			draw_polyline(diamond, color, 3, true)
	for i in range(2):
		var x := size.x * (0.21 if i == 0 else 0.79)
		var selected := shown_action == i
		var target_color := AQUA if selected else Color("#637782")
		draw_circle(Vector2(x, floor_y - 10), 12, target_color, true, -1, true)
		var label_text := "Left" if i == 0 else "Right"
		draw_string(ThemeDB.fallback_font, Vector2(x - 45, floor_y + 24), label_text, HORIZONTAL_ALIGNMENT_CENTER, 90, 15, PALE)
	draw_set_transform(body, 0, Vector2(1, 0.23))
	draw_circle(Vector2(0, 187), 43, Color("#0b1821"), true, -1, true)
	draw_set_transform(Vector2.ZERO, 0)
	var direction := 0.0 if shown_action < 0 else (-1.0 if shown_action == 0 else 1.0)
	draw_set_transform(body, direction * 0.12)
	draw_circle(Vector2.ZERO, 42, AQUA, true, -1, true)
	draw_circle(Vector2(-28, 28), 11, AQUA, true, -1, true)
	draw_circle(Vector2(28, 28), 11, AQUA, true, -1, true)
	for x in [-14, 14]:
		draw_circle(Vector2(x, -3), 8, Color.WHITE, true, -1, true)
		draw_circle(Vector2(float(x) + direction * 3.5, -3), 4.5, INK, true, -1, true)
	draw_arc(Vector2(0, 8), 5, 0.25, PI - 0.25, 12, INK, 2, true)
	draw_set_transform(Vector2.ZERO, 0)
