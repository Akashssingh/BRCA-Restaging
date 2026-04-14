# BRCA Restaging — Automated Cancer Stage Prediction from Pathology Reports

## Overview

This project builds a machine learning pipeline to automatically predict AJCC breast cancer stage from surgical pathology reports. Source data is from TCGA-BRCA via the GDC Data Portal. Pathology reports are processed with Claude Vision API (OCR + structured feature extraction), and the resulting feature matrix is used to train a multiclass classifier.

**Current phase:** OCR feature extraction (150-case pilot cohort).

---

## Repository Structure

What you see on GitHub (data directories are gitignored — see [Getting the Data](#getting-the-data)):

```
BRCA-Restaging/
├── brca_ocr_extract.py              # Main OCR + feature extraction pipeline
├── claude.py                        # Batch OCR pipeline (teammate script)
├── gdc_manifest_brca_pathreports.txt # GDC manifest — 1098 pathology report PDFs
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── README.md
│
├── Analyses_and_Reports/            # Clinical analysis scripts and outputs
│   ├── brca_clinical_analysis.py    # Stage distribution, case matching, dedup TSV
│   ├── brca_clinical_dedup.tsv      # 1 row per patient (ground truth stages)
│   ├── brca_case_id_list.txt        # GDC file IDs for 1098 cases
│   ├── cohort_150_selected.tsv      # Final cohort: folder_id, stage, ml_class, ml_label
│   ├── cohort_150_folder_ids.txt    # Plain list of folder IDs
│   ├── cohort_150_summary.txt       # Cohort composition summary
│   └── stage_distribution.png      # Bar chart of full stage distribution
│
├── gdc_accessory_files/             # GDC clinical metadata TSVs
│   ├── clinical.tsv                 # Full clinical data (5545 rows, 1098 patients)
│   ├── exposure.tsv
│   ├── follow_up.tsv
│   └── ...
│
├── temp-python-scrpt/               # Utility scripts
│   ├── list_folders.py
│   ├── ocr_feature_extract.py       # Single-report test script
│   ├── organize_by_stage.py         # Generates stage_organized/ symlink tree
│   └── select_cohort.py             # Balanced 150-case cohort selection
│
└── ocr_output/
    └── brca_features_master.csv     # Feature matrix — one row per processed case
```

**Gitignored locally (not on GitHub):**
- `gdc_downloads/` — 1098 PDF folders downloaded via GDC client (~large)
- `Gaivi_package/` — raw GDC cart export
- `stage_organized/` — symlink tree generated from `gdc_downloads/`
- `ocr_output/json/`, `ocr_output/txt/`, `ocr_output/features/` — per-case extraction outputs

---

## Getting the Data

### 1. GDC Pathology Report PDFs

```bash
# Install gdc-client from https://gdc.cancer.gov/access-data/gdc-data-transfer-tool
# Then download the 1098 TCGA-BRCA pathology reports:
mkdir -p gdc_downloads
gdc-client download -m gdc_manifest_brca_pathreports.txt -d ./gdc_downloads --n-processes 8
```

> **Cluster note:** If `/tmp` is mounted `noexec` (e.g. USF GAIVI cluster), PyInstaller-based binaries fail.  
> Fix: `mkdir -p ~/tmp_exec && TMPDIR=~/tmp_exec gdc-client download ...`

### 2. Clinical Metadata

The `gdc_accessory_files/clinical.tsv` is included in the repo. It is a denormalized export from GDC — one row per treatment record per patient:
- **5545 total rows** across **1098 unique patients**
- Ground truth: `diagnoses.ajcc_pathologic_stage` column
- Deduplicated version (1 row per patient): `Analyses_and_Reports/brca_clinical_dedup.tsv`

---

## Stage Distribution (1098 patients)

| Stage | n | % |
|---|---|---|
| Stage IIA | 334 | 30.4% |
| Stage IIB | 235 | 21.4% |
| Stage IIIA | 138 | 12.6% |
| Stage IA | 85 | 7.7% |
| Stage I | 83 | 7.6% |
| Not Reported | 102 | 9.3% |
| Stage IIIC | 56 | 5.1% |
| Stage IIIB | 23 | 2.1% |
| Stage IV | 18 | 1.6% |
| Others | 24 | 2.2% |

---

## Pilot Cohort (150 cases)

Balanced 4-class stratified random sample. Seed = 42 for reproducibility.

| ML Label | Class | Merged Stages | n |
|---|---|---|---|
| 0 | Stage_I_group | Stage I + IA + IB | 38 |
| 1 | Stage_IIA | Stage IIA | 38 |
| 2 | Stage_IIB | Stage IIB | 38 |
| 3 | Stage_III_group | Stage IIIA + IIIB + IIIC | 36 |

**Excluded:** Stage 0 (n=4), Stage II (n=7), Stage IV (n=18), Stage X (n=7), Not Reported (n=102) — insufficient sample sizes. Stage consolidation follows AJCC 8th Edition groupings.

Cohort definition: `Analyses_and_Reports/cohort_150_selected.tsv`

---

## Feature Extraction Pipeline

**Script:** `brca_ocr_extract.py`

Single-pass: each PDF page → Claude Vision API → returns full OCR text + structured JSON features in one response. No second API call.

**PDF rendering:** PyMuPDF renders at 300 DPI, auto-scaled to ≤1568px (Claude's image size limit).

### Feature Schema (69 columns)

Designed around AJCC 8th Edition staging requirements:

| Category | Fields |
|---|---|
| Metadata | patient_id, report_date, institution |
| Specimen | type, laterality, tumor_site |
| Histology | histologic_type, DCIS, LCIS, necrosis, LVI, PNI |
| T stage | tumor_size_cm, focality, skin/nipple/chest wall involvement, pathologic_T |
| N stage | lymph_nodes_positive, lymph_nodes_examined, axillary levels, pathologic_N |
| M stage | distant_metastasis, pathologic_M |
| Grade | nottingham_grade, tubule_formation, nuclear_pleomorphism, mitotic_count |
| Biomarkers | ER_status/percent, PR_status/percent, HER2_status/IHC/ISH, Ki67 |
| Margins | margins, margin_distance_mm |
| Derived | derived_anatomic_stage, extraction_confidence, missing_for_anatomic_stage |
| ML labels | _ml_class, _ml_label |

### Post-processing

- **MX / missing M:** Defaulted to `cM0` for surgical specimens (`pM0` is not valid per AJCC 8th Ed). Flagged with `_m_defaulted: true`.
- **Roman numeral node counts:** Converted to integers (e.g. `IX/XV` → 9 positive / 15 examined). Swap logic corrects positive > examined parse errors.
- **Retry on 529:** Up to 5 retries with exponential backoff (15→30→60→120→240s) for Anthropic overloaded errors.
- **Resume-safe:** `SKIP_PROCESSED=True` — re-running skips completed cases.

### Outputs

```
ocr_output/
├── json/{folder_id}.json               # Full: OCR text + features + per-page breakdown
├── txt/{folder_id}.txt                 # OCR text only
├── features/{folder_id}_features.json  # Features only (lightweight)
└── brca_features_master.csv            # Feature matrix — ML input
```

---

## Running the Pipeline

### Option A — Local (conda)

```bash
conda create -n brca python=3.10
conda activate brca
pip install -r requirements.txt

export ANTHROPIC_API_KEY="sk-ant-..."
python3 Analyses_and_Reports/brca_clinical_analysis.py   # clinical analysis
python3 temp-python-scrpt/select_cohort.py               # cohort selection
python3 brca_ocr_extract.py                              # OCR extraction → type 'yes'
```

### Option B — Docker

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
docker compose up
```

Mounts `./gdc_downloads` and `./ocr_output` into the container. The API key is passed via environment variable — never hardcode it.

---

## ML Model (Planned)

**Task:** 4-class stage classification from histological features  
**Input:** `ocr_output/brca_features_master.csv`  
**Approach:**
- Track A: XGBoost / Random Forest on structured features (fast baseline)
- Track B: Frozen ClinicalBERT embeddings from raw OCR text + shallow classifier
- Track C: Late fusion — structured features + text embeddings

**Evaluation:** 5-fold stratified CV on 120 cases + 30-case held-out test set. Primary metric: macro F1.

---

## Cluster Notes (USF GAIVI)

| Issue | Fix |
|---|---|
| `/tmp` mounted `noexec` | `mkdir -p ~/tmp_exec && TMPDIR=~/tmp_exec gdc-client ...` |
| `conda run` strips env vars | Use `source activate emogi-env` instead |
| API key not persisting | Re-export each session: `export ANTHROPIC_API_KEY="..."` |


---

## Project Structure

```
BRCA-Restaging/
├── Gaivi_package/                     # Raw GDC download: clinical TSV, biospecimen TSV
│   ├── clinical.tsv                   # Full clinical data (5545 rows, 1098 patients)
│   ├── BRCA-Pathology-Reports/        # 710 report folders (Salient dataset, separate)
│   └── ...
│
├── gdc_downloads/                     # 1098 pathology report PDFs (GDC download)
│   └── {folder_id}/                   # One folder per case
│       └── TCGA-XX-XXXX.*.PDF
│
├── gdc_manifest_brca_pathreports.txt  # GDC manifest used to download PDFs
│
├── stage_organized/                   # Folders organized by AJCC stage (symlinks)
│   ├── folder_stage_map.tsv           # folder_id → tcga_barcode → stage
│   ├── stage_summary.txt              # Stage distribution counts
│   ├── Stage_IIA/                     # Symlinks to gdc_downloads folders
│   └── ...
│
├── ocr_output/                        # Extraction outputs
│   ├── json/                          # Full JSON per case (OCR text + all features)
│   ├── txt/                           # OCR text only per case
│   ├── features/                      # Feature JSON only per case (lightweight)
│   └── brca_features_master.csv       # Feature matrix — one row per case
│
├── temp-python-scrpt/                 # Analysis and utility scripts
│   ├── brca_clinical_analysis.py      # Stage distribution analysis + subset TSV
│   ├── brca_clinical_dedup.tsv        # One row per patient (stages as ground truth)
│   ├── select_cohort.py               # Balanced 150-case cohort selection
│   ├── cohort_150_selected.tsv        # Cohort: folder_id, stage, ml_class, ml_label
│   ├── cohort_150_folder_ids.txt      # Plain list of folder IDs for extraction
│   ├── cohort_150_summary.txt         # Cohort composition and justification
│   ├── stage_distribution.png         # Bar chart of full 1098-patient stage distribution
│   └── organize_by_stage.py           # Script that created stage_organized/
│
├── brca_ocr_extract.py                # Main OCR + feature extraction pipeline
└── README.md                          # This file
```

---

## Data Sources

| File | Source | Description |
|---|---|---|
| `clinical.tsv` | GDC Cart export | Clinical data for all 1098 TCGA-BRCA patients |
| `gdc_manifest_brca_pathreports.txt` | GDC Data Portal | Manifest for 1098 pathology report PDFs |
| `gdc_downloads/` | GDC client v1.6.1 | Downloaded pathology report PDFs |
| `Gaivi_package/BRCA-Pathology-Reports/` | Separate dataset | 710 reports (used for folder enumeration only) |

**GDC download command:**
```bash
TMPDIR=~/tmp_exec ~/tools/gdc-client download \
  -m gdc_manifest_brca_pathreports.txt \
  -d ./gdc_downloads --n-processes 8
```
Note: On this cluster `/tmp` is mounted `noexec`; `TMPDIR` must point to a writable, executable path.

---

## Clinical Data

The `clinical.tsv` from GDC is a denormalized flat export — one row per treatment record, not per patient. Each patient has 1–40 rows depending on treatments recorded.

- **5545 total rows** across **1098 unique patients**
- Deduplicated to one row per patient in `brca_clinical_dedup.tsv`
- The `diagnoses.ajcc_pathologic_stage` column is used as ground truth

**Stage distribution (1098 patients):**

| Stage | n | % |
|---|---|---|
| Stage IIA | 334 | 30.4% |
| Stage IIB | 235 | 21.4% |
| Stage IIIA | 138 | 12.6% |
| Stage IA | 85 | 7.7% |
| Stage I | 83 | 7.6% |
| Not Reported | 102 | 9.3% |
| Stage IIIC | 56 | 5.1% |
| Stage IIIB | 23 | 2.1% |
| Stage IV | 18 | 1.6% |
| Stage IB | 6 | 0.5% |
| Stage II | 7 | 0.6% |
| Stage X | 7 | 0.6% |
| Stage 0 | 4 | 0.4% |

---

## Cluster Notes (USF GAIVI)

| Issue | Fix |
|---|---|
| `/tmp` mounted `noexec` | `mkdir -p ~/tmp_exec && TMPDIR=~/tmp_exec gdc-client ...` |
| `conda run` strips env vars | Use `source activate emogi-env` instead |
| API key not persisting | Re-export each session: `export ANTHROPIC_API_KEY="..."` |

