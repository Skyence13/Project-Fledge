extends VBoxContainer

const Address = preload("res://service_address.gd")
const HistoryPlot = preload("res://lab_history.gd")
const MINT := Color("#95e5d5")
const GOLD := Color("#f4c17f")
const MUTED := Color("#b0c4cf")
const INK := Color("#142430")
const EDGE := Color("#314958")
const CONDITIONS := {
	"fixed": "Fixed",
	"rules_only": "Neuron rules",
	"wiring_only": "Wiring",
	"rules_and_wiring": "Rules + wiring",
}
const TINTS := {
	"fixed": Color("#a9bcd4"), "rules_only": Color("#91dfca"),
	"wiring_only": Color("#d2b9ef"), "rules_and_wiring": Color("#f4c17f"),
}

var http := HTTPRequest.new()
var poll := Timer.new()
var snapshot: Dictionary = {}
var connected_to_lab := false
var busy := false
var sending_command := false
var queued_commands: Array[Dictionary] = []
var syncing := false
var training_mode := OptionButton.new()
var pace_mode := OptionButton.new()
var run_button := Button.new()
var step_button := Button.new()
var left_button := Button.new()
var right_button := Button.new()
var next_button := Button.new()
var reset_button := Button.new()
var reset_dialog := ConfirmationDialog.new()
var auto_advance := CheckBox.new()
var cohort_tree := Tree.new()
var items: Dictionary = {}
var icons: Dictionary = {}
var rows_generation := -1
var headline := Label.new()
var lineage_label := Label.new()
var status := Label.new()
var error_text := Label.new()
var description := Label.new()
var stage_flow := HFlowContainer.new()
var stage_labels: Array[Label] = []
var progress_label := Label.new()
var progress := ProgressBar.new()
var cue_prompt := Label.new()
var metrics := Label.new()
var details := Label.new()
var selected_name := Label.new()
var history_caption := Label.new()
var history_plot := HistoryPlot.new()
var comparison := BoxContainer.new()

func _ready() -> void:
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(scroll)
	var outer := MarginContainer.new()
	outer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(outer)
	for side in ["left", "right"]:
		outer.add_theme_constant_override("margin_" + side, 18)
	for side in ["top", "bottom"]:
		outer.add_theme_constant_override("margin_" + side, 12)
	var main := VBoxContainer.new()
	main.add_theme_constant_override("separation", 9)
	outer.add_child(main)

	var header := HBoxContainer.new()
	main.add_child(header)
	_label(header, headline, "Evolution laboratory", 24)
	headline.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_label(header, lineage_label, "", 14, MUTED)
	lineage_label.hide()
	_label(header, status, "Connecting…", 15, MINT)
	status.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_label(main, error_text, "", 14, GOLD)
	error_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	error_text.hide()

	var tools := _flow(main)
	_label(tools, Label.new(), "Training", 14, MUTED)
	_options(tools, training_mode, ["Manual", "Automatic"])
	training_mode.item_selected.connect(func(i): _configure({"mode": "manual" if i == 0 else "auto"}))
	_label(tools, Label.new(), "Pace", 14, MUTED)
	_options(tools, pace_mode, ["Real time · 1×", "Accelerated · 16×"])
	pace_mode.tooltip_text = "Automatic training targets 1 or 16 cohort trials per second. Manual training advances when you press a trial button."
	pace_mode.item_selected.connect(func(i): _configure({"speed": "realtime" if i == 0 else "accelerated"}))
	_button(tools, run_button, "Start", func(): _configure({"running": not bool(snapshot.get("running", false))}))
	auto_advance.text = "Auto-advance generations"
	auto_advance.tooltip_text = "Breed the next generation automatically after all lessons. Switch off to inspect each final cohort."
	tools.add_child(auto_advance)
	auto_advance.toggled.connect(func(on): _configure({"auto_advance": on}))
	_button(tools, next_button, "Next generation", func(): _command({"action": "next_generation"}))

	var lesson := _card(main)
	_label(lesson, Label.new(), "Learning plan", 18)
	lesson.add_child(stage_flow)
	stage_flow.add_theme_constant_override("h_separation", 9)
	stage_flow.add_theme_constant_override("v_separation", 4)
	_label(lesson, description, "Waiting for the lesson plan.", 14, MUTED)
	description.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_label(lesson, progress_label, "", 14)
	progress.show_percentage = false
	progress.custom_minimum_size.y = 7
	progress.add_theme_stylebox_override("background", _box(Color("#344957"), Color.TRANSPARENT, 3))
	progress.add_theme_stylebox_override("fill", _box(Color("#71bcad"), Color.TRANSPARENT, 3))
	lesson.add_child(progress)
	var actions := _flow(lesson)
	_button(actions, step_button, "Step lesson", func(): _command({"action": "step"}))
	_button(actions, left_button, "Teach left", func(): _command({"action": "step", "target": 0}))
	_button(actions, right_button, "Teach right", func(): _command({"action": "step", "target": 1}))
	_label(actions, cue_prompt, "", 14, MINT)
	cue_prompt.autowrap_mode = TextServer.AUTOWRAP_OFF

	_label(main, metrics, "Waiting for generation statistics.", 15, MUTED)
	metrics.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	main.add_child(comparison)
	comparison.add_theme_constant_override("separation", 9)
	var population := _card(comparison)
	population.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	population.get_parent().size_flags_stretch_ratio = 1.65
	_label(population, Label.new(), "Current generation", 18)
	_setup_tree(population)
	var inspector := _card(comparison)
	inspector.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_label(inspector, selected_name, "Selected Wisp", 18, MINT)
	_label(inspector, details, "Select a Wisp to inspect its network and choices.", 14)
	details.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	inspector.add_child(HSeparator.new())
	_label(inspector, Label.new(), "Generation history", 16)
	_label(inspector, history_caption, "Finish a generation to see its results.", 13, MUTED)
	history_caption.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	inspector.add_child(history_plot)
	history_plot.custom_minimum_size = Vector2(0, 49)
	var footer := _flow(main)
	_button(footer, reset_button, "New founder population", reset_dialog.popup_centered)
	reset_dialog.title = "Start a new founder population?"
	reset_dialog.dialog_text = "A new independent lineage will start from the original seed. You can reopen this population in Lineages."
	reset_dialog.ok_button_text = "Start new lineage"
	reset_dialog.confirmed.connect(func(): _command({"action": "reset"}))
	add_child(reset_dialog)
	resized.connect(_resize_layout)
	_resize_layout()
	add_child(http)
	http.timeout = 3.0
	http.request_completed.connect(_received)
	add_child(poll)
	poll.wait_time = 0.3
	poll.timeout.connect(_refresh)
	poll.start()
	visibility_changed.connect(_refresh)
	_update_controls()
	_refresh()

func _resize_layout() -> void:
	comparison.vertical = size.x < 715

func _box(fill: Color, border: Color, radius: int) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = fill
	box.border_color = border
	box.set_border_width_all(1)
	box.set_corner_radius_all(radius)
	return box

func _card(parent: Container) -> VBoxContainer:
	var frame := PanelContainer.new()
	var box := _box(INK, EDGE, 8)
	box.content_margin_left = 12
	box.content_margin_right = 12
	box.content_margin_top = 8
	box.content_margin_bottom = 8
	frame.add_theme_stylebox_override("panel", box)
	frame.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	parent.add_child(frame)
	var column := VBoxContainer.new()
	column.add_theme_constant_override("separation", 4)
	frame.add_child(column)
	return column

func _label(parent: Control, label: Label, value: String, font_size: int, color := Color.WHITE) -> void:
	label.text = value
	label.add_theme_font_size_override("font_size", font_size)
	label.add_theme_color_override("font_color", color)
	parent.add_child(label)

func _flow(parent: Control) -> HFlowContainer:
	var flow := HFlowContainer.new()
	flow.add_theme_constant_override("h_separation", 7)
	flow.add_theme_constant_override("v_separation", 4)
	parent.add_child(flow)
	return flow

func _options(parent: Control, menu: OptionButton, labels: Array) -> void:
	for text in labels:
		menu.add_item(text)
	menu.add_theme_font_size_override("font_size", 14)
	parent.add_child(menu)

func _button(parent: Control, button: Button, caption: String, callback: Callable) -> void:
	button.text = caption
	button.add_theme_font_size_override("font_size", 14)
	button.pressed.connect(callback)
	parent.add_child(button)

func _setup_tree(parent: Control) -> void:
	cohort_tree.hide_root = true
	cohort_tree.columns = 4
	cohort_tree.column_titles_visible = true
	cohort_tree.select_mode = Tree.SELECT_ROW
	cohort_tree.allow_reselect = true
	cohort_tree.custom_minimum_size = Vector2(0, 213)
	cohort_tree.add_theme_font_size_override("font_size", 14)
	cohort_tree.add_theme_font_size_override("title_button_font_size", 13)
	var titles := ["Wisp", "Evolves", "Last gaze", "Fitness"]
	var widths := [74, 97, 65, 53]
	for i in titles.size():
		cohort_tree.set_column_title(i, titles[i])
		cohort_tree.set_column_title_alignment(i, HORIZONTAL_ALIGNMENT_LEFT)
		cohort_tree.set_column_custom_minimum_width(i, widths[i])
		cohort_tree.set_column_expand(i, true)
	cohort_tree.item_selected.connect(_inspect_selection)
	parent.add_child(cohort_tree)

func _refresh() -> void:
	if is_visible_in_tree() and is_node_ready() and not busy:
		_request({})

func _command(data: Dictionary) -> void:
	if syncing or not connected_to_lab:
		return
	if busy:
		queued_commands.append(data)
		_update_controls()
	else:
		_request(data)

func _configure(fields: Dictionary) -> void:
	var data := fields.duplicate()
	data["action"] = "configure"
	_command(data)

func _request(data: Dictionary) -> void:
	busy = true
	sending_command = not data.is_empty()
	var route := "/lab/control" if sending_command else "/lab/state"
	var method := HTTPClient.METHOD_POST if sending_command else HTTPClient.METHOD_GET
	var body := JSON.stringify(data) if sending_command else ""
	if sending_command:
		_update_controls()
	var result := http.request(Address.base_url() + route, ["Content-Type: application/json"], method, body)
	if result != OK:
		busy = false
		sending_command = false
		_error("Could not start the request. Retrying the connection.", true)

func _error(message: String, disconnected: bool) -> void:
	error_text.text = message
	error_text.show()
	if disconnected:
		connected_to_lab = false
		status.text = "Disconnected"
		queued_commands.clear()
	_update_controls()

func _received(result: int, code: int, _headers: PackedStringArray, bytes: PackedByteArray) -> void:
	var was_command := sending_command
	busy = false
	sending_command = false
	var payload = JSON.parse_string(bytes.get_string_from_utf8()) if not bytes.is_empty() else null
	if result != HTTPRequest.RESULT_SUCCESS or typeof(payload) != TYPE_DICTIONARY:
		_error("Cannot reach the local laboratory. Start the Fledge launcher to reconnect.", true)
	elif code < 200 or code >= 300:
		_error(str(payload.get("error", "The laboratory could not process this action.")), not was_command or code >= 500)
	elif not payload.has("variants") or not payload.has("plan"):
		_error("This service does not provide the laboratory interface.", true)
	else:
		if not connected_to_lab or was_command:
			error_text.hide()
		connected_to_lab = true
		snapshot = payload
		_render()
		if snapshot.get("archive_error") != null and not str(snapshot.archive_error).is_empty():
			error_text.text = str(snapshot.archive_error)
			error_text.show()
	if not queued_commands.is_empty() and connected_to_lab:
		_request(queued_commands.pop_front())

func _update_controls() -> void:
	var locked := not connected_to_lab or sending_command or not queued_commands.is_empty()
	var manual: bool = snapshot.get("mode", "manual") == "manual"
	var waiting: bool = snapshot.get("awaiting_next", false)
	var teaching: bool = snapshot.get("teaching", false)
	training_mode.disabled = locked
	pace_mode.disabled = locked
	auto_advance.disabled = locked
	run_button.disabled = locked or manual or (waiting and not auto_advance.button_pressed and not bool(snapshot.get("running", false)))
	run_button.text = "Pause" if bool(snapshot.get("running", false)) else "Start"
	step_button.disabled = locked or not manual or waiting
	left_button.disabled = step_button.disabled or not teaching
	right_button.disabled = left_button.disabled
	next_button.disabled = locked or not waiting
	reset_button.disabled = locked

func _percent(value) -> String:
	return "—" if value == null else "%.1f%%" % (100.0 * float(value))

func _gaze(value) -> String:
	if value == null:
		return "—"
	return "Left" if int(value) == 0 else "Right"

func _cue(value) -> String:
	if value == null:
		return "—"
	return "ring" if int(value) == 0 else "diamond"

func _render() -> void:
	syncing = true
	training_mode.select(0 if snapshot.mode == "manual" else 1)
	pace_mode.select(0 if snapshot.speed == "realtime" else 1)
	auto_advance.set_pressed_no_signal(bool(snapshot.get("auto_advance", false)))
	headline.text = "Evolution laboratory  ·  Generation %d" % int(snapshot.generation)
	var lineage := str(snapshot.get("active_branch_name", ""))
	lineage_label.visible = not lineage.is_empty()
	lineage_label.text = "Lineage: " + lineage.left(24) + ("…" if lineage.length() > 24 else "")
	lineage_label.tooltip_text = lineage
	if snapshot.awaiting_next and not snapshot.running:
		status.text = "Generation complete"
	elif snapshot.running:
		status.text = "Running · continuous" if snapshot.auto_advance else "Running to generation end"
	else:
		status.text = "Manual stepping" if snapshot.mode == "manual" else "Paused"
	_render_plan()
	var stats: Dictionary = snapshot.stats
	metrics.text = "%d Wisps  ·  %d distinct genomes  ·  Mean fitness %s  ·  Best %s  ·  %d total choices" % [
		int(snapshot.population), int(stats.unique_genomes), _percent(stats.mean_fitness),
		_percent(stats.best_fitness), int(stats.total_choices)]
	metrics.tooltip_text = "Fitness is the fraction of correct choices during scored recall probes. Wisps share some founder genomes across experimental conditions."
	_render_variants()
	_render_history()
	syncing = false
	_update_controls()

func _render_plan() -> void:
	var stages: Array = snapshot.plan
	if stage_labels.size() != stages.size():
		for label in stage_labels:
			label.queue_free()
		stage_labels.clear()
		for stage in stages:
			var label := Label.new()
			_label(stage_flow, label, str(stage.label), 14, MUTED)
			stage_labels.append(label)
	var active := int(snapshot.phase_index)
	for i in stages.size():
		var label := stage_labels[i]
		var stage: Dictionary = stages[i]
		label.text = "› " + str(stage.label) if i == active else str(stage.label)
		label.add_theme_color_override("font_color", MINT if i == active else MUTED)
		label.tooltip_text = "%d trials. %s" % [int(stage.trials), str(stage.description)]
	if not stages.is_empty():
		description.text = str(stages[active].description)
	progress_label.text = "Lifetime %d of %d    ·    Stage trials %d / %d    ·    Generation trials %d / %d" % [
		int(snapshot.lifetime), int(snapshot.lifetimes_per_generation), int(snapshot.phase_step),
		int(snapshot.phase_total), int(snapshot.step), int(snapshot.steps_per_generation)]
	progress.max_value = float(snapshot.steps_per_generation)
	progress.value = float(snapshot.step)
	if snapshot.awaiting_next:
		cue_prompt.text = "All lessons complete."
	elif snapshot.teaching:
		cue_prompt.text = "Next: %s · planned gaze %s" % [_cue(snapshot.next_cue), _gaze(snapshot.next_target).to_lower()]
	else:
		cue_prompt.text = "Next: %s · observe without feedback" % _cue(snapshot.next_cue)
	step_button.tooltip_text = "Present the next scheduled cue to every Wisp and follow the learning plan."
	left_button.tooltip_text = "Present the next cue and give each Wisp feedback for a left target."
	right_button.tooltip_text = "Present the next cue and give each Wisp feedback for a right target."

func _render_variants() -> void:
	var members: Array = snapshot.variants
	if rows_generation != int(snapshot.generation) or items.size() != members.size():
		cohort_tree.clear()
		items.clear()
		var root := cohort_tree.create_item()
		# Matched slots from all four conditions appear together for comparison.
		var display_members := members.duplicate()
		display_members.sort_custom(func(a, b): return int(a.slot) < int(b.slot) if a.slot != b.slot else str(a.condition) < str(b.condition))
		for v in display_members:
			var row := cohort_tree.create_item(root)
			row.set_metadata(0, str(v.id))
			items[str(v.id)] = row
		rows_generation = int(snapshot.generation)
	for v in members:
		var row: TreeItem = items[str(v.id)]
		var family := str(v.condition)
		row.set_text(0, " %02d" % (int(v.slot) + 1))
		row.set_icon(0, _wisp_icon(family, v.last_action))
		row.set_text(1, str(CONDITIONS.get(family, family)))
		row.set_text(2, _gaze(v.last_action))
		row.set_text(3, _percent(v.fitness))
		row.set_tooltip_text(0, "%s · genome %s" % [str(v.label), str(v.genome).left(10)])
		row.set_tooltip_text(1, "Inherited neuron dynamics and/or wiring that may vary in this condition.")
		row.set_tooltip_text(2, "Last response to the %s cue." % _cue(v.last_cue))
		row.set_tooltip_text(3, "%d correct / %d scored recalls" % [int(v.correct), int(v.probes)])
		if str(v.id) == str(snapshot.selected):
			if cohort_tree.get_selected() != row:
				row.select(0)
			selected_name.text = "%s  ·  Wisp %02d" % [str(CONDITIONS.get(family, family)), int(v.slot) + 1]
			var rank_text := "—" if v.rank == null else str(int(v.rank))
			details.text = "Rank %s    ·    Fitness %s\n%d correct / %d scored probes\n%d choices    ·    %d feedback events\n%d neurons (%d adaptive)    ·    %d synapses\nLearning rate %.3f    ·    Genome %s" % [
				rank_text, _percent(v.fitness), int(v.correct), int(v.probes), int(v.choices),
				int(v.feedback), int(v.nodes), int(v.adaptive), int(v.edges), float(v.learning_rate), str(v.genome).left(8)]

func _wisp_icon(condition: String, gaze) -> Texture2D:
	var direction := 0 if gaze == null else (-2 if int(gaze) == 0 else 2)
	var key := "%s/%d" % [condition, direction]
	if not icons.has(key):
		var img := Image.create_empty(30, 24, false, Image.FORMAT_RGBA8)
		var color: Color = TINTS.get(condition, MINT)
		_disc(img, Vector2i(15, 12), 9, color)
		_disc(img, Vector2i(8, 19), 3, color)
		_disc(img, Vector2i(22, 19), 3, color)
		for x in [11, 19]:
			_disc(img, Vector2i(x, 11), 3, Color.WHITE)
			_disc(img, Vector2i(x + direction, 11), 1, INK)
		icons[key] = ImageTexture.create_from_image(img)
	return icons[key]

func _disc(img: Image, center: Vector2i, radius: int, color: Color) -> void:
	for y in range(-radius, radius + 1):
		for x in range(-radius, radius + 1):
			if x * x + y * y <= radius * radius:
				img.set_pixel(center.x + x, center.y + y, color)

func _inspect_selection() -> void:
	var row := cohort_tree.get_selected()
	if row != null and not syncing and str(row.get_metadata(0)) != str(snapshot.get("selected", "")):
		_command({"action": "select", "id": str(row.get_metadata(0))})

func _render_history() -> void:
	var history: Array = snapshot.history
	history_plot.visible = not history.is_empty()
	if history.is_empty():
		history_caption.text = "Finish a generation to see its fitness history."
		history_caption.tooltip_text = ""
	else:
		var last: Dictionary = history.back()
		history_caption.text = "Generations %d–%d  ·  0–100%%\nMean %s (mint)  ·  Best %s (amber)" % [
			int(history.front().generation), int(last.generation), _percent(last.mean_fitness), _percent(last.best_fitness)]
		var summaries := PackedStringArray(["Generation %d mean fitness by condition" % int(last.generation)])
		for key in CONDITIONS:
			summaries.append("%s: %s" % [CONDITIONS[key], _percent(last.get("by_condition", {}).get(key))])
		history_caption.tooltip_text = "\n".join(summaries)
	history_plot.tooltip_text = history_caption.tooltip_text
	history_plot.set_records(history)
