"""Renders the Haushalts-Board (household weekly board) for the e-ink display.

Weekly grid: Mo..So columns, each showing the fixed block for that day (Sport,
Hobby-Tag, ...), tasks assigned to that day, and — if Google Calendar is
configured — that day's calendar events. Tasks without a specific day show in
a strip below the grid. Regenerated on demand whenever something changes via
the API (see routes/haushalt.py); REFRESH_INTERVAL here is just a fallback.

Rendering is strictly grayscale (this ends up as a 1-bit/4-level e-ink image),
so "who owns a task" and "is it done" are encoded with small vector badges
(square outline -> filled) rather than Unicode symbols like "✓"/"○" -
SpaceGrotesk (the loaded font) doesn't have glyphs for those, so they used to
fall back to the font's .notdef box, which is what gave the board its
accidental "Windows 95" look.
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
MARGIN = 18
COL_GAP = 5
PAD = 6

HEADER_RULE_Y = 52
GRID_TOP = 64
GRID_BOTTOM = 384
FOOTER_TOP = 392
FOOTER_BOTTOM = 476

INK = 0
MUTED = 140
FAINT = 205
LINE = 218
CARD_TINT = 245

BADGE_SIZE = 12
BADGE_GAP = 3
BADGE_LETTER = {"jonathan": "J", "katarina": "K", "kids": "k"}

_FONT_FILES = {
    "bold": "SpaceGrotesk-Bold.ttf",
    "medium": "SpaceGrotesk-Medium.ttf",
    "regular": "SpaceGrotesk-Regular.ttf",
    "light": "SpaceGrotesk-Light.ttf",
}


class HaushaltPlugin(PluginBase):
    """Weekly household board: Mo..So with blocks, tasks, and calendar events."""

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

        week = await haushalt_store.get_current_week()
        blocks = await haushalt_store.get_blocks()

        monday = _monday_of_current_week()
        calendar_events = {}
        if google_calendar.is_configured():
            calendar_events = await google_calendar.get_week_events(monday)

        image = await asyncio.to_thread(self._render, week, blocks, monday, calendar_events)
        output = await asyncio.to_thread(self.save_assets, image, output_dir, self.BASENAME)
        logger.info(
            "Haushalt board rendered to %s and %s",
            output.monochrome_path,
            output.grayscale_path,
        )
        return output

    # -- fonts ---------------------------------------------------------------

    def _font(self, size: int, weight: str = "medium") -> ImageFont.ImageFont:
        filename = _FONT_FILES.get(weight, _FONT_FILES["medium"])
        path = asset_path(f"fonts/ttf/static/{filename}").as_posix()
        return self.load_font(size, (path,))

    # -- main render -----------------------------------------------------------

    def _render(self, week: dict, blocks: list, monday: date, calendar_events: dict) -> Image.Image:
        image = Image.new("L", CANVAS_SIZE, color=255)
        draw = ImageDraw.Draw(image)

        title_font = self._font(24, "bold")
        subtitle_font = self._font(12, "regular")
        pill_font = self._font(12, "bold")
        day_font = self._font(12, "bold")
        block_font = self._font(11, "bold")
        item_font = self._font(11, "regular")
        badge_font = self._font(8, "bold")
        footer_header_font = self._font(14, "bold")
        footer_item_font = self._font(12, "regular")

        self._draw_header(draw, week, title_font, subtitle_font, pill_font)

        tasks_by_day, unassigned = self._group_tasks(week)
        blocks_by_day: dict[int, list[str]] = {}
        for b in blocks:
            blocks_by_day.setdefault(b["day"], []).append(b["label"])

        usable_width = CANVAS_SIZE[0] - 2 * MARGIN - 6 * COL_GAP
        col_width = usable_width / 7

        for day_idx, day_short in enumerate(haushalt_store.DAY_NAMES):
            x0 = MARGIN + day_idx * (col_width + COL_GAP)
            day_date = monday + timedelta(days=day_idx)
            is_today = day_date == date.today()
            label = f"{day_short} {day_date.day:02d}.{day_date.month:02d}"
            self._draw_day_card(
                draw, x0, col_width, label, is_today,
                blocks_by_day.get(day_idx, []),
                tasks_by_day.get(day_idx, []),
                calendar_events.get(day_idx, []),
                day_font, block_font, item_font, badge_font,
            )

        self._draw_footer(draw, unassigned, footer_header_font, footer_item_font, badge_font)

        return image

    # -- header ------------------------------------------------------------

    @staticmethod
    def _draw_header(draw, week, title_font, subtitle_font, pill_font) -> None:
        draw.text((MARGIN, 10), "Unsere Woche zuhause", fill=INK, font=title_font)
        draw.text((MARGIN, 10 + title_font.size + 4), "Jede*r trägt ein, was ansteht", fill=MUTED, font=subtitle_font)

        label = week.get("label", "")
        bbox = draw.textbbox((0, 0), label, font=pill_font)
        text_w = bbox[2] - bbox[0]
        pill_h = pill_font.size + 12
        pill_w = text_w + 24
        x1 = CANVAS_SIZE[0] - MARGIN
        x0 = x1 - pill_w
        y0 = 12
        y1 = y0 + pill_h
        draw.rounded_rectangle([x0, y0, x1, y1], radius=pill_h / 2, outline=INK, width=1)
        draw.text(((x0 + x1) / 2, (y0 + y1) / 2), label, fill=INK, font=pill_font, anchor="mm")

        draw.line([(MARGIN, HEADER_RULE_Y), (CANVAS_SIZE[0] - MARGIN, HEADER_RULE_Y)], fill=INK, width=1)

    # -- day columns ---------------------------------------------------------

    def _draw_day_card(
        self, draw, x0, col_width, label, is_today,
        block_labels, tasks, events,
        day_font, block_font, item_font, badge_font,
    ) -> None:
        x1 = x0 + col_width
        if is_today:
            draw.rounded_rectangle([x0, GRID_TOP, x1, GRID_BOTTOM], radius=8, fill=CARD_TINT, outline=INK, width=2)
        else:
            draw.rounded_rectangle([x0, GRID_TOP, x1, GRID_BOTTOM], radius=8, outline=FAINT, width=1)

        cx = (x0 + x1) / 2
        y = GRID_TOP + PAD

        if is_today:
            bbox = draw.textbbox((0, 0), label, font=day_font)
            tw = bbox[2] - bbox[0]
            pill_pad = 7
            pill_h = day_font.size + 8
            draw.rounded_rectangle(
                [cx - tw / 2 - pill_pad, y, cx + tw / 2 + pill_pad, y + pill_h],
                radius=pill_h / 2, fill=INK,
            )
            draw.text((cx, y + pill_h / 2), label, font=day_font, fill=255, anchor="mm")
            y += pill_h + 6
        else:
            draw.text((x0 + PAD, y), label, font=day_font, fill=INK)
            y += day_font.size + 8

        for block_label in block_labels:
            chip_h = block_font.size + 8
            text = self._truncate(block_label, block_font, col_width - 2 * PAD - 12)
            bbox = draw.textbbox((0, 0), text, font=block_font)
            chip_w = min(col_width - 2 * PAD, (bbox[2] - bbox[0]) + 16)
            chip_x0 = x0 + (col_width - chip_w) / 2
            draw.rounded_rectangle([chip_x0, y, chip_x0 + chip_w, y + chip_h], radius=chip_h / 2, fill=INK)
            draw.text((chip_x0 + chip_w / 2, y + chip_h / 2), text, font=block_font, fill=255, anchor="mm")
            y += chip_h + 5

        if tasks or events:
            draw.line([(x0 + PAD, y), (x1 - PAD, y)], fill=LINE, width=1)
            y += 5

        row_h = BADGE_SIZE + 5
        bottom_limit = GRID_BOTTOM - PAD
        max_rows = max(0, int((bottom_limit - y) / row_h))
        shown = 0

        for column, task in tasks:
            if shown >= max_rows:
                break
            self._draw_task_row(draw, x0 + PAD, y, col_width - 2 * PAD, column, task, item_font, badge_font)
            y += row_h
            shown += 1

        for event_text in events:
            if shown >= max_rows:
                break
            self._draw_event_row(draw, x0 + PAD, y, col_width - 2 * PAD, event_text, item_font)
            y += row_h
            shown += 1

        remaining = len(tasks) + len(events) - shown
        if remaining > 0 and y + item_font.size <= bottom_limit:
            draw.text((x0 + PAD, y), f"+{remaining} mehr", fill=MUTED, font=item_font)

    def _draw_task_row(self, draw, x, y, max_width, column, task, item_font, badge_font) -> None:
        done = bool(task.get("done"))
        self._draw_badge(draw, x, y, column, done, badge_font)
        text_x = x + BADGE_SIZE + BADGE_GAP
        text = self._truncate(task["text"], item_font, max_width - BADGE_SIZE - BADGE_GAP)
        row_mid = y + BADGE_SIZE / 2
        fill = MUTED if done else INK
        draw.text((text_x, row_mid), text, font=item_font, fill=fill, anchor="lm")
        if done:
            self._strike_through(draw, text_x, row_mid, text, item_font)

    @staticmethod
    def _draw_event_row(draw, x, y, max_width, text, item_font) -> None:
        r = 4
        cy = y + BADGE_SIZE / 2
        cx = x + r
        draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], outline=MUTED)
        text_x = x + 2 * r + 6
        truncated = HaushaltPlugin._truncate(text, item_font, max_width - 2 * r - 6)
        draw.text((text_x, cy), truncated, font=item_font, fill=MUTED, anchor="lm")

    @staticmethod
    def _strike_through(draw, x, row_mid, text, font) -> None:
        """Solid strikethrough for done tasks — unlike a gray fill, this survives
        1-bit e-ink rendering (no dithering) without vanishing."""
        if not text:
            return
        width = font.getlength(text)
        line_y = row_mid + font.size * 0.05
        draw.line([(x, line_y), (x + width, line_y)], fill=INK, width=1)

    @staticmethod
    def _draw_badge(draw, x, y, column, done, font) -> None:
        letter = BADGE_LETTER.get(column, "?")
        box = [x, y, x + BADGE_SIZE, y + BADGE_SIZE]
        if done:
            draw.rounded_rectangle(box, radius=3, fill=INK)
            text_fill = 255
        else:
            draw.rounded_rectangle(box, radius=3, outline=INK, width=1)
            text_fill = INK
        draw.text((x + BADGE_SIZE / 2, y + BADGE_SIZE / 2), letter, font=font, fill=text_fill, anchor="mm")

    # -- unassigned-tasks footer ------------------------------------------

    def _draw_footer(self, draw, unassigned, header_font, item_font, badge_font) -> None:
        x0, y0 = MARGIN, FOOTER_TOP
        x1, y1 = CANVAS_SIZE[0] - MARGIN, FOOTER_BOTTOM
        draw.rounded_rectangle([x0, y0, x1, y1], radius=8, outline=FAINT, width=1)

        x = x0 + PAD
        y = y0 + PAD
        draw.text((x, y), "Diese Woche (kein fester Tag)", font=header_font, fill=INK)
        y += header_font.size + 8

        if not unassigned:
            draw.text((x, y), "—", font=item_font, fill=MUTED)
            return

        right_limit = x1 - PAD
        row_h = max(BADGE_SIZE, item_font.size) + 8
        max_y = y1 - PAD - row_h

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
            fill = MUTED if done else INK
            draw.text((text_x, row_mid), text, font=item_font, fill=fill, anchor="lm")
            if done:
                self._strike_through(draw, text_x, row_mid, text, item_font)
            cx += item_w + 16
            shown += 1

        remaining = len(unassigned) - shown
        if remaining > 0:
            draw.text((cx, cy + BADGE_SIZE / 2), f"+{remaining} mehr", font=item_font, fill=MUTED, anchor="lm")

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _group_tasks(week: dict) -> tuple[dict[int, list[tuple[str, dict]]], list[tuple[str, dict]]]:
        tasks_by_day: dict[int, list[tuple[str, dict]]] = {i: [] for i in range(7)}
        unassigned: list[tuple[str, dict]] = []
        for column in ("jonathan", "katarina", "kids"):
            for task in week.get(column, []):
                day = task.get("day")
                if day is None:
                    unassigned.append((column, task))
                else:
                    tasks_by_day.setdefault(day, []).append((column, task))
        return tasks_by_day, unassigned

    @staticmethod
    def _truncate(text: str, font, max_width: float) -> str:
        if font.getlength(text) <= max_width:
            return text
        while text and font.getlength(text + "…") > max_width:
            text = text[:-1]
        return text + "…" if text else ""


def _monday_of_current_week() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())
