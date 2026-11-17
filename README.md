```
  ___         _      ______               _     _  _
 / _ \       | |     |  _  \             | |   (_)| |
/ /_\ \ _ __ | | __  | | | |  ___ __   __| | __ _ | |_
|  _  || '__|| |/ /  | | | | / _ \\ \ / /| |/ /| || __|
| | | || |   |   <   | |/ / |  __/ \ V / |   < | || |_
\_| |_/|_|   |_|\_\  |___/   \___|  \_/  |_|\_\|_| \__|
___  ___ _____ ______           .--------.
|  \/  |/  __ \| ___ \          | Claude |
| .  . || /  \/| |_/ /          '---+----'
| |\/| || |    |  __/               |  MCP
| |  | || \__/\| |              .---+----.
\_|  |_/ \____/\_|              | DevKit |
```

MCP server that connects the Claude desktop app to the ARK: Survival Ascended DevKit (Unreal Engine 5.8),
so Claude can read and edit Blueprints, variables, data assets and level actors in the open editor:
place nodes, wire pins, set pin values, add variables and functions, compile and save. No C++ and no
editor plugin to build; it uses the Python scripting and Blueprint graph API that already ship in the
DevKit.

**Status: in development.** Tested stand-alone (protocol, MCP server, and the editor module against a fake
`unreal` module). Not yet run against the real editor; the first live run may turn up graph API
signatures that need adjusting (see Troubleshooting).

## How it works

```
Claude desktop app --stdio/MCP--> arkmcp_server.py --UDP 239.0.0.1:6766 + TCP--> DevKit editor (Python)
```

1. Claude desktop starts `src/arkmcp_server.py` with the DevKit's own Python 3.11, so nothing has to be
   installed. Only the Python standard library is used.
2. The server finds the open editor through Unreal's Python **remote execution** (UDP multicast
   discovery, then a TCP command connection the editor opens back to the server). The multicast
   settings are read from the project's `DefaultEngine.ini`, so they always match the editor.
3. On first use the server loads a helper module (`typespec.py` + `editor_side.py`) into the editor's
   Python. Every tool call runs there, on the editor's game thread, through `unreal.BlueprintEditorLibrary`,
   `BlueprintGraphEditor` and `BlueprintGraphPinLibrary`.

Every change is wrapped in an editor transaction (**Ctrl+Z works**) and nothing is written to disk until
`save_assets` is called.

The DevKit's own *Unreal MCP* plugin (`Engine/Plugins/Experimental/ModelContextProtocol`) is only a
`.uplugin` stub with no code or binaries (Epic marks it NoRedist), so it cannot load. Leave it disabled.

## Requirements

- ARK: Survival Ascended DevKit (UE 5.8) at `E:\ARKDevkit` (change in `configs/config.json`)
- Python Editor Script Plugin enabled and **Remote Execution** on: *Edit > Project Settings > Plugins >
  Python > Enable Remote Execution*. The ShooterGame project already has both
  (`bRemoteExecution=True` in `Projects/ShooterGame/Config/DefaultEngine.ini`).
- Claude desktop app

## Install

```powershell
cd C:\Dev\Tools\ArkDevkitMcp
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Check
```

`install.ps1` adds an `ark-devkit` entry to `claude_desktop_config.json` (backing it up first and keeping
every other server). `-Check` then connects to the open editor once and prints its status. Quit the Claude
desktop app from the tray and start it again to load the server.

The server needs only Python's standard library, and should run on a **standalone Python 3.11**, not the
DevKit's bundled one: the Epic launcher treats the DevKit as running while any process from the DevKit
folder is alive, so a server running on the DevKit's Python stops you launching it. `install.ps1` uses
`-Python <path>` if given, otherwise Python 3.11 from the `py` launcher or the usual install folders
(`%LOCALAPPDATA%\Programs\Python\Python311`, `%ProgramFiles%\Python311`, `C:\Python311`). It only falls
back to the DevKit's Python, with a warning, when none is found. The 3.11.9 installer from python.org works;
untick "Add python.exe to PATH" if you don't want it on your PATH.

Options: `-Python <path>`, `-DevkitRoot <path>`, `-ServerName <name>`, `-Uninstall`.

Manual entry, if preferred:

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
| `save_assets` | Save to disk (the only tool that does) |
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

Variable types: `bool`, `int`, `byte`, `real` (`float`/`double`), `name`, `string`, `text`,
`object:/Script/Engine.Actor`, `class:/Game/Mods/X/BP_Y.BP_Y_C`, `struct:/Script/CoreUObject.Vector`,
`array<T>`, `set<T>`, `map<K,V>` (no directly nested containers, same as Blueprints).

Example requests to Claude once it is connected:

- "In /Game/Mods/MyMod/BP_MyStructure, add an int variable MaxUses defaulting to 5 and show me its Class Defaults."
- "In the EventGraph of BP_MyStructure, on BeginPlay print the value of MaxUses, then compile."
- "List every DataTable under /Game/Mods/MyMod."

## Troubleshooting

- **`editor_status` finds no editor**: the DevKit must be open and fully loaded; Remote Execution must be
  on (see Requirements). Windows Firewall may ask about `python.exe` the first time; allow it on private
  networks (traffic stays on this machine, multicast TTL 0).
- **"no signature matched in this DevKit build"**: the graph tools find functions by name on the graph
  editor, node, pin and library classes. If Epic placed one elsewhere or changed its parameters, the
  error lists what was tried and the docstrings. Claude can use `api_help` and `run_python` to call it
  directly in the meantime; report it so the wrapper in `src/arkmcp/editor_side.py` can be fixed.
- **Timed out**: a long command may still be running in the editor. Check the editor before repeating
  anything that changes assets.
- Server log: Claude desktop writes it to `%APPDATA%\Claude\logs\mcp-server-ark-devkit.log`.
- Without Claude: `E:\ARKDevkit\Engine\Binaries\ThirdParty\Python3\Win64\python.exe src\arkmcp_server.py --check`

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
| `configs/` | `config.json`, `config.commented.json` |
| `install.ps1` | Registers the server with the Claude desktop app |
| `tests/logic_tests.py` | Stand-alone tests |

## License

MIT, see [LICENSE](LICENSE). No third-party code; see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
