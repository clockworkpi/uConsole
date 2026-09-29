"""Native package launcher/MCP and optional Tk edit/save qualification.

No guest is booted and no physical device is opened. Preserve an isolated
extraction, user data and JSON evidence under a new output directory.
"""
import argparse
import ast
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tarfile

from forge_workspace import sha256
from uconsole_mcp import PROTOCOL


def check_qemu_sources(resources):
    """Check the packaged builder's own declared inputs without executing it."""
    builder = resources/'tools/build_emulator_qemu.py'
    constants = {}
    for node in ast.parse(builder.read_text()).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and
                isinstance(node.targets[0], ast.Name) and node.targets[0].id in ('PATCHES', 'MODEL_SOURCES')):
            name = node.targets[0].id
            if name in constants:
                raise ValueError('Duplicate packaged QEMU input declaration')
            constants[name] = ast.literal_eval(node.value)
    patches, models = constants.get('PATCHES'), constants.get('MODEL_SOURCES')
    if not isinstance(patches, list) or not patches or not isinstance(models, list) or not models:
        raise ValueError('Missing packaged QEMU input declarations')
    names = []
    for name in patches:
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.patch', name):
            raise ValueError('Invalid packaged QEMU patch name')
        names.append(name)
    for entry in models:
        if (not isinstance(entry, (list, tuple)) or len(entry) != 2 or
                not isinstance(entry[0], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.[ch]', entry[0]) or
                not isinstance(entry[1], str) or not entry[1].startswith('hw/') or '..' in entry[1].split('/')):
            raise ValueError('Invalid packaged QEMU model source')
        names.append(entry[0])
    if len(names) != len(set(names)):
        raise ValueError('Duplicate packaged QEMU source filename')
    hashes = {'tools/build_emulator_qemu.py': sha256(builder)}
    for name in names:
        relative = 'Code/patch/qemu/'+name
        path = resources/relative
        if not path.resolve(strict=True).is_relative_to(resources.resolve()) or not path.is_file() or not path.stat().st_size:
            raise ValueError('Invalid or escaping packaged QEMU input: '+name)
        hashes[relative] = sha256(path)
    return hashes


def check_forge_surface(tools_reply, resources_reply):
    tools = {item['name']: item for item in tools_reply['result']['tools']}
    for name in ('modem_query', 'modem_set', 'modem_connection', 'target_recovery_inspect'):
        if name not in tools:
            raise ValueError('Installed forge tool missing: ' + name)
    properties = tools['modem_connection']['inputSchema']['properties']
    if properties.get('connected') != {'type': 'boolean'}:
        raise ValueError('Installed modem connection schema differs')
    resources = resources_reply['result']['resources']
    expected = {'forge://workspace/test/' + kind: ('text/plain' if kind == 'serial' else 'application/json')
                for kind in ('state', 'serial', 'jobs', 'tasks', 'targets')}
    if len(resources) != len(expected) or {r['uri']: r['mimeType'] for r in resources} != expected:
        raise ValueError('Installed workspace resource set differs')
    return sorted(expected)


def check_skill(payload):
    root = payload / 'share/doc/uconsole-workbench/skills/uconsole-forge'
    entry = root/'SKILL.md'
    text = entry.read_text()
    result = {'SKILL.md': sha256(entry)}
    for reference in re.findall(r'\]\((references/[^)]+)\)', text):
        path = (root/reference).resolve(strict=True)
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('Packaged skill reference escapes its directory')
        result[reference] = sha256(path)
    if len(result) == 1:
        raise ValueError('Packaged skill has no discoverable workflow references')
    return result


def check_adc_schema(reply):
    tools = {item['name']: item for item in reply['result']['tools']}
    properties = tools['power_set']['inputSchema']['properties']
    expected = {'adc_input_uv': {'type': 'integer', 'minimum': 0, 'maximum': 3300000},
                'adc_powered': {'type': 'boolean'}}
    for name, schema in expected.items():
        if properties.get(name) != schema:
            raise ValueError('Installed ADC control schema mismatch: ' + name)
    reference = tools['boot']['inputSchema']['properties'].get('adc_reference', {})
    if reference.get('type') != 'string' or reference.get('enum') != ['fixed', 'missing']:
        raise ValueError('Installed ADC boot reference schema mismatch')
    expected['boot_adc_reference'] = {'type': 'string', 'enum': ['fixed', 'missing']}
    return expected


GUI = r'''
import json, pathlib, sys, tkinter as tk
from uconsole_workbench import Workbench
root = tk.Tk()
root.withdraw()
app = None
try:
    app = Workbench(root, pathlib.Path(sys.argv[1]))
    root.update()
    if app.audio.get() != 'none': raise RuntimeError('Audio default changed')
    menu = root.nametowidget(root.cget('menu'))
    labels = [menu.entrycget(i, 'label') for i in range(menu.index('end')+1)
              if menu.type(i) == 'cascade']
    if labels != ['Help']:
        raise RuntimeError('Help must be the only top-level menu')
    app.show_wizard()
    app.setup_wizard.show(3)
    root.update()
    if app.setup_wizard.next_button.cget('text') != 'Finish':
        raise RuntimeError('Packaged setup wizard has no Finish action')
    app.setup_wizard.window.destroy()
    app.console.feed('hello\\rX\\x1b[31mR\\x1b[0m'.encode().decode('unicode_escape'))
    if app.console.get('1.0', '1.5') != 'XRllo':
        raise RuntimeError('Packaged VT terminal cursor rendering failed')
    app.console.reset_terminal()
    app.physical_target()
    if not hasattr(app.target_panel, 'host_entry'):
        raise RuntimeError('Missing SSH host configuration')
    app.target_panel.close()
    from forge_scenario import PROPERTIES
    if not {'adc_input_uv', 'adc_powered'} <= PROPERTIES.keys():
        raise RuntimeError('Missing packaged ADC controls')
    original = pathlib.Path(app.filename).read_bytes()
    proof = '// native archive edit/save proof\n'
    app.editor.insert('1.0', proof)
    app.save_file()
    if pathlib.Path(app.filename).read_bytes() != proof.encode() + original:
        raise RuntimeError('Native source edit/save mismatch')
    if not (pathlib.Path(app.filename).parent / '.forge-origin.json').is_file():
        raise RuntimeError('Missing user project provenance')
    app.show_help('Getting started')
    app.guide.query.set('machine.json')
    if 'Errors and diagnostics' not in app.guide.matches:
        raise RuntimeError('Packaged offline guide is missing troubleshooting content')
    app.diagnostics.path = pathlib.Path(sys.argv[1]).parent / 'diagnostics/application.jsonl'
    from unittest.mock import patch
    app.show_setup()
    app.setup_panel.custom_image.set(True)
    with patch('uconsole_workbench.filedialog.askopenfilename', return_value=''):
        app.guard(app.start)
        import time
        deadline = time.monotonic() + 10
        while app.setup_panel.flow_active and time.monotonic() < deadline:
            root.after(20, root.quit)
            root.mainloop()
    if 'cancelled' not in app.setup_panel.status.get():
        raise RuntimeError('Missing guest did not offer cancellable image selection')
    app.setup_panel.close()
    def diagnostic_fixture():
        raise ValueError('Diagnostic test fixture: machine.json is missing')
    app.guard(diagnostic_fixture)  # Exercise actual error UI independently of setup.
    diagnostic = json.loads(app.diagnostics.path.read_text())
    if diagnostic['action'] != 'diagnostic_fixture' or 'machine.json' not in diagnostic['message']:
        raise RuntimeError('Packaged missing-image diagnostics failed')
    root.update()
    error_window = next(child for child in root.winfo_children()
                        if isinstance(child, tk.Toplevel) and child.title().endswith('— Error'))
    pending = [error_window]
    while pending:
        widget = pending.pop()
        pending.extend(widget.winfo_children())
        if 'text' in widget.keys() and widget['text'] == 'Copy text':
            if not widget.winfo_viewable() or (widget.winfo_rooty() - error_window.winfo_rooty() +
                                               widget.winfo_height() > error_window.winfo_height()):
                raise RuntimeError('Packaged error copy button is not visible')
            widget.invoke()
            if 'machine.json' not in root.clipboard_get():
                raise RuntimeError('Packaged error clipboard action failed')
            break
    else:
        raise RuntimeError('Packaged error copy button is missing')
    user_source = str(app.filename)
    app.show_schematic()
    from workbench_schematic import COMPONENTS
    from uconsole_emulator import ROOT
    for component in COMPONENTS:
        for source in component.sources:
            source.resolve(ROOT)
        app.schematic.select(component.id)
        if app.filename is not None or app.editor.edit_modified():
            raise RuntimeError('Packaged schematic source was not opened as an inspection copy')
    app.schematic.select('keyboard')
    sheet = app.schematic.open_sheet()
    sheet.page.current(1)
    sheet.change_page()
    sheet.query.set('GD32F103Rx')
    sheet.find()
    root.update()
    if not sheet.canvas.find_withtag('search'):
        raise RuntimeError('Packaged schematic sheet cannot find keyboard MCU')
    sheet.window.destroy()
    from workbench_schematic_trace import Recording
    from unittest.mock import patch
    trace = Recording('native-package-replay-fixture', 0.)
    trace.append(dict(identity='native-package-replay-fixture', component='keyboard', state='active',
                      time=0., source='Explicit native-package test fixture', detail='Replay UI only'))
    trace_path = pathlib.Path(sys.argv[1]).parent / 'schematic-fixture.json'
    trace.save(trace_path)
    with patch('workbench_schematic_gui.filedialog.askopenfilename', return_value=str(trace_path)):
        app.schematic.load_replay()
    app.schematic.window.after_cancel(app.schematic.timer)
    app.schematic.tick()
    root.update()
    if not app.schematic.mode.get().startswith('REPLAY'):
        raise RuntimeError('Packaged replay is not visibly distinguished from live observations')
    app.schematic.close()
    print(json.dumps({'status':'passed', 'python':sys.version.split()[0],
                      'tk':root.tk.call('info', 'patchlevel'), 'user_source':user_source,
                      'schematic_sources': True, 'schematic_sheets': True, 'schematic_replay': True,
                      'offline_help': True, 'structured_errors': True, 'copy_error_text': True}))
finally:
    if app is not None:
        app.close()
    else:
        root.destroy()
'''


def run(archive, output, *, gui=False, preferred_python=None):
    archive, output = Path(archive).resolve(), Path(output).resolve()
    digest = sha256(archive)
    output.mkdir(mode=0o700)
    record = {'status': 'failed', 'archive_sha256': digest, 'platform': sys.platform}
    try:
        with tarfile.open(archive) as stream:
            stream.extractall(output, filter='data')
        payload = output / 'uconsole-workbench'
        resources = payload / 'libexec/uconsole-workbench'
        adc_assets = ['Code/patch/qemu/adc101c.c', 'Code/patch/qemu/adc101c-core.h',
                      'Code/patch/qemu/uconsole-adc101c.patch',
                      'Code/patch/qemu/bcm2835-i2c-nack-completion.patch',
                      'Code/patch/qemu/axp2xx-i2c-migration.patch',
                      'Code/patch/qemu/adc101c-reference-profile.patch']
        record['adc_assets'] = {name: sha256(resources / name) for name in adc_assets}
        record['qemu_build_inputs'] = check_qemu_sources(resources)
        from workbench_emulator import selected
        emulator = selected(resources, output / 'empty-build-data')
        if emulator != resources / 'emulator/bin':
            raise ValueError('Package does not contain a matching standalone emulator')
        record['bundled_emulator'] = {}
        for binary, arguments in [('qemu-system-aarch64', ['-machine', 'help']), ('qemu-img', ['--version'])]:
            result = subprocess.run([str(emulator / binary), *arguments], cwd=output,
                                    capture_output=True, text=True, timeout=30)
            record['bundled_emulator'][binary] = dict(exit_code=result.returncode,
                                                     stdout=result.stdout, stderr=result.stderr)
            if result.returncode or (binary == 'qemu-system-aarch64' and 'raspi4b' not in result.stdout):
                raise ValueError('Bundled emulator failed: ' + binary)
        record['agent_skill'] = check_skill(payload)
        env = dict(os.environ, XDG_DATA_HOME=str(output / 'user-data'))
        if preferred_python is not None:
            env['PYTHON'] = preferred_python
        for name in ('uconsole-workbench', 'uconsole-mcp'):
            result = subprocess.run([str(payload / 'bin' / name), '--help'], cwd=output,
                                    env=env, text=True, capture_output=True, timeout=30)
            record[name] = {'exit_code':result.returncode, 'stdout':result.stdout, 'stderr':result.stderr}
            if result.returncode or 'usage:' not in result.stdout:
                raise ValueError('Installed launcher failed: ' + name)
        request = {'jsonrpc':'2.0', 'id':1, 'method':'initialize', 'params':{
            'protocolVersion':PROTOCOL, 'capabilities':{},
            'clientInfo':{'name':'native-archive-acceptance', 'version':'1'}}}
        mcp = subprocess.run([str(payload / 'bin/uconsole-mcp'), '--workspace', 'test=' + str(output / 'guest')],
            cwd=output, env=env, text=True, capture_output=True, timeout=30,
            input='\n'.join(json.dumps(message) for message in (
                request, {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'resources/list'})) + '\n')
        record['mcp'] = {'exit_code':mcp.returncode, 'stdout':mcp.stdout, 'stderr':mcp.stderr}
        replies = [json.loads(line) for line in mcp.stdout.splitlines()]
        if mcp.returncode or len(replies) != 3 or replies[0]['result']['serverInfo']['name'] != 'uconsole-forge':
            raise ValueError('Installed MCP initialization failed')
        if replies[1].get('id') != 2:
            raise ValueError('Installed MCP tool list reply mismatch')
        record['adc_schema'] = check_adc_schema(replies[1])
        if replies[2].get('id') != 3:
            raise ValueError('Installed MCP resources reply mismatch')
        record['forge_resources'] = check_forge_surface(replies[1], replies[2])
        if gui:
            resources = payload / 'libexec/uconsole-workbench'
            bundled = resources / 'Code/uconsole_keyboard/uconsole_keyboard.ino'
            original = bundled.read_bytes()
            gui_env = dict(env, PYTHONPATH=str(resources / 'tools'), UCONSOLE_ROOT=str(resources),
                           UCONSOLE_BUILD_DIR=str(output / 'user-data/uconsole-workbench'))
            command = [sys.executable, '-c', GUI, str(output / 'guest')]
            if sys.platform == 'darwin':
                # Suppress Tk's saved-window recovery prompt for this isolated
                # acceptance process only; do not alter the user's defaults.
                command += ['-ApplePersistenceIgnoreState', 'YES']
            result = subprocess.run(command,
                cwd=output, env=gui_env, capture_output=True, text=True, timeout=30)
            record['gui'] = {'exit_code':result.returncode, 'stdout':result.stdout, 'stderr':result.stderr}
            if result.returncode or json.loads(result.stdout)['status'] != 'passed':
                raise ValueError('Native packaged Tk edit/save failed')
            if bundled.read_bytes() != original:
                raise ValueError('Native GUI changed bundled source')
        record['status'] = 'passed'
    except BaseException as exc:
        record['error'] = str(exc)
        raise
    finally:
        (output / 'native-archive-acceptance.json').write_text(json.dumps(record, indent=2) + '\n')
        print('Native archive: ' + record['status'], flush=True)
    return record


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--archive', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--gui', action='store_true')
    cli.add_argument('--preferred-python', help='Exercise launcher fallback from this candidate')
    args = cli.parse_args()
    run(args.archive, args.output, gui=args.gui, preferred_python=args.preferred_python)
