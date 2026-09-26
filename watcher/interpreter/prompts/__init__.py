"""Versioned Interpreter prompts. One file per version; never edit a
released version in place - add a new file and bump the import below.

1.0.0  first release
1.0.1  confidence covers the whole answer; routine answers must carry a
       real confidence (1.0.0 returned 0.00 for every routine filing)
"""

from .v1_0_1 import PROMPT_VERSION, build_prompt  # noqa: F401
