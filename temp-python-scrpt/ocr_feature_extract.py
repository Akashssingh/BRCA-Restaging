"""
ocr_feature_extract.py

Test script: Takes ONE pathology report PDF from gdc_downloads,
sends each page to Claude Vision API, extracts structured features,
and writes results to a JSON and a text report.
"""

import os
import sys
import json
import base64
import subprocess
import tempfile
from pathlib import Path
import anthropic

# ── Config ────────────────────────────────────────────────────────────────────
GDC_DOWNLOADS_DIR = "/home/a/akashsingh/BRCA-Restaging/gdc_downloads"
OUTPUT_DIR        = "/home/a/akashsingh/BRCA-Restaging/temp-python-scrpt"
TEST_FOLDER_ID    = "0010ce59-fd1e-4a45-8753-a224164fb818"   # first report
MODEL             = "claude-opus-4-5"
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a clinical pathology expert. You will be given an image of a page
from a breast cancer (BRCA) surgical pathology report. Extract every clinically relevant feature
you can identify. Be precise and use only what is explicitly stated in the document — do not infer
or guess. If a field is not present on this page, set it to null."""

EXTRACTION_PROMPT = """Extract the following features from this pathology report page.
Return ONLY a valid JSON object with these exact keys (null if not found on this page):

{
  "patient_id": "TCGA barcode or patient identifier",
  "specimen_type": "e.g. left breast mastectomy, core biopsy, lumpectomy",
  "tumor_site": "anatomical location within breast",
  "tumor_size_cm": "largest dimension in cm as a number",
  "tumor_size_raw": "exact text from report",
  "histologic_type": "e.g. Invasive ductal carcinoma, lobular, mucinous",
  "histologic_grade": "Nottingham/SBR grade (1/2/3) or raw text",
  "nuclear_grade": "nuclear grade if separately stated",
  "tubule_formation_score": "score 1-3 if stated",
  "nuclear_pleomorphism_score": "score 1-3 if stated",
  "mitotic_count_score": "score 1-3 if stated",
  "lymphovascular_invasion": "present / absent / not evaluated",
  "perineural_invasion": "present / absent / not evaluated",
  "margins": "positive / negative / close / not evaluated",
  "margin_distance_mm": "closest margin distance in mm if stated",
  "skin_involvement": "present / absent / not evaluated",
  "nipple_involvement": "present / absent / not evaluated",
  "chest_wall_involvement": "present / absent / not evaluated",
  "lymph_nodes_examined": "total number of nodes examined",
  "lymph_nodes_positive": "number of positive nodes",
  "lymph_node_extranodal_extension": "present / absent / not evaluated",
  "pathologic_T_stage": "pT stage e.g. pT2",
  "pathologic_N_stage": "pN stage e.g. pN1",
  "pathologic_M_stage": "pM stage if stated",
  "overall_pathologic_stage": "overall stage e.g. Stage IIA",
  "ER_status": "positive / negative / not evaluated, include % if stated",
  "PR_status": "positive / negative / not evaluated, include % if stated",
  "HER2_status": "positive / negative / equivocal / not evaluated",
  "HER2_method": "IHC / FISH / CISH if stated",
  "Ki67_percent": "Ki-67 proliferation index % if stated",
  "DCIS_component": "present / absent / not evaluated",
  "DCIS_nuclear_grade": "low / intermediate / high if stated",
  "necrosis": "present / absent / not evaluated",
  "tumor_focality": "unifocal / multifocal / multicentric",
  "additional_findings": "any other clinically relevant findings as a brief string",
  "page_number": "page number if visible"
}"""


def find_pdf(folder_id: str) -> Path:
    folder = Path(GDC_DOWNLOADS_DIR) / folder_id
    pdfs = list(folder.glob("*.PDF")) + list(folder.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"No PDF found in {folder}")
    return pdfs[0]


def pdf_pages_to_base64(pdf_path: Path) -> list[dict]:
    """Convert each PDF page to a base64-encoded JPEG using ImageMagick."""
    pages = []
    with tempfile.TemporaryDirectory() as tmpdir:
        out_pattern = os.path.join(tmpdir, "page-%03d.jpg")
        subprocess.run(
            ["convert", "-density", "200", str(pdf_path), "-quality", "85", out_pattern],
            check=True, capture_output=True
        )
        page_files = sorted(Path(tmpdir).glob("page-*.jpg"))
        if not page_files:
            # Single page may not have index suffix
            page_files = sorted(Path(tmpdir).glob("*.jpg"))
        for i, pf in enumerate(page_files):
            with open(pf, "rb") as f:
                b64 = base64.standard_b64encode(f.read()).decode("utf-8")
            pages.append({"page": i + 1, "b64": b64, "path": str(pf)})
    return pages


def extract_features_from_page(client: anthropic.Anthropic, page_b64: str, page_num: int) -> dict:
    """Send one page image to Claude and get structured features back."""
    print(f"  Sending page {page_num} to Claude...", end=" ", flush=True)
    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": page_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": EXTRACTION_PROMPT
                    }
                ],
            }
        ],
    )
    raw = message.content[0].text.strip()
    print(f"done. Input tokens: {message.usage.input_tokens}, Output: {message.usage.output_tokens}")

    # Parse JSON — strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_response": raw, "parse_error": True}


def merge_page_features(page_results: list[dict]) -> dict:
    """Merge features across pages — later non-null values override earlier nulls."""
    merged = {}
    for page_data in page_results:
        for key, val in page_data.items():
            if key in ("page_number", "raw_response", "parse_error"):
                continue
            if val is not None and val != "null":
                merged[key] = val
            elif key not in merged:
                merged[key] = val
    return merged


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    pdf_path = find_pdf(TEST_FOLDER_ID)
    print(f"PDF: {pdf_path}")

    print("Converting PDF pages to images...")
    pages = pdf_pages_to_base64(pdf_path)
    print(f"Found {len(pages)} page(s).")

    page_results = []
    for page in pages:
        result = extract_features_from_page(client, page["b64"], page["page"])
        result["_page"] = page["page"]
        page_results.append(result)

    merged = merge_page_features(page_results)
    merged["_source_file"] = pdf_path.name
    merged["_folder_id"] = TEST_FOLDER_ID
    merged["_pages_processed"] = len(pages)

    # Write JSON
    json_out = os.path.join(OUTPUT_DIR, "ocr_test_result.json")
    with open(json_out, "w") as f:
        json.dump({"per_page": page_results, "merged": merged}, f, indent=2)

    # Write human-readable report
    txt_out = os.path.join(OUTPUT_DIR, "ocr_test_result.txt")
    with open(txt_out, "w") as f:
        f.write("=" * 70 + "\n")
        f.write("OCR FEATURE EXTRACTION — TEST RESULT\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"Source PDF    : {pdf_path.name}\n")
        f.write(f"Folder ID     : {TEST_FOLDER_ID}\n")
        f.write(f"Pages processed: {len(pages)}\n")
        f.write(f"Model         : {MODEL}\n\n")

        f.write("-" * 70 + "\n")
        f.write("MERGED FEATURES (across all pages)\n")
        f.write("-" * 70 + "\n")
        for key, val in merged.items():
            if not key.startswith("_"):
                f.write(f"  {key:<40} : {val}\n")

        for pr in page_results:
            f.write(f"\n{'- '*35}\n")
            f.write(f"PAGE {pr.get('_page')} RAW EXTRACTION\n")
            f.write(f"{'- '*35}\n")
            for key, val in pr.items():
                if key != "_page":
                    f.write(f"  {key:<40} : {val}\n")

    print(f"\nJSON output : {json_out}")
    print(f"Text output : {txt_out}")
    print("\nMERGED FEATURES:")
    for key, val in merged.items():
        if not key.startswith("_"):
            print(f"  {key:<40} : {val}")


if __name__ == "__main__":
    main()
