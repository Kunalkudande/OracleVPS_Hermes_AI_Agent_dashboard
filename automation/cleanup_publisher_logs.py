"""Delete only confirmed silent X-publisher reports older than 48 hours."""
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
import re
import time

JOB = 'd92bae72a243'
AGE = timedelta(hours=48)
NAME = re.compile(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.md')


def is_silent_report(text, stamp):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines == [
        '# Cron Job: X automatic publisher',
        '**Job ID:** ' + JOB,
        '**Run Time:** ' + stamp.strftime('%Y-%m-%d %H:%M:%S'),
        '**Mode:** no_agent (script)',
        '**Status:** silent (empty output)',
    ]


def cleanup(output_root, apply=False, now=None):
    root = Path(output_root).resolve(strict=True)
    folder = root / JOB
    if folder.is_symlink() or folder.resolve().parent != root:
        raise ValueError('Publisher report directory must stay inside cron output')
    cutoff = (datetime.fromtimestamp(now, timezone.utc) if now is not None else datetime.now(timezone.utc)) - AGE
    result = {'eligible': 0, 'deleted': 0, 'bytes_deleted': 0}
    if not folder.exists():
        return result
    for path in folder.iterdir():
        if not NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
            continue
        try:
            stamp = datetime.strptime(path.stem, '%Y-%m-%d_%H-%M-%S').replace(tzinfo=timezone.utc)
            stat = path.stat()
            if stamp >= cutoff or stat.st_mtime >= cutoff.timestamp() or stat.st_size > 4096:
                continue
            if not is_silent_report(path.read_text(encoding='utf-8'), stamp):
                continue
            result['eligible'] += 1
            if apply:
                path.unlink()
                result['deleted'] += 1
                result['bytes_deleted'] += stat.st_size
        except (FileNotFoundError, UnicodeDecodeError, ValueError):
            # The scheduler may prune a report concurrently; unknown reports stay.
            continue
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Delete eligible reports; default is preview only')
    args = parser.parse_args()
    result = cleanup(Path.home() / '.hermes/cron/output', apply=args.apply)
    print(('Applied' if args.apply else 'Preview') + ': ' + str(result))


if __name__ == '__main__':
    main()
