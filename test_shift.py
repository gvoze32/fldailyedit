def test():
    # Roster IDs
    removed_idx = 10
    replacement_idx = 31

    # Lineup where indices are just their initial positions for easy tracking
    lineup = list(range(40))
    # Let's say departed player was at pos 2 (a starter)
    lineup[2] = 10
    # Replacement player (Cucurella) was at pos 35 (reserve)
    lineup[35] = 31

    print(f"Original Lineup (first 15): {lineup[:15]}")
    print(f"Original Lineup (last 10): {lineup[30:]}")

    # 1. Find pos_A
    try:
        pos_A = lineup.index(removed_idx)
    except ValueError:
        pos_A = -1

    if pos_A != -1:
        if pos_A < 11:
            lineup[pos_A] = lineup[11]
            for i in range(11, 39):
                lineup[i] = lineup[i+1]
            lineup[39] = 0xFF
        else:
            for i in range(pos_A, 39):
                lineup[i] = lineup[i+1]
            lineup[39] = 0xFF

    # 2. Re-map replacement_idx to removed_idx
    for i in range(40):
        if lineup[i] == replacement_idx and replacement_idx >= 0:
            lineup[i] = removed_idx

    print(f"\nNew Lineup (first 15): {lineup[:15]}")
    print(f"New Lineup (last 10): {lineup[30:]}")

test()
