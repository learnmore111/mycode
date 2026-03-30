"""Question tool — ask the user a question. Equivalent to src/tool/question.ts."""
from __future__ import annotations

from pydantic import BaseModel, Field

from opencode.tool.base import CallableTool, ToolContext, ToolOk, ToolResult


class QuestionParams(BaseModel):
    """Parameters for the question tool."""
    question: str = Field(description="The question to ask the user")
    options: list[str] = Field(default_factory=list, description="Optional list of choices for the user")


class QuestionTool(CallableTool[QuestionParams]):
    id = "question"
    description = (
        "Ask the user a question to get clarification or additional information. "
        "Use this when you need user input to proceed."
    )

    async def call(self, params: QuestionParams, ctx: ToolContext) -> ToolResult:
        question = params.question
        options = params.options

        # In headless/API mode, this returns the question as output
        # The client is responsible for showing it to the user and sending the reply
        output = question
        if options:
            output += "\n\nOptions:\n" + "\n".join(f"  {i+1}. {o}" for i, o in enumerate(options))

        return ToolOk(
            output,
            title="Question",
            metadata={"question": question, "options": options, "awaiting_response": True},
        )


tool = QuestionTool()
