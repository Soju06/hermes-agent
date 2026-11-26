"""Markdown table -> Discord-friendly fenced box-drawing tables for ``DiscordAdapter.format_message``.

Discord does not render GitHub-flavored markdown pipe tables. Leaving them as raw pipes makes
dense tables hard to scan, especially in narrow mobile views, so detected tables are reformatted
as monospace box-drawing tables inside code fences. BasePlatformAdapter.truncate_message()
preserves code-block boundaries when a converted table exceeds Discord's 2000-character limit.
"""
from gateway.platforms.helpers import (
    TABLE_SEPARATOR_RE, display_width, is_table_row, split_markdown_table_row,
)


def _table_cells(line: str) -> list[str]:
    """Cells of a GFM row as upstream's splitter cuts them: an escaped pipe or a pipe inside a
    code span stays in its cell. The box replaces the table syntax, so the cell drops that
    syntax's ``\\|`` escape, as GFM does before it renders a cell."""
    return [cell.replace("\\|", "|") for cell in split_markdown_table_row(line)]


def _pad_display_width(text: str, target_width: int) -> str:
    """Pad *text* with spaces up to *target_width* display columns."""
    padding = target_width - display_width(text)
    return text + (" " * padding if padding > 0 else "")


def _render_markdown_table_for_discord(table_block: list[str]) -> str:
    """Render a detected GFM pipe table as a fenced box-drawing table."""
    if len(table_block) < 2:
        return "\n".join(table_block)

    headers = _table_cells(table_block[0])
    if len(headers) < 2:
        return "\n".join(table_block)

    num_cols = len(headers)
    rows: list[list[str]] = []
    for row_line in table_block[2:]:
        row = _table_cells(row_line)
        if len(row) < num_cols:
            row.extend([""] * (num_cols - len(row)))
        elif len(row) > num_cols:
            row = row[:num_cols]
        rows.append(row)

    col_widths = [max(3, display_width(header)) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            col_widths[index] = max(col_widths[index], display_width(cell))

    def border(left: str, middle: str, right: str) -> str:
        return left + middle.join("─" * (width + 2) for width in col_widths) + right

    def data_row(cells: list[str]) -> str:
        padded = [
            " " + _pad_display_width(cell, col_widths[index]) + " "
            for index, cell in enumerate(cells)
        ]
        return "│" + "│".join(padded) + "│"

    rendered = [
        border("┌", "┬", "┐"),
        data_row(headers),
        border("├", "┼", "┤"),
    ]
    rendered.extend(data_row(row) for row in rows)
    rendered.append(border("└", "┴", "┘"))

    return "```\n" + "\n".join(rendered) + "\n```"


def _wrap_markdown_tables_for_discord(text: str) -> str:
    """Rewrite GFM-style pipe tables into Discord-friendly codeblock tables.

    Tables are detected by a header row containing '|' immediately followed by
    a delimiter row matching :data:`TABLE_SEPARATOR_RE`. Existing fenced code
    blocks are left untouched so preformatted examples stay stable.
    """
    if '|' not in text or '-' not in text:
        return text

    lines = text.split('\n')
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        if stripped.startswith('```'):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue

        if (
            '|' in line
            and i + 1 < len(lines)
            and TABLE_SEPARATOR_RE.match(lines[i + 1])
        ):
            table_block = [line, lines[i + 1]]
            j = i + 2
            while j < len(lines) and is_table_row(lines[j]):
                table_block.append(lines[j])
                j += 1
            out.append(_render_markdown_table_for_discord(table_block))
            i = j
            continue

        out.append(line)
        i += 1

    return "\n".join(out)
