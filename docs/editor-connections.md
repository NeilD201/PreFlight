# Editor connections

Open **Editors** in the top navigation. Select the original local project files,
or enter their full paths, one per line. The local server remembers those paths.
It checks for saves every three seconds and waits for two stable observations
before recording a changed content hash. Typical detection is three to six
seconds; the browser refreshes connection status every five seconds.

This is saved-file monitoring, not a universal native CAD integration.

| Editor | Local save monitoring | Analysis |
| --- | --- | --- |
| KiCad | Original project, schematic sheets, PCB, rules | Existing KiCad review engine |
| Autodesk EAGLE | XML `.sch`, `.brd`, `.lbr` | Native reader not implemented |
| Fusion Electronics | Experimental PreflightSync add-in exports on save | Native reader not implemented |
| EasyEDA | Local `.json` / `.epro` files only | Native reader not implemented |
| Altium Designer | Local project, schematic sheets, PCB | Native reader not implemented |
| OrCAD / Allegro | Local `.opj`, `.dsn`, `.brd` | Native reader not implemented |
| Siemens PADS | Local `.prj`, `.sch`, `.pcb`, `.asc` | Native reader not implemented |

## What updates automatically

- File fingerprints, detected revision and connection status.
- An outdated-review notice when the connected KiCad source differs from the
  source hashes used in the current report.
- Detection after reopening Preflight, including saves made while it was closed.
- Recovery when a temporarily missing file returns to its original path.

Click **Review saved files** to run the review with the current settings. File
monitoring itself makes no AI requests, exports, network calls or source edits.
It stores paths and hashes in the workspace SQLite database, not duplicate PCB
files. Disconnect removes the connection and leaves design files untouched.

## What still needs an adapter

A downloaded EasyEDA project or manually exported Fusion design is a snapshot.
It does not stay synchronized with the cloud editor. Fusion now has a separate
experimental add-in; EasyEDA Standard and Pro still need dedicated adapters.

### Fusion setup

1. In **Editors**, choose **Autodesk Fusion Electronics → Set up Fusion sync**.
2. Open the intended saved Electronics project in Fusion (linked PCB and schematic).
3. In Fusion's **Scripts and Add-Ins**, add the displayed `PreflightSync` folder
   and run it. The installed Fusion version must expose `adsk.electron` and the
   Electronics export API. Autodesk labels this API as a preview in the installed SDK.
4. Save both documents. Preflight lists the exported generation after both exports succeed.

The add-in writes only to its Preflight export directory. It uses no network
requests or credentials. It binds the selected document identities on first run;
saves to unrelated designs are ignored. Unsaved changes in either document block
export. A complete generation is published with an atomic manifest; a failed
export leaves the previous generation available. Only the latest two generations
are retained. Disconnect disables future exports; existing snapshots remain local.

The add-in has been tested with simulated Fusion export managers, including failed
exports, unsaved files, project isolation and retention. A live save/export test in
Fusion remains outstanding. Sync support does not enable native analysis or edits.

Direct cloud-editor integration must pair a specific project, export on save,
preserve document identities and revisions, and report failures. Its native reader
must preserve schematic nets, component identities, board geometry and units.
An editor dropdown alone does not provide those capabilities.

Official API references for the next adapters:

- [EasyEDA Standard source API](https://docs.easyeda.com/en/API/3-API-List/)
- [Fusion documentSaved event](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_Application_documentSaved.htm)
- [Fusion Electronics export API](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ElectronicsExport.htm)
- [KiCad project import documentation](https://docs.kicad.org/9.0/en/kicad/kicad.html)

## Boundaries

The existing localhost Host/Origin/CSRF checks remain in place. No browser editor
is granted cross-origin access. Selected files must be regular, nonempty files;
symlinks and unsupported extensions are rejected. Limits are 50 files per
connection, 100 MB per file, 250 MB total, and 12 connections. Only explicitly
selected files are watched; add hierarchical sheets and local rules when needed.
Referenced libraries and other dependency files are not discovered automatically.

Tests use synthetic designs and actual filesystem saves. These connections have
not completed an in-editor save test in EasyEDA, Fusion, Altium, OrCAD or PADS. Native analysis and
bidirectional edits for these editors are not implemented.
