# Skills directory

Each subfolder is a single skill. Structure per skill:

```
<skill-name>/
├── SKILL.md              # Frontmatter + methodology
├── helpers/              # Optional helper scripts
│   └── *.py
└── outputs/              # Skill outputs (created at runtime)
    └── *.md / *.json
```

Skills are built autonomously via the scheduled task `skill-builder-investment-tool`. Build progress tracked in `../skill_build_state.json`. Build queue defined in `../skill_build_plan.json` (immutable).

Once all 13 skills are built and smoke-tested, this folder will be packaged into a `.plugin` for Claude Code portability.
