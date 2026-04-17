Create a new skill file with custom instructions.

Skills are markdown files that provide domain-specific knowledge, guidelines, or specialized workflows to the AI agent. They are automatically loaded into system reminders when available.

## When to Use

- Create project-specific coding standards (e.g., "python-conventions", "react-guidelines")
- Define reusable expertise (e.g., "security-audit", "performance-optimization")
- Capture team workflows (e.g., "code-review-process", "deployment-checklist")

## Parameters

- **name**: Skill identifier (alphanumeric, hyphens, underscores). Used to load the skill later.
- **content**: Full markdown content with title and detailed instructions.
- **scope**: 
  - `"project"`: Save in `.opencode/skills/` (local to current project)
  - `"global"`: Save in `~/.opencode/skills/` (available across all projects)

## Example Usage

```
create_skill(
    name="python-expert",
    content="""# Python Expert
    
You are an expert Python developer who follows best practices.

## Code Style
- Use type hints for all function signatures
- Follow PEP 8 conventions
- Write comprehensive docstrings

## Best Practices
- Prefer list comprehensions over loops
- Use context managers for resource handling
- Write tests before implementation
""",
    scope="global"
)
```

## After Creation

Once created, use the skill by calling:
```
skill(name="python-expert")
```

The skill will be automatically included in system reminders, providing context to the AI agent for subsequent interactions.
