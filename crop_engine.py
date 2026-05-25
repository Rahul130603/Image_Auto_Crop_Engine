"""Image/PDF crop engine — configurable DPI, color mode, cancel, original pixel size."""

from __future__ import annotations

import cv2
import fitz
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from PIL import Image

MIN_REGION_FRACTION = 0.01
MAX_REGION_FRACTION = 0.93
WHITE_TRIM_TOLERANCE = 14
FINAL_PAD = 2
PNG_COMPRESSION = 1

COLOR_MODES = ("grayscale", "rgb", "cmyk", "bitmap")
OUTPUT_FORMATS = ("png", "tiff", "pdf")
DPI_OPTIONS = (300, 600, 1200)

ProgressCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]
StatusCallback = Callable[[dict], None]


def sanitize_source_name(name: str) -> str:
    """Clean file name for output: no extension, no invalid path characters."""
    base = Path(name).stem
    for ch in '<>:"/\\|?*':
        base = base.replace(ch, "_")
    base = base.strip(" .")
    return base or "document"


def crop_output_filename(source_name: str, page_no: int, crop_no: int, ext: str) -> str:
    """Format: file_name_p{page}_crop_{crop}.ext"""
    safe = sanitize_source_name(source_name)
    return f"{safe}_p{page_no}_crop_{crop_no}{ext}"


@dataclass
class CropSettings:
    output_dir: Path
    dpi: int = 300
    color_mode: str = "rgb"
    output_format: str = "png"
    source_name: str = "document"
    preserve_original_pixels: bool = True
    on_progress: Optional[ProgressCallback] = None
    on_status: Optional[StatusCallback] = None
    should_cancel: Optional[CancelCallback] = None
    _saved_total: int = 0

    def log(self, message: str) -> None:
        if self.on_progress:
            self.on_progress(message)

    def status(self, **kwargs) -> None:
        if self.on_status:
            self.on_status(kwargs)

    def cancelled(self) -> bool:
        return bool(self.should_cancel and self.should_cancel())


def dpi_to_zoom(dpi: int) -> float:
    return max(dpi, 72) / 72.0


def is_color_image(bgr: np.ndarray) -> bool:
    """True when image has meaningful color (not grayscale)."""
    if bgr is None or bgr.size == 0 or len(bgr.shape) < 3:
        return False
    b, g, r = cv2.split(bgr.astype(np.int16))
    channel_diff = float(np.mean(np.abs(b - g)) + np.mean(np.abs(g - r)))
    return channel_diff > 4.0


def extension_for_output(output_format: str) -> str:
    fmt = output_format.lower()
    if fmt == "tiff":
        return ".tif"
    if fmt == "pdf":
        return ".pdf"
    return ".png"


def save_image(
    bgr: np.ndarray,
    out_path: Path,
    color_mode: str,
    dpi: int = 300,
) -> None:
    """Save crop with embedded DPI so Acrobat Preflight reports the chosen resolution."""
    mode = color_mode.lower()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dpi_tuple = (float(dpi), float(dpi))

    if mode == "grayscale":
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        pil = Image.fromarray(gray, mode="L")
        pil.save(
            str(out_path),
            dpi=dpi_tuple,
            compress_level=PNG_COMPRESSION,
        )
        return

    if mode == "bitmap":
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        pil.save(str(out_path), dpi=dpi_tuple)
        return

    if mode == "cmyk":
        if out_path.suffix.lower() == ".png":
            save_cmyk_png(bgr, out_path, dpi=dpi)
        else:
            save_cmyk_image(bgr, out_path, dpi=dpi)
        return

    if mode == "rgb_tiff":
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        pil.save(
            str(out_path),
            format="TIFF",
            compression="tiff_lzw",
            dpi=dpi_tuple,
            resolution_unit=2,
        )
        return

    if mode == "grayscale_tiff":
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        pil = Image.fromarray(gray, mode="L")
        pil.save(
            str(out_path),
            format="TIFF",
            compression="tiff_lzw",
            dpi=dpi_tuple,
            resolution_unit=2,
        )
        return

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    pil.save(
        str(out_path),
        dpi=dpi_tuple,
        compress_level=PNG_COMPRESSION,
    )


def bgr_to_cmyk_pil(bgr: np.ndarray) -> Image.Image:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb).convert("CMYK")


def save_cmyk_raster(bgr: np.ndarray, out_path: Path, dpi: int = 300) -> None:
    """Save true CMYK with embedded DPI (TIFF raster; .tif or .png extension)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dpi_tuple = (float(dpi), float(dpi))
    bgr_to_cmyk_pil(bgr).save(
        str(out_path),
        format="TIFF",
        compression="tiff_lzw",
        dpi=dpi_tuple,
        resolution_unit=2,
    )


def save_cmyk_png(bgr: np.ndarray, out_path: Path, dpi: int = 300) -> None:
    save_cmyk_raster(bgr, out_path, dpi=dpi)


def save_cmyk_image(bgr: np.ndarray, out_path: Path, dpi: int = 300) -> None:
    save_cmyk_raster(bgr, out_path, dpi=dpi)


def patch_page_images_device_cmyk(page: fitz.Page) -> None:
    """Use /DeviceCMYK so Preflight shows ppi + CMYK (not CMYK with ICC profile)."""
    doc = page.parent
    for im in page.get_images():
        if len(im) > 5 and im[5] == "ICCBased":
            doc.xref_set_key(im[0], "ColorSpace", "/DeviceCMYK")


def save_as_pdf(
    bgr: np.ndarray,
    out_path: Path,
    dpi: int = 300,
    color_mode: str = "rgb",
) -> None:
    """Save one crop as PDF using the user-selected colour mode."""
    import io

    out_path.parent.mkdir(parents=True, exist_ok=True)
    h, w = bgr.shape[:2]
    dpi = max(dpi, 72)
    page_w = w * 72.0 / dpi
    page_h = h * 72.0 / dpi
    rect = fitz.Rect(0, 0, page_w, page_h)
    mode = color_mode.lower()
    dpi_tuple = (float(dpi), float(dpi))

    doc = fitz.open()
    try:
        page = doc.new_page(width=page_w, height=page_h)
        if mode == "cmyk":
            pil_cmyk = bgr_to_cmyk_pil(bgr)
            arr = np.ascontiguousarray(np.array(pil_cmyk))
            pix = fitz.Pixmap(fitz.csCMYK, w, h, arr.tobytes(), 0)
            page.insert_image(rect, pixmap=pix)
            pix = None
            patch_page_images_device_cmyk(page)
        elif mode == "grayscale":
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            buf = io.BytesIO()
            Image.fromarray(gray, mode="L").save(buf, format="PNG")
            page.insert_image(rect, stream=buf.getvalue())
        else:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            buf = io.BytesIO()
            Image.fromarray(rgb).save(buf, format="PNG")
            page.insert_image(rect, stream=buf.getvalue())
        doc.save(str(out_path), deflate=True)
    finally:
        doc.close()


def trim_white_borders(bgr: np.ndarray, tolerance: int = WHITE_TRIM_TOLERANCE) -> np.ndarray:
    if bgr is None or bgr.size == 0:
        return bgr

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    content = gray < (255 - tolerance)
    if not np.any(content):
        return bgr

    ys, xs = np.where(content)
    y1 = max(int(ys.min()) - FINAL_PAD, 0)
    y2 = min(int(ys.max()) + 1 + FINAL_PAD, bgr.shape[0])
    x1 = max(int(xs.min()) - FINAL_PAD, 0)
    x2 = min(int(xs.max()) + 1 + FINAL_PAD, bgr.shape[1])
    return bgr[y1:y2, x1:x2]


def tight_content_crop(bgr: np.ndarray) -> np.ndarray:
    if bgr is None or bgr.size == 0:
        return bgr

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, ink = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    edges = cv2.Canny(blur, 50, 150)
    mask = cv2.bitwise_or(ink, edges)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    min_pixels = max(80, int(h * w * 0.00015))
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    boxes = []
    for i in range(1, num_labels):
        if stats[i, cv2.CC_STAT_AREA] < min_pixels:
            continue
        boxes.append(
            (
                stats[i, cv2.CC_STAT_LEFT],
                stats[i, cv2.CC_STAT_TOP],
                stats[i, cv2.CC_STAT_WIDTH],
                stats[i, cv2.CC_STAT_HEIGHT],
            )
        )

    if not boxes:
        return trim_white_borders(bgr)

    x1 = max(min(b[0] for b in boxes) - FINAL_PAD, 0)
    y1 = max(min(b[1] for b in boxes) - FINAL_PAD, 0)
    x2 = min(max(b[0] + b[2] for b in boxes) + FINAL_PAD, w)
    y2 = min(max(b[1] + b[3] for b in boxes) + FINAL_PAD, h)
    return trim_white_borders(bgr[y1:y2, x1:x2])


def _page_limits(page_h: int, page_w: int):
    image_area = page_h * page_w
    min_area = max(25000, int(image_area * MIN_REGION_FRACTION))
    kernel = max(15, min(page_w, page_h) // 35)
    if kernel % 2 == 0:
        kernel += 1
    return image_area, min_area, kernel


def _is_text_block(gray_roi: np.ndarray, w: int, h: int) -> bool:
    binary_roi = cv2.threshold(
        gray_roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]
    horizontal_projection = np.sum(binary_roi > 0, axis=1)
    text_rows = sum(1 for val in horizontal_projection if w * 0.05 < val < w * 0.72)
    text_ratio = text_rows / max(h, 1)
    edges = cv2.Canny(gray_roi, 80, 200)
    edge_ratio = np.sum(edges > 0) / max(w * h, 1)
    return text_ratio > 0.72 and edge_ratio < 0.018


def _is_visual_content(bgr_roi: np.ndarray) -> bool:
    if bgr_roi.size == 0:
        return False
    hsv = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2HSV)
    mean_sat = float(np.mean(hsv[:, :, 1]))
    color_std = float(np.std(bgr_roi))
    gray = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 180)
    edge_ratio = np.sum(edges > 0) / max(bgr_roi.shape[0] * bgr_roi.shape[1], 1)
    if mean_sat > 22 or color_std > 32:
        return True
    if edge_ratio > 0.02:
        return True
    return not _is_text_block(gray, bgr_roi.shape[1], bgr_roi.shape[0])


def _overlap_too_much(box, saved_boxes) -> bool:
    x, y, w, h = box
    area = w * h
    for sx, sy, sw, sh in saved_boxes:
        ix1, iy1 = max(x, sx), max(y, sy)
        ix2, iy2 = min(x + w, sx + sw), min(y + h, sy + sh)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        smaller = min(area, sw * sh)
        if smaller > 0 and inter / smaller > 0.55:
            return True
    return False


def find_visual_regions(page_image: np.ndarray) -> List[tuple]:
    page_h, page_w = page_image.shape[:2]
    image_area, min_area, kernel_size = _page_limits(page_h, page_w)
    gray = cv2.cvtColor(page_image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (7, 7), 0)
    thresh = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    morph = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    morph = cv2.morphologyEx(
        morph, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    )
    contours, _ = cv2.findContours(morph, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    saved_boxes = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if y < page_h * 0.04 or y + h > page_h * 0.985:
            continue
        area = w * h
        aspect = w / float(max(h, 1))
        if area < min_area or area > image_area * MAX_REGION_FRACTION:
            continue
        if aspect > 22 or aspect < 0.2:
            continue
        if not _is_visual_content(page_image[y : y + h, x : x + w]):
            continue
        box = (x, y, w, h)
        if _overlap_too_much(box, saved_boxes):
            continue
        saved_boxes.append(box)
    saved_boxes.sort(key=lambda b: (b[1], b[0]))
    return saved_boxes


def estimate_native_dpi(img_w: int, img_h: int, page) -> int:
    """Estimate DPI from embedded image pixels vs PDF page size (points)."""
    rect = page.rect
    if rect.width <= 0 or rect.height <= 0:
        return 300
    dpi_x = img_w * 72.0 / rect.width
    dpi_y = img_h * 72.0 / rect.height
    return max(72, int(round((dpi_x + dpi_y) / 2)))


def crop_region(
    page_image: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    tight_trim: bool = False,
) -> np.ndarray:
    """Extract exact source pixels — no resize, optional margin trim only if requested."""
    pad = 8
    x1, y1 = max(x - pad, 0), max(y - pad, 0)
    x2 = min(x + w + pad, page_image.shape[1])
    y2 = min(y + h + pad, page_image.shape[0])
    crop = page_image[y1:y2, x1:x2].copy()
    if tight_trim:
        return tight_content_crop(crop)
    return crop


def _pixmap_to_bgr(pix) -> np.ndarray:
    img = np.frombuffer(pix.samples, dtype=np.uint8)
    if pix.n == 4:
        img = img.reshape(pix.height, pix.width, 4)
        return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    img = img.reshape(pix.height, pix.width, 3)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def _embedded_covers_page(img_w: int, img_h: int, page) -> bool:
    rect = page.rect
    return img_w >= rect.width * 1.2 and img_h >= rect.height * 1.2


def extract_embedded_images(doc, page) -> List[np.ndarray]:
    images = []
    for item in page.get_images(full=True):
        try:
            base = doc.extract_image(item[0])
        except Exception:
            continue
        data = base.get("image")
        if not data:
            continue
        decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is not None:
            images.append(decoded)
    return images


def _write_crop(
    settings: CropSettings,
    bgr: np.ndarray,
    page_no: int,
    crop_no: int,
) -> bool:
    if bgr is None or bgr.size == 0 or bgr.shape[0] < 40 or bgr.shape[1] < 40:
        return False

    color = settings.color_mode.lower()
    ext = extension_for_output(settings.output_format)
    out_name = crop_output_filename(settings.source_name, page_no, crop_no, ext)
    out_path = settings.output_dir / out_name
    save_dpi = settings.dpi

    if color == "cmyk":
        if ext == ".pdf":
            save_as_pdf(bgr, out_path, dpi=save_dpi, color_mode="cmyk")
        elif ext == ".png":
            save_cmyk_png(bgr, out_path, dpi=save_dpi)
        else:
            save_cmyk_image(bgr, out_path, dpi=save_dpi)
    elif ext == ".pdf":
        save_as_pdf(bgr, out_path, dpi=save_dpi, color_mode=color)
    elif ext == ".tif":
        if color == "grayscale":
            save_image(bgr, out_path, "grayscale_tiff", dpi=save_dpi)
        else:
            save_image(bgr, out_path, "rgb_tiff", dpi=save_dpi)
    elif ext == ".png":
        if color == "grayscale":
            save_image(bgr, out_path, "grayscale", dpi=save_dpi)
        else:
            save_image(bgr, out_path, "rgb", dpi=save_dpi)
    else:
        save_image(bgr, out_path, "rgb", dpi=save_dpi)

    settings._saved_total += 1
    settings.log(
        f"Saved {bgr.shape[1]}x{bgr.shape[0]} px — "
        f"{settings.color_mode.upper()} @ {save_dpi} DPI → {out_path.name}"
    )
    settings.status(
        images_saved=settings._saved_total,
        last_output=str(out_path),
    )
    return True


def crop_images_from_page(
    page_image: np.ndarray,
    settings: CropSettings,
    page_no: int,
    crop_start: int = 1,
) -> int:
    crop_no = crop_start
    saved = 0
    tight = not settings.preserve_original_pixels
    for x, y, w, h in find_visual_regions(page_image):
        if settings.cancelled():
            break
        crop = crop_region(page_image, x, y, w, h, tight_trim=tight)
        if _write_crop(settings, crop, page_no, crop_no):
            saved += 1
            crop_no += 1
    return saved


def process_pdf(pdf_path: Path, settings: CropSettings) -> int:
    doc = fitz.open(pdf_path)
    total = 0
    page_count = len(doc)

    settings.status(
        total_pages=page_count,
        current_page=0,
        current_file=pdf_path.name,
        progress_percent=0,
    )

    try:
        for page_no in range(page_count):
            if settings.cancelled():
                settings.log("Stopped by user.")
                break

            page = doc[page_no]
            page_num = page_no + 1
            pct = int(100 * (page_num) / max(page_count, 1))
            settings.status(
                current_page=page_num,
                total_pages=page_count,
                progress_percent=pct,
                queue_status="running",
            )
            settings.log(f"Processing page {page_num} / {page_count}")

            embedded = extract_embedded_images(doc, page)
            page_crop_no = 1
            if embedded:
                for img in embedded:
                    if settings.cancelled():
                        break
                    if _embedded_covers_page(img.shape[1], img.shape[0], page):
                        settings.log(
                            f"  Native {img.shape[1]}x{img.shape[0]} px — "
                            f"detecting figures (save @ {settings.dpi} DPI, "
                            f"{settings.color_mode.upper()})"
                        )
                        n = crop_images_from_page(
                            img, settings, page_num, page_crop_no
                        )
                        total += n
                        page_crop_no += n
                    else:
                        crop = img.copy()
                        if _write_crop(settings, crop, page_num, page_crop_no):
                            total += 1
                            page_crop_no += 1
                continue

            # Scanned page without embed — render at chosen DPI, crop 1:1 pixels
            pix = page.get_pixmap(dpi=settings.dpi)
            page_img = _pixmap_to_bgr(pix)
            settings.log(
                f"  Rendered at {settings.dpi} DPI "
                f"({page_img.shape[1]}x{page_img.shape[0]} px) — original pixel crop"
            )
            total += crop_images_from_page(page_img, settings, page_num, 1)
    finally:
        doc.close()

    return total


def process_image_file(image_path: Path, settings: CropSettings) -> int:
    settings.status(
        total_pages=1,
        current_page=1,
        current_file=image_path.name,
        progress_percent=50,
        queue_status="running",
    )
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        settings.log(f"Cannot read: {image_path.name}")
        return 0
    settings.log(
        f"Image {settings.source_name} — original size {img.shape[1]}x{img.shape[0]} px"
    )
    count = crop_images_from_page(img, settings, page_no=1, crop_start=1)
    settings.status(progress_percent=100)
    return count


def run_crop_job(
    file_paths: List[Path],
    settings: CropSettings,
    display_names: Optional[List[str]] = None,
) -> int:
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings._saved_total = 0
    total_saved = 0
    image_exts = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
    n_files = len(file_paths)

    for file_index, file_path in enumerate(file_paths):
        if settings.cancelled():
            break

        if display_names and file_index < len(display_names):
            settings.source_name = sanitize_source_name(display_names[file_index])
        else:
            settings.source_name = sanitize_source_name(file_path.name)

        ext = file_path.suffix.lower()
        settings.log(f"Processing: {settings.source_name}{ext}")
        settings.status(current_file=settings.source_name + ext, queue_status="running")

        if ext == ".pdf":
            total_saved += process_pdf(file_path, settings)
        elif ext in image_exts:
            total_saved += process_image_file(file_path, settings)
        else:
            settings.log(f"Skipped unsupported: {file_path.name}")

        if n_files > 1:
            settings.status(
                progress_percent=int(100 * (file_index + 1) / n_files)
            )

    settings.status(progress_percent=100, queue_status="done")
    settings.log(f"Finished. {total_saved} image(s) saved to {settings.output_dir}")
    return total_saved
