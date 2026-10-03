# PhotoCardOrganizer 0.11.2 native crash investigation

Date: 2026-09-25. Status: both dumps symbolized; root cause and fix NOT established.

## Scope and preservation

Read-only examination of the two preserved minidumps and installed binaries.
No photo libraries, user settings, installed application, or existing source
changes were modified. No release/build/install or speculative repair was made.
Debugger downloads and output are confined to ignored `build/crash-analysis`.
This document is the only new source-controlled-path file from this pass.

Repository: `C:\Users\vroom\Documents\Codex\2026-07-12\i\work\PhotoCardOrganizer-ui`.
Evidence directory: `C:\Users\vroom\Documents\Codex\2026-09-20\w\outputs\PhotoCardOrganizer-crash-evidence`.
Use its HANDOFF.md and event exports alongside this report.

## Matching build and tooling

- Release source: `40aca8a919912cc8b480bd4c86f5a187adbfd6aa` (0.11.2).
  The current dirty working tree is newer and must not be treated as that build.
- Installed application: `C:\Users\vroom\AppData\Local\Programs\PhotoCardOrganizer`.
- Executable SHA-256, checked against the preserved PyInstaller executable:
  `E6DCCFCACE254C08EBDB460D984E17173E173B2653879F0FEF21F846DAB73830`.
- Installed Qt6Core.dll SHA-256:
  `D6A3C37C5263C5D418FC0E80B722BC9FCA7C0859E6F635F9624A2FDE00BDC653`.
  Version 6.11.2; image timestamp `0x6a7cf959`.
- Microsoft CDB 10.0.29617.1000 extracted locally from the official WinDbg
  package. No system debugger installation or registry changes were needed.
- Exact Qt private PDBs for Core, Gui, and Widgets obtained from Qt's official
  6.11.2 MSVC2022 x64 debug-info repository, archive:
  `6.11.2-0-202608131017qtbase-Windows-Windows_11_24H2-MSVC2022-Windows-Windows_11_24H2-X86_64-debug-symbols.7z`.
  CDB accepted matching private PDBs without forcing a symbol mismatch.
- Windows symbols retrieved from Microsoft's symbol server. Python private
  symbols were not obtained; no Python source line is identified by these dumps.

## Crash 1: table-header reset

Dump: `PhotoCardOrganizer.exe.45816.dmp` (8,075,260 bytes).
SHA-256: `AC2452CEEEB266DF39CEE1B9610F4496EAC1A5EE286C837ED686DE8F17F9E9E6`.
Event 30503: 2026-09-17 03:39:55 UTC. Main UI thread 4484 (0x1184).
Process uptime at dump: approximately 7 hours 9 minutes.

The symbolized stack, innermost first, includes:

1. Atomic reference decrement through `QSlotObjectBase::destroyIfLastRef`.
2. `QQueuedMetaCallEvent::~QQueuedMetaCallEvent` and its deleting destructor.
3. `QItemSelectionModel::reset`.
4. `QAbstractItemView::reset`, `QHeaderView::reset`.
5. `QAbstractItemView::timerEvent`, normal widget event delivery and event loop.

Fault: write access violation at Qt6Core+0xeb703 (`lock xadd [r10], eax`).
The target is an executable-code address in QtWidgets, not a valid writable
reference counter. Multiple callable types share that linker-folded address;
its QSystemTrayIcon symbol alias is NOT evidence that the tray icon caused it.

Disassembly of `QItemSelectionModel::reset` shows a virtual call through vtable
offset 0x70 immediately before return offset +0x44. The unwind enters the
queued-event deleting destructor from this call. That is an unexpected object
dispatch, consistent with stale/reused object storage or memory corruption.
It is not enough to identify who deleted or overwrote the selection model.

## Crash 2: zero-delay timer inside a modal dialog

Dump: `PhotoCardOrganizer.exe.40844.dmp` (7,598,383 bytes).
SHA-256: `241C236845C787F78049419ABF68262290322D9765C0728D431E94A8311A5F9C`.
Event 30505: 2026-09-17 04:01:59 UTC. Main UI thread 39644 (0x9adc).
Process uptime at dump: approximately 21 minutes 50 seconds.

Fault: read access violation at Qt6Core+0xa5f4a,
`QCoreApplication::notifyInternal2+0x2a`.
Receiver: `0x00000213c36a1580`.
Disassembly and matching PDB type layouts establish this chain:

1. Receiver +0x08 supplies its private-data pointer.
2. Private data +0x58 supplies `QObjectPrivate::threadData`.
3. That pointer is `0x6001000360010002`, not a valid canonical x64 address.
4. Reading `QThreadData::requiresCoreApplication` at +0x97 faults.

The caller is the zero-timer-event branch of `QEventDispatcherWin32::event`,
forwarding a QTimerEvent to a registered receiver. A nested `QDialog::exec`
appears farther down the stack, entered through Python/PySide following a
button-click signal. This does NOT identify which dialog was open, or establish
that the dialog itself was the invalid receiver. Qt item views use internal
zero-delay timers even when the app's explicit timers have nonzero intervals.

## What is and is not established

Both are genuine native GUI-thread memory access violations with invalid object
state during timer-driven work. Object lifetime/reentrancy and overwritten
memory are investigation targets, not proven diagnoses. A shared root cause is
possible but unproven; the crashing module does not establish a Qt library bug.

The previous COM-callback hypothesis came from raw stack-word scanning. Proper
unwinding does not support it. Do not use it to justify COM or Windows repairs.

These are small minidumps. The relevant heap objects are absent, so debugger
memory-read errors do not prove those objects had been freed. Their identities,
ownership chains, and allocation/free history cannot be recovered here.
No evidence ties these exceptions to photo contents, checksums, or disk I/O.

The existing uncommitted preview invalidation fix addresses a separate Python
bug; it is not a validated fix for either native crash. No controlled reproduction
or regression test for these two native faults has succeeded in this pass.

## Reusable local evidence

Under `build/crash-analysis`:

- `45816-symbolized.txt`, `40844-symbolized.txt`: authoritative symbolized stacks.
- `45816-objects.txt`: reset disassembly, reference-counter target analysis.
- `40844-objects.txt`: documented missing heap memory.
- `40844-timer-path.txt`, `40844-types.txt`: second crash pointer chain and types.
- `inspect-dump.txt`: repeatable debugger command file.
- `tools/windbg/amd64/cdb.exe`, `qt-symbols`, `symbols`: reusable tools/cache.

Early `*-initial.txt` and `45816-debugger.txt` contain incomplete-symbol results;
do not reuse their WRONG_SYMBOLS buckets as diagnoses. `45816-vtable.txt` is an
unsuccessful optional symbol-expression probe, not evidence.
Keep dumps/debugger logs local: they can expose paths and in-memory user data.
Do not commit downloaded tools, symbol caches, or memory dumps.

## Next bounded investigation

1. Preserve this matching executable and symbol cache before updating anything.
   Export release source with `git archive` into a disposable directory; do not
   check out over the user's current uncommitted work.
2. Reproduce with an explicit disposable configuration and synthetic libraries,
   monitoring disabled. Exercise opening/closing merge, migration, reorganization,
   and integrity dialogs while table models refresh, including cancellation and
   repeated dialog entry. Use the matching runtime first, not an upgraded Qt.
3. Run that isolated client under CDB. Stop on access violations (`sxe av`);
   capture a full user-mode dump (`.dump /ma <local-output-path>`) at the fault,
   plus `.ecxr`, `kv`, and all-thread stacks. Do not continue corrupt file work.
   Do not enable global crash settings or attach to an active real transfer.
4. Identify the receiver's native type, parent, Python wrapper, registered timer,
   and selection-model ownership from the full dump. Correlate with narrowly
   scoped dialog/model lifecycle logging and Python faulthandler output.
5. If it is reproducible, compare one controlled lifecycle change at a time and
   add a stress/regression test before selecting a fix. Only then compare another
   supported Qt/PySide version if evidence points to a binding/runtime defect.

Budget checkpoint: latest check during this pass showed 39% five-hour allowance
and 9% weekly allowance remaining. No reset credit was consumed. The evidence
above avoids repeating the debugger setup or broad speculative source changes.

## Bounded reproduction pass: 2026-09-26

The user recalls attempting a move immediately before the crash. Whether the
source/destination were on the same filesystem, different drives, or a network
location remains to be clarified. Do not assume a small same-drive test covers
the original environment or library size.

Exported release commit `40aca8a919912cc8b480bd4c86f5a187adbfd6aa` to
`build/crash-analysis/release-0.11.2` using git archive, without altering the
working tree. The local Python is 3.12.14; Qt/PySide are 6.11.2. Compared SHA-256
for Qt6Core.dll, Qt6Widgets.dll, QtCore.pyd, and Shiboken.pyd between the local
environment and installed application: all four pairs match exactly.

Ran release-source tests under CDB with the Windows Qt platform plugin (not
offscreen), using temporary application-state directories, synthetic media, and
hidden modal dialogs. Access violations were configured to capture a full dump
locally and stop. No native access violation occurred, so no new dump was made.
This is source/runtime testing, not validation of the PyInstaller executable.

Results:

- 18 checks passed in 63.461 seconds: repeated migration preview invalidation,
  integrity selection/verification, merge processing, cancellation, reorganization
  preview, modal table resets, and migration processing.
- Full Libraries-page migration passed against release source (1 test, 5.751s):
  destination selection, real preview, confirmation, worker processing, final
  destination bytes, saved configuration, and clearing the busy state.
- The same Libraries-page migration passed against the current pending source
  (1 test, 7.121s). No settings or media outside disposable test state were used.
- Original 0.11.2 migration retains sources by design; the pending implementation
  defaults to moving. Do not confuse identical UI completion checks with identical
  underlying transfer behavior.

The first expanded stress harness was stopped after appearing stalled. A bounded
retry identified slow repeated global theme application in unittest class setup,
not either original access violation. Grouping cases and reusing the configured
QApplication removed that harness overhead. No app theme change was made on this
basis. All launched debugger/test sessions were subsequently ended.

Local reproduction files (ignored build output):

- `reproduce-release.py`: explicit source selection, isolated state, 90-second
  watchdog; `--move-only` exercises the Libraries-page migration.
- `repro-capture.txt`: debugger exception/capture commands.
- `release-repro-debugger.txt`: module/debugger record for the 18-check pass.
- `release-move-console.txt`, `release-move-debugger.txt`: release UI migration.
- `current-move-console.txt`, `current-move-debugger.txt`: pending-source UI migration.

No native-crash fix, installer, installation change, commit, or push was made.
Next economical step: use the user's drive topology and approximate operation
sequence to refine the disposable reproduction. If that still does not reproduce,
prepare an explicitly approved diagnostic build/capture session; do not keep
repeating the same passing synthetic tests or change Qt versions speculatively.
