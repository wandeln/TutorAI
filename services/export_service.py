"""
Excel-Export-Service: Generiert .xlsx-Dateien aus Übersichtstabellen.

Exportiert alle Students × Tasks mit erreichten Punkten + Summen.
Unterstützt Filter nach Blatt/Typ und Styling.
"""

import io
from typing import Optional
from sqlmodel import Session
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from config import EXCEL_SHEET_NAME
from database.models import Task, User


class ExportService:
    """
    Generiert Excel-Exporte für Tutor-Übersichten.
    
    Usage:
        export = ExportService()
        workbook = export.generate_overview(course_id, students, tasks, scores, session)
        return StreamingResponse(io.BytesIO(workbook.read()), media_type=..., filename=...)
    """
    
    # Styling-Constants
    HEADER_FONT = Font(name="Calibri", bold=True, size=12, color="FFFFFF")
    HEADER_FILL = PatternFill(start_color="2E74B5", end_color="2E74B5", fill_type="solid")
    SUM_FONT = Font(name="Calibri", bold=True, size=11)
    PASS_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")  # Grün
    FAIL_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")  # Rot
    THIN_BORDER = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    
    def generate_overview(
        self,
        course_name: str,
        students: list[User],
        tasks: list[Task],
        scores: dict,  # {student_id: {task_id: points}}
        session: Session,
        filter_text: Optional[str] = None,
    ) -> Workbook:
        """
        Generiert eine Excel-Übersicht.
        
        Structure:
          Row 1: Course header (merged)
          Row 2: Headers (Name, Task1, Task2, ..., Summe)
          Row 3+: Data (student_name, points, ..., total)
        """
        wb = Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = EXCEL_SHEET_NAME
        
        # ─── Filter Tasks wenn nötig ────────────────────────────
        if filter_text:
            filter_lower = filter_text.lower()
            tasks = [t for t in tasks if filter_lower in t.title.lower()]
        
        # ─── Row 1: Kurs-Header ─────────────────────────────────
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(tasks) + 2)
        header_cell = ws.cell(row=1, column=1, value=f"Punktestand: {course_name}")
        header_cell.font = Font(name="Calibri", bold=True, size=14, color="1F4E79")
        header_cell.alignment = Alignment(horizontal="center")
        
        # ─── Row 2: Spalten-Header ──────────────────────────────
        header_fill = self.HEADER_FILL
        header_font = self.HEADER_FONT
        
        headers = ["Name", "Matr.-Nr."]
        for task in tasks:
            headers.append(task.title)
        headers.extend(["Summe", "Anteil (%)"])
        
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=2, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", wrap_text=True)
            cell.border = self.THIN_BORDER
        
        # ─── Rows 3+: Daten ─────────────────────────────────────
        max_possible = sum(t.max_points for t in tasks)
        
        for row, student in enumerate(students, 3):
            student_id = student.id
            student_scores = scores.get(student_id, {})
            
            # Name
            ws.cell(row=row, column=1, value=student.name).font = Font(name="Calibri")
            ws.cell(row=row, column=1).border = self.THIN_BORDER
            
            # Matr.-Nr. (username als Ersatz)
            ws.cell(row=row, column=2, value=student.username).font = Font(name="Calibri")
            ws.cell(row=row, column=2).border = self.THIN_BORDER
            
            # Einzelpunkte pro Task
            total = 0
            for col, task in enumerate(tasks, 3):
                points = student_scores.get(task.id, 0)
                total += points
                cell = ws.cell(row=row, column=col, value=points)
                cell.alignment = Alignment(horizontal="center")
                cell.border = self.THIN_BORDER
                
                # Bedingte Formatierung: Grün/Rot basierend auf %
                if task.max_points > 0:
                    pct = points / task.max_points
                    cell.fill = self.PASS_FILL if pct >= 0.5 else self.FAIL_FILL
            
            # Summe
            ws.cell(row=row, column=3 + len(tasks), value=total).font = self.SUM_FONT
            ws.cell(row=row, column=3 + len(tasks)).border = self.THIN_BORDER
            
            # Anteil %
            pct_total = (total / max_possible * 100) if max_possible > 0 else 0
            pct_cell = ws.cell(row=row, column=4 + len(tasks), value=f"{pct_total:.1f}%")
            pct_cell.font = self.SUM_FONT
            pct_cell.alignment = Alignment(horizontal="center")
            pct_cell.border = self.THIN_BORDER
            pct_cell.fill = self.PASS_FILL if pct_total >= 50 else self.FAIL_FILL
        
        # ─── Spaltenbreiten anpassen ────────────────────────────
        ws.column_dimensions["A"].width = 20  # Name
        ws.column_dimensions["B"].width = 15  # Matr.-Nr.
        for col in range(3, len(tasks) + 3):
            from openpyxl.utils import get_column_letter
            col_letter = get_column_letter(col)
            ws.column_dimensions[col_letter].width = 25  # Tasks
        from openpyxl.utils import get_column_letter
        ws.column_dimensions[get_column_letter(3 + len(tasks))].width = 12  # Summe
        ws.column_dimensions[get_column_letter(4 + len(tasks))].width = 12  # %
        
        return wb
    
    def generate_overview_bytes(
        self,
        course_name: str,
        students: list[User],
        tasks: list[Task],
        scores: dict,
        session: Session,
        filter_text: Optional[str] = None,
    ) -> bytes:
        """Wie generate_overview, aber gibt Bytes zurück (für Response)."""
        wb = self.generate_overview(course_name, students, tasks, scores, session, filter_text)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()