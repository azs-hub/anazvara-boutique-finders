export type Row = readonly string[];

/** Formats rows as left-aligned columns separated by two spaces, with a dashed line under the header. */
export function formatTable(header: Row, rows: readonly Row[]): string[] {
  const widths = header.map((cell, column) =>
    Math.max(cell.length, ...rows.map((row) => (row[column] ?? "").length)),
  );
  const line = (row: Row) =>
    widths
      .map((width, column) => (row[column] ?? "").padEnd(width))
      .join("  ")
      .trimEnd();

  return [line(header), line(widths.map((width) => "-".repeat(width))), ...rows.map(line)];
}
