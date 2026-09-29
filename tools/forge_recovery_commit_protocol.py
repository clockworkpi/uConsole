"""Strict phase protocol for the separate, mutating recovery commit channel."""
import json


def exact(left, right):
    return json.dumps(left,sort_keys=True,allow_nan=False) == json.dumps(right,sort_keys=True,allow_nan=False)


def decode(data):
    def unique(items):
        result = {}
        for key,value in items:
            if key in result: raise ValueError('Duplicate commit protocol field')
            result[key] = value
        return result
    return json.loads(data,object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite commit field')))


class Protocol:
    def __init__(self, plan, pin, attempt):
        self.plan,self.pin,self.attempt = plan,pin,attempt
        self.phase = 'hashing'
        self.progress = {}
        self.result = None

    @property
    def may_renew(self):
        return self.phase in ('hashing','checking')

    def complete_ranges(self, names):
        for name in names:
            length = self.plan[name+'_guard']['bytes']
            if length and self.progress.get(name) != length:
                raise ValueError('Missing completed protected-range progress: '+name)

    def consume(self, message):
        if not isinstance(message,dict) or message.get('attempt') != self.attempt:
            raise ValueError('Commit reply belongs to another attempt')
        kind = message.get('type')
        if kind == 'progress':
            names = ('root','prefix','suffix') if self.phase == 'hashing' else ('root','suffix')
            if (not self.may_renew or set(message) != {'type','attempt','range','bytes','total'} or
                    message['range'] not in names):
                raise ValueError('Unexpected commit hash progress phase')
            name,done,total = message['range'],message['bytes'],message['total']
            if (type(done) is not int or type(total) is not int or
                    total != self.plan[name+'_guard']['bytes'] or
                    not self.progress.get(name,0) < done <= total):
                raise ValueError('Invalid or regressing commit hash progress')
            self.progress[name] = done
        elif kind == 'prepared':
            if self.phase != 'hashing' or not exact(message, dict(type='prepared',attempt=self.attempt,
                                                          pin=self.pin,binding=self.plan['binding'])):
                raise ValueError('Prepared commit reply differs from pinned plan')
            self.complete_ranges(('root','prefix','suffix'))
            self.phase = 'prepared'
        elif kind == 'unmounted':
            if self.phase != 'committing' or not exact(message, dict(type='unmounted',attempt=self.attempt,
                                                            pin=self.pin,boot_id=self.plan['binding']['boot_id'])):
                raise ValueError('Unrecognized commit unmount acknowledgement')
            self.phase,self.progress = 'checking',{}
        elif kind == 'complete':
            expected = dict(status='boot-file-commit-verified',plan_sha256=self.pin,
                boot_id=self.plan['binding']['boot_id'],operation=self.plan['operation'],
                file=dict(status='applied',path='/boot/firmware/config.txt'),
                root_written=False,boot_unmounted=True,reboot_performed=False,physical_boot_qualified=False)
            if self.phase != 'checking' or not exact(message, dict(type='complete',attempt=self.attempt,result=expected)):
                raise ValueError('Commit completion receipt differs')
            self.complete_ranges(('root','suffix'))
            self.phase,self.result = 'complete',expected
        else:
            raise ValueError('Unknown commit protocol message')
        return kind

    def committed(self):
        if self.phase != 'prepared': raise ValueError('Commit sent in the wrong phase')
        self.phase = 'committing'
