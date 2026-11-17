"""Settings: configs/config.json (comments allowed) plus the DevKit project's remote execution settings."""

import json
import os

DEFAULTS = {
    "devkit_root": "E:\\ARKDevkit",
    "project": "ShooterGame",
    "multicast_group_endpoint": "",
    "multicast_bind_address": "",
    "multicast_ttl": -1,
    "command_bind_address": "127.0.0.1",
    "discovery_timeout_seconds": 5,
    "connect_timeout_seconds": 15,
    "command_timeout_seconds": 120,
    "max_output_chars": 60000,
}

# Epic's own defaults, used when neither config.json nor DefaultEngine.ini says otherwise.
EPIC_GROUP_ENDPOINT = "239.0.0.1:6766"
EPIC_BIND_ADDRESS = "127.0.0.1"
EPIC_TTL = 0

INI_SECTION = "/script/pythonscriptplugin.pythonscriptpluginsettings"


def strip_json_comments(text):
    """Remove // and /* */ comments outside of strings."""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def load_json_with_comments(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.loads(strip_json_comments(f.read()))


def parse_python_ini(text):
    """Return the PythonScriptPluginSettings keys (lower-cased) from an Unreal .ini file's text."""
    values = {}
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_section = line[1:-1].strip().lower() == INI_SECTION
            continue
        if in_section and "=" in line:
            key, _, val = line.partition("=")
            key = key.strip().lstrip("+-.!").lower()
            values[key] = val.strip()
    return values


def parse_endpoint(text):
    host, _, port = text.strip().rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"bad endpoint {text!r}, expected host:port")
    return host, int(port)


def load(config_path=None):
    """Load settings. Returns (settings dict, list of notes for the log)."""
    notes = []
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))
    config_path = config_path or os.environ.get("ARKMCP_CONFIG") or os.path.join(repo, "configs", "config.json")

    settings = dict(DEFAULTS)
    if os.path.isfile(config_path):
        try:
            user = load_json_with_comments(config_path)
            unknown = sorted(set(user) - set(DEFAULTS))
            if unknown:
                notes.append(f"config: ignoring unknown keys {unknown}")
            settings.update({k: v for k, v in user.items() if k in DEFAULTS})
        except (OSError, ValueError) as e:
            notes.append(f"config: could not read {config_path}: {e}; using defaults")
    else:
        notes.append(f"config: {config_path} not found; using defaults")
    settings["config_path"] = config_path

    ini_path = os.path.join(settings["devkit_root"], "Projects", settings["project"], "Config", "DefaultEngine.ini")
    ini = {}
    if os.path.isfile(ini_path):
        try:
            with open(ini_path, "r", encoding="utf-8-sig", errors="replace") as f:
                ini = parse_python_ini(f.read())
        except OSError as e:
            notes.append(f"ini: could not read {ini_path}: {e}")
    else:
        notes.append(f"ini: {ini_path} not found; using Epic's default remote execution settings")
    settings["ini_path"] = ini_path
    settings["ini_remote_execution"] = ini.get("bremoteexecution", "").lower() in ("true", "1")
    settings["ini_found"] = bool(ini)

    settings["multicast_group_endpoint"] = (
        settings["multicast_group_endpoint"] or ini.get("remoteexecutionmulticastgroupendpoint") or EPIC_GROUP_ENDPOINT)
    settings["multicast_bind_address"] = (
        settings["multicast_bind_address"] or ini.get("remoteexecutionmulticastbindaddress") or EPIC_BIND_ADDRESS)
    if int(settings["multicast_ttl"]) < 0:
        try:
            settings["multicast_ttl"] = int(ini.get("remoteexecutionmulticastttl", EPIC_TTL))
        except ValueError:
            settings["multicast_ttl"] = EPIC_TTL
    parse_endpoint(settings["multicast_group_endpoint"])  # validate early
    return settings, notes
