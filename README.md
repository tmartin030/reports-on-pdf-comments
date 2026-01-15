# PDF Highlight Reports

A Windows-friendly desktop app that scans PDFs in a folder and generates Markdown reports of annotations grouped by Highlights, Underlines, and Comments.

## Features

- Tkinter GUI with folder pickers and progress updates.
- Recursive scanning (optional).
- Markdown output per PDF with annotations grouped by type.
- Options for color naming, empty sections, and sorting.
- Background processing keeps the UI responsive.

## Usage

1. Run the app:

   ```powershell
   python app.py
   ```

2. In the GUI:
   - Select a PDF folder.
   - Optionally select an output folder (defaults to `Reports` in the chosen folder).
   - Adjust options as desired.
   - Click **Generate Reports**.
   - Use **Open Output Folder** when done.

Reports are saved as Markdown files (UTF-8, Windows line endings) with filenames based on each document title.

## Build a Windows .exe with PyInstaller

1. Install dependencies:

   ```powershell
   pip install pymupdf pyinstaller
   ```

2. Build the executable:

   ```powershell
   pyinstaller --noconfirm --onefile --windowed --name "PDF Highlight Reports" app.py
   ```

3. The executable will be created at:

   ```text
   dist\PDF Highlight Reports.exe
   ```

Double-click the `.exe` to run the GUI (no console window due to `--windowed`).
