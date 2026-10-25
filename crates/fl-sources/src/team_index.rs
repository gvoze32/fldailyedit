//! The FotMob team catalog: every FotMob club (id, slug-derived name, URL).
//!
//! The catalog is the only input club-identity binding has for mapping save
//! clubs to FotMob ids. It is not bundled: [`crawl`] builds it from FotMob's
//! team sitemaps (an index of child sitemaps, each listing
//! `/teams/<id>/overview/<slug>` pages) and the engine caches the result.
//! The crawl is all-or-nothing: one failed child sitemap fails the whole
//! crawl, so a partial catalog is never used or stored.

use std::collections::HashSet;
use std::sync::LazyLock;
use std::time::Duration;

use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::fotmob::DEFAULT_HEADERS;
use crate::http::{HttpClient, HttpError, Request, RetryPolicy};
use crate::lenient::{lenient_int, trimmed};

/// Index of the child team sitemaps.
pub const SITEMAP_INDEX_URL: &str = "https://www.fotmob.com/sitemap/en/teams.xml";

/// Child sitemaps are ~2 MB of XML each; the default 15 s request timeout is too tight.
const SITEMAP_TIMEOUT: Duration = Duration::from_secs(60);

/// Pause between child sitemap requests (politeness; instant on replays).
const SITEMAP_PAUSE: Duration = Duration::from_millis(500);

/// Sitemaps are XML, not the JSON the other FotMob requests expect.
const SITEMAP_HEADERS: [(&str, &str); 3] = [
    DEFAULT_HEADERS[0],
    ("Accept", "application/xml, text/xml, */*"),
    ("Referer", "https://www.fotmob.com/"),
];

/// One FotMob club. `name` is the title-cased club slug (the identity
/// binder's only name source), not FotMob's display name.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TeamEntry {
    pub fotmob_id: i64,
    pub name: String,
    #[serde(default)]
    pub slug: String,
    #[serde(default)]
    pub url: String,
}

#[derive(Debug, thiserror::Error)]
pub enum TeamIndexError {
    #[error("FotMob team catalog is not valid JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("FotMob team catalog must contain a JSON array")]
    NotAnArray,
    #[error("FotMob team entry is missing fotmob_id")]
    MissingId,
    #[error("Invalid or duplicate FotMob team entry: {0}")]
    InvalidEntry(i64),
}

/// Parse and validate catalog JSON (array of entries; ids unique, names non-empty).
pub fn parse_catalog(json: &str) -> Result<Vec<TeamEntry>, TeamIndexError> {
    let Value::Array(items) = serde_json::from_str(json)? else {
        return Err(TeamIndexError::NotAnArray);
    };
    let mut seen = std::collections::HashSet::new();
    let mut entries = Vec::with_capacity(items.len());
    for item in &items {
        let id = item
            .as_object()
            .and_then(|object| object.get("fotmob_id"))
            .ok_or(TeamIndexError::MissingId)
            .and_then(|value| lenient_int(value).ok_or(TeamIndexError::MissingId))?;
        let name = {
            let name = trimmed(item.get("name"));
            if name.is_empty() {
                trimmed(item.get("slug"))
            } else {
                name
            }
        };
        if name.is_empty() || !seen.insert(id) {
            return Err(TeamIndexError::InvalidEntry(id));
        }
        entries.push(TeamEntry {
            fotmob_id: id,
            name,
            slug: trimmed(item.get("slug")),
            url: trimmed(item.get("url")),
        });
    }
    Ok(entries)
}

/// Serialize a catalog the way [`parse_catalog`] reads it back (2-space
/// indent, non-ASCII verbatim, trailing newline).
pub fn render_catalog(entries: &[TeamEntry]) -> String {
    let mut text = serde_json::to_string_pretty(entries).expect("team entries serialize");
    text.push('\n');
    text
}

/// Percent-decode `text` as UTF-8 (invalid sequences become U+FFFD; `+` stays).
fn unquote(text: &str) -> String {
    fn hex(byte: u8) -> Option<u8> {
        char::from(byte)
            .to_digit(16)
            .and_then(|digit| u8::try_from(digit).ok())
    }
    let bytes = text.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%'
            && let (Some(high), Some(low)) = (
                bytes.get(index + 1).copied().and_then(hex),
                bytes.get(index + 2).copied().and_then(hex),
            )
        {
            decoded.push(high << 4 | low);
            index += 3;
        } else {
            decoded.push(bytes[index]);
            index += 1;
        }
    }
    String::from_utf8_lossy(&decoded).into_owned()
}

/// Club name of a FotMob slug: percent-decoded, dashes to spaces, each run of
/// letters title-cased (`"o'neil-fc"` → `"O'Neil Fc"`).
pub fn clean_name(slug: &str) -> String {
    let mut name = String::with_capacity(slug.len());
    let mut previous_cased = false;
    for c in unquote(slug).chars() {
        let c = if c == '-' { ' ' } else { c };
        if previous_cased {
            name.extend(c.to_lowercase());
        } else {
            name.extend(c.to_uppercase());
        }
        previous_cased = c.is_lowercase() || c.is_uppercase();
    }
    name
}

static CHILD_SITEMAP: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"<loc>(https://www\.fotmob\.com/sitemap/en/teams/\d+\.xml)</loc>")
        .expect("static pattern")
});

static TEAM_URL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"<loc>https://www\.fotmob\.com/teams/(\d+)/overview/([^<]+)</loc>")
        .expect("static pattern")
});

/// Child sitemap URLs listed by the sitemap index, in order.
pub fn parse_sitemap_index(xml: &str) -> Vec<String> {
    CHILD_SITEMAP
        .captures_iter(xml)
        .map(|captures| captures[1].to_owned())
        .collect()
}

/// Teams listed by one child sitemap, in document order (duplicates included).
pub fn parse_team_sitemap(xml: &str) -> Vec<TeamEntry> {
    TEAM_URL
        .captures_iter(xml)
        .filter_map(|captures| {
            let id: i64 = captures[1].parse().ok()?;
            let slug = &captures[2];
            Some(TeamEntry {
                fotmob_id: id,
                name: clean_name(slug),
                slug: slug.to_owned(),
                url: format!("https://www.fotmob.com/teams/{id}/overview/{slug}"),
            })
        })
        .collect()
}

#[derive(Debug, thiserror::Error)]
pub enum CrawlError {
    #[error("FotMob club list download cancelled")]
    Cancelled,
    /// The crawl could not read the complete list; nothing was produced.
    #[error("{0}")]
    Incomplete(String),
}

async fn fetch_sitemap(http: &HttpClient, url: &str) -> Result<String, CrawlError> {
    let request = Request::get(url)
        .headers(SITEMAP_HEADERS)
        .timeout(SITEMAP_TIMEOUT);
    match http.get_ok(&request, &RetryPolicy::FOTMOB).await {
        Ok(response) => Ok(response.text()),
        Err(HttpError::Cancelled) => Err(CrawlError::Cancelled),
        Err(HttpError::Status { status, .. }) => Err(CrawlError::Incomplete(format!(
            "{url} returned HTTP {status}"
        ))),
        Err(error) => Err(CrawlError::Incomplete(error.to_string())),
    }
}

/// Download the complete team catalog from FotMob's sitemaps, sorted by id with
/// the first listing of a duplicated id kept. `progress(done, total)` fires
/// before the first child sitemap (`done == 0`) and after each one.
///
/// Any failure (a child sitemap that cannot be fetched or lists no teams, an
/// empty index, an empty result) fails the whole crawl.
pub async fn crawl(
    http: &HttpClient,
    progress: &mut dyn FnMut(usize, usize),
) -> Result<Vec<TeamEntry>, CrawlError> {
    let index = fetch_sitemap(http, SITEMAP_INDEX_URL).await?;
    let children = parse_sitemap_index(&index);
    if children.is_empty() {
        return Err(CrawlError::Incomplete(
            "the FotMob sitemap index lists no team sitemaps".to_owned(),
        ));
    }

    let total = children.len();
    progress(0, total);
    let mut teams: Vec<TeamEntry> = Vec::new();
    let mut seen: HashSet<i64> = HashSet::new();
    for (position, url) in children.iter().enumerate() {
        if position > 0 {
            http.pause(SITEMAP_PAUSE)
                .await
                .map_err(|_| CrawlError::Cancelled)?;
        }
        let listed = parse_team_sitemap(&fetch_sitemap(http, url).await?);
        if listed.is_empty() {
            return Err(CrawlError::Incomplete(format!("{url} lists no teams")));
        }
        teams.extend(
            listed
                .into_iter()
                .filter(|team| seen.insert(team.fotmob_id)),
        );
        progress(position + 1, total);
    }
    teams.sort_by_key(|team| team.fotmob_id);
    Ok(teams)
}

#[cfg(test)]
mod tests;
