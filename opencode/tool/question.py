"""Question tool — ask the user a question. Equivalent to src/tool/question.ts."""
from __future__ import annotations

from typing import Any

from opencode.tool.base import ToolContext, ToolInfo, ToolResult


class QuestionTool(ToolInfo):
    id = "question"
    description = (
        "Ask the user a question to get clarification or additional information. "
        "Use this when you need user input to proceed."
    )

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask the user"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of choices for the user",
                },
            },
            "required": ["question"],
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        question = args["question"]
        options = args.get("options", [])

        # In headless/API mode, this returns the question as output
        # The client is responsible for showing it to the user and sending the reply
        output = question
        if options:
            output += "\n\nOptions:\n" + "\n".join(f"  {i+1}. {o}" for i, o in enumerate(options))

        return ToolResult(
            title="Question",
            output=output,
            metadata={"question": question, "options": options, "awaiting_response": True},
        )


tool = QuestionTool()
