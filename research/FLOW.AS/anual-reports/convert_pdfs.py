#!/usr/bin/env python3

import fitz
import os
from pathlib import Path

def convert_pdf_to_markdown(pdf_path, output_path):
    print(f"Converting {pdf_path.name}...")

    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    markdown_content = []
    markdown_content.append(f"# {pdf_path.stem}\n\n")

    for page_num in range(total_pages):
        print(f"  Processing page {page_num + 1}/{total_pages}...", end='\r')
        page = doc[page_num]

        text = page.get_text()

        if text.strip():
            markdown_content.append(f"## Page {page_num + 1}\n\n")
            markdown_content.append(text)
            markdown_content.append("\n\n---\n\n")

    doc.close()

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(''.join(markdown_content))

    print(f"\n  ✓ Saved to {output_path.name}")

def main():
    pdf_dir = Path("pdf")
    output_dir = Path(".")

    if not pdf_dir.exists():
        print(f"Error: {pdf_dir} directory not found")
        return

    pdf_files = sorted(pdf_dir.glob("*.pdf"))

    if not pdf_files:
        print(f"No PDF files found in {pdf_dir}")
        return

    print(f"Found {len(pdf_files)} PDF files\n")

    for pdf_file in pdf_files:
        output_file = output_dir / f"{pdf_file.stem}.md"
        try:
            convert_pdf_to_markdown(pdf_file, output_file)
        except Exception as e:
            print(f"\n  ✗ Error processing {pdf_file.name}: {e}")

    print("\nConversion complete!")

if __name__ == "__main__":
    main()
