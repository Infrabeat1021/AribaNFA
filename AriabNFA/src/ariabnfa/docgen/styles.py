"""Document-wide styles for the NFA, matching the reference letterhead."""

from __future__ import annotations

from docx.enum.style import WD_STYLE_TYPE
from docx.shared import Cm, Pt, RGBColor

BODY_FONT = "Calibri"
BODY_SIZE = Pt(11)
TABLE_SIZE = Pt(10)

#: Shown when a field could not be resolved. Deliberately loud: a blank cell in
#: an approval document can be signed off without anyone noticing it is empty.
MISSING_STYLE = "NFA Missing"
HEADER_FILL = "D9D9D9"

# A4 with the reference document's margins.
PAGE_WIDTH_CM = 21.0
PAGE_HEIGHT_CM = 29.7
MARGIN_TOP_CM = 1.0
MARGIN_BOTTOM_CM = 1.5
MARGIN_SIDE_CM = 2.54
#: Usable width between the left and right margins.
CONTENT_WIDTH_CM = PAGE_WIDTH_CM - (2 * MARGIN_SIDE_CM)


def apply_styles(document) -> None:
    """Set base fonts and register the custom styles the builder relies on."""
    normal = document.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = BODY_SIZE
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.space_before = Pt(0)

    if MISSING_STYLE not in [s.name for s in document.styles]:
        missing = document.styles.add_style(MISSING_STYLE, WD_STYLE_TYPE.CHARACTER)
        missing.font.name = BODY_FONT
        missing.font.italic = True
        missing.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)


def setup_page(section) -> None:
    """A4 portrait with the reference document's margins."""
    section.page_width = Cm(PAGE_WIDTH_CM)
    section.page_height = Cm(PAGE_HEIGHT_CM)
    section.top_margin = Cm(MARGIN_TOP_CM)
    section.bottom_margin = Cm(MARGIN_BOTTOM_CM)
    section.left_margin = Cm(MARGIN_SIDE_CM)
    section.right_margin = Cm(MARGIN_SIDE_CM)
