//! Command line front end: `run` (default), `validate`, `inspect`, `log`.

use std::ffi::OsString;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use chrono::NaiveDate;
use clap::{Args, Parser, Subcommand, ValueEnum};
use fl_engine::api::{
    CancelToken, Environment, ErrorCode, Outcome, Progress, Stage, UpdateError, UpdateRequest,
    UpdateResult, Window,
};
use fl_engine::locations;

use crate::engine::Engine;
use crate::report::{group_skipped, reason_label, skipped_line};

/// Exit code for a failed validation or a failed update.
pub const EXIT_FAILURE: i32 = 2;
/// Exit code for a missing or unreadable input save.
pub const EXIT_INPUT: i32 = 1;
/// Exit code when the user cancelled (the shell convention for SIGINT).
pub const EXIT_CANCELLED: i32 = 130;

const SUBCOMMANDS: [&str; 5] = ["run", "validate", "inspect", "log", "help"];

#[derive(Debug, Parser)]
#[command(
    name = "FLDailyEdit",
    version,
    about = "FL Daily Edit: bring a Football Life 2026 / PES 2021 save up to date.",
    long_about = "FL Daily Edit: bring a Football Life 2026 / PES 2021 save up to date.\n\n\
        Run without arguments to open the app. `FLDailyEdit --edit-file PATH` is \
        shorthand for `FLDailyEdit run --edit-file PATH`."
)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Apply verified transfers, squad numbers, captains and tactics (default)
    Run(RunArgs),
    /// Check that a save matches the supported edit-file layout (exit 2 on FAIL)
    Validate(ValidateArgs),
    /// Show what a save contains
    Inspect(SaveArg),
    /// Show the most recent save changes from the transfer log
    Log(LogArgs),
}

#[derive(Debug, Args)]
pub struct SaveArg {
    /// Path to EDIT00000000
    #[arg(long, value_name = "PATH")]
    pub edit_file: PathBuf,
}

#[derive(Debug, Args)]
pub struct ValidateArgs {
    /// Path to EDIT00000000
    #[arg(long, value_name = "PATH")]
    pub edit_file: PathBuf,
    /// List every affected team instead of the first few
    #[arg(short, long)]
    pub verbose: bool,
}

#[derive(Debug, Args)]
pub struct LogArgs {
    /// Number of entries to show
    #[arg(short = 'n', long, value_name = "N", default_value_t = 20)]
    pub last: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum WindowArg {
    /// Continue from the save's last applied update
    Auto,
    /// Replay all available transfer history
    All,
    /// The current or most recent summer window
    Summer,
    /// The current or most recent winter window
    Winter,
}

#[derive(Debug, Args)]
pub struct RunArgs {
    /// Save to update (default: the only save found in your Documents folder)
    #[arg(long, value_name = "PATH")]
    pub edit_file: Option<PathBuf>,
    /// Write the updated save here instead of updating the input save in place
    #[arg(short, long, value_name = "PATH")]
    pub output: Option<PathBuf>,
    /// Game folder (the one that contains the download folder with data_extra.cpk); required, the player and club lists are read from the game
    #[arg(long, value_name = "DIR")]
    pub game_root: Option<PathBuf>,
    /// Transfer window
    #[arg(long, value_enum, conflicts_with = "since")]
    pub window: Option<WindowArg>,
    /// Only transfers on or after this date (YYYY-MM-DD)
    #[arg(long, value_name = "DATE", value_parser = parse_date)]
    pub since: Option<NaiveDate>,
    /// Focus on one club (name or FotMob id)
    #[arg(long, value_name = "CLUB")]
    pub club: Option<String>,
    /// Minimum player-name match confidence, 0-100 (default 80; 0 accepts any match)
    #[arg(long, value_name = "PERCENT", value_parser = parse_percentage)]
    pub threshold: Option<f64>,
    /// Keep full rosters unchanged instead of releasing a squad player
    #[arg(long)]
    pub no_overflow_release: bool,
    /// JSON protected-player / usage policy for overflow releases
    #[arg(long, value_name = "FILE")]
    pub release_policy: Option<PathBuf>,
    /// Plan and report without writing anything
    #[arg(long)]
    pub dry_run: bool,
}

fn parse_date(text: &str) -> Result<NaiveDate, String> {
    NaiveDate::parse_from_str(text, "%Y-%m-%d")
        .map_err(|_| format!("`{text}` is not a date; use YYYY-MM-DD"))
}

fn parse_percentage(text: &str) -> Result<f64, String> {
    let value: f64 = text
        .parse()
        .map_err(|_| format!("`{text}` is not a number"))?;
    if value.is_finite() && (0.0..=100.0).contains(&value) {
        Ok(value)
    } else {
        Err("must be between 0 and 100".into())
    }
}

impl RunArgs {
    /// The engine request for this command line. `edit_path` is resolved
    /// and guarded by the caller.
    pub fn to_request(&self, edit_path: PathBuf) -> UpdateRequest {
        UpdateRequest {
            edit_path,
            output_path: self.output.clone(),
            game_root: self.game_root.clone(),
            window: match (self.since, self.window) {
                (Some(date), _) => Window::Since(date),
                (None, Some(WindowArg::All)) => Window::All,
                (None, Some(WindowArg::Summer)) => Window::Summer,
                (None, Some(WindowArg::Winter)) => Window::Winter,
                (None, Some(WindowArg::Auto) | None) => Window::Auto,
            },
            club: self.club.clone(),
            threshold: self.threshold,
            allow_overflow_release: !self.no_overflow_release,
            release_policy: self.release_policy.clone(),
            dry_run: self.dry_run,
        }
    }
}

/// `FLDailyEdit --edit-file X` means `FLDailyEdit run --edit-file X`.
fn with_default_command(args: Vec<OsString>) -> Vec<OsString> {
    let is_global =
        |arg: &OsString| matches!(arg.to_str(), Some("-h" | "--help" | "-V" | "--version"));
    match args.get(1) {
        Some(first)
            if !is_global(first)
                && !SUBCOMMANDS.iter().any(|name| first.to_str() == Some(name)) =>
        {
            let mut args = args;
            args.insert(1, "run".into());
            args
        }
        _ => args,
    }
}

/// Parse and execute a command line (`args[0]` is the program name).
///
/// Everything meant for the user goes to `out`; clap prints its own usage and
/// parse errors. Returns the process exit code.
pub fn run_cli(
    args: Vec<OsString>,
    engine: &dyn Engine,
    env: &Environment,
    out: &mut dyn Write,
) -> i32 {
    let cli = match Cli::try_parse_from(with_default_command(args)) {
        Ok(cli) => cli,
        Err(error) => {
            let code = if error.use_stderr() { EXIT_FAILURE } else { 0 };
            let _ = error.print();
            return code;
        }
    };
    let result = match cli.command {
        Command::Run(args) => cmd_run(&args, engine, env, out),
        Command::Validate(arg) => cmd_validate(&arg.edit_file, arg.verbose, out),
        Command::Inspect(arg) => cmd_inspect(&arg.edit_file, out),
        Command::Log(args) => cmd_log(args.last, engine, env, out),
    };
    result.unwrap_or_else(|error| {
        // Output pipe closed: nothing useful left to say.
        let _ = error;
        EXIT_FAILURE
    })
}

fn cmd_run(
    args: &RunArgs,
    engine: &dyn Engine,
    env: &Environment,
    out: &mut dyn Write,
) -> io::Result<i32> {
    let edit_path = match locate_save(args, out)? {
        Ok(path) => path,
        Err(code) => return Ok(code),
    };
    if let Some(output) = &args.output
        && let Err(error) = locations::reject_game_root_save(output, args.game_root.as_deref())
    {
        writeln!(out, "Unsafe output path: {error}")?;
        return Ok(EXIT_FAILURE);
    }

    let request = args.to_request(edit_path);
    let mut printer = ProgressPrinter::default();
    let mut write_failed = None;
    let cancel = CancelToken::new();
    let outcome = engine.run(
        &request,
        env,
        &mut |progress| {
            if write_failed.is_none()
                && let Err(error) = printer.print(out, &progress)
            {
                write_failed = Some(error);
            }
        },
        &cancel,
    );
    if let Some(error) = write_failed {
        return Err(error);
    }
    match outcome {
        Ok(result) => {
            print_result(out, &request, &result)?;
            Ok(0)
        }
        Err(error) => {
            writeln!(out, "\nUpdate failed: {}", error.message)?;
            Ok(exit_code(&error))
        }
    }
}

/// The save to work on: `--edit-file`, else the only detected save.
fn locate_save(args: &RunArgs, out: &mut dyn Write) -> io::Result<Result<PathBuf, i32>> {
    let chosen = match &args.edit_file {
        Some(path) => path.clone(),
        None => {
            let found = locations::discover_saves();
            match found.as_slice() {
                [only] => {
                    writeln!(out, "Using {}", only.edit_file.display())?;
                    only.edit_file.clone()
                }
                [] => {
                    writeln!(
                        out,
                        "No save found. Use --edit-file to choose EDIT00000000."
                    )?;
                    return Ok(Err(EXIT_INPUT));
                }
                many => {
                    writeln!(out, "Several saves found; choose one with --edit-file:")?;
                    for candidate in many {
                        writeln!(
                            out,
                            "  {} ({})",
                            candidate.edit_file.display(),
                            candidate.family.game_name()
                        )?;
                    }
                    return Ok(Err(EXIT_INPUT));
                }
            }
        }
    };
    match locations::resolve_save_file(&chosen, args.game_root.as_deref()) {
        Ok(path) => Ok(Ok(path)),
        Err(error @ locations::SaveFileError::InsideGame(_)) => {
            writeln!(out, "Unsafe save path: {error}")?;
            Ok(Err(EXIT_FAILURE))
        }
        Err(error) => {
            writeln!(out, "Save not usable: {error}")?;
            Ok(Err(EXIT_INPUT))
        }
    }
}

fn exit_code(error: &UpdateError) -> i32 {
    match error.code {
        ErrorCode::Cancelled => EXIT_CANCELLED,
        ErrorCode::SaveNotFound | ErrorCode::InvalidSave => EXIT_INPUT,
        _ => EXIT_FAILURE,
    }
}

/// Prints one line per distinct progress message, tagged with its stage.
#[derive(Default)]
struct ProgressPrinter {
    last: String,
}

impl ProgressPrinter {
    fn print(&mut self, out: &mut dyn Write, progress: &Progress) -> io::Result<()> {
        if progress.message.is_empty() || progress.message == self.last {
            return Ok(());
        }
        let step = Stage::ALL
            .iter()
            .position(|stage| *stage == progress.stage)
            .map_or(0, |index| index + 1);
        let count = if progress.total > 0 {
            format!(" ({}/{})", progress.current, progress.total)
        } else {
            String::new()
        };
        writeln!(
            out,
            "[{step}/{}] {}{count}",
            Stage::ALL.len(),
            progress.message
        )?;
        self.last.clone_from(&progress.message);
        Ok(())
    }
}

fn print_result(
    out: &mut dyn Write,
    request: &UpdateRequest,
    result: &UpdateResult,
) -> io::Result<()> {
    print_skipped(out, &result.skipped)?;
    for warning in &result.warnings {
        writeln!(out, "Warning: {warning}")?;
    }
    let counts = &result.counts;
    match result.outcome {
        Outcome::Preview => {
            writeln!(
                out,
                "\nPreview only, nothing was written: {} transfers, {} shirt numbers, {} captains, \
                 {} tactical settings, {} formations would change.",
                counts.transfers,
                counts.shirt_numbers,
                counts.captains,
                counts.tactics,
                counts.formations
            )?;
        }
        Outcome::NoChanges => {
            writeln!(
                out,
                "\nNo verified transfer, roster, captain, tactical, or formation changes were \
                 available. The save was not modified."
            )?;
        }
        Outcome::Updated => {
            writeln!(
                out,
                "\nDone! {} transfers applied; {} shirt numbers changed; {} captains changed; \
                 {} tactical settings changed; {} formations changed.",
                counts.transfers,
                counts.shirt_numbers,
                counts.captains,
                counts.tactics,
                counts.formations
            )?;
            if same_file(&result.target_path, &request.edit_path) {
                writeln!(out, "  Updated file:  {}", result.target_path.display())?;
            } else {
                writeln!(out, "  Input:         {}", request.edit_path.display())?;
                writeln!(out, "  Output:        {}", result.target_path.display())?;
            }
            if let Some(backup) = &result.backup_path {
                writeln!(out, "  Backup:        {}", backup.display())?;
            }
            for report in &result.report_paths {
                writeln!(out, "  Report:        {}", report.display())?;
            }
        }
    }
    Ok(())
}

fn same_file(a: &Path, b: &Path) -> bool {
    match (a.canonicalize(), b.canonicalize()) {
        (Ok(a), Ok(b)) => a == b,
        _ => a == b,
    }
}

/// Every transfer not applied, grouped by reason (see [`crate::report::group_skipped`]).
pub fn print_skipped(
    out: &mut dyn Write,
    skipped: &[fl_engine::api::SkippedTransfer],
) -> io::Result<()> {
    if skipped.is_empty() {
        return Ok(());
    }
    let relevant = skipped.iter().filter(|item| item.relevant).count();
    writeln!(
        out,
        "\nNot applied: {} transfers ({relevant} touch this save; players missing from the \
         save are reported, never created)",
        skipped.len()
    )?;
    for group in group_skipped(skipped) {
        writeln!(
            out,
            "\n  {} ({}):",
            reason_label(group.reason),
            group.items.len()
        )?;
        for item in &group.items {
            let marker = if item.relevant { '*' } else { ' ' };
            writeln!(out, "   {marker} {}", skipped_line(item))?;
        }
    }
    writeln!(out, "\n  * touches a club in this save")
}

fn cmd_validate(edit_file: &Path, verbose: bool, out: &mut dyn Write) -> io::Result<i32> {
    writeln!(out, "Validating {}...", edit_file.display())?;
    let edit = match load_edit_file(edit_file) {
        Ok(loaded) => loaded,
        Err(message) => {
            writeln!(out, "FAIL: {message}")?;
            return Ok(EXIT_FAILURE);
        }
    };
    let report = edit.file.validate_integrity();
    writeln!(out, "{}", checked_summary(&report.metrics))?;
    print_warnings(out, &report.warnings, verbose)?;
    for error in &report.errors {
        writeln!(out, "ERROR: {error}")?;
    }
    if report.is_valid() {
        writeln!(
            out,
            "PASS: save structure matches the supported PES edit-file layout"
        )?;
        Ok(0)
    } else {
        writeln!(
            out,
            "FAIL: {} supported PES edit-file layout error(s)",
            report.errors.len()
        )?;
        Ok(EXIT_FAILURE)
    }
}

/// One line of what the integrity check covered.
fn checked_summary(metrics: &fl_save::validate::IntegrityMetrics) -> String {
    let mut line = format!(
        "Checked {} rosters ({} clubs) and {} game plans",
        metrics.rosters, metrics.clubs, metrics.checked_game_plans
    );
    if metrics.duplicate_club_players > 0 {
        line.push_str(&format!(
            "; {} duplicate club players",
            metrics.duplicate_club_players
        ));
    }
    if metrics.undersized_clubs > 0 {
        line.push_str(&format!("; {} undersized clubs", metrics.undersized_clubs));
    }
    line.push('.');
    line
}

/// Teams listed per warning kind unless `--verbose`.
const WARNING_TEAM_PREVIEW: usize = 10;

/// One line per warning kind with the number of teams and their ids, so a
/// condition that hits hundreds of teams stays readable.
fn print_warnings(
    out: &mut dyn Write,
    warnings: &[fl_save::validate::IntegrityWarning],
    verbose: bool,
) -> io::Result<()> {
    let mut groups: Vec<(String, Vec<u32>)> = Vec::new();
    for warning in warnings {
        let kind = warning.kind();
        match groups.iter_mut().find(|(known, _)| *known == kind) {
            Some((_, teams)) => teams.push(warning.team_id()),
            None => groups.push((kind, vec![warning.team_id()])),
        }
    }
    for (kind, teams) in groups {
        let shown = if verbose {
            teams.len()
        } else {
            teams.len().min(WARNING_TEAM_PREVIEW)
        };
        let ids: Vec<String> = teams[..shown].iter().map(u32::to_string).collect();
        let noun = if teams.len() == 1 { "team" } else { "teams" };
        let more = if shown < teams.len() {
            format!(", … and {} more (--verbose lists all)", teams.len() - shown)
        } else {
            String::new()
        };
        writeln!(
            out,
            "WARNING: {kind} — {} {noun}: {}{more}",
            teams.len(),
            ids.join(", ")
        )?;
    }
    Ok(())
}

struct Loaded {
    file: fl_save::edit_file::EditFile,
    game_version: String,
}

fn load_edit_file(path: &Path) -> Result<Loaded, String> {
    let bytes =
        std::fs::read(path).map_err(|error| format!("cannot read {}: {error}", path.display()))?;
    let blocks =
        fl_save::crypto::decrypt(&bytes).map_err(|error| format!("decryption failed: {error}"))?;
    let game_version = blocks.save_header().game_version;
    let file = fl_save::edit_file::EditFile::from_bytes(blocks.data)
        .map_err(|error| format!("the decrypted save is damaged: {error}"))?;
    Ok(Loaded { file, game_version })
}

fn cmd_inspect(edit_file: &Path, out: &mut dyn Write) -> io::Result<i32> {
    writeln!(out, "Reading {}...", edit_file.display())?;
    let Loaded { file, game_version } = match load_edit_file(edit_file) {
        Ok(loaded) => loaded,
        Err(message) => {
            writeln!(out, "{message}")?;
            return Ok(EXIT_INPUT);
        }
    };
    let counts = file.counts();
    writeln!(out, "\nGame version:  {game_version}")?;
    writeln!(out, "Players:       {}", counts.players)?;
    writeln!(out, "Teams:         {}", counts.teams)?;
    writeln!(out, "Managers:      {}", counts.managers)?;
    writeln!(out, "Stadiums:      {}", counts.stadiums)?;
    writeln!(out, "Competitions:  {}", counts.competitions)?;
    writeln!(out, "Rosters:       {}", counts.team_players)?;
    writeln!(out, "Game plans:    {}", counts.game_plans)?;

    let report = file.validate_integrity();
    writeln!(
        out,
        "\nIntegrity: {}",
        if report.is_valid() { "PASS" } else { "FAIL" }
    )?;
    writeln!(out, "  {}", checked_summary(&report.metrics))?;
    for error in report.errors.iter().take(20) {
        writeln!(out, "  ERROR: {error}")?;
    }
    if report.errors.len() > 20 {
        writeln!(out, "  ... and {} more errors", report.errors.len() - 20)?;
    }

    let names: std::collections::HashMap<u32, String> = file
        .teams()
        .into_iter()
        .map(|team| (team.team_id, team.name))
        .collect();
    writeln!(out, "\n--- League divisions in the save ---")?;
    for (index, division) in file.league_divisions().iter().enumerate() {
        let label = |id: &u32| names.get(id).cloned().unwrap_or_else(|| format!("#{id}"));
        let preview: Vec<String> = division.iter().take(3).map(label).collect();
        let tail = match division.last() {
            Some(last) if division.len() > 3 => format!(" ... [{}]", label(last)),
            _ => String::new(),
        };
        writeln!(
            out,
            "  Division {:2} ({:2} teams): {}{tail}",
            index + 1,
            division.len(),
            preview.join(", ")
        )?;
    }
    let clubs = file.club_team_ids().len();
    writeln!(out, "\nTotal clubs: {clubs}")?;
    writeln!(
        out,
        "Total other/national teams: {}",
        names.len().saturating_sub(clubs)
    )?;
    Ok(0)
}

fn cmd_log(
    count: usize,
    engine: &dyn Engine,
    env: &Environment,
    out: &mut dyn Write,
) -> io::Result<i32> {
    match engine.recent_log(env, count) {
        Ok(text) => {
            write!(out, "{text}")?;
            if !text.ends_with('\n') {
                writeln!(out)?;
            }
            Ok(0)
        }
        Err(error) => {
            writeln!(out, "Cannot read the transfer log: {error:#}")?;
            Ok(EXIT_FAILURE)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scripted::{ScriptedEngine, Step};
    use fl_engine::api::{ChangeCounts, SkippedTransfer};

    fn args(list: &[&str]) -> Vec<OsString> {
        std::iter::once("FLDailyEdit")
            .chain(list.iter().copied())
            .map(OsString::from)
            .collect()
    }

    fn parse(list: &[&str]) -> RunArgs {
        match Cli::try_parse_from(with_default_command(args(list)))
            .unwrap()
            .command
        {
            Command::Run(run) => run,
            other => panic!("expected run, got {other:?}"),
        }
    }

    #[test]
    fn bare_flags_mean_run() {
        let run = parse(&["--edit-file", "x", "--dry-run"]);
        assert!(run.dry_run);
        assert_eq!(run.edit_file.as_deref(), Some(Path::new("x")));
        assert!(matches!(
            Cli::try_parse_from(args(&["validate", "--edit-file", "x"]))
                .unwrap()
                .command,
            Command::Validate(_)
        ));
    }

    #[test]
    fn request_mirrors_flags_and_honors_zero_threshold() {
        let run = parse(&[
            "run",
            "--edit-file",
            "in",
            "-o",
            "out",
            "--game-root",
            "g",
            "--window",
            "winter",
            "--club",
            "Chelsea",
            "--threshold",
            "0",
            "--no-overflow-release",
            "--release-policy",
            "p.json",
        ]);
        let request = run.to_request("in".into());
        assert_eq!(request.output_path.as_deref(), Some(Path::new("out")));
        assert_eq!(request.game_root.as_deref(), Some(Path::new("g")));
        assert_eq!(request.window, Window::Winter);
        assert_eq!(request.club.as_deref(), Some("Chelsea"));
        assert_eq!(
            request.threshold,
            Some(0.0),
            "0 is a real threshold, not 'unset'"
        );
        assert!(!request.allow_overflow_release);
        assert_eq!(request.release_policy.as_deref(), Some(Path::new("p.json")));
        assert!(!request.dry_run);

        let defaults = parse(&["run"]).to_request("in".into());
        assert_eq!(defaults.threshold, None);
        assert_eq!(defaults.window, Window::Auto);
        assert!(defaults.allow_overflow_release);
        assert_eq!(defaults.output_path, None);

        let since = parse(&["run", "--since", "2026-01-15"]).to_request("in".into());
        assert_eq!(
            since.window,
            Window::Since(NaiveDate::from_ymd_opt(2026, 1, 15).unwrap())
        );
    }

    #[test]
    fn rejects_bad_values() {
        for bad in [
            vec!["run", "--threshold", "101"],
            vec!["run", "--threshold", "-1"],
            vec!["run", "--threshold", "abc"],
            vec!["run", "--since", "yesterday"],
            vec!["run", "--window", "autumn"],
            vec!["run", "--since", "2026-01-01", "--window", "all"],
        ] {
            assert!(
                Cli::try_parse_from(with_default_command(args(&bad))).is_err(),
                "{bad:?}"
            );
        }
    }

    fn skipped(player: &str, reason: &str, relevant: bool) -> SkippedTransfer {
        SkippedTransfer {
            player: player.into(),
            from_club: "Inter".into(),
            to_club: "Milan".into(),
            date: Some("2026-01-02".into()),
            transfer_type: "transfer".into(),
            reason: reason.into(),
            detail: "no match".into(),
            relevant,
        }
    }

    #[test]
    fn run_prints_progress_skipped_groups_and_summary() {
        let dir = tempfile::tempdir().unwrap();
        let save = dir.path().join("EDIT00000000");
        std::fs::write(&save, b"x").unwrap();
        let result = UpdateResult {
            target_path: save.clone(),
            outcome: Outcome::Updated,
            backup_path: Some(dir.path().join("backup")),
            counts: ChangeCounts {
                transfers: 3,
                shirt_numbers: 2,
                ..ChangeCounts::default()
            },
            skipped: vec![
                skipped("A. Player", "player_not_in_save", false),
                skipped("B. Player", "roster_full", true),
            ],
            warnings: vec!["one warning".into()],
            ..UpdateResult::default()
        };
        let engine = ScriptedEngine::new(
            vec![
                ScriptedEngine::step(Stage::Fetching, "Fetching Chelsea", 1, 2),
                ScriptedEngine::step(Stage::Fetching, "Fetching Chelsea", 1, 2),
                Step::Checkpoint(Stage::Fetching),
            ],
            Ok(result),
        );
        let env = Environment {
            state_dir: dir.path().into(),
        };
        let mut out = Vec::new();
        let code = run_cli(
            args(&["--edit-file", save.to_str().unwrap()]),
            &engine,
            &env,
            &mut out,
        );
        let text = String::from_utf8(out).unwrap();
        assert_eq!(code, 0, "{text}");
        assert_eq!(text.matches("Fetching Chelsea").count(), 1, "{text}");
        assert!(text.contains("[2/7] Fetching Chelsea (1/2)"), "{text}");
        assert!(
            text.contains("Not applied: 2 transfers (1 touch this save"),
            "{text}"
        );
        let relevant_first =
            text.find("Roster full (1)").unwrap() < text.find("Player not in save (1)").unwrap();
        assert!(relevant_first, "{text}");
        assert!(
            text.contains("* B. Player: Inter -> Milan (2026-01-02) — no match"),
            "{text}"
        );
        assert!(text.contains("Warning: one warning"), "{text}");
        assert!(
            text.contains("Done! 3 transfers applied; 2 shirt numbers changed"),
            "{text}"
        );
        assert!(text.contains("Backup:"), "{text}");
    }

    #[test]
    fn failures_map_to_exit_codes() {
        let dir = tempfile::tempdir().unwrap();
        let save = dir.path().join("EDIT00000000");
        std::fs::write(&save, b"x").unwrap();
        let env = Environment {
            state_dir: dir.path().into(),
        };
        for (code, expected) in [
            (ErrorCode::Cancelled, EXIT_CANCELLED),
            (ErrorCode::InvalidSave, EXIT_INPUT),
            (ErrorCode::FetchFailed, EXIT_FAILURE),
        ] {
            let engine = ScriptedEngine::new(
                Vec::new(),
                Err(UpdateError {
                    code,
                    stage: None,
                    message: "boom".into(),
                }),
            );
            let mut out = Vec::new();
            let exit = run_cli(
                args(&["run", "--edit-file", save.to_str().unwrap()]),
                &engine,
                &env,
                &mut out,
            );
            assert_eq!(exit, expected);
            assert!(
                String::from_utf8(out)
                    .unwrap()
                    .contains("Update failed: boom")
            );
        }
    }

    #[test]
    fn missing_or_game_folder_saves_never_reach_the_engine() {
        let dir = tempfile::tempdir().unwrap();
        let env = Environment {
            state_dir: dir.path().into(),
        };
        let engine = ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default()));

        let mut out = Vec::new();
        let missing = dir.path().join("none");
        let code = run_cli(
            args(&["run", "--edit-file", missing.to_str().unwrap()]),
            &engine,
            &env,
            &mut out,
        );
        assert_eq!(code, EXIT_INPUT);

        let game = dir.path().join("game");
        std::fs::create_dir_all(game.join("download")).unwrap();
        std::fs::write(game.join("download/data_extra.cpk"), b"x").unwrap();
        std::fs::write(game.join("EDIT00000000"), b"x").unwrap();
        let mut out = Vec::new();
        let code = run_cli(
            args(&[
                "run",
                "--edit-file",
                game.join("EDIT00000000").to_str().unwrap(),
            ]),
            &engine,
            &env,
            &mut out,
        );
        assert_eq!(code, EXIT_FAILURE);
        assert!(String::from_utf8(out).unwrap().contains("Unsafe save path"));
    }

    #[test]
    fn validate_fails_on_garbage_with_exit_2() {
        let dir = tempfile::tempdir().unwrap();
        let save = dir.path().join("EDIT00000000");
        std::fs::write(&save, vec![7u8; 100]).unwrap();
        let engine = ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default()));
        let env = Environment {
            state_dir: dir.path().into(),
        };
        let mut out = Vec::new();
        let code = run_cli(
            args(&["validate", "--edit-file", save.to_str().unwrap()]),
            &engine,
            &env,
            &mut out,
        );
        assert_eq!(code, EXIT_FAILURE);
        assert!(
            String::from_utf8(out)
                .unwrap()
                .contains("FAIL: decryption failed")
        );
    }

    #[test]
    fn log_prints_engine_text() {
        let engine = ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default()))
            .with_log("2026-01-01 moved X");
        let env = Environment {
            state_dir: PathBuf::new(),
        };
        let mut out = Vec::new();
        assert_eq!(
            run_cli(args(&["log", "-n", "5"]), &engine, &env, &mut out),
            0
        );
        assert_eq!(String::from_utf8(out).unwrap(), "2026-01-01 moved X\n");
    }
}

#[cfg(test)]
mod warning_tests {
    use super::*;
    use fl_save::validate::IntegrityWarning;

    fn render(warnings: &[IntegrityWarning], verbose: bool) -> String {
        let mut out = Vec::new();
        print_warnings(&mut out, warnings, verbose).unwrap();
        String::from_utf8(out).unwrap()
    }

    #[test]
    fn identical_warnings_collapse_to_one_line_per_kind() {
        let mut warnings: Vec<IntegrityWarning> = (1..=25)
            .map(|team_id| IntegrityWarning::EmptyRoles {
                team_id,
                roles: vec!["captain"],
            })
            .collect();
        warnings.push(IntegrityWarning::ShirtOnEmptySlot { team_id: 7 });

        let brief = render(&warnings, false);
        let lines: Vec<&str> = brief.lines().collect();
        assert_eq!(lines.len(), 2, "{brief}");
        assert_eq!(
            lines[0],
            "WARNING: game plan has empty roles: captain — 25 teams: \
             1, 2, 3, 4, 5, 6, 7, 8, 9, 10, … and 15 more (--verbose lists all)"
        );
        assert_eq!(
            lines[1],
            "WARNING: shirt number assigned to an empty roster slot — 1 team: 7"
        );

        let full = render(&warnings, true);
        assert!(full.contains("24, 25\n"), "{full}");
        assert!(!full.contains("more"), "{full}");
    }
}
