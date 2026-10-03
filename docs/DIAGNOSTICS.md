# Diagnostics

## Using the controls

Open **Help & about > Diagnostics**. Normal error recording is always enabled
when the log folder is writable. Select **Detailed diagnostic logging**, then
**Save settings**, before reproducing a problem. Turn it off afterward.

**Open log folder** opens this client's diagnostic folder. **Export diagnostic
report** creates a ZIP with logs only, not media, configuration, or memory dumps.
Known home/library root paths are replaced, but filenames, messages, and other
paths may remain. Review the archive before sharing it.

A notice in Activity and Help & about indicates when the preceding session did
not finish cleanly. This may mean a crash, forced close, unhandled exception, or
power interruption; it is not a diagnosis. It does not initiate file recovery or
automatically resume a transfer.

## Evidence and overhead

- `application.log`: maximum 2 MiB, with three rotated backups. Includes session
  identity, app/Python version, platform, errors, Qt warnings, and operation IDs.
- Detailed mode adds library-dialog open/close and table-model reset boundaries,
  with object identifiers and row counts. No per-cell or per-timer-event tracing.
- `native-crash.log`: Python thread traces from the native fault handler when
  available. This is not a full-memory dump and cannot replace native debugging.
- `native-crash.previous.log`: retained evidence from the preceding captured
  native fault. Clean launches do not replace it with an empty report header.
- `session-active`: a tiny per-launch marker, removed after a clean shutdown.
  No continuous heartbeat writes are needed.

An explicit `--config` path keeps diagnostics alongside that configuration.
No checksum passes, extra media reads, registry changes, uploads, or debugger
attachment are triggered by enabling detailed logging. An externally enabled
Python fault handler is left in place rather than taken over.

## Full-memory crash capture

Full-memory capture is intentionally not an automatic setting in this pass.
It can consume substantial disk space and contain private in-memory data.
Use the supervised, disposable-client procedure in
[the 0.11.2 investigation](CRASH-INVESTIGATION-0.11.2.md), with the preserved
matching binaries and symbols. Do not attach to a real running migration or
silently enable global Windows crash-dump settings. Keep dumps out of Git and
support-report ZIP files.

## Validation checkpoint: 2026-09-25

- Diagnostics and Qt workflow suite: 61 tests passed before consolidating the
  duplicate native-fault test into the existing process-safety suite.
- Final diagnostics/process-safety suite: 11 tests passed, including an isolated
  native crash, abrupt termination detection, custom-config CLI initialization,
  and 18 repeated dialog/preview/cancellation checks in a child process.
- Help & about visually inspected at 980x660 and 1440x900 using disposable state.
- No reproduction or fix of the two original native crashes is claimed.
- No installer build, installation change, commit, or push was performed.
- Linux execution and packaged-build verification remain outstanding.
