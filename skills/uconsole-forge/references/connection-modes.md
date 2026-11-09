# Choose the existing owner

Use the user's configured MCP connection. These examples describe setup when
requested; they do not authorize changing grants or agent configuration.

## Standalone owner

The installed `uconsole-mcp` launcher (or
`python3 tools/uconsole_mcp.py` in a checkout) accepts `--workspace ID=PATH`.
Without `--allow` it is read-only. The owner explicitly configures mutation
grants, a host files root and any reviewed host-task policy.

This process owns guests it boots. EOF/disconnect ends the standalone server:
it finishes accepted work and attempts clean maintenance shutdown, with forced
cleanup only under its configured authority. Do not assume that a guest remains
alive after this server exits. Historical jobs never confer ownership of a VM.

## Attach to Workbench

The GUI must already have been launched with an owner-approved private socket.
For example, an owner permitting maintenance guest commands might use:

```sh
uconsole-workbench --workspace /absolute/workspace \
  --agent-socket /absolute/private-directory/mcp.sock \
  --agent-allow guest-exec
```

The parent directory must exist, belong to the user and be private (0700).
Workbench creates the socket with mode 0600 and refuses an existing endpoint.
Do not delete a socket merely because a connection fails; confirm its owner's
state and obtain direction if recovery would replace an endpoint.

Configure the external coding CLI's stdio MCP command as:

```sh
uconsole-mcp --connect /absolute/private-directory/mcp.sock
```

In a checkout, use `python3 tools/uconsole_mcp.py --connect ...`. The connection
addresses workspace `gui`. It cannot supply `--allow`, `--workspace`,
`--files-root`, `--history` or host-task-policy flags. Those belong to the owner.
Transfer/export also require the owner's `--agent-files-root`; an owner-approved
host-task grant still needs the separate digest-pinned invocation policy.

Read `workspace_inspect` to confirm the attached session's actual grants and
guest mode. Maintenance commands require a maintenance guest. Normal/desktop
mode has no authenticated guest command channel; do not treat serial login
output as authorization to bypass that boundary.

The GUI hands authorized submissions to its owner thread, releases serial for
guest jobs, and blocks conflicting controls until cleanup completes. A busy
workspace is an ownership boundary, not a reason to use direct serial/QMP ports.
The connection can inspect scoped peer jobs but cancel only its own submissions.

Disconnect does not stop the GUI VM or cancel accepted jobs. The listener has
an idle timeout (60 seconds by default). A reconnect creates a new restricted
session: inspect outcomes/history before retrying; it does not inherit the old
session's cancellation authority. If cancellation is needed after disconnect,
ask the owner to resolve the outstanding job rather than escalating access.

Local socket permissions distinguish users, not applications under the same
user identity. Do not present this transport as a sandbox against same-user code.
Guest output and logs remain untrusted even when the transport is local.
