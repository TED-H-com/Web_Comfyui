"""Deterministic frozen bootstrap transport; no permissive tar extraction."""
import io,tarfile,hashlib,json,inspect

def pack(entries):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w',format=tarfile.USTAR_FORMAT) as archive:
        for name,raw in sorted(entries):
            item=tarfile.TarInfo(name);item.size=len(raw);item.mode=0o600
            item.uid=item.gid=item.mtime=0;item.uname=item.gname=''
            archive.addfile(item,io.BytesIO(raw))
    return stream.getvalue()

def extract(path,base,expected,checksum):
    import hashlib,tarfile,os,uuid
    from pathlib import Path,PurePosixPath
    path=Path(path);base=Path(base)
    if path.is_symlink() or path.stat().st_size>32*1024*1024 or hashlib.sha256(path.read_bytes()).hexdigest()!=checksum:raise ValueError('archive_digest_mismatch')
    inventory={e['path']:e for e in expected}
    if len(inventory)!=len(expected):raise ValueError('archive_duplicate')
    contents={}
    with tarfile.open(path,mode='r:') as archive:
        for entry in archive:
            name=entry.name;parts=PurePosixPath(name).parts
            if (not entry.isreg() or not parts or name.startswith('/') or '..' in parts or '.' in name.split('/') or '\\' in name or name not in inventory or name in contents):raise ValueError('archive_member_invalid')
            e=inventory[name]
            if entry.size!=e['size'] or not 0<=entry.size<=32*1024*1024:raise ValueError('archive_size_mismatch')
            raw=archive.extractfile(entry).read(entry.size+1)
            if len(raw)!=e['size'] or hashlib.sha256(raw).hexdigest()!=e['sha256']:raise ValueError('archive_file_mismatch')
            contents[name]=raw
    if set(contents)!=set(inventory):raise ValueError('archive_inventory_mismatch')
    # Verify the whole archive BEFORE publishing any member. Repeat-safe fixed
    # destinations; no link, traversal, executable tar metadata or extractall.
    if base.is_symlink():raise ValueError('archive_destination_invalid')
    for name,raw in contents.items():
        p=base/name;parent=base
        for part in PurePosixPath(name).parts[:-1]:
            parent=parent/part
            if parent.is_symlink():raise ValueError('archive_destination_invalid')
            parent.mkdir(mode=0o700,exist_ok=True)
        if p.is_symlink():raise ValueError('archive_destination_invalid')
        temp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            os.replace(temp,p)
            if p.stat().st_size!=inventory[name]['size'] or hashlib.sha256(p.read_bytes()).hexdigest()!=inventory[name]['sha256']:raise ValueError('archive_published_mismatch')
        finally:temp.unlink(missing_ok=True)
    return True

def verification_code(remote,entries,checksum):
    return inspect.getsource(extract)+f"\nextract({remote+'/bootstrap.tar'!r},{remote!r},{entries!r},{checksum!r})\n"
