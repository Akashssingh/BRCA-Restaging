import csv
import os

CLINICAL_TSV = "/home/a/akashsingh/BRCA-Restaging/Gaivi_package/clinical.tsv"
MANIFEST_FILE = "/home/a/akashsingh/BRCA-Restaging/gdc_manifest_brca_pathreports.txt"
CASE_ID_LIST = "/home/a/akashsingh/BRCA-Restaging/temp-python-scrpt/brca_case_id_list.txt"
OUTPUT_REPORT = "/home/a/akashsingh/BRCA-Restaging/temp-python-scrpt/brca_clinical_analysis.txt"
OUTPUT_TSV = "/home/a/akashsingh/BRCA-Restaging/temp-python-scrpt/brca_clinical_subset.tsv"

SUBMITTER_ID_COL = "cases.submitter_id"
STAGE_COL = "diagnoses.ajcc_pathologic_stage"
OUTPUT_DEDUP_TSV = "/home/a/akashsingh/BRCA-Restaging/temp-python-scrpt/brca_clinical_dedup.tsv"


def load_file_ids(path):
    with open(path, "r") as f:
        return set(line.strip() for line in f if line.strip())


def load_manifest(path):
    """Returns dict: file_id -> TCGA barcode (e.g. TCGA-BH-A18H)"""
    mapping = {}
    with open(path, "r") as f:
        next(f)  # skip header
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= 2:
                file_id = parts[0].strip()
                tcga_barcode = parts[1].split(".")[0].strip()
                mapping[file_id] = tcga_barcode
    return mapping


def load_clinical(path):
    rows = []
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        headers = reader.fieldnames
        # clinical.tsv has a second header row with type info — skip it
        next(reader)
        for row in reader:
            rows.append(row)
    return headers, rows


def main():
    # --- Load inputs ---
    file_ids = load_file_ids(CASE_ID_LIST)
    manifest = load_manifest(MANIFEST_FILE)
    headers, rows = load_clinical(CLINICAL_TSV)

    # Map file_id -> TCGA barcode for IDs in our list
    file_id_to_barcode = {fid: manifest[fid] for fid in file_ids if fid in manifest}
    barcodes_from_list = set(file_id_to_barcode.values())

    # File IDs in the list that are missing from manifest (shouldn't happen)
    missing_from_manifest = file_ids - set(manifest.keys())

    # Index clinical rows by submitter_id (TCGA barcode); keep all rows per barcode
    clinical_by_submitter = {}
    for row in rows:
        sid = row.get(SUBMITTER_ID_COL, "").strip()
        if sid:
            clinical_by_submitter.setdefault(sid, []).append(row)

    # --- Analysis 1: unique ajcc_pathologic_stage values (full clinical.tsv) ---
    all_stages = {}
    for row in rows:
        stage = row.get(STAGE_COL, "").strip()
        if not stage or stage == "'--":
            stage = "Not Reported / Empty"
        all_stages[stage] = all_stages.get(stage, 0) + 1

    # --- Analysis 2: match barcodes to clinical ---
    found_barcodes = barcodes_from_list & set(clinical_by_submitter.keys())
    missing_barcodes = barcodes_from_list - set(clinical_by_submitter.keys())

    # --- Analysis 3: subset rows (all clinical rows for matched barcodes) ---
    subset_rows = []
    for barcode in sorted(found_barcodes):
        subset_rows.extend(clinical_by_submitter[barcode])

    # --- Analysis 4: deduplicated rows — one row per patient (first row per barcode) ---
    dedup_rows = []
    for barcode in sorted(found_barcodes):
        dedup_rows.append(clinical_by_submitter[barcode][0])

    # Stage counts across deduplicated patients
    dedup_stage_counts = {}
    for row in dedup_rows:
        stage = row.get(STAGE_COL, "").strip()
        if not stage or stage == "'--":
            stage = "Not Reported / Empty"
        dedup_stage_counts[stage] = dedup_stage_counts.get(stage, 0) + 1

    # Write report
    with open(OUTPUT_REPORT, "w") as out:
        out.write("=" * 70 + "\n")
        out.write("BRCA CLINICAL DATA ANALYSIS\n")
        out.write("=" * 70 + "\n\n")

        out.write(f"Clinical TSV    : {CLINICAL_TSV}\n")
        out.write(f"Manifest file   : {MANIFEST_FILE}\n")
        out.write(f"Case ID list    : {CASE_ID_LIST}\n\n")
        out.write(f"Total rows in clinical.tsv (excl. headers) : {len(rows)}\n")
        out.write(f"File IDs in case ID list                   : {len(file_ids)}\n")
        out.write(f"File IDs missing from manifest             : {len(missing_from_manifest)}\n")
        out.write(f"Unique TCGA barcodes resolved from list    : {len(barcodes_from_list)}\n\n")

        out.write("-" * 70 + "\n")
        out.write("1. UNIQUE VALUES OF diagnoses.ajcc_pathologic_stage (full clinical.tsv)\n")
        out.write("-" * 70 + "\n")
        for stage, count in sorted(all_stages.items()):
            out.write(f"  {stage:<45} : {count} rows\n")
        out.write(f"\n  Total unique stage values: {len(all_stages)}\n\n")

        out.write("-" * 70 + "\n")
        out.write("2. CASE MATCH CONFIRMATION (via manifest -> TCGA barcode -> clinical)\n")
        out.write("-" * 70 + "\n")
        out.write(f"  Barcodes resolved from list       : {len(barcodes_from_list)}\n")
        out.write(f"  Found in clinical.tsv             : {len(found_barcodes)}\n")
        out.write(f"  MISSING from clinical.tsv         : {len(missing_barcodes)}\n\n")

        if missing_barcodes:
            out.write("  Missing TCGA barcodes:\n")
            for bc in sorted(missing_barcodes):
                fid = next(k for k, v in file_id_to_barcode.items() if v == bc)
                out.write(f"    {bc}  (file_id: {fid})\n")
        else:
            out.write("  All barcodes were found in clinical.tsv.\n")
        out.write("\n")

        out.write("-" * 70 + "\n")
        out.write("3. STAGE DISTRIBUTION — DEDUPLICATED (1 row per patient)\n")
        out.write("-" * 70 + "\n")
        out.write(f"  Total unique patients : {len(dedup_rows)}\n\n")
        out.write(f"  {'Stage':<45} {'Count':>6}  {'% of patients':>14}\n")
        out.write(f"  {'-'*45} {'-'*6}  {'-'*14}\n")
        for stage, count in sorted(dedup_stage_counts.items()):
            pct = 100.0 * count / len(dedup_rows)
            out.write(f"  {stage:<45} {count:>6}  {pct:>13.1f}%\n")
        out.write(f"\n  Total unique stage categories: {len(dedup_stage_counts)}\n\n")

        out.write("-" * 70 + "\n")
        out.write("4. OUTPUT FILES\n")
        out.write("-" * 70 + "\n")
        out.write(f"  Full subset TSV (all rows)    : {OUTPUT_TSV}\n")
        out.write(f"  Deduplicated TSV (1 per patient): {OUTPUT_DEDUP_TSV}\n\n")

    # Write full subset TSV
    with open(OUTPUT_TSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(subset_rows)

    # Write deduplicated TSV (one row per patient)
    with open(OUTPUT_DEDUP_TSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dedup_rows)

    print(f"Analysis written to   : {OUTPUT_REPORT}")
    print(f"Full subset TSV       : {OUTPUT_TSV}")
    print(f"Deduplicated TSV      : {OUTPUT_DEDUP_TSV}  ({len(dedup_rows)} patients)")
    print(f"Barcodes matched: {len(found_barcodes)}/{len(barcodes_from_list)} | Missing: {len(missing_barcodes)}")


if __name__ == "__main__":
    main()
