"""
select_cohort.py

Selects a balanced 150-case cohort for the BRCA restaging ML pilot study.

Strategy: 4-class balanced sampling
  Class 0 — Stage_I  : Stage I + IA + IB  (n=38)
  Class 1 — Stage_IIA: Stage IIA           (n=38)
  Class 2 — Stage_IIB: Stage IIB           (n=38)
  Class 3 — Stage_III: Stage IIIA+IIIB+IIIC (n=36)

Outputs:
  cohort_150_selected.tsv   — full record (folder_id, tcga_barcode, stage, ml_class)
  cohort_150_folder_ids.txt — plain list of folder_ids to feed into brca_ocr_extract.py
  cohort_150_summary.txt    — human-readable summary with justification
"""

import csv
import random
import os
from collections import defaultdict
from pathlib import Path

FOLDER_STAGE_MAP = "/home/a/akashsingh/BRCA-Restaging/stage_organized/folder_stage_map.tsv"
OUTPUT_DIR       = "/home/a/akashsingh/BRCA-Restaging/temp-python-scrpt"
RANDOM_SEED      = 42   # fixed for reproducibility

# ML class definitions: ml_class_label -> (list of stage strings to include, n to sample)
ML_CLASSES = {
    "Stage_I_group":   (["Stage I", "Stage IA", "Stage IB"], 38),
    "Stage_IIA":       (["Stage IIA"],                       38),
    "Stage_IIB":       (["Stage IIB"],                       38),
    "Stage_III_group": (["Stage IIIA", "Stage IIIB", "Stage IIIC"], 36),
}

def load_stage_map(path):
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            rows.append(row)
    return rows

def main():
    random.seed(RANDOM_SEED)
    rows = load_stage_map(FOLDER_STAGE_MAP)

    # Group rows by stage string
    by_stage = defaultdict(list)
    for row in rows:
        by_stage[row["stage"]].append(row)

    # Verify PDF exists in gdc_downloads before including
    gdc_root = Path("/home/a/akashsingh/BRCA-Restaging/gdc_downloads")
    def has_pdf(folder_id):
        folder = gdc_root / folder_id
        return folder.exists() and (
            list(folder.glob("*.PDF")) + list(folder.glob("*.pdf"))
        )

    selected = []
    summary_lines = []
    summary_lines.append("=" * 64)
    summary_lines.append("BRCA RESTAGING PILOT — COHORT SELECTION SUMMARY")
    summary_lines.append("=" * 64)
    summary_lines.append("")
    summary_lines.append("Strategy: Balanced 4-class stratified random sampling")
    summary_lines.append(f"Seed: {RANDOM_SEED} (fixed for reproducibility)")
    summary_lines.append("")

    for ml_class, (stage_list, n_target) in ML_CLASSES.items():
        # Pool all candidates across the merged stages
        candidates = []
        for stage in stage_list:
            candidates.extend(by_stage.get(stage, []))

        # Filter to those that have PDFs
        candidates = [r for r in candidates if has_pdf(r["folder_id"])]

        if len(candidates) < n_target:
            print(f"WARNING: {ml_class} only has {len(candidates)} valid PDFs, wanted {n_target}")
            n_target = len(candidates)

        sampled = random.sample(candidates, n_target)
        for row in sampled:
            selected.append({
                "folder_id":    row["folder_id"],
                "tcga_barcode": row["tcga_barcode"],
                "stage":        row["stage"],
                "ml_class":     ml_class,
                "ml_label":     list(ML_CLASSES.keys()).index(ml_class),
            })

        # Stage breakdown within this class
        stage_breakdown = defaultdict(int)
        for row in sampled:
            stage_breakdown[row["stage"]] += 1

        summary_lines.append(f"ML Class {list(ML_CLASSES.keys()).index(ml_class)}: {ml_class}")
        summary_lines.append(f"  Merged from: {', '.join(stage_list)}")
        summary_lines.append(f"  Available PDFs: {len(candidates)}")
        summary_lines.append(f"  Selected: {n_target}")
        summary_lines.append(f"  Breakdown:")
        for s, c in sorted(stage_breakdown.items()):
            summary_lines.append(f"    {s}: {c}")
        summary_lines.append("")

    summary_lines.append("-" * 64)
    summary_lines.append(f"TOTAL SELECTED: {len(selected)}")
    summary_lines.append("")
    summary_lines.append("Justification:")
    summary_lines.append("  Stage consolidation follows AJCC 8th Edition groupings where")
    summary_lines.append("  sub-stages share equivalent therapeutic implications.")
    summary_lines.append("  Equal class sizes ensure unbiased model training without")
    summary_lines.append("  requiring class-weight corrections at this pilot scale.")
    summary_lines.append("  Cases excluded: Stage 0 (n=4), Stage II undifferentiated (n=7),")
    summary_lines.append("  Stage IV (n=18), Stage X (n=7), Not Reported (n=102).")

    # Write cohort TSV
    tsv_out = os.path.join(OUTPUT_DIR, "cohort_150_selected.tsv")
    with open(tsv_out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["folder_id", "tcga_barcode", "stage", "ml_class", "ml_label"], delimiter="\t")
        writer.writeheader()
        writer.writerows(selected)

    # Write plain folder ID list
    ids_out = os.path.join(OUTPUT_DIR, "cohort_150_folder_ids.txt")
    with open(ids_out, "w") as f:
        for row in selected:
            f.write(row["folder_id"] + "\n")

    # Write summary
    summary_out = os.path.join(OUTPUT_DIR, "cohort_150_summary.txt")
    with open(summary_out, "w") as f:
        f.write("\n".join(summary_lines) + "\n")

    print("\n".join(summary_lines))
    print(f"\nOutputs:")
    print(f"  {tsv_out}")
    print(f"  {ids_out}")
    print(f"  {summary_out}")

if __name__ == "__main__":
    main()
