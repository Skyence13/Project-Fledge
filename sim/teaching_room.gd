extends Control

const WispView = preload("res://wisp_view.gd")
const LabPanel = preload("res://lab_panel.gd")
const LineagePanel = preload("res://lineage_panel.gd")
const LabTheme = preload("res://lab_theme.gd")
const Address = preload("res://service_address.gd")
const MUTED := Color("#b6c8d1")

var http := HTTPRequest.new()
var wisp := WispView.new()
var message := Label.new()
var summary := Label.new()
var ring_button: Button
var diamond_button: Button
var yes_button: Button
var no_button: Button
var save_button: Button
var load_button: Button
var refresh_button: Button
var ready_for_input := false
var busy := false
var pending := false
var saved := false
var current_path := ""
var tabs := TabContainer.new()

func _ready() -> void:
	get_window().title = "Project Fledge | Wisp laboratory"
	theme = LabTheme.make_theme()
	var frame := MarginContainer.new()
	add_child(frame)
	frame.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left", "right", "top", "bottom"]:
		frame.add_theme_constant_override("margin_" + side, 12)
	tabs.name = "Rooms"
	frame.add_child(tabs)
	var lab := LabPanel.new()
	lab.name = "EvolutionLab"
	tabs.add_child(lab)
	tabs.set_tab_title(0, "Evolution lab")
	var personal := ScrollContainer.new()
	personal.name = "PersonalWisp"
	personal.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	tabs.add_child(personal)
	tabs.set_tab_title(1, "Personal Wisp")
	var lineages := LineagePanel.new()
	lineages.name = "Lineages"
	tabs.add_child(lineages)
	tabs.set_tab_title(2, "Lineages")
	lineages.open_lab.connect(func(): tabs.current_tab = 0)
	var margin := MarginContainer.new()
	personal.add_child(margin)
	margin.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	margin.size_flags_vertical = Control.SIZE_EXPAND_FILL
	for side in ["left", "right", "top", "bottom"]:
		margin.add_theme_constant_override("margin_" + side, 24)
	var column := VBoxContainer.new()
	column.add_theme_constant_override("separation", 10)
	margin.add_child(column)
	var title := Label.new()
	title.text = "One-to-one teaching"
	title.add_theme_font_size_override("font_size", 28)
	column.add_child(title)
	var intro := Label.new()
	intro.text = "Decide privately which direction each cue should mean. Show a cue, watch Wisp, then give feedback."
	intro.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	intro.add_theme_color_override("font_color", MUTED)
	column.add_child(intro)
	wisp.custom_minimum_size = Vector2(0, 187)
	wisp.size_flags_vertical = Control.SIZE_EXPAND_FILL
	column.add_child(wisp)
	var teaching := _row(column)
	ring_button = _button(teaching, "Show ring", func(): _send("/choice", {"cue": 0}))
	diamond_button = _button(teaching, "Show diamond", func(): _send("/choice", {"cue": 1}))
	yes_button = _button(teaching, "Yes, that's it", func(): _send("/feedback", {"value": 1}))
	no_button = _button(teaching, "Try the other way", func(): _send("/feedback", {"value": -1}))
	summary.add_theme_color_override("font_color", MUTED)
	summary.text = "Connecting to the local learning service…"
	column.add_child(summary)
	message.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	message.text = "Wisp is waiting for its current learning state."
	message.custom_minimum_size.y = 50
	column.add_child(message)
	var memory := _row(column)
	save_button = _button(memory, "Save learning", func(): _send("/save", {}))
	load_button = _button(memory, "Load saved learning", func(): _send("/load", {}))
	refresh_button = _button(memory, "Refresh connection", func(): _send("/state", {}))
	add_child(http)
	http.timeout = 4.0
	http.request_completed.connect(_on_completed)
	_send("/state", {})

func _row(parent: Control) -> HFlowContainer:
	var row := HFlowContainer.new()
	row.add_theme_constant_override("h_separation", 9)
	row.add_theme_constant_override("v_separation", 4)
	parent.add_child(row)
	return row

func _button(parent: Control, caption: String, callback: Callable) -> Button:
	var button := Button.new()
	button.text = caption
	button.custom_minimum_size.y = 38
	button.pressed.connect(callback)
	parent.add_child(button)
	return button

func _send(path: String, data: Dictionary) -> void:
	if busy:
		return
	current_path = path
	busy = true
	_update_buttons()
	var verb := HTTPClient.METHOD_GET if path == "/state" else HTTPClient.METHOD_POST
	var body := "" if path == "/state" else JSON.stringify(data)
	var err := http.request(Address.base_url() + path, ["Content-Type: application/json"], verb, body)
	if err != OK:
		_disconnected("Request could not start (%s)." % error_string(err))

func _on_completed(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	busy = false
	if result != HTTPRequest.RESULT_SUCCESS:
		_disconnected("Start the local Fledge service at %s, then refresh the connection." % Address.base_url())
		return
	var payload = JSON.parse_string(body.get_string_from_utf8())
	if typeof(payload) != TYPE_DICTIONARY:
		_disconnected("Unexpected service reply. Refresh to synchronise.")
		return
	if code < 200 or code >= 300:
		_disconnected("Service returned %d: %s. Refresh to synchronise." % [code, str(payload.get("error", "unknown error"))])
		return
	for key in ["cue", "action", "pending", "trials", "saved"]:
		if not payload.has(key):
			_disconnected("Incomplete state. Refresh to synchronise.")
			return
	var cue := -1 if payload.cue == null else int(payload.cue)
	var action := -1 if payload.action == null else int(payload.action)
	pending = bool(payload.pending)
	saved = bool(payload.saved)
	ready_for_input = true
	wisp.show_choice(cue, action)
	summary.text = "Feedback rounds: %d    •    Saved memory: %s" % [int(payload.trials), "available" if saved else "none"]
	if pending:
		message.text = "Wisp looked %s. Give feedback for that choice." % ("left" if action == 0 else "right")
	elif current_path == "/save":
		message.text = "Learning saved. Show a cue to continue."
	elif current_path == "/load":
		message.text = "Saved learning restored. Show a cue to try it."
	elif current_path == "/feedback":
		message.text = "Feedback received. Show either cue for the next round."
	else:
		message.text = "Connected. Show either cue to begin."
	_update_buttons()

func _disconnected(reason: String) -> void:
	ready_for_input = false
	busy = false
	summary.text = "Waiting for the local service"
	message.text = reason
	wisp.show_choice(-1, -1)
	_update_buttons()

func _update_buttons() -> void:
	var unavailable := busy or not ready_for_input
	ring_button.disabled = unavailable or pending
	diamond_button.disabled = unavailable or pending
	yes_button.disabled = unavailable or not pending
	no_button.disabled = unavailable or not pending
	save_button.disabled = unavailable or pending
	load_button.disabled = unavailable or pending or not saved
	refresh_button.disabled = busy
