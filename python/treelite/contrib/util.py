# coding: utf-8

from __future__ import absolute_import as _abs
import os
import subprocess
from sys import platform as _platform
from multiprocessing import cpu_count
from ..common.compat import _str_decode, _str_encode
from ..common.util import TreeliteError, lineno, log_info

def _is_windows():
  return _platform == 'win32'

def _shell():
  if _is_windows():
    return 'cmd.exe'
  else:
    return os.environ['SHELL']

def _libext():
  if _platform == 'darwin':
    return '.dylib'
  elif _platform == 'win32' or _platform == 'cygwin':
    return '.dll'
  else:
    return '.so'

def _create_log_cmd_unix(logfile):
  return ' > {}'.format(logfile)

def _save_retcode_cmd_unix(logfile):
  return 'echo $? >> {}'.format(logfile)

def _create_log_cmd_windows(logfile):
  return 'type NUL > {}'.format(logfile)

def _save_retcode_cmd_windows(logfile):
  return 'echo %errorlevel% >> {}'.format(logfile)

def _enqueue(args):
  tid = args['tid']
  queue = args['queue']
  dirpath = args['dirpath']
  shell = args['shell']
  init_cmd = args['init_cmd']
  create_log_cmd = args['create_log_cmd']
  save_retcode_cmd = args['save_retcode_cmd']

  proc = subprocess.Popen(shell, shell=True,
                          stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
  proc.stdin.write(_str_encode(init_cmd))
  proc.stdin.write(_str_encode('cd {}\n'.format(dirpath)))
  proc.stdin.write(_str_encode(
      create_log_cmd('retcode_cpu{}.txt'.format(tid) + '\n')))
  for command in queue:
    proc.stdin.write(_str_encode(command + '\n'))
    proc.stdin.write(_str_encode(
        save_retcode_cmd('retcode_cpu{}.txt'.format(tid)) + '\n'))
  proc.stdin.flush()

  return proc

def _wait(proc, args):
  tid = args['tid']
  dirpath = args['dirpath']
  stdout, _ = proc.communicate()
  with open(os.path.join(dirpath, 'retcode_cpu{}.txt'.format(tid)), 'r') as f:
    retcode = [int(line) for line in f]
  return {'stdout':_str_decode(stdout), 'retcode':retcode}

def _create_shared_base(dirpath, recipe, nthread, verbose):
  # Fetch toolchain-specific commands
  obj_cmd = recipe['create_object_cmd']
  lib_cmd = recipe['create_library_cmd']
  create_log_cmd \
    = _create_log_cmd_windows if _is_windows() else _create_log_cmd_unix
  save_retcode_cmd \
    = _save_retcode_cmd_windows if _is_windows() else _save_retcode_cmd_unix

  # 1. Compile sources in parallel
  if verbose:
    log_info(__file__, lineno(),
             'Compiling sources files in directory {} '.format(dirpath) +\
             'into object files (*{})...'.format(recipe['object_ext']))
  ncore = cpu_count()
  ncpu = min(ncore, nthread) if nthread is not None else ncore
  workqueue = [{
      'tid': tid,
      'queue': [],
      'dirpath': os.path.abspath(dirpath),
      'shell': recipe['shell'],
      'init_cmd': recipe['initial_cmd'],
      'create_log_cmd': create_log_cmd,
      'save_retcode_cmd': save_retcode_cmd
  } for tid in range(ncpu)]
  for i, source in enumerate(recipe['sources']):
    workqueue[i % ncpu]['queue'].append(obj_cmd(source[0]))
  proc = [_enqueue(workqueue[tid]) for tid in range(ncpu)]
  result = []
  for tid in range(ncpu):
    result.append(_wait(proc[tid], workqueue[tid]))

  for tid in range(ncpu):
    if not all(x == 0 for x in result[tid]['retcode']):
      with open(os.path.join(dirpath, 'log_cpu{}.txt'.format(tid)), 'w') as f:
        f.write(result[tid]['stdout'] + '\n')
      raise TreeliteError('Error occured in worker #{}: '.format(tid) +\
                          '{}'.format(result[tid]['stdout']))

  # 2. Package objects into a dynamic shared library
  if verbose:
    log_info(__file__, lineno(),
             'Generating dynamic shared library {}...'\
                     .format(os.path.join(dirpath,
                             recipe['target'] + recipe['library_ext'])))
  workqueue = {
      'tid': 0,
      'queue': [lib_cmd(recipe['sources'], recipe['target'])],
      'dirpath': os.path.abspath(dirpath),
      'shell': recipe['shell'],
      'init_cmd': recipe['initial_cmd'],
      'create_log_cmd': create_log_cmd,
      'save_retcode_cmd': save_retcode_cmd
  }
  proc = _enqueue(workqueue)
  result = _wait(proc, workqueue)

  if result['retcode'][0] != 0:
    with open(os.path.join(dirpath, 'log_cpu0.txt'), 'w') as f:
      f.write(result['stdout'] + '\n')
    raise TreeliteError('Error occured while creating dynamic library: ' +\
                        '{}'.format(result['stdout']))

  # 3. Clean up
  for tid in range(ncpu):
    os.remove(os.path.join(dirpath, 'retcode_cpu{}.txt').format(tid))

  # Return full path of shared library
  return os.path.join(os.path.abspath(dirpath),
                      recipe['target'] + recipe['library_ext'])

__all__ = []
