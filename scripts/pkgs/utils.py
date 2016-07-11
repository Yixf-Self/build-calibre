#!/usr/bin/env python2
# vim:fileencoding=utf-8
# License: GPLv3 Copyright: 2016, Kovid Goyal <kovid at kovidgoyal.net>

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)
from contextlib import contextmanager
import time
import os
import shlex
import pipes
import subprocess
import sys
import errno
import glob
import shutil
import tarfile
import zipfile
import stat

from .constants import (
    build_dir, current_source, mkdtemp, PATCHES, PYTHON, MAKEOPTS, LIBDIR,
    worker_env, pkg_ext, islinux, PREFIX, iswindows
)


class ModifiedEnv(object):

    def __init__(self, **kwargs):
        self.mods = kwargs

    def apply(self, mods):
        for k, val in mods.iteritems():
            if val:
                worker_env[k] = val
            else:
                worker_env.pop(k, None)

    def __enter__(self):
        self.orig = {k: worker_env.get(k) for k in self.mods}
        self.apply(self.mods)

    def __exit__(self, *args):
        self.apply(self.orig)


def current_env(library_path=False):
    env = os.environ.copy()
    env.update(worker_env)
    if library_path:
        if library_path is True:
            library_path = LIBDIR
        else:
            library_path = library_path + os.pathsep + LIBDIR
        env['LD_LIBRARY_PATH'] = library_path
    enc = 'mbcs' if iswindows else 'utf-8'
    env = {k.encode(enc): v.encode(enc) for k, v in env.iteritems()}
    return env


def run_shell(library_path=False):
    sh = '/bin/bash' if islinux else 'C:/cygwin64/bin/zsh' if iswindows else '/bin/zsh'
    env = current_env(library_path=library_path)
    if iswindows:
        from .constants import cygwin_paths
        paths = env['PATH'].split(os.pathsep)
        paths.extend(cygwin_paths)
        env[b'PATH'] = os.pathsep.join(paths)
    return subprocess.Popen([sh, '-i'], env=env).wait()


def run(*args, **kw):
    if len(args) == 1 and isinstance(args[0], type('')):
        cmd = shlex.split(args[0])
    else:
        cmd = args
    print(' '.join(pipes.quote(x) for x in cmd))
    try:
        p = subprocess.Popen(cmd, env=current_env(library_path=kw.get('library_path')))
    except EnvironmentError as err:
        if err.errno == errno.ENOENT:
            raise SystemExit('Could not find the program: %s' % cmd[0])
        raise
    sys.stdout.flush()
    if p.wait() != 0:
        print('The following command failed, with return code:', p.wait(),
              file=sys.stderr)
        print(' '.join(pipes.quote(x) for x in cmd))
        print('Dropping you into a shell')
        sys.stdout.flush()
        run_shell(library_path=kw.get('library_path'))
        raise SystemExit(1)


def extract(source):
    if source.lower().endswith('.zip'):
        with zipfile.ZipFile(source) as zf:
            zf.extractall()
    else:
        run('tar', 'xf', source)


def chdir_for_extract(name):
    tdir = mkdtemp(prefix=os.path.basename(name).split('-')[0] + '-')
    os.chdir(tdir)


def extract_source():
    source = current_source()
    chdir_for_extract(source)
    extract(source)
    x = os.listdir('.')
    if len(x) == 1:
        os.chdir(x[0])


def apply_patch(name, level=0, reverse=False):
    if not os.path.isabs(name):
        name = os.path.join(PATCHES, name)
    args = ['patch', '-p%d' % level, '-i', name]
    if reverse:
        args.insert(1, '-R')
    run(*args)


def simple_build(configure_args=(), make_args=(), install_args=(), library_path=None, override_prefix=None, no_parallel=False, configure_name='./configure'):
    if isinstance(configure_args, type('')):
        configure_args = shlex.split(configure_args)
    if isinstance(make_args, type('')):
        make_args = shlex.split(make_args)
    if isinstance(install_args, type('')):
        install_args = shlex.split(install_args)
    run(configure_name, '--prefix=' + (override_prefix or build_dir()), *configure_args)
    make_opts = [] if no_parallel else shlex.split(MAKEOPTS)
    run('make', *(make_opts + list(make_args)))
    mi = ['make'] + list(install_args) + ['install']
    run(*mi, library_path=library_path)


def python_build(extra_args=()):
    if isinstance(extra_args, type('')):
        extra_args = shlex.split(extra_args)
    run(PYTHON, 'setup.py', 'install', '--root', build_dir(), *extra_args, library_path=True)


def replace_in_file(path, old, new):
    if isinstance(old, type('')):
        old = old.encode('utf-8')
    if isinstance(new, type('')):
        new = new.encode('utf-8')
    with open(path, 'r+b') as f:
        raw = f.read()
        if isinstance(old, bytes):
            nraw = raw.replace(old, new)
        else:
            nraw = old.sub(new, raw)
        if raw == nraw:
            raise SystemExit('Failed to patch: ' + path)
        f.seek(0), f.truncate()
        f.write(nraw)


def lcopy(src, dst):
    try:
        if os.path.islink(src):
            linkto = os.readlink(src)
            os.symlink(linkto, dst)
            return True
        else:
            shutil.copy(src, dst)
            return False
    except EnvironmentError as err:
        if err.errno == errno.EEXIST:
            os.unlink(dst)
            return lcopy(src, dst)


def install_binaries(pattern, destdir='lib', do_symlinks=False):
    dest = os.path.join(build_dir(), destdir)
    ensure_dir(dest)
    files = glob.glob(pattern)
    files.sort(key=len, reverse=True)
    if not files:
        raise ValueError('The pattern %s did not match any actual files' % pattern)
    for f in files:
        dst = os.path.join(dest, os.path.basename(f))
        islink = lcopy(f, dst)
        if not islink:
            os.chmod(dst, 0o755)
    if do_symlinks:
        library_symlinks(files[0], destdir=destdir)


def install_tree(src, dest_parent='include', ignore=None):
    dest_parent = os.path.join(build_dir(), dest_parent)
    dst = os.path.join(dest_parent, os.path.basename(src))
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True, ignore=ignore)
    return dst


def copy_headers(pattern, destdir='include'):
    dest = os.path.join(build_dir(), destdir)
    ensure_dir(dest)
    files = glob.glob(pattern)
    for f in files:
        dst = os.path.join(dest, os.path.basename(f))
        shutil.copy2(f, dst)


def library_symlinks(full_name, destdir='lib'):
    parts = full_name.split('.')
    idx = parts.index('so')
    basename = '.'.join(parts[:idx + 1])
    parts = parts[idx + 1:]
    for i in range(len(parts)):
        suffix = '.'.join(parts[:i])
        if suffix:
            suffix = '.' + suffix
        ln = os.path.join(build_dir(), destdir, basename + suffix)
        try:
            os.symlink(full_name, ln)
        except EnvironmentError as err:
            if err.errno != errno.EEXIST:
                raise
            os.unlink(ln)
            os.symlink(full_name, ln)


def ensure_dir(path):
    try:
        os.makedirs(path)
    except EnvironmentError as err:
        if err.errno != errno.EEXIST:
            raise


def create_package(module, src_dir, outfile):

    exclude = getattr(module, 'pkg_exclude_names', frozenset('doc man info test tests gtk-doc README'.split()))

    def filter_tar(tar_info):
        parts = tar_info.name.split('/')
        for p in parts:
            if p in exclude or p.rpartition('.')[-1] in ('pyc', 'pyo', 'la'):
                return
        if hasattr(module, 'filter_pkg') and module.filter_pkg(parts):
            return
        tar_info.uid, tar_info.gid, tar_info.mtime = 1000, 100, 0
        return tar_info

    c = pkg_ext.split('.')[-1]
    with tarfile.open(outfile, 'w:' + c) as archive:
        for x in os.listdir(src_dir):
            path = os.path.join(src_dir, x)
            if os.path.isdir(path):
                archive.add(path, arcname=x, filter=filter_tar)


def install_package(pkg_path, dest_dir):
    with tarfile.open(pkg_path) as archive:
        archive.extractall(dest_dir)


def set_title(x):
    if sys.stdout.isatty():
        print('''\033]2;%s\007''' % x)


def walk(path):
    ''' A nice interface to os.walk '''
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            yield os.path.join(dirpath, f)


def read_lib_names(p):
    lines = subprocess.check_output(['otool', '-D', p]).decode('utf-8').splitlines()
    install_name = None
    if len(lines) > 1:
        install_name = lines[1].strip()
    lines = subprocess.check_output(['otool', '-L', p]).decode('utf-8').splitlines()
    deps = []
    for line in lines[1:]:
        val = line.partition('(')[0].strip()
        if val != install_name:
            deps.append(val)
    return install_name, deps


def flipwritable(fn, mode=None):
    """
    Flip the writability of a file and return the old mode. Returns None
    if the file is already writable.
    """
    if os.access(fn, os.W_OK):
        return None
    old_mode = os.stat(fn).st_mode
    os.chmod(fn, stat.S_IWRITE | old_mode)
    return old_mode


def change_lib_names(p, changes):
    cmd = ['install_name_tool']
    for old_name, new_name in changes:
        if old_name is None:
            cmd.extend(['-id', new_name])
        else:
            cmd.extend(['-change', old_name, new_name])
    cmd.append(p)
    old_mode = flipwritable(p)
    subprocess.check_call(cmd)
    if old_mode is not None:
        flipwritable(p, old_mode)


def is_macho_binary(p):
    try:
        with open(p, 'rb') as f:
            return f.read(4) in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf')
    except EnvironmentError as err:
        if err.errno == errno.ENOENT:
            return False
        raise


def fix_install_names(m, output_dir):
    dylibs = set()
    mfunc = getattr(m, 'install_name_change_predicate', lambda p: False)
    mcfunc = getattr(m, 'install_name_change', lambda old_name, is_dep: old_name)
    for dirpath, dirnames, filenames in os.walk(output_dir):
        for f in filenames:
            p = os.path.abspath(os.path.realpath(os.path.join(dirpath, f)))
            if p not in dylibs and os.path.exists(p):
                if p.endswith('.dylib') or ('bin' in p.split('/') and is_macho_binary(p)) or (mfunc(p) and is_macho_binary(p)):
                    dylibs.add(p)
    for p in dylibs:
        changes = []
        install_name, deps = read_lib_names(p)
        if install_name:
            nn = install_name.replace(output_dir, PREFIX)
            nn = mcfunc(nn, False)
            if nn != install_name:
                changes.append((None, nn))
        for name in deps:
            nn = name.replace(output_dir, PREFIX)
            nn = mcfunc(nn, True)
            if nn != name:
                changes.append((name, nn))
        if changes:
            print('Changing lib names in:', p)
            change_lib_names(p, changes)


@contextmanager
def timeit():
    ' Usage: `with timeit() as times: whatever()` minutes, seconds = times '
    times = [0, 0]
    st = time.time()
    yield times
    dt = time.time() - st
    times[0], times[1] = dt // 60, dt % 60


@contextmanager
def current_dir(path):
    cwd = os.getcwd()
    os.chdir(path)
    yield path
    os.chdir(cwd)
