"""اسکریپت تفکیک ۶۲ ردیف ستاره‌دار قیمت‌صفر بر پایهٔ برش فاز ۱ و دستهٔ ردیف (گام ج-۴ فاز ۲: P2-0)."""

from pathlib import Path
import sqlite3

SLICE_CHAPTERS = ("01", "02", "03", "05", "06", "07", "08", "28")


def main() -> None:
    """شمارش و گزارش ۶۲ ردیف ستاره‌دار قیمت‌صفر به تفکیک داخل/خارج برش و item_category."""
    repo_root = Path(__file__).resolve().parent.parent
    db_path = repo_root / "data_processed" / "reference.db"
    log_path = repo_root / "docs" / "step_c4_logs" / "p2_0_star_zero_split.txt"

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute(
        """
        SELECT code_official, chapter_code, item_category, unit, unit_price_rial, is_star_price_zero, description
        FROM items
        WHERE is_star_price_zero = 1
        ORDER BY code_official
        """
    )
    rows = cur.fetchall()
    conn.close()

    matrix: dict[tuple[str, str], list[tuple]] = {}
    out_of_slice_chapters: dict[str, int] = {}
    in_slice_chapters: dict[str, int] = {}

    for r in rows:
        code, ch, cat, unit, price, flag, desc = r
        slice_label = "in_slice" if ch in SLICE_CHAPTERS else "out_of_slice"
        matrix.setdefault((slice_label, cat), []).append(r)
        if slice_label == "in_slice":
            in_slice_chapters[ch] = in_slice_chapters.get(ch, 0) + 1
        else:
            out_of_slice_chapters[ch] = out_of_slice_chapters.get(ch, 0) + 1

    lines: list[str] = []
    lines.append(f"Total rows with is_star_price_zero = 1: {len(rows)}")
    lines.append("")
    lines.append("=== Cross-tab: (slice_status x item_category) ===")
    for slice_label in ("in_slice", "out_of_slice"):
        for cat in ("base", "additional", "deduction"):
            cnt = len(matrix.get((slice_label, cat), []))
            lines.append(f"  {slice_label:12s} | {cat:10s} : {cnt}")
    in_total = sum(len(matrix.get(("in_slice", c), [])) for c in ("base", "additional", "deduction"))
    out_total = sum(len(matrix.get(("out_of_slice", c), [])) for c in ("base", "additional", "deduction"))
    base_total = sum(len(matrix.get((s, "base"), [])) for s in ("in_slice", "out_of_slice"))
    add_total = sum(len(matrix.get((s, "additional"), [])) for s in ("in_slice", "out_of_slice"))
    lines.append(f"  TOTAL in_slice     : {in_total}")
    lines.append(f"  TOTAL out_of_slice : {out_total}")
    lines.append(f"  TOTAL base         : {base_total}")
    lines.append(f"  TOTAL additional   : {add_total}")
    lines.append("")
    lines.append(f"=== In-slice chapters breakdown ({in_total} rows) ===")
    for ch, cnt in sorted(in_slice_chapters.items()):
        lines.append(f"  chapter {ch}: {cnt}")
    lines.append("")
    lines.append(f"=== Out-of-slice chapters breakdown ({out_total} rows across {len(out_of_slice_chapters)} chapters) ===")
    lines.append("  Out-of-slice chapter_code list: " + ", ".join(sorted(out_of_slice_chapters.keys())))
    for ch, cnt in sorted(out_of_slice_chapters.items()):
        lines.append(f"  chapter {ch}: {cnt}")
    lines.append("")
    lines.append("=== Detailed list of IN-SLICE star-price-zero rows ===")
    for cat in ("base", "additional", "deduction"):
        for r in matrix.get(("in_slice", cat), []):
            lines.append(f"  {r[0]} | ch={r[1]} | cat={r[2]} | unit={r[3]} | price={r[4]} | {r[6][:60]}")

    output_text = "\n".join(lines) + "\n"
    log_path.write_text(output_text, encoding="utf-8")
    print("\n".join(lines[:38]))


if __name__ == "__main__":
    main()
