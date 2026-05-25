# PDF Image Crop Tool

Desktop-style **full-stack local app**: drag & drop files, pick output folder, DPI, color mode, start/stop — crops saved at **original pixel size** (no resize).

## Features

- Drag & drop PDF/images (or browse)
- Choose **output folder** (Browse opens Windows folder dialog)
- **DPI**: 300 / 600 / 1200 (PDF page render for scanned pages)
- **Color mode**: Grayscale, RGB, CMYK, Bitmap
- **Start** / **Stop** cropping
- Embedded PDF images saved at native resolution; crops are not downscaled
- **DPI embedded in every saved file** — Acrobat Preflight shows your chosen PPI (not 72)

## Setup

```bash
cd pdf_image_crop_ai_tool
pip install -r requirements.txt
```

## Run web UI (recommended)

```bash
python server.py
```

Open: **http://127.0.0.1:8000**

1. Drop files on the page (anywhere)
2. Browse → select output folder
3. Pick DPI and color mode
4. **Start cropping** — use **Stop** to cancel

## Run CLI (simple)

```bash
mkdir input
# copy PDF/images into input/
python app.py
# results in output/
```

## Output file naming

`{file_name}_p{page}_crop_{number}.{ext}`

Example: `MyBook_p43_crop_1.png`, `MyBook_p44_crop_1.tif`

## Output format (choose before Process)

| Format | Extension | Use |
|--------|-----------|-----|
| **PNG** | `.png` | Colour images (default) |
| **TIFF** | `.tif` | Print / CMYK workflow |
| **PDF** | `.pdf` | One crop per single-page PDF |

**Colour Mode** (RGB / Grayscale / CMYK) controls colour space; **Output Format** controls file type.

## Colour mode + format

| Colour Mode | PNG | TIFF | PDF |
|-------------|-----|------|-----|
| RGB | RGB PNG | RGB TIFF | Image PDF |
| Grayscale | Gray PNG | Gray TIFF | Image PDF |
| CMYK | — | CMYK TIFF | Image PDF |

## Project layout

```text
pdf_image_crop_ai_tool/
├── server.py          # Web UI server
├── crop_engine.py     # Crop logic
├── app.py             # CLI
├── static/            # Frontend
├── uploads/           # Temp uploads (auto)
└── output/            # Default CLI output
```
