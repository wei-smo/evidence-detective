"""Reproduce role crops from the authorized 3-column, 2-row RGBA original.

Requires Pillow, numpy and scipy. No generation, repainting or color changes.
Run: python tools/crop_roles.py [--source PATH]
Without --source, reuse the preserved original when available.
"""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage


ROOT = Path(__file__).resolve().parents[1]
UPLOAD = ROOT / "static/assets/roles/original.png"
NAMES = ("compass", "blue", "fox", "gold", "violet", "ink")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    assets = ROOT / "static/assets/roles"
    artifacts = ROOT / "artifacts/roles"
    original = assets / "original.png"
    source = args.source or (original if original.exists() else UPLOAD)
    with Image.open(source) as loaded:
        assert loaded.mode == "RGBA" and loaded.size == (1536, 1024), "Expected 1536x1024 RGBA"
        atlas = loaded.copy()
    assets.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    if source.resolve() != original.resolve():
        shutil.copyfile(source, original)
    manifest = {
        "source_path": str(UPLOAD if source == original else source.resolve()),
        "preserved_source": str(original.relative_to(ROOT)),
        "source_sha256": sha256(original),
        "source_size": list(atlas.size),
        "bbox_convention": "left, top, right exclusive, bottom exclusive",
        "processing": {
            "grid": [3, 2], "cell_size": 512, "canvas_size": [256, 256],
            "visual_height": 224, "resampling": "LANCZOS",
            "mask": "Largest 8-connected component at alpha>=16, expanded 3 pixels; preserve original RGBA within mask, zero outside. Removes isolated alpha noise without repainting.",
        },
        "roles": [],
    }
    sprites = []
    for index, name in enumerate(NAMES):
        x, y = index % 3 * 512, index // 3 * 512
        cell_box = (x, y, x + 512, y + 512)
        cell = atlas.crop(cell_box)
        raw_bbox = cell.getchannel("A").getbbox()
        pixels = np.array(cell)
        alpha = pixels[:, :, 3]
        labels, count = ndimage.label(alpha >= 16, structure=np.ones((3, 3)))
        assert count, f"No role detected: {name}"
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        body = labels == sizes.argmax()
        keep = ndimage.binary_dilation(body, structure=np.ones((3, 3)), iterations=3)
        removed = int(np.count_nonzero((alpha > 0) & ~keep))
        pixels[~keep] = 0
        cleaned = Image.fromarray(pixels)
        bbox = cleaned.getchannel("A").getbbox()
        assert bbox and min(bbox[:2]) > 0 and max(bbox[2:]) < 512, f"Role touches grid edge: {name}"
        cropped = cleaned.crop(bbox)
        width = round(cropped.width * 224 / cropped.height)
        assert width <= 224, f"Role too wide for unified height: {name}"
        resized = cropped.resize((width, 224), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (256, 256))
        offset = ((256 - width) // 2, 16)
        canvas.paste(resized, offset)
        output = assets / f"{name}.png"
        canvas.save(output)
        with Image.open(output) as check:
            assert check.mode == "RGBA" and check.size == (256, 256)
            assert check.getpixel((0, 0))[3] == 0
            assert check.getchannel("A").getbbox() is not None
        sprites.append(canvas)
        manifest["roles"].append({
            "name": name, "grid_index_row_major": index, "cell_bbox_source": cell_box,
            "raw_alpha_bbox_in_cell": raw_bbox, "clean_alpha_bbox_in_cell": bbox,
            "clean_alpha_bbox_source": [bbox[0] + x, bbox[1] + y, bbox[2] + x, bbox[3] + y],
            "removed_noise_pixels": removed, "resize": [width, 224], "offset": offset,
            "output_alpha_bbox": canvas.getchannel("A").getbbox(),
            "path": str(output.relative_to(ROOT)), "sha256": sha256(output),
        })

    # Each preview is pasted at exactly the labeled pixel dimensions, never enlarged.
    sheet = Image.new("RGB", (820, 510), "#18212b")
    draw = ImageDraw.Draw(sheet)
    draw.text((16, 12), "Original role crops | actual 48 / 64 / 96 px canvases", fill="white")
    for col, name in enumerate(NAMES):
        draw.text((80 + col * 122, 40), name, fill="white")
    for row, size in enumerate((48, 64, 96)):
        top = 70 + row * 142
        draw.text((10, top + 42), f"{size}px", fill="white")
        for col, sprite in enumerate(sprites):
            left = 72 + col * 122
            for cy in range(0, 112, 8):
                for cx in range(0, 112, 8):
                    color = "#e8e9eb" if (cx // 8 + cy // 8) % 2 == 0 else "#cdd0d4"
                    draw.rectangle((left + cx, top + cy, left + cx + 7, top + cy + 7), fill=color)
            preview = sprite.resize((size, size), Image.Resampling.LANCZOS)
            sheet.paste(preview, (left + (112 - size) // 2, top + (112 - size) // 2), preview)
    contact = artifacts / "contact-sheet.png"
    sheet.save(contact)
    manifest["contact_sheet"] = {"path": str(contact.relative_to(ROOT)), "sha256": sha256(contact), "actual_sizes": [48, 64, 96]}
    manifest["verification"] = {
        "file_geometry_transparency_grid_clearance": "PASS",
        "independent_visual_review": "NOT_RUN: contact sheet supplied for main agent review",
        "web_path_access": "NOT_RUN: asset-only task",
    }
    (assets / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
