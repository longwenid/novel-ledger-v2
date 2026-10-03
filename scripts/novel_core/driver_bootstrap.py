"""Wait until the controller has persisted this PID, then run the configured driver.

A killed controller cannot leave an unregistered long-running model process in the
small Popen-to-DB-record window. This file is an internal executable, not a CLI.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

launch=Path(sys.argv[1])
data=json.loads(launch.read_text(encoding='utf-8'))
gate=launch.with_suffix('.go')
deadline=time.monotonic()+10


def parent_alive(pid):
    if os.name=='nt':
        # os.kill(pid, 0) is not a liveness probe on Windows; probe via the kernel.
        import ctypes
        kernel=ctypes.windll.kernel32
        handle=kernel.OpenProcess(0x00100000,False,pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle,0)==0x102  # WAIT_TIMEOUT
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid,0)
        return True
    except (ProcessLookupError,PermissionError):
        return False


while not gate.exists():
    if time.monotonic()>=deadline:
        raise SystemExit(75)
    if not parent_alive(data['parent_pid']):
        raise SystemExit(75)
    time.sleep(0.05)

if os.name=='nt':
    # exec does not preserve the PID reliably on Windows. Run the argv inside a
    # kill-on-close job object instead: if this bootstrap dies (controller kill,
    # crash), the kernel reaps the worker, so no orphan outlives its registration.
    import ctypes
    kernel=ctypes.windll.kernel32

    class BasicLimits(ctypes.Structure):
        _fields_=[('PerProcessUserTimeLimit',ctypes.c_int64),('PerJobUserTimeLimit',ctypes.c_int64),
                  ('LimitFlags',ctypes.c_uint32),('MinimumWorkingSetSize',ctypes.c_size_t),
                  ('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',ctypes.c_uint32),
                  ('Affinity',ctypes.c_size_t),('PriorityClass',ctypes.c_uint32),('SchedulingClass',ctypes.c_uint32)]

    class IoCounters(ctypes.Structure):
        _fields_=[(name,ctypes.c_uint64) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount',
                                                      'ReadTransferCount','WriteTransferCount','OtherTransferCount')]

    class ExtendedLimits(ctypes.Structure):
        _fields_=[('BasicLimitInformation',BasicLimits),('IoInfo',IoCounters),
                  ('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),
                  ('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]

    job=kernel.CreateJobObjectW(None,None)
    info=ExtendedLimits()
    info.BasicLimitInformation.LimitFlags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel.SetInformationJobObject(job,9,ctypes.byref(info),ctypes.sizeof(info))  # JobObjectExtendedLimitInformation
    kernel.AssignProcessToJobObject(job,kernel.GetCurrentProcess())
    sys.exit(subprocess.run(data['argv']).returncode)

os.execvpe(data['argv'][0],data['argv'],os.environ)
