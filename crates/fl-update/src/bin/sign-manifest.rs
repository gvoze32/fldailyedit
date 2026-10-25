//! Release tool: builds `update.json` for the release executable and signs it.

use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use fl_update::release::{SIGNING_KEY_ENVIRONMENT, build_manifest, sign_manifest_file};

#[derive(Parser)]
#[command(
    name = "sign-manifest",
    version,
    about = "Build and sign the app self-update manifest"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Write update.json for FLDailyEdit.exe.
    Build {
        /// Path to the release executable (must be named FLDailyEdit.exe).
        #[arg(long)]
        asset: PathBuf,
        /// Where to write the manifest.
        #[arg(long)]
        output: PathBuf,
        /// MAJOR.MINOR.PATCH; defaults to this build's workspace version.
        #[arg(long, default_value = env!("CARGO_PKG_VERSION"))]
        version: String,
    },
    /// Sign a manifest with the PEM PKCS#8 Ed25519 key in $INSTALLER_SIGNING_KEY.
    Sign {
        /// Manifest to sign.
        #[arg(long)]
        manifest: PathBuf,
        /// Where to write the base64 signature.
        #[arg(long)]
        output: PathBuf,
    },
}

fn run(cli: Cli) -> fl_update::Result<()> {
    match cli.command {
        Command::Build {
            asset,
            output,
            version,
        } => build_manifest(&asset, &output, &version).map(drop),
        Command::Sign { manifest, output } => {
            let key = std::env::var(SIGNING_KEY_ENVIRONMENT).unwrap_or_default();
            sign_manifest_file(&manifest, &output, &key)
        }
    }
}

fn main() -> ExitCode {
    match run(Cli::parse()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("sign-manifest: {error} [{}]", error.kind().code());
            ExitCode::FAILURE
        }
    }
}
