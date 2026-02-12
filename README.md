# PDF Cutter ✂️

A lightweight, desktop PDF viewer and page-range extractor built with Python, PyQt6, and PyMuPDF.

> **Note:** This project was vibe-coded with [GitHub Copilot](https://github.com/features/copilot) (Claude) — from initial scaffolding to final polish.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![GUI](https://img.shields.io/badge/GUI-PyQt6-orange)

---

## What It Does

PDF Cutter lets you open any PDF, browse through it, and **extract a page range into a new file** — for example, pulling pages 50–100 out of a large document. The output file gets a sensible default name like `report_pages_50-100.pdf`.

## Features

| Feature | Details |
|---|---|
| **Open PDF** | File dialog, toolbar button, or drag-and-drop |
| **View & Scroll** | Pages rendered vertically with smooth scrolling |
| **Zoom** | `Ctrl+Scroll`, `Ctrl+=`/`Ctrl+-`, toolbar buttons, or type any % |
| **Fit Modes** | Fit Width / Fit Page |
| **Bookmarks (TOC)** | Sidebar shows the PDF's built-in Table of Contents — click to jump |
| **Custom Bookmarks** | Add your own bookmarks for the current session |
| **Page Navigation** | Page spinner in toolbar; current page auto-updates on scroll |
| **Text Search** | Search bar with yellow hit highlighting; jumps to first match |
| **Text Selection** | Click-and-drag to select a region; text is copied to clipboard |
| **Export / Trim** | `Ctrl+E` — pick a page range, choose output path, done |

## Installation

### Prerequisites

- **Python 3.10+** (check with `python --version`)
- **Git** (optional, for cloning)

### Steps

1. **Clone the repository** (or download the ZIP):

   ```bash
   git clone https://github.com/your-username/pdf_cutter.git
   cd pdf_cutter
   ```

2. **Create a virtual environment:**

   ```bash
   python -m venv .venv
   ```

3. **Activate the virtual environment:**

   - **Windows (PowerShell):**
     ```powershell
     .venv\Scripts\Activate
     ```
   - **Windows (cmd):**
     ```cmd
     .venv\Scripts\activate.bat
     ```
   - **macOS / Linux:**
     ```bash
     source .venv/bin/activate
     ```

4. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

### Running

```bash
python pdf_cutter.py
```

Or open a PDF directly from the command line:

```bash
python pdf_cutter.py "C:\path\to\your\document.pdf"
```

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+O` | Open PDF |
| `Ctrl+E` | Export page range |
| `Ctrl+=` / `Ctrl+Scroll Up` | Zoom in |
| `Ctrl+-` / `Ctrl+Scroll Down` | Zoom out |
| `Ctrl+0` | Fit width |
| `Ctrl+F` | Focus search bar |
| `Ctrl+Q` | Quit |

## Tech Stack

- **[PyMuPDF](https://pymupdf.readthedocs.io/)** — Fast PDF parsing, rendering, text extraction, and page-range export
- **[PyQt6](https://www.riverbankcomputing.com/software/pyqt/)** — Cross-platform desktop GUI framework

## Project Structure

```
pdf_cutter/
├── .venv/                # Virtual environment (not committed)
├── pdf_cutter.py         # Main application (single file)
├── requirements.txt      # Python dependencies
├── LICENSE               # MIT License
└── README.md             # This file
```

## License

This project is licensed under the [MIT License](LICENSE).
