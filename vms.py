#!/usr/bin/env python2
# vim:fileencoding=utf-8
# License: GPLv3 Copyright: 2016, Kovid Goyal <kovid at kovidgoyal.net>

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)
import subprocess
import time
import socket
import shlex
import tempfile


def is_host_reachable(name, timeout=1):
    try:
        socket.create_connection((name, 22), timeout).close()
        return True
    except:
        return False


def is_vm_running(name):
    qname = '"%s"' % name
    try:
        lines = subprocess.check_output('VBoxManage list runningvms'.split()).decode('utf-8').splitlines()
    except Exception:
        time.sleep(1)
        lines = subprocess.check_output('VBoxManage list runningvms'.split()).decode('utf-8').splitlines()
    for line in lines:
        if line.startswith(qname):
            return True
    return False


SSH = [
    'ssh', '-o', 'User=kovid',
    '-o', 'ControlMaster=auto', '-o', 'ControlPersist=yes', '-o', 'ControlPath={}/%r@%h:%p'.format(tempfile.gettempdir())
]


def run_in_vm(name, *args, **kw):
    if len(args) == 1:
        args = shlex.split(args[0])
    p = subprocess.Popen(SSH + [name] + list(args))
    if kw.get('async'):
        return p
    if not p.wait() == 0:
        raise SystemExit(p.wait())


def ensure_vm(name):
    if not is_vm_running(name):
        subprocess.check_call(['VBoxManage', 'startvm', name])
        time.sleep(2)
    print('Waiting for SSH server to start...')
    st = time.time()
    while not is_host_reachable(name):
        time.sleep(0.1)
    print('SSH server started in', '%.1f' % (time.time() - st), 'seconds')


def shutdown_vm(name):
    if not is_vm_running(name):
        return
    isosx = name.startswith('osx-')
    cmd = 'sudo shutdown -h now' if isosx else ['shutdown.exe', '-s', '-f', '-t', '0']
    run_in_vm(name, cmd)
    subprocess.Popen(SSH + ['-O', 'exit', name])

    while is_host_reachable(name):
        time.sleep(0.1)
    if isosx:
        # OS X VM does not shutdown cleanly
        time.sleep(5)
        subprocess.check_call(('VBoxManage controlvm %s poweroff' % name).split())
    while is_vm_running(name):
        time.sleep(0.1)


class Rsync(object):

    excludes = frozenset({'*.pyc', '*.pyo', '*.swp', '*.swo', '*.pyj-cached', '*~', '.git'})

    def __init__(self, name):
        self.name = name

    def from_vm(self, from_, to, excludes=frozenset()):
        f = self.name + ':' + from_
        self(f, to, excludes)

    def to_vm(self, from_, to, excludes=frozenset()):
        t = self.name + ':' + to
        self(from_, t, excludes)

    def __call__(self, from_, to, excludes=frozenset()):
        ssh = ' '.join(SSH)
        if isinstance(excludes, type('')):
            excludes = excludes.split()
        excludes = frozenset(excludes) | self.excludes
        excludes = ['--exclude=' + x for x in excludes]
        cmd = ['rsync', '-a', '-e', ssh, '--delete', '--delete-excluded'] + excludes + [from_ + '/', to]
        # print(' '.join(cmd))
        p = subprocess.Popen(cmd)
        if p.wait() != 0:
            raise SystemExit(p.wait())