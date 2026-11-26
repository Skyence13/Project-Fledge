extends ScrollContainer

const Address = preload("res://service_address.gd")
const Kit = preload("res://lab_theme.gd")
const GROUPS := ["neural", "no_plasticity", "uninformative_feedback", "association", "reflex"]
const GROUP_NAMES := ["Wisp", "Plasticity off", "Random feedback", "Association", "Fixed reflex"]
const AMBER := Color("#f4ca8e")

var http := HTTPRequest.new()
var poll := Timer.new()
var snapshot: Dictionary = {}
var report: Dictionary = {}
var busy := false
var reading_start := false
var online := false
var report_key := ""
var start_button := Button.new()
var refresh_button := Button.new()
var status_label := Label.new()
var source_label := Label.new()
var error_label := Label.new()
var progress := ProgressBar.new()
var plan_cards := BoxContainer.new()
var result_box := VBoxContainer.new()
var result_note := Label.new()
var result_table := Tree.new()
var narrow_results := Label.new()
var result_path := LineEdit.new()
var file_row := VBoxContainer.new()
var copy_button := Button.new()

func _ready() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	var inset := MarginContainer.new()
	inset.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	for side in ["top", "left", "right", "bottom"]:
		inset.add_theme_constant_override("margin_" + side, 17)
	add_child(inset)
	var page := VBoxContainer.new()
	page.add_theme_constant_override("separation", 11)
	inset.add_child(page)
	_label(page, Label.new(), "Memory experiments", 25)
	_label(page, Label.new(), "Evaluate the latest completed population on two new lessons. Each genome starts a fresh Wisp for each test. The active evolutionary run can continue.", 15, Kit.MUTED)
	plan_cards.add_theme_constant_override("separation", 11)
	page.add_child(plan_cards)
	var delayed := _card(plan_cards)
	_label(delayed, Label.new(), "Delayed feedback", 18, Kit.ACCENT)
	_label(delayed, Label.new(), "Teach two cues, then delay approval or correction by 0, 1, or 3 intervening choices. Test recall with feedback removed.", 15)
	var retention := _card(plan_cards)
	_label(retention, Label.new(), "Retention across lessons", 18, Kit.ACCENT)
	_label(retention, Label.new(), "Teach A, teach B, then probe A again. A matched control sees B without feedback. A separate probe checks whether B was learned.", 15)
	var controls := _card(page)
	var bar := HFlowContainer.new()
	bar.add_theme_constant_override("h_separation", 9)
	controls.add_child(bar)
	start_button.text = "Evaluate latest population"
	start_button.pressed.connect(_start)
	bar.add_child(start_button)
	refresh_button.text = "Refresh"
	refresh_button.pressed.connect(_refresh)
	bar.add_child(refresh_button)
	_label(controls, status_label, "Connecting to the evaluation service…", 16, Kit.ACCENT)
	_label(controls, source_label, "", 14, Kit.MUTED)
	progress.show_percentage = false
	progress.custom_minimum_size.y = 7
	progress.max_value = 1
	controls.add_child(progress)
	progress.hide()
	_label(controls, error_label, "", 14, AMBER)
	error_label.hide()
	page.add_child(result_box)
	result_box.add_theme_constant_override("separation", 7)
	_label(result_box, Label.new(), "Held-out responses", 19)
	_label(result_box, result_note, "Complete an evaluation to compare the population, controls, and baselines.", 14, Kit.MUTED)
	_setup_results()
	_label(result_box, narrow_results, "", 14)
	result_box.add_child(file_row)
	var file_head := HFlowContainer.new()
	file_row.add_child(file_head)
	var file_label := Label.new()
	_label(file_head, file_label, "Saved JSON report", 14, Kit.MUTED)
	file_label.autowrap_mode = TextServer.AUTOWRAP_OFF
	copy_button.text = "Copy path"
	copy_button.pressed.connect(func(): DisplayServer.clipboard_set(result_path.text))
	copy_button.visible = DisplayServer.has_feature(DisplayServer.FEATURE_CLIPBOARD)
	file_head.add_child(copy_button)
	result_path.editable = false
	result_path.select_all_on_focus = true
	result_path.add_theme_color_override("font_uneditable_color", Kit.TEXT)
	file_row.add_child(result_path)
	file_row.hide()
	add_child(http)
	http.timeout = 12.0
	http.request_completed.connect(_received)
	add_child(poll)
	poll.wait_time = 1.0
	poll.timeout.connect(_refresh)
	poll.start()
	visibility_changed.connect(_refresh)
	resized.connect(_layout)
	_layout()
	_update_controls()
	call_deferred("_refresh")

func _card(parent: Control) -> VBoxContainer:
	var frame := PanelContainer.new()
	frame.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	frame.add_theme_stylebox_override("panel", Kit.surface(Color("#182632"), Color("#344854"), 7, 13, 10))
	parent.add_child(frame)
	var body := VBoxContainer.new()
	body.add_theme_constant_override("separation", 6)
	frame.add_child(body)
	return body

func _label(parent: Control, label: Label, value: String, points: int, color := Color("#edf5f6")) -> void:
	label.text = value
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.add_theme_font_size_override("font_size", points)
	label.add_theme_color_override("font_color", color)
	parent.add_child(label)

func _setup_results() -> void:
	result_table.hide_root = true
	result_table.columns = 1 + GROUPS.size()
	result_table.column_titles_visible = true
	result_table.set_column_title(0, "Measure")
	result_table.set_column_custom_minimum_width(0, 146)
	for i in GROUP_NAMES.size():
		result_table.set_column_title(i + 1, GROUP_NAMES[i])
		result_table.set_column_custom_minimum_width(i + 1, 127 if i < 3 else 115)
	for i in result_table.columns:
		result_table.set_column_title_alignment(i, HORIZONTAL_ALIGNMENT_LEFT)
		result_table.set_column_expand(i, true)
	result_table.add_theme_font_size_override("font_size", 14)
	result_table.add_theme_font_size_override("title_button_font_size", 14)
	result_table.custom_minimum_size.y = 195
	result_box.add_child(result_table)

func _layout() -> void:
	plan_cards.vertical = size.x < 740
	result_table.visible = not report.is_empty() and size.x >= 945
	narrow_results.visible = not report.is_empty() and size.x < 945

func _refresh() -> void:
	if is_visible_in_tree() and is_node_ready() and not busy:
		_request(false)

func _start() -> void:
	if not busy and online and snapshot.get("eligible", false) and snapshot.get("status") != "running":
		error_label.hide()
		_request(true)

func _request(start: bool) -> void:
	busy = true
	reading_start = start
	var path := "/lab/next-plan/start" if start else "/lab/next-plan"
	var method := HTTPClient.METHOD_POST if start else HTTPClient.METHOD_GET
	var err := http.request(Address.base_url() + path, ["Content-Type: application/json"], method, "{}" if start else "")
	if err != OK:
		_failed("Unable to connect to the local evaluation service.", true)
	_update_controls()

func _failed(text: String, disconnected: bool) -> void:
	busy = false
	error_label.text = text
	error_label.show()
	if disconnected:
		online = false
		status_label.text = "Service offline · retrying"
		poll.wait_time = 2.0
	_update_controls()

func _received(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var was_start := reading_start
	busy = false
	reading_start = false
	var data = JSON.parse_string(body.get_string_from_utf8()) if body.size() > 0 else null
	if result != HTTPRequest.RESULT_SUCCESS or typeof(data) != TYPE_DICTIONARY:
		_failed("Could not read memory experiments. The next refresh will retry.", true)
		return
	if code < 200 or code >= 300 or not data.has("status"):
		_failed(str(data.get("error", "The service returned an incomplete evaluation state.")), not was_start)
		return
	error_label.hide()
	online = true
	snapshot = data
	_render()
	_update_controls()

func _update_controls() -> void:
	var is_running: bool = snapshot.get("status") == "running"
	start_button.disabled = busy or not online or is_running or not snapshot.get("eligible", false)
	refresh_button.disabled = busy

func _render() -> void:
	var state := str(snapshot.status)
	var candidate := int(snapshot.get("latest_completed_generation", 0))
	var lineage := str(snapshot.get("active_branch_name", ""))
	var can_start: bool = snapshot.get("eligible", false)
	var source = snapshot.get("source")
	var location := ""
	if typeof(source) == TYPE_DICTIONARY:
		location = "%s · Generation %d" % [str(source.get("branch_name", "")), int(source.get("generation", 0))]
	if can_start:
		source_label.text = "Latest eligible population: %s · Generation %d" % [lineage, candidate]
	else:
		source_label.text = "Complete a generation in the evolution lab to unlock this evaluation."
	progress.visible = state == "running"
	poll.wait_time = 0.5 if progress.visible else 3.0
	match state:
		"running":
			progress.value = clampf(float(snapshot.get("progress", 0.0)), 0, 1)
			status_label.text = "Evaluating %s  ·  %d%%" % [location, roundi(progress.value * 100.0)]
		"complete":
			status_label.text = "Evaluation complete  ·  %s" % location
		"error":
			status_label.text = "Evaluation interrupted  ·  %s" % location
			error_label.text = str(snapshot.get("error", "The evaluation could not finish."))
			error_label.show()
		_:
			status_label.text = "Ready to evaluate" if can_start else "Waiting for a completed population"
	var next_report = snapshot.get("report")
	if state == "complete" and typeof(next_report) == TYPE_DICTIONARY:
		var path := "" if snapshot.get("path") == null else str(snapshot.path)
		var key := "%s/%s/%s" % [location, path, next_report.get("protocol", "")]
		if report.is_empty() or key != report_key:
			report = next_report
			report_key = key
			_render_results()
		result_path.text = path
		result_path.tooltip_text = path
		file_row.visible = not path.is_empty()
	elif not report.is_empty():
		report = {}
		report_key = ""
		result_table.clear()
		file_row.hide()
		narrow_results.text = ""
	if state != "complete":
		result_note.text = "Scores will appear when this evaluation finishes." if state == "running" else "Complete an evaluation to compare the population, controls, and baselines."
	_layout()

func _score_text(raw) -> String:
	if typeof(raw) != TYPE_DICTIONARY:
		return "—"
	var total := int(raw.get("total", 0))
	if total == 0:
		return "—"
	var correct := int(raw.get("correct", 0))
	return "%d/%d · %.1f%%" % [correct, total, 100.0 * correct / total]

func _render_results() -> void:
	result_table.clear()
	var tree_root := result_table.create_item()
	var small := PackedStringArray()
	var results: Dictionary = report.get("summary", {})
	for metric in report.get("metrics", []):
		var id := str(metric.id)
		var text := str(metric.label)
		var row := result_table.create_item(tree_root)
		row.set_text(0, _short_label(id, text))
		row.set_autowrap_mode(0, TextServer.AUTOWRAP_WORD_SMART)
		row.set_metadata(0, id)
		row.set_tooltip_text(0, text)
		var group_scores := PackedStringArray()
		for i in GROUPS.size():
			var score := _score_text(results.get(GROUPS[i], {}).get(id))
			row.set_text(i + 1, score)
			row.set_autowrap_mode(i + 1, TextServer.AUTOWRAP_WORD_SMART)
			row.set_tooltip_text(i + 1, "%s · %s · %s" % [GROUP_NAMES[i], text, score])
			group_scores.append("%s %s" % [GROUP_NAMES[i], score])
		small.append("%s\n%s" % [text, "    ·    ".join(group_scores)])
	narrow_results.text = "\n\n".join(small)
	result_note.text = "Correct / tested responses. Wisp and control counts pool %d candidates. Baselines use one shared lesson schedule. The association learner knows the feedback delay." % report.get("variants", []).size()
	result_table.custom_minimum_size.y = minf(375, 43 + 31 * report.get("metrics", []).size())

func _short_label(id: String, fallback: String) -> String:
	if id.begins_with("delay_"):
		var parts := id.split("_", false, 2)
		var name: String = {"first": "first recall", "recall": "4 recalls", "opposite_recall": "opposite targets"}.get(parts[2], fallback)
		return "Delay %s · %s" % [parts[1], name]
	if id == "retain_before_first":
		return "A taught · first A recall"
	var arms := {"retain_sham_": "B exposure", "retain_learn_b_": "B taught"}
	for prefix in arms:
		if id.begins_with(prefix):
			var measure: String = {"a_first": "first A", "a_recall": "4 A recalls", "b_first": "first B"}.get(id.trim_prefix(prefix), fallback)
			return "%s · %s" % [arms[prefix], measure]
	return fallback
