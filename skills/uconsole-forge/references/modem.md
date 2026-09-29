# Composite modem scenarios

Use `modem: "composite"` when booting an owned guest to select the synthetic
five-serial-port/RNDIS model. It is not a physical SIM7600 firmware or RF test.
Checkpointing is blocked in this mode.

`modem_query` takes the workspace ID and returns a sampled-state job without a
device-control grant. `modem_set` requires that grant and accepts the supported
SIM, radio, registration, RSSI and BER fields shown by `tools/list`. Poll each
job to completion; an uncertain result neither proves no effect nor authorizes
repeating the mutation.

`modem_connection` takes `workspace` and boolean `connected` under
device-control. It changes the modeled USB cable, retains SIM/PDP state, and
is not a power cycle. Verify guest port disappearance/reappearance separately
from the QEMU attachment readback. Queried `link_up` means synthetic packet
session readiness, not USB attachment or end-to-end connectivity.

The guest's supported `CGDCONT`, `CGACT` and `CGATT` subset controls synthetic
IPv4 contexts. Reattaching alone does not reactivate a context. Multiple
contexts share one bearer; IPv6, PPP, carrier provisioning, calls, SMS and GNSS
are not established by a successful AT response or packet probe. Record the
actual guest enumeration and traffic evidence separately from model readback.
