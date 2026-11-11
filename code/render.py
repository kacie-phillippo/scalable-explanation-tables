"""Render a readable explanation-table CSV as a PNG using Pillow."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont


def detect_delimiter(sample: str) -> str:
    """Detect a comma or semicolon delimiter, with a simple fallback."""
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;").delimiter
    except csv.Error:
        first_line = sample.splitlines()[0] if sample else ""
        return ";" if first_line.count(";") > first_line.count(",") else ","


def read_table(path: Path, encoding: str) -> list[list[str]]:
    """Read a delimited text file and return a rectangular table."""
    with path.open("r", encoding=encoding, newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        delimiter = detect_delimiter(sample)
        rows = [[cell.strip() for cell in row] for row in csv.reader(handle, delimiter=delimiter)]

    if not rows:
        raise ValueError("The input file is empty.")

    column_count = max(len(row) for row in rows)
    if column_count == 0:
        raise ValueError("The input file does not contain any columns.")

    return [row + [""] * (column_count - len(row)) for row in rows]


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Load a commonly available TrueType font, falling back if necessary."""
    candidates = (
        ["DejaVuSans-Bold.ttf", "arialbd.ttf"]
        if bold
        else ["DejaVuSans.ttf", "arial.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    """Measure text, including empty cells and multi-line values."""
    measured = text if text else " "
    box = draw.multiline_textbbox((0, 0), measured, font=font, spacing=4)
    return box[2] - box[0], box[3] - box[1]


def render_table(
    rows: Sequence[Sequence[str]],
    output_path: Path,
    font_size: int,
    padding: int,
    header: bool,
    scale: float,
) -> None:
    """Render the table at its natural size so no text is clipped."""
    body_font = load_font(font_size)
    header_font = load_font(font_size, bold=True)
    measuring_image = Image.new("RGB", (1, 1), "white")
    measuring_draw = ImageDraw.Draw(measuring_image)

    column_widths: list[int] = []
    for column in range(len(rows[0])):
        widest = 0
        for row_index, row in enumerate(rows):
            font = header_font if header and row_index == 0 else body_font
            widest = max(widest, text_size(measuring_draw, row[column], font)[0])
        column_widths.append(widest + padding * 2)

    row_heights: list[int] = []
    for row_index, row in enumerate(rows):
        font = header_font if header and row_index == 0 else body_font
        tallest = max(text_size(measuring_draw, cell, font)[1] for cell in row)
        row_heights.append(tallest + padding * 2)

    table_width = sum(column_widths) + 1
    table_height = sum(row_heights) + 1
    image = Image.new("RGB", (table_width, table_height), "white")
    draw = ImageDraw.Draw(image)

    y = 0
    for row_index, (row, row_height) in enumerate(zip(rows, row_heights)):
        x = 0
        fill = "#e8eef7" if header and row_index == 0 else ("#f7f7f7" if row_index % 2 == 0 else "white")
        font = header_font if header and row_index == 0 else body_font
        for cell, column_width in zip(row, column_widths):
            draw.rectangle(
                (x, y, x + column_width, y + row_height),
                fill=fill,
                outline="#9aa0a6",
                width=1,
            )
            draw.multiline_text(
                (x + padding, y + padding),
                cell,
                fill="#111111",
                font=font,
                spacing=4,
            )
            x += column_width
        y += row_height

    if scale != 1.0:
        new_size = (max(1, round(table_width * scale)), max(1, round(table_height * scale)))
        image = image.resize(new_size, Image.Resampling.LANCZOS)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG")

