# Evals

Blue-team assurance write-ups. Each attack is understood so a defense can be
derived; the deliverable is the control a deployer can act on, not the break.
The reporting discipline (byte-identical reruns, a measured interval, honest
bounded nulls, falsified layers) is fixed across the set, starting from EVAL-0001.

| id | target | status | headline measured result |
|---|---|---|---|
| [EVAL-0001](EVAL-0001-mcp-tool-poisoning-assurance.md) | Agent Breaker OmniChat Desktop (`mcp_chat_poisoning`), a public teaching system | measured, published | Input guard bypass 0.75 (15/20, 95% CI [0.51, 0.91]); outbound/DLP filter falsified as absent; verbatim exfil session-conditional (0.10-0.60) |
| [EVAL-0002](EVAL-0002-approval-bypass-prereg.md) | DeepSeek Harness `interaction/` approval gate, local build | pre-registered, not yet fired | None yet - predictions and pass bars locked before the run; results tables fill after fires land |
| EVAL-0003 | Target-agnostic: IPI via tool-return content | planned / in progress (authored separately) | Pending |
