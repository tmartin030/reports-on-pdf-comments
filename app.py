"""PDF annotation report generator with a Tkinter GUI."""
from __future__ import annotations

import json
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import fitz  # PyMuPDF
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

CONFIG_PATH = Path(__file__).with_name("app_config.json")

COMMENT_TYPES = {
    "Highlight": "Highlights",
    "Underline": "Underlines",
    "Squiggly": "Underlines",
    "StrikeOut": "Underlines",
    "Text": "Comments",
}

COLOR_NAMES = {
    "yellow": (255, 255, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "red": (255, 0, 0),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
}


@dataclass
class AppOptions:
    """User-configurable options for report generation."""

    recursive: bool = True
    include_empty_sections: bool = False
    add_color_names: bool = True
    use_filename_title: bool = True
    sort_by_page: bool = True
    header_mode: str = "document"


@dataclass
class AnnotationRow:
    """Represents a single extracted annotation entry."""

    group: str
    text: str
    page: int
    x0: float
    y0: float
    subtype: str = ""
    color_hex: str = ""
    color_name: str = ""
    subject: str = ""
    comment: str = ""
    author: str = ""
    created: str = ""
    modified: str = ""
    annot_id: str = ""


def load_config() -> Dict[str, object]:
    """Load the last-used configuration from disk."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_config(data: Dict[str, object]) -> None:
    """Persist configuration to disk."""
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def sanitize_filename(name: str) -> str:
    """Replace characters invalid for Windows filenames with underscores."""
    invalid = '<>:/\\|?*"'
    sanitized = "".join("_" if char in invalid else char for char in name)
    return sanitized.strip() or "Untitled"


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    """Convert an RGB tuple to hex color string."""
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def nearest_color_name(rgb: Tuple[int, int, int], threshold: float = 100.0) -> str:
    """Return the nearest friendly color name if within threshold."""
    best_name = ""
    best_distance = float("inf")
    for name, base_rgb in COLOR_NAMES.items():
        distance = sum((channel - base) ** 2 for channel, base in zip(rgb, base_rgb)) ** 0.5
        if distance < best_distance:
            best_distance = distance
            best_name = name
    return best_name if best_distance <= threshold else ""


def scan_pdfs(folder: Path, recursive: bool) -> List[Path]:
    """Collect all PDF paths from a folder."""
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(folder.glob(pattern))


def quads_for_annot(annot: fitz.Annot) -> List[fitz.Quad]:
    """Get quads for an annotation with fallbacks across PyMuPDF versions."""
    quads: List[fitz.Quad] = []
    try:
        raw_quads = getattr(annot, "quads", None)
        if raw_quads:
            for quad in raw_quads:
                quads.append(quad if isinstance(quad, fitz.Quad) else fitz.Quad(quad))
            return quads
    except Exception:
        quads = []
    try:
        vertices = getattr(annot, "vertices", None)
        if vertices:
            for i in range(0, len(vertices), 4):
                chunk = vertices[i : i + 4]
                if len(chunk) == 4:
                    quads.append(fitz.Quad(chunk))
            if quads:
                return quads
    except Exception:
        quads = []
    try:
        rect = annot.rect
        return [fitz.Quad(rect.tl, rect.tr, rect.br, rect.bl)]
    except Exception:
        return []


def words_in_clip(page: fitz.Page, clip: fitz.Rect) -> List[Tuple[float, float, float, float, str]]:
    """Extract words within a clip rect, sorted in reading order."""
    words = page.get_text("words", clip=clip) or []
    words_sorted = sorted(words, key=lambda w: (w[1], w[0]))
    unique: Dict[Tuple[float, float, float, float, str], Tuple[float, float, float, float, str]] = {}
    for word in words_sorted:
        key = (word[0], word[1], word[2], word[3], word[4])
        unique[key] = word
    return list(unique.values())


def extract_text_from_quads(page: fitz.Page, quads: List[fitz.Quad]) -> str:
    """Extract underlying text for a list of quads."""
    fragments: List[Tuple[float, float, float, float, str]] = []
    for quad in quads:
        clip = quad.rect
        fragments.extend(words_in_clip(page, clip))
    fragments_sorted = sorted(fragments, key=lambda w: (w[1], w[0]))
    text = " ".join(word[4] for word in fragments_sorted).strip()
    return " ".join(text.split())


def annotation_color(annot: fitz.Annot) -> Tuple[str, str]:
    """Return (hex, name) for annotation color if available."""
    rgb_tuple = None
    colors = getattr(annot, "colors", None)
    if colors:
        rgb_tuple = colors.get("fill") or colors.get("stroke")
    if not rgb_tuple:
        return "", ""
    rgb = tuple(int(channel * 255) for channel in rgb_tuple)
    return rgb_to_hex(rgb), nearest_color_name(rgb)


def extract_annotations(pdf_path: Path, options: AppOptions) -> Tuple[str, List[AnnotationRow]]:
    """Extract annotations from a PDF file."""
    rows: List[AnnotationRow] = []
    with fitz.open(pdf_path) as doc:
        metadata_title = (doc.metadata or {}).get("title") or ""
        if metadata_title.strip():
            title = metadata_title.strip()
        elif options.use_filename_title:
            title = pdf_path.stem
        else:
            title = "Untitled"

        natural_index = 0
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            annot = page.first_annot
            while annot:
                annot_type = annot.type[1] if annot.type else ""
                group = COMMENT_TYPES.get(annot_type, "")
                info = annot.info or {}
                content = (info.get("content") or "").strip()
                subject = (info.get("subject") or "").strip()
                author = (info.get("title") or info.get("author") or "").strip()
                created = (info.get("creationDate") or "").strip()
                modified = (info.get("modDate") or "").strip()
                annot_id = str(getattr(annot, "id", "") or info.get("id", "") or annot.xref or "").strip()

                quads = quads_for_annot(annot)
                text = ""
                if quads:
                    text = extract_text_from_quads(page, quads)

                if not group and content:
                    group = "Comments"
                if not group:
                    annot = annot.next
                    continue

                color_hex, color_name = annotation_color(annot)
                if not options.add_color_names:
                    color_name = ""

                rect = annot.rect
                rows.append(
                    AnnotationRow(
                        group=group,
                        text=text if group != "Comments" else (content or text),
                        page=page_index + 1,
                        x0=rect.x0,
                        y0=rect.y0,
                        subtype=annot_type if group == "Underlines" else "",
                        color_hex=color_hex,
                        color_name=color_name,
                        subject=subject,
                        comment=content,
                        author=author,
                        created=created,
                        modified=modified,
                        annot_id=annot_id,
                    )
                )
                natural_index += 1
                annot = annot.next
    return title, rows


def group_by_type(rows: Iterable[AnnotationRow], sort_by_page: bool) -> Dict[str, List[AnnotationRow]]:
    """Group annotations by comment type."""
    grouped: Dict[str, List[AnnotationRow]] = {"Highlights": [], "Underlines": [], "Comments": []}
    for row in rows:
        grouped.setdefault(row.group, []).append(row)

    if sort_by_page:
        for key in grouped:
            grouped[key].sort(key=lambda r: (r.page, r.y0, r.x0))
    return grouped


def metadata_line(row: AnnotationRow) -> str:
    """Build the metadata line for a row based on its group."""
    parts = [f"page: {row.page}"]
    if row.group == "Underlines" and row.subtype:
        parts.append(f"subtype: {row.subtype}")
    if row.group in {"Highlights", "Underlines"}:
        color_value = row.color_name or row.color_hex
        parts.append(f"color: {color_value}")
    if row.group in {"Highlights", "Underlines", "Comments"}:
        parts.append(f"tag: {row.subject}")
    if row.group in {"Highlights", "Underlines", "Comments"}:
        parts.append(f"comment: {row.comment}")
    parts.append(f"author: {row.author}")
    parts.append(f"created: {row.created}")
    parts.append(f"modified: {row.modified}")
    parts.append(f"id: {row.annot_id}")
    return " ".join(part if part else "" for part in parts).replace("  ", " ").strip()


def render_markdown(doc_title: str, grouped_rows: Dict[str, List[AnnotationRow]], options: AppOptions) -> str:
    """Render grouped annotations into Markdown."""
    lines = [f"# {doc_title}"]
    for section in ["Highlights", "Underlines", "Comments"]:
        rows = grouped_rows.get(section, [])
        if not rows and not options.include_empty_sections:
            continue
        lines.append(f"## {section}")
        for row in rows:
            text = row.text.strip()
            lines.append(f"- \"{text}\"")
            lines.append(f"  - {metadata_line(row)}")
            lines.append("")
    return "\r\n".join(lines).rstrip() + "\r\n"


def process_folder(
    input_path: Path,
    output_path: Path,
    options: AppOptions,
) -> Tuple[int, int, List[str]]:
    """Process all PDFs and write Markdown reports."""
    output_path.mkdir(parents=True, exist_ok=True)
    pdfs = scan_pdfs(input_path, options.recursive)
    processed = 0
    total_annotations = 0
    errors: List[str] = []

    for pdf_path in pdfs:
        try:
            title, rows = extract_annotations(pdf_path, options)
            grouped = group_by_type(rows, options.sort_by_page)
            content = render_markdown(title, grouped, options)
            filename = sanitize_filename(title) + ".md"
            (output_path / filename).write_text(content, encoding="utf-8", newline="\r\n")
            processed += 1
            total_annotations += len(rows)
        except Exception as exc:
            errors.append(f"{pdf_path.name}: {exc}")
            continue

    return processed, total_annotations, errors


def run_gui() -> None:
    """Launch the Tkinter GUI."""
    root = tk.Tk()
    root.title("PDF Highlight Reports")
    root.geometry("600x420")

    config = load_config()

    input_var = tk.StringVar(value=config.get("input_path", ""))
    output_var = tk.StringVar(value=config.get("output_path", ""))

    recursive_var = tk.BooleanVar(value=config.get("recursive", True))
    empty_sections_var = tk.BooleanVar(value=config.get("include_empty_sections", False))
    color_names_var = tk.BooleanVar(value=config.get("add_color_names", True))
    filename_title_var = tk.BooleanVar(value=config.get("use_filename_title", True))
    sort_var = tk.StringVar(value=config.get("sort_by_page", True) and "page" or "natural")

    status_var = tk.StringVar(value="Select a PDF folder to start.")

    progress = ttk.Progressbar(root, mode="determinate")
    progress.pack(fill="x", padx=16, pady=(16, 8))

    status_label = ttk.Label(root, textvariable=status_var, wraplength=560, justify="left")
    status_label.pack(fill="x", padx=16)

    form_frame = ttk.Frame(root)
    form_frame.pack(fill="x", padx=16, pady=12)

    ttk.Label(form_frame, text="PDF Folder:").grid(row=0, column=0, sticky="w")
    input_entry = ttk.Entry(form_frame, textvariable=input_var, width=50)
    input_entry.grid(row=0, column=1, padx=(8, 8), sticky="ew")

    ttk.Label(form_frame, text="Output Folder:").grid(row=1, column=0, sticky="w", pady=(8, 0))
    output_entry = ttk.Entry(form_frame, textvariable=output_var, width=50)
    output_entry.grid(row=1, column=1, padx=(8, 8), pady=(8, 0), sticky="ew")

    form_frame.columnconfigure(1, weight=1)

    options_frame = ttk.LabelFrame(root, text="Options")
    options_frame.pack(fill="x", padx=16, pady=8)

    ttk.Checkbutton(options_frame, text="Recursive", variable=recursive_var).grid(row=0, column=0, sticky="w")
    ttk.Checkbutton(
        options_frame, text="Include empty sections", variable=empty_sections_var
    ).grid(row=0, column=1, sticky="w")
    ttk.Checkbutton(options_frame, text="Add color names", variable=color_names_var).grid(
        row=1, column=0, sticky="w"
    )
    ttk.Checkbutton(
        options_frame, text="Use filename if metadata title missing", variable=filename_title_var
    ).grid(row=1, column=1, sticky="w")

    sort_frame = ttk.Frame(options_frame)
    sort_frame.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
    ttk.Label(sort_frame, text="Sort entries:").pack(side="left")
    ttk.Radiobutton(sort_frame, text="By page", value="page", variable=sort_var).pack(side="left")
    ttk.Radiobutton(sort_frame, text="Natural order", value="natural", variable=sort_var).pack(side="left")

    button_frame = ttk.Frame(root)
    button_frame.pack(fill="x", padx=16, pady=12)

    open_output_button = ttk.Button(button_frame, text="Open Output Folder")
    open_output_button.pack(side="right")
    open_output_button.state(["disabled"])

    generate_button = ttk.Button(button_frame, text="Generate Reports")
    generate_button.pack(side="right", padx=8)

    select_pdf_button = ttk.Button(button_frame, text="Select PDF Folder")
    select_pdf_button.pack(side="left")

    select_output_button = ttk.Button(button_frame, text="Select Output Folder")
    select_output_button.pack(side="left", padx=8)

    work_queue: queue.Queue[Tuple[str, object]] = queue.Queue()

    def choose_input_folder() -> None:
        folder = filedialog.askdirectory()
        if folder:
            input_var.set(folder)
            if not output_var.get():
                output_var.set(str(Path(folder) / "Reports"))

    def choose_output_folder() -> None:
        folder = filedialog.askdirectory()
        if folder:
            output_var.set(folder)

    def open_output_folder() -> None:
        path = output_var.get()
        if not path:
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo("Output Folder", f"Reports are located at: {path}")

    def set_buttons_state(disabled: bool) -> None:
        state = ["disabled"] if disabled else ["!disabled"]
        select_pdf_button.state(state)
        select_output_button.state(state)
        generate_button.state(state)

    def worker(input_path: Path, output_path: Path, options: AppOptions) -> None:
        pdfs = scan_pdfs(input_path, options.recursive)
        work_queue.put(("total", len(pdfs)))
        processed = 0
        annotations = 0
        errors: List[str] = []
        for pdf_path in pdfs:
            try:
                title, rows = extract_annotations(pdf_path, options)
                grouped = group_by_type(rows, options.sort_by_page)
                content = render_markdown(title, grouped, options)
                filename = sanitize_filename(title) + ".md"
                (output_path / filename).write_text(content, encoding="utf-8", newline="\r\n")
                processed += 1
                annotations += len(rows)
                work_queue.put(("progress", processed, pdf_path.name))
            except Exception as exc:
                errors.append(f"{pdf_path.name}: {exc}")
                work_queue.put(("progress", processed, pdf_path.name))
                continue
        work_queue.put(("done", processed, annotations, errors, str(output_path)))

    def start_processing() -> None:
        input_path = Path(input_var.get())
        if not input_path.exists():
            messagebox.showerror("Missing Folder", "Please select a valid PDF folder.")
            return
        output_path = Path(output_var.get() or input_path / "Reports")
        output_path.mkdir(parents=True, exist_ok=True)
        output_var.set(str(output_path))

        options = AppOptions(
            recursive=recursive_var.get(),
            include_empty_sections=empty_sections_var.get(),
            add_color_names=color_names_var.get(),
            use_filename_title=filename_title_var.get(),
            sort_by_page=sort_var.get() == "page",
        )

        config_data = {
            "input_path": str(input_path),
            "output_path": str(output_path),
            "recursive": options.recursive,
            "include_empty_sections": options.include_empty_sections,
            "add_color_names": options.add_color_names,
            "use_filename_title": options.use_filename_title,
            "sort_by_page": options.sort_by_page,
        }
        save_config(config_data)

        set_buttons_state(True)
        open_output_button.state(["disabled"])
        progress["value"] = 0
        status_var.set("Processing PDFs...")

        thread = threading.Thread(target=worker, args=(input_path, output_path, options), daemon=True)
        thread.start()
        root.after(100, poll_queue)

    def poll_queue() -> None:
        try:
            while True:
                message = work_queue.get_nowait()
                if message[0] == "total":
                    progress["maximum"] = message[1]
                elif message[0] == "progress":
                    progress["value"] = message[1]
                    status_var.set(f"Processing {message[2]} ({message[1]}/{progress['maximum']})")
                elif message[0] == "done":
                    processed, annotations, errors, output_path = message[1:5]
                    set_buttons_state(False)
                    open_output_button.state(["!disabled"])
                    progress["value"] = progress["maximum"]
                    status = f"Processed {processed} PDFs with {annotations} annotations."
                    if errors:
                        status += f" Errors: {len(errors)} (see message box)."
                        messagebox.showwarning("Some files failed", "\n".join(errors))
                    status_var.set(status)
                work_queue.task_done()
        except queue.Empty:
            pass
        if generate_button.instate(["disabled"]):
            root.after(100, poll_queue)

    select_pdf_button.config(command=choose_input_folder)
    select_output_button.config(command=choose_output_folder)
    open_output_button.config(command=open_output_folder)
    generate_button.config(command=start_processing)

    root.mainloop()


if __name__ == "__main__":
    run_gui()
