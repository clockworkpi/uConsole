#!/usr/bin/env python3
"""Compose a synthetic PCIe2 contract tree; never an unmodified Pi500 tree."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

SELECTED_SHA = '400b2da08abb8fd0ffd65085d1e0e1e6c6d01cadd27385403ea3a3fd5a1537f4'
HOST = '/axi/pcie@1000120000'


def decode(blob):
    h = struct.unpack_from('>10I', blob)
    magic,total,structure,strings,reserve,version,last,boot,ssize,tsize = h
    if magic != 0xd00dfeed or total > len(blob) or structure+tsize > total or strings+ssize > total:
        raise ValueError('invalid FDT header')
    nodes,stack,pos = {},[],structure
    def word():
        nonlocal pos
        if pos+4 > structure+tsize: raise ValueError('short FDT token')
        n=struct.unpack_from('>I',blob,pos)[0];pos+=4;return n
    while pos < structure+tsize:
        token=word()
        if token==1:
            end=blob.index(b'\0',pos,structure+tsize);stack.append(blob[pos:end].decode());pos=(end+4)&~3
            nodes['/'+'/'.join(stack[1:])]={}
        elif token==2:
            if not stack: raise ValueError('unbalanced FDT')
            stack.pop()
        elif token==3:
            length,offset=word(),word()
            if offset>=ssize or pos+length>structure+tsize: raise ValueError('short FDT property')
            end=blob.index(b'\0',strings+offset,strings+ssize);name=blob[strings+offset:end].decode()
            nodes['/'+'/'.join(stack[1:])][name]=blob[pos:pos+length];pos=(pos+length+3)&~3
        elif token==4: pass
        elif token==9:
            if stack: raise ValueError('unbalanced FDT end')
            break
        else: raise ValueError('unknown FDT token')
    end=reserve
    while True:
        a,n=struct.unpack_from('>QQ',blob,end);end+=16
        if a==n==0:break
        if end>total:raise ValueError('invalid reserve map')
    return nodes,blob[reserve:end],boot


def words(raw):
    if len(raw)%4:raise ValueError('non-cell property')
    return list(struct.unpack('>'+str(len(raw)//4)+'I',raw))


def packed(values):return struct.pack('>'+str(len(values))+'I',*values)


def compatible(props,text):return text.encode() in props.get('compatible',b'').split(b'\0')


def phandles(nodes):
    result={}
    for path,props in nodes.items():
        for key in ['phandle','linux,phandle']:
            if key not in props:continue
            row=words(props[key])
            if len(row)!=1 or row[0]==0:raise ValueError('invalid phandle')
            n=row[0]
            if n in result and result[n]!=path:raise ValueError('duplicate phandle')
            result[n]=path
    return result


def reference_positions(nodes,path,key,raw,handles):
    """Binding-aware references; never rewrite integers merely matching an ID."""
    a=words(raw);props=nodes[path]
    lists={'pinctrl-0','pinctrl-1','pinctrl-2','nvmem-cells','memory-region','interrupt-affinity','shmem'}
    singles={'interrupt-parent','msi-parent','phy-handle','next-level-cache','operating-points-v2','brcm,firmware','firmware','cpu'}
    if key in lists or re.fullmatch(r'pinctrl-\d+',key):return list(range(len(a)))
    if key in singles or key.endswith('-supply'):
        if len(a)!=1:raise ValueError('unsupported singular reference '+key)
        return [0]
    types={'clocks':'#clock-cells','assigned-clocks':'#clock-cells','assigned-clock-parents':'#clock-cells',
           'resets':'#reset-cells','dmas':'#dma-cells','phys':'#phy-cells','power-domains':'#power-domain-cells',
           'pwms':'#pwm-cells','iommus':'#iommu-cells','mboxes':'#mbox-cells','io-channels':'#io-channel-cells',
           'thermal-sensors':'#thermal-sensor-cells','cooling-device':'#cooling-cells','sound-dai':'#sound-dai-cells',
           'interrupts-extended':'#interrupt-cells'}
    if (key in {'gpio','gpios'} or key.endswith('-gpios')) and 'gpio-hog' not in props:types[key]='#gpio-cells'
    if key=='gpio-ranges':
        if len(a)%4:raise ValueError('invalid gpio-ranges')
        return list(range(0,len(a),4))
    if key in ['msi-map','iommu-map']:
        if len(a)%4:raise ValueError('unsupported map cells')
        return list(range(1,len(a),4))
    if key=='msi-ranges':
        out=[];i=0
        while i<len(a):
            if a[i] not in handles:raise ValueError('invalid msi-ranges parent')
            out.append(i);p=nodes[handles[a[i]]];i+=2+words(p['#interrupt-cells'])[0]
        if i!=len(a):raise ValueError('short msi-ranges')
        return out
    if key=='interrupt-map':
        parent=nodes[path];ac=words(parent.get('#address-cells',packed([0])))[0];ic=words(parent.get('#interrupt-cells',packed([1])))[0]
        out=[];i=0
        while i<len(a):
            i+=ac+ic
            if i>=len(a) or a[i] not in handles:raise ValueError('invalid interrupt-map parent')
            out.append(i);p=nodes[handles[a[i]]];i+=1+words(p.get('#address-cells',packed([0])))[0]+words(p['#interrupt-cells'])[0]
        if i!=len(a):raise ValueError('short interrupt-map')
        return out
    if key not in types:return []
    out=[];i=0
    while i<len(a):
        if a[i]==0:i+=1;continue
        if a[i] not in handles:raise ValueError('unresolved reference '+path+':'+key)
        out.append(i);provider=nodes[handles[a[i]]];cellkey=types[key]
        if cellkey not in provider:raise ValueError('missing '+cellkey+' for '+key)
        count=words(provider[cellkey])
        if len(count)!=1 or count[0]>32:raise ValueError('unsupported specifier width')
        i+=1+count[0]
    if i!=len(a):raise ValueError('short specifier '+path+':'+key)
    return out


def references(nodes):
    h=phandles(nodes);refs=[]
    for path,props in nodes.items():
        if path in ['/__symbols__','/aliases'] or path.startswith('/__symbols__/'):continue
        if path in ['/__fixups__','/__local_fixups__'] or path.startswith('/__fixups__/') or path.startswith('/__local_fixups__/'):
            raise ValueError('overlay fixups unsupported; complete DTB required')
        for key,raw in props.items():
            # Non-cell strings are not reference arrays.
            if len(raw)%4:
                if key in {'gpio','gpios','clocks','resets','dmas','phys','power-domains','pwms','iommus','mboxes','io-channels','thermal-sensors','cooling-device','sound-dai','interrupts-extended','interrupt-parent','msi-parent','phy-handle','gpio-ranges','interrupt-map','msi-map','iommu-map','pinctrl-0'} or key.endswith('-gpios') or key.endswith('-supply'):
                    raise ValueError('malformed reference property '+path+':'+key)
                continue
            for i in reference_positions(nodes,path,key,raw,h):
                value=words(raw)[i]
                if value not in h:raise ValueError('dangling '+path+':'+key+' '+hex(value))
                refs.append((path,key,i,value))
    return refs


def encode(nodes,reserve,boot):
    strings=bytearray();offsets={};body=bytearray()
    def emit(n):body.extend(struct.pack('>I',n))
    def padded(data):body.extend(data);body.extend(b'\0'*((-len(data))%4))
    def visit(path):
        emit(1);padded((path.rsplit('/',1)[-1] if path!='/' else '').encode()+b'\0')
        for key,data in nodes[path].items():
            if key not in offsets:offsets[key]=len(strings);strings.extend(key.encode()+b'\0')
            emit(3);emit(len(data));emit(offsets[key]);padded(data)
        for child in nodes:
            if child!='/' and child.rsplit('/',1)[0]==('' if path=='/' else path):visit(child)
        emit(2)
    visit('/');emit(9);start=40+len(reserve);end=start+len(body)
    return struct.pack('>10I',0xd00dfeed,end+len(strings),start,end,40,17,16,boot,len(strings),len(body))+reserve+body+strings


def compose(base,selected):
    if hashlib.sha256(selected).hexdigest()!=SELECTED_SHA:raise ValueError('selected DTB hash mismatch')
    n,reserve,boot=decode(base);s,_,_=decode(selected);original={p:dict(v) for p,v in n.items()}
    h=phandles(n);gics=[p for p,v in n.items() if compatible(v,'arm,gic-400')]
    if len(gics)!=1:raise ValueError('exactly one existing GIC400 required')
    gic=gics[0];gicids=[k for k,p in h.items() if p==gic]
    if len(gicids)!=1:raise ValueError('existing GIC phandle required')
    mapping={old:(1 if path==gic else 0x10000+old) for old,path in h.items()}
    refs=references(n)
    for path,key,i,old in refs:
        a=words(n[path][key]);a[i]=mapping[old];n[path][key]=packed(a)
    for old,path in h.items():
        for key in ['phandle','linux,phandle']:
            if key in n[path]:n[path][key]=packed([mapping[old]])
    disabled=[]
    for path,props in n.items():
        if compatible(props,'brcm,bcm2711-pcie') or compatible(props,'brcm,bcm2711-genet-v5'):
            props['status']=b'disabled\0';disabled.append(path)
    # Selected provider siblings preserve bus cells and translate exactly as400b.
    parent='/pcie2-contract-soc'
    if parent in n or '/axi' in n:raise ValueError('synthetic parent already exists')
    soc=next(path.rsplit('/',1)[0] for path in s if path.endswith('/reset-controller@119500'))
    n[parent]=dict(s[soc]);n['/axi']=dict(s['/axi'])
    selected_paths=[soc+'/reset-controller@119500',soc+'/reset-controller@1504318',soc+'/pinctrl@7d510700',soc+'/gpio@7d517c00',HOST,'/axi/msi-controller@1000130000']
    copied={}
    for root in selected_paths:
        for path,props in s.items():
            if path!=root and not path.startswith(root+'/'):continue
            # Retain the required RP1 container, never its peripherals/nexus.
            if path.startswith(HOST+'/rp1/'):continue
            dest=parent+path[len(soc):] if path.startswith(soc+'/') else path
            n[dest]=dict(props);copied[dest]=path
    rp1=HOST+'/rp1';n[rp1]['status']=b'disabled\0'
    n['/']['model']=b'Synthetic PCIe2 protocol contract on BCM2711 boot substrate\0'
    n['/']['compatible']=b'daemonarchy,pcie2-contract\0'+n['/'].get('compatible',b'')
    # Exhaustive closure for known reference bindings and unique phandles.
    checked=references(n);phandles(n)
    for dest,source in copied.items():
        for key,value in s[source].items():
            if source==rp1 and key=='status':continue
            if n[dest][key]!=value:raise ValueError('selected cell changed')
    for path in ['/chosen']:
        if path in original:
            for key,value in original[path].items():
                if key not in ['phandle','linux,phandle'] and n[path].get(key)!=value:
                    raise ValueError('boot chosen changed')
    return encode(n,reserve,boot),{'synthetic':True,'selected_sha256':SELECTED_SHA,'gic_node':gic,'gic_xref':1,'base_phandle_remap':{hex(k):hex(v) for k,v in mapping.items()},'base_reference_count':len(refs),'checked_reference_count':len(checked),'disabled_base_consumers':disabled,'copied_nodes':copied,'rp1_container_disabled':True,'scope':'Selected cells/providers with original SD/PL011 substrate; no whole Pi500 or RP1 model'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--selected',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise SystemExit('output already exists')
    blob,receipt=compose(a.base.read_bytes(),a.selected.read_bytes());a.output.write_bytes(blob)
    receipt.update(base_sha256=hashlib.sha256(a.base.read_bytes()).hexdigest(),output_sha256=hashlib.sha256(blob).hexdigest())
    a.output.with_suffix(a.output.suffix+'.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k not in ['base_phandle_remap','copied_nodes']},indent=2))

if __name__=='__main__':main()
