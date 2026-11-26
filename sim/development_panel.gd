extends ScrollContainer

signal open_memory

const Address = preload("res://service_address.gd")
const Kit = preload("res://lab_theme.gd")

var http := HTTPRequest.new()
var poll := Timer.new()
var topic := TabBar.new()
var memory_button := Button.new()
var copy_button := Button.new()
var live_button := Button.new()
var report: Dictionary = {}
var scope_branch := ""
var scope_generation := 0
var requesting := false
var request_scope := ""
var header := Label.new()
var status := Label.new()
var summary := VBoxContainer.new()
var curriculum := VBoxContainer.new()
var headline := Label.new()
var explanation := Label.new()
var evidence := GridContainer.new()
var evidence_cells: Array[Label] = []
var trend := Label.new()
var anatomy := Label.new()
var group_scores := Label.new()
var limits := Label.new()
var lesson_steps := VBoxContainer.new()
var lesson_position := Label.new()
var report_text := ""
var copied_at := -10000

func _ready() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	var inset := MarginContainer.new()
	inset.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	for side in ["top", "bottom", "left", "right"]:
		inset.add_theme_constant_override("margin_" + side, 16)
	add_child(inset)
	var page := VBoxContainer.new()
	page.add_theme_constant_override("separation", 9)
	inset.add_child(page)
	_label(page, header, "Plan & development", 24)
	var nav := HFlowContainer.new()
	page.add_child(nav)
	topic.add_tab("Generation summary")
	topic.add_tab("Learning plan")
	topic.custom_minimum_size.x = 315
	nav.add_child(topic)
	topic.tab_changed.connect(_show_topic)
	_button(nav, copy_button, "Copy summary", _copy)
	_button(nav, live_button, "View live generation", func(): show_report("", 0, 0))
	_label(page, status, "Reading the current generation…", 14, Kit.MUTED)
	page.add_child(summary)
	page.add_child(curriculum)
	summary.add_theme_constant_override("separation", 10)
	curriculum.add_theme_constant_override("separation", 12)

	var findings := _card(summary)
	_label(findings, headline, "Waiting for observations", 20, Kit.ACCENT)
	_label(findings, explanation, "", 15)
	evidence.columns = 3
	evidence.add_theme_constant_override("h_separation", 20)
	evidence.add_theme_constant_override("v_separation", 5)
	findings.add_child(evidence)
	for caption in ["Measurement", "Correct / observed", "Accuracy"]:
		var field := Label.new()
		field.add_theme_color_override("font_color", Kit.MUTED)
		field.text = caption
		evidence.add_child(field)
	var population := _card(summary)
	_label(population, Label.new(), "Population & comparison", 18)
	_label(population, anatomy, "", 15)
	_label(population, group_scores, "", 14, Kit.MUTED)
	_label(population, trend, "", 15, Kit.MUTED)
	_label(population, limits, "", 14, Color("#f4ca8e"))

	var protocol := _card(curriculum)
	_label(protocol, Label.new(), "Two cues, one reversal", 20, Kit.ACCENT)
	_label(protocol, lesson_position, "", 15)
	protocol.add_child(lesson_steps)
	lesson_steps.add_theme_constant_override("separation", 5)
	var roadmap := _card(curriculum)
	_label(roadmap, Label.new(), "Next curriculum: memory experiments", 19)
	_label(roadmap, Label.new(), "After 128 cohort trials the lab saves the generation. Auto-advance breeds offspring for the same lesson. With auto-advance off, you can review the completed population.", 15)
	_label(roadmap, Label.new(), "Test a completed population on two new lessons. Delay the teacher’s feedback across other choices, then measure whether learning a second cue changes recall of the first.", 15)
	_button(roadmap, memory_button, "Open memory experiments", func(): open_memory.emit())
	_label(roadmap, Label.new(), "Later: body adaptation. Change a simulated sensor or movement delay and test whether earlier lessons survive recalibration.", 15)
	_label(roadmap, Label.new(), "Physical transfer follows virtual qualification. A candidate must retain a taught rule going from Godot to the bench and back.", 15, Kit.MUTED)
	var review := _card(curriculum)
	_label(review, Label.new(), "When to change the curriculum", 19)
	_label(review, Label.new(), "Review recall and reversal separately on fresh lessons across independent lineages. Keep a checkpoint on the original task before introducing a new one.", 15)
	_label(review, Label.new(), "A specialized circuit is a testable hypothesis. If a lineage struggles with delayed recall, compare freely evolved wiring with a group-duplication variant. Test whether briefly silencing a candidate group selectively impairs recall while simple orientation survives.", 15, Kit.MUTED)

	add_child(http)
	http.timeout = 5
	http.request_completed.connect(_received)
	add_child(poll)
	poll.wait_time = 1.0
	poll.timeout.connect(_poll_live)
	poll.start()
	visibility_changed.connect(_refresh)
	_show_topic(0)
	copy_button.disabled = true
	live_button.hide()
	call_deferred("_refresh")

func _card(parent: Control) -> VBoxContainer:
	var panel := PanelContainer.new()
	panel.add_theme_stylebox_override("panel", Kit.surface(Color("#182632"), Color("#344854"), 7, 14, 10))
	parent.add_child(panel)
	var content := VBoxContainer.new()
	content.add_theme_constant_override("separation", 6)
	panel.add_child(content)
	return content

func _label(parent: Control, item: Label, caption: String, font_size: int, tint := Color("#edf5f6")) -> void:
	item.text = caption
	item.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	item.add_theme_font_size_override("font_size", font_size)
	item.add_theme_color_override("font_color", tint)
	parent.add_child(item)

func _button(parent: Control, item: Button, caption: String, callback: Callable) -> void:
	item.text = caption
	item.pressed.connect(callback)
	parent.add_child(item)

func _show_topic(index: int) -> void:
	summary.visible = index == 0
	curriculum.visible = index == 1

func show_report(branch_id: String, generation: int, tab_index: int) -> void:
	scope_branch = branch_id
	scope_generation = generation
	topic.current_tab = tab_index
	_show_topic(tab_index)
	live_button.visible = scope_generation > 0
	if not report.is_empty():
		report.clear()
		copy_button.disabled = true
		summary.hide()
		curriculum.hide()
	status.text = "Reading the generation…"
	_refresh()

func _scope() -> String:
	return "%s/%d" % [scope_branch, scope_generation]

func _poll_live() -> void:
	if Time.get_ticks_msec() - copied_at > 2000:
		copy_button.text = "Copy summary"
	if scope_generation == 0 or report.is_empty():
		_refresh()

func _refresh() -> void:
	if not is_node_ready() or not is_visible_in_tree() or requesting:
		return
	var path := "/lab/development"
	if scope_generation > 0:
		path += "?branch=%s&generation=%d" % [scope_branch.uri_encode(), scope_generation]
	request_scope = _scope()
	requesting = true
	if http.request(Address.base_url() + path) != OK:
		_failed("Could not reach the laboratory.")

func _failed(message: String) -> void:
	requesting = false
	status.text = message
	copy_button.disabled = true

func _received(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	requesting = false
	if request_scope != _scope():
		call_deferred("_refresh")
		return
	var data = JSON.parse_string(body.get_string_from_utf8()) if not body.is_empty() else null
	if result != HTTPRequest.RESULT_SUCCESS or typeof(data) != TYPE_DICTIONARY or code != 200 or not data.has("evidence"):
		_failed("Report unavailable. Check the laboratory connection.")
		return
	report = data
	_render()
	_show_topic(topic.current_tab)

func _percent(value) -> String:
	return "—" if value == null else "%.1f%%" % (100.0 * float(value))

func _render() -> void:
	var title := "%s · Generation %d" % [str(report.branch_name), int(report.generation)]
	header.text = title
	status.text = "%s  ·  %d of %d cohort trials" % ["Archived checkpoint" if scope_generation > 0 else "Live observations", int(report.step), int(report.steps_per_generation)]
	headline.text = str(report.headline)
	explanation.text = str(report.interpretation)
	var lines: Array[String] = [title, status.text, headline.text, explanation.text]
	var rows: Array = report.evidence
	while evidence_cells.size() < rows.size() * 3:
		var cell := Label.new()
		evidence_cells.append(cell)
		evidence.add_child(cell)
	for i in rows.size():
		var row: Dictionary = rows[i]
		var count := "Awaiting trials"
		if row.status == "unrecorded":
			count = "Unrecorded"
		elif int(row.observed) > 0:
			count = "%d / %d" % [int(row.correct), int(row.observed)]
		var values := [str(row.label), count, _percent(row.accuracy)]
		for k in range(3):
			evidence_cells[i * 3 + k].text = values[k]
			evidence_cells[i * 3 + k].tooltip_text = "%s · %d observations planned" % [str(row.status).capitalize(), int(row.expected)]
		lines.append("%s: %s, %s." % values)
	var p: Dictionary = report.population
	var f: Dictionary = report.fitness
	anatomy.text = "%d Wisps, %d distinct genomes. Networks have %d–%d neurons and %d–%d synapses.\nMean recall fitness %s · Best %s" % [
		int(p.candidates), int(p.unique_genomes), int(p.min_nodes), int(p.max_nodes), int(p.min_edges), int(p.max_edges), _percent(f.mean), _percent(f.best)]
	var groups: Array[String] = []
	for condition in report.conditions:
		groups.append("%s %s" % [str(condition.label), _percent(condition.mean)])
	group_scores.text = "Mean by group: " + "   ·   ".join(groups)
	trend.text = str(report.trend.text)
	limits.text = "Still to test: " + ", ".join(report.untested) + "."
	lines.append_array([anatomy.text, group_scores.text, trend.text, limits.text])
	lines.append("Cohort teaching steps with a manual override: %d." % int(report.manual_overrides))
	lines.append(str(report.scope))
	if report.has("evolution_seed"):
		lines.append("Evolution seed: %s" % str(report.evolution_seed))
	report_text = "\n".join(lines)
	copy_button.disabled = false
	var plan: Array = report.plan
	while lesson_steps.get_child_count() < plan.size():
		_label(lesson_steps, Label.new(), "", 15)
	lesson_position.text = "Lifetime %d of %d. The cue assignment flips between lifetimes. Feedback is withheld during probes." % [int(report.lifetime), int(report.lifetimes_per_generation)]
	for i in range(plan.size()):
		var stage: Dictionary = plan[i]
		var item: Label = lesson_steps.get_child(i)
		var current := i == int(report.phase_index) and not bool(report.complete)
		item.text = "%d. %s%s  ·  %d trials  ·  %s" % [i+1, str(stage.label), " (current)" if current else "", int(stage.trials), str(stage.description)]
		item.add_theme_color_override("font_color", Kit.ACCENT if current else Kit.TEXT)

func _copy() -> void:
	if not report_text.is_empty():
		DisplayServer.clipboard_set(report_text)
		copied_at = Time.get_ticks_msec()
		copy_button.text = "Copied"
