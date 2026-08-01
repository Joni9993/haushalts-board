"""Renders the Haushalts-Board (household weekly board) for the e-ink display.

Rolling 3-day view (Heute / Morgen / Übermorgen) instead of a full Mo-So
week — the board is a small 800x480 panel, three wide columns read better
than seven cramped ones. The window can cross an ISO-week boundary (e.g.
today=Saturday -> day 3 lands on next week's Monday), so task lookup goes
through haushalt_store.get_week_for_date() per date rather than assuming a
single week bucket.

Visual style follows e-ink UI conventions (confirmed against TRMNL's own
framework docs, since this is a TRMNL fork): sharp corners over rounded
ones, solid black rules instead of thin gray hairlines, and weight/size for
hierarchy instead of gray fills — mid-gray fills a) read as flat/dated at
this resolution and b) nearly vanish once quantized down for the actual
1-bit e-ink panel. "Done" and "not today" are conveyed via fill/strikethrough
and bar-vs-plain-text, never via a gray tone.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, timedelta
from typing import Optional

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

INK = 0

BADGE_SIZE = 18
BADGE_GAP = 6
BADGE_LETTER = {"jonathan": "J", "katarina": "K", "kids": "k"}

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
    """Rolling 3-day household board: Heute/Morgen/Übermorgen with blocks, tasks, and calendar events."""

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

        blocks = await haushalt_store.get_blocks()
        tasks_by_date, unassigned = await self._load_rolling_tasks(dates)

        calendar_events: dict[int, list[str]] = {}
        if google_calendar.is_configured():
            calendar_events = await google_calendar.get_events(today, WINDOW_DAYS)

        image = await asyncio.to_thread(
            self._render, dates, blocks, tasks_by_date, unassigned, calendar_events
        )
        output = await asyncio.to_thread(self.save_assets, image, output_dir, self.BASENAME)
        logger.info(
            "Haushalt board rendered to %s and %s",
            output.monochrome_path,
            output.grayscale_path,
        )
        return output

    @staticmethod
    async def _load_rolling_tasks(dates: list[date]):
        """Resolve per-day tasks for the rolling window, fetching each date's
        ISO-week bucket (cached per week key, since most windows stay within
        a single week and only split at the Sat/Sun -> Mon boundary)."""
        week_cache: dict[str, dict] = {}

        async def week_for(d: date) -> dict:
            iso_year, iso_week, _ = d.isocalendar()
            key = f"{iso_year}-w{iso_week}"
            if key not in week_cache:
                week_cache[key] = await haushalt_store.get_week_for_date(d)
            return week_cache[key]

        tasks_by_date: dict[date, list[tuple[str, dict]]] = {}
        for d in dates:
            payload = await week_for(d)
            day_tasks = [
                (column, task)
                for column in ("jonathan", "katarina", "kids")
                for task in payload.get(column, [])
                if task.get("day") == d.weekday()
            ]
            tasks_by_date[d] = day_tasks

        current_week = await week_for(date.today())
        unassigned = [
            (column, task)
            for column in ("jonathan", "katarina", "kids")
            for task in current_week.get(column, [])
            if task.get("day") is None
        ]
        return tasks_by_date, unassigned

    # -- fonts ---------------------------------------------------------------

    def _font(self, size: int, weight: str = "medium") -> ImageFont.ImageFont:
        filename = _FONT_FILES.get(weight, _FONT_FILES["medium"])
        path = asset_path(f"fonts/ttf/static/{filename}").as_posix()
        return self.load_font(size, (path,))

    # -- main render -----------------------------------------------------------

    def _render(self, dates, blocks, tasks_by_date, unassigned, calendar_events) -> Image.Image:
        image = Image.new("L", CANVAS_SIZE, color=255)
        draw = ImageDraw.Draw(image)

        header_label_font = self._font(22, "bold")
        header_date_font = self._font(13, "regular")
        block_font = self._font(15, "bold")
        item_font = self._font(15, "regular")
        badge_font = self._font(11, "bold")
        footer_header_font = self._font(18, "bold")
        footer_item_font = self._font(15, "regular")

        blocks_by_weekday: dict[int, list[str]] = {}
        for b in blocks:
            blocks_by_weekday.setdefault(b["day"], []).append(b["label"])

        col_width = (CANVAS_SIZE[0] - 2 * MARGIN) / WINDOW_DAYS

        for i, d in enumerate(dates):
            x0 = MARGIN + i * col_width
            self._draw_day_column(
                draw, x0, col_width, i, d,
                blocks_by_weekday.get(d.weekday(), []),
                tasks_by_date.get(d, []),
                calendar_events.get(i, []),
                header_label_font, header_date_font, block_font, item_font, badge_font,
            )

        draw.line([(MARGIN, RULE_Y), (CANVAS_SIZE[0] - MARGIN, RULE_Y)], fill=INK, width=RULE_THICK)
        for i in range(1, WINDOW_DAYS):
            x = MARGIN + i * col_width
            draw.line([(x, GRID_TOP), (x, GRID_BOTTOM)], fill=INK, width=RULE_THICK)

        self._draw_footer(draw, unassigned, footer_header_font, footer_item_font, badge_font)

        return image

    # -- day columns ---------------------------------------------------------

    def _draw_day_column(
        self, draw, x0, col_width, i, d,
        block_labels, tasks, events,
        header_label_font, header_date_font, block_font, item_font, badge_font,
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

        for block_label in block_labels:
            h = self._draw_block_bar(draw, inner_x, inner_w, y, block_label, block_font)
            y += h + 8

        row_h = BADGE_SIZE + 10
        bottom_limit = GRID_BOTTOM - 10
        max_rows = max(0, int((bottom_limit - y) / row_h))
        shown = 0

        for column, task in tasks:
            if shown >= max_rows:
                break
            self._draw_task_row(draw, inner_x, y, inner_w, column, task, item_font, badge_font)
            y += row_h
            shown += 1

        for event_text in events:
            if shown >= max_rows:
                break
            self._draw_event_row(draw, inner_x, y, inner_w, event_text, item_font)
            y += row_h
            shown += 1

        remaining = len(tasks) + len(events) - shown
        if remaining > 0 and y + item_font.size <= bottom_limit:
            draw.text((inner_x, y), f"+{remaining} mehr", fill=INK, font=item_font)

    @staticmethod
    def _draw_block_bar(draw, x0, width, y, label, block_font) -> int:
        h = block_font.size + 16
        draw.rectangle([x0, y, x0 + width, y + h], fill=INK)
        text = HaushaltPlugin._truncate(label, block_font, width - 20)
        draw.text((x0 + 10, y + h / 2), text, font=block_font, fill=255, anchor="lm")
        return h

    def _draw_task_row(self, draw, x, y, max_width, column, task, item_font, badge_font) -> None:
        done = bool(task.get("done"))
        self._draw_badge(draw, x, y, column, done, badge_font)
        text_x = x + BADGE_SIZE + BADGE_GAP
        text = self._truncate(task["text"], item_font, max_width - BADGE_SIZE - BADGE_GAP)
        row_mid = y + BADGE_SIZE / 2
        draw.text((text_x, row_mid), text, font=item_font, fill=INK, anchor="lm")
        if done:
            self._strike_through(draw, text_x, row_mid, text, item_font)

    @staticmethod
    def _draw_event_row(draw, x, y, max_width, text, item_font) -> None:
        r = 6
        cy = y + BADGE_SIZE / 2
        cx = x + r
        draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], outline=INK, width=2)
        text_x = x + 2 * r + 8
        truncated = HaushaltPlugin._truncate(text, item_font, max_width - 2 * r - 8)
        draw.text((text_x, cy), truncated, font=item_font, fill=INK, anchor="lm")

    @staticmethod
    def _strike_through(draw, x, row_mid, text, font) -> None:
        """Solid strikethrough for done tasks — unlike a gray fill, this survives
        1-bit e-ink rendering (no dithering) without vanishing."""
        if not text:
            return
        width = font.getlength(text)
        line_y = row_mid + font.size * 0.05
        draw.line([(x, line_y), (x + width, line_y)], fill=INK, width=2)

    @staticmethod
    def _draw_badge(draw, x, y, column, done, font) -> None:
        letter = BADGE_LETTER.get(column, "?")
        box = [x, y, x + BADGE_SIZE, y + BADGE_SIZE]
        if done:
            draw.rectangle(box, fill=INK)
            text_fill = 255
        else:
            draw.rectangle(box, outline=INK, width=2)
            text_fill = INK
        draw.text((x + BADGE_SIZE / 2, y + BADGE_SIZE / 2), letter, font=font, fill=text_fill, anchor="mm")

    # -- unassigned-tasks footer ------------------------------------------

    def _draw_footer(self, draw, unassigned, header_font, item_font, badge_font) -> None:
        draw.line(
            [(MARGIN, FOOTER_RULE_Y), (CANVAS_SIZE[0] - MARGIN, FOOTER_RULE_Y)],
            fill=INK, width=RULE_THICK,
        )

        x = MARGIN
        y = FOOTER_TOP
        draw.text((x, y), "Diese Woche", font=header_font, fill=INK)
        y += header_font.size + 10

        if not unassigned:
            draw.text((x, y), "–", font=item_font, fill=INK)
            return

        right_limit = CANVAS_SIZE[0] - MARGIN
        row_h = max(BADGE_SIZE, item_font.size) + 10
        max_y = FOOTER_BOTTOM - row_h

        cx, cy = x, y
        shown = 0
        for column, task in unassigned:
            text = task["text"]
            done = bool(task.get("done"))
            bbox = draw.textbbox((0, 0), text, font=item_font)
            item_w = BADGE_SIZE + BADGE_GAP + (bbox[2] - bbox[0])

            if cx != x and cx + item_w > right_limit:
                cx = x
                cy += row_h
            if cy > max_y:
                break

            self._draw_badge(draw, cx, cy, column, done, badge_font)
            text_x = cx + BADGE_SIZE + BADGE_GAP
            row_mid = cy + BADGE_SIZE / 2
            draw.text((text_x, row_mid), text, font=item_font, fill=INK, anchor="lm")
            if done:
                self._strike_through(draw, text_x, row_mid, text, item_font)
            cx += item_w + 20
            shown += 1

        remaining = len(unassigned) - shown
        if remaining > 0:
            draw.text((cx, cy + BADGE_SIZE / 2), f"+{remaining} mehr", font=item_font, fill=INK, anchor="lm")

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _truncate(text: str, font, max_width: float) -> str:
        if font.getlength(text) <= max_width:
            return text
        while text and font.getlength(text + "…") > max_width:
            text = text[:-1]
        return text + "…" if text else ""
