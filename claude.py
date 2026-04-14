import anthropic
import base64
import sys
from pathlib import Path
import time
import torch
from typing import Dict, List, Tuple
from PIL import Image
import fitz  # PyMuPDF - Used for PDF processing and image extraction
import io
import os
import csv


client = anthropic.Anthropic()

def ocr_with_retry(
    image str,
    page_num int = 1,
    max_new_tokens int = 4196,
    use_cot bool = True
) - Dict
    
    if use_cot
        prompt = Extract all text from this image with perfect accuracy. Let's work systematically

1. First, identify the document structure (headings, paragraphs, tables, lists)
2. Then, read each section carefully
3. Preserve the table information accurately.
4. If a cell is missing indicate it with na.
3. Preserve line breaks, and special characters
4. Double-check numbers, dates, and proper nouns
5. If a line is cut off at the bottom of the image, do not attempt to complete or repeat it.
6. Finally, output the complete text exactly as shown

Please provide the full text transcription
    else
        prompt = Read and transcribe all text from this image exactly as shown, preserving formatting and structure.

    messages = [
        {
            role user,
            content [
                {
                  type image, 
                  source {
                      type base64,
                      media_type imagepng,
                      data image,
                  },
                },
                {type text, text prompt}
            ]
        }
    ]
    start_time = time.time()
    response = client.messages.create(
            model=claude-sonnet-4-6,
            max_tokens=4096,
            messages=messages,
    )
    page_text = response.content[0].text
    print(time taken for page {} {.2f} seconds.format(page_num, time.time() - start_time))
    char_count = len(page_text)
    word_count = len(page_text.split())
    return { 'text' page_text,
      'chars' char_count,
      'words' word_count,
    }


def preprocess_image_for_ocr(
    image str,
    pix fitz.Pixmap = None,
    max_size int = 2048,
) - str
    
    Resize images that exceed maximum dimensions to prevent memory issues.

    Large images can cause out-of-memory errors during model inference and don't
    necessarily improve OCR accuracy. This function downscales oversized images while
    maintaining aspect ratio. LANCZOS resampling is used because it provides the best
    quality for downscaling, preserving text clarity better than other methods.

    Args
        image Input PIL Image in any mode
        max_size Maximum allowed dimension (width or height) in pixels.
                  2048 is chosen as a reasonable upper bound that balances quality
                  with memory constraints for most GPUs (typically uses ~4-6GB VRAM)

    Returns
        Preprocessed PIL Image, resized if necessary
    
    img = Image.frombytes(RGB, [pix.width, pix.height], pix.samples)

    # Resize based on long edge (Claude's limit is 1568px)
    MAX_LONG_EDGE = 1568
    long_edge = max(img.width, img.height)
    if long_edge  MAX_LONG_EDGE
        scale = MAX_LONG_EDGE  long_edge
        new_size = (int(img.width  scale), int(img.height  scale))
        img = img.resize(new_size, Image.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, format=PNG, quality=85)

    return base64.standard_b64encode(buffer.getvalue()).decode(utf-8)


def pdf_to_images(pdf_path str, dpi int = 300) - List[Tuple[str, fitz.Pixmap]]
    
    Convert each page of a PDF document into a PIL Image.

    This function uses PyMuPDF's rendering engine to convert PDF pages to raster images.
    Higher DPI values produce better quality but increase memory usage and processing time.
    300 DPI is chosen as default because it provides a good balance between quality and
    performance for most OCR tasks.

    Args
        pdf_path Absolute or relative path to the PDF file
        dpi Dots per inch for rendering. Standard values are
             - 72 Screen quality (fast, lower quality)
             - 150 Acceptable for basic OCR
             - 300 High quality for accurate OCR (recommended)
             - 600 Very high quality for small text

    Returns
        List of PIL Images in RGB format, one image per page

    Raises
        FileNotFoundError If the PDF file doesn't exist
        fitz.FileDataError If the file is not a valid PDF
    
    if not os.path.exists(pdf_path)
        raise FileNotFoundError(fPDF file not found {pdf_path})

    print(fConverting PDF to images at {dpi} DPI...)

    doc = fitz.open(pdf_path)
    images = []

    # Calculate zoom factor from desired DPI
    # PyMuPDF uses 72 DPI as base resolution, so we scale relative to that
    zoom = dpi  72.0
    mat = fitz.Matrix(zoom, zoom)

    try
        for page_num in range(len(doc))
            page = doc[page_num]

            # Render page to pixmap (raster image)
            # alpha=False removes transparency channel to save memory and ensure RGB output
            
            pix = page.get_pixmap(matrix=mat, alpha=False)
            png_bytes = pix.tobytes(output=png)
            images.append((base64.standard_b64encode(png_bytes).decode(utf-8), pix))

            # print(f{len(png_bytes)  1024  1024.2f} MB)

            print(f  Processed page {page_num + 1}{len(doc)} {pix.width}x{pix.height}px)

    finally
        # Ensure document is closed even if an error occurs
        # This prevents memory leaks from unclosed file handles
        doc.close()

    print(fSuccessfully converted {len(images)} pages)
    return images

def ocr_pdf(
    pdf_path str,
    dpi int = 300,
    attempts_per_page int = 3,
    use_cot bool = True,
    max_pages int = None
) - Dict
    
    Perform OCR on an entire PDF document with retry logic and confidence scoring per page.

    Args
        pdf_path Path to the PDF file
        dpi Resolution for PDF rendering
        attempts_per_page Number of OCR attempts per page
        use_cot Whether to use Chain-of-Thought prompting
        max_pages Optional limit on pages to process

    Returns
        Dictionary with results including per-page confidence scores
    
    print(=  80)
    print(PDF OCR WITH RETRY LOGIC AND CONFIDENCE SCORING)
    print(=  80)

    images = pdf_to_images(pdf_path, dpi=dpi)

    if max_pages
        images = images[max_pages]
        print(fProcessing first {max_pages} pages only (limit applied))

    total_pages = len(images)
    print(fTotal pages to process {total_pages}n)

    all_results = []
    total_start = time.time()
    full_text = 
    chars = 0
    words = 0

    for i, (image, pix) in enumerate(images, 1)
        print(fProcessing page {i}{total_pages}...)
        processed_img = preprocess_image_for_ocr(image, pix)#.rotate(90, expand=True))
        result = ocr_with_retry(
            image=processed_img,
            page_num=i,
            max_new_tokens=2048,
            use_cot=use_cot
        )
        full_text += f{'='  37} PAGE {i} {'='  37}n{result['text']}nn
        chars += result['chars']
        words += result['words']
    all_results = {
        'pages' total_pages,
        'chars' chars,
        'words' words,
    }
    print(time taken for doc {.2f} seconds.format(i, time.time() - total_start))
    return all_results, full_text

def find_pdf_files(root_directory str, skip_processed bool = True) - List[Tuple[str, str]]
    
    Recursively find all PDF files in a directory and its subdirectories.

    This function traverses the entire directory tree to locate PDFs while optionally
    skipping files that have already been processed (i.e., have corresponding .txt files).
    This prevents redundant processing in subsequent runs.

    Args
        root_directory Path to the root directory to search
        skip_processed If True, skip PDFs that already have corresponding .txt output files.
                       This is useful for resuming interrupted batch jobs.

    Returns
        List of tuples (pdf_path, output_txt_path) for each PDF to process
    
    pdf_files = []

    # Validate root directory exists
    if not os.path.exists(root_directory)
        raise FileNotFoundError(fDirectory not found {root_directory})

    print(fScanning directory {root_directory})

    # os.walk recursively yields (dirpath, dirnames, filenames) for each directory
    # This is more efficient than recursive function calls for deep hierarchies
    for dirpath, dirnames, filenames in os.walk(root_directory)
        for filename in filenames
            # Case-insensitive PDF detection to handle .PDF, .pdf, .Pdf, etc.
            if filename.lower().endswith('.pdf')
                pdf_path = os.path.join(dirpath, filename)

                # Generate output filename by replacing .pdf extension with .txt
                # This keeps the output in the same directory as the source
                output_filename = os.path.splitext(filename)[0] + '_ocr.txt'
                output_path = os.path.join('claude', output_filename)

                # Skip if already processed (unless user wants to reprocess)
                if skip_processed and os.path.exists(output_path)
                    print(f  Skipping (already processed) {pdf_path})
                    continue

                pdf_files.append((pdf_path, output_path))

    print(fFound {len(pdf_files)} PDF(s) to process)
    return pdf_files


def save_results(results, full_text, filename, include_metadata=False)
    print(results)
    with open(filename, 'w') as file
        file.write(full_text)

    with open('claudestats.csv', mode='a', newline='', encoding='utf-8') as csv_file
        writer = csv.DictWriter(csv_file, fieldnames=['file_name', 'stats'])
        writer.writerow({
            'file_name' filename.split('.')[0].split('')[-1],
            'stats' {
                'pages' results['pages'],
                'chars' results['chars'],
                'words' results['words']
            }
        })

def process_pdf_batch(
    root_directory str,
    dpi int = 300,
    attempts_per_page int = 3,
    use_cot bool = True,
    max_pages int = None,
    skip_processed bool = True,
    save_detailed bool = False
)
    
    Process all PDFs found in a directory tree with OCR.

    This function orchestrates batch OCR processing across multiple files. It handles
    errors gracefully so that one failed PDF doesn't stop the entire batch. Progress
    is tracked and reported to help monitor long-running jobs.

    Args
        root_directory Root directory to search for PDFs
        dpi Resolution for PDF rendering
        attempts_per_page Number of OCR attempts per page
        use_cot Whether to use Chain-of-Thought prompting
        max_pages Optional limit on pages per PDF (useful for testing)
        skip_processed Skip PDFs that already have output files
        save_detailed If True, also save detailed JSON output with all attempts

    Returns
        Dictionary with batch processing statistics
    

    print(=  80)
    print(BATCH PDF OCR PROCESSING)
    print(=  80)

    # Find all PDFs to process
    pdf_files = find_pdf_files(root_directory, skip_processed=skip_processed)

    if not pdf_files
        print(nNo PDFs found to process.)
        return {
            'total_files' 0,
            'successful' 0,
            'failed' 0,
            'skipped' 0
        }

    # Track batch statistics
    total_files = len(pdf_files)
    successful = 0
    failed = 0
    failed_files = []
    batch_start = time.time()

    print(fnProcessing {total_files} PDF file(s)...n)

    # Process each PDF individually
    # Using enumerate for progress tracking
    for idx, (pdf_path, output_path) in enumerate(pdf_files, 1)
        print(=  80)
        print(fFILE {idx}{total_files} {os.path.basename(pdf_path)})
        print(fLocation {os.path.dirname(pdf_path)})
        print(=  80)

        try
            # Run OCR on the PDF
            # Each file is processed independently to isolate errors
            results, full_text = ocr_pdf(
                pdf_path=pdf_path,
                dpi=dpi,
                attempts_per_page=attempts_per_page,
                use_cot=use_cot,
                max_pages=max_pages
            )
            
            save_results(
                results=results,
                full_text=full_text,
                filename=output_path,
                include_metadata=False
            )

            successful += 1
            print(fnFile {idx}{total_files} completed successfully)

        except Exception as e
            raise Exception

inp = input(!!!!!!!!Expensive claude run!!!!!!!!!!! type NO to cancel anything else to proceed ).lower()
if inp=='no' or inp == 'n'
    exit()
results = process_pdf_batch(
    root_directory='guidelineExtractorsguidelines',
    dpi=300,
    attempts_per_page=1,
    skip_processed=True,
    save_detailed=True,
    # max_pages=2
)