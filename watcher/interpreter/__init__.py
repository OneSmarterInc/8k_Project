"""
Interpreter package (guide Parts 4-6).

Phase 1: taxonomy.py       - the frozen category list (G2/G3 depend on it)
Phase 2: filing_text.py    - filing text for labellers (and later the model)
         labelling.py      - blind, stratified labelling queue

Nothing in this package is imported by the Watcher's run path. The
Watcher's job is timestamps; nothing here can slow or fail a capture run.
"""
