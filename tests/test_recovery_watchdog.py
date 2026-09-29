from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from forge_recovery_watchdog import SOURCE


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('cc'), 'Linux C compiler required')
class RecoveryWatchdogTests(unittest.TestCase):
    def test_compiles_and_bounded_core_fails_closed(self):
        harness = r'''
#include <assert.h>
struct fake { double time; int pings, ready, fail_ping, fail_ready, fail_wait, reverse;
              double extension; int renewals; };
static double now(void *p) { return ((struct fake *)p)->time; }
static int ping(void *p) { struct fake *f=p; f->pings++; return f->fail_ping; }
static int ready(void *p) { struct fake *f=p; f->ready++; return f->fail_ready; }
static int wait_step(void *p) {
    struct fake *f=p; f->time += f->reverse ? -2 : 2; return f->fail_wait;
}
static double renew(void *p) {
    struct fake *f=p;
    f->renewals++;
    return f->extension;
}
static double renew_once(void *p) {
    struct fake *f=p;
    f->renewals++;
    return f->time == 4 ? 6 : 0;
}
int main(void) {
    const struct keeper_ops ops={now,ping,ready,wait_step,NULL};
    const struct keeper_ops renewable={now,ping,ready,wait_step,renew};
    const struct keeper_ops one_renewal={now,ping,ready,wait_step,renew_once};
    struct fake f={0};
    assert(keep(&f,&ops,6)==1 && f.pings==3 && f.ready==1 && f.time==6);
    f=(struct fake){.fail_ping=1};
    assert(keep(&f,&ops,6)==1 && f.pings==1 && f.ready==0);
    f=(struct fake){.fail_ready=1};
    assert(keep(&f,&ops,6)==1 && f.pings==1 && f.ready==1 && f.time==0);
    f=(struct fake){.fail_wait=1};
    assert(keep(&f,&ops,6)==1 && f.pings==1);
    f=(struct fake){.reverse=1};
    assert(keep(&f,&ops,6)==1 && f.pings==1);
    f=(struct fake){0};
    assert(keep(&f,&ops,301)==1 && f.pings==0 && f.ready==0);
    f=(struct fake){.time=-1};
    assert(keep(&f,&ops,6)==1 && f.pings==0);
    f=(struct fake){0};
    assert(keep(&f,&one_renewal,6)==1 && f.time==10 && f.pings==5 && f.ready==1);
    /* At t=6 an unrenewed lease is already dead; callback is not consulted. */
    f=(struct fake){0};
    assert(keep(&f,&renewable,6)==1 && f.time==6 && f.renewals==3);
    f=(struct fake){.extension=300};
    assert(keep(&f,&renewable,6)==1 && f.time==86400 && f.ready==1 && f.pings==43200);
    f=(struct fake){.extension=301};
    assert(keep(&f,&renewable,6)==1 && f.pings==0);
    f=(struct fake){.extension=-1};
    assert(keep(&f,&renewable,6)==1 && f.pings==0);
    f=(struct fake){.extension=NAN};
    assert(keep(&f,&renewable,6)==1 && f.pings==0);
    f=(struct fake){.time=NAN};
    assert(keep(&f,&ops,6)==1 && f.pings==0);
    f=(struct fake){0};
    assert(keep(&f,&ops,NAN)==1 && f.pings==0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            production = root/'keeper.c'
            production.write_text(SOURCE)
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-O2', str(production),
                            '-o', str(root/'keeper')], check=True)
            # Do not execute the production binary or open any watchdog device.
            test = root/'test.c'
            test.write_text('#define FORGE_WATCHDOG_TEST\n' + SOURCE + harness)
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-O2', str(test),
                            '-o', str(root/'test')], check=True)
            subprocess.run([str(root/'test')], check=True, timeout=5)
