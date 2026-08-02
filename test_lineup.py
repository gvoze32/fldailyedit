import sys
from pathlib import Path
from editor import crypto
from editor.editfile import EditFile, GP_LINEUP

print("Decrypting sample...")
temp_dir = crypto.decrypt(Path("sample/EDIT00000000"))
data_dat = temp_dir / "data.dat"

ef = EditFile()
ef.load(data_dat)

gp_offset = ef._find_game_plan_offset(10252) # Aston Villa or some team
lineup_offset = gp_offset + GP_LINEUP
lineup = list(ef._data[lineup_offset : lineup_offset + 40])
print(f"Lineup: {[hex(x) for x in lineup]}")
