"""The Win32 surface: frameless window messages, and the pseudo-console.

This module is what remains of ``embed/win32.py`` after the docking backend was removed.
That file was 516 lines of style mangling, SetParent, geometry snapping and detach
contracts, all of it in service of borrowing another process's window. None of it is
needed once the shell runs on a pseudo-console.

Two things are left, and neither borrows anybody's window. A frameless window still has to
answer WM_NCCALCSIZE and WM_NCHITTEST itself, and Qt clears the styles Windows consults
before it will size or move a window. And a pseudo-console is a C API, so calling it directly
has to happen somewhere; ``term/windowspty.py`` is what gives those calls a meaning, and this
file only makes them.

**Importable everywhere, functional only on Windows.** ``ctypes.WinDLL`` and
``ctypes.wintypes`` both fail outright off Windows -- wintypes raises on import -- so the
plumbing sits behind a platform guard and the three public functions answer inertly
elsewhere. That is deliberately here rather than at the call sites: ``app.py`` calls
``set_app_user_model_id`` unconditionally and already treats a ``False`` return as "this
platform has no taskbar identity to claim", so a guard on this side means the macOS port
touches neither ``app.py`` nor ``ui/frameless.py``. The alternative -- ``if IS_WINDOWS``
sprinkled at every caller -- is how a boundary module stops being a boundary.
"""

from __future__ import annotations

import ctypes

from . import platform_

GWL_STYLE = -16
MASK32 = 0xFFFFFFFF

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084

# Plain integers, and so defined unconditionally: ``FramelessHelper.hit_test`` is portable
# geometry that happens to return one of these, and keeping them available off Windows lets
# that function -- and its tests -- stay platform-free.
HTCLIENT = 1
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17


if platform_.IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    # GWL_STYLE fits in 32 bits, but the *Ptr forms are the correct API on x64. Declaring
    # argtypes is mandatory: without them ctypes defaults to c_int and silently truncates
    # the 64-bit LONG_PTR.
    _get_window_long = user32.GetWindowLongPtrW
    _set_window_long = user32.SetWindowLongPtrW
    _get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
    _get_window_long.restype = ctypes.c_ssize_t
    _set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _set_window_long.restype = ctypes.c_ssize_t

    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL

    class _MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", ctypes.c_size_t),
            ("lParam", ctypes.c_ssize_t),
            ("time", wintypes.DWORD),
            ("pt_x", ctypes.c_long),
            ("pt_y", ctypes.c_long),
        ]

    # --- the pseudo-console ----------------------------------------------------------
    #
    # Defined only here, unlike the frameless functions below, which answer inertly off
    # Windows. Nothing elsewhere can reach these: term/conpty.py imports the module that
    # calls them only on Windows, and a name that does not exist is a louder failure than a
    # guard returning something plausible.

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    INFINITE = 0xFFFFFFFF
    WAIT_OBJECT_0 = 0

    _ERROR_INSUFFICIENT_BUFFER = 122
    #: What reading a pipe reports when the other end has closed. Not a failure: it is how a
    #: pipe says end of stream.
    _ERROR_BROKEN_PIPE = 109

    _STARTF_USESTDHANDLES = 0x00000100
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016

    class _COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

    class _PROCESS_INFORMATION(ctypes.Structure):  # noqa: N801 - the Win32 name
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    kernel32.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE),
                                    ctypes.POINTER(wintypes.HANDLE),
                                    ctypes.c_void_p, wintypes.DWORD]
    kernel32.CreatePipe.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD,
                                   ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_void_p]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
        wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p,
        ctypes.POINTER(_PROCESS_INFORMATION)]
    kernel32.CreateProcessW.restype = wintypes.BOOL

    _create_pseudo_console = kernel32.CreatePseudoConsole
    _create_pseudo_console.argtypes = [_COORD, wintypes.HANDLE, wintypes.HANDLE,
                                       wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    _create_pseudo_console.restype = ctypes.c_long
    _resize_pseudo_console = kernel32.ResizePseudoConsole
    _resize_pseudo_console.argtypes = [ctypes.c_void_p, _COORD]
    _resize_pseudo_console.restype = ctypes.c_long
    _close_pseudo_console = kernel32.ClosePseudoConsole
    _close_pseudo_console.argtypes = [ctypes.c_void_p]
    _close_pseudo_console.restype = None

    def use_console_host(dll_path: str) -> None:
        """Route the three pseudo-console calls through a ``conpty.dll`` rather than kernel32.

        The same API under ``Conpty``-prefixed names, and the handle it makes is what
        ``spawn_on_console`` already passes to CreateProcessW -- so nothing else changes.
        What changes is which program hosts the shell: this DLL starts the ``OpenConsole.exe``
        beside it, rather than the ``conhost.exe`` the running copy of Windows came with.
        ``term/windowspty.py`` says why that matters.

        Raises OSError if the DLL will not load and AttributeError if it lacks the exports,
        and in either case leaves the kernel32 route in place.
        """
        global _create_pseudo_console, _resize_pseudo_console, _close_pseudo_console
        library = ctypes.WinDLL(dll_path, use_last_error=True)
        create = library.ConptyCreatePseudoConsole
        resize = library.ConptyResizePseudoConsole
        close = library.ConptyClosePseudoConsole
        for new, old in ((create, _create_pseudo_console), (resize, _resize_pseudo_console),
                         (close, _close_pseudo_console)):
            new.argtypes, new.restype = old.argtypes, old.restype
        _create_pseudo_console, _resize_pseudo_console, _close_pseudo_console = (
            create, resize, close)

    def _last_error() -> OSError:
        return ctypes.WinError(ctypes.get_last_error())

    def _size(cols: int, rows: int) -> _COORD:
        # A COORD holds shorts. A grid that overflows one cannot be drawn on any screen that
        # exists, but it should clamp rather than wrap into a negative size.
        return _COORD(min(max(1, int(cols)), 32767), min(max(1, int(rows)), 32767))

    def close_handle(handle: int) -> None:
        kernel32.CloseHandle(handle)

    def open_pseudo_console(cols: int, rows: int) -> tuple[int, int, int]:
        """Make a pseudo-console. Returns ``(console, write_to_it, read_from_it)``.

        The two far ends are closed here once the console holds its own copies. They have to
        be: while this process still holds the write end of the output pipe, a read on that
        pipe can never report end of stream, because a writer still exists -- and it would
        be this process.
        """
        input_read, input_write = wintypes.HANDLE(), wintypes.HANDLE()
        output_read, output_write = wintypes.HANDLE(), wintypes.HANDLE()
        if not kernel32.CreatePipe(ctypes.byref(input_read), ctypes.byref(input_write),
                                   None, 0):
            raise _last_error()
        if not kernel32.CreatePipe(ctypes.byref(output_read), ctypes.byref(output_write),
                                   None, 0):
            error = _last_error()
            close_handle(input_read)
            close_handle(input_write)
            raise error
        console = ctypes.c_void_p()
        result = _create_pseudo_console(_size(cols, rows), input_read, output_write, 0,
                                        ctypes.byref(console))
        close_handle(input_read)
        close_handle(output_write)
        if result != 0:
            close_handle(input_write)
            close_handle(output_read)
            raise OSError(f"CreatePseudoConsole failed: {result & 0xFFFFFFFF:#010x}")
        return console.value, input_write.value, output_read.value

    def resize_pseudo_console(console: int, cols: int, rows: int) -> None:
        result = _resize_pseudo_console(console, _size(cols, rows))
        if result != 0:
            raise OSError(f"ResizePseudoConsole failed: {result & 0xFFFFFFFF:#010x}")

    def close_pseudo_console(console: int) -> None:
        """End the console and everything still attached to it.

        **May block, so never call this on the UI thread.** Until recent Windows releases it
        does not return until the console host has exited, and the host cannot exit while it
        has output it is failing to write -- so something has to be reading the other end
        for this to finish.
        """
        _close_pseudo_console(console)

    def spawn_on_console(console: int, command_line: str, cwd: str,
                         environment: str) -> tuple[int, int]:
        """Start a process attached to a pseudo-console. Returns ``(process, pid)``.

        ``environment`` is the block CreateProcessW takes, already joined and terminated.

        ``STARTF_USESTDHANDLES`` with all three handles null is not the no-op it looks like.
        Without it, a parent whose own standard handles are redirected passes those to the
        child in preference to the console, so the shell runs with its output going into
        whatever pipe launched us and the pane stays empty. Handing over nothing leaves the
        console as the only place they can come from.
        """
        size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
            raise _last_error()
        attributes = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(attributes, 1, 0,
                                                          ctypes.byref(size)):
            raise _last_error()
        try:
            # The console handle is the value here, not a pointer to one. The API takes a
            # pointer-sized attribute by address for every attribute but this.
            if not kernel32.UpdateProcThreadAttribute(
                    attributes, 0, _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, console,
                    ctypes.sizeof(ctypes.c_void_p), None, None):
                raise _last_error()
            startup = _STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
            startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
            info = _PROCESS_INFORMATION()
            block = ctypes.create_string_buffer(
                environment.encode("utf-16-le", "surrogatepass"))
            # CreateProcessW may write to the command line, so it has to be a real buffer.
            command = ctypes.create_unicode_buffer(command_line)
            if not kernel32.CreateProcessW(
                    None, command, None, None, False,
                    _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT,
                    block, cwd, ctypes.byref(startup), ctypes.byref(info)):
                raise _last_error()
        finally:
            kernel32.DeleteProcThreadAttributeList(attributes)
        close_handle(info.hThread)
        return info.hProcess, info.dwProcessId

    def read_handle(handle: int, size: int) -> bytes:
        """Block until there is something to read. ``b""`` means the other end has gone."""
        buffer = ctypes.create_string_buffer(size)
        count = wintypes.DWORD()
        if not kernel32.ReadFile(handle, buffer, size, ctypes.byref(count), None):
            if ctypes.get_last_error() == _ERROR_BROKEN_PIPE:
                return b""
            raise _last_error()
        return buffer.raw[:count.value]

    def write_handle(handle: int, data: bytes) -> int:
        count = wintypes.DWORD()
        if not kernel32.WriteFile(handle, data, len(data), ctypes.byref(count), None):
            raise _last_error()
        return count.value

    def wait_for_process(process: int, timeout_ms: int = INFINITE) -> bool:
        """True if the process has exited."""
        return kernel32.WaitForSingleObject(process, timeout_ms) == WAIT_OBJECT_0

    def process_exit_code(process: int) -> int:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process, ctypes.byref(code)):
            raise _last_error()
        return code.value

    def terminate_process(process: int, code: int) -> None:
        if not kernel32.TerminateProcess(process, code):
            raise _last_error()


def _signed16(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


def decode_native_message(message_ptr: int) -> tuple[int, int, int, int]:
    """Read a Qt nativeEvent pointer into (message, wparam, screen_x, screen_y).

    The coordinates only mean anything for the mouse messages; they are decoded
    unconditionally so every cast stays on this side of the boundary.

    Raises off Windows rather than returning a neutral tuple. There is no honest answer to
    "decode this MSG" on a platform that has no MSG, and the only caller is
    ``FramelessHelper.native_event``, reached from a ``nativeEvent`` override that is dead
    code whenever the frameless helper is absent -- which is always, on macOS. A raise says
    so if that ever stops being true; a fake ``(0, 0, 0, 0)`` would quietly answer HTCLIENT
    forever.
    """
    if not platform_.IS_WINDOWS:
        raise RuntimeError("decode_native_message is Windows-only")
    msg = ctypes.cast(int(message_ptr), ctypes.POINTER(_MSG)).contents
    x = _signed16(msg.lParam & 0xFFFF)
    y = _signed16((msg.lParam >> 16) & 0xFFFF)
    return int(msg.message), int(msg.wParam), x, y


def add_window_styles(hwnd: int, bits: int) -> int:
    """OR extra bits into GWL_STYLE and re-apply the frame. Returns the new style.

    A frameless window still needs WS_THICKFRAME for Windows to run its sizing loop, and
    WS_MAXIMIZEBOX for Aero snap. Qt's FramelessWindowHint clears both, so a hit-test
    that correctly answers HTBOTTOMRIGHT resizes nothing at all.

    Returns 0 off Windows -- "no styles", which is the truth: there is no GWL_STYLE to add
    to, and a window there was never made frameless in the first place.
    """
    if not platform_.IS_WINDOWS:
        return 0
    style = (_get_window_long(hwnd, GWL_STYLE) | bits) & MASK32
    _set_window_long(hwnd, GWL_STYLE, style)
    user32.SetWindowPos(
        hwnd, 0, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )
    return style


def set_app_user_model_id(app_id: str) -> bool:
    """Tell Windows this process is its own application. True if it took.

    Without it the taskbar groups us under whatever launched the interpreter and shows
    that program's icon, no matter what ``setWindowIcon`` says -- so a source run appears
    as Python. Lives here rather than next to the icon code because it is a Win32 call,
    and spec 10's boundary is that every one of those is in this file.

    Best effort by design: it is cosmetic, and a shell too old to have the entry point is
    not a reason to refuse to start. Off Windows there is nothing to claim -- macOS gets
    its identity from the bundle's CFBundleIdentifier -- so False is the honest answer and
    ``app.py`` already logs it at debug and carries on.
    """
    if not platform_.IS_WINDOWS:
        return False
    try:
        from ctypes import wintypes
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
        shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
        return shell32.SetCurrentProcessExplicitAppUserModelID(app_id) == 0
    except Exception:
        return False
