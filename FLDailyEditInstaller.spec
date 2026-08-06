# -*- mode: python ; coding: utf-8 -*-


CRYPTO_BINARIES = [
    (
        "vendor/pesXdecrypter/decrypter21.exe",
        "vendor/pesXdecrypter",
    ),
    (
        "vendor/pesXdecrypter/encrypter21.exe",
        "vendor/pesXdecrypter",
    ),
]
RUNTIME_DATA = [
    ("data/major_clubs.json", "data"),
    ("data/fotmob_teams_validated.json", "data"),
    ("data/name_overrides.json", "data"),
    ("data/team_aliases.json", "data"),
    ("data/FL262_teams.txt", "data"),
]

a = Analysis(
    ["installer/__main__.py"],
    pathex=["."],
    binaries=CRYPTO_BINARIES,
    datas=RUNTIME_DATA,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FLDailyEditInstaller",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
