# streamlit_bulk_replace.py
# Streamlit UI for bulk_full_replace.py functionality
# Run locally with: streamlit run streamlit_bulk_replace.py

import streamlit as st
import pathlib
import sys
import shutil
import zipfile
import datetime
import os
from typing import List

# ---- Config -----
ALLOWED_SUFFIXES = [".json", ".log", ".lock"]

# ---- Utility functions -----

def find_content_candidates(root: pathlib.Path, old: str) -> List[pathlib.Path]:
    candidates = []
    for f in root.rglob("*"):
        if f.is_file() and f.suffix in ALLOWED_SUFFIXES:
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            if old in text:
                candidates.append(f)
    return candidates


def find_file_candidates(root: pathlib.Path, old: str) -> List[pathlib.Path]:
    return [f for f in root.rglob("*") if f.is_file() and old in f.name]


def find_folder_candidates(root: pathlib.Path, old: str) -> List[pathlib.Path]:
    return [d for d in root.rglob("*") if d.is_dir() and old in d.name]


def make_backup(root: pathlib.Path) -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    zipname = f"backup_{root.name}_{ts}.zip"
    with zipfile.ZipFile(zipname, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in root.rglob("*"):
            # write relative path
            zf.write(f, arcname=str(f.relative_to(root.parent)))
    return os.path.abspath(zipname)


# ---- App UI -----
st.set_page_config(page_title="Bulk Replace (safe)", layout="centered")
st.title("Bulk Replace — Files, Folders and Contents")
st.markdown(
    """
This app performs a **dry-run** by default and shows exactly what will change.

**How to run:** host locally and point `Root folder` to a path accessible by the server. This app performs real file I/O when you click **Apply changes** — use the backup option.
"""
)

with st.form(key="inputs"):
    root_input = st.text_input("Root folder (absolute or relative)", value="tasks")
    old = st.text_input("Old substring to replace", value="old-name-you-want-to-change")
    new = st.text_input("New substring", value="new-name")
    do_backup = st.checkbox("Create zip backup before applying", value=True)
    apply_changes = st.checkbox("Apply changes (uncheck = dry run)", value=False)
    extra_confirm = st.text_input("Type the word APPLY to enable destructive actions (case-sensitive)", value="")
    submitted = st.form_submit_button("Scan")

if not submitted:
    st.info("Fill inputs and press **Scan** to preview changes.")
    st.stop()

# Validate root
root = pathlib.Path(root_input)
if not root.exists():
    st.error(f"Root path not found: {root}")
    st.stop()
if not root.is_dir():
    st.error("Root must be a directory")
    st.stop()

if apply_changes and extra_confirm != "APPLY":
    st.warning("To actually apply changes you must type APPLY exactly in the confirmation box.")
    apply_changes = False

st.write(f"**Mode:** {'APPLY' if apply_changes else 'DRY RUN'}")
st.write(f"Root: {root.resolve()}\nOld: '{old}' -> New: '{new}'")

# Scan
with st.spinner("Scanning files..."):
    content_candidates = find_content_candidates(root, old)
    file_candidates = find_file_candidates(root, old)
    folder_candidates = find_folder_candidates(root, old)

st.markdown("### Preview")
col1, col2, col3 = st.columns(3)
col1.metric("Files with content matches", len(content_candidates))
col2.metric("Files with names to rename", len(file_candidates))
col3.metric("Folders to rename", len(folder_candidates))

if len(content_candidates) > 0:
    with st.expander("Files that contain the old substring (content)"):
        for f in content_candidates:
            st.write(f)

if len(file_candidates) > 0:
    with st.expander("Files with the old substring in the filename"):
        for f in sorted(file_candidates, key=lambda x: -len(str(x))):
            st.write(f)

if len(folder_candidates) > 0:
    with st.expander("Folders with the old substring in the foldername"):
        for d in sorted(folder_candidates, key=lambda x: -len(str(x))):
            st.write(d)

# If dry run only, offer example rename preview
if not apply_changes:
    st.success("Dry run complete. Review the previews above. Check Apply changes and confirm to perform the operations.")
    st.stop()

# At this point user chose to apply changes and typed APPLY
# Backup
backup_path = None
if do_backup:
    try:
        with st.spinner("Creating backup (this may take time)..."):
            backup_path = make_backup(root)
        st.success(f"Backup created: {backup_path}")
    except Exception as e:
        st.error(f"Backup failed: {e}")
        st.stop()

# Apply changes
log_lines = []
# Step 1: rename files (deepest first)
file_candidates_sorted = sorted(file_candidates, key=lambda x: -len(str(x)))
for f in file_candidates_sorted:
    new_name = f.name.replace(old, new)
    new_path = f.parent / new_name
    entry = f"RENAME FILE: {f} -> {new_path}"
    if new_path.exists():
        entry += f"  SKIP (target exists)"
        log_lines.append(entry)
        continue
    try:
        f.rename(new_path)
        log_lines.append(entry + "  OK")
    except Exception as e:
        log_lines.append(entry + f"  ERROR: {e}")

# Step 2: rename folders (deepest first)
folder_candidates_sorted = sorted(folder_candidates, key=lambda x: -len(str(x)))
for d in folder_candidates_sorted:
    new_name = d.name.replace(old, new)
    new_path = d.parent / new_name
    entry = f"RENAME FOLDER: {d} -> {new_path}"
    if new_path.exists():
        entry += f"  SKIP (target exists)"
        log_lines.append(entry)
        continue
    try:
        d.rename(new_path)
        log_lines.append(entry + "  OK")
    except Exception as e:
        log_lines.append(entry + f"  ERROR: {e}")

# Step 3: replace contents
changed = 0
for f in root.rglob("*"):
    if f.is_file() and f.suffix in ALLOWED_SUFFIXES:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        if old in text:
            try:
                f.write_text(text.replace(old, new), encoding="utf-8")
                log_lines.append(f"UPDATED CONTENT: {f}")
                changed += 1
            except Exception as e:
                log_lines.append(f"ERROR updating {f}: {e}")

log_lines.append(f"TOTAL content files updated: {changed}")

st.success("Apply completed — see log below")
with st.expander("Operation log", expanded=True):
    for line in log_lines:
        st.text(line)

if backup_path:
    st.download_button("Download backup zip", data=open(backup_path, "rb"), file_name=os.path.basename(backup_path))

st.info("Finished. Make sure your repository is clean and verify results. This tool performs irreversible filesystem operations.")
