"""User-only ACL for decrypted resources; no administrator privilege needed."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path

def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    adv=c.WinDLL('advapi32',use_last_error=True); kernel=c.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=w.HANDLE
    adv.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)]
    adv.ConvertSidToStringSidW.argtypes=[c.c_void_p,c.POINTER(w.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,c.POINTER(c.c_void_p),c.c_void_p]
    adv.SetFileSecurityW.argtypes=[w.LPCWSTR,w.DWORD,c.c_void_p]
    kernel.CloseHandle.argtypes=[w.HANDLE];kernel.LocalFree.argtypes=[c.c_void_p]
    token=w.HANDLE(); size=w.DWORD(); sid_text=w.LPWSTR(); descriptor=c.c_void_p()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(),8,c.byref(token)):raise c.WinError(c.get_last_error())
    try:
        adv.GetTokenInformation(token,1,None,0,c.byref(size))
        info=c.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token,1,info,size,c.byref(size)):raise c.WinError(c.get_last_error())
        sid=c.cast(info,c.POINTER(c.c_void_p)).contents.value
        if not adv.ConvertSidToStringSidW(sid,c.byref(sid_text)):raise c.WinError(c.get_last_error())
        sddl='D:P(A;OICI;FA;;;'+sid_text.value+')'
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,c.byref(descriptor),None):raise c.WinError(c.get_last_error())
        if not adv.SetFileSecurityW(str(path),0x80000004,descriptor):raise c.WinError(c.get_last_error())
    finally:
        if descriptor:kernel.LocalFree(descriptor)
        if sid_text:kernel.LocalFree(c.cast(sid_text,c.c_void_p))
        kernel.CloseHandle(token)
