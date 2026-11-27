extends SceneTree
## Standalone 1D camera check. From the repository root run:
## godot --path sim --script res://raster_pan_check.gd --audio-driver Dummy
## Use a real renderer. The headless display driver returns no raster.
## Angles span -30 to 30 at four pixels/degree. Rounding to the nearest column
## introduces at most 0.125 degrees of error. Every visible frame is checked
## against the five triangular receptor fields and their left/right projection.

const PIXELS_PER_DEGREE := 4.0
const CENTER_COLUMN := 120
const RECEPTORS := [-30.0, -15.0, 0.0, 15.0, 30.0]
const PIXEL_TOLERANCE_DEG := 0.13

var sensor := SubViewport.new()
var scene := Node2D.new()
var target := ColorRect.new()
var checked := 0
var failures := 0
var max_error := 0.0


func _initialize() -> void:
	call_deferred("run_check")


func run_check() -> void:
	sensor.size = Vector2i(241, 9)
	sensor.transparent_bg = true
	sensor.world_2d = World2D.new()
	sensor.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	root.add_child(sensor)
	sensor.add_child(scene)
	scene.add_child(target)
	target.size = Vector2(1, 3)
	target.color = Color.WHITE
	for speed in [-1, 0, 1]:
		for command in [-2, -1, 1, 2]:
			for gain in [2, 4, 6]:
				var start := 3.37 if command < 0 else -4.17
				for tick in range(4):
					var world_bearing: float = start + tick * speed
					var camera_pan: float = command * gain if tick >= 2 else 0
					target.position = Vector2(CENTER_COLUMN + round(world_bearing * PIXELS_PER_DEGREE), 3)
					scene.position.x = -camera_pan * PIXELS_PER_DEGREE
					target.visible = tick != 2
					await RenderingServer.frame_post_draw
					var raster := sensor.get_texture().get_image()
					if raster == null or raster.is_empty():
						printerr("Godot renderer returned no image")
						quit(2)
						return
					check_frame(raster, world_bearing - camera_pan, tick == 2)
	print("raster-pan frames=%d failures=%d max_bearing_error_deg=%.4f" % [checked, failures, max_error])
	quit(1 if failures else 0)


func check_frame(raster: Image, expected: float, shuttered: bool) -> void:
	checked += 1
	var energy := 0.0
	var levels := PackedFloat64Array([0, 0, 0, 0, 0])
	for x in range(raster.get_width()):
		var column_light := 0.0
		for y in range(raster.get_height()):
			var pixel := raster.get_pixel(x, y)
			column_light += pixel.get_luminance() * pixel.a
		if column_light == 0.0:
			continue
		energy += column_light
		var angle := (x - CENTER_COLUMN) / PIXELS_PER_DEGREE
		for i in range(RECEPTORS.size()):
			levels[i] += column_light * maxf(0.0, 1.0 - absf(angle - RECEPTORS[i]) / 15.0)
	if shuttered:
		if energy != 0.0:
			failures += 1
		return
	if energy <= 0:
		failures += 1
		return
	var left := 0.0
	var right := 0.0
	for i in range(RECEPTORS.size()):
		levels[i] /= energy
		var ideal := maxf(0.0, 1.0 - absf(expected - RECEPTORS[i]) / 15.0)
		if absf(ideal - levels[i]) > PIXEL_TOLERANCE_DEG / 15.0:
			failures += 1
		left += levels[i] * (30.0 - float(RECEPTORS[i])) / 60.0
		right += levels[i] * (30.0 + float(RECEPTORS[i])) / 60.0
	var observed := (right - left) * 30.0
	var error := absf(observed - expected)
	max_error = maxf(error, max_error)
	if error > PIXEL_TOLERANCE_DEG:
		failures += 1
