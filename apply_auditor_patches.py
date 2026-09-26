"""
Applies the two Auditor patches to an existing 8-K backend.

Run from your Django project root (the folder containing manage.py):

    python apply_auditor_patches.py

Safe to run twice: each patch checks whether it is already applied and
says so instead of duplicating anything. Every file it edits is backed
up first as <name>.bak.
"""

import os
import shutil
import sys


ROOT = os.path.dirname(os.path.abspath(__file__))
KB_MODELS = os.path.join(ROOT, "watcher", "knowledge_base", "models.py")
APP_MODELS = os.path.join(ROOT, "watcher", "models.py")
APPEND_SRC = os.path.join(ROOT, "patches", "models_append.py")

NAMES = ("AuditSample", "AuditWindow", "AuditorRun")


def fail(message):
    print(f"ERROR: {message}")
    sys.exit(1)


def backup(path):
    if not os.path.exists(path + ".bak"):
        shutil.copy2(path, path + ".bak")


def patch_kb_models():
    if not os.path.exists(KB_MODELS):
        fail(f"Not found: {KB_MODELS}\n       Run this from the folder containing manage.py.")

    source = open(KB_MODELS, encoding="utf-8").read()

    if "class AuditorRun(models.Model):" in source:
        print("a. knowledge_base/models.py ... already has the three models, skipped")
        return

    if not os.path.exists(APPEND_SRC):
        fail(f"Not found: {APPEND_SRC}\n       Copy the zip's patches/ folder next to manage.py.")

    addition = open(APPEND_SRC, encoding="utf-8").read()
    # Drop the leading "# Append verbatim ..." instruction comment.
    lines = addition.split("\n")
    while lines and (lines[0].startswith("#") or not lines[0].strip()):
        lines.pop(0)
    addition = "\n".join(lines)

    backup(KB_MODELS)
    with open(KB_MODELS, "a", encoding="utf-8") as handle:
        handle.write("\n\n" + addition.rstrip() + "\n")

    print("a. knowledge_base/models.py ... appended AuditorRun, AuditSample, AuditWindow")


def patch_app_models():
    if not os.path.exists(APP_MODELS):
        fail(f"Not found: {APP_MODELS}")

    source = open(APP_MODELS, encoding="utf-8").read()
    original = source

    anchor = "from watcher.knowledge_base.models import (\n"
    if anchor not in source:
        fail("Could not find the import block in watcher/models.py. Edit it by hand.")

    missing = [n for n in NAMES if f"    {n},\n" not in source]
    if missing:
        block = "".join(f"    {n},\n" for n in NAMES)
        source = source.replace(anchor, anchor + block, 1)

    for name in NAMES:
        if f'"{name}"' not in source:
            source = source.replace(
                '    "InterpreterRun",',
                '    "InterpreterRun",\n' + "".join(f'    "{n}",\n' for n in NAMES).rstrip("\n"),
                1,
            )
            break

    if source == original:
        print("b. watcher/models.py ............. already imports and exports them, skipped")
        return

    backup(APP_MODELS)
    open(APP_MODELS, "w", encoding="utf-8").write(source)
    print("b. watcher/models.py ............. added the three imports and __all__ entries")


def verify():
    source = open(APP_MODELS, encoding="utf-8").read()
    for name in NAMES:
        if f"    {name},\n" not in source:
            fail(f"{name} still not imported in watcher/models.py — edit it by hand.")
    kb = open(KB_MODELS, encoding="utf-8").read()
    if "class AuditorRun(models.Model):" not in kb:
        fail("AuditorRun still missing from knowledge_base/models.py.")
    print("\nBoth patches present.")
    print("Next:  python manage.py migrate")
    print("       python manage.py test watcher        (expect 402, OK)")


if __name__ == "__main__":
    print(f"Project root: {ROOT}\n")
    patch_kb_models()
    patch_app_models()
    verify()
