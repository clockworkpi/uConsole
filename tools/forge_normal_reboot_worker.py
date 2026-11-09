"""Fixed normal-return entry point; accepts only a release-hold selector plan."""
from forge_held_reboot_worker import reboot


def run(request):
    reboot(request, 'release-hold')
