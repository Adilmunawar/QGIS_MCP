# Security Policy

QGIS MCP lets an AI agent act on a desktop GIS session, so security is a core design concern.

## Supported versions

| Version | Supported |
| --- | --- |
| 4.0.x | Yes |
| Earlier | No |

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's private reporting instead:

1. Go to the repository's **Security** tab.
2. Choose **Report a vulnerability** (GitHub private vulnerability reporting).

Include the plugin version, QGIS version, OS, reproduction steps and the impact you expect. You can expect an acknowledgement within a few days and a fix or mitigation plan as soon as it is assessed.

## Security model

- **Loopback only.** The plugin listener binds to `127.0.0.1` by default. Do not expose the port to a network.
- **Permission profiles.** Read only, Standard, Full and Master Control gate four risk categories (read, write, destructive, code). Commands not in the table are treated as destructive.
- **Approval queue.** Commands needing approval wait for a human decision in the QGIS panel and time out if unanswered.
- **Pairing token.** Optionally require a token via the `hello` handshake. Keep `QGIS_MCP_TOKEN` out of version control.
- **Audit log.** Every dispatched command is recorded in `qgis_mcp_audit.jsonl` in the QGIS settings directory.
- **`execute_code`** runs arbitrary PyQGIS inside QGIS and is denied or approval-gated in all but the Master Control profile. Treat it as full control of that QGIS Python environment.

## Out of scope

- Behaviour of Master Control or `execute_code` that is documented as unrestricted.
- Issues that require an attacker who already controls the local machine or the user's MCP client configuration.
- Vulnerabilities in QGIS, Qt, Python or the MCP SDK themselves (report those upstream).
