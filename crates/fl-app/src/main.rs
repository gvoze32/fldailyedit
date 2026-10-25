//! FL Daily Edit. No arguments: the app. Arguments: the command line.

// Release builds on Windows use the GUI subsystem so the app never opens a
// console window; the CLI attaches to the parent console on demand.
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

use std::io::Write;
use std::process::ExitCode;
use std::sync::Arc;

use fl_app::gui::{self, platform};
use fl_app::selfupdate::SignedRelease;
use fl_app::{cli, console, wiring};
use fl_engine::api::Environment;
use fl_engine::locations;

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args_os().collect();
    let command_line = args.len() > 1;
    if command_line {
        console::attach_parent();
    }

    let Some(state_dir) = locations::state_dir() else {
        let message = "FL Daily Edit could not find your user data folder (LOCALAPPDATA).";
        if command_line {
            eprintln!("{message}");
        } else {
            rfd::MessageDialog::new()
                .set_title("FL Daily Edit")
                .set_description(message)
                .set_level(rfd::MessageLevel::Error)
                .show();
        }
        return ExitCode::from(cli::EXIT_INPUT as u8);
    };
    let env = Environment { state_dir };
    let engine = wiring::engine();

    if command_line {
        let mut out = std::io::stdout().lock();
        let code = cli::run_cli(args, engine.as_ref(), &env, &mut out);
        let _ = out.flush();
        return ExitCode::from(u8::try_from(code).unwrap_or(1));
    }

    let services = gui::Services {
        engine,
        env,
        platform: Arc::new(platform::Native),
        update_backend: Arc::new(SignedRelease::new()),
        silent_update_check: true,
        detect: Arc::new(gui::detect_installed),
    };
    match gui::run(services) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            rfd::MessageDialog::new()
                .set_title("FL Daily Edit")
                .set_description(format!("The app could not start: {error}"))
                .set_level(rfd::MessageLevel::Error)
                .show();
            ExitCode::FAILURE
        }
    }
}
