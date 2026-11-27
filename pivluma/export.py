"""Write a forecast and its evaluation as an Excel workbook."""

from pathlib import Path
from tempfile import NamedTemporaryFile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


def write_forecast(path: Path, result, last_day, series: str, text) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = text("Forecast")
    sheet.append((text("Series"), series))
    sheet["B1"].data_type = "s"
    sheet.append((text("Last observed date"), last_day))
    sheet["B2"].number_format = "yyyy-mm-dd"
    sheet.append((text("Selected model"), text(result.model)))
    sheet.append((text("Model MAE"), result.mae))
    sheet.append((text("Last-price MAE"), result.baseline_mae))
    sheet.append((text("Test origins"), result.origins))
    sheet.append(())
    sheet.append((text("Step"), text("Forecast"), text("Lower bound"), text("Upper bound")))
    for cell in sheet[8]:
        cell.fill = PatternFill("solid", fgColor="234365")
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center")
    for step, values in enumerate(zip(result.forecast, result.lower, result.upper), 1):
        sheet.append((step, *values))
    for row in sheet.iter_rows(min_row=9, min_col=2, max_col=4):
        for cell in row:
            cell.number_format = "#,##0.00######"
    for index, width in enumerate((25, min(65, max(24, len(series) + 2)), 24, 24), 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A9"
    sheet.auto_filter.ref = f"A8:D{sheet.max_row}"
    with NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as file:
        temporary = Path(file.name)
    try:
        workbook.save(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
        workbook.close()
