"""
organize_by_stage.py

Maps each gdc_downloads folder to its AJCC pathologic stage using:
  manifest → TCGA barcode → clinical dedup TSV → stage

Then creates symlinked directories organized by stage so you can
browse PDFs by stage and hand-pick your final 150 report list.

Output structure:
  stage_organized/
    Stage_IIA/
      TCGA-BH-A18H  -> ../../gdc_downloads/62c32125-.../
      ...
    Stage_IIB/
      ...
    not_reported/
      ...
    stage_summary.txt
    folder_stage_map.tsv   (folder_id, tcga_barcode, stage)
"""

import os
import csv
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
GDC_DOWNLOADS_DIR  = "/home/a/akashsingh/BRCA-Restaging/gdc_downloads"
MANIFEST_FILE      = "/home/a/akashsingh/BRCA-Restaging/gdc_manifest_brca_pathreports.txt"
CLINICAL_DEDUP_TSV = "/home/a/akashsingh/BRCA-Restaging/temp-python-scrpt/brca_clinical_dedup.tsv"
OUTPUT_DIR         = "/home/a/akashsingh/BRCA-Restaging/stage_organized"

SUBMITTER_ID_COL   = "cases.submitter_id"
STAGE_COL          = "diagnoses.ajcc_pathologic_stage"
# ──────────────────────────────────────────────────────────────────────────────


def load_manifest(path):
    """file_id -> TCGA barcode (e.g. TCGA-BH-A18H)"""
    mapping = {}
    with open(path) as f:
        next(f)  # skip header
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= 2:
                file_id = parts[0].strip()
                barcode = parts[1].split(".")[0].strip()
                mapping[file_id] = barcode
    return mapping


def load_clinical_stages(path):
    """TCGA barcode -> AJCC stage"""
    mapping = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            barcode = row.get(SUBMITTER_ID_COL, "").strip()
            stage   = row.get(STAGE_COL, "").strip()
            if barcode:
                mapping[barcode] = stage
    return mapping


def stage_to_dirname(stage: str) -> str:
    """Convert stage string to a safe directory name."""
    if not stage or stage in ("'--", "--", ""):
        return "not_reported"
    return stage.replace(" ", "_").replace("/", "_")


def main():
    print("Loading manifest...")
    file_id_to_barcode = load_manifest(MANIFEST_FILE)

    print("Loading clinical stages...")
    barcode_to_stage = load_clinical_stages(CLINICAL_DEDUP_TSV)

    # Build mapping: folder_id -> (barcode, stage)
    folders = sorted(d.name for d in Path(GDC_DOWNLOADS_DIR).iterdir() if d.is_dir())
    print(f"Found {len(folders)} folders in gdc_downloads\n")

    folder_map = []
    for folder_id in folders:
        barcode = file_id_to_barcode.get(folder_id, "UNKNOWN")
        stage   = barcode_to_stage.get(barcode, "not_reported")
        folder_map.append((folder_id, barcode, stage))

    # Create output directory structure
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # Remove existing symlinks to allow re-running cleanly
    for existing in Path(OUTPUT_DIR).iterdir():
        if existing.is_dir() and existing.name not in (".", ".."):
            for link in existing.iterdir():
                if link.is_symlink():
                    link.unlink()

    # Create stage subdirs and symlinks
    stage_counts = {}
    for folder_id, barcode, stage in folder_map:
        stage_dir_name = stage_to_dirname(stage)
        stage_dir = Path(OUTPUT_DIR) / stage_dir_name
        stage_dir.mkdir(exist_ok=True)

        # Use TCGA barcode as the symlink name (human-readable)
        link_name = barcode if barcode != "UNKNOWN" else folder_id
        link_path = stage_dir / link_name

        # Target: relative path back to gdc_downloads folder
        target = Path(GDC_DOWNLOADS_DIR) / folder_id

        if link_path.exists() or link_path.is_symlink():
            link_path.unlink()
        link_path.symlink_to(target)

        stage_counts[stage_dir_name] = stage_counts.get(stage_dir_name, 0) + 1

    # Write folder->stage TSV
    tsv_path = Path(OUTPUT_DIR) / "folder_stage_map.tsv"
    with open(tsv_path, "w", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["folder_id", "tcga_barcode", "stage", "stage_dir"])
        for folder_id, barcode, stage in folder_map:
            writer.writerow([folder_id, barcode, stage, stage_to_dirname(stage)])

    # Write summary
    summary_path = Path(OUTPUT_DIR) / "stage_summary.txt"
    with open(summary_path, "w") as f:
        f.write("=" * 60 + "\n")
        f.write("STAGE DISTRIBUTION — gdc_downloads folders\n")
        f.write("=" * 60 + "\n\n")
        total = sum(stage_counts.values())
        f.write(f"  {'Stage directory':<30} {'Count':>6}  {'%':>6}\n")
        f.write(f"  {'-'*30} {'-'*6}  {'-'*6}\n")
        for stage_dir, count in sorted(stage_counts.items()):
            pct = 100 * count / total
            f.write(f"  {stage_dir:<30} {count:>6}  {pct:>5.1f}%\n")
        f.write(f"\n  {'TOTAL':<30} {total:>6}\n")

    print("Stage distribution:")
    with open(summary_path) as f:
        print(f.read())

    print(f"Symlinks created in : {OUTPUT_DIR}/")
    print(f"Folder-stage map    : {tsv_path}")
    print(f"\nBrowse stages with:")
    print(f"  ls {OUTPUT_DIR}/")
    print(f"  ls {OUTPUT_DIR}/Stage_IIA/ | head -20")


if __name__ == "__main__":
    main()
