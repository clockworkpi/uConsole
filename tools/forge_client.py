"""Owner-created, permission-restricted client sessions on one live controller.

This is the authorization layer for local attachment, not a listener or runtime
discovery mechanism. Only the owner constructs sessions and chooses their scope.
Disconnecting a client never releases the controller or cancels accepted jobs.
"""
from pathlib import Path


READ_ONLY = frozenset(('workspace_inspect', 'host_tasks', 'target_transactions', 'recovery_jobs', 'job_history', 'job_status', 'power_query', 'audio_query', 'modem_query'))
REQUIRED = {
    'boot': ('boot', 'force-stop'), 'stop': ('boot',),
    'pause': ('boot',), 'resume': ('boot',),
    'power_set': ('device-control',), 'power_replay': ('device-control',),
    'audio_set': ('device-control',),
    'modem_set': ('device-control',),
    'modem_connection': ('device-control',),
    'keyboard_input': ('device-control',),
    'guest_exec': ('guest-exec',), 'upload': ('transfer',), 'download': ('transfer',),
    'screenshot': ('transfer',), 'host_task': ('host-task',),
    'target_transition': ('target-write',),
    'target_recovery_inspect': ('target-write',),
    'target_staging_reconcile': ('target-write',),
    'recovery_job': ('target-recovery',),
    **{name: ('image-write',) for name in (
        'checkpoint', 'restore', 'recover', 'refresh_boot', 'configure_display', 'export', 'prepare')},
}
FILE_TOOLS = {'upload': 'host_path', 'download': 'host_path', 'screenshot': 'host_path',
              'export': 'host_path', 'prepare': 'host_path', 'power_replay': 'schedule_path'}


class ClientSession:
    """Restricted facade compatible with the MCP Server controller interface.

Grants and workspace IDs are supplied by the owner, never by a wire request.
Clients can read scoped GUI/peer jobs, but cancel only jobs submitted through
this session. The owning Controller still serializes all accepted work.
"""
    def __init__(self, owner, workspace_ids, grants=(), *, files_root=None, dispatch=None):
        self.owner = owner
        self.dispatch = dispatch
        self.grants = frozenset(grants)
        if not self.grants <= owner.grants:
            raise PermissionError('Client grants exceed owner authority')
        self.workspaces = {name: owner.workspace(name) for name in workspace_ids}
        if not self.workspaces:
            raise ValueError('A client requires at least one owner-approved workspace')
        self.paths = frozenset(self.workspaces.values())
        self.files_root = Path(files_root).resolve() if files_root else None
        if self.files_root is not None:
            if owner.files_root is None or not self.files_root.is_relative_to(owner.files_root):
                raise PermissionError('Client files root exceeds owner scope')
        self.submitted = set()
        self.closed = False

    def check_open(self):
        if self.closed:
            raise ValueError('Client session is disconnected')

    def workspace(self, name):
        self.check_open()
        if name not in self.workspaces:
            raise ValueError('Workspace is not approved for this client')
        return self.workspaces[name]

    def inspect(self, name):
        self.workspace(name)
        return dict(self.owner.inspect(name), grants=sorted(self.grants), attached=True)

    def job(self, job_id):
        self.check_open()
        live = self.owner.jobs.get(job_id)
        if live:
            if self.owner.workspace(live[0]) not in self.paths:
                raise ValueError('Job is not in this client workspace scope')
            return self.owner.job(job_id)
        if self.owner.history:
            return self.owner.history.get(job_id, self.paths)
        raise ValueError('Unknown job ID')

    def host_file(self, value):
        if self.files_root is None:
            raise PermissionError('Client host file access requires an owner-approved files root')
        path = (self.files_root / value).resolve()
        if path == self.files_root or not path.is_relative_to(self.files_root):
            raise PermissionError('Host file escapes client files root')
        return str(path)

    def call(self, tool, arguments):
        self.check_open()
        # Keep this boundary safe independently of whichever transport calls it.
        from uconsole_mcp import BY_NAME, validate
        if tool not in BY_NAME:
            raise ValueError('Unknown tool')
        validate(BY_NAME[tool]['inputSchema'], arguments)
        args = dict(arguments)
        if tool == 'job_status':
            return self.job(args['job_id'])
        if tool == 'job_cancel':
            if args['job_id'] not in self.submitted:
                raise PermissionError('Clients may cancel only their own submitted jobs')
            self.job(args['job_id'])
            return self.owner.cancel(args['job_id'])
        self.workspace(args['workspace'])
        if tool not in READ_ONLY:
            required = REQUIRED.get(tool)
            if required is None or not set(required) <= self.grants:
                raise PermissionError(f'Client was not granted authority for {tool}')
        if tool == 'stop' and args.get('force') and 'force-stop' not in self.grants:
            raise PermissionError('Client was not granted force-stop')
        field = FILE_TOOLS.get(tool)
        if tool == 'boot' and 'scenario_path' in args:
            field = 'scenario_path'
        if field:
            args[field] = self.host_file(args[field])
        if tool == 'workspace_inspect':
            return self.inspect(args['workspace'])
        invoke = lambda: self.owner.call(tool, args)
        result = self.dispatch(tool, args, invoke) if self.dispatch else invoke()
        if tool == 'host_tasks':
            result = dict(result, execution_granted='host-task' in self.grants)
        if tool == 'target_transactions':
            result = dict(result, execution_granted='target-write' in self.grants)
        if tool == 'recovery_jobs':
            result = dict(result, execution_granted='target-recovery' in self.grants)
        if 'job_id' in result:
            # Retired jobs remain readable, but never confer cancellation authority.
            # Bound connection bookkeeping along with the owner's live-job cache.
            with self.owner.mutex:
                self.submitted.intersection_update(self.owner.jobs)
            self.submitted.add(result['job_id'])
        return result

    def close(self):
        # Work and runtime belong to the owner, not the transport connection.
        self.closed = True
