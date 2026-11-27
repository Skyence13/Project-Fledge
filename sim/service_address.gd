extends RefCounted

static func base_url() -> String:
	var raw := OS.get_environment("FLEDGE_SERVICE_PORT")
	var port := raw.to_int() if raw.is_valid_int() else 8765
	if port < 1 or port > 65535:
		port = 8765
	return "http://127.0.0.1:%d" % port
