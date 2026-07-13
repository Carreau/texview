# Instructions for the reviewing agent

The canonical, self-contained prompt to give a reviewing agent (one
that may not have access to this repo) lives in
[`texreview/instructions.md`](texreview/instructions.md) and is shipped
inside the installed package. Print it, followed by the machine-readable
JSON Schema, with:

```
tex-review instruct            # full prompt + schema
tex-review instruct --schema   # JSON Schema only (texreview/schema.json)
```

Copy-paste that output into the agent's prompt.
