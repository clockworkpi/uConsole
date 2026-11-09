"""Owner-approved physical transactions; clients cannot select arbitrary hosts."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from forge_host_tasks import unique_object


@dataclass(frozen=True)
class TargetTransaction:
    name: str
    workspace: str
    journal: Path
    plan_sha256: str = ''
    authorization_sha256: str = ''
    kind: str = 'files'
    phase: str = ''
    boot_id: str = ''

    def definition(self):
        base = {'workspace': self.workspace, 'journal': str(self.journal)}
        if self.kind == 'recovery-stage':
            return dict(base, kind=self.kind, authorization_sha256=self.authorization_sha256,
                        phase=self.phase, boot_id=self.boot_id)
        if self.kind == 'service':
            return dict(base, kind='service', authorization_sha256=self.authorization_sha256)
        return dict(base, plan_sha256=self.plan_sha256)


class TargetTransactions:
    def __init__(self, path, expected_sha256, workspaces):
        if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
            raise ValueError('Target policy requires an explicitly approved SHA-256')
        with Path(path).open('rb') as stream:
            payload = stream.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError('Target policy exceeds 1 MiB')
        self.sha256 = hashlib.sha256(payload).hexdigest()
        if self.sha256 != expected_sha256:
            raise ValueError('Target policy differs from approved SHA-256')
        definition = json.loads(payload, object_pairs_hook=unique_object)
        if (not isinstance(definition, dict) or set(definition) != {'schema', 'transactions'} or
                type(definition['schema']) is not int or definition['schema'] != 1 or
                not isinstance(definition['transactions'], dict) or
                not 1 <= len(definition['transactions']) <= 64):
            raise ValueError('Target policy requires schema 1 and 1..64 transactions')
        self.transactions = {}
        for name, item in definition['transactions'].items():
            if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', name):
                raise ValueError('Invalid target transaction name')
            if not isinstance(item, dict):
                raise ValueError('Invalid target transaction fields')
            service = item.get('kind') == 'service'
            staging = item.get('kind') == 'recovery-stage'
            fields = {'workspace', 'journal', 'kind', 'authorization_sha256'} if service or staging else {'workspace', 'journal', 'plan_sha256'}
            if staging: fields |= {'phase', 'boot_id'}
            if set(item) != fields:
                raise ValueError('Invalid target transaction fields')
            pin = 'authorization_sha256' if service or staging else 'plan_sha256'
            if staging and (item['phase'] not in ('firmware-start', 'firmware-fixup', 'command', 'selector') or
                    not isinstance(item['boot_id'], str) or not re.fullmatch(
                        '[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', item['boot_id'])):
                raise ValueError('Staging approval requires a fixed phase and explicit normal boot UUID')
            if not isinstance(item['workspace'], str) or item['workspace'] not in workspaces:
                raise ValueError('Target transaction must bind to a registered workspace')
            if (not isinstance(item['journal'], str) or not Path(item['journal']).is_absolute() or
                    not isinstance(item[pin], str) or
                    not re.fullmatch('[0-9a-f]{64}', item[pin])):
                raise ValueError('Target transaction requires an absolute journal and approved plan digest')
            self.transactions[name] = TargetTransaction(name, item['workspace'], Path(item['journal']),
                **{pin: item[pin]}, kind='recovery-stage' if staging else 'service' if service else 'files',
                **({'phase': item['phase'], 'boot_id': item['boot_id']} if staging else {}))

    def get(self, name, workspace):
        item = self.transactions.get(name)
        if item is None or item.workspace != workspace:
            raise ValueError('Target transaction is not approved for this workspace')
        return item

    def describe(self, workspace):
        return [dict({'name': item.name, 'policy_sha256': self.sha256},
                     **({'kind': item.kind, 'authorization_sha256': item.authorization_sha256,
                         'phase': item.phase, 'boot_id': item.boot_id} if item.kind == 'recovery-stage' else
                        {'kind': 'service', 'authorization_sha256': item.authorization_sha256}
                        if item.kind == 'service' else {'plan_sha256': item.plan_sha256}))
                for item in sorted(self.transactions.values(), key=lambda item: item.name)
                if item.workspace == workspace]
