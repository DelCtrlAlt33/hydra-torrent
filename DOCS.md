# Where the overview doc lives

The full overview + "how to run it" reference for this project is at:
**`C:\Users\Matth\project-docs$n.pdf`**  (repo: DelCtrlAlt33/project-docs)

## Refresh it when this project changes
1. Ask Claude: "re-read this project and update its entry in project-docs\projects.py"
2. `cd C:\Users\Matth\project-docs && python generate_docs.py`

The PDF is generated from `projects.py` - the generator only reformats, it does
not re-read code, so the entry must be updated from a fresh read to stay true.
