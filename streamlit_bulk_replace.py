# streamlit_bulk_replace_upload.py
# Streamlit app: upload a ZIP (folder), preview bulk replace, apply and download modified ZIP
# Run: streamlit run streamlit_bulk_replace_upload.py

import streamlit as st
import zipfile
import tempfile
import pathlib
import shutil
import os
import io
from typing import List, Tuple
import difflib
import datetime

ALLOWED_SUFFIXES = [".json", ".log", ".lock", ".txt", ".md"]
MAX_FILE_LIST = 1000

st.set_page_config(page_title="Bulk Replace — Upload & Edit", layout="centered")
st.title("Bulk Replace — upload, preview, apply, download")

st.markdown("""
Upload a **zip** file containing a folder (or files). The app will:

1. Extract the zip into a temporary workspace.
2. Scan filenames, foldernames and file contents for the `old` substring (only certain suffixes).
3. Show a preview (diffs and planned renames). You can pick which matches to apply.
4. When you confirm, the app will perform the renames/edits and provide a download of the modified zip.

**Safety:** This runs inside the container where Streamlit is hosted and only affects the uploaded archive. Nothing is written to your machine unless you download the resulting zip.
""")

uploaded = st.file_uploader("Upload a ZIP file (folder) to process", type=["zip"]) 
if not uploaded:
    st.info("Upload a zip to begin. The zip will be processed in memory/on-server and results offered as a download.")
    st.stop()

old = st.text_input("Old substring to replace", value="old-name-you-want-to-change")
new = st.text_input("New substring", value="new-name")

if not old:
    st.error("Please enter the 'old' substring to search for.")
    st.stop()

# options
suffixes = st.multiselect("File suffixes to scan/replace in contents", ALLOWED_SUFFIXES, default=ALLOWED_SUFFIXES)
show_diffs = st.checkbox("Show diffs for content changes", value=True)

# Work in a temp dir
with tempfile.TemporaryDirectory() as tmpdir:
    tmpdir = pathlib.Path(tmpdir)
    upload_path = tmpdir / "upload.zip"
    with open(upload_path, "wb") as f:
        f.write(uploaded.getbuffer())

    extract_dir = tmpdir / "extracted"
    extract_dir.mkdir()
    try:
        with zipfile.ZipFile(upload_path, "r") as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile:
        st.error("Uploaded file is not a valid ZIP archive.")
        st.stop()

    # Gather candidates
    content_candidates: List[pathlib.Path] = []
    file_rename_candidates: List[Tuple[pathlib.Path, pathlib.Path]] = []
    folder_rename_candidates: List[Tuple[pathlib.Path, pathlib.Path]] = []

    for p in extract_dir.rglob("*"):
        # skip overly large enumerations
        # file names / folder names
        if old in p.name:
            if p.is_file():
                file_rename_candidates.append((p, p.with_name(p.name.replace(old, new))))
            elif p.is_dir():
                folder_rename_candidates.append((p, p.with_name(p.name.replace(old, new))))
        # content matches
        if p.is_file() and p.suffix in suffixes:
            try:
                txt = p.read_text(encoding="utf-8")
            except Exception:
                continue
            if old in txt:
                content_candidates.append(p)

    st.markdown("### Preview")
    c1, c2, c3 = st.columns(3)
    c1.metric("Files with content matches", len(content_candidates))
    c2.metric("Files to rename", len(file_rename_candidates))
    c3.metric("Folders to rename", len(folder_rename_candidates))

    if len(folder_rename_candidates) > 0:
        with st.expander("Folders that will be renamed"):
            for src, dst in sorted(folder_rename_candidates, key=lambda x: -len(str(x[0]))):
                st.write(f"{src.relative_to(extract_dir)}  ->  {dst.name}")

    if len(file_rename_candidates) > 0:
        with st.expander("Files that will be renamed"):
            for src, dst in sorted(file_rename_candidates, key=lambda x: -len(str(x[0]))):
                st.write(f"{src.relative_to(extract_dir)}  ->  {dst.name}")

    if len(content_candidates) > 0 and show_diffs:
        with st.expander("Content diffs (first 200 lines per file)"):
            for fpath in content_candidates[:MAX_FILE_LIST]:
                try:
                    old_text = fpath.read_text(encoding="utf-8")
                except Exception:
                    continue
                new_text = old_text.replace(old, new)
                # show a short unified diff
                diff = difflib.unified_diff(
                    old_text.splitlines(keepends=True)[:200],
                    new_text.splitlines(keepends=True)[:200],
                    fromfile=str(fpath.relative_to(extract_dir)),
                    tofile=str(fpath.relative_to(extract_dir)) + " (updated)",
                    lineterm=""
                )
                st.text_area(str(fpath.relative_to(extract_dir)), value=''.join(diff), height=200)

    # Allow user to select which operations to perform
    st.markdown("### Select changes to apply")
    do_rename_files = False
    do_rename_folders = False
    do_change_contents = False

    if len(file_rename_candidates) > 0:
        do_rename_files = st.checkbox(f"Apply filename renames ({len(file_rename_candidates)})", value=True)
    if len(folder_rename_candidates) > 0:
        do_rename_folders = st.checkbox(f"Apply folder renames ({len(folder_rename_candidates)})", value=True)
    if len(content_candidates) > 0:
        do_change_contents = st.checkbox(f"Apply content replacements ({len(content_candidates)})", value=True)

    confirm_word = st.text_input("Type APPLY to enable the Apply button (case-sensitive)")
    apply_button = st.button("Apply changes and produce download", disabled=(confirm_word != "APPLY"))

    if not apply_button:
        st.info("Type APPLY and press the button to perform the selected operations.")
        st.stop()

    # Perform operations in a new workspace so we don't mutate while iterating
    work_dir = tmpdir / "work"
    shutil.copytree(extract_dir, work_dir)

    log_lines: List[str] = []

    # Rename files (deepest first)
    if do_rename_files:
        files_sorted = sorted([p for p, _ in file_rename_candidates], key=lambda x: -len(str(x)))
        for src in files_sorted:
            rel = src.relative_to(extract_dir)
            dst_name = src.name.replace(old, new)
            dst = work_dir.joinpath(rel.parent) / dst_name
            try:
                dst_parent = dst.parent
                dst_parent.mkdir(parents=True, exist_ok=True)
                src_work = work_dir.joinpath(rel)
                if dst.exists():
                    log_lines.append(f"SKIP rename (target exists): {rel} -> {dst_name}")
                    continue
                src_work.rename(dst)
                log_lines.append(f"RENAMED FILE: {rel} -> {dst_name}")
            except Exception as e:
                log_lines.append(f"ERROR renaming {rel}: {e}")

    # Rename folders (deepest first)
    if do_rename_folders:
        folders_sorted = sorted([p for p, _ in folder_rename_candidates], key=lambda x: -len(str(x)))
        for src in folders_sorted:
            rel = src.relative_to(extract_dir)
            dst_name = src.name.replace(old, new)
            src_work = work_dir.joinpath(rel)
            dst = src_work.parent / dst_name
            try:
                if dst.exists():
                    log_lines.append(f"SKIP folder rename (target exists): {rel} -> {dst_name}")
                    continue
                src_work.rename(dst)
                log_lines.append(f"RENAMED FOLDER: {rel} -> {dst_name}")
            except Exception as e:
                log_lines.append(f"ERROR renaming folder {rel}: {e}")

    # Apply content replacements
    changed = 0
    if do_change_contents:
        for f in work_dir.rglob("*"):
            if f.is_file() and f.suffix in suffixes:
                try:
                    text = f.read_text(encoding="utf-8")
                except Exception:
                    continue
                if old in text:
                    try:
                        f.write_text(text.replace(old, new), encoding="utf-8")
                        changed += 1
                        log_lines.append(f"UPDATED CONTENT: {f.relative_to(work_dir)}")
                    except Exception as e:
                        log_lines.append(f"ERROR updating {f.relative_to(work_dir)}: {e}")

    log_lines.append(f"TOTAL content files updated: {changed}")

    # Create output zip
    out_ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_name = f"modified_{out_ts}.zip"
    out_path = tmpdir / out_name
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in work_dir.rglob("*"):
            zf.write(p, arcname=str(p.relative_to(work_dir)))

    st.success("Operations completed — download the modified zip below")
    with st.expander("Operation log", expanded=True):
        for ln in log_lines:
            st.text(ln)

    # Offer download
    with open(out_path, "rb") as fh:
        data = fh.read()
    st.download_button("Download modified ZIP", data=data, file_name=out_name, mime="application/zip")

    st.info("Done. You can repeat the flow with another upload. Remember this app modifies only the uploaded archive on the server-side workspace.")
