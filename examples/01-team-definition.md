# Team Definition -- TeamCard & TeamCardMember Hierarchies

## Concepts Covered

- **AgentCard as role descriptor**: Each agent in a team is described by an `AgentCard` containing its role, skills, config, and routing rules. The card is a blueprint -- not a running instance.

- **TeamCardMember tree structure**: `TeamCardMember` wraps an `AgentCard` with a `headcount` (how many instances to spawn) and a recursive `members` list (subordinates). This forms a tree describing the team hierarchy.

- **TeamCard as team blueprint**: A `TeamCard` holds the team's `name`, `description`, an `entry_point` (the agent receiving external messages), top-level `members`, and `message_types`. It is a pure data model -- no actors are created until `TeamFactory.build()` is called.

- **`agent_cards` property for O(1) lookup**: `TeamCard.agent_cards` walks the entire member tree and returns a flat `dict[str, AgentCard]` keyed by `config.name`. This gives constant-time access to any agent's card by name.

- **`supervisors` property for hierarchy discovery**: `TeamCard.supervisors` returns the `AgentCard` for every `TeamCardMember` whose `.members` list is non-empty. This identifies which agents manage subordinates.

- **Pydantic serialization round-trip**: `TeamCard` (and all nested models) are Pydantic models. `model_dump()` produces a plain dict; `model_validate()` reconstructs the full object graph. This enables persistence and transport.

## Key API Patterns

```python
# Nesting members to form a hierarchy
analyst_member = TeamCardMember(card=analyst_card)
researcher_member = TeamCardMember(card=researcher_card, members=[analyst_member])

# Building the TeamCard -- entry_point is separate from members
team_card = TeamCard(
    name="research-team",
    description="...",
    entry_point=manager_member,
    members=[researcher_member, reviewer_member],
    message_types=[],
)

# Property-based inspection
all_cards = team_card.agent_cards      # dict[str, AgentCard]
supervisors = team_card.supervisors    # list[AgentCard]
```

## Common Pitfalls

- **Duplicate config names raise ValueError**: Every `AgentCard` in the tree must have a unique `config.name`. The `agent_cards` property enforces this and raises `ValueError` on duplicates.

- **`entry_point` is NOT in `members`**: The `entry_point` is a separate field on `TeamCard`. The `members` list contains only the top-level members that are not the entry point. Both are walked by `agent_cards` and `supervisors`.

- **`message_types` is a list of classes, not instances**: Pass message class references (e.g., `[UserMessage]`), not message objects.

## See Also

- [Example 02: Team Factory](./02-team-factory.md) -- building a running team from the card defined here.
