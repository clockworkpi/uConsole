"""Exercise the detach patch with each host's actual patch implementation."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PATCH = Path(__file__).resolve().parents[1]/'Code/patch/qemu/dwc2-detached-device.patch'
FIRST = '''    assert(port->index == 0);
    p = container_of(packet, DWC2Packet, packet);
    dev = dwc2_find_device(s, p->devadr);
    ep = usb_ep_get(dev, p->pid, p->epnum);
    trace_usb_dwc2_async_packet_complete(port, packet, p->index >> 3, dev,
                                         p->epnum, dirs[p->epdir], p->len);
    assert(p->async == DWC2_ASYNC_INFLIGHT);
'''
SECOND = '''        p = &s->packet[chan];
        if (p->needs_service) {
            dev = dwc2_find_device(s, p->devadr);
            ep = usb_ep_get(dev, p->pid, p->epnum);
            trace_usb_dwc2_work_bh_service(s->next_chan, chan, dev, p->epnum);
            dwc2_handle_packet(s, p->devadr, dev, ep, p->index, true);
            found = true;
        }
'''


@unittest.skipUnless(shutil.which('patch'), 'Host patch utility required')
class Dwc2PatchPortabilityTests(unittest.TestCase):
    def test_forward_reverse_and_context_offset(self):
        for shift in (0, 21):
            with self.subTest(shift=shift), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root/'hw/usb/hcd-dwc2.c'
                target.parent.mkdir(parents=True)
                original = '\n'*(510+shift)+FIRST+'\n'*73+SECOND+'\n'*10
                target.write_text(original)
                command = [shutil.which('patch'), '--batch', '-p1', '-i', str(PATCH)]
                def run(*flags):
                    result = subprocess.run(command+list(flags), cwd=root, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                run('--dry-run', '--forward')
                self.assertEqual(target.read_text(), original)
                run('--forward')
                changed = target.read_text()
                self.assertIn('ep = dev ? usb_ep_get', changed)
                self.assertIn('packet->status = USB_RET_NODEV;', changed)
                self.assertIn('p->needs_service = false;', changed)
                self.assertIn('dwc2_update_hc_irq(s, p->index);', changed)
                run('--dry-run', '--reverse')
                run('--reverse')
                self.assertEqual(target.read_text(), original)
