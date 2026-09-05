"""Compare every packaged Groundtruth byte with its materialized source tree.

This is component correspondence, not a substitute for clean-commit, review,
producer identity, dependency, or installed-bundle acceptance gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath


def verify_wheel_source(wheel: Path, source: Path) -> dict:
    members: dict[str, str] = {}
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            if not info.filename.startswith('groundtruth/') or info.is_dir():
                continue
            path = PurePosixPath(info.filename)
            if (
                '..' in path.parts or '\\' in info.filename
                or str(path) != info.filename or info.filename in members
            ):
                raise ValueError('unsafe_member_or_duplicate')
            members[info.filename] = hashlib.sha256(archive.read(info)).hexdigest()
    actual: dict[str, str] = {}
    root = source.resolve()
    for path in sorted((source / 'groundtruth').rglob('*')):
        if '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('unsafe_source_symlink')
        if path.is_file():
            actual[path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    missing = sorted(members.keys() - actual.keys())
    extra = sorted(actual.keys() - members.keys())
    changed = sorted(name for name in members.keys() & actual.keys() if members[name] != actual[name])
    return {
        'schema': 'gt.wheel_source_correspondence.v1',
        'status': 'PASS' if members and not (missing or extra or changed) else 'FAIL',
        'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
        'package_file_count': len(members),
        'missing': missing, 'extra': extra, 'changed': changed,
        'package_files': members,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    result = verify_wheel_source(args.wheel, args.source)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
