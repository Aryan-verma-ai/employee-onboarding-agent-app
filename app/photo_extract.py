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
        pixels = list(small.getdata())
        skin_count = 0
        for r, g, b in pixels:
            # Broad skin-tone heuristic covering light to dark skin
            if r > 60 and g > 40 and b > 20:
                if abs(r - g) < 80 and r > b:
                    skin_count += 1
        return (skin_count / len(pixels)) > threshold
    except Exception:
        return False


def _find_face_region_from_image(img) -> Optional[tuple]:
    """Find the face region in an ID card image using colour-based segmentation.

    PAN/Aadhaar cards have a standard layout: photo is typically on the left or
    right side, taking up about 25-35% of the card width.
    We try candidate regions and pick the one with the most skin-tone pixels.
    """

    w, h = img.size

    # Standard ID card photo positions (as fraction of card dimensions)
    # PAN: photo is on the left side, roughly 25-35% from left edge
    # Aadhaar: photo is on the left side too
    candidate_regions = [
        # Left side portrait region (PAN/Aadhaar typical)
        (0, int(h * 0.15), int(w * 0.35), int(h * 0.95)),
        # Slightly inset left
        (int(w * 0.02), int(h * 0.20), int(w * 0.32), int(h * 0.90)),
        # Right side (some card layouts)
        (int(w * 0.65), int(h * 0.15), w, int(h * 0.95)),
        # Center-left
        (int(w * 0.05), int(h * 0.10), int(w * 0.40), int(h * 0.85)),
        # Wider left region
        (0, int(h * 0.05), int(w * 0.42), h),
    ]

    best_region = None
    best_score = 0

    for x1, y1, x2, y2 in candidate_regions:
        region = img.crop((x1, y1, x2, y2))
        rw, rh = region.size
        if rw < 30 or rh < 30:
            continue

        if not _is_portrait_ratio(rw, rh):
            continue

        # Score by skin-tone density
        small = region.resize((60, 60)).convert("RGB")
        pixels = list(small.getdata())
        skin_count = sum(
            1 for r, g, b in pixels if r > 60 and g > 40 and b > 20 and abs(r - g) < 80 and r > b
        )
        score = skin_count / len(pixels)
        if score > best_score and score > 0.05:
            best_score = score
            best_region = (x1, y1, x2, y2)

    return best_region


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
