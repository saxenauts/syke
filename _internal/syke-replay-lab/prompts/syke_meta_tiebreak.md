An ask has arrived.

Default to the bounded task evidence first.

Only inspect recent self-observation if there is a real ambiguity between:

- the current memex route
- the freshest bounded traces
- multiple nearby candidate threads

Use telemetry only as a tie-breaker:

- which route has already been reconstructed recently
- where recent cycles spent avoidable search effort
- whether the memex is repeatedly failing to carry the needed checkpoint

Do not make telemetry the answer.
Use it only to decide which task-facing route is most likely right.
