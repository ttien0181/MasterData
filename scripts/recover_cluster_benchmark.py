"""Recover missing public benchmark files into a checksum-gated workspace cache."""

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path

FOLDERS = {"TC6_CLUSTER_BRIDGE": "14dDLqCaE4tnkZ8w3Iu0qV_vd7K-Mz6To",
           "TC8_JUNK_PLACEHOLDER": "1UZBnh11Pa8xdAoTOJQIPXC78HceISUZw",
           "TC13_HOT_BLOCK_RETRIEVAL": "1VTbT1L5mPDMNXouKxgqyahOqvyiG1kCW"}


def contained_path(root, relative):
    root = root.resolve()
    candidate = (root / relative).resolve()
    if Path(relative).is_absolute() or not candidate.is_relative_to(root):
        raise ValueError("Manifest path escapes its bundle or recovery cache")
    return candidate


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def listing(folder):
    source = fetch("https://drive.google.com/drive/folders/" + folder).decode("utf-8")
    source = re.sub(r"\\x([0-9A-Fa-f]{2})", lambda match: chr(int(match[1], 16)), source)
    return {name: (file_id, mime.replace("\\/", "/")) for file_id, name, mime in
            re.findall(r'\["([\w-]+)",\["[\w-]+"\],"([^"\n]+)","([^"\n]+)"', source)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", default=["TC6_CLUSTER_BRIDGE", "TC8_JUNK_PLACEHOLDER"])
    args = parser.parse_args()
    datasets, cache = args.datasets.resolve(), args.cache.resolve()
    if cache.is_relative_to(datasets) or datasets.is_relative_to(cache):
        parser.error("Recovery cache and readonly dataset tree must be separate")
    report = []
    for case in args.cases:
        bundle = args.datasets / case / "100k/seed-7"
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        for relative in manifest["file_hashes"]:
            contained_path(bundle, relative)
        missing = [path for path in manifest["file_hashes"]
                   if path.startswith("input_p119/") and not contained_path(bundle, path).is_file()]
        if not missing:
            continue
        folder = FOLDERS[case]
        for directory in ("100k", "seed-7", "input_p119"):
            folder, mime = listing(folder)[directory]
            if mime != "application/vnd.google-apps.folder":
                raise ValueError("Expected public folder")
        entries = listing(folder)
        for relative in missing:
            file_id, mime = entries[Path(relative).name]
            recovery_bundle = contained_path(args.cache, case + "/100k/seed-7")
            destination = contained_path(recovery_bundle, relative)
            expected = manifest["file_hashes"][relative]
            if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
                actual = expected
                count = destination.stat().st_size
            else:
                content = fetch("https://drive.google.com/uc?export=download&id=" + file_id)
                actual = hashlib.sha256(content).hexdigest()
                if actual != expected:
                    raise ValueError("Public download does not match manifest: " + case + "/" + relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                count = len(content)
            item = {"case": case, "relative": relative, "sha256": actual, "bytes": count,
                    "source": "https://drive.google.com/file/d/" + file_id + "/view"}
            report.append(item)
            print(json.dumps(item), flush=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    (args.cache / "recovery.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
