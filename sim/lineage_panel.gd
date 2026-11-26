extends ScrollContainer

signal open_lab
signal open_report(branch_id: String, generation: int)

const Address = preload("res://service_address.gd")
const ThemeKit = preload("res://lab_theme.gd")
const PAGE := 20

var http := HTTPRequest.new()
var branches := OptionButton.new()
var switch_button := Button.new()
var refresh_button := Button.new()
var export_button := Button.new()
var export_dialog := AcceptDialog.new()
var export_result: Dictionary = {}
var export_filename := Label.new()
var export_context := Label.new()
var export_path := LineEdit.new()
var copy_path_button := Button.new()
var open_folder_button := Button.new()
var export_note := Label.new()
var page_back := Button.new()
var page_forward := Button.new()
var generations := ItemList.new()
var title := Label.new()
var score := Label.new()
var variants := Tree.new()
var branch_name := LineEdit.new()
var fork_button := Button.new()
var report_button := Button.new()
var lineage_info := Label.new()
var message := Label.new()
var branch_items: Array = []
var rows: Array = []
var active_id := ""
var selected_branch := ""
var selected_generation := 0
var snapshot: Dictionary = {}
var cursors: Array[int] = [0]
var has_older := false
var pending := ""
var columns := BoxContainer.new()

func _ready() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	var margin := MarginContainer.new()
	margin.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	for side in ["left", "right", "top", "bottom"]:
		margin.add_theme_constant_override("margin_" + side, 18)
	add_child(margin)
	var page := VBoxContainer.new()
	page.add_theme_constant_override("separation", 12)
	margin.add_child(page)
	_label(page, Label.new(), "Lineage archive", 25)
	_label(page, Label.new(), "Inspect a completed generation or breed a new lineage from its saved population.", 15, ThemeKit.MUTED)
	var tools := HFlowContainer.new()
	page.add_child(tools)
	branches.custom_minimum_size.x = 200
	branches.fit_to_longest_item = false
	tools.add_child(branches)
	branches.item_selected.connect(_choose_branch)
	_button(tools, switch_button, "Open this lineage", func(): _change_lineage(false))
	_button(tools, refresh_button, "Refresh", refresh)
	_button(tools, export_button, "Export run…", func(): _send("export", "/lab/export"))
	export_button.tooltip_text = "Save every lineage, the active population at its current trial, the personal Wisp, and readable reports in a portable ZIP."
	_setup_export_dialog()
	_label(page, lineage_info, "", 15, ThemeKit.ACCENT)
	columns.add_theme_constant_override("separation", 15)
	page.add_child(columns)
	var left := _card(columns)
	left.custom_minimum_size.x = 245
	_label(left, Label.new(), "Saved generations", 19)
	generations.custom_minimum_size.y = 245
	left.add_child(generations)
	generations.item_selected.connect(_choose_generation)
	var nav := HBoxContainer.new()
	left.add_child(nav)
	_button(nav, page_back, "Newer", _newer)
	_button(nav, page_forward, "Older", _older)
	var detail := _card(columns)
	detail.size_flags_stretch_ratio = 2.6
	_label(detail, title, "Generation details", 19)
	_label(detail, score, "", 14, ThemeKit.MUTED)
	variants.columns = 4
	variants.hide_root = true
	variants.column_titles_visible = true
	variants.custom_minimum_size.y = 225
	for col in range(4):
		variants.set_column_title(col, ["Wisp", "Fitness", "Neurons", "Synapses"][col])
		variants.set_column_expand(col, col == 0)
		variants.set_column_custom_minimum_width(col, [100, 68, 70, 78][col])
	detail.add_child(variants)
	var fork_row := HFlowContainer.new()
	detail.add_child(fork_row)
	branch_name.placeholder_text = "Lineage name (optional)"
	branch_name.max_length = 64
	branch_name.custom_minimum_size.x = 180
	fork_row.add_child(branch_name)
	_button(fork_row, fork_button, "Branch from here", func(): _change_lineage(true))
	_button(fork_row, report_button, "Generation summary", func(): open_report.emit(selected_branch, selected_generation))
	fork_button.tooltip_text = "Start a separate lineage at the next generation. The source keeps its descendants."
	_label(page, message, "", 14, Color("#f4ca8e"))
	add_child(http)
	http.timeout = 7
	http.request_completed.connect(_received)
	visibility_changed.connect(_shown)
	resized.connect(_layout)
	_layout()
	_controls()
	call_deferred("_shown")

func _layout() -> void:
	columns.vertical = size.x < 780

func _card(parent: Control) -> VBoxContainer:
	var panel := PanelContainer.new()
	panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	panel.add_theme_stylebox_override("panel", ThemeKit.surface(Color("#182632"), Color("#344854"), 7, 14, 12))
	parent.add_child(panel)
	var content := VBoxContainer.new()
	content.add_theme_constant_override("separation", 8)
	panel.add_child(content)
	return content

func _label(parent: Control, item: Label, caption: String, font_size: int, tint := Color("#edf5f6")) -> void:
	item.text = caption
	item.add_theme_font_size_override("font_size", font_size)
	item.add_theme_color_override("font_color", tint)
	item.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	parent.add_child(item)

func _button(parent: Control, item: Button, caption: String, callback: Callable) -> void:
	item.text = caption
	item.pressed.connect(callback)
	parent.add_child(item)

func _setup_export_dialog() -> void:
	export_dialog.title = "Run exported"
	export_dialog.ok_button_text = "Close"
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(205, 175)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	export_dialog.add_child(scroll)
	var column := VBoxContainer.new()
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.add_theme_constant_override("separation", 7)
	scroll.add_child(column)
	_label(column, export_filename, "", 19, ThemeKit.ACCENT)
	_label(column, export_context, "", 14)
	_label(column, Label.new(), "Saved ZIP", 14, ThemeKit.MUTED)
	export_path.editable = false
	export_path.select_all_on_focus = true
	export_path.add_theme_color_override("font_uneditable_color", ThemeKit.TEXT)
	column.add_child(export_path)
	var actions := HFlowContainer.new()
	column.add_child(actions)
	_button(actions, copy_path_button, "Copy path", _copy_export_path)
	_button(actions, open_folder_button, "Open folder", _open_export_folder)
	copy_path_button.visible = DisplayServer.has_feature(DisplayServer.FEATURE_CLIPBOARD)
	open_folder_button.visible = OS.get_name() in ["Windows", "macOS", "Linux", "FreeBSD", "NetBSD", "OpenBSD"]
	_label(column, export_note, "", 14, ThemeKit.ACCENT)
	export_note.hide()
	_label(column, Label.new(), "Copy the ZIP to the next computer. In its Project Fledge folder, use the ZIP’s new path below and choose a new import directory.", 14, ThemeKit.MUTED)
	var commands := TextEdit.new()
	commands.editable = false
	commands.add_theme_color_override("font_readonly_color", ThemeKit.TEXT)
	commands.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	commands.custom_minimum_size.y = 96
	commands.text = 'python -m wisp.transfer import --input "PATH-TO-ZIP" --destination "runs/imported-lab"\npython -m wisp.play --save "runs/imported-lab/personal.json"'
	commands.tooltip_text = "Replace PATH-TO-ZIP with the archive path on the destination computer."
	column.add_child(commands)
	add_child(export_dialog)

func _copy_export_path() -> void:
	DisplayServer.clipboard_set(export_path.text)
	export_note.text = "Copied the ZIP path."
	export_note.show()

func _open_export_folder() -> void:
	var normalized := export_path.text.replace("\\", "/")
	var result := OS.shell_open(normalized.get_base_dir())
	if result != OK:
		export_note.text = "This desktop could not open the folder. Use Copy path to locate the ZIP."
		export_note.show()

func _show_export(data: Dictionary) -> void:
	export_result = data
	export_filename.text = str(data.filename)
	export_path.text = str(data.path)
	export_path.tooltip_text = export_path.text
	export_context.text = "Active lineage %s  ·  Generation %d  ·  Trial %d\nIncludes all lineages, the current population, your personal Wisp, and readable reports." % [
		str(data.branch_name), int(data.generation), int(data.step)]
	export_note.hide()
	var window_size := get_viewport_rect().size
	export_dialog.popup_centered(Vector2i(clampi(int(window_size.x) - 60, 240, 620), clampi(int(window_size.y) - 70, 260, 460)))

func _shown() -> void:
	if is_visible_in_tree() and is_node_ready() and pending.is_empty():
		refresh()

func refresh() -> void:
	if pending.is_empty():
		_send("branches", "/lab/branches")

func _choose_branch(index: int) -> void:
	selected_branch = str(branch_items[index].id)
	selected_generation = 0
	cursors.assign([0])
	_show_origin(branch_items[index])
	_history()

func _history() -> void:
	_clear_details()
	# Request one extra row to determine if an older page exists.
	var path := "/lab/history?branch=%s&limit=%d" % [selected_branch.uri_encode(), PAGE + 1]
	if cursors.back() > 0:
		path += "&before=%d" % cursors.back()
	_send("history", path)

func _choose_generation(index: int) -> void:
	selected_generation = int(rows[index].generation)
	_clear_details()
	_send("generation", "/lab/generation?branch=%s&generation=%d" % [selected_branch.uri_encode(), selected_generation])

func _older() -> void:
	cursors.append(int(rows.back().generation))
	selected_generation = 0
	_history()

func _newer() -> void:
	cursors.pop_back()
	selected_generation = 0
	_history()

func _clear_details() -> void:
	snapshot = {}
	variants.clear()
	score.text = ""
	title.text = "Generation details"

func _change_lineage(fork: bool) -> void:
	if not fork and selected_branch == active_id:
		open_lab.emit()
		return
	var command: Dictionary = {"action": "switch", "branch": selected_branch}
	if fork:
		command = {"action": "branch", "branch": selected_branch, "generation": selected_generation,
			"name": branch_name.text.strip_edges()}
	_send("change", "/lab/control", command)

func _branch_title(id: String) -> String:
	for b in branch_items:
		if str(b.id) == id:
			return str(b.name)
	return id

func _show_origin(branch: Dictionary) -> void:
	lineage_info.text = "Origin lineage" if branch.get("parent_id") == null else "Branched from %s at generation %d" % [
		_branch_title(str(branch.parent_id)), int(branch.fork_generation)]

func _send(kind: String, path: String, data: Dictionary = {}) -> void:
	if not pending.is_empty():
		return
	pending = kind
	message.text = "Creating the portable run archive…" if kind == "export" else ""
	_controls()
	var post := not data.is_empty() or kind == "export"
	var method := HTTPClient.METHOD_POST if post else HTTPClient.METHOD_GET
	# A large archive can take time to write on the lab PC.
	http.timeout = 0.0 if kind == "export" else 7.0
	var err := http.request(Address.base_url() + path, ["Content-Type: application/json"], method,
		JSON.stringify(data) if post else "")
	if err != OK:
		_fail("Could not contact the lineage archive. Use Refresh to try again.")

func _fail(reason: String) -> void:
	pending = ""
	message.text = reason
	_controls()

func _received(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var kind := pending
	pending = ""
	var data = JSON.parse_string(body.get_string_from_utf8())
	if result != HTTPRequest.RESULT_SUCCESS or typeof(data) != TYPE_DICTIONARY:
		_fail("Could not read the lineage archive. Use Refresh to try again.")
		return
	if code < 200 or code >= 300:
		_fail(str(data.get("error", "The archive request failed.")))
		return
	match kind:
		"export":
			if not data.has("filename") or not data.has("path") or not data.has("branch_name") or not data.has("generation") or not data.has("step"):
				_fail("The service did not return the location of the exported run.")
				return
			message.text = "Run exported to " + str(data.path)
			_show_export(data)
		"branches":
			branch_items = data.branches
			active_id = str(data.active_branch_id)
			branches.clear()
			var chosen := 0
			for i in range(branch_items.size()):
				var b: Dictionary = branch_items[i]
				var id := str(b.id)
				branches.add_item(str(b.name) + ("  ·  active" if id == active_id else ""))
				if id == (active_id if selected_branch.is_empty() else selected_branch):
					chosen = i
			if not branch_items.is_empty():
				branches.select(chosen)
				selected_branch = str(branch_items[chosen].id)
				_show_origin(branch_items[chosen])
				_history()
		"history":
			var fetched: Array = data.generations
			has_older = fetched.size() > PAGE
			rows = fetched.slice(0, PAGE)
			generations.clear()
			var chosen := 0
			for i in range(rows.size()):
				var row: Dictionary = rows[i]
				generations.add_item("Generation %d" % int(row.generation))
				if int(row.generation) == selected_generation:
					chosen = i
			if rows.is_empty():
				title.text = "Awaiting a completed generation"
				score.text = "Complete the learning plan in the evolution lab to create the first checkpoint."
			else:
				generations.select(chosen)
				_choose_generation(chosen)
		"generation":
			snapshot = data.state
			title.text = "Generation %d" % selected_generation
			var selection := generations.get_selected_items()
			if not selection.is_empty():
				var origin := str(rows[selection[0]].branch_id)
				if origin != selected_branch:
					title.text += "  ·  Inherited from %s" % _branch_title(origin)
			var stats: Dictionary = snapshot.stats
			score.text = "%d Wisps   ·   Mean fitness %.1f%%   ·   Best %.1f%%   ·   %d distinct genomes" % [
				int(snapshot.population), 100.0 * float(stats.mean_fitness), 100.0 * float(stats.best_fitness), int(stats.unique_genomes)]
			var root := variants.create_item()
			for v in snapshot.variants:
				var row := variants.create_item(root)
				row.set_text(0, str(v.label))
				row.set_text(1, "%.1f%%" % (100.0 * float(v.fitness)))
				row.set_text(2, str(int(v.nodes)))
				row.set_text(3, str(int(v.edges)))
		"change":
			active_id = str(data.active_branch_id)
			selected_branch = active_id
			selected_generation = 0
			cursors.assign([0])
			branch_name.clear()
			open_lab.emit()
	_controls()

func _controls() -> void:
	var busy := not pending.is_empty()
	branches.disabled = busy or branch_items.is_empty()
	refresh_button.disabled = busy
	export_button.disabled = busy
	switch_button.disabled = busy or selected_branch.is_empty()
	fork_button.disabled = busy or snapshot.is_empty()
	report_button.disabled = fork_button.disabled
	page_back.disabled = busy or cursors.size() < 2
	page_forward.disabled = busy or not has_older
	generations.mouse_filter = Control.MOUSE_FILTER_IGNORE if busy else Control.MOUSE_FILTER_STOP
	generations.focus_mode = Control.FOCUS_NONE if busy else Control.FOCUS_ALL
