# Remaining-work validation — November 17, 2026

This follows the historical [47-check comparison](VALIDATION-2026-11-17.md).
The Python server now advertises **27 tools**. Live checks use the real stdio
server, remote execution and the open ASA DevKit UE 5.8.3 editor.

## Implemented and verified

- Comments appear in graph listings with stable valid GUIDs, positions and
  dimensions. New comments receive the GUID missing from this DevKit's native
  graph API. Saving and reloading preserves the GUID and bounds.
- `refit_comment` adjusts one explicitly selected comment around explicitly
  selected nodes. It preserves all member positions. Bounds use the existing
  visual size estimates because the engine reports placeholder K2 node sizes.
- `create_blueprint` creates an unsaved asset through Unreal's factory and
  refuses replacement of an existing asset.
- `add_reroute` and `route_bus` provide explicit knot placement and a data bus.
  Bus taps must be unconnected and type compatible without automatic conversion;
  execution/delegate pins and node overlaps are refused. A failed connection
  removes the newly created knots. Existing nodes and wiring are preserved.
- Component-bound event creation now resolves the actual component template
  through Unreal's subobject subsystem. The 5.8 API accepts a component object,
  not its variable-name string. Duplicate handles are deduplicated by object path.
- Every one of the 14 advertised `add_node` kinds passed creation/readback checks.
  A StaticMesh component overlap event also compiled successfully.
- Map, set, object-reference and Vector struct defaults passed manual reflection
  checks. The struct type spelling is `struct:/Script/CoreUObject.Vector`.

## Connection behavior

Unreal's Python remote-execution implementation supports one active command
channel per editor. Updated local MCP clients coordinate through a process lock
and close their command channel after each tool call. Helper upload and the call
share one lock. Two independent stdio servers ran the 28-check suite concurrently
and both passed, with their fixtures removed.

Commands are no longer automatically resent after an uncertain send/receive
failure. Errors explicitly require state readback before retrying a mutation.
Logic tests verify the send happens once and failed connections clear their state.
This coordination covers updated ArkDevkitMcp clients; unrelated or old remote
execution clients do not participate in the lock.

## Evidence and limits

- `tests/logic_tests.py --shipped`: passed.
- `tests/live_acceptance.py`: **39/39 checks passed** on the rebuilt native editor.
  This includes persistence, all node kinds, refusal cases, injected connection
  failure cleanup, mixed comment/K2 deletion and the reroute layout guard.
- Two concurrent suites: **28/28 each**. Earlier 28-check runs are retained too.
- Final native/Python persistence and actor comparison: **16/16 readback checks**.
  Native CanEverTick is compiler-derived; the connected Tick graph survives a
  restart, while a direct flag-only edit can be overwritten.
- All suites create unique assets under `/ArkMCPTesting/_MCPAcceptance`, then
  delete only their fixtures. Existing BP_McpTest1 and CloudBshelf are reference
  assets; they are never relaid out or saved by these checks.
- One earlier editor crash occurred in Python VM evaluation. The captured stack
  does not establish its cause. After restoring the native SDK patches, repeated
  suites and component/event creation passed. This is evidence of the tested
  paths, not a claim that arbitrary editor Python cannot crash.
- Hand-arranged graphs containing knots still refuse full relayout. Bus routing
  is explicit placement, not an automatic PLC diagram router; it does not
  guarantee that wires avoid all intervening nodes.
- Native C++ compatibility remains separate. Its audit still finds 15 unmatched
  class/struct layouts; native PIE, physics, terrain, foliage, PCG, rendering and
  modal inspection have not been certified by the Python results.

Raw transcripts, build logs, layout comparisons and pre-edit backups are local:

`C:\Dev\Tools\ADKUEPluginBuilds-Dev\Backups\remaining-work-20261117`

The native follow-up is `COMPATIBILITY-FOLLOWUP.md` in that directory. No Claude
or MCP connection configuration was changed. Restart existing Python MCP clients
after updating to discover the four added tools and use connection coordination.

Both additional persistent scratch Blueprints were deleted through Unreal's
asset API. Final inspection found no test assets in the three fixture roots,
no unsaved map/content packages, and the editor open on TestMapArea.
