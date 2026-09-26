"""
The Auditor: an automatic agent that checks the Interpreter's output
and the generated summaries against the source filing.

Two checks, deliberately separate because they fail for different
reasons and a blended score would hide both:

  classification_check  a second model re-reads the filing blind and
                        its category is compared with the
                        Interpreter's. Yields an agreement rate.

  grounding             every factual claim in the generated summary
                        is verified against the filing text. Yields a
                        grounding rate.

Isolation, copied from the Interpreter's own isolation from the
Watcher: own package, own advisory lock ID, own management command,
own run table, additive-only migration. Nothing here imports from or
writes to watcher/services/.
"""
