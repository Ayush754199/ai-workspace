"Artifact-level checks for deterministic document rendering."""

import csv
import io
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pypdf import PdfReader
import openpyxl
from app.api.deps import get_db
from app.core.database import Base
from app.core.security import create_access_token
from app.main import app
from app.models.user import User

def test_pdf_xlsx_csv_generated_from_supplied_content(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'artifacts.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = factory()
    user = User(email="artifact-user@example.com", identifier="artifact-user@example.com", full_name="Artifact User", hashed_password="hash")
    db.add(user)
    db.commit()
    db.refresh(user)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': user.identifier})}"}
    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()
    app.dependency_overrides[get_db] = override_db
    content = "Date,Product,Quantity,Unit Price,Total Amount\n2026-01-01,Café mug,2,5.50,11.00\n2026-01-02,Tea,1,3.00,3.00"
    try:
        with TestClient(app) as client:
            pdf = client.post("/api/v1/documents/generate", headers=headers, json={"title":"Monthly sales","content":content,"format":"pdf","filename":"sales"})
            xlsx = client.post("/api/v1/documents/generate", headers=headers, json={"title":"Monthly sales","content":content,"format":"xlsx","filename":"sales"})
            csv_file = client.post("/api/v1/documents/generate", headers=headers, json={"title":"Monthly sales","content":content,"format":"csv","filename":"sales"})
        assert pdf.status_code == xlsx.status_code == csv_file.status_code == 200
        assert pdf.headers["content-type"].startswith("application/pdf")
        assert pdf.headers["content-disposition"].endswith('sales.pdf"')
        extracted = " ".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf.content)).pages)
        assert "Café mug" in extracted and "Total Amount" in extracted
        book = openpyxl.load_workbook(io.BytesIO(xlsx.content), data_only=True)
        sheet = book.active
        assert [sheet.cell(1, col).value for col in range(1, 6)] == ["Date", "Product", "Quantity", "Unit Price", "Total Amount"]
        assert sheet.cell(2, 2).value == "Café mug" and sheet.cell(2, 5).value == "11.00"
        assert csv_file.headers["content-type"].startswith("text/csv")
        decoded = csv_file.content.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(decoded)))
        assert rows[0]["Product"] == "Café mug" and rows[0]["Total Amount"] == "11.00"
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
