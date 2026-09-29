"""Read-only physical recovery inspection through real Tk and stdio MCP."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import tempfile
import threading

from target_gui_validation import GUITransitions
from target_mcp_validation import MCPTransitions


def verify(job, policy):
    if (job['status'] != 'completed' or job['operation'] != 'target_recovery_inspect'
            or job['context']['policy_sha256'] != policy
            or job['result']['recovery_qualified'] is not False):
        raise ValueError('Recovery inspection job failed or provenance differs')
    report = job['result']
    if report['error_paths'] or any(report['watchdog_observations'][key] is not False for key in
            ('firmware_handoff_qualified', 'failed_boot_fallback_qualified')):
        raise ValueError('Incomplete inventory or unsupported recovery qualification')
    return report


def run(journal, digest, output, *, service=False, attached=False):
    journal = journal.resolve()
    if service:
        from forge_target_recovery import binding
        from forge_targets import TargetTransaction
        binding(TargetTransaction('proof', 'gui', journal, kind='service',
                                  authorization_sha256=digest))
        prepared = dict(journal=str(journal), kind='service', authorization_sha256=digest)
    else:
        if hashlib.sha256((journal/'plan.json').read_bytes()).hexdigest() != digest:
            raise ValueError('Reviewed file plan hash differs')
        prepared = dict(journal=str(journal), plan_sha256=digest)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    evidence = {'status': 'failed', 'target_mutations_requested': False,
                'transaction_kind': 'service' if service else 'files'}
    sockets = tempfile.TemporaryDirectory(prefix='uc-target-proof-')
    endpoint = Path(sockets.name)/'owner.sock'
    gui = GUITransitions(output, prepared, agent_socket=endpoint if attached else None,
                         agent_grants=('target-write',) if attached else ())
    mcp = MCPTransitions(output, prepared)
    try:
        gui.start()
        # Preserve the existing Apply/Restore control mapping for other real
        # hardware acceptance drivers, independently of new inspection controls.
        if [b.cget('text') for b in gui.panel.buttons[:2]] != ['Apply to hardware…', 'Restore hardware…']:
            raise ValueError('Physical transition button mapping changed')
        gui.panel.inspect_button.invoke()
        job_id = gui.panel.job
        if not job_id:
            raise RuntimeError('GUI inspection did not submit: ' + gui.panel.status.get())
        deadline = time.monotonic() + 60
        while gui.panel.job is not None:
            if time.monotonic() > deadline:
                raise TimeoutError('GUI inspection deadline')
            gui.root.update()
            time.sleep(0.02)
        evidence['gui_job'] = gui.app.controller.job(job_id)
        gui_report = verify(evidence['gui_job'], gui.policy_sha256)
        evidence['panel_status'] = gui.panel.status.get()
        evidence['panel_details'] = json.loads(gui.panel.details.get('1.0', 'end'))
        if evidence['panel_details'] != gui_report:
            raise ValueError('Panel did not render the shared job report')
        if attached:
            from target_recovery_client_validation import inspect
            results, failures = [], []
            def attached_client():
                try:
                    results.append(inspect(endpoint, output, prepared, gui.policy_sha256,
                                           evidence['gui_job']))
                except BaseException as exc:
                    failures.append(str(exc))
            worker = threading.Thread(target=attached_client, daemon=True)
            worker.start()
            deadline = time.monotonic() + 90
            while worker.is_alive():
                if time.monotonic() > deadline:
                    raise TimeoutError('Attached inspection client deadline')
                gui.root.update()
                worker.join(0.02)
            if failures:
                raise RuntimeError('Attached inspection failed: ' + '; '.join(failures))
            evidence['attached'] = results[0]
            job = results[0]['job']
            verify(job, gui.policy_sha256)
            if gui.app.controller.job(job['job_id']) != job:
                raise ValueError('GUI owner did not retain the attached client job')
            if job['result']['machine_id'] != gui_report['machine_id']:
                raise ValueError('Attached client inspected a different target')
        gui.close()
        mcp.start()
        submitted = mcp.call('target_recovery_inspect', {'workspace': 'target', 'transaction': 'proof'})
        deadline = time.monotonic() + 60
        while True:
            job = mcp.call('job_status', {'job_id': submitted['job_id']})
            if job['status'] in ('completed', 'failed', 'cancelled'):
                break
            if time.monotonic() > deadline:
                raise TimeoutError('MCP inspection deadline')
            time.sleep(0.05)
        evidence['mcp_job'] = job
        mcp_report = verify(job, mcp.policy_sha256)
        if gui_report['machine_id'] != mcp_report['machine_id'] or gui_report['host'] != mcp_report['host']:
            raise ValueError('GUI/MCP inspected different targets')
        mcp.close()
        evidence['mcp_exit'] = mcp.process.returncode
        evidence['status'] = 'passed'
    except BaseException as exc:
        evidence['error'] = str(exc)
        raise
    finally:
        try:
            gui.close()
            if mcp.process is not None and mcp.process.poll() is None:
                mcp.close()
        finally:
            sockets.cleanup()
            with (output/'acceptance.json').open('x') as stream:
                json.dump(evidence, stream, indent=2)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', required=True, type=Path)
    pins = parser.add_mutually_exclusive_group(required=True)
    pins.add_argument('--plan-sha256')
    pins.add_argument('--authorization-sha256')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--attached', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.journal, args.plan_sha256 or args.authorization_sha256,
                         args.output, service=args.authorization_sha256 is not None,
                         attached=args.attached), indent=2))
