#!/usr/bin/env python3
"""Hand-seeded nickname / shortened-name aliases for All-Star matching.

Maps the spelling that appears in awards.csv (usually a nickname or a shortened
first name) to the player's official name as it appears in Players.csv. Seeded
from the near-miss candidates surfaced by build_allstar_flags.py's review list,
using general basketball knowledge to pick the obvious real player.

This is only ever consulted as the third matching pass, AFTER exact-name and
generational-suffix matching, and it is still subject to the same one-candidate
safety guard in build_allstar_flags.py: an alias is applied only when its target
resolves to exactly one personId. So a deliberately ambiguous target (e.g.
"Eddie Johnson", of whom there are two) is left unmatched rather than guessed.

Keys and values are compared case/accent-insensitively by the consumer, so plain
readable spellings are fine here.

Run directly to print the table and a quick sanity summary.
"""

# awards.csv spelling  ->  official Players.csv name
ALIASES = {
    # --- resolved from the current review list's near-miss candidates ---
    "Predrag Stojakovic": "Peja Stojakovic",
    "Tommy Heinsohn": "Tom Heinsohn",
    "Fred Scolari": "Freddie Scolari",
    "Frankie Brian": "Frank Brian",
    "Bill Gabor": "Billy Gabor",
    "Kenny Sears": "Ken Sears",
    "Jo Jo White": "Jojo White",
    "World B. Free": "World Free",
    "Maurice Williams": "Mo Williams",
    "Tiny Archibald": "Nate Archibald",
    "Hot Rod Hundley": "Rod Hundley",
    "Red Kerr": "Johnny Kerr",
    "Fat Lever": "Lafayette Lever",

    # --- deliberately ambiguous: target has two real players, so the
    #     one-candidate guard will (correctly) refuse to merge this one ---
    "Fast Eddie Johnson": "Eddie Johnson",

    # --- general well-known nickname forms (harmless if the awards file already
    #     uses the official spelling; only used when exact + suffix both fail) ---
    "Pistol Pete Maravich": "Pete Maravich",
    "Dr. J": "Julius Erving",
    "Magic Johnson": "Earvin Johnson",
}


def get_aliases():
    return dict(ALIASES)


if __name__ == "__main__":
    print(f"{len(ALIASES)} alias(es):")
    for k, v in sorted(ALIASES.items()):
        print(f"  {k!r} -> {v!r}")
