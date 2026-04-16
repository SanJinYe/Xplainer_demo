from typing import Iterable


def parse_item_line(line: str) -> tuple[str, int]:
    cleaned_line = line.strip()
    if not cleaned_line:
        return ("Unknown Item", 0)

    parts = [part.strip() for part in cleaned_line.split(":", maxsplit=1)]
    if len(parts) != 2:
        return (cleaned_line, 0)

    item_name, cents_text = parts
    if not item_name:
        item_name = "Unknown Item"

    try:
        cents = int(cents_text)
    except ValueError:
        cents = 0

    return (item_name, cents)


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    absolute_cents = abs(cents)
    dollars = absolute_cents // 100
    remainder = absolute_cents % 100
    return f"{sign}${dollars}.{remainder:02d}"


def _iter_non_empty_lines(raw_items: Iterable[str]) -> list[str]:
    lines: list[str] = []
    for raw_line in raw_items:
        candidate = raw_line.strip()
        if candidate:
            lines.append(candidate)
    return lines


def _merge_item_totals(raw_items: list[str]) -> list[tuple[str, int]]:
    totals_by_name: dict[str, int] = {}
    for raw_line in _iter_non_empty_lines(raw_items):
        item_name, cents = parse_item_line(raw_line)
        totals_by_name[item_name] = totals_by_name.get(item_name, 0) + cents
    return sorted(totals_by_name.items(), key=lambda item: item[0].lower())


def _render_item_lines(item_totals: list[tuple[str, int]]) -> list[str]:
    rendered_lines: list[str] = []
    for item_name, cents in item_totals:
        rendered_lines.append(f"- {item_name}: {format_cents(cents)}")
    return rendered_lines


def _build_footer(total_cents: int, item_count: int) -> str:
    if item_count == 0:
        return "No billable items."
    return f"Items: {item_count} | Total: {format_cents(total_cents)}"


def build_customer_summary(customer_name: str, raw_items: list[str]) -> str:
    cleaned_customer_name = " ".join(customer_name.strip().split())
    item_totals = _merge_item_totals(raw_items)

    total_cents = 0
    for _, cents in item_totals:
        total_cents += cents

    header = f"Summary for {cleaned_customer_name}"
    body_lines = _render_item_lines(item_totals)
    footer = _build_footer(total_cents, len(item_totals))

    sections = [header]
    sections.extend(body_lines)
    sections.append(footer)
    return "\n".join(sections)


class OrderSummaryService:
    def __init__(self, title_prefix: str = "Order Summary") -> None:
        self._title_prefix = title_prefix

    def build_summary(self, customer_name: str, raw_items: list[str]) -> str:
        cleaned_customer_name = " ".join(customer_name.strip().split())
        item_totals = _merge_item_totals(raw_items)
        total_cents = self._calculate_total_cents(item_totals)
        rendered_items = self._render_item_block(item_totals)

        title_line = f"{self._title_prefix}: {cleaned_customer_name}"
        summary_lines = [title_line]
        summary_lines.extend(rendered_items)
        summary_lines.append(_build_footer(total_cents, len(item_totals)))

        highest_item = self._find_highest_item(item_totals)
        if highest_item is not None:
            highest_name, highest_cents = highest_item
            summary_lines.append(
                f"Top item: {highest_name} ({format_cents(highest_cents)})"
            )

        return "\n".join(summary_lines)

    def _calculate_total_cents(self, item_totals: list[tuple[str, int]]) -> int:
        total_cents = 0
        for _, cents in item_totals:
            total_cents += cents
        return total_cents

    def _render_item_block(self, item_totals: list[tuple[str, int]]) -> list[str]:
        rendered_items: list[str] = []
        for item_name, cents in item_totals:
            rendered_items.append(f"* {item_name}: {format_cents(cents)}")
        if not rendered_items:
            rendered_items.append("* No items")
        return rendered_items

    def _find_highest_item(
        self,
        item_totals: list[tuple[str, int]],
    ) -> tuple[str, int] | None:
        if not item_totals:
            return None

        highest_name, highest_cents = item_totals[0]
        for item_name, cents in item_totals[1:]:
            if cents > highest_cents:
                highest_name = item_name
                highest_cents = cents
        return (highest_name, highest_cents)


if __name__ == "__main__":
    demo_items = [
        "Book:1299",
        "Pen:299",
        "Book:1299",
        "Sticker:199",
    ]
    print(build_customer_summary("  Ada   Lovelace  ", demo_items))
    print()
    service = OrderSummaryService()
    print(service.build_summary("  Ada   Lovelace  ", demo_items))
