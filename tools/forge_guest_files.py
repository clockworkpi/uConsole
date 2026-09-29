"""Bounded, JSON-safe directory pages for supervised guest commands."""
import shlex


PROGRAM = '''import itertools,json,os,stat,sys
p=sys.argv[1]; offset=int(sys.argv[2]); rows=[]; more=False
with os.scandir(p) as scan:
 for entry in itertools.islice(scan,offset,None):
  if len(rows)==32:
   more=True; break
  info=entry.stat(follow_symlinks=False)
  kind='d' if stat.S_ISDIR(info.st_mode) else 'l' if stat.S_ISLNK(info.st_mode) else 'f'
  row={'name':entry.name,'type':kind,'bytes':info.st_size}
  if len(json.dumps(rows+[row]))>10000:
   more=True; break
  rows.append(row)
print(json.dumps({'entries':rows,'next_offset':offset+len(rows) if more else None}))
'''


def listing_script(path, offset=0):
    if not isinstance(path, str) or not path.startswith('/') or '\x00' in path:
        raise ValueError('Guest directory must be an absolute path without NUL')
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Directory offset must be in 0..1000000')
    script = f'/usr/bin/python3 -c {shlex.quote(PROGRAM)} {shlex.quote(path)} {offset}'
    if len(script) > 2200:
        raise ValueError('Directory path exceeds the supported guest-command limit')
    return script
