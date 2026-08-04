"""Renders the Haushalts-Board for the e-ink display.

Rolling 3-day view (Heute / Morgen / Übermorgen) instead of a full Mo-So
week — the board is a small 800x480 panel, three wide columns read better
than seven cramped ones.

Per day, household tasks and Google Calendar events are merged into a single
list and ordered the way a calendar app presents a day: untimed items first
(in the manual order set by dragging on the phone page), then everything with
a time, chronologically — tasks and calendar entries interleaved. A calendar
event gets the same owner badge a task has if its Google Calendar color maps
to a person (google_calendar._color_person_map) — this household color-codes
events per person, so the badge reuses that instead of needing its own
per-event ownership field.

Visual style follows e-ink UI conventions (confirmed against TRMNL's own
framework docs, since this is a TRMNL fork): sharp corners over rounded
ones, solid black rules instead of thin gray hairlines, and weight/size for
hierarchy instead of gray fills — mid-gray fills a) read as flat/dated at
this resolution and b) nearly vanish once quantized down for the actual
1-bit e-ink panel. "Done" is conveyed via a filled badge plus strikethrough,
never via a gray tone.

Rows alternate between plain (white background) and inverted (solid black
bar, white ink) purely by position within the day — not by any per-task
flag. This isn't decorative banding for its own sake: it's what makes each
row's boundaries unambiguous on a 1-bit panel, where there's no light gray
gridline to lean on.

Text is word-wrapped rather than truncated with an ellipsis: every row is
measured first (wrapped line count -> pixel height) and only drawn if it
still fits before GRID_BOTTOM/FOOTER_BOTTOM, so a long task simply takes
more vertical space instead of losing content.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, timedelta
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

from .. import google_calendar, haushalt_store
from ..utils import asset_path
from .base import PluginBase, PluginOutput

logger = logging.getLogger(__name__)

CANVAS_SIZE = (800, 480)
MARGIN = 24

GRID_TOP = 18
HEADER_H = 58
RULE_Y = GRID_TOP + HEADER_H
BODY_TOP = RULE_Y + 12
GRID_BOTTOM = 344
FOOTER_RULE_Y = GRID_BOTTOM + 12
FOOTER_TOP = FOOTER_RULE_Y + 14
FOOTER_BOTTOM = 468

RULE_THICK = 3
LINE_GAP = 5   # extra vertical space between wrapped lines within one row
ROW_PAD = 4    # top/bottom padding inside every row's box, inverted or not
ROW_PAD_X = 8  # left/right padding for a row's content — without this, an
               # inverted (black-bar) row's icon sits flush against its own
               # edge instead of just against the column edge like a plain
               # row's does, which reads as cramped

INK = 0

BADGE_SIZE = 18
BADGE_GAP = 6
BADGE_LETTER = {"jonathan": "J", "katarina": "K", "kids": "k", "alle": "A"}

WINDOW_DAYS = 3
DAY_LABELS = ("Heute", "Morgen", "Übermorgen")
WEEKDAY_SHORT = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

_FONT_FILES = {
    "bold": "SpaceGrotesk-Bold.ttf",
    "medium": "SpaceGrotesk-Medium.ttf",
    "regular": "SpaceGrotesk-Regular.ttf",
    "light": "SpaceGrotesk-Light.ttf",
}


class HaushaltPlugin(PluginBase):
    """Rolling 3-day household board: Heute/Morgen/Übermorgen."""

    BASENAME = "haushalt"
    OUTPUT_SUBDIR = "haushalt"
    DISPLAY_NAME = "Haushalts-Board"
    REGISTRY_ORDER = 10
    SET_PRIMARY = True
    # Safety-net only: normal updates happen instantly via routes/haushalt.py
    # calling process_plugin_output() after every change on the phone.
    REFRESH_INTERVAL = 6 * 3600

    def get_content_ttl(self) -> int:
        return 6 * 3600

    async def run(self, **kwargs) -> Optional[PluginOutput]:
        output_dir = kwargs.get("output_dir", "web")
        os.makedirs(output_dir, exist_ok=True)

        today = date.today()
        dates = [today + timedelta(days=i) for i in range(WINDOW_DAYS)]

        tasks_by_date = await haushalt_store.get_tasks_for_dates(dates)
        undated = await haushalt_store.get_undated_tasks()

        calendar_events: dict[int, list[dict]] = {}
        if google_calendar.is_configured():
            calendar_events = await google_calendar.get_events(today, WINDOW_DAYS)

        rows_by_date = {
            d: self._merge_day(tasks_by_date.get(d, []), calendar_events.get(i, []))
            for i, d in enumerate(dates)
        }

        image = await asyncio.to_thread(self._render, dates, rows_by_date, undated)
        output = await asyncio.to_thread(self.save_assets, image, output_dir, self.BASENAME)
        logger.info(
            "Haushalt board rendered to %s and %s",
            output.monochrome_path,
            output.grayscale_path,
        )
        return output

    @staticmethod
    def _merge_day(tasks: List[dict], events: List[dict]) -> List[dict]:
        """One chronological list per day: untimed items first (manual order),
        then timed tasks and calendar events interleaved by time."""
        rows: List[dict] = []
        for task in tasks:
            rows.append({
                "kind": "task",
                "text": task["text"],
                "time": task.get("time"),
                "person": task.get("person", "alle"),
                "done": task.get("done", False),
                "order": task.get("order", 0),
            })
        for event in events:
            rows.append({
                "kind": "event",
                "text": event["title"],
                "time": event.get("time"),
                "person": event.get("person"),
                "order": 0,
            })
        rows.sort(key=lambda r: (1 if r["time"] else 0, r["time"] or "", r["order"]))
        return rows

    # -- fonts ---------------------------------------------------------------

    def _font(self, size: int, weight: str = "medium") -> ImageFont.ImageFont:
        filename = _FONT_FILES.get(weight, _FONT_FILES["medium"])
        path = asset_path(f"fonts/ttf/static/{filename}").as_posix()
        return self.load_font(size, (path,))

    # -- main render -----------------------------------------------------------

    def _render(self, dates, rows_by_date, undated) -> Image.Image:
        image = Image.new("L", CANVAS_SIZE, color=255)
        draw = ImageDraw.Draw(image)

        header_label_font = self._font(22, "bold")
        header_date_font = self._font(13, "regular")
        item_font = self._font(15, "regular")
        badge_font = self._font(11, "bold")
        footer_header_font = self._font(18, "bold")
        footer_item_font = self._font(15, "regular")

        col_width = (CANVAS_SIZE[0] - 2 * MARGIN) / WINDOW_DAYS

        for i, d in enumerate(dates):
            x0 = MARGIN + i * col_width
            self._draw_day_column(
                draw, x0, col_width, i, d, rows_by_date.get(d, []),
                header_label_font, header_date_font, item_font, badge_font,
            )

        draw.line([(MARGIN, RULE_Y), (CANVAS_SIZE[0] - MARGIN, RULE_Y)], fill=INK, width=RULE_THICK)
        for i in range(1, WINDOW_DAYS):
            x = MARGIN + i * col_width
            draw.line([(x, GRID_TOP), (x, GRID_BOTTOM)], fill=INK, width=RULE_THICK)

        self._draw_footer(draw, undated, footer_header_font, footer_item_font, badge_font)

        return image

    # -- day columns ---------------------------------------------------------

    def _draw_day_column(
        self, draw, x0, col_width, i, d, rows,
        header_label_font, header_date_font, item_font, badge_font,
    ) -> None:
        is_today = i == 0
        label = DAY_LABELS[i]
        date_str = f"{WEEKDAY_SHORT[d.weekday()]} · {d.day:02d}.{d.month:02d}."
        pad = 10

        if is_today:
            draw.rectangle([x0, GRID_TOP, x0 + col_width, GRID_TOP + HEADER_H], fill=INK)
            text_fill = 255
        else:
            text_fill = INK
        draw.text((x0 + pad, GRID_TOP + 9), label, font=header_label_font, fill=text_fill)
        draw.text(
            (x0 + pad, GRID_TOP + 9 + header_label_font.size + 3),
            date_str, font=header_date_font, fill=text_fill,
        )

        inner_x = x0 + 6
        inner_w = col_width - 12
        y = BODY_TOP
        bottom_limit = GRID_BOTTOM - 10
        shown = 0

        for idx, row in enumerate(rows):
            inverted = idx % 2 == 1
            if row["kind"] == "task":
                height = self._draw_task_row(draw, inner_x, y, inner_w, row, item_font, badge_font, bottom_limit, inverted)
            else:
                height = self._draw_event_row(draw, inner_x, y, inner_w, row, item_font, badge_font, bottom_limit, inverted)
            if height is None:
                break
            y += height + 8
            shown += 1

        remaining = len(rows) - shown
        if remaining > 0 and y + item_font.size <= bottom_limit:
            draw.text((inner_x + ROW_PAD_X, y), f"+{remaining} mehr", fill=INK, font=item_font)

    def _draw_task_row(self, draw, x, y, max_width, row, item_font, badge_font, bottom_limit, inverted) -> Optional[int]:
        content_x = x + ROW_PAD_X
        text_x = content_x + BADGE_SIZE + BADGE_GAP
        avail = max_width - 2 * ROW_PAD_X - BADGE_SIZE - BADGE_GAP
        lines = self._wrap(self._with_time(row), item_font, avail)
        content_h = max(BADGE_SIZE, self._text_block_height(lines, item_font))
        box_h = content_h + 2 * ROW_PAD
        if y + box_h > bottom_limit:
            return None
        if inverted:
            draw.rectangle([x, y, x + max_width, y + box_h], fill=INK)
        content_y = y + ROW_PAD
        ink = 255 if inverted else INK
        self._draw_badge(draw, content_x, content_y, row["person"], row["done"], badge_font, inverted)
        self._draw_lines(draw, text_x, content_y, lines, item_font, fill=ink, strike=row["done"])
        return box_h

    def _draw_event_row(self, draw, x, y, max_width, row, item_font, badge_font, bottom_limit, inverted) -> Optional[int]:
        r = 6
        content_x = x + ROW_PAD_X
        person = row.get("person")
        # Calendar events carry a person only when their Google Calendar
        # color maps to one (see google_calendar._color_person_map) — shown
        # as the same owner badge tasks use, right after the diamond marker
        # that marks this row as a calendar entry rather than a task.
        if person:
            badge_x = content_x + 2 * r + 8
            text_x = badge_x + BADGE_SIZE + BADGE_GAP
        else:
            text_x = content_x + 2 * r + 8
        avail = max_width - 2 * ROW_PAD_X - (text_x - content_x)
        lines = self._wrap(self._with_time(row), item_font, avail)
        content_h = max(BADGE_SIZE, self._text_block_height(lines, item_font))
        box_h = content_h + 2 * ROW_PAD
        if y + box_h > bottom_limit:
            return None
        if inverted:
            draw.rectangle([x, y, x + max_width, y + box_h], fill=INK)
        content_y = y + ROW_PAD
        ink = 255 if inverted else INK
        cx, cy = content_x + r, content_y + BADGE_SIZE / 2
        draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], outline=ink, width=2)
        if person:
            self._draw_badge(draw, badge_x, content_y, person, False, badge_font, inverted)
        self._draw_lines(draw, text_x, content_y, lines, item_font, fill=ink)
        return box_h

    @staticmethod
    def _with_time(row: dict) -> str:
        return f"{row['time']} {row['text']}" if row.get("time") else row["text"]

    @staticmethod
    def _draw_badge(draw, x, y, person, done, font, inverted: bool = False) -> None:
        letter = BADGE_LETTER.get(person, "?")
        box = [x, y, x + BADGE_SIZE, y + BADGE_SIZE]
        ink = 255 if inverted else INK
        paper = INK if inverted else 255
        if done:
            draw.rectangle(box, fill=ink)
            text_fill = paper
        else:
            draw.rectangle(box, outline=ink, width=2)
            text_fill = ink
        draw.text((x + BADGE_SIZE / 2, y + BADGE_SIZE / 2), letter, font=font, fill=text_fill, anchor="mm")

    # -- undated-tasks footer ------------------------------------------

    def _draw_footer(self, draw, undated, header_font, item_font, badge_font) -> None:
        draw.line(
            [(MARGIN, FOOTER_RULE_Y), (CANVAS_SIZE[0] - MARGIN, FOOTER_RULE_Y)],
            fill=INK, width=RULE_THICK,
        )

        x = MARGIN
        y = FOOTER_TOP
        draw.text((x, y), "Diese Woche", font=header_font, fill=INK)
        y += header_font.size + 10

        if not undated:
            draw.text((x, y), "–", font=item_font, fill=INK)
            return

        right_limit = CANVAS_SIZE[0] - MARGIN
        full_width = right_limit - x
        item_gap = 20

        cx, cy = x, y
        line_height_used = 0
        shown = 0
        for task in undated:
            done = bool(task.get("done"))
            lines = self._wrap(task["text"], item_font, full_width - BADGE_SIZE - BADGE_GAP)
            item_h = max(BADGE_SIZE, self._text_block_height(lines, item_font))
            text_w = max((item_font.getlength(line) for line in lines), default=0)
            item_w = BADGE_SIZE + BADGE_GAP + text_w
            multiline = len(lines) > 1

            if cx != x and (multiline or cx + item_w > right_limit):
                cx = x
                cy += line_height_used + 10
                line_height_used = 0

            if cy + item_h > FOOTER_BOTTOM:
                break

            self._draw_badge(draw, cx, cy, task.get("person", "alle"), done, badge_font)
            self._draw_lines(draw, cx + BADGE_SIZE + BADGE_GAP, cy, lines, item_font, strike=done)
            line_height_used = max(line_height_used, item_h)
            shown += 1

            if multiline:
                cx = x
                cy += item_h + 10
                line_height_used = 0
            else:
                cx += item_w + item_gap

        remaining = len(undated) - shown
        if remaining > 0:
            draw.text((cx, cy), f"+{remaining} mehr", font=item_font, fill=INK)

    # -- text wrapping helpers -------------------------------------------------

    @staticmethod
    def _wrap(text: str, font, max_width: float) -> List[str]:
        words = text.split()
        if not words:
            return [""]
        lines: List[str] = []
        current: List[str] = []
        for word in words:
            candidate = " ".join(current + [word])
            if font.getlength(candidate) <= max_width or not current:
                current.append(word)
            else:
                lines.append(" ".join(current))
                current = [word]
        if current:
            lines.append(" ".join(current))
        return lines

    @staticmethod
    def _text_block_height(lines: List[str], font) -> int:
        return (font.size + LINE_GAP) * len(lines)

    @staticmethod
    def _draw_lines(draw, x, y, lines: List[str], font, fill: int = INK, strike: bool = False) -> None:
        line_h = font.size + LINE_GAP
        ty = y
        for line in lines:
            draw.text((x, ty), line, font=font, fill=fill)
            if strike:
                width = font.getlength(line)
                sy = ty + font.size * 0.55
                draw.line([(x, sy), (x + width, sy)], fill=fill, width=2)
            ty += line_h
