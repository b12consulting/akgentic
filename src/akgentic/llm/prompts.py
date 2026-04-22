"""Prompt templates, rendering, and built-in prompt providers."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class PromptTemplate(BaseModel):
    """Structured prompt with template interpolation.

    Supports {placeholder} syntax for parameter substitution.
    Parameters are resolved at config time, not at runtime.

    Example:
        >>> tpl = PromptTemplate(
        ...     template="You are {role}.\\n{instructions}",
        ...     params={"role": "Architect", "instructions": "Design systems."}
        ... )
        >>> tpl.render()
        'You are Architect.\\nDesign systems.'
    """

    template: str = Field(
        default="You are a useful assistant",
        description="Template string with {placeholder} syntax",
    )
    params: dict[str, str] = Field(
        default_factory=dict,
        description="Key-value replacements for template placeholders",
    )

    def render(self) -> str:
        """Render this template to its final string form.

        Interpolates params into template using Python's str.format().

        Returns:
            str: Rendered prompt string with all placeholders replaced.

        Raises:
            KeyError: If template references a param not in params dict.

        Example:
            >>> tpl = PromptTemplate(
            ...     template="You are {role}.",
            ...     params={"role": "Architect"}
            ... )
            >>> tpl.render()
            'You are Architect.'
        """

        return self.template.format(**self.params)


def current_datetime_prompt(ctx: Any) -> str:
    """Standard datetime system prompt.

    Returns current date and time in user's timezone.
    """
    now = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M")
    return f"The current date and time is {now}."


def json_output_reminder_prompt(ctx: Any) -> str:
    """Reminder for JSON-only structured output.

    Useful for models that sometimes include explanatory text.
    """
    return (
        "\n\nCRITICAL: Return ONLY a valid JSON object."
        " Do not include explanations or markdown code blocks."
    )
