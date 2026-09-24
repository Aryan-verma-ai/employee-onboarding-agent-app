"""Extract candidate portrait photos from PAN, Aadhaar, and Resume documents.

Strategy:
 - PAN / Aadhaar (image scans): Use Pillow to detect face-like regions via
   skin-tone colour analysis and portrait-aspect-ratio heuristics, then crop.
 - Resume (PDF): Use pymupdf (fitz) to extract embedded images and pick the
   best portrait-proportioned one (likely the candidate photo).
 - Fallback: If no face region is found, return None (no photo stored).
"""

import io
import logging
from typing import Optional

log = logging.getLogger(__name__)


def _is_portrait_ratio(w: int, h: int) -> bool:
    """Check if dimensions have portrait-like aspect ratio (taller than wide, or roughly square)."""
    if w < 40 or h < 40:
        return False
    ratio = h / w
    return 0.8 <= ratio <= 2.0  # Portrait photos are taller than wide


def _has_skin_tones(img, threshold: float = 0.08) -> bool:
    """Rough check whether image contains enough skin-tone-ish pixels (works across skin colours)."""
    try:
        small = img.resize((80, 80)).convert("RGB")
        raw = small.tobytes()
        total_pixels = len(raw) // 3
        skin_count = 0
        for i in range(0, len(raw), 3):
            r, g, b = raw[i], raw[i + 1], raw[i + 2]
            if r > 60 and g > 40 and b > 20 and abs(r - g) < 80 and r > b:
                skin_count += 1
        return (skin_count / total_pixels) > threshold
    except Exception:
        return False


def _is_skin_pixel(r: int, g: int, b: int) -> bool:
    """Check if RGB values match human skin-tone range across all skin colours."""
    return r > 60 and g > 40 and b > 20 and abs(r - g) < 80 and r > b and (r - g) > 5


def _find_face_region_from_image(img) -> Optional[tuple]:
    """Find the portrait photo region in an ID card image.

    PAN and Aadhaar cards have a standardized layout:
    - The portrait photo is located in the upper-left quadrant (below the top header banner,
      above the bottom signature/UID band, and to the left of the name/text fields).
    - It takes up roughly 25-32% of card width and 40-48% of card height.
    - We use skin-tone pixel clustering to locate the candidate's actual headshot and crop
      with portrait margins (hair, face, shoulders).
    - Fallback: A standard ID photo frame is used if the photo is monochrome/grayscale,
      ensuring we NEVER crop the entire card or include signatures/headers.
    """
    w, h = img.size
    is_landscape = w >= h

    zones = []
    if is_landscape:
        # Standard PAN/Aadhaar photo is strictly in the upper-left quadrant
        # (below header, above signature/footer, left of text fields)
        zones.append((int(w * 0.03), int(h * 0.14), int(w * 0.40), int(h * 0.68)))
        # Right quadrant for cards with photo on the right side
        zones.append((int(w * 0.60), int(h * 0.14), int(w * 0.97), int(h * 0.68)))
    else:
        # Vertical image / smartphone photo of card
        zones.append((int(w * 0.05), int(h * 0.15), int(w * 0.55), int(h * 0.65)))
        zones.append((int(w * 0.10), int(h * 0.08), int(w * 0.90), int(h * 0.50)))

    from PIL import Image

    # Pass 1: Skin-tone cluster detection (crops strictly around headshot/portrait)
    for zx1, zy1, zx2, zy2 in zones:
        zone = img.crop((zx1, zy1, zx2, zy2))
        zw, zh = zone.size
        # Downscale zone for fast skin-tone clustering if it's large
        scale = 1.0
        if zw > 200 or zh > 200:
            scale = 200.0 / max(zw, zh)
            small_w = max(1, int(zw * scale))
            small_h = max(1, int(zh * scale))
            proc_zone = zone.resize((small_w, small_h), Image.BILINEAR)
        else:
            proc_zone = zone

        pw, ph = proc_zone.size
        raw = proc_zone.tobytes()
        skin_pts = []
        for y in range(0, ph, 2):
            row_offset = y * pw * 3
            for x in range(0, pw, 2):
                idx = row_offset + x * 3
                if _is_skin_pixel(raw[idx], raw[idx + 1], raw[idx + 2]):
                    skin_pts.append((int(x / scale), int(y / scale)))

        if len(skin_pts) >= 20:
            xs = sorted(p[0] for p in skin_pts)
            ys = sorted(p[1] for p in skin_pts)
            n = len(xs)
            min_x, max_x = xs[int(n * 0.05)], xs[int(n * 0.95)]
            min_y, max_y = ys[int(n * 0.05)], ys[int(n * 0.95)]

            fw = max_x - min_x
            fh = max_y - min_y
            if fw >= 20 and fh >= 20:
                pad_top = int(fh * 0.40)
                pad_bot = int(fh * 0.45)
                pad_side = int(fw * 0.35)

                crop_x1 = max(0, min_x - pad_side)
                crop_y1 = max(0, min_y - pad_top)
                crop_x2 = min(zw, max_x + pad_side)
                crop_y2 = min(zh, max_y + pad_bot)

                cw = crop_x2 - crop_x1
                ch = crop_y2 - crop_y1
                if cw > 30 and ch > 30 and 0.75 <= (ch / cw) <= 1.8:
                    return (zx1 + crop_x1, zy1 + crop_y1, zx1 + crop_x2, zy1 + crop_y2)

    # Pass 2: Fallback to standard photo frame for monochrome/grayscale ID photos
    if is_landscape:
        # Standard ID photo frame: strictly 27% width, 44% height (never the full card!)
        frame_x1, frame_y1, frame_x2, frame_y2 = (
            int(w * 0.05),
            int(h * 0.18),
            int(w * 0.32),
            int(h * 0.62),
        )
        sample = img.crop((frame_x1, frame_y1, frame_x2, frame_y2)).convert("L")
        sample_small = sample.resize((100, 100))
        pixels = list(sample_small.tobytes())
        mean = sum(pixels) / len(pixels)
        variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)
        std_dev = variance**0.5
        if std_dev > 15:  # Non-trivial portrait content (not blank white/plain background)
            return (frame_x1, frame_y1, frame_x2, frame_y2)

    return None


def extract_photo_from_id_card(content: bytes) -> Optional[tuple[bytes, str]]:
    """Extract the portrait photo from a PAN or Aadhaar card image.

    Args:
        content: Raw image bytes (JPEG or PNG).

    Returns:
        Tuple of (cropped_photo_bytes, content_type) or None if no face found.
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(content))
        img = img.convert("RGB")

        # Only process if image is large enough to be an ID card scan
        w, h = img.size
        if w < 200 or h < 100:
            log.debug("Image too small to be an ID card: %dx%d", w, h)
            return None

        region = _find_face_region_from_image(img)
        if not region:
            log.info("No face region detected in ID card image")
            return None

        x1, y1, x2, y2 = region
        face_crop = img.crop((x1, y1, x2, y2))

        # Resize to a standard portrait size (maintaining aspect ratio)
        max_dim = 400
        fw, fh = face_crop.size
        if fw > max_dim or fh > max_dim:
            scale = max_dim / max(fw, fh)
            face_crop = face_crop.resize((int(fw * scale), int(fh * scale)), Image.LANCZOS)

        # Validate the crop has reasonable content (not blank/white)
        if not _has_skin_tones(face_crop, threshold=0.03):
            log.info("Cropped region does not contain face-like content")
            return None

        buf = io.BytesIO()
        face_crop.save(buf, format="JPEG", quality=90)
        return buf.getvalue(), "image/jpeg"

    except Exception as exc:
        log.warning("Failed to extract photo from ID card: %s", exc)
        return None


def extract_photo_from_pdf(content: bytes) -> Optional[tuple[bytes, str]]:
    """Extract the best portrait-proportioned embedded image from a PDF resume.

    Args:
        content: Raw PDF bytes.

    Returns:
        Tuple of (image_bytes, content_type) or None if no suitable image found.
    """
    try:
        import fitz  # pymupdf

        doc = fitz.open(stream=content, filetype="pdf")
        best_image = None
        best_score = 0

        for page_num in range(min(len(doc), 3)):  # Only check first 3 pages
            page = doc[page_num]
            images = page.get_images(full=True)

            for img_info in images:
                xref = img_info[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    w, h = pix.width, pix.height

                    # Skip tiny images (logos, icons, decorations)
                    if w < 60 or h < 60:
                        continue
                    # Skip very large images (full-page scans, backgrounds)
                    if w > 2000 or h > 2000:
                        continue

                    # Score: prefer portrait-proportioned images of reasonable size
                    score = 0
                    area = w * h
                    ratio = h / w

                    # Portrait ratio bonus (0.8 to 2.0)
                    if 0.8 <= ratio <= 2.0:
                        score += 50
                    elif 0.6 <= ratio <= 2.5:
                        score += 20

                    # Size bonus — prefer medium-sized images (typical passport photos)
                    if 5000 < area < 500000:
                        score += 30
                    elif 3000 < area < 1000000:
                        score += 15

                    # Page 1 bonus (photos are usually on page 1 of resumes)
                    if page_num == 0:
                        score += 20

                    if score <= best_score:
                        if pix.alpha:
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        continue

                    # Convert to RGB if needed
                    if pix.alpha:
                        pix = fitz.Pixmap(fitz.csRGB, pix)

                    img_bytes = pix.tobytes("jpeg")

                    # Validate skin tones
                    from PIL import Image as PILImage

                    pil_img = PILImage.open(io.BytesIO(img_bytes))
                    if _has_skin_tones(pil_img, threshold=0.03):
                        score += 25

                    if score > best_score:
                        best_score = score
                        best_image = img_bytes

                except Exception as exc:
                    log.debug("Failed to extract image xref %d: %s", xref, exc)
                    continue

        doc.close()

        if best_image and best_score >= 50:
            # Resize if too large
            from PIL import Image as PILImage

            pil_img = PILImage.open(io.BytesIO(best_image))
            max_dim = 400
            w, h = pil_img.size
            if w > max_dim or h > max_dim:
                scale = max_dim / max(w, h)
                pil_img = pil_img.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
                buf = io.BytesIO()
                pil_img.save(buf, format="JPEG", quality=90)
                best_image = buf.getvalue()

            log.info("Extracted portrait photo from PDF (score=%d)", best_score)
            return best_image, "image/jpeg"

        log.info("No suitable portrait image found in PDF")
        return None

    except ImportError:
        log.warning("pymupdf not installed — cannot extract photos from PDFs")
        return None
    except Exception as exc:
        log.warning("Failed to extract photo from PDF: %s", exc)
        return None


def extract_photo_from_document(
    content: bytes, doc_type: str, content_type: str
) -> Optional[tuple[bytes, str]]:
    """Main entry point: extract a portrait photo from a document.

    Args:
        content: Raw document bytes.
        doc_type: Document classification ('pan', 'aadhaar', 'resume', etc.).
        content_type: MIME type of the document.

    Returns:
        Tuple of (photo_bytes, photo_content_type) or None.
    """
    ct = (content_type or "").lower()
    dt = (doc_type or "").lower()

    # PAN / Aadhaar cards are image scans
    if dt in ("pan", "aadhaar") and ct.startswith("image/"):
        return extract_photo_from_id_card(content)

    # PAN / Aadhaar as PDF (less common but possible)
    if dt in ("pan", "aadhaar") and ct == "application/pdf":
        return extract_photo_from_pdf(content)

    # Resume — typically PDF
    if dt == "resume" and ct == "application/pdf":
        return extract_photo_from_pdf(content)

    # Resume as image (scanned resume) — try face detection
    if dt == "resume" and ct.startswith("image/"):
        return extract_photo_from_id_card(content)

    return None
