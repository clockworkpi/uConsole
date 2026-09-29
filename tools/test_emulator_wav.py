"""Compile the actual pinned WAV write callback with isolated clock/type stubs.

This exercises real stdio failures, not guest USB or ALSA. No sound device or
microphone is opened. Generated test sources and recordings are disposable.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def probe(source):
    source = Path(source).read_text()
    declarations = source[source.index('typedef struct WAVState {'):
                          source.index('static size_t wav_write_out(')]
    callback = source[source.index('static size_t wav_write_out('):
                      source.index('/* VICE code:')]
    prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <errno.h>
#include <string.h>
#include <stdarg.h>
typedef struct { int unused; } Audiodev;
typedef struct { int bytes_per_frame; } PCMInfo;
typedef struct { PCMInfo info; } HWVoiceOut;
typedef struct { int unused; } RateCtl;
#define MIN(a,b) ((a) < (b) ? (a) : (b))
static int diagnostics;
static void dolog(const char *format, ...) { diagnostics++; }
static int64_t audio_rate_get_bytes(RateCtl *rate, PCMInfo *info, size_t len)
{ return len; }
'''
    harness = r'''
int main(void)
{
    char samples[16] = {0};
    WAVState state = { .f = tmpfile() };
    WAVVoiceOut voice = { .hw.info.bytes_per_frame = 4, .state = &state };
    assert(state.f);
    assert(wav_write_out(&voice.hw, samples, sizeof(samples)) == 16);
    assert(state.total_bytes == 16 && !state.failed && !diagnostics);
    assert(ftell(state.f) == 16);
    fclose(state.f);

    /* /dev/full accepts opens but rejects writes, including buffered flush. */
    state.f = fopen("/dev/full", "wb");
    assert(state.f);
    assert(wav_write_out(&voice.hw, samples, sizeof(samples)) == 0);
    assert(state.failed && state.total_bytes == 16 && diagnostics == 1);
    assert(wav_write_out(&voice.hw, samples, sizeof(samples)) == 0);
    assert(state.total_bytes == 16 && diagnostics == 1);
    fclose(state.f);

    /* Exercise the RIFF boundary without allocating a four-GiB file. */
    state = (WAVState){ .f = tmpfile(), .total_bytes = UINT32_MAX - 39 - 4 };
    assert(state.f);
    assert(wav_write_out(&voice.hw, samples, sizeof(samples)) == 4);
    assert(state.total_bytes == UINT32_MAX - 39);
    assert(wav_write_out(&voice.hw, samples, sizeof(samples)) == 0);
    assert(state.total_bytes == UINT32_MAX - 39);
    fclose(state.f);
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='uc-wav-write-') as directory:
        root = Path(directory)
        unit = root / 'write-test.c'
        unit.write_text(prefix + declarations + callback + harness)
        binary = root / 'write-test'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Wno-unused-parameter',
                        '-Wno-sign-compare', str(unit), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=10)
    print('PASS: actual WAV write callback, successful bytes, buffered ENOSPC, sticky failure and RIFF bound')


def header_failure_probe(qemu):
    commands = [{'execute': 'qmp_capabilities', 'id': 'caps'},
                {'execute': 'quit', 'id': 'quit'}]
    result = subprocess.run([
        str(qemu), '-machine', 'raspi4b', '-display', 'none', '-serial', 'none',
        '-monitor', 'none', '-S', '-qmp', 'stdio',
        '-audiodev', 'wav,id=fault,path=/dev/full',
        '-device', 'usb-audio,audiodev=fault'],
        input=''.join(json.dumps(command) + '\n' for command in commands),
        text=True, capture_output=True, timeout=20)
    if 'wav_init_out: failed to write header' not in result.stderr:
        raise AssertionError('Buffered header failure was not reported: ' + result.stderr[-2000:])
    if result.returncode:
        raise AssertionError('QEMU failed to exit cleanly after recording error: ' + result.stderr[-2000:])
    print('PASS: actual QEMU reports buffered WAV header failure and exits cleanly')


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--source', required=True, type=Path)
    cli.add_argument('--qemu', type=Path, help='Also check header failure in actual diskless QEMU')
    args = cli.parse_args()
    if not Path('/dev/full').exists():
        cli.error('This fault test requires Linux /dev/full')
    probe(args.source)
    if args.qemu is not None:
        header_failure_probe(args.qemu)
