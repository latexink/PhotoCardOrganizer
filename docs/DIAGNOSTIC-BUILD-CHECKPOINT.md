# Diagnostic build checkpoint - 2026-09-26

## Resolved 2026-09-27

Capture commands validated with an actual null-address write in a disposable
Python child. CDB caught first-chance 0xc0000005 and successfully saved a
42,268,994-byte full-memory dump. The previous faulthandler signal probe did not
exercise this exception path. This validates capture, NOT a fix of the app crash.
Launcher now removes stale GUI-check reports and detects debugger failures without
dumps. ZIP/hash refreshed; the original checksum below is historical.
The launcher also stops following child processes: debugging console helpers
captured an unrelated conhost access violation and prematurely ended the smoke
test. The onedir app is a single process. Fresh packaged GUI checks now pass with
capture enabled. Capture still stops at the app's first access violation so that
evidence is preserved even if another handler might have caught it.

## Original checkpoint

Prepared artifacts/PhotoCardOrganizer-Diagnostic-0.11.3.dev1.zip (55,947,694 bytes).
SHA-256: 1bebfc3ee9124183a9025d0062f3a293f7a9457c681ddf7291cc6211c0208fbe

243 Windows tests ran; 242 passed, one expected skip. Packaged GUI smoke passed.
Normal and capture launcher GUI-check modes passed. PDF diagnostic supplement and
cover rendered and visually checked. Defender custom scans of the bundle and ZIP
completed with no matching detections; protection remained enabled.

Remaining blocker: full-memory dump creation has NOT been validated end to end.
The synthetic fault probe under CDB terminated without producing crash.dmp. Check
build/crash-analysis/capture-selftest.txt and capture-selftest.py, then retry in an
appropriate isolated environment. Do not call capture verified or the original
native crashes fixed. No real installation/settings/libraries were modified.

Ordinary logs can be tested with launcher option 1, using disposable media only.
Before recommending option 2, verify the debugger actually captures a deliberate
native fault. ZIP excludes diagnostic-state, debug tools, and dumps. CDB is found
at this computer's existing debugger path or supplied via -DebuggerPath.

Stopped near the user-requested 5% five-hour allowance checkpoint. No reset credit,
commit, GitHub push, installer build, or real installation change performed.
