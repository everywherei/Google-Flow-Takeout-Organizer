# Flow Takeout Organizer

Sorts a **Google Takeout** export of **Google Flow** back into folders, and optionally writes each item's prompt and metadata **inside** the image or video so prompt-reader tools can read it by drag and drop.

Google Takeout dumps every generated item into a single `Flow/media/` folder (`<id>.jpg` + `<id>_metadata.json`), losing the collections you had in Flow. This tool rebuilds the folders from the JSON files in the export.

> Not affiliated with or endorsed by Google. "Google Flow" and "Google Takeout" are trademarks of their owners.

## Features

- Rebuilds the **collection hierarchy** (`Parent/Child/...`) from `collections.json`.
- Finds each item's collection from `edit_stacks.json`, from its `_metadata.json`, or inherits it from the item it was derived from (`parentMediaId`).
- Items without a collection go to `<project>/_No collection/<YYYY-MM>/` (by month, day, or one folder).
- **Embed metadata** into JPG, PNG and MP4 files (lossless: pixels / streams are not re-encoded).
- Optional readable file names: `Title__shortid.jpg` instead of a UUID.
- **Simulation by default** and an `index.csv` listing where everything went.
- Double-click mode, no paths needed. Standard library only (Python 3.8+).

## Quick start (Windows, double-click)

1. Download `organize_flow.py` and `RUN.bat`.
2. Put both next to the `Flow` folder (inside the `Takeout` folder), or put a built `OrganizeFlow.exe` there.
3. Double-click `RUN.bat` (or the `.exe`).
4. Read the simulation summary, then choose **C** (copy) or **M** (move), and what to do with the metadata.
5. Your sorted files appear in a new `project` folder next to it.

### Build a standalone `.exe`

Double-click `BUILD_EXE.bat` (needs Python and internet once). It creates `dist\OrganizeFlow.exe`; copy it into your Takeout folder.

## Command line

```
python organize_flow.py TAKEOUT_PATH OUTPUT_PATH [options]
```

`TAKEOUT_PATH` can be the `Takeout` folder, the `Flow` folder, or the folder containing them. Without `--apply` nothing is changed.

| Option | Description |
| --- | --- |
| `--apply` | Run for real (default is a simulation). |
| `--mode copy\|move\|link` | `copy` (default, keeps originals), `move` (no extra disk space), `link` (hard links, same disk). |
| `--metadata separate\|embed\|embed-only` | `separate` keeps the `_metadata.json` files (default). `embed` also writes the metadata into the files. `embed-only` embeds and does not copy the `.json` files. |
| `--fallback month\|day\|none` | How to split items that have no collection. |
| `--stacks` | Subfolder per edit stack inside each collection. |
| `--no-project` | Do not create a per-project top-level folder. |
| `--rename` | Name files `Title__shortid.ext`. |
| `--prompts-txt` | Also write a `.prompt.txt` per item. |
| `--diagnose` | Only print statistics about the export. |

Example:

```
python organize_flow.py "C:\Takeout" "C:\Sorted" --rename --metadata embed --apply
```

## Where embedded metadata goes

| Format | Fields written |
| --- | --- |
| JPG | EXIF `ImageDescription`, `UserComment`, Windows `XPTitle`/`XPComment`, and XMP (full JSON in `flow:metadata`). An existing EXIF block is replaced. |
| PNG | `parameters` (prompt + a `Key: value` line, Stable-Diffusion style), `flow_metadata` (full JSON), XMP. |
| MP4/MOV | `title`, `description`, `comment`, `flow_metadata` via `ffmpeg` (must be in `PATH` or next to the program). |

Compatibility depends on the reader tool; the standard fields above are the ones most of them look at.

## Known limitations

- **Takeout does not export every item-to-collection link.** Items whose collection is not present in any JSON file cannot be placed in it; they are sorted by date instead.
- `characters.json` has no link to media, so characters are not used.
- Folder names replace characters Windows forbids (for example `:` becomes `_`).
- The format of Google's export may change; open an issue with a *redacted* sample if something stops working.

## Privacy

Your export contains your private prompts and images. Never commit it: the included `.gitignore` excludes the usual Takeout folders. Redact prompts before posting samples in issues.

## License

[MIT](LICENSE)
