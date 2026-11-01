#!/usr/bin/env python3
"""
organize_flow.py - Sorts a Google Takeout export of Google Flow into folders.

Takeout dumps everything into Flow/media/ (<id>.jpg + <id>_metadata.json).
This script rebuilds the folder structure:
  1. Items with a collection go into that collection's folder (with hierarchy).
     The collection is read from edit_stacks.json, from the metadata files, or
     inherited from the item they were derived from (parentMediaId).
  2. Everything else goes to "<project>/_No collection/<YYYY-MM>/".
  3. Optionally (--metadata embed) the prompt and metadata are written INTO the
     images/videos so prompt-reader tools can read them by drag and drop.

Double-click mode: run without arguments. The program looks for the Flow folder
next to itself and creates a "project" folder beside it.

Command line:
    python organize_flow.py TAKEOUT_PATH OUTPUT_PATH [options]
By default it only SIMULATES. Add --apply to run for real. Standard library only.
"""
import argparse
import csv
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
NO_COLLECTION = "_No collection"
XMP_ID = b"http://ns.adobe.com/xap/1.0/\x00"
PNG_KEYS = (b"parameters", b"flow_metadata", b"XML:com.adobe.xmp")
JPEG_EXT, PNG_EXT, VIDEO_EXT = {".jpg", ".jpeg"}, {".png"}, {".mp4", ".mov", ".m4v"}


# --------------------------------------------------------------------------- helpers
def clean_name(name, default="unnamed", maxlen=80):
    n = INVALID.sub("_", (name or "").strip()).strip(" .")
    return n[:maxlen].rstrip(" .") or default


def load_json_list(flow, filename, key):
    p = flow / filename
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f).get(key, [])


def find_flow(root):
    for cand in [root, root / "Flow", root / "Takeout" / "Flow"]:
        if (cand / "edit_stacks.json").exists():
            return cand
    for p in root.rglob("edit_stacks.json"):
        return p.parent
    sys.exit(f"Could not find edit_stacks.json inside {root}")


def collection_chain(cid, collections):
    parts, seen = [], set()
    while cid and cid in collections and cid not in seen:
        seen.add(cid)
        c = collections[cid]
        parts.append(clean_name(c["name"]))
        cid = c.get("parentCollectionId") or ""
    return list(reversed(parts))


def index_media(media_dir):
    idx = {}
    for p in media_dir.rglob("*"):
        if p.is_file():
            for u in set(UUID_RE.findall(p.name)):
                idx.setdefault(u.lower(), []).append(p)
    return idx


def read_metadata(media_dir):
    meta = {}
    for p in media_dir.rglob("*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        for e in d.get("mediaEntries", []) if isinstance(d, dict) else []:
            if isinstance(e, dict) and e.get("mediaId"):
                meta[e["mediaId"].lower()] = e
    return meta


def free_path(dest, used):
    base, n = dest, 1
    while dest in used or dest.exists():
        dest = base.with_name(f"{base.stem}_{n}{base.suffix}")
        n += 1
    used.add(dest)
    return dest


def transfer(src, dst, mode):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if mode == "move":
        shutil.move(str(src), str(dst))
    elif mode == "link":
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    else:
        shutil.copy2(src, dst)


def fallback_subfolder(entry, mode):
    if mode == "none":
        return []
    date = (entry or {}).get("createdAt", "")
    if not date:
        return ["_No date"]
    return [date[:7]] if mode == "month" else [date[:7], date[:10]]


def base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


# --------------------------------------------------------------------------- metadata embedding
def txt(s):
    return CONTROL.sub("", s or "")


def get_prompt(e):
    return (e.get("generationInputs") or {}).get("prompt") or ""


def info_text(e):
    """Prompt followed by one 'Key: value' line (A1111-style, understood by most prompt readers)."""
    gi, dims = e.get("generationInputs") or {}, e.get("dimensions") or {}
    parts = []
    if e.get("modelName"):
        parts.append(f"Model: {e['modelName']}")
    if dims.get("width") and dims.get("height"):
        parts.append(f"Size: {dims['width']}x{dims['height']}")
    if gi.get("aspectRatio"):
        parts.append(f"Aspect ratio: {gi['aspectRatio']}")
    for label, key in (("Source", "source"), ("Collection", "collection"), ("Created", "createdAt")):
        if e.get(key):
            parts.append(f"{label}: {e[key]}")
    line = ", ".join(parts)
    return txt(get_prompt(e) + ("\n" + line if line else ""))


def build_xmp(e, with_json=True):
    prompt, title = txt(get_prompt(e)), txt(e.get("mediaTitle"))
    alt = lambda t: f'<rdf:Alt><rdf:li xml:lang="x-default">{xml_escape(t)}</rdf:li></rdf:Alt>'
    body = ""
    if title:
        body += f"<dc:title>{alt(title)}</dc:title>"
    if prompt:
        body += f"<dc:description>{alt(prompt)}</dc:description><flow:prompt>{xml_escape(prompt)}</flow:prompt>"
    if with_json:
        body += f"<flow:metadata>{xml_escape(txt(json.dumps(e, ensure_ascii=False)))}</flow:metadata>"
    xmp = ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
           '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
           f'xmlns:flow="https://flow.example/ns/1.0/">{body}</rdf:Description></rdf:RDF></x:xmpmeta>'
           '<?xpacket end="w"?>')
    return xmp.encode("utf-8")


def _pack_ifd(entries, ifd_off):
    entries = sorted(entries, key=lambda x: x[0])
    data_off = ifd_off + 2 + 12 * len(entries) + 4
    ifd, data = struct.pack("<H", len(entries)), b""
    for tag, typ, count, payload in entries:
        if len(payload) <= 4:
            val = payload.ljust(4, b"\0")
        else:
            val = struct.pack("<I", data_off + len(data))
            data += payload
            if len(data) % 2:
                data += b"\0"
        ifd += struct.pack("<HHI", tag, typ, count) + val
    return ifd + struct.pack("<I", 0), data


def build_exif(e):
    prompt, title = txt(get_prompt(e)), txt(e.get("mediaTitle"))
    e0 = []
    if prompt:
        b = prompt.encode("utf-8", "replace") + b"\0"
        e0.append((0x010E, 2, len(b), b))                      # ImageDescription
        x = prompt.encode("utf-16le") + b"\0\0"
        e0.append((0x9C9C, 1, len(x), x))                      # XPComment (Windows)
    if title:
        x = title.encode("utf-16le") + b"\0\0"
        e0.append((0x9C9B, 1, len(x), x))                      # XPTitle (Windows)
    uc = b"UNICODE\0" + info_text(e).encode("utf-16le")
    e1 = [(0x9286, 7, len(uc), uc)]                            # UserComment
    e0.append((0x8769, 4, 1, struct.pack("<I", 0)))            # pointer to Exif IFD
    ifd, data = _pack_ifd(e0, 8)
    exif_off = 8 + len(ifd) + len(data)
    e0[-1] = (0x8769, 4, 1, struct.pack("<I", exif_off))
    ifd, data = _pack_ifd(e0, 8)
    ifd1, data1 = _pack_ifd(e1, exif_off)
    return b"Exif\0\0" + b"II*\0" + struct.pack("<I", 8) + ifd + data + ifd1 + data1


def _app1(payload):
    if len(payload) > 65533:
        raise ValueError("metadata too large for a JPEG segment")
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def _write_atomic(path, blob):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, path)


def embed_jpeg(path, e):
    """Lossless: only inserts metadata segments, pixel data is untouched."""
    data = path.read_bytes()
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a valid JPEG")
    pos, segs = 2, []
    while pos + 2 <= len(data) and data[pos] == 0xFF:
        marker = data[pos + 1]
        if marker in (0xDA, 0xD9):
            break
        if marker == 0xFF:
            pos += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            segs.append((marker, data[pos:pos + 2]))
            pos += 2
            continue
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        seg = data[pos:pos + 2 + length]
        payload = seg[4:]
        if not (marker == 0xE1 and (payload.startswith(b"Exif\0\0") or payload.startswith(XMP_ID))):
            segs.append((marker, seg))
        pos += 2 + length
    rest = data[pos:]
    try:
        xmp = _app1(XMP_ID + build_xmp(e))
    except ValueError:
        xmp = _app1(XMP_ID + build_xmp(e, with_json=False))
    new = [_app1(build_exif(e)), xmp]
    lead = 0
    while lead < len(segs) and segs[lead][0] == 0xE0:       # keep JFIF header first
        lead += 1
    out = b"\xff\xd8" + b"".join(s for _, s in segs[:lead]) + b"".join(new) + b"".join(s for _, s in segs[lead:]) + rest
    _write_atomic(path, out)


def _itxt(key, text):
    body = key + b"\0\0\0\0\0" + text.encode("utf-8")
    return struct.pack(">I", len(body)) + b"iTXt" + body + struct.pack(">I", zlib.crc32(b"iTXt" + body) & 0xFFFFFFFF)


def embed_png(path, e):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a valid PNG")
    pos, chunks = 8, []
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        whole = data[pos:pos + 12 + length]
        pos += 12 + length
        if ctype in (b"tEXt", b"iTXt") and body.split(b"\0", 1)[0] in PNG_KEYS:
            continue
        chunks.append((ctype, whole))
    new = [_itxt(b"parameters", info_text(e)),
           _itxt(b"flow_metadata", json.dumps(e, ensure_ascii=False)),
           _itxt(b"XML:com.adobe.xmp", build_xmp(e).decode("utf-8"))]
    out = data[:8]
    for ctype, whole in chunks:
        if ctype == b"IEND":
            out += b"".join(new)
        out += whole
    _write_atomic(path, out)


def find_ffmpeg():
    return shutil.which("ffmpeg") or next(
        (str(p) for p in (base_dir() / "ffmpeg.exe", base_dir() / "ffmpeg") if p.exists()), None)


def embed_video(path, e):
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg not found (put ffmpeg.exe next to this program or in PATH)")
    tmp = path.with_name(path.stem + ".tmp" + path.suffix)
    cmd = [ff, "-y", "-loglevel", "error", "-i", str(path), "-map", "0", "-c", "copy", "-map_metadata", "0",
           "-movflags", "+use_metadata_tags",
           "-metadata", f"title={txt(e.get('mediaTitle'))}",
           "-metadata", f"description={txt(get_prompt(e))}",
           "-metadata", f"comment={info_text(e)}",
           "-metadata", f"flow_metadata={txt(json.dumps(e, ensure_ascii=False))}", str(tmp)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def embeddable(path):
    return path.suffix.lower() in JPEG_EXT | PNG_EXT | VIDEO_EXT


def embed(path, e):
    ext = path.suffix.lower()
    if ext in JPEG_EXT:
        embed_jpeg(path, e)
    elif ext in PNG_EXT:
        embed_png(path, e)
    elif ext in VIDEO_EXT:
        embed_video(path, e)
    else:
        raise ValueError("unsupported file type")


# --------------------------------------------------------------------------- main logic
def run(args):
    flow = find_flow(args.takeout)
    media_dir = flow / "media"
    if not media_dir.is_dir():
        sys.exit(f"Folder not found: {media_dir}")

    collections = {c["collectionId"]: c for c in load_json_list(flow, "collections.json", "collections")}
    stacks = load_json_list(flow, "edit_stacks.json", "editStacks")
    idx = index_media(media_dir)
    meta = read_metadata(media_dir)

    col_of, col_name, parent, stack_of, stack_prompt = {}, {}, {}, {}, {}
    n_stack = n_meta = n_inherit = 0
    for s in stacks:
        cid = s.get("collectionId") or ""
        for m in s.get("editStackMedia", []):
            mid = m["mediaId"].lower()
            stack_of[mid] = s
            if m.get("prompt"):
                stack_prompt[mid] = m["prompt"]
            if m.get("parentMediaId"):
                parent[mid] = m["parentMediaId"].lower()
            if cid:
                col_of[mid] = cid
                col_name.setdefault(cid, s.get("collection"))
                n_stack += 1
    for mid, e in meta.items():
        if e.get("parentMediaId"):
            parent.setdefault(mid, e["parentMediaId"].lower())
        cid = e.get("collectionId")
        if cid and mid not in col_of:
            col_of[mid] = cid
            col_name.setdefault(cid, e.get("collection"))
            n_meta += 1
    for mid in idx:
        cur, hops = mid, 0
        while mid not in col_of and cur in parent and hops < 20:
            cur, hops = parent[cur], hops + 1
            if cur in col_of:
                col_of[mid] = col_of[cur]
                n_inherit += 1

    print(f"Flow folder: {flow}")
    print(f"Collections defined: {len(collections)} | Stacks: {len(stacks)} | "
          f"Media IDs: {len(idx)} | With metadata file: {len(meta)}")
    print(f"Items with a collection: {len([m for m in idx if m in col_of])} "
          f"(from stacks {n_stack}, from metadata {n_meta}, inherited {n_inherit})")
    keys = Counter(k for e in meta.values() for k in e)
    print("Metadata keys:", dict(keys))
    print("Sources:", dict(Counter(e.get("source") for e in meta.values())),
          "| Types:", dict(Counter(e.get("mediaType") for e in meta.values())))
    if args.diagnose:
        return

    do_embed = args.metadata in ("embed", "embed-only")
    used, rows, summary, placed = set(), [], Counter(), set()
    stats = Counter()
    failures = []
    for mid, files in idx.items():
        e, s = meta.get(mid), stack_of.get(mid)
        project = clean_name((e or {}).get("project") or (s or {}).get("project"), "_No project")
        parts = [] if args.no_project else [project]
        cid = col_of.get(mid)
        chain = []
        if cid:
            chain = collection_chain(cid, collections)
            if not chain and col_name.get(cid):
                chain = [clean_name(col_name[cid])]
        if chain:
            parts += chain
            if args.stacks and s:
                parts.append(clean_name(s.get("stackName"), "stack"))
            summary_key, origin = "/".join(parts), "collection"
        else:
            parts.append(NO_COLLECTION)
            summary_key, origin = "/".join(parts), "fallback"
            parts += fallback_subfolder(e, args.fallback)
        folder = args.output.joinpath(*parts)
        title = clean_name((e or {}).get("mediaTitle") or (s or {}).get("stackName"), "", 60)
        newid = f"{title}__{mid[:8]}" if (args.rename and title) else mid
        prompt = get_prompt(e or {}) or stack_prompt.get(mid)
        # metadata used for embedding (fall back to the stack prompt when there is no metadata file)
        emb = e or ({"mediaId": mid, "generationInputs": {"prompt": prompt}} if prompt else None)

        is_side = lambda p: p.suffix.lower() == ".json"
        ordered = sorted(files, key=is_side)                      # media first, JSON sidecars last
        embed_failed = False
        has_embeddable = any(embeddable(p) for p in files)
        for src in ordered:
            name = src.name.replace(mid, newid) if args.rename else src.name
            status = ""
            if is_side(src) and args.metadata == "embed-only" and has_embeddable and emb and not embed_failed:
                status = "not copied (embedded instead)"
                rows.append([mid, name, (e or {}).get("createdAt", ""), title, "/".join(parts), origin, status])
                placed.add(src)
                continue
            dst = free_path(folder / name, used)
            placed.add(src)
            if args.apply:
                transfer(src, dst, args.mode)
            if do_embed and emb and not is_side(src):
                if not embeddable(src):
                    status = "embed skipped (unsupported type)"
                    stats["skipped"] += 1
                elif args.apply:
                    try:
                        embed(dst, emb)
                        status = "embedded"
                        stats["embedded"] += 1
                    except Exception as ex:
                        status, embed_failed = f"EMBED FAILED: {ex}", True
                        stats["failed"] += 1
                        failures.append((dst.name, str(ex)))
                else:
                    status = "would embed"
                    stats["embedded"] += 1
            rows.append([mid, dst.name, (e or {}).get("createdAt", ""), title, "/".join(parts), origin, status])
        if prompt and args.prompts_txt:
            t = free_path(folder / f"{newid}.prompt.txt", used)
            if args.apply:
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_text(prompt, encoding="utf-8")
        summary[summary_key] += 1

    orphans = [p for p in media_dir.rglob("*") if p.is_file() and p not in placed]
    for src in orphans:
        dst = free_path(args.output / "_Unclassified" / src.name, used)
        if args.apply:
            transfer(src, dst, args.mode)
        rows.append(["", src.name, "", "", "_Unclassified", "no id", ""])

    if args.apply:
        args.output.mkdir(parents=True, exist_ok=True)
        with open(args.output / "index.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["mediaId", "file", "created", "title", "folder", "origin", "metadata"])
            w.writerows(rows)

    print("\nSummary by folder (items):")
    for c, n in sorted(summary.items()):
        print(f"  {c}: {n}")
    print(f"\nFiles processed: {len(rows)} | files without a recognizable ID: {len(orphans)}")
    if do_embed:
        verb = "Embedded" if args.apply else "Would embed"
        print(f"{verb} metadata in {stats['embedded']} files | skipped: {stats['skipped']} | failed: {stats['failed']}")
        for n, msg in failures[:10]:
            print(f"   FAILED {n}: {msg}")
    print("Done. Index saved to index.csv." if args.apply else
          "\nSIMULATION: nothing was changed. Add --apply to run for real.")


def interactive(args):
    """Double-click mode: finds Flow next to the program and creates a 'project' folder beside it."""
    base = base_dir()
    args.takeout, args.output, args.rename = base, base / "project", True
    try:
        print("=== Google Flow Takeout Organizer ===\n")
        if args.output.exists() and any(args.output.iterdir()):
            sys.exit("The 'project' folder already exists and is not empty. Delete or rename it and run again.")
        args.apply = False
        run(args)  # simulation first
        r = input("\nType C to COPY files (keeps the originals, uses double the disk space)\n"
                  "or M to MOVE them (no extra space). Press Enter to cancel: ").strip().lower()
        if r not in ("c", "m"):
            print("Cancelled. Nothing was changed.")
        else:
            args.mode = "copy" if r == "c" else "move"
            m = input("\nWhat to do with the metadata (prompts, etc.)?\n"
                      "  1 = Keep separate _metadata.json files only (default)\n"
                      "  2 = Embed into the images/videos AND keep the .json files\n"
                      "  3 = Embed into the images/videos only (no .json files)\n"
                      "Choose 1, 2 or 3: ").strip()
            args.metadata = {"2": "embed", "3": "embed-only"}.get(m, "separate")
            args.apply = True
            print("\nWorking, this can take a few minutes...\n")
            run(args)
            print(f"\nFinished. Result in: {args.output}")
    except SystemExit as e:
        if e.code not in (None, 0):
            print("\nERROR:", e.code)
    except Exception as e:
        print("\nUNEXPECTED ERROR:", repr(e))
    input("\nPress Enter to close...")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("takeout", type=Path, nargs="?", help="(optional) if omitted, uses the folder of this program")
    ap.add_argument("output", type=Path, nargs="?")
    ap.add_argument("--apply", action="store_true", help="run for real (default: simulation only)")
    ap.add_argument("--mode", choices=["copy", "move", "link"], default="copy",
                    help="copy (default, safe), move (no extra space) or link (hard links, same disk)")
    ap.add_argument("--metadata", choices=["separate", "embed", "embed-only"], default="separate",
                    help="separate: keep _metadata.json files (default); embed: also write prompt/metadata "
                         "inside JPG/PNG/MP4 files; embed-only: embed and do not copy the .json files")
    ap.add_argument("--fallback", choices=["month", "day", "none"], default="month",
                    help="how to split items without a collection: by month (default), day, or one folder")
    ap.add_argument("--stacks", action="store_true", help="subfolder per stack inside each collection")
    ap.add_argument("--no-project", action="store_true", help="do not create a per-project folder")
    ap.add_argument("--rename", action="store_true", help="name files 'Title__shortid.jpg' instead of the UUID")
    ap.add_argument("--prompts-txt", action="store_true", help="also write a .prompt.txt per item")
    ap.add_argument("--diagnose", action="store_true", help="only print statistics about media/")
    args = ap.parse_args()
    if args.takeout is None:
        return interactive(args)
    if args.output is None:
        ap.error("OUTPUT path is required when TAKEOUT_PATH is given")
    run(args)


if __name__ == "__main__":
    main()
