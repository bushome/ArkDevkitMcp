# Python MCP implementation context

The Python MCP is a functional path as of November 17, 2026. Current connection
evidence and configuration are documented in the README. Historical UE 5.8.3
signatures, test coverage, known gaps and required graph-layout conventions
were imported into the native compatibility project at:

`C:\Dev\Tools\ADKUEPluginBuilds-Dev\docs\PYTHON-MCP-CONTEXT.md`

The native and Python suites use disposable content in the ArkMCPTesting mod
and share capability-level assertions. The native suite runs first; Python
checks follow through the real stdio server and editor remote-execution path.

Never relayout existing hand-arranged graphs. Only position newly created
nodes; full layout must refuse a graph containing reroute knots. Existing
CloudBshelf and BP_McpTest1 assets are read-only reference material for testing.

Do not carry native C++ header/ABI failures over as assumptions about Python:
the editor executes its own Python graph API using its own engine binaries.
Use the editor's actual api_help/signatures when resolving differences.

The live comparison is complete: [November 17 validation](VALIDATION-2026-11-17.md)
records 47 passing readback checks across all 23 advertised tool names, plus
the remaining comment-node warning and native ABI blockers. Reroute creation
and wiring work through the existing palette action.
