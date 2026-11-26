extends Control

const MEAN := Color("#80decf")
const BEST := Color("#f4c17f")
var records: Array = []

func set_records(data: Array) -> void:
	records = data
	queue_redraw()

func _draw() -> void:
	if records.is_empty() or size.x < 24:
		return
	var plot := Rect2(Vector2(8, 7), Vector2(size.x - 16, size.y - 14))
	for fraction in [0.0, 0.5, 1.0]:
		var y := plot.position.y + float(fraction) * plot.size.y
		draw_line(Vector2(plot.position.x, y), Vector2(plot.end.x, y), Color("#30434f"))
	for key in ["best_fitness", "mean_fitness"]:
		var color := BEST if key == "best_fitness" else MEAN
		var points := PackedVector2Array()
		for i in range(records.size()):
			var value = records[i].get(key)
			if value == null:
				continue
			var x := plot.position.x + plot.size.x * float(i) / maxf(1, records.size() - 1)
			var y := plot.end.y - plot.size.y * clampf(float(value), 0.0, 1.0)
			points.append(Vector2(x, y))
		if points.size() > 1:
			draw_polyline(points, color, 2.0, true)
		if not points.is_empty():
			draw_circle(points[-1], 3.0, color, true, -1, true)
