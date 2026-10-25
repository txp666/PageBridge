"""Stable Windows package metadata and read-only, cross-platform ZIP verification."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import xml.etree.ElementTree as ET
import zipfile

MANIFEST = 'BUNDLE_MANIFEST.json'
UI_FILES = ('PageBridge.Windows.exe', 'PageBridge.Windows.dll', 'PageBridge.Windows.pri', 'App.xbf')
ENGINE_FILES = ('worker.py', 'translation_guard.py', 'layout_guard.py', 'pdf_links.py', 'pyproject.toml', 'uv.lock')
REQUIRED = (*UI_FILES, 'WINDOWS_BUILD.json', 'README.md', 'README-Windows.md', 'docs/USER_GUIDE.md',
            'resources/VERSION.json', 'resources/windows/app.manifest', 'resources/windows/packages.lock.json',
            'resources/desktop/bridge.py', 'resources/engine/runtime/python.exe', 'resources/engine/uv.lock')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('Duplicate JSON member: '+key)
        result[key] = value
    return result


def read_json(data):
    return json.loads(data, object_pairs_hook=_object)


def load_version(root):
    value = read_json((Path(root)/'VERSION.json').read_text(encoding='utf-8-sig'))
    version, build = value.get('version'), value.get('build')
    if (not isinstance(version, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version)
            or not isinstance(build, str) or not re.fullmatch(r'[1-9][0-9]*', build)
            or any(int(part) > 65535 for part in version.split('.')) or int(build) > 65535):
        raise ValueError('VERSION.json must contain major.minor.patch and a build string in 1..65535.')
    return {'version': version, 'build': build}


def build_metadata(root):
    return dict(**load_version(root), channel='stable', architecture='x64', ui='WinUI 3',
                automaticUpdates=False, signed=False, updateMode='manual', distribution='portable-zip')


def package_name(root):
    return 'PageBridge-'+load_version(root)['version']+'-windows-x64'


def safe_name(name):
    if not isinstance(name, str): raise ValueError('Package path must be text.')
    path = PurePosixPath(name)
    if (not isinstance(name, str) or not name or path.is_absolute() or '..' in path.parts
            or '\\' in name or ':' in name or str(path) != name):
        raise ValueError('Unsafe package path: '+str(name))


def ordinary(path):
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('Linked package/source path: '+str(path))


def source_inputs(root):
    root = Path(root)
    paths = {root/name for name in ('VERSION.json', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
        'windows/Directory.Build.props', 'windows/PageBridge.Windows/packages.lock.json',
        'scripts/build-windows.ps1', 'scripts/windows_package.py', 'scripts/build-windows-engine.py',
        'scripts/verify-windows-engine.py', 'scripts/bundle-profile.json',
        'scripts/setup-inno.ps1', 'scripts/windows-installer.iss')}
    paths.update(root/'engine'/name for name in ENGINE_FILES)
    for base in ('windows', 'desktop', 'docs', 'demos'):
        for path in (root/base).rglob('*'):
            if not path.is_file() or any(part in ('bin', 'obj', '.vs', '__pycache__') for part in path.parts): continue
            if path.name.startswith('._') or path.name == '.DS_Store' or path.suffix in ('.pyc', '.pyo'): continue
            paths.add(path)
    result = {}
    for path in sorted(paths):
        ordinary(path)
        if not path.is_file(): raise ValueError('Missing release source: '+str(path))
        result[path.relative_to(root).as_posix()] = digest(path)
    return result


def payload_sources(root):
    root = Path(root)
    result = {f'resources/engine/{name}': f'engine/{name}' for name in ENGINE_FILES}
    result.update({f'resources/{name}': name for name in ('VERSION.json', 'LICENSE', 'THIRD_PARTY_NOTICES.md')})
    result.update({'resources/desktop/bridge.py': 'desktop/bridge.py', 'README.md': 'README.md',
                   'README-Windows.md': 'docs/WINDOWS.md',
                   'resources/windows/packages.lock.json': 'windows/PageBridge.Windows/packages.lock.json'})
    for directory, prefix in (('desktop/pagebridge', 'resources/desktop/pagebridge'), ('docs', 'docs'), ('demos', 'resources/demos')):
        for path in (root/directory).rglob('*'):
            if not path.is_file() or '__pycache__' in path.parts or path.name.startswith('._') or path.name == '.DS_Store' or path.suffix in ('.pyc','.pyo'): continue
            result[prefix+'/'+path.relative_to(root/directory).as_posix()] = path.relative_to(root).as_posix()
    return result


def copy_docs(root, folder):
    root, folder = Path(root), Path(folder)
    for source, target in ((root/'README.md', folder/'README.md'), (root/'docs/WINDOWS.md', folder/'README-Windows.md'),
                           (root/'windows/PageBridge.Windows/packages.lock.json', folder/'resources/windows/packages.lock.json')):
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
    shutil.copytree(root/'docs', folder/'docs', copy_function=shutil.copyfile,
                    ignore=shutil.ignore_patterns('.DS_Store', '._*', '__pycache__', '*.pyc', '*.pyo'))


def _validate_metadata(read, root):
    version = load_version(root)
    if read_json(read('resources/VERSION.json')) != version: raise ValueError('Bundled VERSION.json differs from source.')
    if read_json(read('WINDOWS_BUILD.json')) != build_metadata(root): raise ValueError('Windows metadata differs from stable source policy.')
    manifest = ET.fromstring(read('resources/windows/app.manifest'))
    identity = manifest.find('{urn:schemas-microsoft-com:asm.v1}assemblyIdentity')
    if identity is None or identity.get('version') != version['version']+'.'+version['build']:
        raise ValueError('Windows application manifest version differs from VERSION.json.')
    lock = read_json(read('resources/windows/packages.lock.json'))
    if lock.get('version') != 1 or not lock.get('dependencies'): raise ValueError('Missing NuGet dependency lock.')


def write_manifest(folder, root):
    folder, root = Path(folder), Path(root)
    for name in REQUIRED:
        if not (folder/name).is_file() or (folder/name).stat().st_size == 0: raise ValueError('Missing package file: '+name)
    _validate_metadata(lambda name: (folder/name).read_bytes(), root)
    files, folded = {}, set()
    for directory, dirs, names in os.walk(folder):
        for child in dirs + names: ordinary(Path(directory)/child)
        for name in names:
            path = Path(directory)/name; relative = path.relative_to(folder).as_posix()
            if relative == MANIFEST: continue
            safe_name(relative)
            if relative.casefold() in folded: raise ValueError('Case-insensitive package collision.')
            folded.add(relative.casefold())
            files[relative] = dict(bytes=path.stat().st_size, sha256=digest(path))
    sources = payload_sources(root)
    for name, relative in sources.items():
        if name not in files or files[name]['sha256'] != digest(root/relative): raise ValueError('Bundled source differs: '+name)
    manifest = dict(schema=1, packageName=package_name(root), metadata=build_metadata(root),
                    files=dict(sorted(files.items())), sourceFiles=sources, sourceIdentity=source_inputs(root))
    (folder/MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    return manifest


def verify_archive(path, source_root):
    """Read-only Linux/macOS/Windows release verification; no app execution."""
    path, root = Path(path), Path(source_root)
    prefix = package_name(root)+'/'
    with zipfile.ZipFile(path) as archive:
        members, folded = {}, set()
        for item in archive.infolist():
            if item.is_dir() or not item.filename.startswith(prefix): raise ValueError('Unexpected ZIP root/directory.')
            name = item.filename[len(prefix):]; safe_name(name)
            if name.casefold() in folded or stat.S_ISLNK(item.external_attr >> 16): raise ValueError('Duplicate/link ZIP member.')
            folded.add(name.casefold()); members[name] = item
        if MANIFEST not in members or members[MANIFEST].file_size > 8*1024*1024: raise ValueError('Missing or oversized bundle manifest.')
        manifest_bytes = archive.read(members[MANIFEST])
        manifest = read_json(manifest_bytes)
        if (manifest.get('schema') != 1 or manifest.get('packageName') != package_name(root)
                or manifest.get('metadata') != build_metadata(root)
                or manifest.get('sourceIdentity') != source_inputs(root)
                or manifest.get('sourceFiles') != payload_sources(root)):
            raise ValueError('Bundle manifest does not identify the exact release source/version.')
        expected = manifest['files']
        if set(members) != set(expected)|{MANIFEST} or any(name not in expected for name in REQUIRED):
            raise ValueError('ZIP file set differs from its manifest or required payload.')
        total = 0
        for name, entry in expected.items():
            safe_name(name)
            if type(entry.get('bytes')) is not int or entry['bytes'] < 0: raise ValueError('Invalid manifest size.')
            member = members[name]
            with archive.open(member) as stream:
                first = stream.read(64); hashed = hashlib.sha256(first)
                if PurePosixPath(name).suffix.lower() in ('.exe','.dll','.pyd','.winmd') and (len(first)<64 or first[:2]!=b'MZ'):
                    raise ValueError('Invalid PE payload: '+name)
                while block := stream.read(1024*1024): hashed.update(block)
            if member.file_size != entry['bytes'] or hashed.hexdigest() != entry.get('sha256'):
                raise ValueError('ZIP content differs from its manifest: '+name)
            total += member.file_size
        for name, relative in manifest['sourceFiles'].items():
            if expected[name]['sha256'] != digest(root/relative): raise ValueError('Packaged source differs from release: '+name)
        _validate_metadata(lambda name: archive.read(members[name]), root)
    return dict(status='passed', version=load_version(root)['version'], build=load_version(root)['build'],
                channel='stable', signed=False, automaticUpdates=False, updateMode='manual',
                files=len(members), expandedBytes=total+members[MANIFEST].file_size,
                archiveBytes=path.stat().st_size, sha256=digest(path), sourceIdentity='matched', zipCRC='passed',
                bundleManifestSHA256=hashlib.sha256(manifest_bytes).hexdigest())


def source_identity_hash(root):
    return hashlib.sha256(json.dumps(source_inputs(root), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def pe_version_info(data):
    """Read the actual RT_VERSION resource, not a string searched in an overlay."""
    if len(data) < 128 or data[:2] != b'MZ': raise ValueError('Invalid installer PE header.')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if pe + 24 > len(data) or data[pe:pe+4] != b'PE\0\0': raise ValueError('Invalid PE signature.')
    machine, count = struct.unpack_from('<HH', data, pe+4)
    optional_size = struct.unpack_from('<H', data, pe+20)[0]
    optional = pe+24
    magic = struct.unpack_from('<H', data, optional)[0]
    directories = optional + (96 if magic == 0x10b else 112 if magic == 0x20b else 0)
    if directories == optional or directories+40 > optional+optional_size: raise ValueError('Invalid optional PE header.')
    resource_rva, resource_size = struct.unpack_from('<II', data, directories+16)
    certificate_offset, certificate_size = struct.unpack_from('<II', data, directories+32)
    sections = []
    for i in range(count):
        offset = optional+optional_size+i*40
        if offset+40 > len(data): raise ValueError('Truncated PE section table.')
        virtual_size, address, raw_size, raw_offset = struct.unpack_from('<IIII', data, offset+8)
        sections.append((address, max(virtual_size, raw_size), raw_offset, raw_size))
    def file_offset(rva, size):
        for address, length, raw_offset, raw_size in sections:
            if address <= rva and rva+size <= address+length and rva+size-address <= raw_size:
                offset = raw_offset+rva-address
                if offset+size <= len(data): return offset
        raise ValueError('PE resource leaves its section.')
    base = file_offset(resource_rva, resource_size)
    def entries(relative):
        if relative+16 > resource_size: raise ValueError('Invalid PE resource directory.')
        named, ids = struct.unpack_from('<HH', data, base+relative+12)
        if relative+16+(named+ids)*8 > resource_size: raise ValueError('Invalid PE resource entries.')
        return [struct.unpack_from('<II', data, base+relative+16+i*8) for i in range(named+ids)]
    version_entry = next((target for name,target in entries(0) if name == 16), None)
    if version_entry is None or not version_entry & 0x80000000: raise ValueError('Installer has no version resource.')
    current = version_entry
    for _ in range(2):
        children = entries(current & 0x7fffffff)
        if not children: raise ValueError('Empty version resource directory.')
        current = children[0][1]
    if current & 0x80000000 or current+16 > resource_size: raise ValueError('Invalid version resource data.')
    rva, size = struct.unpack_from('<II', data, base+current)
    if size > 1024*1024: raise ValueError('Oversized version resource.')
    offset = file_offset(rva, size); blob = data[offset:offset+size]
    strings = {}
    fixed = None
    def block(start, end, depth=0):
        nonlocal fixed
        if depth > 8 or start+6 > end: raise ValueError('Invalid version block.')
        length, value_length, value_type = struct.unpack_from('<HHH', blob, start)
        limit=start+length
        if length < 6 or limit > end: raise ValueError('Invalid version block length.')
        position=start+6
        while position+2 <= limit and blob[position:position+2] != b'\0\0': position+=2
        if position+2 > limit: raise ValueError('Unterminated version key.')
        key=blob[start+6:position].decode('utf-16-le')
        position=(position+2+3)&~3
        value_size=value_length*(2 if value_type else 1)
        if position+value_size > limit: raise ValueError('Invalid version value.')
        value=blob[position:position+value_size]
        if key == 'VS_VERSION_INFO':
            if len(value) < 52 or struct.unpack_from('<I',value)[0] != 0xfeef04bd: raise ValueError('Invalid fixed version info.')
            fixed=struct.unpack_from('<IIII',value,8)
        elif value_type and value_length: strings[key]=value.decode('utf-16-le').rstrip('\0')
        position=(position+value_size+3)&~3
        while position+6 <= limit:
            child_length=struct.unpack_from('<H',blob,position)[0]
            if child_length == 0: break
            block(position,limit,depth+1); position=(position+child_length+3)&~3
    block(0,len(blob))
    if fixed is None: raise ValueError('Missing fixed version.')
    def dotted(high,low):return '.'.join(str(v) for v in (high>>16,high&65535,low>>16,low&65535))
    return dict(machine=machine,fileVersion=dotted(*fixed[:2]),productVersion=dotted(*fixed[2:]),
                strings=strings,hasCertificate=bool(certificate_offset or certificate_size))


def verify_installer(path, source_root):
    """Validate PE version/source and its binding to the adjacent verified ZIP.

    This does not decompress Inno payloads; test-windows-installer.ps1 separately
    installs and compares every actual installed byte with the bundle manifest.
    """
    path, root = Path(path), Path(source_root)
    version=load_version(root); expected=version['version']+'.'+version['build']
    expected_name=package_name(root)+'-setup.exe'
    if path.name != expected_name: raise ValueError('Installer filename/version mismatch.')
    info=pe_version_info(path.read_bytes())
    if info['machine'] not in (0x14c,0x8664) or info['fileVersion'] != expected or info['productVersion'] != expected:
        raise ValueError('Installer PE version/build/architecture mismatch.')
    if info['hasCertificate']: raise ValueError('Unsigned release metadata differs from installer signature.')
    archive=path.with_name(package_name(root)+'.zip')
    archive_report=verify_archive(archive,root)
    description='PageBridge Setup; source='+source_identity_hash(root)+'; bundle='+archive_report['bundleManifestSHA256']
    strings=info['strings']
    if (strings.get('FileDescription') != description or strings.get('OriginalFilename') != expected_name
            or strings.get('ProductVersion') != version['version']+'+build.'+version['build']):
        raise ValueError('Installer resource does not match release source or ZIP manifest.')
    return dict(status='passed',version=version['version'],build=version['build'],channel='stable',signed=False,
                automaticUpdates=False,updateMode='manual',bytes=path.stat().st_size,sha256=digest(path),
                bootstrapMachine=hex(info['machine']),sourceIdentity='matched',
                bundleManifestSHA256=archive_report['bundleManifestSHA256'],
                payloadVerification='metadata bound to verified ZIP; actual install verified separately')


def archive_bundle(folder, archive, root):
    folder, archive, root = Path(folder), Path(archive), Path(root)
    if folder.name != package_name(root) or archive.name != folder.name+'.zip': raise ValueError('Formal package name/version mismatch.')
    if archive.exists() or Path(str(archive)+'.sha256').exists(): raise ValueError('Archive or checksum already exists.')
    # Revalidate the folder source/version immediately before archiving.
    manifest = write_manifest(folder, root)
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
        for name in sorted([*manifest['files'], MANIFEST]): handle.write(folder/name, folder.name+'/'+name)
    report = verify_archive(archive, root)
    Path(str(archive)+'.sha256').write_text(report['sha256']+'  '+archive.name+'\n', encoding='ascii')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('version','docs','metadata','manifest','archive','verify','installer','identity'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--folder', type=Path);parser.add_argument('--archive', type=Path);parser.add_argument('--report', type=Path)
    args=parser.parse_args()
    if args.action=='version':result=load_version(args.root)
    elif args.action=='identity':print(source_identity_hash(args.root));return
    elif args.action=='docs':copy_docs(args.root,args.folder);return
    elif args.action=='metadata':
        (args.folder/'WINDOWS_BUILD.json').write_text(json.dumps(build_metadata(args.root),indent=2)+'\n',encoding='utf-8');return
    elif args.action=='manifest':result=write_manifest(args.folder,args.root);result={'status':'passed','files':len(result['files'])+1}
    elif args.action=='archive':result=archive_bundle(args.folder,args.archive,args.root)
    elif args.action=='installer':
        result=verify_installer(args.archive,args.root)
        Path(str(args.archive)+'.sha256').write_text(result['sha256']+'  '+args.archive.name+'\n',encoding='ascii')
    else:result=verify_archive(args.archive,args.root)
    content=json.dumps(result,indent=2,ensure_ascii=False)+'\n'
    if args.report:args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(content,encoding='utf-8')
    print(content,end='')


if __name__=='__main__':main()
