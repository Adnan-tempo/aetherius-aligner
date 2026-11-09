"""Align one recording and exit -- the GitHub Actions entry point.

    python run_once.py <journey_days|teaching_episodes> <row id>

Same job as the server's worker (app.run): read the row, download the audio
from Supabase storage, align, write `transcript_words` back. Needs
SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment.
"""

import sys

from app import TABLES, run


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in TABLES:
        print(__doc__, file=sys.stderr)
        return 2
    table, row_id = sys.argv[1], sys.argv[2]
    result = run(table, row_id)
    print(f"{table}:{row_id} -> {result}")
    # A transcript that does not line up is not a crash, but say so loudly.
    return 0 if result.startswith("timed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
