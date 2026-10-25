//! Windows executable resources: icon, version info and the application
//! manifest (per-monitor DPI awareness, `asInvoker`). Other targets need none.

use std::env;
use std::fs;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=assets");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }

    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let out_dir = PathBuf::from(env::var("OUT_DIR").unwrap());
    let version = env::var("CARGO_PKG_VERSION").unwrap();
    let mut numbers = version
        .split(['.', '-'])
        .map(|part| part.parse::<u16>().unwrap_or(0))
        .chain(std::iter::repeat(0));
    let [a, b, c, d] = std::array::from_fn(|_| numbers.next().unwrap());

    // rc.exe/windres need forward slashes (or escaped backslashes) in paths.
    let path = |name: &str| {
        manifest_dir
            .join("assets")
            .join(name)
            .display()
            .to_string()
            .replace('\\', "/")
    };
    let rc = format!(
        r#"#include <winver.h>
1 ICON "{icon}"
1 24 "{manifest}"
1 VERSIONINFO
FILEVERSION {a},{b},{c},{d}
PRODUCTVERSION {a},{b},{c},{d}
FILEOS VOS_NT_WINDOWS32
FILETYPE VFT_APP
{{
  BLOCK "StringFileInfo"
  {{
    BLOCK "040904B0"
    {{
      VALUE "CompanyName", "FL Daily Edit"
      VALUE "FileDescription", "FL Daily Edit"
      VALUE "FileVersion", "{version}"
      VALUE "InternalName", "FLDailyEdit"
      VALUE "LegalCopyright", "MIT License"
      VALUE "OriginalFilename", "FLDailyEdit.exe"
      VALUE "ProductName", "FL Daily Edit"
      VALUE "ProductVersion", "{version}"
    }}
  }}
  BLOCK "VarFileInfo"
  {{
    VALUE "Translation", 0x0409, 0x04B0
  }}
}}
"#,
        icon = path("icon.ico"),
        manifest = path("app.manifest"),
    );
    let rc_path = out_dir.join("fl-app.rc");
    fs::write(&rc_path, rc).expect("write resource script");
    embed_resource::compile(&rc_path, embed_resource::NONE)
        .manifest_required()
        .expect("compile Windows resources");
}
