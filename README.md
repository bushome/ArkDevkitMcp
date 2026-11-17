```
  ___         _      ______               _     _  _
 / _ \       | |     |  _  \             | |   (_)| |
/ /_\ \ _ __ | | __  | | | |  ___ __   __| | __ _ | |_
|  _  || '__|| |/ /  | | | | / _ \\ \ / /| |/ /| || __|
| | | || |   |   <   | |/ / |  __/ \ V / |   < | || |_
\_| |_/|_|   |_|\_\  |___/   \___|  \_/  |_|\_\|_| \__|
___  ___ _____ ______           .--------.
|  \/  |/  __ \| ___ \          | Client |
| .  . || /  \/| |_/ /          '---+----'
| |\/| || |    |  __/               |  MCP
| |  | || \__/\| |              .---+----.
\_|  |_/ \____/\_|              | DevKit |
```

MCP server that connects Codex or Claude Desktop to the ARK: Survival Ascended DevKit (Unreal Engine 5.8),
so the assistant can read and edit Blueprints, variables, data assets and level actors in the open editor:
place nodes, wire pins, set pin values, add variables and functions, compile and save. No C++ and no
editor plugin to build; it uses the Python scripting and Blueprint graph API that already ship in the
DevKit.

**Status: in development, with a working live editor connection.** On November 17, 2026, Codex connected
through MCP to the ShooterGame DevKit **5.8.3**, executed Python **3.11.9** inside the editor, and confirmed
that `BlueprintEditorLibrary`, `BlueprintGraphEditor` and `BlueprintGraphPinLibrary` are available.
It also found the training Blueprint at `/ArkMCPTesting/BP_McpTest1`. This connection check was read-only.
Earlier live sessions exercised Blueprint editing and layout; see Validation and known gaps below.

## How it works

```
Codex / Claude Desktop --stdio/MCP--> arkmcp_server.py --UDP discovery + TCP--> DevKit editor (Python)
```

1. The MCP client starts `src/arkmcp_server.py` with a standalone Python 3.11 installation. Only the
   Python standard library is used; there are no pip dependencies. This external server process is
   separate from the Python embedded in the editor.
2. The server finds the open editor through Unreal's Python **remote execution** (UDP multicast
   discovery, then a TCP command connection the editor opens back to the server). The multicast
   settings are read from the project's `DefaultEngine.ini`, so they always match the editor.
3. On first use the server loads its helpers (`typespec.py`, `layout.py` and `editor_side.py`) into the editor's
   Python. Every tool call runs there, on the editor's game thread, through `unreal.BlueprintEditorLibrary`,
   `BlueprintGraphEditor` and `BlueprintGraphPinLibrary`.

The built-in editing tools wrap changes in editor transactions (**Ctrl+Z works**) and leave assets
unsaved until `save_assets` is called. `run_python` executes arbitrary editor Python; its undo and save
behavior depends on the code supplied.

The DevKit's own *Unreal MCP* plugin (`Engine/Plugins/Experimental/ModelContextProtocol`) is only a
`.uplugin` stub with no code or binaries (Epic marks it NoRedist), so it cannot load. Leave it disabled.

## Requirements

- ARK: Survival Ascended DevKit (UE 5.8) at `E:\ARKDevkit` (change in `configs/config.json`)
- Python Editor Script Plugin enabled and **Remote Execution** on: *Edit > Project Settings > Plugins >
  Python > Enable Remote Execution*. The ShooterGame project already has both
  (`bRemoteExecution=True` in `Projects/ShooterGame/Config/DefaultEngine.ini`).
- Codex or Claude Desktop configured to launch the server over stdio

## Install

The server needs only Python's standard library, and should run on a **standalone Python 3.11**, not the
DevKit's bundled one: the Epic launcher treats the DevKit as running while any process from the DevKit
folder is alive, so a server running on the DevKit's Python stops you launching it. `install.ps1` uses
`-Python <path>` if given, otherwise Python 3.11 from the `py` launcher or the usual install folders
(`%LOCALAPPDATA%\Programs\Python\Python311`, `%ProgramFiles%\Python311`, `C:\Python311`). It only falls
back to the DevKit's Python, with a warning, when none is found. The 3.11.9 installer from python.org works;
untick "Add python.exe to PATH" if you don't want it on your PATH.

### Codex (verified November 17, 2026)

Add this entry to `%USERPROFILE%\.codex\config.toml`, replacing `<you>` and the repository path as needed.
This matches the manually configured server used for the successful live connection. The single quotes
are TOML literal strings, so Windows backslashes do not need escaping.

```toml
[mcp_servers.arkmcp]
command = 'C:\Users\<you>\AppData\Local\Programs\Python\Python311\python.exe'
args = ['-u', 'C:\Dev\Tools\ArkDevkitMcp\src\arkmcp_server.py']
enabled = true
```

After Codex loads the configuration, open the DevKit and call `editor_status`. A successful result lists
ShooterGame under `editors`, sets `connected_to` to `ShooterGame`, and reports all three graph API flags
as `true`. `install.ps1` configures Claude Desktop only; it does not edit Codex's `config.toml`.

### Claude Desktop

Fully close Claude Desktop before editing its config: the app can rewrite the file on exit and discard
outside changes. On this workstation, the active file is under
`%LOCALAPPDATA%\Claude-Data\claude_<id>\claude_desktop_config.json`, rather than `%APPDATA%\Claude`.
Merge this server entry into the active file's existing `mcpServers` object, then restart Claude Desktop:

```json
{
  "mcpServers": {
    "ark-devkit": {
      "command": "C:\\Users\\<you>\\AppData\\Local\\Programs\\Python\\Python311\\python.exe",
      "args": ["-u", "C:\\Dev\\Tools\\ArkDevkitMcp\\src\\arkmcp_server.py"]
    }
  }
}
```

The checked-in installer still targets `%APPDATA%\Claude` and the Microsoft Store package's
`LocalCache\Roaming\Claude` folder. It does **not** discover `Claude-Data` and has no `-ConfigPath` option,
so use the manual entry above for that layout. For installations using the older locations:

```powershell
cd C:\Dev\Tools\ArkDevkitMcp
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Check
```

The script backs up the config and preserves other server entries. `-Check` also connects to the open
editor and prints its status. Options: `-Python <path>`, `-DevkitRoot <path>`, `-ServerName <name>`,
`-Check`, `-Uninstall`.

### Check without an MCP client

From the repository folder, with the DevKit open and loaded, run the same entry point directly using
standalone Python:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -u .\src\arkmcp_server.py --check
```

## Configuration

`configs/config.json` (comments allowed; `config.commented.json` has the same values with explanations).
`ARKMCP_CONFIG` can point to a different file.

| Key | Default | Meaning |
|---|---|---|
| `devkit_root` | `E:\ARKDevkit` | DevKit folder, used to find `DefaultEngine.ini` |
| `project` | `ShooterGame` | Project folder; preferred if several editors answer |
| `multicast_group_endpoint` | *(ini)* | Empty = from `DefaultEngine.ini`, else Epic default `239.0.0.1:6766` |
| `multicast_bind_address` | *(ini)* | Empty = from `DefaultEngine.ini`, else `127.0.0.1` |
| `multicast_ttl` | `-1` | -1 = from `DefaultEngine.ini`, else 0 (this machine only) |
| `command_bind_address` | `127.0.0.1` | Address the editor connects back to (port picked automatically) |
| `discovery_timeout_seconds` | `5` | Wait for the editor to answer pings |
| `connect_timeout_seconds` | `15` | Wait for the editor to open the command connection |
| `command_timeout_seconds` | `120` | Longest single command |
| `max_output_chars` | `60000` | Longer tool replies are cut off |

## Tools

| Tool | What it does |
|---|---|
| `editor_status` | Editors found, engine version, whether the graph API is present |
| `run_python` | Run any Python in the editor (`unreal` available); returns printed output |
| `api_search`, `api_help` | Find `unreal` types and read exact signatures/docstrings in this DevKit |
| `find_assets` | Assets under a path, filtered by class and name |
| `get_properties`, `set_properties` | Read/write editable properties of an asset, a Blueprint's Class Defaults (incl. Blueprint variables), a class (`..._C`) or a level actor (`actor:<label>`) |
| `save_assets` | Explicitly save selected assets to disk |
| `blueprint_info` | Parent class, graphs, variables (type, category, default), functions, events, dispatchers |
| `compile_blueprint` | Compile and list nodes with errors/warnings |
| `add_variable`, `remove_variable` | Member variables; type syntax below |
| `create_function` | New function graph |
| `list_graph` | Nodes with ids, titles, positions, pins, values and links |
| `list_available_nodes` | The right-click palette as `Category\|Name` |
| `add_node` | `action` (palette name), `call_function`, `branch`, `custom_event`, `event`, `get_variable`/`set_variable`, `get_local`/`set_local`, `macro`, `return`, `dispatcher_event`, `component_event`, `comment` |
| `connect_pins`, `disconnect_pins` | Wire / unwire output to input pins |
| `set_pin_value` | Literal value of an unconnected input pin |
| `move_node`, `remove_nodes`, `add_node_pin` | Layout, delete, add pins to Sequence/Switch/Make Array/math nodes |
| `tidy_graph` | Place selected new nodes around an existing layout; `dry_run=true` previews positions |

Variable types: `bool`, `int`, `byte`, `real` (`float`/`double`), `name`, `string`, `text`,
`object:/Script/Engine.Actor`, `class:/MyMod/BP_Y.BP_Y_C`, `struct:/Script/CoreUObject.Vector`,
`array<T>`, `set<T>`, `map<K,V>` (no directly nested containers, same as Blueprints).

## Mod paths and graph layout

Mods mount under their own content roots, rather than `/Game`. `find_assets` defaults to `/Game`, so
pass the mod root explicitly. The training mod lives at
`E:\ARKDevkit\Projects\ShooterGame\Mods\ArkMCPTesting` and appears under Plugins in the Content Browser.
Its verified asset root is `/ArkMCPTesting`; the current training Blueprint is
`/ArkMCPTesting/BP_McpTest1`.

Example `find_assets` arguments:

```json
{"path": "/ArkMCPTesting", "name_contains": "BP_McpTest", "recursive": true}
```

Preserve existing graph placement. These graphs follow PLC ladder conventions: exec flow runs left to
right along a rail, shared inputs use buses of reroute knots below it, and results use stacked output
lanes. Sequence nodes wrap execution onto later rows; branch alternatives have their own rows.
Use `tidy_graph` with `nodes=[...]` containing only the nodes just added, and preview with `dry_run=true`
when calibrating placement. A full re-layout is refused when reroute knots are present unless
`force=true`; do not use that override on an existing hand-arranged graph.

Layout estimates account for collapsed advanced pins and Development Only banners, including Print
String. Spacing is still being calibrated through manual placement corrections.

Example requests once connected:

- "Find Blueprints under /ArkMCPTesting."
- "In /ArkMCPTesting/BP_McpTest1, add an int variable MaxUses defaulting to 5 and show me its Class Defaults."
- "In that Blueprint's EventGraph, print MaxUses, position only the new nodes, then compile."

## Validation and known gaps

The November 17 Codex check verified connection, editor Python execution, graph API availability and
training asset discovery. It did not repeat editing tests or save assets.

Earlier live testing on a scratch Blueprint exercised asset discovery, API help, Blueprint inspection,
graph and palette listing, variable creation (including defaults and `array<string>`), function creation,
node creation (function calls, variable getters, branches, palette actions, comments and custom events),
exec/data connections, pin values, Class Defaults read/write, node movement/removal, layout and compilation
(`BS_UP_TO_DATE`).

Known gaps and remaining live checks:

- `disconnect_pins`, `add_node_pin`, event overrides, `remove_variable` and `save_assets` were not exercised
  in the imported live-test findings.
- `list_graph` returns K2 nodes and omits comment boxes; layout does not refit comments.
- Dedicated asset creation and reroute knot/bus lane tools are not implemented. Reroute support is the
  next planned addition.
- Node dimensions are estimates because this DevKit returns placeholder sizes; layout needs further
  calibration against manual placements.

## Troubleshooting

- **`editor_status` finds no editor**: the DevKit must be open and fully loaded; Remote Execution must be
  on (see Requirements). Windows Firewall may ask about `python.exe` the first time; allow it on private
  networks (traffic stays on this machine, multicast TTL 0).
- **"no signature matched in this DevKit build"**: the graph tools find functions by name on the graph
  editor, node, pin and library classes. If Epic placed one elsewhere or changed its parameters, the
  error lists what was tried and the docstrings. Use `api_help` and `run_python` to call it
  directly in the meantime; report it so the wrapper in `src/arkmcp/editor_side.py` can be fixed.
- **Timed out**: a long command may still be running in the editor. Check the editor before repeating
  anything that changes assets.
- **Mod assets are missing**: pass the mod root to `find_assets`, such as `/ArkMCPTesting`, instead of
  relying on the default `/Game` path.
- **Claude config changes have no effect**: confirm the active config location and fully close the app
  before editing it. The checked-in installer does not target the `Claude-Data` layout.
- Server diagnostics go to stderr, captured by the MCP client. For older Claude Desktop layouts, the
  server log is `%APPDATA%\Claude\logs\mcp-server-ark-devkit.log`; locations vary by client/install.
- Use the standalone `--check` command above to separate editor connectivity from MCP client setup.

## Tests

```
python tests\logic_tests.py --shipped
```

Stand-alone (no editor, any Python 3.11+): type specs, config/ini parsing, the remote execution wire
format, the MCP server, and the editor module run against a fake `unreal`. `--shipped` also checks that
`config.json` and `config.commented.json` hold the same values.

## Files

| Path | Purpose |
|---|---|
| `src/arkmcp_server.py` | Entry point (MCP over stdio; `--check`) |
| `src/arkmcp/mcp.py` | Minimal MCP / JSON-RPC server |
| `src/arkmcp/tools.py` | Tool list and dispatch |
| `src/arkmcp/ue_link.py` | Remote execution client (discovery, command connection, helper upload) |
| `src/arkmcp/protocol.py` | Remote execution wire format |
| `src/arkmcp/config.py` | Settings and `DefaultEngine.ini` reader |
| `src/arkmcp/typespec.py` | Variable type parser (also sent into the editor) |
| `src/arkmcp/editor_side.py` | Runs inside the editor; implements the editor tools |
| `src/arkmcp/layout.py` | Node size estimates, full layout and placement around existing nodes |
| `configs/` | `config.json`, `config.commented.json` |
| `install.ps1` | Registers the server with the Claude desktop app |
| `tests/logic_tests.py` | Stand-alone tests |

## License

MIT, see [LICENSE](LICENSE). No third-party code; see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
