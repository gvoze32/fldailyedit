//! FL Daily Edit: the desktop app and the command line, one executable.
//!
//! Layout: [`cli`] and [`gui`] are the two front ends over the [`engine::Engine`]
//! seam; [`wiring`] picks the engine; [`selfupdate`] keeps the app current.

pub mod cli;
pub mod console;
pub mod engine;
pub mod gui;
pub mod report;
pub mod scripted;
pub mod selfupdate;
pub mod session;
pub mod settings;
pub mod wiring;
