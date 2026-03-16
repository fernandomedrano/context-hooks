---
description: Cross-reference all memory layers to find stale rules, undocumented patterns, knowledge gaps, and emerging file pairs
---

Run the memory cross-reference report for the current project.

```bash
context-hooks xref
```

Analyze the output and present findings organized by priority:
- **Priority 1**: Bug knowledge gaps, stale knowledge entries, emerging parallel paths
- **Priority 2**: Rules without evidence, undocumented patterns
- **Priority 3**: Layer overlap, well-supported rules

After presenting, ask the user which findings they want to action.
