# Native/Python live comparison — November 17, 2026

The Python MCP is functional against the open ASA DevKit 5.8.3 editor. Tests
used its real stdio JSON-RPC endpoint, standalone Python 3.11 as the external
process, and the editor's embedded Python/Blueprint APIs. The native bridge
was checked first. Both connections still answered from the same editor
process (PID 28376) after the tests; no editor crash or restart occurred.

## Results

| Capability | Native reconstructed-SDK bridge | Python MCP |
|---|---|---|
| Connection, discovery and Blueprint inspection | Passed sampled cases | Passed |
| Integer and array<string> variables/defaults | Passed, persisted readback | Passed, including defaults after disk reload |
| Remove variable, compile, save | Passed | Passed |
| Function creation | Not exercised in native follow-up | Passed |
| Event override/custom event, branch, function call, getter | Native graph editing blocked pending layout compatibility | Passed |
| Exec/data wiring, pin literals, wildcard integer promotion | Blocked in native suite | Passed |
| Disconnect one link / all links | Blocked in native suite | Passed |
| Add Sequence output pin | Blocked in native suite | Passed |
| Node move/remove | Blocked in native suite | Passed |
| Selected-node layout preserving other nodes/wires | Blocked in native suite | Passed, including wired reroutes |
| Full layout on scratch graph without knots | Blocked in native suite | Passed |
| Full layout with knots | Not invoked in native suite | Correctly refused |
| Reroutes for data and exec wires | Not invoked in native suite | Passed through palette action |
| Asset create/duplicate/rename/delete | Passed sampled native CRUD cases | Passed through run_python fallback |
| Landscape/foliage/PIE/runtime/UI tools | Known layout blockers or not exercised | Outside Python tool surface / not exercised |

The Python transcript contains 95 tool calls covering all 23 advertised tool
names. Forty-seven behavior/readback assertions passed. This is sampled
capability coverage: it does not validate every node kind, pin type, error
case, graph geometry or arbitrary run_python program.

The existing dependency-free logic tests also passed with:
`py -3.11 tests\logic_tests.py --shipped`.

The native baseline report records 44 calls across 36 distinct actions:
35 latest results passed and Canvas was unavailable. This follow-up added
integer/string-array defaults, variable removal and save/readback checks.
The native symbol audit found 26 mismatched type layouts among 59 compared
definitions. Unsafe native graph, actor/component and UI paths were not
repeated merely to reproduce known crashes. Matching data fields alone do
not establish full ABI or virtual-method compatibility.

## Warnings and limits

- Python list_graph still omits comment boxes. Creation returned a comment
  object and an independent object lookup confirmed it existed, but package
  reload logged a missing NodeGuid warning for that newly created comment.
  Comment persistence/cooking is not cleanly validated, and layout does not
  refit comments. No production Python behavior was changed in this pass.
- Reroutes are available with
  `add_node(kind="action", name="|AddRerouteNode...")`.
  There is still no dedicated bus-lane authoring tool.
- Python asset creation and CRUD used run_python, since dedicated tools are
  absent. Blueprint-specific checks used their advertised tools.
- Two exploratory API queries used nonexistent API locations, and two wiring
  requests used a stale node ID after package reload. All four were corrected;
  their failed responses remain in the transcript. Node IDs must be read from
  creation/listing results rather than inferred.
- The first cleanup attempt was refused because this test's diagnostic
  global BlueprintGraphEditor reference still held the graph. Releasing the
  diagnostic references allowed deletion. This was a test-harness lifetime
  issue; the server's graph-tool variables are local to each call.
- The native follow-up initially used the wrong default argument name and a
  JSON array where the native handler expects Unreal text. Corrected requests
  passed, including `("Alpha","Beta")` with persisted array readback.
- Native add_variable still returns an invalid rollback payload containing
  variableName in addition to name. Creation succeeded; cleanup used the
  supported explicit delete arguments.
- Programmatic deletion passing does not verify an interactive Content
  Browser deletion dialog. The native Slate/dialog and other untested tool
  paths remain subject to the existing compatibility report.

## Scope and cleanup

Fixtures were created only beneath
`/ArkMCPTesting/_MCPParityNative_20261117` and
`/ArkMCPTesting/_MCPParityPython_20261117`. Their assets, copies and rename
artifacts were deleted through editor APIs; both asset listings are empty.
Empty scratch directories may remain. The reference Blueprint
`/ArkMCPTesting/BP_McpTest1` returned identical information before and after.
No existing graphs were edited. Full layout was used only on the disposable
fixture. Claude configuration, MCP connection settings and installed native
binaries were not changed during this comparison.

## Files and evidence

The user-supplied Python context was added before testing to the native
project's `docs/PYTHON-MCP-CONTEXT.md` and referenced from the Python
project's `docs/IMPLEMENTATION-CONTEXT.md`. README files now distinguish
current results from imported historical observations. The Python repository
also contains an optional `tests/live_stdio_client.py` diagnostic client;
it performs no asset operation unless given one.

Backups and raw evidence:
`C:\Dev\Tools\ADKUEPluginBuilds-Dev\Backups\parity-tests-20261117`

- Native-README.md and Python-README.md: pre-edit README backups.
- Source.Runtime.Engine.Classes.EdGraph.EdGraphNode.h.patch and EdGraphNode.h:
  read-only SDK audit backups; these files were not changed.
- native-parity.json: native follow-up requests/results.
- python-parity.json: all stdio requests/results and server diagnostics.
- python-assertions.json and verify_transcript.py: 47 readback checks.
- COMPATIBILITY-PARITY.md: this report.

Earlier native audit:
`C:\Dev\Tools\ADKUEPluginBuilds-Dev\Backups\tooling-checks-20261117\COMPATIBILITY-REPORT.md`

No user action is required for the connections tested here. Python is the
verified path for the graph-editing capabilities exercised in this pass.

