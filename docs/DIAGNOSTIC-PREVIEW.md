# Photo Card Organizer 0.11.3.dev1 diagnostic preview

Not a confirmed crash fix. Not a logging-only rebuild. Includes pending move-by-default,
resume/recovery, preview, rules, and forget-library changes; see CHANGELOG.md.

1. Extract the entire ZIP to a writable local folder. Close the installed app.
2. Run PhotoCardOrganizer-Diagnostic.bat, never the nested EXE directly.
3. Choose 2 for native crash capture and type CAPTURE to consent. The launcher
   uses the Microsoft debugger already downloaded on this computer. Another
   computer needs CDB; pass its path using Start-Diagnostic.ps1 -Capture -DebuggerPath.
4. Add ONLY a disposable library copy on the internal HDD. Choose an empty
   disposable destination on the external USB HDD. Do not use real library roots.
5. Try the same move. Note the approximate time, progress, and last visible action.
6. After a crash, keep diagnostic-state intact. It contains isolated settings,
   ordinary logs, job history, and any capture folder. Share logs only after review.

Capture stops on an access violation and writes crash.dmp plus debugger.txt.
Full dumps can contain private memory and require considerable free disk space.
Nothing is uploaded. Never put dumps in Git. No Windows crash registry settings,
Defender exclusions, installation, startup entry, or desktop shortcut are changed.

The launcher starts with separate settings, no libraries, and card discovery off.
It does not import or back up your installed configuration because it never edits it.
The normal single-instance guard remains; close other copies first.

To remove the preview, close it and delete its extracted folder only after retaining
any wanted diagnostic evidence. Do not delete the source or destination test media
until you have checked both; do not manually replay source deletions after a crash.
