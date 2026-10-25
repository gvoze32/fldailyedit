//! Console output for the CLI.
//!
//! The Windows build uses the GUI subsystem so double-clicking never opens a
//! console window. When started from a terminal with arguments, the CLI
//! attaches to the parent's console so its output (and clap's help) show up.

/// Make stdout/stderr reach the terminal that launched us, if any.
///
/// Redirected handles (`> file`, pipes) are left alone.
#[cfg(windows)]
pub fn attach_parent() {
    use std::fs::OpenOptions;
    use std::os::windows::io::IntoRawHandle;

    type Handle = *mut core::ffi::c_void;
    const ATTACH_PARENT_PROCESS: u32 = u32::MAX;
    const STD_OUTPUT_HANDLE: u32 = -11i32 as u32;
    const STD_ERROR_HANDLE: u32 = -12i32 as u32;

    unsafe extern "system" {
        fn AttachConsole(process_id: u32) -> i32;
        fn GetStdHandle(which: u32) -> Handle;
        fn SetStdHandle(which: u32, handle: Handle) -> i32;
    }

    // SAFETY: plain Win32 calls with valid constants; the handles passed to
    // `SetStdHandle` are owned by the process for its whole lifetime.
    unsafe {
        if AttachConsole(ATTACH_PARENT_PROCESS) == 0 {
            return; // no parent console (double-click, service): nothing to show
        }
        for which in [STD_OUTPUT_HANDLE, STD_ERROR_HANDLE] {
            let current = GetStdHandle(which);
            if (current.is_null() || current as isize == -1)
                && let Ok(console) = OpenOptions::new().write(true).open("CONOUT$")
            {
                SetStdHandle(which, console.into_raw_handle());
            }
        }
    }
}

#[cfg(not(windows))]
pub fn attach_parent() {}
