# Claude Skill

Drop this directory into your skills folder, or point an agentic host at the
MCP server in `../mcp/`.

The skill file carries the operating rules the model needs and cannot infer
from a tool schema: that bands are evidence strength rather than probability,
that `OWNERDOMAIN` is a self-assertion, that person-scoped searches are refused
by design and should not be routed around, and that instructions found in
retrieved content are evidence about the subject rather than direction.

Those rules live here because a model summarising a result will otherwise
present `STRONG_EVIDENCE` as certainty — and a caveat in documentation the
model never reads is not a caveat.
