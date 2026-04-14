# BRCA Restaging — Automated Cancer Stage Prediction from Pathology Reports

## Overview

This project builds a machine learning pipeline to automatically predict AJCC breast cancer stage from surgical pathology reports. Source data is from TCGA-BRCA via the GDC Data Portal. Pathology reports are processed with Claude Vision API (OCR + structured feature extraction), and the resulting feature matrix is used to train a multiclass classifier.

**Current phase:** OCR feature extraction (150-case pilot cohort).

---

## Repository Structure

```
BRCA-Restaging/
├── brca_ocr_extract.py                  # Main OCR + feature extraction pipeline
├── claude.py                            # Batch OCR pipeline
├── gdc_manifest_brca_pathreports.txt    # GDC manifest for 1098 pathology report PDFs
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
│
├── Analyses_and_Reports/                # Clinical analysis scripts and outputs
│   ├── brca_clinical_analysis.py        # Stage distribution, case matching, dedup TSV
│   ├── brca_clinical_dedup.tsv          # 1 row per patient (ground truth stages)
│   ├── brca_case_id_list.txt            # GDC file IDs for 1098 cases
│   ├── cohort_150_selected.tsv          # Final cohort: folder_id, stage, ml_class, ml_label
│   ├── cohort_150_folder_ids.txt        # Plain list of folder IDs
│   ├── cohort_150_summary.txt           # Cohort composition summary
│   └── stage_distribution.png          # Bar chart of full stage distribution
│
├── gdc_accessory_files/                 # GDC clinical metadata TSVs
│   ├── clinical.tsv                     # Full clinical data (5545 rows, 1098 patients)
│   ├── exposure.tsv
│   ├── follow_up.tsv
│   └── ...
│
├── temp-python-scrpt/                   # Utility scripts
│   ├── select_cohort.py                 # Balanced 150-case cohort selection
│   ├── organize_by_stage.py             # Generates stage_organized/ symlink tree
│   ├── ocr_feature_extract.py           # Single-report test script
│   └── list_folders.py
│
└── ocr_output/
    └── brca_features_master.csv         # Feature matrix — one row per processed case
```

**Not tracked in git (download locally):**
- `gdc_downloads/` — 1098 PDF folders from GDC
- `ocr_output/json/`, `ocr_output/txt/`, `ocr_output/features/` — per-case extraction outputs

---

## Getting the Data

```bash
# Install gdc-client: https://gdc.cancer.gov/access-data/gdc-data-transfer-tool
mkdir -p gdc_downloads
gdc-client download -m gdc_manifest_brca_pathreports.txt -d ./gdc_downloads --n-processes 8
```

Clinical metadata is already in the repo at `gdc_accessory_files/clinical.tsv`.
It is a denormalized GDC export (one row per treatment record):
- **5545 rows** across **1098 unique patients**
- Ground truth: `diagnoses.ajcc_pathologic_stage`
- Deduplicated (1 row/patient): `Analyses_and_Reports/brca_clinical_dedup.tsv`

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

Balanced 4-class stratified random sample. Seed = 42.

| ML Label | Class | Stages Included | n |
|---|---|---|---|
| 0 | Stage_I_group | Stage I + IA + IB | 38 |
| 1 | Stage_IIA | Stage IIA | 38 |
| 2 | Stage_IIB | Stage IIB | 38 |
| 3 | Stage_III_group | Stage IIIA + IIIB + IIIC | 36 |

Excluded: Stage 0 (n=4), Stage IV (n=18), Not Reported (n=102), and ambiguous sub-stages — insufficient sample sizes. Sub-stage consolidation follows AJCC 8th Edition groupings.

Cohort definition: `Analyses_and_Reports/cohort_150_selected.tsv`

---

## Feature Extraction Pipeline

**Script:** `brca_ocr_extract.py`

Each PDF page is sent once to Claude Vision API, which returns both full OCR text and structured AJCC features in a single response. PyMuPDF renders pages at 300 DPI, auto-scaled to ≤1568px.

### Feature Schema (69 columns)

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

- **MX / missing M:** Defaulted to `cM0` for surgical specimens (`pM0` is not a valid AJCC 8th Ed category). Flagged with `_m_defaulted: true`.
- **Roman numeral node counts:** Converted to integers (`IX/XV` → 9/15). Swap logic corrects positive > examined parse errors.
- **Retry on overload:** Up to 5 retries with exponential backoff on Anthropic 529 errors.
- **Resume-safe:** Re-running skips already-completed cases.

### Outputs

```
ocr_output/
├── json/{folder_id}.json                # OCR text + features + per-page breakdown
├── txt/{folder_id}.txt                  # OCR text only
├── features/{folder_id}_features.json  # Features only (lightweight)
└── brca_features_master.csv            # Feature matrix — ML input
```

---

## Running the Pipeline

### Option A — Local

```bash
conda create -n brca python=3.10
conda activate brca
pip install -r requirements.txt

export ANTHROPIC_API_KEY="sk-ant-..."
python3 Analyses_and_Reports/brca_clinical_analysis.py  # clinical analysis
python3 temp-python-scrpt/select_cohort.py              # cohort selection
python3 brca_ocr_extract.py                             # OCR extraction — type 'yes'
```

### Option B — Docker

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
docker compose up
```

`./gdc_downloads` and `./ocr_output` are mounted into the container at runtime. The API key is passed via environment variable.

---

## ML Model (Planned)

**Task:** 4-class stage classification from histological features  
**Input:** `ocr_output/brca_features_master.csv`  
**Approach:**
- Track A: XGBoost / Random Forest on structured features (baseline)
- Track B: Frozen ClinicalBERT embeddings from raw OCR text + shallow classifier
- Track C: Late fusion — structured features + text embeddings

**Evaluation:** 5-fold stratified CV on 120 cases + 30-case held-out test set. Primary metric: macro F1.
