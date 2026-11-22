"""Picture files: used as they are when plain and small, otherwise a working copy is written."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from conftest import drawn_picture, make_settings, noise_picture, save_picture
from fraudcheck.ingest.models import Code
from fraudcheck.ingest.pictures import identify_picture, process_picture


def run(settings, path: Path):
    file_format = identify_picture(path)
    assert file_format is not None
    out_dir = settings.cache_path / "DOC-TEST"
    return process_picture(path, file_format, path.stat().st_size, out_dir, settings, settings.display)


def only_page(result):
    assert result.error is None, result.error
    assert len(result.pages) == 1
    return result.pages[0]


def open_page(settings, page) -> Image.Image:
    image = Image.open(settings.resolve(page.picture))
    image.load()
    return image


def test_small_plain_picture_is_used_as_received(settings) -> None:
    path = save_picture(drawn_picture((64, 48)), settings.documents_path / "DOC-1.png")
    page = only_page(run(settings, path))
    assert (page.source, page.picture, page.width, page.height) == ("original", "data/documents/DOC-1.png", 64, 48)
    assert page.reasons == [] and page.scale is None
    assert not settings.cache_path.exists()  # nothing written


def test_heavy_file_gets_a_lighter_working_copy_and_the_original_is_untouched(workspace) -> None:
    settings = make_settings(workspace, max_bytes=10_000)
    path = save_picture(noise_picture((120, 120)), settings.documents_path / "DOC-2.png")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    page = only_page(run(settings, path))
    assert page.source == "working_copy" and page.reasons == ["bytes"]
    copy = settings.resolve(page.picture)
    assert copy.parent == settings.cache_path / "DOC-TEST" and copy.suffix == ".jpg"
    assert copy.stat().st_size < path.stat().st_size
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_too_many_pixels_reduces_the_working_copy_to_the_limit(workspace) -> None:
    settings = make_settings(workspace, max_side_px=256)
    path = save_picture(drawn_picture((600, 300)), settings.documents_path / "DOC-3.png")
    page = only_page(run(settings, path))
    assert page.reasons == ["pixels"]
    assert (page.width, page.height) == (256, 128)
    assert page.scale == round(256 / 600, 4)
    assert open_page(settings, page).size == (256, 128)


def test_real_transparency_is_laid_on_white(settings) -> None:
    image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    image.paste((0, 0, 0, 255), (10, 10, 30, 30))
    path = save_picture(image, settings.documents_path / "DOC-4.png")
    page = only_page(run(settings, path))
    assert page.reasons == ["transparency"]
    copy = open_page(settings, page).convert("RGB")
    assert copy.getpixel((0, 0)) == (255, 255, 255)
    assert max(copy.getpixel((20, 20))) < 40


def test_opaque_alpha_channel_is_not_transparency(settings) -> None:
    path = save_picture(drawn_picture((30, 30), "RGBA"), settings.documents_path / "DOC-5.png")
    assert only_page(run(settings, path)).source == "original"


def test_palette_with_transparent_colour_gets_a_copy(settings) -> None:
    image = drawn_picture((30, 30)).convert("P")
    path = save_picture(image, settings.documents_path / "DOC-6.png", transparency=0)
    assert "transparency" in only_page(run(settings, path)).reasons


def test_unusual_colour_modes_are_converted(settings) -> None:
    cmyk = save_picture(Image.new("CMYK", (40, 40), (0, 0, 0, 255)), settings.documents_path / "DOC-7.jpg")
    page = only_page(run(settings, cmyk))
    assert page.reasons == ["colour_mode"] and open_page(settings, page).mode == "RGB"

    deep = Image.new("I;16", (64, 8), 32768)  # mid-grey on a 0-65535 scale
    deep.paste(0, (0, 0, 16, 8))
    deep.paste(65535, (48, 0, 64, 8))
    sixteen = save_picture(deep, settings.documents_path / "DOC-8.png")
    page = only_page(run(settings, sixteen))
    assert page.reasons == ["colour_mode"]
    copy = open_page(settings, page).convert("L")
    # Black, mid-grey and white survive the move to 8 bits (a plain conversion would clip grey to white).
    assert copy.getpixel((4, 4)) < 10
    assert 118 <= copy.getpixel((32, 4)) <= 138
    assert copy.getpixel((60, 4)) > 245


def test_one_bit_scan_is_plain(settings) -> None:
    path = save_picture(drawn_picture((50, 50), "1"), settings.documents_path / "DOC-9.png")
    assert only_page(run(settings, path)).source == "original"


def test_tiff_frames_become_pages(settings) -> None:
    frames = [drawn_picture((50, 40)), Image.new("RGB", (50, 40), "green"), Image.new("RGB", (60, 30), "blue")]
    path = settings.documents_path / "DOC-10.tif"
    frames[0].save(path, save_all=True, append_images=frames[1:])
    result = run(settings, path)
    assert result.error is None
    assert [page.number for page in result.pages] == [1, 2, 3]
    assert all(page.source == "working_copy" and {"format", "frames"} <= set(page.reasons) for page in result.pages)
    assert (result.pages[2].width, result.pages[2].height) == (60, 30)


def test_tiff_pages_beyond_the_limit_are_dropped_with_a_warning(workspace) -> None:
    settings = make_settings(workspace, max_pages=2)
    path = settings.documents_path / "DOC-11.tiff"
    frames = [Image.new("L", (20, 20), shade) for shade in (0, 100, 200)]
    frames[0].save(path, save_all=True, append_images=frames[1:])
    result = run(settings, path)
    assert len(result.pages) == 2
    assert [notice.code for notice in result.warnings] == [Code.PAGES_TRUNCATED]


def test_32_bit_tiff_is_stretched_to_8_bits(settings) -> None:
    image = Image.new("I", (32, 32))
    image.putdata([value * 70 for value in range(32 * 32)])
    path = save_picture(image, settings.documents_path / "DOC-12.tif")
    page = only_page(run(settings, path))
    assert open_page(settings, page).convert("L").getextrema() == (0, 255)


def test_animation_keeps_its_first_frame_only(settings) -> None:
    frames = [drawn_picture((40, 40)).convert("P"), Image.new("RGB", (40, 40), "red").convert("P")]
    path = settings.documents_path / "DOC-13.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    result = run(settings, path)
    page = only_page(result)
    assert "frames" in page.reasons
    assert Code.EXTRA_FRAMES_IGNORED in [notice.code for notice in result.warnings]


def test_webp_and_jpeg_are_accepted_as_received(settings) -> None:
    webp = save_picture(drawn_picture(), settings.documents_path / "DOC-14.webp")
    jpeg = save_picture(drawn_picture(), settings.documents_path / "DOC-15.JPG")
    assert only_page(run(settings, webp)).source == "original"
    assert only_page(run(settings, jpeg)).source == "original"


def test_orientation_tag_is_dropped_and_pixels_are_not_turned(settings) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 degrees to display"
    path = save_picture(drawn_picture((80, 40)), settings.documents_path / "DOC-16.jpg", exif=exif)
    result = run(settings, path)
    page = only_page(result)
    assert page.reasons == ["orientation_tag"]
    assert (page.width, page.height) == (80, 40)
    copy = open_page(settings, page)
    assert copy.size == (80, 40) and copy.getexif().get(0x0112) is None
    assert [notice.code for notice in result.warnings] == [Code.ORIENTATION_TAG]


def test_picture_above_the_open_limit_is_refused(workspace) -> None:
    settings = make_settings(workspace, max_open_megapixels=0.001)
    path = save_picture(drawn_picture((100, 100)), settings.documents_path / "DOC-17.png")
    result = run(settings, path)
    assert result.pages == [] and result.error.code == Code.TOO_LARGE


def test_truncated_picture_is_reported_as_corrupt(settings) -> None:
    path = save_picture(noise_picture((100, 100)), settings.documents_path / "DOC-18.png")
    path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])
    result = run(settings, path)
    assert result.pages == [] and result.error.code == Code.CORRUPT_FILE


def test_identify_picture_reads_content_not_extension(settings) -> None:
    disguised = save_picture(drawn_picture(), settings.documents_path / "DOC-19.pdf", format="PNG")
    assert identify_picture(disguised) == "png"
    text = settings.documents_path / "notes.txt"
    text.write_text("plain text", encoding="utf-8")
    assert identify_picture(text) is None
