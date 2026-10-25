# Third-party notices

FL Daily Edit itself is available under the [MIT License](LICENSE). It contains,
ports, or relies on the following third-party work. Components keep their
original licenses and attribution.

## pesXdecrypter (save file encryption)

- Project: <https://github.com/the4chancup/pesXdecrypter> (maintained by
  4ccbent; originally `pes16decrypter` by a contributor who wishes to remain
  anonymous; simplified decryption/encryption functions and additional
  encryption keys by zlac).
- Use: the algorithm of the EDIT-file decrypter/encrypter (block layout, master
  key, and stream cipher) is ported to Rust in `crates/fl-save/src/crypto`.
  The original C sources are no longer part of this repository; the app uses
  no external binaries.
- License: public domain dedication, except the Mersenne Twister generator
  (see next section). The full license text is reproduced below.

```text
This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or
distribute this software, either in source code form or as a compiled
binary, for any purpose, commercial or non-commercial, and by any
means.

In jurisdictions that recognize copyright laws, the author or authors
of this software dedicate any and all copyright interest in the
software to the public domain. We make this dedication for the benefit
of the public at large and to the detriment of our heirs and
successors. We intend this dedication to be an overt act of
relinquishment in perpetuity of all present and future rights to this
software under copyright law.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.
```

## MT19937 Mersenne Twister

`crates/fl-save/src/crypto/mt19937.rs` is a port of `mt19937ar.c` (MT19937 with
`init_by_array`), which pesXdecrypter bundles under the following license.

```text
A C-program for MT19937, with initialization improved 2002/1/26.
Coded by Takuji Nishimura and Makoto Matsumoto.

Copyright (C) 1997 - 2002, Makoto Matsumoto and Takuji Nishimura,
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

  1. Redistributions of source code must retain the above copyright
     notice, this list of conditions and the following disclaimer.

  2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.

  3. The names of its contributors may not be used to endorse or promote
     products derived from this software without specific prior written
     permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Format and rating references

- Edit-file layout: the community documentation at
  <https://implyingrigged.info/wiki/Pro_Evolution_Soccer_2021/Edit_file> and the
  structures in [4ccEditor](https://github.com/the4chancup/4ccEditor), checked
  against real Football Life 2026 saves. Only facts about the file format are
  used; no source code from these projects is included.
- Native overall rating (`crates/fl-save/src/rating.rs`): the ability weights and
  rounding were recovered from Ejogc327's PES 2020 Editor V0.12.10 and verified
  against pesdb.net PES 2021 overall ratings. No editor code is included.

## Base save and game data

- `base/EDIT00000000` is derived from Gondowan's
  [Road to FL27 MiniPatch](https://www.reddit.com/r/SPFootballLife/comments/1wssmur/release_minipatch_road_to_fl27_for_football_life/)
  for SP Football Life 2026 (Update 2.2, SmokePatch National Teams Selection
  Update). Football Life 2026, SmokePatch, and the game databases belong to
  their respective authors; the updater is a tool for your own save and does
  not include game files. Vanilla PES 2021 `Player.bin`, `Team.bin`, and
  `PlayerAssignment.bin` are read from your own installation at run time.
- Football Life player and team names are read from your game folder's
  `Player.bin` and `Team.bin` at run time; the app ships no game or player
  data. FotMob's team list (ids and club names) is downloaded from FotMob's
  public sitemaps on the first update and cached in the app's state folder for
  30 days.

## Live data sources

Transfer, squad, lineup, and tactics data is fetched at run time from FotMob,
Transfermarkt, Wikipedia, Sortitoutsi, BeSoccer, Sofascore, and Soccerway. This
data is not redistributed with the app; it remains subject to each provider's
terms. Only your own save is changed, and no prebuilt saves are published.

## Rust dependencies

The app is built with Rust crates from <https://crates.io>, mostly under MIT
and/or Apache-2.0 licenses (the exact set is pinned in `Cargo.lock`). Notable
runtime pieces: `eframe`/`egui` (MIT OR Apache-2.0), `reqwest` and `rustls`
(MIT OR Apache-2.0, ISC), `tokio` (MIT), `ed25519-dalek` (BSD-3-Clause), and
`clap` (MIT OR Apache-2.0).

The `egui` default fonts are bundled in the executable: Hack (MIT / Bitstream
Vera license), Ubuntu-Light (Ubuntu Font Licence 1.0), Noto Emoji (SIL Open Font
License 1.1), and emoji-icon-font (MIT). Their license texts ship in the
`epaint_default_fonts` crate (`fonts/` directory).
