"""Daily native Hermes job; stdout is delivered through the existing owner Telegram chat."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path.home() / 'hermes-control'))
from jobsearch import main

if __name__ == '__main__':
    sys.argv = ['jobsearch.py', 'daily']
    try:
        main()
    except Exception as error:
        print('Job shortlist failed: ' + type(error).__name__ + '. Check Jobs source health and native run status.', file=sys.stderr)
        sys.exit(1)
