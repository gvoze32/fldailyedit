#!/usr/bin/env python3
"""
Validates FotMob teams against PES save file teams.
Extracts clubs from EDIT00000000, compares them with FotMob 5707 clubs via fuzzy matching,
and saves the validated subset to data/fotmob_teams_validated.json.
"""

import json
import logging
import sys
from pathlib import Path

from rapidfuzz import fuzz

# Add parent directory to path so we can import editor modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from editor import crypto
from editor.editfile import EditFile

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("validate_teams")

def get_pes_teams(edit_file_path: str) -> list[str]:
    path = Path(edit_file_path)
    if not path.exists():
        logger.error(f"Edit file not found: {path}")
        sys.exit(1)
        
    logger.info(f"Decrypting {path}...")
    temp_dir = crypto.decrypt(path)
    
    data_dat = temp_dir / "data.dat"
    if not data_dat.exists():
        dat_files = list(temp_dir.glob("*.dat"))
        if dat_files:
            data_dat = max(dat_files, key=lambda f: f.stat().st_size)
        else:
            logger.error("No .dat file found in decrypted folder")
            sys.exit(1)
            
    ef = EditFile()
    ef.load(data_dat)
    
    team_dict = ef.get_all_team_info()
    # Filter out national teams if possible, or just take all names
    pes_names = []
    for team in team_dict.values():
        if team.name:
            pes_names.append(team.name)
            
    return pes_names

def validate():
    logger.info("Loading fotmob_teams.json...")
    fotmob_path = Path("data/fotmob_teams.json")
    if not fotmob_path.exists():
        logger.error("data/fotmob_teams.json does not exist. Run crawl_fotmob_teams.py first.")
        sys.exit(1)
        
    with open(fotmob_path, "r", encoding="utf-8") as f:
        fotmob_teams = json.load(f)
        
    logger.info(f"Loaded {len(fotmob_teams)} FotMob teams.")
    
    pes_teams = get_pes_teams("sample/EDIT00000000")
    logger.info(f"Loaded {len(pes_teams)} Club teams from PES save file.")
    
    # Save the PES teams to JSON as requested
    pes_out_path = Path("data/pes_teams.json")
    with open(pes_out_path, "w", encoding="utf-8") as f:
        json.dump(pes_teams, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved raw PES teams to {pes_out_path}")
    
    validated_fotmob_teams = []
    matched_fotmob_ids = set()
    
    # Matching logic
    # We loop through PES teams and find the best match in FotMob
    for pes_club in pes_teams:
        best_score = 0
        best_match = None
        
        for ft in fotmob_teams:
            ft_name = ft.get("name") or ft.get("slug", "")
            
            # Fast exact match
            if ft_name.lower() == pes_club.lower():
                best_score = 100
                best_match = ft
                break
                
            score = fuzz.token_set_ratio(pes_club.lower(), ft_name.lower())
            if score > best_score:
                best_score = score
                best_match = ft
                
        # threshold 85
        if best_score >= 85 and best_match:
            fid = best_match["fotmob_id"]
            if fid not in matched_fotmob_ids:
                matched_fotmob_ids.add(fid)
                validated_fotmob_teams.append(best_match)
                
    logger.info(f"Validation complete. Found {len(validated_fotmob_teams)} matched clubs.")
    
    # Sort by ID
    validated_fotmob_teams.sort(key=lambda x: x["fotmob_id"])
    
    out_path = Path("data/fotmob_teams_validated.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(validated_fotmob_teams, f, indent=2, ensure_ascii=False)
        
    logger.info(f"Saved validated teams to {out_path} ({(out_path.stat().st_size / 1024):.2f} KB)")

if __name__ == "__main__":
    validate()
