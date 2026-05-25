"""CLI entry — uses input/ and output/ folders."""

from pathlib import Path

from crop_engine import CropSettings, run_crop_job

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def main() -> None:
    if not INPUT_DIR.exists():
        print("Create 'input' folder and put PDF/images inside.")
        raise SystemExit(1)

    files = sorted(
        f for f in INPUT_DIR.iterdir() if f.is_file() and not f.name.startswith(".")
    )
    if not files:
        print("No files found in input/.")
        raise SystemExit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)
    settings = CropSettings(
        output_dir=OUTPUT_DIR,
        dpi=300,
        color_mode="rgb",
        on_progress=print,
    )
    run_crop_job(files, settings)


if __name__ == "__main__":
    main()
