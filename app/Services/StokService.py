from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment

class StokService:
    @staticmethod
    def generate_excel_from_data(title, headers, data, mapping_fn=None):
        wb = Workbook()
        ws = wb.active
        ws.title = title

        # Headers
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal='center')

        # Data rows
        for row in data:
            if mapping_fn:
                ws.append(mapping_fn(row))
            else:
                ws.append(row)

        # Auto-size columns
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            ws.column_dimensions[column].width = adjusted_width

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output
