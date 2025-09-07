# 📦 Repository Structure

```
pdf-master-pro/
├─ backend/
│  ├─ app.py
│  ├─ requirements.txt
│  ├─ Procfile
│  ├─ Dockerfile
│  └─ render.yaml
└─ frontend/
   └─ website.html
```
```
# backend/app.py
import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime

from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename

import fitz  # PyMuPDF
from pdf2docx import Converter
from PyPDF2 import PdfReader, PdfWriter
import pikepdf
import img2pdf
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch

app = Flask(__name__)
CORS(app)

ALLOWED_PDF = {"application/pdf", ".pdf"}
ALLOWED_DOC = {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"}
ALLOWED_IMG = {"image/jpeg", ".jpg", ".jpeg"}


def _is_type(file_storage, allowed):
    filename = file_storage.filename.lower()
    ctype = file_storage.mimetype.lower() if file_storage.mimetype else ""
    return any(x in filename for x in allowed) or any(x in ctype for x in allowed)


def _send_bytes(byte_data: bytes, filename: str, mimetype: str):
    return send_file(io.BytesIO(byte_data), as_attachment=True, download_name=filename, mimetype=mimetype)


@app.route("/api/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.route("/api/merge", methods=["POST"])  # files[] -> merged.pdf
def merge_pdf():
    files = request.files.getlist("files")
    if not files:
        return ("No files uploaded", 400)
    writer = PdfWriter()
    for f in files:
        if not _is_type(f, ALLOWED_PDF):
            return (f"Invalid file type: {f.filename}", 400)
        reader = PdfReader(f)
        for p in reader.pages:
            writer.add_page(p)
    out = io.BytesIO()
    writer.write(out)
    writer.close()
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="merged.pdf", mimetype="application/pdf")


@app.route("/api/split", methods=["POST"])  # file + pages (e.g., 1-3,5)
def split_pdf():
    f = request.files.get("file")
    pages = request.form.get("pages", "").strip()
    if not f or not _is_type(f, ALLOWED_PDF):
        return ("PDF required", 400)
    reader = PdfReader(f)

    def parse_ranges(spec, maxn):
        sel = set()
        for part in spec.replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                a = int(a); b = int(b)
                for i in range(max(1, a), min(maxn, b) + 1):
                    sel.add(i)
            else:
                sel.add(int(part))
        return sorted(i for i in sel if 1 <= i <= maxn)

    selected = parse_ranges(pages or f"1-{len(reader.pages)}", len(reader.pages))
    writer = PdfWriter()
    for i in selected:
        writer.add_page(reader.pages[i - 1])
    out = io.BytesIO()
    writer.write(out)
    writer.close()
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="split.pdf", mimetype="application/pdf")


@app.route("/api/compress", methods=["POST"])  # file + level (screen|ebook|printer)
def compress_pdf():
    f = request.files.get("file")
    preset = request.form.get("level", "ebook")
    if not f or not _is_type(f, ALLOWED_PDF):
        return ("PDF required", 400)

    # Try Ghostscript if available for stronger compression
    try:
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, secure_filename(f.filename) or "input.pdf")
            outp = os.path.join(td, "compressed.pdf")
            f.save(inp)
            gs_preset = {"screen": "/screen", "ebook": "/ebook", "printer": "/printer"}.get(preset, "/ebook")
            cmd = [
                "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
                f"-dPDFSETTINGS={gs_preset}", "-dNOPAUSE", "-dQUIET", "-dBATCH",
                f"-sOutputFile={outp}", inp
            ]
            subprocess.check_call(cmd)
            with open(outp, "rb") as fh:
                data = fh.read()
            return _send_bytes(data, "compressed.pdf", "application/pdf")
    except Exception:
        pass

    # Fallback using pikepdf optimization
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "in.pdf")
        outp = os.path.join(td, "out.pdf")
        f.save(inp)
        with pikepdf.open(inp) as pdf:
            pdf.save(outp, optimize_version=True)
        with open(outp, "rb") as fh:
            data = fh.read()
        return _send_bytes(data, "compressed.pdf", "application/pdf")


@app.route("/api/pdf-to-word", methods=["POST"])  # file -> .docx
def pdf_to_word():
    f = request.files.get("file")
    if not f or not _is_type(f, ALLOWED_PDF):
        return ("PDF required", 400)
    with tempfile.TemporaryDirectory() as td:
        pdf_path = os.path.join(td, "input.pdf")
        docx_path = os.path.join(td, "converted.docx")
        f.save(pdf_path)
        cv = Converter(pdf_path)
        cv.convert(docx_path, start=0, end=None)
        cv.close()
        return send_file(docx_path, as_attachment=True, download_name="converted.docx")


@app.route("/api/word-to-pdf", methods=["POST"])  # .docx -> .pdf via LibreOffice headless
def word_to_pdf():
    f = request.files.get("file")
    if not f or not _is_type(f, ALLOWED_DOC):
        return ("DOCX required", 400)
    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, secure_filename(f.filename) or "input.docx")
        out_dir = td
        f.save(in_path)
        try:
            subprocess.check_call(["soffice", "--headless", "--convert-to", "pdf", "--outdir", out_dir, in_path])
        except FileNotFoundError:
            return ("LibreOffice not available on server. Use Dockerfile provided.", 500)
        base = os.path.splitext(os.path.basename(in_path))[0] + ".pdf"
        out_path = os.path.join(out_dir, base)
        return send_file(out_path, as_attachment=True, download_name="converted.pdf")


@app.route("/api/pdf-to-jpg", methods=["POST"])  # file -> zip of jpgs (dpi optional)
def pdf_to_jpg():
    f = request.files.get("file")
    dpi = int(request.form.get("dpi", 150))
    if not f or not _is_type(f, ALLOWED_PDF):
        return ("PDF required", 400)
    with tempfile.TemporaryDirectory() as td:
        pdf_path = os.path.join(td, "in.pdf")
        f.save(pdf_path)
        doc = fitz.open(pdf_path)
        img_paths = []
        for i, page in enumerate(doc):
            zoom = dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            outp = os.path.join(td, f"page_{i+1}.jpg")
            pix.save(outp)
            img_paths.append(outp)
        # Zip them
        zip_bytes = io.BytesIO()
        with zipfile.ZipFile(zip_bytes, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in img_paths:
                zf.write(p, arcname=os.path.basename(p))
        zip_bytes.seek(0)
        return send_file(zip_bytes, as_attachment=True, download_name="images.zip", mimetype="application/zip")


@app.route("/api/jpg-to-pdf", methods=["POST"])  # files[] -> single pdf
def jpg_to_pdf():
    files = request.files.getlist("files")
    if not files:
        return ("No images uploaded", 400)
    with tempfile.TemporaryDirectory() as td:
        image_paths = []
        for f in files:
            if not _is_type(f, ALLOWED_IMG):
                return (f"Invalid image: {f.filename}", 400)
            p = os.path.join(td, secure_filename(f.filename) or f"img_{len(image_paths)}.jpg")
            f.save(p)
            image_paths.append(p)
        pdf_bytes = img2pdf.convert(image_paths)
        return _send_bytes(pdf_bytes, "converted.pdf", "application/pdf")


@app.route("/api/protect", methods=["POST"])  # file + password
def protect_pdf():
    f = request.files.get("file")
    pwd = request.form.get("password", "").strip()
    if not f or not _is_type(f, ALLOWED_PDF) or not pwd:
        return ("PDF and password required", 400)
    reader = PdfReader(f)
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    writer.encrypt(pwd)
    out = io.BytesIO()
    writer.write(out)
    writer.close()
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="protected.pdf", mimetype="application/pdf")


@app.route("/api/unlock", methods=["POST"])  # file + password
def unlock_pdf():
    f = request.files.get("file")
    pwd = request.form.get("password", "").strip()
    if not f or not _is_type(f, ALLOWED_PDF) or not pwd:
        return ("PDF and password required", 400)
    reader = PdfReader(f)
    if reader.is_encrypted:
        if not reader.decrypt(pwd):
            return ("Incorrect password", 401)
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    out = io.BytesIO()
    writer.write(out)
    writer.close()
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="unlocked.pdf", mimetype="application/pdf")


@app.route("/api/page-number", methods=["POST"])  # file -> add numbers bottom-right
def add_page_numbers():
    f = request.files.get("file")
    if not f or not _is_type(f, ALLOWED_PDF):
        return ("PDF required", 400)
    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, "in.pdf")
        f.save(in_path)
        reader = PdfReader(in_path)
        # Create a temp PDF with numbers per page and merge
        numbered_paths = []
        for i in range(len(reader.pages)):
            num_pdf = os.path.join(td, f"num_{i}.pdf")
            c = canvas.Canvas(num_pdf, pagesize=letter)
            w, h = letter
            c.setFont("Helvetica", 10)
            c.drawString(w - 0.8*inch, 0.5*inch, str(i + 1))
            c.showPage()
            c.save()
            numbered_paths.append(num_pdf)
        # Merge overlays (sizes assume letter; for varied sizes this is a simple implementation)
        writer = PdfWriter()
        for idx, page in enumerate(reader.pages):
            # Read stamp
            stamp_reader = PdfReader(numbered_paths[idx])
            page.merge_page(stamp_reader.pages[0])
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        writer.close()
        out.seek(0)
        return send_file(out, as_attachment=True, download_name="numbered.pdf", mimetype="application/pdf")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
```
```
# backend/requirements.txt
Flask==3.0.3
flask-cors==4.0.1
PyMuPDF==1.24.10
pdf2docx==0.5.8
PyPDF2==3.0.1
pikepdf==9.2.1
img2pdf==0.5.1
reportlab==4.2.5
gunicorn==22.0.0
```
```
# backend/Procfile
web: gunicorn app:app
```
```
# backend/Dockerfile
FROM python:3.11-slim

# System deps (LibreOffice for DOCX→PDF, Ghostscript for compression, fonts)
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libreoffice-common libreoffice-writer \
    ghostscript fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PYTHONUNBUFFERED=1
ENV PORT=10000
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--workers", "2"]
```
```
# backend/render.yaml
services:
  - type: web
    name: pdf-master-pro-api
    env: docker
    plan: free
    dockerfilePath: ./Dockerfile
    autoDeploy: true
```
```
<!-- frontend/website.html (UPDATED to call the API) -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PDF Master Pro - All-in-One PDF Tools</title>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <script src="https://unpkg.com/react@18/umd/react.development.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
  <style>
    /* keep your original styles (omitted here for brevity) */
  </style>
</head>
<body>
  <div id="root"></div>
  <script type="text/babel">
    const { useState, useMemo } = React;

    // 🔧 Change this to your Render API URL after deploy
    const API_BASE = 'https://YOUR-RENDER-SERVICE.onrender.com';

    function apiEndpointFor(tool, extra={}) {
      switch (tool.title) {
        case 'Merge PDF': return `${API_BASE}/api/merge`;
        case 'Split PDF': return `${API_BASE}/api/split`;
        case 'Compress PDF': return `${API_BASE}/api/compress`;
        case 'PDF to Word': return `${API_BASE}/api/pdf-to-word`;
        case 'Word to PDF': return `${API_BASE}/api/word-to-pdf`;
        case 'PDF to JPG': return `${API_BASE}/api/pdf-to-jpg`;
        case 'JPG to PDF': return `${API_BASE}/api/jpg-to-pdf`;
        case 'Protect PDF': return `${API_BASE}/api/protect`;
        case 'Unlock PDF': return `${API_BASE}/api/unlock`;
        case 'Page Number': return `${API_BASE}/api/page-number`;
        default: return null;
      }
    }

    function App() {
      const [activeTool, setActiveTool] = useState(null);
      const [showModal, setShowModal] = useState(false);

      const tools = [
        { id: 1, icon: 'fa-file-pdf', title: 'Merge PDF', description: 'Combine multiple PDFs into one' },
        { id: 2, icon: 'fa-cut', title: 'Split PDF', description: 'Extract selected pages' },
        { id: 3, icon: 'fa-compress-arrows-alt', title: 'Compress PDF', description: 'Reduce file size' },
        { id: 4, icon: 'fa-exchange-alt', title: 'PDF to Word', description: 'Convert PDF to DOCX' },
        { id: 5, icon: 'fa-exchange-alt', title: 'Word to PDF', description: 'Convert DOCX to PDF' },
        { id: 6, icon: 'fa-file-image', title: 'PDF to JPG', description: 'Pages as JPG images' },
        { id: 7, icon: 'fa-file-image', title: 'JPG to PDF', description: 'Images to single PDF' },
        { id: 8, icon: 'fa-lock', title: 'Protect PDF', description: 'Add password' },
        { id: 9, icon: 'fa-unlock', title: 'Unlock PDF', description: 'Remove password' },
        { id: 10, icon: 'fa-sort-numeric-down', title: 'Page Number', description: 'Add page numbers' },
      ];

      const handleToolClick = (tool) => { setActiveTool(tool); setShowModal(true); };
      const closeModal = () => { setShowModal(false); setActiveTool(null); };

      return (
        <div>
          {/* Keep your existing Header/Hero/Features/etc. */}
          <Tools tools={tools} onToolClick={handleToolClick} />
          <ToolModal show={showModal} tool={activeTool} onClose={closeModal} />
        </div>
      );
    }

    function Tools({ tools, onToolClick }) {
      return (
        <section id="tools" className="container">
          <div className="tools-grid">
            {tools.map(t => (
              <div key={t.id} className="tool-card">
                <div className="tool-icon"><i className={`fas ${t.icon}`}></i></div>
                <div className="tool-content">
                  <h3>{t.title}</h3>
                  <p>{t.description}</p>
                  <button className="tool-button" onClick={() => onToolClick(t)}>Use Tool</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      );
    }

    function ToolModal({ show, tool, onClose }) {
      const [files, setFiles] = useState([]);
      const [pages, setPages] = useState('');
      const [level, setLevel] = useState('ebook');
      const [password, setPassword] = useState('');
      const [dpi, setDpi] = useState(150);
      const [busy, setBusy] = useState(false);

      if (!show || !tool) return null;

      const isMulti = tool.title === 'Merge PDF' || tool.title === 'JPG to PDF';
      const accept = tool.title === 'JPG to PDF' ? 'image/jpeg' : (tool.title.includes('Word') ? '.docx' : 'application/pdf');

      const handleFiles = (e) => setFiles(Array.from(e.target.files || []));

      const handleProcess = async () => {
        if (!files.length) return alert('Select file(s)');
        setBusy(true);
        try {
          const fd = new FormData();
          if (tool.title === 'Merge PDF' || tool.title === 'JPG to PDF') {
            files.forEach(f => fd.append('files', f));
          } else {
            fd.append('file', files[0]);
          }
          if (tool.title === 'Split PDF') fd.append('pages', pages || '');
          if (tool.title === 'Compress PDF') fd.append('level', level);
          if (tool.title === 'Protect PDF' || tool.title === 'Unlock PDF') fd.append('password', password);
          if (tool.title === 'PDF to JPG') fd.append('dpi', String(dpi));

          const endpoint = apiEndpointFor(tool);
          const res = await fetch(endpoint, { method: 'POST', body: fd });
          if (!res.ok) {
            const msg = await res.text();
            throw new Error(msg || 'Request failed');
          }
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          const ext = (tool.title === 'PDF to JPG') ? 'zip' : (tool.title === 'PDF to Word' ? 'docx' : 'pdf');
          a.href = url;
          a.download = `${tool.title.replace(/\s+/g,'_').toLowerCase()}.${ext}`;
          document.body.appendChild(a); a.click(); a.remove();
          URL.revokeObjectURL(url);
        } catch (e) {
          alert(e.message);
        } finally {
          setBusy(false);
        }
      };

      return (
        <div className="modal-overlay active">
          <div className="modal">
            <button className="modal-close" onClick={onClose}><i className="fas fa-times"></i></button>
            <div className="modal-header"><h2>{tool.title}</h2><p>{tool.description}</p></div>
            <div className="modal-content">
              <input type="file" accept={accept} multiple={isMulti} onChange={handleFiles} />

              {tool.title === 'Split PDF' && (
                <div style={{marginTop:10}}>
                  <label>Pages (e.g., 1-3,5): </label>
                  <input placeholder="1-3,5" value={pages} onChange={e=>setPages(e.target.value)} />
                </div>
              )}
              {tool.title === 'Compress PDF' && (
                <div style={{marginTop:10}}>
                  <label>Quality: </label>
                  <select value={level} onChange={e=>setLevel(e.target.value)}>
                    <option value="screen">High compression</option>
                    <option value="ebook">Balanced</option>
                    <option value="printer">Better quality</option>
                  </select>
                </div>
              )}
              {(tool.title === 'Protect PDF' || tool.title === 'Unlock PDF') && (
                <div style={{marginTop:10}}>
                  <label>Password: </label>
                  <input type="password" value={password} onChange={e=>setPassword(e.target.value)} />
                </div>
              )}
              {tool.title === 'PDF to JPG' && (
                <div style={{marginTop:10}}>
                  <label>DPI: </label>
                  <input type="number" min="72" max="300" value={dpi} onChange={e=>setDpi(e.target.value)} />
                </div>
              )}
            </div>
            <div className="modal-actions">
              <button className="modal-button primary" onClick={handleProcess} disabled={busy}>{busy ? 'Processing…' : 'Process'}</button>
              <button className="modal-button secondary" onClick={onClose} disabled={busy}>Cancel</button>
            </div>
          </div>
        </div>
      );
    }

    ReactDOM.createRoot(document.getElementById('root')).render(<App />);
  </script>
</body>
</html>
