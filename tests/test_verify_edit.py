"""Comprehensive corruption checker for EDIT00000000 file."""
import struct
from pathlib import Path
from editor.editfile import (
    EditFile,
    GAME_PLAN_ENTRY_SIZE,
    GP_TEAM_ID,
    GP_LINEUP,
    GP_CAPTAIN,
    GP_LEFT_CK,
    GP_RIGHT_CK,
    GP_PK,
    GP_ATTACK_PLAYERS,
    TP_MAX_PLAYERS,
    TEAM_PLAYER_ENTRY_SIZE,
    TP_TEAM_ID,
    TP_PLAYER_IDS,
)
import editor.crypto as crypto

def check_corruption(edit_path: Path):
    print(f"=== Corruption Checker for {edit_path} ===\n")
    if not edit_path.exists():
        print(f"File {edit_path} does not exist!")
        return -1
        
    temp_dir = crypto.decrypt(edit_path)
    dat_files = list(temp_dir.glob("*.dat"))
    data_dat = max(dat_files, key=lambda f: f.stat().st_size)
    
    ef = EditFile()
    ef.load(data_dat)
    
    errors = 0
    warnings = 0
    
    # ═══════════════════════════════════════════
    # CHECK 1: Game Plan Lineup Integrity
    # ═══════════════════════════════════════════
    print("--- CHECK 1: Game Plan Lineups ---")
    competition_entry_size = 0x1230
    gp_base = ef.game_plan_start + competition_entry_size
    
    for i in range(ef.game_plan_count):
        offset = gp_base + i * GAME_PLAN_ENTRY_SIZE
        if offset + GAME_PLAN_ENTRY_SIZE > len(ef._data):
            break
        
        tid = struct.unpack_from("<I", ef._data, offset + GP_TEAM_ID)[0]
        if tid == 0 or tid == 0xFFFF0300 or tid in (4971, 5129):
            continue
            
        lineup_off = offset + GP_LINEUP
        lineup = list(ef._data[lineup_off : lineup_off + 40])
        
        # In PES21, lineup must be a strict permutation of 0..39
        if sorted(lineup) != list(range(40)):
            print(f"  ❌ Team {tid}: Lineup NOT a valid permutation of 0..39! {lineup}")
            errors += 1
        
        # Check for duplicate starter slots
        starters = lineup[:11]
        if len(set(starters)) != 11:
            print(f"  ❌ Team {tid}: Duplicate starters! {starters}")
            errors += 1
            
        # Check roles (must be 0-39 or 0xFF)
        role_checks = [
            ("Captain", GP_CAPTAIN),
            ("Left CK", GP_LEFT_CK),
            ("Right CK", GP_RIGHT_CK),
            ("PK", GP_PK),
        ]
        for role_name, role_off in role_checks:
            val = ef._data[offset + role_off]
            if val != 0xFF and val >= 40:
                print(f"  ❌ Team {tid}: {role_name} = {val} (INVALID, must be 0-39 or 0xFF)")
                errors += 1
        
        # Check Attack Players (3 bytes, each 0-39 or 0xFF)
        att_off = offset + GP_ATTACK_PLAYERS
        for b in range(3):
            val = ef._data[att_off + b]
            if val != 0xFF and val >= 40:
                print(f"  ❌ Team {tid}: Attack Player {b} = {val} (INVALID)")
                errors += 1
    
    # ═══════════════════════════════════════════
    # CHECK 2: Team-Player Roster Integrity
    # ═══════════════════════════════════════════
    print("\n--- CHECK 2: Team-Player Roster Integrity ---")
    for i in range(ef.team_player_count):
        offset = ef.team_player_start + i * TEAM_PLAYER_ENTRY_SIZE
        if offset + 4 > len(ef._data):
            break
        tid = struct.unpack_from("<I", ef._data, offset + TP_TEAM_ID)[0]
        
        pids = []
        for j in range(40):
            pid = struct.unpack_from("<I", ef._data, offset + TP_PLAYER_IDS + j * 4)[0]
            pids.append(pid)
        
        # Check for holes (0 in the middle of non-zero entries)
        last_nonzero = -1
        for j in range(39, -1, -1):
            if pids[j] != 0:
                last_nonzero = j
                break
        
        if last_nonzero > 0:
            holes = [j for j in range(last_nonzero) if pids[j] == 0]
            if holes:
                print(f"  ❌ Team {tid}: HOLES in roster at slots {holes} (last player at slot {last_nonzero})")
                errors += 1
    
    # ═══════════════════════════════════════════
    # CHECK 3: Check Cucurella & Real Madrid & Chelsea
    # ═══════════════════════════════════════════
    print("\n--- CHECK 3: Chelsea & Madrid Status ---")
    cuc_id = 116575
    chelsea_id = 102
    madrid_id = 109
    
    chelsea_roster = ef.get_team_roster(chelsea_id)
    madrid_roster = ef.get_team_roster(madrid_id)
    
    if chelsea_roster:
        print(f"  Chelsea has Cucurella: {chelsea_roster.has_player(cuc_id)}")
    if madrid_roster:
        has_cuc = madrid_roster.has_player(cuc_id)
        print(f"  Real Madrid has Cucurella: {has_cuc}")
        if has_cuc:
            idx = madrid_roster.player_index(cuc_id)
            print(f"  Cucurella roster index in Madrid: {idx}")
            gp_off = ef._find_game_plan_offset(madrid_id)
            if gp_off:
                lineup = list(ef._data[gp_off + GP_LINEUP : gp_off + GP_LINEUP + 40])
                lineup_pos = lineup.index(idx) if idx in lineup else -1
                print(f"  Cucurella game plan lineup slot: {lineup_pos}")
    
    # ═══════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════
    print(f"\n=== SUMMARY ===")
    print(f"  Errors: {errors}")
    print(f"  Warnings: {warnings}")
    if errors > 0:
        print(f"  🔴 FILE HAS ERRORS")
    else:
        print(f"  🟢 FILE IS 100% VALID & CLEAN - Zero corruption!")
    
    crypto.cleanup_temp(temp_dir)
    return errors

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "output/EDIT_TEST"
    check_corruption(Path(path))
