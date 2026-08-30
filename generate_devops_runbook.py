#!/usr/bin/env python3
"""
Script to generate DEVOPS_RUNBOOK.docx
Comprehensive DevOps Runbook & Operational Guide for IT Helpdesk Multi-Agent AI System.
"""

import os
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

# --- Color Palette Constants ---
NAVY_PRIMARY = RGBColor(26, 54, 93)      # #1A365D - Main Headings
BLUE_SECONDARY = RGBColor(43, 108, 176)   # #2B6CB0 - Sub-headings
TEXT_DARK = RGBColor(45, 55, 72)         # #2D3748 - Body Text
GRAY_MUTED = RGBColor(113, 128, 150)     # #718096 - Metadata / Captions
WHITE = RGBColor(255, 255, 255)
COLOR_CODE_BG = "F1F5F9"                 # Light Slate Gray for code
COLOR_CALLOUT_NOTE_BG = "EBF8FF"         # Soft Blue
COLOR_CALLOUT_NOTE_BORDER = "3182CE"
COLOR_CALLOUT_WARN_BG = "FFF5F5"         # Soft Red
COLOR_CALLOUT_WARN_BORDER = "E53E3E"
COLOR_CALLOUT_IMPORTANT_BG = "FFFAF0"    # Soft Orange/Amber
COLOR_CALLOUT_IMPORTANT_BORDER = "DD6B20"
COLOR_CALLOUT_TIP_BG = "F0FFF4"          # Soft Green
COLOR_CALLOUT_TIP_BORDER = "38A169"
COLOR_TABLE_HEADER_BG = "1E3A8A"         # Deep Blue
COLOR_TABLE_ROW_ALT = "F8FAFC"           # Very Light Slate
COLOR_TABLE_BORDER = "CBD5E1"            # Light Border

def set_cell_background(cell, fill_hex):
    """Sets cell shading color in hex."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>')
    tcPr.append(shd)

def set_cell_margins(cell, top=140, bottom=140, left=180, right=180):
    """Sets cell padding (in dxa)."""
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'<w:top w:w="{top}" w:type="dxa"/>'
        f'<w:bottom w:w="{bottom}" w:type="dxa"/>'
        f'<w:left w:w="{left}" w:type="dxa"/>'
        f'<w:right w:w="{right}" w:type="dxa"/>'
        f'</w:tcMar>'
    )
    tcPr.append(tcMar)

def set_table_borders(table, border_color="CBD5E1"):
    """Sets subtle table borders."""
    tblPr = table._tbl.tblPr
    borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        f'<w:top w:val="single" w:sz="4" w:space="0" w:color="{border_color}"/>'
        f'<w:left w:val="none"/>'
        f'<w:bottom w:val="single" w:sz="6" w:space="0" w:color="{border_color}"/>'
        f'<w:right w:val="none"/>'
        f'<w:insideH w:val="single" w:sz="4" w:space="0" w:color="{border_color}"/>'
        f'<w:insideV w:val="none"/>'
        f'</w:tblBorders>'
    )
    tblPr.append(borders)

def add_styled_heading(doc, text, level):
    """Adds styled headings with proper spacing and color."""
    p = doc.add_paragraph()
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text)
    run.bold = True
    
    if level == 1:
        p.paragraph_format.space_before = Pt(18)
        p.paragraph_format.space_after = Pt(8)
        run.font.size = Pt(15)
        run.font.color.rgb = NAVY_PRIMARY
        run.font.name = "Arial"
    elif level == 2:
        p.paragraph_format.space_before = Pt(14)
        p.paragraph_format.space_after = Pt(6)
        run.font.size = Pt(12.5)
        run.font.color.rgb = BLUE_SECONDARY
        run.font.name = "Arial"
    elif level == 3:
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(4)
        run.font.size = Pt(11)
        run.font.color.rgb = NAVY_PRIMARY
        run.font.name = "Arial"
    return p

def add_body_paragraph(doc, text="", space_after=6, bold_prefix=None):
    """Adds a standardized body paragraph."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.15
    
    if bold_prefix:
        r_prefix = p.add_run(bold_prefix)
        r_prefix.bold = True
        r_prefix.font.size = Pt(10)
        r_prefix.font.color.rgb = TEXT_DARK
        r_prefix.font.name = "Arial"
        
    if text:
        r = p.add_run(text)
        r.font.size = Pt(10)
        r.font.color.rgb = TEXT_DARK
        r.font.name = "Arial"
    return p

def add_code_block(doc, code_text):
    """Creates a distinct monospace code block with shaded background and border."""
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    
    cell = tbl.cell(0, 0)
    cell.width = Inches(6.5)
    set_cell_background(cell, COLOR_CODE_BG)
    set_cell_margins(cell, top=120, bottom=120, left=180, right=180)
    
    # Border for the code box
    tcPr = cell._tc.get_or_add_tcPr()
    borders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'<w:top w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        f'<w:left w:val="single" w:sz="18" w:space="0" w:color="94A3B8"/>'
        f'<w:bottom w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        f'<w:right w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        f'</w:tcBorders>'
    )
    tcPr.append(borders)
    
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.05
    
    run = p.add_run(code_text.strip())
    run.font.name = "Consolas"
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(30, 41, 59)
    
    sp = doc.add_paragraph()
    sp.paragraph_format.space_before = Pt(0)
    sp.paragraph_format.space_after = Pt(4)

def add_callout(doc, callout_type, title, message):
    """Adds a formatted callout box (NOTE, WARNING, IMPORTANT, TIP)."""
    type_configs = {
        "NOTE": (COLOR_CALLOUT_NOTE_BG, COLOR_CALLOUT_NOTE_BORDER, "📌 GHI CHÚ (NOTE):"),
        "WARNING": (COLOR_CALLOUT_WARN_BG, COLOR_CALLOUT_WARN_BORDER, "⚠️ CẢNH BÁO (WARNING):"),
        "IMPORTANT": (COLOR_CALLOUT_IMPORTANT_BG, COLOR_CALLOUT_IMPORTANT_BORDER, "❗ QUAN TRỌNG (IMPORTANT):"),
        "TIP": (COLOR_CALLOUT_TIP_BG, COLOR_CALLOUT_TIP_BORDER, "💡 GỢI Ý KỸ THUẬT (TIP):"),
    }
    bg_color, border_color, default_prefix = type_configs.get(
        callout_type.upper(), 
        (COLOR_CALLOUT_NOTE_BG, COLOR_CALLOUT_NOTE_BORDER, "📌 NOTE:")
    )
    
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    
    cell = tbl.cell(0, 0)
    cell.width = Inches(6.5)
    set_cell_background(cell, bg_color)
    set_cell_margins(cell, top=140, bottom=140, left=180, right=180)
    
    tcPr = cell._tc.get_or_add_tcPr()
    borders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'<w:top w:val="none"/>'
        f'<w:left w:val="single" w:sz="24" w:space="0" w:color="{border_color}"/>'
        f'<w:bottom w:val="none"/>'
        f'<w:right w:val="none"/>'
        f'</w:tcBorders>'
    )
    tcPr.append(borders)
    
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.15
    
    r_title = p.add_run(f"{default_prefix} {title}\n" if title else f"{default_prefix}\n")
    r_title.bold = True
    r_title.font.name = "Arial"
    r_title.font.size = Pt(9.5)
    r_title.font.color.rgb = NAVY_PRIMARY
    
    r_msg = p.add_run(message)
    r_msg.font.name = "Arial"
    r_msg.font.size = Pt(9.5)
    r_msg.font.color.rgb = TEXT_DARK
    
    sp = doc.add_paragraph()
    sp.paragraph_format.space_before = Pt(0)
    sp.paragraph_format.space_after = Pt(4)

def format_custom_table(table, col_widths, headers, data):
    """Fills and formats a professional table with custom header and alternate row shading."""
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_table_borders(table)
    
    # Format Header Row
    hdr_cells = table.rows[0].cells
    for i, title in enumerate(headers):
        cell = hdr_cells[i]
        cell.width = Inches(col_widths[i])
        set_cell_background(cell, COLOR_TABLE_HEADER_BG)
        set_cell_margins(cell, top=140, bottom=140, left=140, right=140)
        p = cell.paragraphs[0]
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(title)
        run.bold = True
        run.font.name = "Arial"
        run.font.size = Pt(9.5)
        run.font.color.rgb = WHITE
        
    # Set cantSplit and tblHeader
    trPr = table.rows[0]._tr.get_or_add_trPr()
    trPr.append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))
    trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))

    # Format Data Rows
    for row_idx, row_data in enumerate(data):
        row = table.add_row()
        trPr = row._tr.get_or_add_trPr()
        trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
        
        bg_color = COLOR_TABLE_ROW_ALT if row_idx % 2 == 1 else "FFFFFF"
        for col_idx, text in enumerate(row_data):
            cell = row.cells[col_idx]
            cell.width = Inches(col_widths[col_idx])
            set_cell_background(cell, bg_color)
            set_cell_margins(cell, top=100, bottom=100, left=140, right=140)
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.1
            run = p.add_run(str(text))
            run.font.name = "Arial"
            run.font.size = Pt(9)
            run.font.color.rgb = TEXT_DARK

def build_runbook_docx(output_path):
    """Main builder function for the complete DevOps Runbook."""
    doc = Document()
    
    # Set page margins to 1 inch
    sections = doc.sections
    for section in sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)
        
        # Header setup
        header = section.header
        hp = header.paragraphs[0]
        hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        hr = hp.add_run("IT Helpdesk Multi-Agent AI | Enterprise DevOps Runbook v2.1")
        hr.font.name = "Arial"
        hr.font.size = Pt(8.5)
        hr.font.color.rgb = GRAY_MUTED
        
        # Footer setup
        footer = section.footer
        fp = footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        fr = fp.add_run("CONFIDENTIAL - INTERNAL IT & DEVOPS USE ONLY")
        fr.font.name = "Arial"
        fr.font.size = Pt(8.5)
        fr.font.color.rgb = GRAY_MUTED

    # --- DOCUMENT HEADER / TITLE ---
    title_p = doc.add_paragraph()
    title_p.paragraph_format.space_before = Pt(0)
    title_p.paragraph_format.space_after = Pt(4)
    run_title = title_p.add_run("HƯỚNG DẪN VẬN HÀNH & TRIỂN KHAI HẠ TẦNG\n(DEVOPS RUNBOOK & OPERATIONAL GUIDE)")
    run_title.bold = True
    run_title.font.name = "Arial"
    run_title.font.size = Pt(18)
    run_title.font.color.rgb = NAVY_PRIMARY

    sub_p = doc.add_paragraph()
    sub_p.paragraph_format.space_before = Pt(0)
    sub_p.paragraph_format.space_after = Pt(14)
    run_sub = sub_p.add_run("Hệ thống IT Helpdesk Multi-Agent AI Enterprise (Google Cloud Run, BigQuery Vector Search, ADK & SSO)")
    run_sub.font.name = "Arial"
    run_sub.font.size = Pt(11)
    run_sub.font.color.rgb = BLUE_SECONDARY

    # Metadata Table
    meta_table = doc.add_table(rows=1, cols=4)
    format_custom_table(
        meta_table,
        [1.5, 1.75, 1.5, 1.75],
        ["Thuộc tính", "Giá trị", "Thuộc tính", "Giá trị"],
        [
            ["Hệ thống", "IT Helpdesk Multi-Agent AI", "Phiên bản tài liệu", "2.1.0-Enterprise"],
            ["Môi trường mục tiêu", "Production / Staging / Dev", "Chủ quản kỹ thuật", "DevOps & Cloud Platform"],
            ["Mô hình AI", "Gemini 3 Flash & Pro", "Phê duyệt bởi", "Principal Cloud Architect"],
            ["Hạ tầng Cloud", "Google Cloud Platform (GCP)", "Ngày cập nhật", "28/08/2026"],
        ]
    )

    doc.add_paragraph().paragraph_format.space_after = Pt(6)

    # --- MỤC 1: TỔNG QUAN KIẾN TRÚC HẠ TẦNG ---
    add_styled_heading(doc, "1. TỔNG QUAN KIẾN TRÚC HẠ TẦNG & THÀNH PHẦN VẬN HÀNH", 1)
    
    add_body_paragraph(
        doc,
        "Hệ thống IT Helpdesk Multi-Agent AI được thiết kế theo kiến trúc phi trạng thái (Stateless Microservices), "
        "đóng gói container chuẩn OCI và triển khai trên hạ tầng Google Cloud Run thế hệ 2 (Cloud Run v2). "
        "Hệ thống phối hợp chặt chẽ giữa các dịch vụ PaaS và Serverless cao cấp của Google Cloud Platform nhằm đảm bảo "
        "tối ưu hóa độ trễ, khả năng tự động mở rộng (Autoscaling) và tối thiểu hóa chi phí vận hành cố định."
    )

    add_body_paragraph(
        doc,
        "",
        bold_prefix="Danh mục các thành phần hạ tầng cốt lõi:"
    )

    infra_table = doc.add_table(rows=1, cols=3)
    format_custom_table(
        infra_table,
        [1.8, 1.8, 2.9],
        ["Thành phần GCP", "Dịch vụ đảm nhiệm", "Mô tả vai trò kỹ thuật & Cơ chế vận hành"],
        [
            ["Compute Layer", "Google Cloud Run (v2)", "Chạy ứng dụng FastAPI & ADK Agents. Cấu hình 2 vCPU, 2GB RAM, concurrency 80, tự động scale 0 -> N instances."],
            ["Vector Knowledge Base", "BigQuery Vector Search", "Lưu trữ tài liệu RAG & vector embeddings (Dataset: it_helpdesk_kb). Serverless 100%, chi phí cố định 0 USD."],
            ["Ticket State Store", "Cloud Firestore", "Lưu trữ trạng thái Helpdesk Tickets (Collection: helpdesk_tickets). Write-through caching kết hợp in-memory fallback."],
            ["Identity & SSO", "Google OIDC + Cloud IAM", "Xác thực danh tính qua JWKS public keys của accounts.google.com, phân quyền theo email domain và vai trò RBAC."],
            ["Semantic Cache", "In-Memory Cosine Cache", "Bộ đệm ngữ nghĩa câu hỏi lặp lại với ngưỡng Cosine Sim >= 0.92, phản hồi < 50ms, tiết kiệm 100% token Gemini."],
            ["Secret Management", "GCP Secret Manager", "Lưu trữ an toàn các biến môi trường nhạy cảm, API keys và secret JWT mà không hardcode vào mã nguồn."],
            ["Container Registry", "Artifact Registry", "Kho lưu trữ Docker images bảo mật (Format: Docker v2, Region: us-central1 hoặc asia-southeast1)."],
            ["Observability", "Cloud Trace & Logging", "Thu thập structured logs chuẩn JSON, tracing phân tán qua OpenTelemetry và giám sát sức khỏe dịch vụ."],
        ]
    )

    add_callout(
        doc,
        "TIP",
        "Tối ưu hóa Chi phí Hạ tầng Serverless",
        "Nhờ áp dụng BigQuery Vector Search thay thế cho Vertex AI Vector Search Index Endpoints chuyên dụng, chi phí hạ tầng tĩnh giảm từ ~300 USD/tháng xuống gần 0 USD/tháng cho các doanh nghiệp có kho tri thức dưới 100.000 vectors."
    )

    # --- MỤC 2: BẢNG QUẢN LÝ BIẾN MÔI TRƯỜNG & BÍ MẬT BẢO MẬT ---
    add_styled_heading(doc, "2. BẢNG QUẢN LÝ BIẾN MÔI TRƯỜNG & BÍ MẬT BẢO MẬT", 1)
    
    add_body_paragraph(
        doc,
        "Mọi tham số cấu hình của hệ thống được quản lý thông qua biến môi trường (Environment Variables) "
        "và Google Secret Manager. Khi triển khai trên Production, toàn bộ các biến bảo mật phải được nạp thông qua cơ chế "
        "Secret Reference của Cloud Run thay vì gán giá trị Plaintext."
    )

    env_table = doc.add_table(rows=1, cols=5)
    format_custom_table(
        env_table,
        [1.6, 0.9, 1.2, 0.8, 2.0],
        ["Tên Biến Môi Trường", "Bắt buộc", "Giá trị mặc định", "Secret Mngr", "Mục đích & Hướng dẫn Cấu hình"],
        [
            ["ENVIRONMENT", "Có", "development", "Không", "Môi trường thực thi: 'development', 'staging', 'production'."],
            ["GOOGLE_CLOUD_PROJECT", "Có", "—", "Không", "ID của dự án Google Cloud Platform (GCP Project ID)."],
            ["GOOGLE_CLOUD_REGION", "Có", "us-central1", "Không", "Vùng triển khai Cloud Run và BigQuery (vd: us-central1)."],
            ["SSO_CLIENT_ID", "Có", "—", "Có", "OAuth 2.0 Client ID được cấp từ GCP Identity Console."],
            ["ALLOWED_DOMAINS", "Có (Prod)", "—", "Không", "Danh sách domain email công ty được phép (vd: company.com,corp.com). Bắt buộc phải set trong Prod để kích hoạt Fail-Closed."],
            ["ALLOW_LOCAL_DEV_SSO", "Không", "false", "Không", "Bật tính năng giả lập SSO cho local dev. Tự động bị khóa thành false trên môi trường Production."],
            ["SSO_JWT_SECRET", "Không", "—", "Có", "Khóa bí mật HS256 chỉ dùng để ký token thử nghiệm trong local dev."],
            ["USE_FIRESTORE_TICKETS", "Không", "false", "Không", "Bật lưu trữ Firestore cho tickets (tự động bật trên Cloud Run)."],
            ["KNOWLEDGE_BACKEND", "Không", "in_memory", "Không", "Backend cho RAG Knowledge Store: 'in_memory' hoặc 'bigquery'."],
            ["BIGQUERY_KB_DATASET", "Không", "it_helpdesk_kb", "Không", "Dataset BigQuery chứa bảng vectors và tài liệu tri thức."],
            ["SEMANTIC_CACHE_ENABLED", "Không", "true", "Không", "Bật/Tắt lớp bộ đệm ngữ nghĩa cho câu hỏi lặp lại."],
            ["SEMANTIC_CACHE_THRESHOLD", "Không", "0.92", "Không", "Ngưỡng độ tương đồng Cosine Similarity để coi là trùng khớp."],
            ["OTEL_TO_CLOUD", "Không", "false", "Không", "Đẩy OpenTelemetry traces lên GCP Cloud Trace."],
        ]
    )

    add_callout(
        doc,
        "WARNING",
        "Quy định An toàn Thông tin - Biến ALLOWED_DOMAINS",
        "Trên môi trường Production, nếu ALLOWED_DOMAINS bị bỏ trống, hệ thống sẽ kích hoạt cơ chế Fail-Closed và từ chối toàn bộ token đăng nhập. Tuyệt đối không để rỗng cấu hình này khi bàn giao cho bộ phận Vận hành."
    )

    # --- MỤC 3: HƯỚNG DẪN KHỞI TẠO HẠ TẦNG TỰ ĐỘNG (TERRAFORM) ---
    add_styled_heading(doc, "3. HƯỚNG DẪN KHỞI TẠO HẠ TẦNG TỰ ĐỘNG (TERRAFORM IAC)", 1)
    
    add_body_paragraph(
        doc,
        "Toàn bộ tài nguyên đám mây được định nghĩa dưới dạng mã (Infrastructure as Code) trong thư mục deployment/terraform. "
        "Bộ mã Terraform đã được cấu hình tuân thủ nguyên tắc đặc quyền tối thiểu (Least Privilege) và quản lý vòng đời tài nguyên thông minh."
    )

    add_styled_heading(doc, "3.1. Cấu trúc Tài nguyên Terraform (Terraform Resource Mapping)", 2)
    
    add_body_paragraph(
        doc,
        "• google_service_account.helpdesk_sa: Service Account đại diện cho ứng dụng chạy trên Cloud Run.\n"
        "• google_project_iam_member: Cấp các quyền chặt chẽ: roles/aiplatform.user (Vertex AI), roles/bigquery.dataEditor & roles/bigquery.jobUser (BigQuery), roles/logging.logWriter (Cloud Logging), roles/secretmanager.secretAccessor (Secret Manager).\n"
        "• google_bigquery_dataset.kb_dataset: Khởi tạo dataset it_helpdesk_kb lưu trữ vector articles.\n"
        "• google_artifact_registry_repository.app_repo: Kho chứa Docker Image định dạng Docker v2.\n"
        "• google_cloud_run_v2_service.helpdesk_service: Định nghĩa Cloud Run Service với cấu hình CPU, Memory và Environment Variables."
    )

    add_styled_heading(doc, "3.2. Quy trình Thực thi Terraform", 2)
    
    add_body_paragraph(
        doc,
        "Thực hiện theo các bước sau từ máy trạm DevOps hoặc qua Terraform Cloud / CI Runner:"
    )

    add_code_block(
        doc,
        "# Bước 1: Di chuyển vào thư mục Terraform\n"
        "cd deployment/terraform\n\n"
        "# Bước 2: Khởi tạo Terraform Provider & State Backend\n"
        "terraform init\n\n"
        "# Bước 3: Kiểm tra kế hoạch thay đổi (Dry-Run)\n"
        "terraform plan \\\n"
        "  -var=\"project_id=my-company-it-prod\" \\\n"
        "  -var=\"region=us-central1\" \\\n"
        "  -var=\"sso_client_id=123456789-abc.apps.googleusercontent.com\" \\\n"
        "  -var=\"allowed_domains=mycompany.com,corp.mycompany.com\" \\\n"
        "  -out=tfplan.binary\n\n"
        "# Bước 4: Áp dụng thay đổi vào hạ tầng GCP\n"
        "terraform apply tfplan.binary"
    )

    add_callout(
        doc,
        "IMPORTANT",
        "Cơ chế Chống Drift Cấu hình CI/CD (Lifecycle Ignore Changes)",
        "Trong file main.tf, khối cấu hình Cloud Run đã được gán 'lifecycle { ignore_changes = [template[0].containers[0].image] }'. Điều này đảm bảo khi Pipeline CI/CD cập nhật SHA của image container mới, Terraform sẽ không tự ý rollback image về phiên bản cũ khi chạy lệnh apply định kỳ."
    )

    # --- MỤC 4: QUY TRÌNH ĐÓNG GÓI CONTAINER & TRIỂN KHAI ---
    add_styled_heading(doc, "4. QUY TRÌNH ĐÓNG GÓI CONTAINER & TRIỂN KHAI (CI/CD PIPELINE)", 1)
    
    add_body_paragraph(
        doc,
        "Hệ thống sử dụng Dockerfile chuẩn Production với chiến lược Multi-Stage Build và quản lý package cực nhanh qua công cụ UV của Astral. "
        "Container được chạy dưới quyền người dùng không đặc quyền (non-root user `appuser` với UID 1001) để phòng chống leo thang đặc quyền."
    )

    add_styled_heading(doc, "4.1. Quy trình Build & Push Container Image", 2)

    add_code_block(
        doc,
        "# Đặt biến môi trường dự án\n"
        "export PROJECT_ID=\"my-company-it-prod\"\n"
        "export REGION=\"us-central1\"\n"
        "export REPO=\"it-helpdesk-repo\"\n"
        "export IMAGE_TAG=\"$(git rev-parse --short HEAD)\"\n"
        "export IMAGE_URI=\"${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/it-helpdesk-agent:${IMAGE_TAG}\"\n\n"
        "# Build và push image lên Artifact Registry qua Cloud Build\n"
        "gcloud builds submit --tag ${IMAGE_URI} .\n\n"
        "# Hoặc sử dụng Makefile tích hợp sẵn\n"
        "make docker-deploy PROJECT_ID=${PROJECT_ID} REGION=${REGION}"
    )

    add_styled_heading(doc, "4.2. Cập nhật Revision Mới trên Cloud Run", 2)

    add_code_block(
        doc,
        "# Cập nhật container image cho Cloud Run service\n"
        "gcloud run services update it-helpdesk-agent \\\n"
        "  --image=${IMAGE_URI} \\\n"
        "  --region=${REGION} \\\n"
        "  --project=${PROJECT_ID}"
    )

    add_styled_heading(doc, "4.3. Chiến lược Di chuyển Lưu lượng & Rollback (Traffic Migration)", 2)
    
    add_body_paragraph(
        doc,
        "Cloud Run tự động kích hoạt Revision mới với 100% traffic sau khi vượt qua Healthcheck. "
        "Nếu phát hiện lỗi nghiêm trọng, thực hiện rollback về Revision trước đó chỉ trong vòng dưới 30 giây bằng lệnh:"
    )

    add_code_block(
        doc,
        "# Liệt kê các revision gần nhất\n"
        "gcloud run revisions list --service=it-helpdesk-agent --region=${REGION}\n\n"
        "# Điều hướng 100% lưu lượng về Revision ổn định trước đó (vd: it-helpdesk-agent-00042-xyz)\n"
        "gcloud run services update-traffic it-helpdesk-agent \\\n"
        "  --to-revisions=it-helpdesk-agent-00042-xyz=100 \\\n"
        "  --region=${REGION}"
    )

    # --- MỤC 5: QUY TRÌNH VẬN HÀNH CHUẨN (SOPS) ---
    add_styled_heading(doc, "5. QUY TRÌNH VẬN HÀNH CHUẨN (STANDARD OPERATING PROCEDURES)", 1)

    add_styled_heading(doc, "SOP-01: Đồng bộ Môi trường & Dependencies Cục bộ", 3)
    add_body_paragraph(
        doc,
        "Sử dụng công cụ UV để khởi tạo và đồng bộ virtual environment:"
    )
    add_code_block(doc, "uv sync")

    add_styled_heading(doc, "SOP-02: Khởi chạy Chế độ Kiểm thử Tương tác CLI", 3)
    add_body_paragraph(
        doc,
        "Cho phép kỹ sư DevOps kiểm tra trực tiếp phản hồi của Orchestrator và các Sub-agent từ Terminal:"
    )
    add_code_block(doc, "uv run python main.py --mode cli")

    add_styled_heading(doc, "SOP-03: Khởi chạy Web Server FastAPI & ADK UI", 3)
    add_body_paragraph(
        doc,
        "Khởi chạy web server phục vụ API và giao diện tương tác:"
    )
    add_code_block(doc, "uv run python main.py --mode serve --host 0.0.0.0 --port 8080")

    add_styled_heading(doc, "SOP-04: Thực thi Toàn bộ Bộ Kiểm thử Tự động (Pytest)", 3)
    add_body_paragraph(
        doc,
        "Bắt buộc thực thi trước khi commit code hoặc deploy lên bất kỳ môi trường nào. Bộ kiểm thử bao gồm 46 test cases:"
    )
    add_code_block(doc, "uv run pytest tests/ -v")

    add_styled_heading(doc, "SOP-05: Quản trị & Giám sát Semantic Cache", 3)
    add_body_paragraph(
        doc,
        "Kiểm tra hiệu quả tỷ lệ Cache Hit Rate thông qua API Endpoint:"
    )
    add_code_block(
        doc,
        "# Lấy thống kê hiệu năng bộ đệm\n"
        "curl -X GET http://localhost:8080/api/cache/stats \\\n"
        "  -H \"Authorization: Bearer <VALID_OIDC_TOKEN>\"\n\n"
        "# Tra cứu trực tiếp câu hỏi tương tự\n"
        "curl -X GET \"http://localhost:8080/api/cache/query?q=cách+đổi+mật+khẩu+wifi&threshold=0.92\" \\\n"
        "  -H \"Authorization: Bearer <VALID_OIDC_TOKEN>\""
    )

    # --- MỤC 6: GIÁM SÁT, CẢNH BÁO & KHẢ NĂNG QUAN SÁT ---
    add_styled_heading(doc, "6. GIÁM SÁT, CẢNH BÁO & KHẢ NĂNG QUAN SÁT (OBSERVABILITY & MONITORING)", 1)
    
    add_body_paragraph(
        doc,
        "Hệ thống cung cấp khả năng quan sát toàn diện thông qua 3 trụ cột: Metrics, Logs và Traces."
    )

    add_styled_heading(doc, "6.1. Healthcheck & Liveness/Readiness Probes", 2)
    add_body_paragraph(
        doc,
        "Endpoint `/healthz` hoạt động công khai không cần auth, phục vụ kiểm tra sức khỏe của Cloud Run Load Balancer:\n"
        "• Trả về `{\"status\": \"ok\", \"service\": \"it_helpdesk_agent\"}` với mã HTTP 200 OK."
    )

    add_styled_heading(doc, "6.2. Ma trận Cảnh báo Vận hành (Alerting Policy Matrix)", 2)

    alert_table = doc.add_table(rows=1, cols=4)
    format_custom_table(
        alert_table,
        [1.6, 1.4, 1.5, 2.0],
        ["Chỉ số Giám sát", "Ngưỡng Cảnh báo", "Mức độ Nghiêm trọng", "Hành động Vận hành Khuyến nghị"],
        [
            ["HTTP 5xx Error Rate", "> 1.0% trong 5 phút", "CRITICAL (P1)", "Kiểm tra Cloud Logging tìm Exception. Rollback revision nếu xảy ra sau khi deploy."],
            ["P95 Latency", "> 3.0 giây trong 5 phút", "HIGH (P2)", "Kiểm tra tỉ lệ Cache Hit Rate, độ trễ API Vertex AI hoặc tắc nghẽn BigQuery."],
            ["OIDC Auth Failures", "> 50 lỗi / phút", "HIGH (P2)", "Kiểm tra cấu hình ALLOWED_DOMAINS, JWKS Google certs hoặc token Client ID."],
            ["Container Memory Usage", "> 85% giới hạn (1.7GB)", "MEDIUM (P3)", "Kiểm tra rò rỉ bộ nhớ (Memory Leak), điều chỉnh giới hạn RAM lên 4GB."],
            ["BigQuery Quota Error", "> 5 lỗi QuotaExceeded", "MEDIUM (P3)", "Yêu cầu tăng quota BigQuery Query Execution trên GCP Console."],
        ]
    )

    # --- MỤC 7: KỊCH BẢN XỬ LÝ SỰ CỐ & KHÔI PHỤC THẢM HỌA ---
    add_styled_heading(doc, "7. KỊCH BẢN XỬ LÝ SỰ CỐ & KHÔI PHỤC THẢM HỌA (INCIDENT RESPONSE)", 1)

    add_styled_heading(doc, "Sự cố 1: Lỗi Xác thực SSO Hàng loạt (HTTP 401/403)", 2)
    add_body_paragraph(
        doc,
        "• Triệu chứng: Người dùng không thể đăng nhập hoặc nhận thông báo 'Truy cập bị từ chối: Domain không được phép'.\n"
        "• Nguyên nhân gốc rễ: Biến ALLOWED_DOMAINS bị thiếu domain mới sáp nhập, hoặc SSO_CLIENT_ID không khớp với Google Client ID trên Frontend.\n"
        "• Quy trình xử lý:\n"
        "  1. Kiểm tra log Cloud Logging: `resource.type=\"cloud_run_revision\" textPayload=~\"OIDC\"`.\n"
        "  2. Cập nhật biến môi trường trên Cloud Run:\n"
        "     `gcloud run services update it-helpdesk-agent --set-env-vars=\"ALLOWED_DOMAINS=company.com,newdomain.com\"`"
    )

    add_styled_heading(doc, "Sự cố 2: Lỗi Kết nối Cloud Firestore", 2)
    add_body_paragraph(
        doc,
        "• Triệu chứng: Log xuất hiện 'Firestore unavailable... Falling back to in-memory ticketing store'.\n"
        "• Tác động: Dịch vụ không bị sập (nhờ cơ chế fallback tự động), nhưng dữ liệu ticket mới sẽ lưu tạm trên RAM và mất khi container restart.\n"
        "• Quy trình xử lý:\n"
        "  1. Kiểm tra quyền IAM của Service Account: đảm bảo có quyền `roles/datastore.user`.\n"
        "  2. Kiểm tra Firestore Database đã được kích hoạt trên GCP Project hay chưa."
    )

    add_styled_heading(doc, "Sự cố 3: Vượt Hạn Ngạch Vertex AI (Rate Limit 429)", 2)
    add_body_paragraph(
        doc,
        "• Triệu chứng: Log xuất hiện mã lỗi `RESOURCE_EXHAUSTED` hoặc `429 Too Many Requests`.\n"
        "• Cơ chế tự phục hồi: Mã nguồn đã tích hợp `HttpRetryOptions(attempts=3)` với Exponential Backoff.\n"
        "• Quy trình xử lý khẩn cấp: Tăng hạn ngạch Quota 'GenerateContent requests per minute' trên Google Cloud Console Quotas & Limits."
    )

    # --- MỤC 8: DANH MỤC KIỂM TRA BẢO MẬT & BÀN GIAO VẬN HÀNH ---
    add_styled_heading(doc, "8. DANH MỤC KIỂM TRA BẢO MẬT & BÀN GIAO (GO-LIVE CHECKLIST)", 1)

    checklist_table = doc.add_table(rows=1, cols=3)
    format_custom_table(
        checklist_table,
        [2.5, 1.2, 2.8],
        ["Hạng mục Kiểm tra", "Trạng thái", "Yêu cầu Tiêu chuẩn Bắt buộc"],
        [
            ["HTTPS / SSL Termination", "BẮT BUỘC", "Cloud Run tự động kích hoạt chứng chỉ SSL/TLS được quản lý bởi Google."],
            ["Google OIDC JWKS Verification", "BẮT BUỘC", "Đảm bảo verify chữ ký RS256 trực tiếp từ accounts.google.com."],
            ["Fail-Closed Domain Whitelist", "BẮT BUỘC", "Biến ALLOWED_DOMAINS đã cấu hình chính xác danh sách domain nội bộ."],
            ["Dev Mock Token Disabled", "BẮT BUỘC", "Biến ALLOW_LOCAL_DEV_SSO phải bằng 'false' trong môi trường Production."],
            ["Secrets in Secret Manager", "BẮT BUỘC", "Tuyệt đối không lưu API keys hoặc secrets dưới dạng Plaintext trong mã nguồn."],
            ["Service Account Least Privilege", "BẮT BUỘC", "Chỉ cấp các IAM roles tối thiểu cần thiết (Vertex, BigQuery, Logging, Secrets)."],
            ["Unit Tests 100% Pass", "BẮT BUỘC", "Bộ 46 unit test cases phải vượt qua 100% trước khi bàn giao."],
        ]
    )

    add_body_paragraph(doc, "")
    add_body_paragraph(
        doc,
        "Tài liệu này là quy chuẩn vận hành chính thức và bắt buộc tuân thủ đối với toàn bộ kỹ sư DevOps, "
        "SRE và Cloud Platform tham gia quản trị hệ thống IT Helpdesk Multi-Agent AI.",
        bold_prefix="KẾT LUẬN & CAM KẾT VẬN HÀNH: "
    )

    # Save to path
    doc.save(output_path)
    print(f"Successfully generated DevOps Runbook: {output_path}")

if __name__ == "__main__":
    target_path = "/Users/luuduc/.gemini/antigravity/scratch/it-helpdesk-agent/DEVOPS_RUNBOOK.docx"
    build_runbook_docx(target_path)
