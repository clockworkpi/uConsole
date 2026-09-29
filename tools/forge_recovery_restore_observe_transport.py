"""Bounded physical observation exchange; journal ownership belongs to caller."""
from forge_recovery_commit_protocol import exact
from forge_recovery_restore_bootstrap import argv, observation_payload, start
from forge_recovery_restore_observe_worker import checked
from forge_recovery_restore_process import run
from forge_recovery_restore_protocol import header


def capture(probe, request, errors, *, record=None):
    if record is not None and not callable(record):
        raise ValueError('Expected durable restore observation recorder')
    def retain(kind,value):
        if record is not None: record(kind,value)
    request = checked(request)
    data, pin = observation_payload(request)
    command = argv(probe,pin,observation=True)
    retain('prepared',dict(packet_sha256=pin,query=request['query'],action=request['action']))
    def operation(channel, half_close, wait_success):
        start(channel, data, pin)
        retain('ready',dict(packet_sha256=pin))
        half_close()
        reply = header(channel)
        retain('reply',reply)
        expected = dict(type='restore-observation', query=request['query'],
            action=request['action'], plan_sha256=request['pin'],
            attempt=request['attempt'], boot_id=request['observed_boot_id'])
        if (not isinstance(reply, dict) or 'result' not in reply or
                not isinstance(reply['result'], dict) or
                not exact({key:value for key,value in reply.items() if key!='result'}, expected)):
            raise ValueError('Restore observation response binding differs')
        if channel.read(1): raise ValueError('Trailing restore observation output')
        wait_success()
        retain('exited',dict(stdout_closed=True,process_exit_code=0))
        return reply['result']
    return run(command, operation, errors,
               timeout=180, idle_timeout=150)
