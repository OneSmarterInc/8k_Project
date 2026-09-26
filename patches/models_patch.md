# Two edits to existing files

## 1. `watcher/knowledge_base/models.py` — append at end of file

Three models: `AuditorRun`, `AuditSample`, `AuditWindow`. Full source in `models_append.py`.
Nothing existing is modified; the migration is three `CreateModel` calls and no `AlterField`.

## 2. `watcher/models.py` — two additions

Import block:

```python
from watcher.knowledge_base.models import (
    AuditSample,
    AuditWindow,
    AuditorRun,
    AutomationRun,
    ...
```

`__all__`:

```python
    "InterpreterRun",
    "AuditSample",
    "AuditWindow",
    "AuditorRun",
```
