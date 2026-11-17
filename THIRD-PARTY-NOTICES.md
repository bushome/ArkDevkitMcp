# Third-party notices

ArkDevkitMcp contains no third-party code and needs no packages beyond the Python standard library.

It runs on the Python 3.11 that ships with the ARK: Survival Ascended DevKit, and talks to the editor
through Unreal Engine's Python remote execution protocol. The protocol is re-implemented in
`src/arkmcp/protocol.py`; none of Epic Games' `remote_execution.py` is copied into this repository.
The Unreal Editor, its Python plugin and the DevKit are Epic Games' and Studio Wildcard's software under
their own licenses and are not distributed here.
