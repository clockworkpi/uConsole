#!/usr/bin/env python3
"""Boot an attached-forge export through a small overlay and verify its app.

Uses the preceding attachment record, preserving the exported raw image as a
read-only backing file. This is boot-back evidence, not physical qualification
or a test of the normal importer copying a second full base image.
"""
import argparse
import json
from pathlib import Path
import re
import threading

from forge_filesystem import check_overlay_root
from forge_runtime import Runtime
from forge_workspace import sha256
from uconsole_emulator import parser, wait_for_log
from validate_desktop_preparation_gui import fixture


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--record', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    options = cli.parse_args()
    previous = json.loads(options.record.read_text())
    image = options.record.resolve().parent / 'files/enhanced.img'
    application = previous['application_path']
    if not re.fullmatch(r'/usr/local/bin/attached-forge-[0-9a-f]{32}', application):
        raise ValueError('Not an attached application fixture path')
    app_hash, image_hash = previous['application_sha256'], previous['export_sha256']
    if any(not re.fullmatch(r'[0-9a-f]{64}', value) for value in (app_hash, image_hash)):
        raise ValueError('Invalid fixture digest')
    if previous.get('validation') != 'passed' or sha256(image) != image_hash:
        raise ValueError('Export record or image checksum is not valid')
    output = options.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    fixture(image, output, image_hash)
    evidence = {'validation': 'failed', 'image': str(image), 'image_sha256': image_hash,
                'application': application, 'forced_cleanup': False, 'physical_boot': 'unverified'}
    runtime = Runtime(parser().parse_args(['--workspace', str(output), 'run', '--mode', 'maintenance']))
    try:
        runtime.start()
        evidence['runtime_identity'] = runtime.identity
        wait_for_log(output / 'serial.log', b'root@(none):/#', runtime.process, 120)
        result = runtime.execute(f'test "$(sha256sum {application} | cut -d" " -f1)" = {app_hash} && {application}',
                                 timeout=60, cancel=threading.Event())
        evidence['application_test'] = result
        if result['exit_code'] != 0 or result['stdout'] != Path(application).name + '\n':
            raise ValueError('Exported application digest/execution did not match')
        runtime.stop()
        if runtime.process.returncode != 0:
            raise ValueError('Export boot-back did not shut down successfully')
        evidence['root_after_stop'] = check_overlay_root(output, runtime.args.qemu_img)
        if sha256(image) != image_hash:
            raise ValueError('Export raw image changed during boot-back')
        evidence['export_unchanged'] = True
        evidence['validation'] = 'passed'
    except BaseException as exc:
        evidence['error'] = str(exc)
        raise
    finally:
        try:
            if runtime.process is not None and runtime.process.poll() is None:
                try:
                    runtime.stop()
                except Exception as cleanup_error:
                    evidence['clean_cleanup_error'] = str(cleanup_error)
                    evidence['forced_cleanup'] = True
                    runtime.stop(force=True)
        finally:
            (output / 'acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
            print(f'Export application boot-back: {evidence["validation"]}; {output / "acceptance.json"}')


if __name__ == '__main__':
    main()
